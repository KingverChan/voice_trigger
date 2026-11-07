# -*- coding: utf-8 -*-
"""
voice-trigger 最小版
麦克风持续监听 -> VAD 切句 (默认 Silero, 可退回 webrtc) -> 本地 faster-whisper 转文字
-> 句子里有唤醒词(fafa 及别名)时, 把后面的话粘贴到聊天输入框并回车。
录音只在内存里, 不存文件。

用法:
  python main.py --calibrate     第一次: 记录聊天输入框坐标
  python main.py                 开始监听 (Ctrl+C 退出)
  python main.py --list-mics     查看麦克风编号
  python main.py --dry-run       只打印, 不点击不粘贴 (调唤醒词用)
  python main.py --test-file a.wav [--mode both] [--vad silero]
                                 用录音文件测试切句+识别+唤醒 (只打印)
"""
import argparse
import json
import queue
import re
import sys
import time
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.json")

DEFAULT_CONFIG = {
    "wake_words": ["fafa", "fa fa", "发发", "發發"],
    "model_size": "small",
    # Whisper 用什么跑: "cpu" / "cuda" (NVIDIA 显卡) / "auto" (有能用的 N 卡就用, 不行自动退回 CPU)
    "device": "cpu",
    "compute_type": "int8",          # CPU 用的精度
    "gpu_compute_type": "float16",   # 显卡用的精度 (老显卡报错可改 "int8_float16")
    "language": "auto",   # 识别语言: "auto" 自动分中文/英文, "zh" 只认中文, "en" 只认英文 (英语练习)
    "mic_device": None,
    # 切句用哪个 VAD: "silero" = 神经网络 VAD (更准, 抗键盘/风扇声);
    # "webrtc" = 原来的老办法。silero 出问题时会自动退回 webrtc。
    "vad_engine": "silero",
    "silero_threshold": 0.5,       # 人声概率大于它算"开始说话"
    "silero_neg_threshold": 0.35,  # 说话中, 概率低于它才算"安静" (迟滞, 防来回跳)
    "silero_start_frames": 3,      # 连续 3 帧(约 96ms)有声才算开口, 过滤"咔"一声
    # 触发方式: "prefix" = 先说发发再说指令 (原来的);
    # "suffix" = 先说话, 最后说发发 -> 把前面攒的话一起发出去;
    # "both" = 两种都行 (发发后面有话 = prefix, 发发前面有话/攒了话 = suffix)
    "trigger_mode": "prefix",
    "buffer_max_s": 60,   # suffix: 只攒最近 60 秒的话
    "buffer_gap_s": 8,    # suffix: 两句之间停了超过 8 秒, 前面的清空重来
    # 自动发送模式: 不用说发发, 说完停顿 auto_silence_ms 就直接发 (所有话都会发出去!)
    # 用热键或面板按钮开/关, 开关状态存在 AUTO_SEND_ON 这个文件里 (有这个文件 = 开)
    "auto_silence_ms": 500,
    "auto_min_chars": 2,           # 少于 2 个字不发 (过滤"嗯""啊")
    "auto_hotkey": "ctrl+alt+f",   # 全局热键, 按一下开/关; 留空 "" 不用热键
    # Smart Turn (实验): 自动发送模式下, 停顿后让一个小模型听语气判断"说完没"。
    # 说完了 -> 停 0.3 秒就发; 没说完 -> 继续等你, 最多等 3 秒。只在自动发送开着时起作用。
    "smart_turn": False,
    "smart_turn_check_ms": 500,     # 停顿多久判断一次 (句中停顿常有 0.3 秒, 太短会把一句话切成两段)
    "smart_turn_threshold": 0.5,    # "说完了"的概率大于它才发
    "smart_turn_max_wait_ms": 3000, # 判断一直是"没说完"时, 最多等这么久也发
    "turn_text_ok_extra_ms": 1000,  # 概率低但文字看着是完整句时, 只再等这么久 (不用等满 3 秒)
    # 只发对 AI 说的话: 自动发送时, 每句话先问 DeepSeek "这是不是对 AI 说的", 是才发
    "ai_filter": False,
    "ai_filter_api_key": "",        # DeepSeek key, 只存在这个文件里, 日志不会打印
    "ai_filter_model": "deepseek-flash",
    # DeepSeek 默认开"思考模式"(很慢), 判断是/否不需要思考, 关掉: {"type": "disabled"}
    "ai_filter_thinking": "disabled",
    "ai_filter_base_url": "https://api.deepseek.com",
    "ai_filter_timeout_s": 3,
    "ai_filter_on_error": "drop",   # 超时/出错时: "drop" = 不发, "send" = 照样发
    "ai_filter_daily_limit": 500,   # 每天最多问几次, 超过就按 on_error 处理
    "ai_filter_context": 3,         # 带上前面几句话当参考
    # DeepSeek 辅助判断说完没 (要先开 Smart Turn): Smart Turn 概率 >= high 直接发, < low 继续等,
    # 中间拿不准时把已经识别的文字给 DeepSeek 看"这句话说完整了吗"。出错/超时就按 Smart Turn 的结果
    "ai_turn": False,
    # 文字规则判断"没说完": 句末是这些词 / 以省略号逗号结尾 / 几乎全是嗯啊 -> 继续等 (最多等 smart_turn_max_wait_ms)
    "turn_rules": True,
    "turn_hang_words_zh": ["然后", "但是", "可是", "不过", "所以", "就是", "那个", "这个", "因为", "如果",
                           "而且", "还有", "或者", "还是", "和", "跟", "把", "帮我", "我想", "我觉得", "比如",
                           "的话", "并且", "以及", "就", "嗯", "呃", "额", "然后呢"],
    "turn_hang_words_en": ["and", "but", "so", "because", "the", "a", "an", "to", "of", "or", "if", "with",
                           "like", "um", "uh", "my", "your", "then", "which", "for", "in", "on"],
    "ai_turn_high": 0.9,
    "ai_turn_low": 0.3,
    # 按窗口自动选择发送方式 (默认关 = 跟以前一样, 永远点聊天框发送):
    #   前台是听写窗口 (Word/微信/编辑器) -> 直接粘贴到你正在打字的地方, 不点击、不回车、不附带剪贴板
    #   前台是 AI 窗口 (Claude/豆包/DeepSeek) -> 跟以前一样点聊天框、粘贴、回车
    #   其他窗口 -> 不发
    # 关键词不分大小写, 匹配程序名 (如 winword.exe) 或窗口标题
    "window_routing": False,
    "dictation_windows": ["winword.exe", "wechat.exe", "weixin.exe", "typora", "code.exe",
                          "obsidian", "notepad"],
    "ai_windows": ["claude", "豆包", "deepseek", "chatgpt"],
    # 说话时带上选中文字: 发送 (AI 模式) 前在你正在用的窗口按一下 Ctrl+C 读选中的内容, 读完还原剪贴板
    "attach_selection": False,
    "selection_max_chars": 6000,
    # 听写窗口里如果框选了文字 -> 当作"问 AI": 带上选中内容发到 AI 聊天框, 不在文档里打字
    # (不然粘贴会把选中的内容替换掉)
    "dictation_selection_to_ai": True,
    # 这些程序没选中东西时按 Ctrl+C 会复制整行 (VS Code), 复制到的正好是"一整行"就当没选中
    "selection_line_copy_apps": ["code.exe", "cursor.exe", "pycharm64.exe", "pycharm.exe", "idea64.exe",
                                 "clion64.exe", "webstorm64.exe", "goland64.exe", "rider64.exe", "studio64.exe"],
    # 这些程序里 Ctrl+C 是"中断", 不去读选中内容
    "selection_skip_apps": ["windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
                            "conhost.exe", "wt.exe", "mintty.exe", "putty.exe"],
    # 判断"是不是对 AI 说的"时带上对话上下文: 你上次发给 AI 的话 (和距今几秒),
    # 还会试着读 AI 窗口里最后的回复 (要装 uiautomation, 读不到就只带你自己的话)
    "ai_context": True,
    "ai_context_max_s": 120,        # 上次发给 AI 超过 120 秒就不带了
    "ai_context_read_reply": True,
    "ai_context_reply_chars": 800,  # AI 回复只取最后 800 字, 只发给 DeepSeek, 不写日志
    # 念出 AI 回复 (edge-tts, 要联网): 发给 AI 后等回复出现并稳定 2 秒, 念最新一条
    "tts": False,
    "tts_only_english": False,
    "tts_voice_en": "en-US-AriaNeural",
    "tts_voice_zh": "zh-CN-XiaoxiaoNeural",
    "tts_rate": "+0%",               # 语速: "-20%" 慢, "+0%" 正常, "+20%" 快
    "tts_max_chars": 2000,
    "tts_stable_s": 1.0,
    # 读文件通道: AI 每次回复后把纯文本写进这个文件, 发发看到文件更新就直接念 (比读窗口快, 也不会念到按钮文字)
    # 文件没更新时, 还是用读窗口的老办法兜底。路径是相对 voice-trigger 文件夹的
    "reply_file": "claude_reply.txt",
    "reply_file_max_age_s": 180,     # 你发出去以后多久内的文件更新才念             # 回复多少秒不再变化就开始念 (Claude 中途停顿久被截断的话调大)
    # 朗读时跳过的界面文字: 一行就等于这些词, 或以它开头 (词至少 4 个字) 且整行很短 (<=40 字), 这行就不念
    "tts_skip_words": ["Claude", "You", "你", "Copy", "Retry", "Edit", "Share", "复制", "重试", "编辑", "分享",
                       "Thought for", "Thinking", "思考", "已思考", "Searched",
                       "Claude can make mistakes", "Claude 可能会出错", "Reply to Claude", "回复 Claude",
                       "【选中的内容】", "【附带的复制内容】", "已发送"],
    "tts_barge_in": True,            # 你一开口就停止朗读 (用音箱外放时会被自己的声音打断, 建议戴耳机)
    "tts_repeat_hotkey": "ctrl+alt+r",
    "tts_stop_hotkey": "ctrl+alt+s",
    # 通话软件正在用麦克风时 (开会/语音通话), 自动发送暂停
    "pause_on_call": False,
    "call_apps": ["teams", "zoom", "wechat", "weixin", "discord", "skype", "feishu", "lark",
                  "dingtalk", "whatsapp", "telegram", "webex", "slack", "qq"],
    "vad_aggressiveness": 3,
    "silence_end_ms": 800,
    "min_speech_ms": 500,
    "min_continuous_ms": 240,
    "no_speech_prob_max": 0.5,
    "avg_logprob_min": -1.0,
    "min_command_chars": 2,
    "dedup_s": 10,
    "energy_ratio": 2.5,
    "min_rms": 300,
    "attach_clipboard": True,
    "attach_only_new": True,
    "attach_max_chars": 4000,
    "image_wait_s": 1.5,
    "attach_window_s": 10,
    "max_speech_s": 15,
    "wait_command_s": 6,
    "send_delay_s": 2,
    "input_box": None,
    "press_enter": True,
    # True = 你说的每一句话(不只是 fafa 指令)都带时间戳存进 daily-journal 的每日日志,
    # 会长期保存在硬盘上, 半夜会发给 DeepSeek 总结成日记。默认关, 想用自己改成 true。
    "log_all_speech": False,
    "daily_journal_dir": "../daily-journal",
}

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480

# 去掉标点和空格, 用来做匹配
_PUNCT = re.compile(r"[\s,，.。!！?？、;；:：\"'“”‘’…~\-—()（）\[\]【】]+")


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    else:
        save_config(cfg)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def normalize(s):
    return _PUNCT.sub("", s).lower()


def find_wake(text, wake_words):
    """
    找第一个唤醒词。没有返回 None, 有返回 (唤醒词前面的话, 唤醒词后面的话)。
    """
    # 在"去标点小写"的文本里找, 再映射回原文位置, 这样 "Fa, fa" 也能命中
    keep = [i for i, ch in enumerate(text) if not _PUNCT.fullmatch(ch)]
    norm = "".join(text[i] for i in keep).lower()
    best = None  # (结束位置)
    for w in wake_words:
        nw = normalize(w)
        if not nw:
            continue
        pos = norm.find(nw)
        if pos != -1:
            end = pos + len(nw)
            if best is None or pos < best[0]:
                best = (pos, end)
    if best is None:
        return None
    before = text[:keep[best[0]]] if best[0] < len(keep) else ""
    before = _PUNCT.sub(" ", before).strip(" ，,。.")
    end_in_norm = best[1]
    if end_in_norm >= len(keep):
        return before, ""
    start_in_text = keep[end_in_norm]
    return before, _PUNCT.sub(" ", text[start_in_text:]).strip(" ，,。.")


def find_command(text, wake_words):
    """
    返回 None: 没有唤醒词
    返回 "":   有唤醒词但后面没话 (等下一句当指令)
    返回 "xxx": 唤醒词后面的指令
    """
    r = find_wake(text, wake_words)
    return None if r is None else r[1]


def _journal_dir(cfg):
    d = Path(cfg.get("daily_journal_dir") or "../daily-journal")
    if not d.is_absolute():
        d = (Path(__file__).resolve().parent / d).resolve()
    return d


def log_speech(cfg, text):
    """log_all_speech 打开时, 把识别出的一句话写进 daily-journal\\logs\\今天.jsonl。"""
    if not cfg.get("log_all_speech"):
        return
    d = _journal_dir(cfg)
    if (d / "PAUSED").exists():  # daily-journal 的 pause.bat 也会暂停语音记录
        return
    try:
        logs = d / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        ts = time.localtime()
        rec = {"t": time.strftime("%H:%M:%S", ts), "src": "voice", "text": text}
        with open(logs / f"{time.strftime('%Y-%m-%d', ts)}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"  写每日日志失败: {e}")


def mark_pasting(cfg):
    """告诉 daily-journal 的剪贴板记录器: 接下来几秒剪贴板变化是我们自己粘贴, 别记。"""
    try:
        d = _journal_dir(cfg)
        if d.exists():
            (d / ".voice_trigger_pasting").write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def calibrate(cfg):
    import pyautogui
    print("把鼠标移到聊天输入框上, 5 秒后记录位置 (别动鼠标)...")
    for i in range(5, 0, -1):
        print(f"  {i} ...  当前位置 {tuple(pyautogui.position())}")
        time.sleep(1)
    x, y = pyautogui.position()
    cfg["input_box"] = [int(x), int(y)]
    save_config(cfg)
    print(f"已保存输入框坐标 ({x}, {y}) 到 {CONFIG_PATH.name}")


def set_clipboard_image(img):
    """把 PIL 图片放进 Windows 剪贴板 (CF_DIB 格式), 聊天框就能 Ctrl+V 粘贴图片。"""
    import io
    import win32clipboard
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    data = buf.getvalue()[14:]  # 去掉 BMP 文件头, 剩下的就是 DIB
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    finally:
        win32clipboard.CloseClipboard()


def window_info(hwnd):
    """窗口句柄 -> (标题, 程序名 如 WINWORD.EXE)"""
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    n = u.GetWindowTextLengthW(hwnd)
    b = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, b, n + 1)
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(520)
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            exe = buf.value.replace("/", "\\").split("\\")[-1]
        k.CloseHandle(h)
    return b.value, exe


LAST_FG = {"title": "", "exe": ""}   # 最近一个不是发发面板的前台窗口 (主循环每 0.3 秒记一次)


def is_panel(title, exe):
    return "发发" in title and exe.lower() in ("pythonw.exe", "python.exe")


def remember_foreground():
    try:
        t, e = foreground_window()
    except Exception:
        return
    if e and not is_panel(t, e):
        LAST_FG["title"], LAST_FG["exe"] = t, e


def foreground_window():
    """现在最前面(你正在用)的窗口: (标题, 程序名)"""
    if sys.platform != "win32":
        return "", ""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = wintypes.HWND
    h = u.GetForegroundWindow()
    return window_info(h) if h else ("", "")


def _match(words, s):
    s = s.lower()
    return any(w.strip().lower() in s for w in words if w.strip())


def route_for(cfg):
    """
    按前台窗口决定怎么发: 返回 (方式, 标题, 程序名)
    方式: "ai" = 点聊天框发送 (原来的方式), "dictation" = 粘贴到当前输入位置, "skip" = 不发
    """
    if not cfg.get("window_routing"):
        return "ai", "", ""
    try:
        title, exe = foreground_window()
    except Exception as e:
        print(f"  [窗口] 读不到前台窗口 ({e}), 按原来的方式发送")
        return "ai", "", ""
    if is_panel(title, exe):
        # 前台是发发自己的面板: 按你切到面板之前用的那个窗口算; 没有记录就按 AI 方式
        if LAST_FG["exe"]:
            print(f"  [窗口] 前台是发发面板, 按之前用的窗口「{LAST_FG['title'][:20]}」算")
            title, exe = LAST_FG["title"], LAST_FG["exe"]
        else:
            return "ai", title, exe
    dic, ai = cfg.get("dictation_windows", []), cfg.get("ai_windows", [])
    if _match(dic, exe):                 # 先看程序名: VS Code 里打开 claude.md 也算听写
        mode = "dictation"
    elif _match(ai, f"{title} {exe}"):
        mode = "ai"
    elif _match(dic, title):
        mode = "dictation"
    else:
        mode = "other"
    return mode, title, exe


ROUTE_NAME = {"ai": "AI 模式 (点聊天框发送)", "dictation": "听写模式 (粘贴到当前位置)",
              "skip": "不在列表里, 不发",
              "other": "其他窗口"}


def paste_here(cfg, text, dry_run=False):
    """听写模式: 粘贴到你正在打字的地方。不点击、不移动鼠标、不回车, 剪贴板用完还原。"""
    if dry_run:
        print(f"  [dry-run] 会粘贴到当前位置: {text}")
        return True
    import pyautogui
    import pyperclip
    try:
        old = pyperclip.paste()
    except Exception:
        old = None
    mark_pasting(cfg)
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    if old is not None:
        pyperclip.copy(old)
    mark_pasting(cfg)
    print(f"  已粘贴: {text}")
    return True


def read_selection(cfg):
    """
    读你在前台窗口里选中(框选)的文字: 模拟按一下 Ctrl+C, 读剪贴板, 再把原来的剪贴板放回去。
    没选中东西时剪贴板不会变, 返回 ""。只在发送的那一刻读一次, 不会在后台一直读。
    """
    import ctypes
    import pyautogui
    import pyperclip
    title, exe = foreground_window()
    if _match(cfg.get("selection_skip_apps", []), exe):
        return "", f"{exe} 里按 Ctrl+C 会中断程序, 不读"
    seq = ctypes.windll.user32.GetClipboardSequenceNumber
    old_text, old_img, _ = read_clipboard()
    before = seq()
    mark_pasting(cfg)
    # 先松开可能还按着的修饰键, 再按 Ctrl+C
    for k in ("alt", "shift", "win"):
        pyautogui.keyUp(k)
    pyautogui.hotkey("ctrl", "c")
    for _ in range(10):  # 最多等 0.5 秒让程序把内容放进剪贴板
        time.sleep(0.05)
        if seq() != before:
            break
    if seq() == before:
        return "", "没有选中内容"
    time.sleep(0.05)
    try:
        sel = pyperclip.paste()
        sel = sel if isinstance(sel, str) else ""
    except Exception:
        sel = ""
    if _match(cfg.get("selection_line_copy_apps", []), exe) and sel.endswith("\n") \
            and sel.rstrip("\r\n").count("\n") == 0:
        sel, exe = "", f"{exe} 没选中 (复制到的是光标所在的整行)"
    # 还原原来的剪贴板
    try:
        if old_img is not None:
            set_clipboard_image(old_img)
        else:
            pyperclip.copy(old_text or "")
    except Exception:
        pass
    mark_pasting(cfg)
    return sel, f"{exe}"


def send_text(cfg, text, image=None, dry_run=False):
    """粘贴文字 (有图片的话再粘贴图片), 然后回车。"""
    if dry_run:
        print(f"  [dry-run] 会发送:\n{text}")
        if image is not None:
            print(f"  [dry-run] 还会粘贴一张图片 {image.size[0]}x{image.size[1]}")
        return
    if not cfg.get("input_box"):
        print("  还没记录输入框坐标, 先运行: python main.py --calibrate")
        return
    import pyautogui
    import pyperclip
    old_text = None
    if image is None:  # 剪贴板是图片时 old 就是这张图, 最后会放回去
        try:
            old_text = pyperclip.paste()
        except Exception:
            old_text = None
    mark_pasting(cfg)
    old_mouse = pyautogui.position()  # 记住鼠标原来的位置
    x, y = cfg["input_box"]
    pyautogui.click(x, y)
    time.sleep(0.2)
    # 1. 粘贴文字
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    # 2. 粘贴图片, 等它出现在输入框里
    if image is not None:
        try:
            set_clipboard_image(image)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(float(cfg.get("image_wait_s", 1.5)))
        except Exception as e:
            print(f"  图片粘贴失败: {e}")
    # 3. 回车发送
    if cfg.get("press_enter", True):
        pyautogui.press("enter")
    time.sleep(0.2)
    pyautogui.moveTo(old_mouse[0], old_mouse[1])  # 鼠标移回原位
    # 4. 还原剪贴板 (图片的情况剪贴板里已经是原来那张图)
    if image is None and old_text is not None:
        pyperclip.copy(old_text)
    mark_pasting(cfg)
    print(f"  已发送: {text}" + ("  + 1 张图片" if image is not None else ""))


LOOPBACK_HINTS = ("立体声混音", "stereo mix", "loopback", "what u hear", "wave out")


def read_clipboard():
    """
    读剪贴板, 返回 (文字, 图片, 指纹)。
    文字: 没有就 ""; 图片: PIL Image 或 None; 指纹: 用来判断是不是"新复制"的。
    """
    try:
        from PIL import ImageGrab, Image
        g = ImageGrab.grabclipboard()
        if isinstance(g, Image.Image):
            import hashlib
            return "", g, "img:" + hashlib.md5(g.convert("RGB").tobytes()).hexdigest()
    except Exception:
        pass
    try:
        import pyperclip
        t = pyperclip.paste()
        t = t if isinstance(t, str) else ""
    except Exception:
        t = ""
    return t, None, "txt:" + t


def with_clipboard(cfg, text, baseline_key, in_window=True, ago=None):
    """
    如果剪贴板里有新复制的文字/图片, 一起带上。
    返回 (要发送的文字, 要粘贴的图片或 None)。
    """
    if not cfg.get("attach_clipboard", True):
        return text, None
    clip, img, key = read_clipboard()
    if cfg.get("attach_only_new", True) and key == baseline_key:
        return text, None  # 上次发送/启动之后没有新复制, 不附带
    if not in_window:
        if ago is not None and (img is not None or clip.strip()):
            print(f"  剪贴板内容是说话前 {ago:.0f} 秒复制的, 超过 "
                  f"{cfg.get('attach_window_s', 10)} 秒, 认为无关, 不附带")
        return text, None
    if img is not None:
        print(f"  附带复制的图片 {img.size[0]}x{img.size[1]}")
        return text, img
    if not clip.strip():
        return text, None
    limit = int(cfg.get("attach_max_chars", 4000))
    if len(clip) > limit:
        print(f"  复制的内容有 {len(clip)} 字, 只附带前 {limit} 字")
        clip = clip[:limit] + "\n...(内容太长, 已截断)"
    print(f"  附带复制内容 {len(clip)} 字")
    return f"{text}\n\n【附带的复制内容】\n{clip}", None


class ClipWatch:
    """记录剪贴板最后一次被(你)改动的时间。Windows 用剪贴板序号, 很便宜, 可以一直查。"""

    def __init__(self):
        try:
            import ctypes
            self._seq = ctypes.windll.user32.GetClipboardSequenceNumber
        except Exception:
            self._seq = None  # 非 Windows: 不做时间限制
        self.last = self._seq() if self._seq else 0
        self.changed_at = None  # 启动前就有的内容不算

    def poll(self, now):
        if self._seq:
            n = self._seq()
            if n != self.last:
                self.last = n
                self.changed_at = now

    def ignore(self):
        """我们自己改的剪贴板 (读选中文字), 不算你复制, 但保留你之前复制的时间。"""
        if self._seq:
            self.last = self._seq()

    def consume(self):
        """我们自己发送时改了剪贴板, 不算你复制; 已附带过的也清掉。"""
        if self._seq:
            self.last = self._seq()
        self.changed_at = None

    def recent(self, cmd_start, window):
        """返回 (是否在时间窗口内, 复制时间比开口早了几秒)。"""
        if not self._seq:
            return True, 0
        if self.changed_at is None:
            return False, None
        ago = cmd_start - self.changed_at
        return ago <= window, ago


def list_mics():
    import sounddevice as sd
    default_in = sd.default.device[0]
    print("可用的输入设备 (把编号填进 config.json 的 mic_device):")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            tag = ""
            if any(h in d["name"].lower() for h in LOOPBACK_HINTS):
                tag = "   <- 这是电脑声音(内录), 不要选"
            elif i == default_in:
                tag = "   <- 当前默认"
            print(f"  {i}: {d['name']}{tag}")


def check_mic(cfg):
    """启动时打印用的是哪个麦克风; 如果选到了"立体声混音"直接退出。"""
    import sounddevice as sd
    dev = cfg.get("mic_device")
    info = sd.query_devices(dev if dev is not None else sd.default.device[0])
    name = info["name"]
    print(f"使用麦克风: {name}")
    if any(h in name.lower() for h in LOOPBACK_HINTS):
        print("这个设备录的是电脑播放的声音, 不是麦克风。")
        print("运行 python main.py --list-mics 找到真正的麦克风, 填进 config.json 的 mic_device")
        sys.exit(1)


class SileroVAD:
    """
    Silero VAD (神经网络), 一次喂一帧 512 个采样点 (16kHz 下 32ms), 返回 0~1 的人声概率。
    模型文件用 faster-whisper 自带的 silero_vad_v6.onnx, 不用另外装 torch。
    原理和 vad-lab 一样, 只是这里直接用 onnxruntime 跑, 并且自己保存模型的"记忆"(h, c)。
    """
    FRAME = 512
    CONTEXT = 64  # 模型要求每帧前面拼上上一帧最后 64 个点

    def __init__(self):
        import numpy as np
        import onnxruntime
        import faster_whisper
        path = Path(faster_whisper.__file__).parent / "assets" / "silero_vad_v6.onnx"
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.sess = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"],
                                                 sess_options=opts)
        self.np = np
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.ctx = np.zeros(self.CONTEXT, dtype=np.float32)

    def prob(self, x):
        """x: float32, 长度 512, 范围 -1~1"""
        np = self.np
        inp = np.concatenate([self.ctx, x]).astype(np.float32)[None, :]
        out, self.h, self.c = self.sess.run(None, {"input": inp, "h": self.h, "c": self.c})
        self.ctx = x[-self.CONTEXT:].astype(np.float32)
        return float(np.asarray(out).ravel()[0])


class SmartTurn:
    """
    Smart Turn v3.2 (pipecat-ai, BSD-2 许可, 8MB, 基于 Whisper-tiny 编码器):
    听最后 8 秒的声音 (语气、语调), 输出"这句话说完了"的概率 0~1。
    特征提取用 faster-whisper 自带的, 跟官方用的 transformers 结果一样, 不用另外装。
    """
    URL = "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx"

    def __init__(self):
        import numpy as np
        import onnxruntime
        from faster_whisper.feature_extractor import FeatureExtractor
        path = Path(__file__).with_name("models") / "smart-turn-v3.2-cpu.onnx"
        if not path.exists():
            import urllib.request
            path.parent.mkdir(exist_ok=True)
            print("下载 Smart Turn 模型 (8MB)...")
            urllib.request.urlretrieve(self.URL, str(path) + ".part")
            Path(str(path) + ".part").replace(path)
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 2
        opts.log_severity_level = 4
        self.sess = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"],
                                                 sess_options=opts)
        self.fe = FeatureExtractor(feature_size=80)
        self.np = np

    def prob(self, audio):
        """audio: float32 16kHz. 返回"说完了"的概率。"""
        np = self.np
        n = 8 * SAMPLE_RATE
        a = audio[-n:] if len(audio) > n else np.pad(audio, (n - len(audio), 0))  # 不够 8 秒前面补 0
        a = (a - a.mean()) / np.sqrt(a.var() + 1e-7)
        feats = self.fe(a, padding=0)[None, :, :800].astype(np.float32)
        return float(self.sess.run(None, {"input_features": feats})[0].ravel()[0])


def mic_frames(cfg, frame_samples):
    """麦克风 -> 一帧一帧的 int16 bytes"""
    import sounddevice as sd
    q = queue.Queue()

    def callback(indata, frames, t, status):
        q.put(bytes(indata))

    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=frame_samples,
                           dtype="int16", channels=1,
                           device=cfg.get("mic_device"), callback=callback):
        while True:
            yield q.get()


def file_frames(path, frame_samples):
    """wav 文件 -> 一帧一帧 (测试用, 文件必须是 16kHz 单声道 16bit), 结尾补 3 秒安静"""
    import wave
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            sys.exit("测试文件必须是 16kHz、单声道、16bit 的 wav")
        data = w.readframes(w.getnframes())
    data += b"\0" * (SAMPLE_RATE * 2 * 3)
    step = frame_samples * 2
    for i in range(0, len(data) - step + 1, step):
        yield data[i:i + step]


def speech_events(cfg, frames_from=None, rt=None):
    """
    持续从麦克风读音频, 用 VAD 切句。产生三种事件:
      ("start", None)   开始说话了
      ("end", pcm)      一句说完, pcm 是 int16 bytes (太短/像噪音时是 b"")
      ("tick", None)    安静时每帧一次, 用来检查要不要发送
    VAD 有两种: silero (默认, 神经网络) / webrtc (原来的)。
    """
    import collections
    import numpy as np

    engine = str(cfg.get("vad_engine", "silero")).lower()
    silero = None
    if engine == "silero":
        try:
            silero = SileroVAD()
            print("VAD: Silero (神经网络)")
        except Exception as e:
            print(f"Silero VAD 加载失败 ({e}), 改用 webrtc VAD")
            engine = "webrtc"
    if engine != "silero":
        import webrtcvad
        vad = webrtcvad.Vad(int(cfg["vad_aggressiveness"]))
        print("VAD: webrtc")
    frame_samples = SileroVAD.FRAME if silero else FRAME_SAMPLES
    frame_ms = frame_samples * 1000 / SAMPLE_RATE  # silero 32ms, webrtc 30ms

    thr = float(cfg.get("silero_threshold", 0.5))
    neg_thr = float(cfg.get("silero_neg_threshold", 0.35))
    start_need = max(1, int(cfg.get("silero_start_frames", 3))) if silero else 1

    # 音量门限: 背景(音乐/风扇)的音量会被慢慢学到, 只有明显比背景大声的人声才算
    energy_ratio = float(cfg.get("energy_ratio", 2.5))
    min_rms = float(cfg.get("min_rms", 300))
    noise = min_rms

    silence_frames_end = int(cfg["silence_end_ms"] / frame_ms)
    auto_frames_end = max(1, int(cfg.get("auto_silence_ms", 500) / frame_ms))
    st = None
    if cfg.get("smart_turn"):
        try:
            st = SmartTurn()
            print(f"Smart Turn: 开 (自动发送时, 停顿 {cfg.get('smart_turn_check_ms', 500) / 1000:.1f}s 后判断说完没, "
                  f"最多等 {cfg.get('smart_turn_max_wait_ms', 3000) / 1000:.1f}s)")
        except Exception as e:
            print(f"Smart Turn 加载失败, 不用它: {e}")
    st_check = max(1, int(cfg.get("smart_turn_check_ms", 500) / frame_ms))
    st_max = max(st_check, int(cfg.get("smart_turn_max_wait_ms", 3000) / frame_ms))
    st_thr = float(cfg.get("smart_turn_threshold", 0.5))
    keep_tail = int(200 / frame_ms)  # 给 Smart Turn 的声音只留 200ms 结尾安静
    text_ok_extra = max(1, int(cfg.get("turn_text_ok_extra_ms", 1000) / frame_ms))
    soft_end = None   # 概率低但文字完整: 停顿到这么多帧就发
    ai_high = float(cfg.get("ai_turn_high", 0.9))
    ai_low = float(cfg.get("ai_turn_low", 0.3))
    rt = rt if rt is not None else {}
    min_frames = int(cfg["min_speech_ms"] / frame_ms)
    max_frames = int(cfg["max_speech_s"] * 1000 / frame_ms)
    min_run = int(cfg.get("min_continuous_ms", 240) / frame_ms)
    pre_roll = int(300 / frame_ms)  # 句子开头多留 300ms, 免得吃掉第一个字

    frames = frames_from(frame_samples) if frames_from else mic_frames(cfg, frame_samples)
    yield ("info", frame_ms)
    history = collections.deque(maxlen=pre_roll + start_need)
    voiced = []
    in_speech = False
    silence = 0
    speech_count = 0
    pre_voiced = 0    # 还没开口时, 连续有声帧数
    run_len = 0       # 当前连续人声帧数
    best_run = 0      # 这句里最长的连续人声
    for frame in frames:
        if len(frame) != frame_samples * 2:
            continue
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(samples * samples)))
        loud = rms > max(min_rms, noise * energy_ratio)
        if silero:
            p = silero.prob(samples / 32768.0)  # 每帧都要喂, 模型要连续的"记忆"
            is_speech = loud and p >= (neg_thr if in_speech else thr)
        else:
            is_speech = loud and vad.is_speech(frame, SAMPLE_RATE)
        # 更新背景音量: 平时慢慢跟上(约 15 秒), 变安静时快速降下来
        if rms < noise:
            noise = 0.9 * noise + 0.1 * rms
        else:
            noise = 0.998 * noise + 0.002 * rms
        noise = max(noise, min_rms / energy_ratio)
        if not in_speech:
            history.append(frame)
            pre_voiced = pre_voiced + 1 if is_speech else 0
            if pre_voiced >= start_need:
                in_speech = True
                voiced = list(history)
                history.clear()
                silence = 0
                speech_count = run_len = best_run = pre_voiced
                pre_voiced = 0
                yield ("start", None)
            else:
                yield ("tick", None)
        else:
            voiced.append(frame)
            if is_speech:
                if silence:  # 又开口了: 之前的判断作废
                    soft_end = None
                    rt.pop("cached", None)
                silence = 0
                speech_count += 1
                run_len += 1
                best_run = max(best_run, run_len)
            else:
                silence += 1
                run_len = 0
            end_need = auto_frames_end if rt.get("auto") else silence_frames_end
            done = len(voiced) >= max_frames
            if st is not None and rt.get("auto") and not done:
                # Smart Turn: 停顿 0.3s 时判断一次; 说完了就发, 没说完就继续等 (你接着说就接上), 最多 3s
                if silence >= st_max:
                    print(f"  [Smart Turn] 等了 {silence * frame_ms / 1000:.1f} 秒, 发送")
                    done = True
                elif soft_end is not None and silence >= soft_end:
                    print(f"  [Smart Turn] 文字是完整句, 停顿 {silence * frame_ms / 1000:.1f}s 没再说话, 发送")
                    done = True
                elif silence == st_check:  # 只判断一次 (后面的安静不送进模型, 再判断结果也一样)
                    cut = len(voiced) - max(0, silence - keep_tail)
                    seg = np.frombuffer(b"".join(voiced[:cut]), dtype=np.int16).astype(np.float32) / 32768
                    p = st.prob(seg)
                    ok_end = p >= st_thr
                    judge = rt.get("judge")
                    textcheck = rt.get("textcheck")
                    high = ai_high if judge is not None else st_thr
                    if judge is not None and ai_low <= p < ai_high:
                        # 拿不准: 先转文字 (先过文字规则), 再问 DeepSeek 这句话说完整没有
                        print(f"  [Smart Turn] 停顿 {silence * frame_ms / 1000:.1f}s, 说完的概率 {p:.2f} -> 拿不准, 问 DeepSeek")
                        t0 = time.time()
                        res, why, txt = judge(seg)
                        dt = time.time() - t0
                        if res is None:
                            print(f"  [AI 说完判断] 没判断成: {why} -> 按 Smart Turn: "
                                  + ("说完了" if ok_end else "没说完") + f"  (共 {dt:.1f}s)")
                        else:
                            ok_end = res
                            print(f"  [AI 说完判断] 「{txt}」 -> {'说完了' if res else '没说完, 继续等'}  "
                                  f"({why}, 转文字+判断共 {dt:.1f}s)")
                        if ok_end and txt:
                            rt["cached"] = (len(voiced), txt)  # 已经转过文字了, 发送时不用再转
                    elif p >= high and textcheck is not None:
                        # 模型说"说完了": 再转文字看一眼句末, 明显没说完 (然后/因为/and...) 就继续等
                        t0 = time.time()
                        txt, why = textcheck(seg)
                        if why:
                            ok_end = False
                            print(f"  [文字规则] 说完的概率 {p:.2f}, 但「{txt}」{why} -> 没说完, "
                                  f"最多再等 {(st_max - silence) * frame_ms / 1000:.1f} 秒 ({time.time() - t0:.1f}s)")
                        else:
                            ok_end = True
                            if txt:
                                rt["cached"] = (len(voiced), txt)
                            print(f"  [Smart Turn] 停顿 {silence * frame_ms / 1000:.1f}s, 说完的概率 {p:.2f} -> 说完了"
                                  f" (文字规则也没拦, {time.time() - t0:.1f}s)")
                    else:
                        if judge is not None:
                            ok_end = p >= ai_high
                        if not ok_end and textcheck is not None:
                            # 概率低 (Smart Turn 对中文常偏低): 转文字看一眼, 句子看着完整就只多等 ~1 秒
                            t0 = time.time()
                            txt, why = textcheck(seg)
                            if txt and not why:
                                soft_end = silence + text_ok_extra
                                rt["cached"] = (len(voiced), txt)
                                print(f"  [Smart Turn] 停顿 {silence * frame_ms / 1000:.1f}s, 说完的概率 {p:.2f} 偏低, "
                                      f"但「{txt}」看着是完整句 -> 再等 {text_ok_extra * frame_ms / 1000:.1f}s 没说话就发"
                                      f" ({time.time() - t0:.1f}s)")
                            else:
                                print(f"  [Smart Turn] 停顿 {silence * frame_ms / 1000:.1f}s, 说完的概率 {p:.2f} -> 没说完"
                                      + (f" (「{txt}」{why})" if why else "")
                                      + f", 最多再等 {(st_max - silence) * frame_ms / 1000:.1f} 秒")
                        else:
                            print(f"  [Smart Turn] 停顿 {silence * frame_ms / 1000:.1f}s, 说完的概率 {p:.2f} -> "
                                  + ("说完了" if ok_end else f"没说完, 最多再等 {(st_max - silence) * frame_ms / 1000:.1f} 秒"))
                    done = ok_end
            elif silence >= end_need:
                done = True
            if done:
                # 敲键盘是一下一下很短的"咔", 人说话会有连续一段人声
                ok = speech_count >= min_frames and best_run >= min_run
                pcm = b"".join(voiced) if ok else b""
                c = rt.pop("cached", None)
                if c is not None and c[0] <= len(voiced) and ok:
                    rt["cached_text"] = c[1]
                soft_end = None
                yield ("end", pcm)
                in_speech = False
                history.clear()
                voiced = []


def add_nvidia_dlls():
    """
    pip 装的 nvidia-cublas-cu12 / nvidia-cudnn-cu12 把 DLL 放在 site-packages\\nvidia\\*\\bin,
    Windows 默认找不到, 这里手动告诉它去哪找。没装就什么都不做。
    """
    import os
    import site
    roots = []
    try:
        roots = site.getsitepackages() + [site.getusersitepackages()]
    except Exception:
        pass
    for r in roots:
        nv = Path(r) / "nvidia"
        if not nv.is_dir():
            continue
        for b in nv.glob("*/bin"):
            try:
                os.add_dll_directory(str(b))
            except (AttributeError, OSError):
                pass
            os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def load_whisper(cfg):
    """按 config 的 device 加载 Whisper。用显卡失败(没 N 卡/缺 DLL/显存不够)就退回 CPU。"""
    import numpy as np
    dev = str(cfg.get("device", "cpu")).lower()
    if dev in ("auto", "cuda", "gpu"):
        add_nvidia_dlls()
    from faster_whisper import WhisperModel
    size = cfg["model_size"]
    if dev in ("auto", "cuda", "gpu"):
        try:
            import ctranslate2
            n = ctranslate2.get_cuda_device_count()
            if n == 0:
                raise RuntimeError("没检测到能用的 NVIDIA 显卡 (或驱动太旧)")
            ct = cfg.get("gpu_compute_type", "float16")
            print(f"加载 Whisper 模型 {size} 到显卡 (cuda, {ct})...")
            model = WhisperModel(size, device="cuda", compute_type=ct)
            # 先试转一段静音: 缺 cuBLAS/cuDNN 的话这里就会报错, 好及时退回 CPU
            list(model.transcribe(np.zeros(16000, dtype=np.float32), language="zh")[0])
            print("Whisper: 显卡加速 (GPU)")
            return model
        except Exception as e:
            print(f"显卡加速用不了, 改用 CPU。原因: {e}")
    print(f"加载 Whisper 模型 {size} (CPU, 第一次会下载, 请等待)...")
    model = WhisperModel(size, device="cpu", compute_type=cfg.get("compute_type", "int8"))
    print("Whisper: CPU")
    return model


AUTO_FLAG = Path(__file__).with_name("AUTO_SEND_ON")


def auto_on():
    return AUTO_FLAG.exists()


def set_auto(on):
    try:
        if on:
            AUTO_FLAG.write_text("on", encoding="utf-8")
        elif AUTO_FLAG.exists():
            AUTO_FLAG.unlink()
    except OSError as e:
        print(f"  切换自动发送失败: {e}")


VK_NAMES = {f"f{i}": 0x6F + i for i in range(1, 13)}
VK_NAMES.update({"space": 0x20, "pause": 0x13, "scrolllock": 0x91})


def start_hotkey(combo, on_press, label="开/关自动发送"):
    """Windows 全局热键 (RegisterHotKey), 在后台线程里等按键。"""
    if not combo or sys.platform != "win32":
        return
    import threading

    def loop():
        import ctypes
        import ctypes.wintypes
        mods = {"ctrl": 2, "control": 2, "alt": 1, "shift": 4, "win": 8}
        parts = [p.strip() for p in combo.lower().split("+") if p.strip()]
        try:
            mod = sum(mods[p] for p in parts[:-1])
            key = parts[-1]
            vk = VK_NAMES.get(key) or ord(key.upper())
        except (KeyError, TypeError, IndexError):
            print(f"热键写法不对: {combo} (例子: ctrl+alt+f)")
            return
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, mod | 0x4000, vk):  # 0x4000 = 按住不重复触发
            print(f"热键 {combo} 注册失败 (可能被别的软件占用了), 可以用面板按钮切换")
            return
        print(f"热键 {combo}: {label}")
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                on_press()

    threading.Thread(target=loop, daemon=True).start()


def beep(on):
    try:
        import winsound
        winsound.Beep(1200 if on else 500, 150)
    except Exception:
        pass


AI_USAGE = Path(__file__).with_name("ai_filter_usage.json")
AI_PROMPT = ("用户开着麦克风, 说的每句话都会被转成文字 (可能有错别字)。"
             "判断【最后一句】是不是用户想发给 AI 助手的话: 提问、下指令、请 AI 帮忙、跟 AI 讨论问题都算「是」。"
             "跟旁边的人聊天、打电话、自言自语、感叹、背景里别人的声音算「否」。只回答一个字: 是 或 否。")


def unfinished_reason(cfg, text):
    """用文字规则看这句话是不是明显没说完, 是的话返回原因 (给日志看), 否则返回 None"""
    if not cfg.get("turn_rules", True):
        return None
    t = text.strip()
    if not t:
        return None
    if re.search(r"(…|\.\.\.|。。|，|,|、)$", t):
        return "句末是省略号/逗号"
    core = re.sub(r"[\s。．.!！?？，,、…~～:：;；\"'“”‘’]+", "", t)
    if not core:
        return None
    fill = sum(1 for ch in core if ch in "嗯呃额啊哦呀")
    if len(core) <= 12 and fill / len(core) >= 0.4:
        return "几乎都是「嗯/啊」"
    tail = re.sub(r"[\s。．.!！?？~～\"'“”‘’]+$", "", t)
    m = re.search(r"([A-Za-z']+)$", tail)
    if m:
        w = m.group(1).lower()
        if w in [x.lower() for x in cfg.get("turn_hang_words_en", [])]:
            return f"句末是「{w}」"
    else:
        for w in sorted(cfg.get("turn_hang_words_zh", []), key=len, reverse=True):
            if w and tail.endswith(w):
                return f"句末是「{w}」"
    return None


AI_TURN_PROMPT = ("下面是语音转成的文字 (可能有错别字), 说话人停顿了一下。"
                  "判断这句话是不是已经说完整了: 意思完整、可以直接发出去算「是」;"
                  "话说到一半、明显后面还有内容 (比如以「然后」「因为」「就是」「帮我把」结尾) 算「否」。"
                  "只回答一个字: 是 或 否。")


def read_ai_reply(cfg, max_chars=800, time_limit=2.5):
    """
    用 Windows UI Automation 读 AI 聊天窗口里的文字, 取最后 max_chars 字 (大致就是 AI 最近的回复)。
    找窗口: 先看聊天框坐标所在的窗口, 再按 ai_windows 关键词找标题/程序名对得上的窗口。
    Claude / 浏览器是 Chromium 内核, 平时不开放页面文字, 第一次被问时才打开, 所以读不到会等 1 秒再读一次。
    只在判断时读一次。返回 (文字, 说明)。
    """
    if sys.platform != "win32":
        return "", "不是 Windows"
    try:
        import uiautomation as auto
    except ImportError:
        return "", "没装 uiautomation (在面板点「安装并测试读取 AI 回复」)"
    t0 = time.time()
    words = [w.lower() for w in cfg.get("ai_windows", ["claude", "豆包", "deepseek", "chatgpt"])]

    def desc(w):
        try:
            title = w.Name or ""
            _, exe = window_info(w.NativeWindowHandle)
        except Exception:
            title, exe = "", ""
        return title, exe

    def poke(top):
        """给 Chromium 的页面子窗口发 WM_GETOBJECT, 让它打开无障碍接口 (页面文字才读得到)"""
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            hwnd = top.NativeWindowHandle
            found = []
            proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def cb(h, _):
                b = ctypes.create_unicode_buffer(64)
                u.GetClassNameW(h, b, 64)
                if b.value.startswith("Chrome_RenderWidgetHostHWND"):
                    found.append(h)
                return True
            u.EnumChildWindows(hwnd, proc(cb), 0)
            res = wintypes.DWORD()
            for h in found:
                for obj in (-4, -25):  # OBJID_CLIENT, UiaRootObjectId
                    u.SendMessageTimeoutW(h, 0x003D, 0, obj, 2, 500, ctypes.byref(res))
            return len(found)
        except Exception:
            return 0

    def collect(top):
        """先试整页文字 (Document 的 TextPattern), 不行就收集所有 Text 控件的文字"""
        texts, n, docs = [], 0, 0
        t1 = time.time()
        for c, _ in auto.WalkControl(top, maxDepth=200):
            n += 1
            if time.time() - t1 > time_limit:
                break
            try:
                ct = c.ControlTypeName
                if ct == "DocumentControl":
                    docs += 1
                    try:
                        whole = c.GetTextPattern().DocumentRange.GetText(-1)
                        if whole and whole.strip():
                            return whole, n, docs
                    except Exception:
                        pass
                elif ct == "TextControl" and c.Name:
                    texts.append(c.Name)
            except Exception:
                pass
        return "\n".join(texts), n, docs

    with auto.UIAutomationInitializerInThread():
        cands = []
        if cfg.get("input_box"):
            try:
                c = auto.ControlFromPoint(*map(int, cfg["input_box"]))
                if c is not None:
                    cands.append(c.GetTopLevelControl())
            except Exception:
                pass
        try:
            for w in auto.GetRootControl().GetChildren():
                t, e = desc(w)
                if any(k in f"{t} {e}".lower() for k in words):
                    cands.append(w)
        except Exception:
            pass
        if not cands:
            return "", "没找到 AI 窗口 (聊天框位置上没窗口, 也没有标题含 claude/豆包/deepseek 的窗口)"
        seen, notes = set(), []
        for w in cands:
            try:
                h = w.NativeWindowHandle
            except Exception:
                h = id(w)
            if h in seen:
                continue
            seen.add(h)
            title, exe = desc(w)
            try:
                if w.GetWindowPattern().WindowVisualState == 2:  # 2 = 最小化
                    notes.append(f"「{title[:20]}」({exe}) 是最小化的, 读不到 (让它露出来, 被别的窗口挡住没关系)")
                    continue
            except Exception:
                pass
            text, n, docs = collect(w)
            tries = 0
            while not text.strip() and tries < 3:
                tries += 1
                poke(w)
                time.sleep(1.0)  # 让 Chromium 打开无障碍接口后再读
                text, n2, docs = collect(w)
                n = max(n, n2)
            if text.strip():
                took = time.time() - t0
                text = text.strip()
                return text[-max_chars:], (f"从「{title[:20]}」({exe}) 读到 {min(len(text), max_chars)} 字"
                                           f" ({took:.1f}s)")
            notes.append(f"「{title[:20]}」({exe}) 看了 {n} 个控件, 没有文字")
    hint = (" — 读不到页面文字。先确认 Claude 窗口没有最小化; 还不行就把 Claude 完全退出再打开一次"
            " (会重新开放文字); 再不行可以给 Claude 加启动参数 --force-renderer-accessibility")
    return "", "; ".join(notes) + hint


def ai_is_for_ai(cfg, text, context, last_ai=None):
    """
    问 DeepSeek 这句话是不是对 AI 说的。
    last_ai = {"text": 上次发给 AI 的话, "at": 时间}: 刚跟 AI 说过话时一起给 DeepSeek 参考。
    """
    ctx = "\n".join(context) if context else "(没有)"
    extra = ""
    if cfg.get("ai_context", True) and last_ai and last_ai.get("text"):
        ago = time.time() - last_ai["at"]
        if ago <= float(cfg.get("ai_context_max_s", 120)):
            extra = f"她 {ago:.0f} 秒前刚对 AI 说了: {last_ai['text'][:300]}\n"
            note = f"带上 {ago:.0f} 秒前发给 AI 的话"
            if cfg.get("ai_context_read_reply", True):
                try:
                    reply, why = read_ai_reply(cfg, int(cfg.get("ai_context_reply_chars", 800)))
                except Exception as e:
                    reply, why = "", f"出错: {type(e).__name__}"
                if reply:
                    extra += f"AI 窗口里最近的内容 (末尾):\n{reply}\n"
                    tail = " ".join(reply.split())[-20:]
                    why += f", 结尾: …{tail}"
                note += f"; AI 回复: {why}"
            print(f"  [上下文] {note}")
    if extra:
        user = (f"{extra}\n她刚和 AI 对话过, 紧接着的追问、补充、对 AI 回复的回应 (比如「那…呢」「为什么」「再详细点」「不对」)"
                f"应该判为「是」。\n\n前面几句 (参考):\n{ctx}\n\n她现在说: {text}")
    else:
        user = f"前面几句 (参考):\n{ctx}\n\n最后一句: {text}"
    return ask_deepseek(cfg, AI_PROMPT, user)


def ask_deepseek(cfg, system, user):
    """
    问 DeepSeek 一个"是/否"问题。
    返回 (结果 True/False/None, 说明)。None = 没问成 (超时/出错/没 key/超过每日上限)。
    """
    import os
    import urllib.request
    key = (cfg.get("ai_filter_api_key") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        return None, "没填 DeepSeek key"
    today = time.strftime("%Y-%m-%d")
    try:
        usage = json.loads(AI_USAGE.read_text(encoding="utf-8"))
    except Exception:
        usage = {}
    n = usage.get(today, 0)
    if n >= int(cfg.get("ai_filter_daily_limit", 500)):
        return None, f"今天已经问了 {n} 次, 到上限了"
    usage = {today: n + 1}
    try:
        AI_USAGE.write_text(json.dumps(usage), encoding="utf-8")
    except OSError:
        pass
    body = {
        "model": cfg.get("ai_filter_model", "deepseek-flash"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 3, "temperature": 0, "stream": False,
    }
    if cfg.get("ai_filter_thinking", "disabled"):
        body["thinking"] = {"type": cfg.get("ai_filter_thinking", "disabled")}
    url = cfg.get("ai_filter_base_url", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get("ai_filter_timeout_s", 3))) as r:
            ans = json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"].strip()
    except Exception as e:
        msg = type(e).__name__
        code = getattr(e, "code", None)
        if code:
            msg += f" {code}" + (" (key 不对?)" if code == 401 else " (余额不足?)" if code == 402 else "")
        elif "timed out" in str(e).lower():
            msg = "超时"
        return None, msg
    dt = time.time() - t0
    return ans.startswith("是"), f"{ans} ({dt:.1f}s, 今天第 {n + 1} 次)"


def mic_users():
    """
    现在正在用麦克风的程序 (从 Windows 隐私设置的记录里读: LastUsedTimeStop = 0 表示正在用)。
    返回小写名字列表, 不包括 python 自己。
    """
    if sys.platform != "win32":
        return []
    import winreg
    out = []
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

    def scan(path):
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        i = 0
        while True:
            try:
                name = winreg.EnumKey(k, i)
            except OSError:
                break
            i += 1
            if name == "NonPackaged":
                scan(path + "\\" + name)
                continue
            try:
                sk = winreg.OpenKey(k, name)
                stop, _ = winreg.QueryValueEx(sk, "LastUsedTimeStop")
                start, _ = winreg.QueryValueEx(sk, "LastUsedTimeStart")
                if stop == 0 and start > 0:
                    n = name.lower().replace("#", "\\").split("\\")[-1]
                    if "python" not in n:
                        out.append(n)
            except OSError:
                pass

    scan(base)
    return out


# ---------------------------------------------------------------
# 朗读 AI 回复 (TTS): edge-tts 生成 mp3, Windows 自带的 MCI 播放 (不用装播放器)
# ---------------------------------------------------------------
_TTS_LIVE = {"mtime": None, "on": None}


def tts_enabled(cfg):
    """念不念 AI 回复: 每次发完都看一眼 config.json 的 "tts" 和 tts_* 朗读设置 (改了立刻生效, 不用重启)"""
    try:
        m = CONFIG_PATH.stat().st_mtime
        if m != _TTS_LIVE["mtime"]:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                new = json.load(f)
            _TTS_LIVE["on"] = bool(new.get("tts", False))
            for k, v in new.items():   # 语速/声音/跳过词这些朗读设置也一起换成新的 (Speaker 用的是同一个 cfg)
                if k.startswith("tts_") and not k.endswith("hotkey"):
                    cfg[k] = v
            cfg["tts"] = _TTS_LIVE["on"]
            _TTS_LIVE["mtime"] = m
        return _TTS_LIVE["on"]
    except Exception:
        return bool(cfg.get("tts"))


TTS_CMD = Path(__file__).with_name("TTS_CMD")   # 面板按钮写 "repeat" / "stop", 后台每 0.3 秒看一次
TTS_DIR = Path(__file__).with_name("tts_cache")


def tts_lang(text):
    """中文字超过 20% 算中文, 否则算英文"""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    letters = sum(1 for ch in text if ch.isalpha())
    return "zh" if letters and cjk / letters > 0.2 else "en"


TIME_RE = re.compile(
    r"(?:(?:今天|昨天|前天|上午|下午|晚上|凌晨|中午|早上)\s*)*\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:AM|PM|am|pm))?"
    r"|\d+\s*(?:秒|分钟|小时|天)前|\b\d+\s*(?:s|sec|min|mins|minutes?|h|hr|hours?)\s+ago\b|刚刚|\bjust now\b",
    re.I)


DATE_RE = re.compile(
    r"\d{4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*日?|\d{1,2}\s*月\s*\d{1,2}\s*日|(?:星期|礼拜|周)[一二三四五六日天]")
LABEL_PREFIX = re.compile(r"^(?:Claude\s*)?(?:已回复|回复了|Claude replied|Replied|You said|你说)\s*[:：]\s*", re.I)


# 聊天界面上的按钮文字, 不管 config 里怎么写都会跳过 (一整行就是这些词才跳过)
UI_BUTTON_WORDS = ["添加回应", "回复", "复制", "复制消息", "编辑", "编辑消息", "更多选项", "重试", "分享",
                   "Add reaction", "Reply", "Copy", "Copy message", "Edit", "Edit message", "More options",
                   "Retry", "Share"]
# 无障碍提示 (给读屏软件的操作说明): 出现这些说法的短行整行跳过
A11Y_HINT_RE = re.compile(r"按\s*(Tab|Esc|Escape|Enter|回车|空格|方向)\s*键|快速回应|焦点(进入|移到|离开)|"
                          r"\bpress (tab|escape|esc|enter)\b|quick reactions?|keyboard shortcut|screen reader", re.I)


# 任务/线程卡片上的状态文字: 这一行和它上面紧挨着的短标题行都不念
STATUS_RE = re.compile(r"^[\s\-–—·•|]*(处理中|进行中|已完成|已暂停|排队中|思考中|正在思考|Working|Processing|"
                       r"In progress|Done|Completed|Queued|Thinking)[.。…\s]*$", re.I)


def drop_repeats(lines, min_len=6):
    """
    大段重复只念一遍: 一行的内容如果已经完整出现在前面念过的内容里 (哪怕前面是分好几行、
    或者这行只是前面某段被截断的开头), 就跳过; 如果这行比前面某行更完整 (前面那行是它的截断版), 用这行替换。
    """
    def key(x):
        return re.sub(r"[\W_]+", "", x).lower()
    out = []
    for x in lines:
        if STATUS_RE.match(x):
            # 状态行上面那行通常是卡片标题 (短、没有句号), 一起去掉
            if out and len(out[-1]) <= 30 and not re.search(r"[。！？.!?]$", out[-1]):
                out.pop()
            continue
        k = key(x)
        if len(k) >= min_len and k in "".join(key(o) for o in out):
            continue
        out = [o for o in out if not (len(key(o)) >= min_len and key(o) in k)]  # 前面的截断版换成完整版
        out.append(x)
    return out


def skip_ui_lines(text, words):
    """去掉聊天界面上的标签行 (发言人名字、时间日期、按钮、无障碍朗读标签), 并去掉重复的段落"""
    ws = [w.strip() for w in list(words) + UI_BUTTON_WORDS if w.strip()]
    rows = []   # (正文, 是否从「已回复：」这类标签里拆出来的)
    for line in text.splitlines():
        # ￼ (U+FFFC) 是图标/按钮占位符, 零宽字符也去掉
        s = re.sub(r"[\ufffc\ufffd\u200b-\u200f\u2060\ufeff]", "", line).strip()
        if not s:
            continue
        if len(s) <= 80 and A11Y_HINT_RE.search(s):
            continue
        # 无障碍按钮标签: 「显示 Claude 已回复：… 的消息操作」/ "Show message actions"
        if s.endswith("的消息操作") or re.search(r"message actions$", s, re.I):
            continue
        # 时间日期: 14:49 / 2:49 PM / 下午 2:49 / 昨天 / 刚刚 / 3 分钟前 / 2026年11月7日星期六
        s = DATE_RE.sub(" ", TIME_RE.sub(" ", s)).strip(" ·•|,，-–—")
        s = " ".join(s.split())
        if not s or re.fullmatch(r"(今天|昨天|前天|刚刚|现在|Just now|Now|Today|Yesterday|"
                                 r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*|[年月日\s\d]+)", s, re.I):
            continue
        low = s.lower()
        if any(low == w.lower() or (len(w) >= 4 and low.startswith(w.lower()) and len(s) <= 40) for w in ws):
            continue
        m = LABEL_PREFIX.match(s)
        if m:
            s = s[m.end():].strip()
            if not s:
                continue
        rows.append((s, bool(m)))

    def key(x):
        return re.sub(r"[\W_]+", "", x).lower()
    plain = {key(x) for x, lab in rows if not lab}
    out, seen = [], set()
    for x, lab in rows:
        k = key(x)
        if lab and any(k in pk for pk in plain):   # 标签里那份 (常常只截了第一句) 和正文重复, 用正文那份
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return "\n".join(drop_repeats(out))


def clean_for_tts(text, skip_words=None):
    """去掉代码、链接、markdown 符号, 只留正文"""
    if skip_words:
        text = skip_ui_lines(text, skip_words)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    keep = []
    for line in text.splitlines():
        s = line.strip().replace("**", "").replace("__", "")
        if not s:
            continue
        sym = sum(1 for ch in s if ch in "{}[]();=<>/\\|$&^%~")
        if len(s) > 8 and sym / len(s) > 0.15:   # 像代码的行不念
            continue
        s = re.sub(r"^[#>*\-+\d.)\s]+", "", s)    # 标题/列表/引用符号
        s = s.replace("*", "")
        keep.append(s)
    out = "\n".join(keep).strip()
    # 去掉回复末尾聊天界面上的按钮文字 (Copy / Retry 之类)
    ui = ("Copy", "Retry", "Edit", "Share", "复制", "重试", "编辑", "分享")
    changed = True
    while changed:
        changed = False
        for w in ui:
            if out.endswith(w):
                out = out[:-len(w)].rstrip()
                changed = True
    return out


def split_chunks(text, size=220, first=15):
    """按句子切成一小段一小段; 第一段特别短 (一句话), 合成快, 能早点开口"""
    parts = re.split(r"(?<=[。！？!?.;；，,\n])", text)
    out, cur = [], ""
    for p in parts:
        lim = first if not out else size
        if len(cur) + len(p) > lim and cur:
            out.append(cur)
            cur = ""
        cur += p
    if cur.strip():
        out.append(cur)
    return [c.strip() for c in out if c.strip()]


class EdgeWS:
    """
    常驻的 edge-tts 连接: 连一次, 之后每一句都在同一条 websocket 上合成。
    edge-tts 自带的 Communicate 每句都新建连接 (你那边每次要 ~1 秒); 连着的时候一句只要 0.1~0.4 秒。
    服务器闲置大约 30~60 秒会断开, 断了就自动重连再试一次。
    """

    def __init__(self):
        import asyncio
        import threading
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.ws = self.session = None
        self.last_used = 0.0
        self.lock = None

    def _run(self, coro, timeout=25):
        import asyncio
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _close(self):
        try:
            if self.ws is not None:
                await self.ws.close()
            if self.session is not None:
                await self.session.close()
        except Exception:
            pass
        self.ws = self.session = None

    async def _connect(self):
        import aiohttp
        from edge_tts import communicate as C
        from edge_tts.drm import DRM
        await self._close()
        self.session = aiohttp.ClientSession(trust_env=True)
        self.ws = await self.session.ws_connect(
            f"{C.WSS_URL}&ConnectionId={C.connect_id()}"
            f"&Sec-MS-GEC={DRM.generate_sec_ms_gec()}&Sec-MS-GEC-Version={C.SEC_MS_GEC_VERSION}",
            compress=15, headers=DRM.headers_with_muid(C.WSS_HEADERS), ssl=C._SSL_CTX)
        await self.ws.send_str(
            f"X-Timestamp:{C.date_to_string()}\r\nContent-Type:application/json; charset=utf-8\r\n"
            "Path:speech.config\r\n\r\n"
            '{"context":{"synthesis":{"audio":{"metadataoptions":{"sentenceBoundaryEnabled":"false",'
            '"wordBoundaryEnabled":"false"},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n')
        self.last_used = time.time()

    def _stale(self):
        return self.ws is None or self.ws.closed or time.time() - self.last_used > 25

    async def _synth_once(self, text, voice, rate):
        import asyncio
        import aiohttp
        from xml.sax.saxutils import escape
        from edge_tts import communicate as C
        ssml = ("<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
                f"<voice name='{voice}'><prosody pitch='+0Hz' rate='{rate}' volume='+0%'>"
                f"{escape(C.remove_incompatible_characters(text))}</prosody></voice></speak>")
        t0 = time.time()
        await self.ws.send_str(C.ssml_headers_plus_data(C.connect_id(), C.date_to_string(), ssml))
        audio, first = bytearray(), None
        while True:
            m = await asyncio.wait_for(self.ws.receive(), 15)
            if m.type == aiohttp.WSMsgType.TEXT:
                if "Path:turn.end" in m.data:
                    break
            elif m.type == aiohttp.WSMsgType.BINARY:
                hl = int.from_bytes(m.data[:2], "big")
                if b"Path:audio" in m.data[2:2 + hl] and len(m.data) > hl + 2:
                    if first is None:
                        first = time.time() - t0
                    audio += m.data[2 + hl:]
            else:
                raise ConnectionError(f"连接断了 ({m.type})")
        if not audio:
            raise ConnectionError("没收到声音")
        self.last_used = time.time()
        return bytes(audio), first or 0.0

    async def _synth(self, text, voice, rate):
        import asyncio
        if self.lock is None:
            self.lock = asyncio.Lock()
        async with self.lock:   # 同一条连接一次只合成一句
            t_conn = 0.0
            if self._stale():
                t = time.time()
                await self._connect()
                t_conn = time.time() - t
            try:
                audio, first = await self._synth_once(text, voice, rate)
            except Exception:
                if t_conn:
                    raise
                t = time.time()
                await self._connect()
                t_conn = time.time() - t
                audio, first = await self._synth_once(text, voice, rate)
            return audio, t_conn, first

    def synth(self, text, voice, rate="+0%"):
        """返回 (mp3 字节, 建连耗时, 首包耗时)"""
        return self._run(self._synth(text, voice, rate))

    def warm(self):
        """没连着 (或闲太久可能被断了) 就连上; 返回建连耗时, 已经连着返回 None"""
        async def go():
            if self.lock is None:
                import asyncio
                self.lock = asyncio.Lock()
            async with self.lock:
                if not self._stale():
                    return None
                t = time.time()
                await self._connect()
                return time.time() - t
        return self._run(go())


_EDGE = None


def edge_conn():
    global _EDGE
    if _EDGE is None:
        _EDGE = EdgeWS()
    return _EDGE


class Speaker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.active = False
        self.last_text = ""
        self._stop = False
        self._thread = None

    def say(self, text, t_ready=None):
        import threading
        self.stop()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.last_text = text
        self._stop = False
        self.active = True
        self._t_ready = t_ready or time.time()
        self._thread = threading.Thread(target=self._run, args=(text,), daemon=True)
        self._thread.start()

    def prewarm(self):
        """你话一发出去就先连上语音服务器 (连接一直留着), 等 AI 回复的这几秒里把连接准备好"""
        import threading
        if time.time() - getattr(self, "_warm_at", 0) < 3:
            return
        self._warm_at = time.time()

        def run():
            try:
                t = edge_conn().warm()
                if t is not None:
                    print(f"[朗读] 预热: 连上语音服务器 {t:.1f}s (连接留着, 念的时候直接用)")
            except Exception as e:
                print(f"[朗读] 预热失败: {type(e).__name__}: {str(e)[:80]}")
        threading.Thread(target=run, daemon=True).start()

    def repeat(self):
        if self.last_text:
            print("[朗读] 再念一遍")
            self.say(self.last_text)
        else:
            print("[朗读] 还没有念过东西")

    def stop(self):
        if self.active:
            self._stop = True

    def _run(self, text):
        import asyncio
        import ctypes
        try:
            import edge_tts
        except ImportError:
            print("[朗读] 没装 edge-tts (在面板点「安装并试听朗读」)")
            self.active = False
            return
        mci = ctypes.windll.winmm.mciSendStringW
        buf = ctypes.create_unicode_buffer(64)
        tts_enabled(self.cfg)   # 先读一下最新的语速/声音 (面板刚改的)
        lang = tts_lang(text)
        voice = self.cfg.get("tts_voice_zh" if lang == "zh" else "tts_voice_en",
                             "zh-CN-XiaoxiaoNeural" if lang == "zh" else "en-US-AriaNeural")
        rate = str(self.cfg.get("tts_rate", "+0%"))
        if not re.fullmatch(r"[+-]\d+%", rate):
            rate = "+0%"
        print(f"[朗读] 用 {voice}, 语速 {rate}")
        TTS_DIR.mkdir(exist_ok=True)
        try:
            import threading
            chunks = split_chunks(text)
            files, errs = {}, {}

            stats = {}

            def synth(i):  # 后台合成第 i 段 (念第 i-1 段的时候就在合成, 段和段之间不用等)
                f = TTS_DIR / f"part{i % 3}.mp3"
                try:
                    try:   # 用常驻连接 (快)
                        audio, t_conn, t_first = edge_conn().synth(chunks[i], voice, rate)
                        f.write_bytes(audio)
                        stats[i] = (t_conn, t_first)
                    except Exception as e1:   # 不行就用 edge-tts 原来的方式
                        print(f"[朗读] 常驻连接出错, 改用普通方式: {type(e1).__name__}: {str(e1)[:60]}")
                        asyncio.run(edge_tts.Communicate(chunks[i], voice, rate=rate).save(str(f)))
                    files[i] = f
                except Exception as e:
                    errs[i] = e

            mci("close vt_tts", None, 0, None)
            t_syn = time.time()
            th = threading.Thread(target=synth, args=(0,), daemon=True)
            th.start()
            for i in range(len(chunks)):
                th.join()
                if self._stop:
                    break
                if i in errs:
                    raise errs[i]
                if i == 0:
                    now = time.time()
                    tc, tf = stats.get(0, (None, None))
                    detail = (f" (建连 {tc:.2f}s, 首包 {tf:.2f}s, 第一句 {len(chunks[0])} 字)"
                              if tc is not None else "")
                    print(f"[朗读] 合成第一句 {now - t_syn:.1f}s{detail}, 从回复写完到开口 {now - self._t_ready:.1f}s")
                if i + 1 < len(chunks):
                    th = threading.Thread(target=synth, args=(i + 1,), daemon=True)
                    th.start()
                f = files[i]
                mci(f'open "{f}" type mpegvideo alias vt_tts', None, 0, None)
                mci("play vt_tts", None, 0, None)
                while not self._stop:
                    time.sleep(0.05)
                    mci("status vt_tts mode", buf, 64, None)
                    if buf.value != "playing":
                        break
                mci("stop vt_tts", None, 0, None)
                mci("close vt_tts", None, 0, None)
        except Exception as e:
            print(f"[朗读] 出错: {type(e).__name__}: {str(e)[:100]}")
        finally:
            mci("close vt_tts", None, 0, None)
            if self._stop:
                print("[朗读] 停止")
            self.active = False


def _flat(s):
    return " ".join(s.split())


def _lines(s):
    """每行内部压缩空格, 去掉空行, 但保留分行 (分行才能认出界面标签行和代码行)"""
    return "\n".join(" ".join(l.split()) for l in s.splitlines() if l.strip())


def _common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _common_suffix(a, b):
    n = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x != y:
            break
        n += 1
    return n


def watch_reply(cfg, sent_text, speaker, rt):
    """
    发给 AI 后在后台等回复: 每秒读一次聊天窗口的全部文字,
    找到你发的那句, 它后面新出现的文字就是回复; 回复 2 秒不再变化就念出来。最多等 3 分钟。
    """
    import threading

    def run():
        key = _flat(re.sub(r"【附带的复制内容】.*|【选中的内容】.*", "", sent_text, flags=re.S))[:14]
        if len(key) < 2:
            return
        my_id = rt["tts_watch"] = rt.get("tts_watch", 0) + 1
        time.sleep(0.6)

        def after_msg():
            full, why = read_ai_reply(cfg, 200000, time_limit=3)
            full = _lines(full)
            # 你那句在页面上可能被折成几行: 允许 key 的字之间有换行/空格
            pat = r"\s*".join(re.escape(ch) for ch in key.replace(" ", ""))
            ms = list(re.finditer(pat, full))
            if not ms:
                return None, why
            rest = full[ms[-1].end():]
            nl = rest.find("\n")   # 你那句剩下的部分 (同一行) 跳过, 从下一行开始算
            return (rest[nl + 1:] if nl >= 0 else ""), why

        base, why = None, ""
        for _ in range(5):  # 读不到就多试几次 (窗口刚切换时 Chromium 可能还没准备好)
            base, why = after_msg()
            if base is not None:
                break
            time.sleep(1.0)
        if base is None:
            print(f"[朗读] 在聊天窗口里没找到你刚发的那句, 不念 ({why[:160]})")
            return
        last, same_since, t0 = None, time.time(), time.time()
        t_first = None   # 回复第一次出现的时间
        while time.time() - t0 < 180:
            time.sleep(0.3)
            if rt.get("tts_watch") != my_id:
                return  # 又发了新消息, 这个不等了
            cur, _ = after_msg()
            if cur is None:
                continue
            p = _common_prefix(base, cur)
            sfx = _common_suffix(base[p:], cur[p:])
            reply = cur[p:len(cur) - sfx].strip()
            if reply != last:
                last, same_since = reply, time.time()
                if t_first is None and len(reply) >= 5:
                    t_first = time.time()
                    speaker.prewarm()   # 回复开始出现了, 再热一次 (离开念只差一两秒)
                continue
            if len(reply) >= 5 and time.time() - same_since >= float(cfg.get("tts_stable_s", 1.0)):
                if rt.get("file_spoke_at", 0) > rt.get("last_send_at", 0):
                    return  # 文件通道已经念过这条了
                rt["uia_spoke_at"] = time.time()
                text = clean_for_tts(reply, cfg.get("tts_skip_words", DEFAULT_CONFIG["tts_skip_words"]))
                lang = tts_lang(text)
                if cfg.get("tts_only_english") and lang != "en":
                    print(f"[朗读] 回复是中文 ({len(text)} 字), 设置了只念英文, 不念")
                    return
                limit = int(cfg.get("tts_max_chars", 2000))
                text = text[:limit]
                try:  # 把这次要念的全文存一份, 方便你核对念的是什么 (只存最后一次)
                    TTS_DIR.mkdir(exist_ok=True)
                    (TTS_DIR / "last_reply.txt").write_text(
                        "=== 从窗口取到的原文 ===\n" + reply + "\n\n=== 过滤后要念的 ===\n" + text, encoding="utf-8")
                except Exception:
                    pass
                print(f"[朗读] {len(text)} 字, {'英文' if lang == 'en' else '中文'}, 开头: {_flat(text)[:40]}")
                t_now = time.time()
                print(f"[朗读] 从回复出现到确认写完 {t_now - (t_first or t_now):.1f}s "
                      f"(停 {cfg.get('tts_stable_s', 1.0)}s 不变才算写完)")
                speaker.say(text, t_now)
                return
        print("[朗读] 3 分钟没等到稳定的回复, 不念了")

    threading.Thread(target=run, daemon=True).start()


def watch_reply_file(cfg, speaker, rt):
    """
    读文件通道: 每 0.15 秒看一眼 reply_file 有没有更新, 更新了就直接念 (不用等窗口稳定)。
    只念你发出去以后 reply_file_max_age_s 秒内的更新; 窗口那边已经念过的不重复念。
    """
    import threading
    name = cfg.get("reply_file")
    if not name:
        return
    path = Path(name)
    if not path.is_absolute():
        path = Path(__file__).with_name(name)
    print(f"[朗读] 读文件通道: 开 (看 {path.name}, 没更新时用读窗口兜底)")

    def run():
        try:
            last = path.stat().st_mtime_ns
        except OSError:
            last = None
        while True:
            time.sleep(0.15)
            try:
                m = path.stat().st_mtime_ns
            except OSError:
                continue
            if m == last:
                continue
            last = m
            t_seen = time.time()
            try:
                raw = path.read_text(encoding="utf-8-sig")
            except Exception as e:
                print(f"[朗读] 读回复文件失败: {e}")
                continue
            lines = raw.splitlines()
            if lines and lines[0].startswith("#"):   # 第一行可以是 "# 2026-11-06 16:20:01" 之类的标记
                lines = lines[1:]
            body = "\n".join(lines).strip()
            if len(body) < 2 or not tts_enabled(cfg):
                continue
            sent = rt.get("last_send_at", 0)
            if not sent or t_seen - sent > float(cfg.get("reply_file_max_age_s", 180)):
                print("[朗读] 回复文件更新了, 但最近没发过消息, 不念")
                continue
            if rt.get("uia_spoke_at", 0) > sent:
                print("[朗读] 回复文件更新了, 但窗口那边已经念过这条, 不重复念")
                continue
            text = clean_for_tts(body)
            if cfg.get("tts_only_english") and tts_lang(text) != "en":
                print(f"[朗读] 回复是中文 ({len(text)} 字), 设置了只念英文, 不念")
                continue
            text = text[:int(cfg.get("tts_max_chars", 2000))]
            rt["file_spoke_at"] = t_seen
            rt["tts_watch"] = rt.get("tts_watch", 0) + 1   # 让读窗口的那个停下
            print(f"[朗读] 从文件读到回复 {len(text)} 字 (发出后 {t_seen - sent:.1f}s), 开头: {_flat(text)[:40]}")
            speaker.say(text, t_seen)

    threading.Thread(target=run, daemon=True).start()


def bench(cfg):
    """用 bench.wav (约 15 秒, 3 句话) 测速度: 每种组合先热身一次, 再计时一次。"""
    import wave
    import numpy as np
    add_nvidia_dlls()
    from faster_whisper import WhisperModel
    with wave.open(str(Path(__file__).with_name("bench.wav")), "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    sec = len(audio) / SAMPLE_RATE
    rows = []
    for size in ("small", "medium"):
        for dev, ct in (("cpu", cfg.get("compute_type", "int8")),
                        ("cuda", cfg.get("gpu_compute_type", "float16"))):
            try:
                m = WhisperModel(size, device=dev, compute_type=ct)
                run1 = lambda: "".join(x.text for x in m.transcribe(
                    audio, language="zh", beam_size=5, initial_prompt="以下是普通话的句子。")[0])
                run1()
                t0 = time.perf_counter()
                text = run1()
                dt = time.perf_counter() - t0
                rows.append(f"{size:7s} {dev:5s} {dt:6.2f} 秒   {text[:30]}")
            except Exception as e:
                rows.append(f"{size:7s} {dev:5s}  失败: {str(e)[:60]}")
            print(rows[-1])
    print(f"\n==== 结果 (音频 {sec:.0f} 秒, 越小越快) ====")
    print("\n".join(rows))


def run(cfg, dry_run=False, test_file=None):
    import numpy as np

    if test_file:
        dry_run = True  # 用文件测试时一定不真的发送
        print(f"测试文件: {test_file} (只打印, 不发送)")
    else:
        check_mic(cfg)
    model = load_whisper(cfg)
    # 提示词只用来引导简体中文。不要放唤醒词: 放了以后 Whisper 听到噪音也容易"脑补"出 fafa
    prompt = "以下是普通话的句子。"
    delay = float(cfg.get("send_delay_s", 2))
    words = cfg["wake_words"]
    mode = str(cfg.get("trigger_mode", "prefix")).lower()
    if mode not in ("prefix", "suffix", "both", "auto"):
        mode = "prefix"
    buf_max = float(cfg.get("buffer_max_s", 60))
    buf_gap = float(cfg.get("buffer_gap_s", 8))
    mode_name = {"prefix": "先说发发再说指令", "suffix": "先说话, 最后说发发发送",
                 "both": "两种都行", "auto": "测试: 自动发送"}[mode]
    print(f"开始监听。唤醒词: {words}   触发方式: {mode} ({mode_name})   Ctrl+C 退出")

    # 时钟: 正常用真实时间; 用文件测试时按"读了多少帧"算时间, 这样 2 秒延迟也能测
    sim = {"t": 0.0, "step": 0.03}
    dropped = {}   # 刚被判成"不发"的一段 (自动发送, AI 窗口): {"piece", "at", "win"}

    def clock():
        return sim["t"] if test_file else time.time()

    waiting_until = 0.0   # 只说了唤醒词时, 在这个时间之前的下一句当指令
    pending = []          # 已识别、还没发送的指令片段
    pending_suffix = False  # True = 这条是"结尾说发发"触发的, 等待期间的新话不合并进来
    send_at = None        # 到这个时间还没再说话, 就发送; None = 正在说话, 先不发
    buf = []              # suffix 模式: 还没发的话 [(开口时间, 说完时间, 文字)], 只在内存里

    last_sent = {"text": "", "at": 0.0}
    last_ai = {"text": "", "at": 0.0}   # 上次发给 AI 的话 (只在内存里)
    clip_baseline = read_clipboard()[2]  # 启动时剪贴板里已有的东西不算"新复制"
    watch = ClipWatch()
    window = float(cfg.get("attach_window_s", 10))
    last_start = clock()   # 最近一次开口的时间
    cmd_start = clock()    # 这条指令开始说的时间

    def prune_buf(t_start):
        """丢掉太旧的; 跟上一句隔太久就整个清空 (说明是另一件事了)"""
        if buf and t_start - buf[-1][1] > buf_gap:
            print(f"  [缓冲] 停顿超过 {buf_gap:g} 秒, 清空前面攒的 {len(buf)} 句")
            buf.clear()
        while buf and clock() - buf[0][1] > buf_max:
            buf.pop(0)

    def add_buf(text, t_start):
        text = text.strip(" ，,。.?？!！")
        if text:
            prune_buf(t_start)
            buf.append((t_start, clock(), text))
            print(f"  [缓冲] 已攒 {len(buf)} 句, 说「发发」就发送")

    def flush(route=None):
        nonlocal pending, send_at, clip_baseline, pending_suffix
        text = "，".join(pending)
        pending, send_at, pending_suffix = [], None, False
        t = clock()
        if route is None:
            route = route_for(cfg)
        mode, wt, we = route
        if cfg.get("window_routing"):
            print(f"  [窗口] 「{wt or '(无标题)'}」({we}) -> {ROUTE_NAME[mode]}")
        if mode == "skip":
            return
        if mode == "other":
            # 说了发发 (或者 AI 已经判断过是对 AI 说的) -> 按 AI 模式发送
            print("  [窗口] 其他窗口, 按 AI 模式发送 (点聊天框)")
            mode = "ai"
        watch.poll(t)
        sel, sel_read = "", False

        def grab_selection():
            nonlocal sel_read
            sel_read = True
            try:
                got, where = read_selection(cfg)
            except Exception as e:
                got, where = "", f"出错: {e}"
            watch.ignore()  # 刚才那次 Ctrl+C 是我们按的, 不算你复制
            if got.strip():
                limit = int(cfg.get("selection_max_chars", 6000))
                note = ""
                if len(got) > limit:
                    note = f", 太长只带前 {limit} 字"
                    got = got[:limit] + "\n...(选中内容太长, 已截断)"
                print(f"  [选中文字] 从 {where} 读到 {len(got)} 字{note}")
                return got
            print(f"  [选中文字] {where}")
            return ""

        can_read = not dry_run and sys.platform == "win32"
        if mode == "dictation" and cfg.get("dictation_selection_to_ai", True) and can_read:
            sel = grab_selection()
            if sel:
                print("  [窗口] 听写窗口里有选中内容 -> 当作问 AI, 发到聊天框 (不在文档里打字)")
                mode = "ai"
        if mode == "dictation":
            if normalize(text) == last_sent["text"] and t - last_sent["at"] < cfg.get("dedup_s", 10):
                print(f"  和刚才的一样, 不重复粘贴: {text}")
                return
            last_sent["text"], last_sent["at"] = normalize(text), t
            paste_here(cfg, text, dry_run)
            return
        if not sel_read and cfg.get("attach_selection") and can_read:
            sel = grab_selection()
        ok, ago = watch.recent(cmd_start, window)
        full, image = with_clipboard(cfg, text, clip_baseline, ok, ago)
        if sel:
            # 选中内容放在说的话后面; 剪贴板附带的内容 (如果有) 放在最后
            head, sep, rest = full.partition("\n\n【附带的复制内容】")
            full = f"{head}\n\n【选中的内容】\n{sel}" + (sep + rest if sep else "")
        if image is not None:
            import hashlib
            dedup_key = normalize(full) + hashlib.md5(image.tobytes()).hexdigest()
        else:
            dedup_key = normalize(full)
        if dedup_key == last_sent["text"] and t - last_sent["at"] < cfg.get("dedup_s", 10):
            print(f"  和刚才发的一样, 不重复发送: {text}")
            return
        last_sent["text"], last_sent["at"] = dedup_key, t
        if send_text(cfg, full, image, dry_run) is not False:
            last_ai["text"], last_ai["at"] = text, time.time()  # 给下次判断当上下文
            if tts_enabled(cfg) and not dry_run:
                rt["last_send_at"] = time.time()
                speaker.prewarm()
                watch_reply(cfg, text, speaker, rt)
        clip_baseline = read_clipboard()[2]  # 附带过的内容下次不再附带
        watch.consume()

    def start_pending(piece, start, suffix):
        nonlocal pending, cmd_start, send_at, pending_suffix
        if len(normalize(piece)) < cfg.get("min_command_chars", 2):
            print("  指令太短, 忽略")
            return
        pending = [piece]
        pending_suffix = suffix
        cmd_start = start
        send_at = clock() + delay
        print(f"  {delay:g} 秒内没再说话就发送...")

    frames_from = (lambda n: file_frames(test_file, n)) if test_file else None
    rt = {"auto": auto_on() or mode == "auto", "checked": 0.0, "call": "", "call_checked": 0.0}
    recent = []   # 最近听到的几句, 给 AI 判断当上下文
    ai_filter = bool(cfg.get("ai_filter"))
    call_apps = [a.lower() for a in cfg.get("call_apps", [])]
    if ai_filter:
        has_key = bool(cfg.get("ai_filter_api_key")) or bool(__import__("os").environ.get("DEEPSEEK_API_KEY"))
        print(f"只发对 AI 说的话: 开 (DeepSeek 判断){'' if has_key else '  ⚠ 还没填 key, 在面板里填'}")
    if cfg.get("pause_on_call"):
        print("通话软件用麦克风时暂停自动发送: 开")
    if mode == "auto":
        mode = "both"  # --mode auto 只用于测试: 自动发送开, 关掉后按 both

    def toggle_auto():
        set_auto(not auto_on())
        beep(auto_on())

    speaker = Speaker(cfg)
    if not test_file and not dry_run:
        watch_reply_file(cfg, speaker, rt)
    if not test_file:
        start_hotkey(cfg.get("auto_hotkey", "ctrl+alt+f"), toggle_auto)
        start_hotkey(cfg.get("tts_repeat_hotkey", "ctrl+alt+r"), speaker.repeat, "再念一遍")
        start_hotkey(cfg.get("tts_stop_hotkey", "ctrl+alt+s"), speaker.stop, "停止朗读")
        print("念出 AI 回复: " + ("开" if cfg.get("tts") else "关") + " (改 config.json 的 tts 立刻生效, 不用重启)")
    print(f"自动发送 (不用说发发, 停顿 {cfg.get('auto_silence_ms', 500)}ms 就发): "
          f"{'开' if rt['auto'] else '关'}")
    relax_ns = str(cfg.get("vad_engine", "silero")).lower() == "silero"
    PROMPTS = {"zh": prompt, "en": "The following is an English sentence."}
    lang_cfg = str(cfg.get("language") or "auto").lower()
    print(f"识别语言: {dict(auto='自动 (中文/英文)', zh='中文', en='英文 (英语练习)').get(lang_cfg, lang_cfg)}")

    def pick_lang(audio):
        """auto: 先让 Whisper 听一下是中文还是英文; 拿不准或者是别的语言就按中文"""
        if lang_cfg in ("zh", "en"):
            return lang_cfg
        try:
            lang, prob, allp = model.detect_language(audio)
            probs = dict(allp)
            zh, en = probs.get("zh", 0.0), probs.get("en", 0.0)
            pick = "en" if en > zh and en >= 0.5 else "zh"
            print(f"  [语言] {pick} (英文 {en:.2f} / 中文 {zh:.2f})")
            return pick
        except Exception as e:
            print(f"  [语言] 检测失败, 按中文 ({type(e).__name__})")
            return "zh"

    def transcribe(audio):
        lang = pick_lang(audio)
        segs, _ = model.transcribe(audio, language=lang,
                                   initial_prompt=PROMPTS.get(lang, prompt), beam_size=5,
                                   vad_filter=False,
                                   condition_on_previous_text=False)
        kept = []
        for sg in segs:
            # 置信度低 / 很可能不是人声的片段丢掉 (键盘声、咳嗽等)
            # Silero 已经确认过是人声, no_speech 只有在 logprob 也偏低时才算数
            # (否则"发发帮我..."这种短句容易被误丢)
            ns_bad = sg.no_speech_prob > cfg.get("no_speech_prob_max", 0.5)
            if relax_ns:
                ns_bad = ns_bad and sg.avg_logprob < -0.5
            if ns_bad or sg.avg_logprob < cfg.get("avg_logprob_min", -1.0):
                print(f"  [丢弃, 不像人声] {sg.text.strip()}  "
                      f"(no_speech={sg.no_speech_prob:.2f}, logprob={sg.avg_logprob:.2f})")
                continue
            kept.append(sg.text)
        return "".join(kept).strip()

    def judge(audio):
        """Smart Turn 拿不准时用: 转文字, 先过文字规则, 再问 DeepSeek 说完整没有。返回 (结果, 说明, 文字)"""
        txt = transcribe(audio)
        if not txt:
            return None, "没识别出文字", ""
        rule = unfinished_reason(cfg, txt)
        if rule:
            return False, f"文字规则: {rule}, 没问 DeepSeek", txt
        res, why = ask_deepseek(cfg, AI_TURN_PROMPT, txt)
        return res, why, txt

    def textcheck(audio):
        """Smart Turn 说"说完了"时用: 转文字看句末。返回 (文字, 没说完的原因 或 None)"""
        txt = transcribe(audio)
        return txt, (unfinished_reason(cfg, txt) if txt else None)

    if cfg.get("smart_turn") and cfg.get("turn_rules", True):
        rt["textcheck"] = textcheck
        print("文字规则判断没说完: 开 (句末是 然后/因为/and... 或省略号时继续等)")

    if cfg.get("ai_turn") and cfg.get("smart_turn"):
        rt["judge"] = judge
        print(f"DeepSeek 辅助判断说完没: 开 (Smart Turn 概率在 "
              f"{cfg.get('ai_turn_low', 0.3)}~{cfg.get('ai_turn_high', 0.9)} 之间时问)")

    for kind, pcm in speech_events(cfg, frames_from, rt):
        if kind == "info":       # speech_events 告诉我们一帧多长
            sim["step"] = pcm / 1000
            continue
        sim["t"] += sim["step"]
        now = clock()
        watch.poll(now)
        if not test_file and cfg.get("pause_on_call") and now - rt["call_checked"] > 2:
            rt["call_checked"] = now  # 每 2 秒看一下有没有通话软件在用麦克风
            try:
                users = [u for u in mic_users() if any(a in u for a in call_apps)]
            except Exception:
                users = []
            c = users[0] if users else ""
            if c != rt["call"]:
                rt["call"] = c
                rt["checked"] = 0.0
                print(f"[通话] {c} 正在用麦克风, 自动发送暂停" if c else "[通话] 结束, 自动发送恢复原来的设置")
        if not test_file and now - rt["checked"] > 0.3:  # 每 0.3 秒看一下开关有没有变
            rt["checked"] = now
            if cfg.get("window_routing"):
                remember_foreground()
            if TTS_CMD.exists():  # 面板上的「再念一遍」「停止念」
                try:
                    c = TTS_CMD.read_text(encoding="utf-8").strip()
                    TTS_CMD.unlink()
                except OSError:
                    c = ""
                if c == "repeat":
                    speaker.repeat()
                elif c == "stop":
                    speaker.stop()
            a = auto_on() and not rt["call"]
            if a != rt["auto"]:
                rt["auto"] = a
                buf.clear()
                waiting_until = 0.0
                print(f"[模式] 自动发送 {'开' if a else '关'}")

        if kind == "tick":
            if pending and send_at is not None and now >= send_at:
                flush()
            continue

        if kind == "start":
            last_start = now
            rt["tts_drop"] = False
            if speaker.active:
                if cfg.get("tts_barge_in", True):
                    print("[朗读] 你开口了, 停止朗读")
                    speaker.stop()
                else:
                    rt["tts_drop"] = True  # 外放时这段可能是 TTS 的声音, 不发
            if pending and not pending_suffix:
                send_at = None  # 她又开口了, 先别发
            continue

        # kind == "end": 一句说完
        text = ""
        cached = rt.pop("cached_text", None)
        if pcm and cached:
            text = cached  # DeepSeek 判断说完时已经转过文字了
        elif pcm:
            text = transcribe(np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0)
        now = clock()
        if text and rt.pop("tts_drop", False):
            print(f"  [朗读中] 听到「{text[:20]}」, 可能是朗读的回声, 不发")
            text = ""
        if text:
            print(f"[听到] {text}")
            log_speech(cfg, text)

        if pending:
            if pending_suffix:
                # 已经说了结尾的发发, 等待发送期间的新话留到下一轮, 不改发送时间
                if text:
                    w = find_wake(text, words)
                    add_buf(" ".join(x for x in w if x) if w else text, last_start)
                continue
            # 等待发送期间又说了话: 合并进同一条指令, 重新计时
            if text:
                cmd = find_command(text, words)
                piece = cmd if cmd else (text if cmd is None else "")
                if piece:
                    pending.append(piece)
                    print(f"  合并: {'，'.join(pending)}")
            send_at = now + delay
            continue

        if not text:
            continue

        if rt["auto"]:
            # 自动发送: 这句话直接发 (去掉里面的"发发"), 不等
            w = find_wake(text, words)
            piece = " ".join(x for x in w if x) if w else text
            route = route_for(cfg)
            if route[0] != "dictation":   # 听写时保留句末标点
                piece = piece.strip(" ，,。.?？!！")
            if len(normalize(piece)) < cfg.get("auto_min_chars", 2):
                recent.append(text)
                continue

            def remember_drop(p, r):
                """被判成"不发"的一段先记着: 紧接着的下一段要发的话, 把它拼到前面 (一句话被停顿切成两段时)"""
                dropped.update(piece=p, at=clock(), win=(r[1], r[2]))

            def merge_drop(p, r):
                d = dict(dropped)
                dropped.clear()
                if d and (r[1], r[2]) == d["win"] and \
                        last_start - d["at"] <= float(cfg.get("merge_dropped_s", 4)):
                    print(f"  [合并了前一段] 「{d['piece']}」 + 这一段一起发")
                    return d["piece"] + "，" + p
                return p
            if route[0] == "other":
                # 其他窗口: 问 DeepSeek 是不是对 AI 说的; 是 -> 按 AI 模式发, 否/出错/没 key -> 不发
                print(f"  [窗口] 其他窗口「{route[1] or '(无标题)'}」({route[2]}) -> 问 DeepSeek")
                ok, why = ai_is_for_ai(cfg, piece, recent[-int(cfg.get("ai_filter_context", 3)):], last_ai)
                recent.append(text)
                del recent[:-10]
                if ok:
                    print(f"  [AI 判断] 是对 AI 说的: {why} -> 按 AI 模式发送")
                    piece = merge_drop(piece, route)
                    route = ("ai", route[1], route[2])
                    pending, pending_suffix, cmd_start = [piece], True, last_start
                    flush(route)
                else:
                    print(f"  [AI 判断] {'不是对 AI 说的' if ok is False else '没判断成'}: {why} -> 不发")
                    remember_drop(piece, route)
                continue
            if ai_filter and route[0] == "ai":  # 听写模式不用判断"是不是对 AI 说的"
                ok, why = ai_is_for_ai(cfg, piece, recent[-int(cfg.get("ai_filter_context", 3)):], last_ai)
                recent.append(text)
                del recent[:-10]
                if ok is None:
                    send = cfg.get("ai_filter_on_error", "drop") == "send"
                    print(f"  [AI 判断] 没判断成: {why} -> {'照样发' if send else '不发'}")
                    if not send:
                        remember_drop(piece, route)
                        continue
                elif not ok:
                    print(f"  [AI 判断] 不是对 AI 说的: {why} -> 不发")
                    remember_drop(piece, route)
                    continue
                else:
                    print(f"  [AI 判断] 是对 AI 说的: {why} -> 发送")
                piece = merge_drop(piece, route)
            pending, pending_suffix, cmd_start = [piece], True, last_start
            flush(route)
            continue

        wake = find_wake(text, words)
        if now < waiting_until:
            # 刚才只说了发发, 这一句就是指令
            if wake is not None and not wake[0] and not wake[1]:
                waiting_until = now + cfg["wait_command_s"]  # 又只说了唤醒词, 继续等
                continue
            waiting_until = 0.0
            start_pending(wake[1] if wake and wake[1] else text, last_start, False)
            continue

        if mode == "prefix":
            if wake is None:
                continue
            if not wake[1]:
                waiting_until = now + cfg["wait_command_s"]
                print(f"  唤醒了, 请在 {cfg['wait_command_s']} 秒内说指令...")
                continue
            start_pending(wake[1], last_start, False)
            continue

        # suffix / both: 先攒着, 听到发发再发
        prune_buf(last_start)
        if wake is None:
            add_buf(text, last_start)
            continue
        before, after = wake
        if mode == "both" and not before and (after or not buf):
            # 发发在最前面 -> 按原来的方式: 发发后面的话就是指令
            if buf:
                print(f"  [缓冲] 发发在句首, 当作新指令, 丢掉前面攒的 {len(buf)} 句")
                buf.clear()
            if after:
                start_pending(after, last_start, False)
            else:
                waiting_until = now + cfg["wait_command_s"]
                print(f"  唤醒了, 请在 {cfg['wait_command_s']} 秒内说指令...")
            continue
        before = before.strip(" ，,。.?？!！")
        parts = [t for _, _, t in buf] + ([before] if before else [])
        first = buf[0][0] if buf else last_start
        buf.clear()
        if after:  # 发发后面还有话, 留给下一轮
            add_buf(after, last_start)
        if not parts:
            print("  说了发发, 但前面没有攒下的话")
            continue
        print(f"  结尾唤醒: 把前面 {len(parts)} 段话合成一条")
        start_pending("，".join(parts), first, True)

    if test_file and pending:  # 文件读完了还没到发送时间, 直接发
        flush()


LOG_PATH = Path(__file__).with_name("voice-trigger.log")


class TimeStampWriter:
    """日志每一行前面加上时间 (时:分:秒.毫秒), 看得出每一步的先后和间隔"""

    def __init__(self, f):
        self.f = f
        self.at_start = True
        self.lock = __import__("threading").Lock()

    def write(self, s):
        with self.lock:
            out = []
            for part in s.splitlines(keepends=True):
                if self.at_start and part.strip():
                    t = time.time()
                    out.append(time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d} ")
                out.append(part)
                self.at_start = part.endswith("\n") or (self.at_start and not part.strip())
            self.f.write("".join(out))
        return len(s)

    def flush(self):
        self.f.flush()

    def __getattr__(self, name):
        return getattr(self.f, name)


def setup_log():
    """后台运行(pythonw)没有窗口, 把输出写进 voice-trigger.log; 超过 5MB 就清空重来。"""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 5 * 1024 * 1024:
            LOG_PATH.unlink()
    except OSError:
        pass
    f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = TimeStampWriter(f)
    print(f"\n===== 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} =====")


def single_instance():
    """防止开了两个(比如开机自启了, 又手动双击一次)。"""
    if sys.platform != "win32":
        return True
    import ctypes
    k = ctypes.windll.kernel32
    single_instance.handle = k.CreateMutexW(None, False, "voice-trigger-fafa")
    return k.GetLastError() != 183  # 183 = 已经存在


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true", help="记录输入框坐标")
    ap.add_argument("--list-mics", "--list-devices", action="store_true", help="列出麦克风")
    ap.add_argument("--dry-run", action="store_true", help="只打印不发送")
    ap.add_argument("--log", action="store_true", help="输出写到 voice-trigger.log (后台运行用)")
    ap.add_argument("--bench", action="store_true", help="测 small/medium 在 CPU/显卡上转一句话要几秒")
    ap.add_argument("--window", action="store_true", help="5 秒后看看前台是哪个窗口、会用哪种方式")
    ap.add_argument("--probe-reply", action="store_true", help="试试能不能读到 AI 窗口里的回复")
    ap.add_argument("--tts-test", help="试听: 念一句话")
    ap.add_argument("--gpu-check", action="store_true", help="测试显卡加速能不能用")
    ap.add_argument("--test-file", help="用 16kHz 单声道 wav 代替麦克风测试 (只打印不发送)")
    ap.add_argument("--mode", choices=["prefix", "suffix", "both", "auto"], help="临时指定触发方式")
    ap.add_argument("--vad", choices=["silero", "webrtc"], help="临时指定 VAD")
    ap.add_argument("--smart-turn", action="store_true", help="临时打开 Smart Turn")
    args = ap.parse_args()
    if args.log or sys.stdout is None:
        setup_log()

    cfg = load_config()
    if args.mode:
        cfg["trigger_mode"] = args.mode
    if args.vad:
        cfg["vad_engine"] = args.vad
    if args.smart_turn:
        cfg["smart_turn"] = True
    if args.list_mics:
        list_mics()
    elif args.calibrate:
        calibrate(cfg)
    elif args.test_file:
        run(cfg, True, args.test_file)  # 测试不占"只能开一个"的名额
    else:
        if args.bench:
            bench(cfg)
            return
        if args.window:
            print("5 秒内切到你想测的窗口...")
            time.sleep(5)
            mode, t, e = route_for(dict(cfg, window_routing=True))
            print(f"前台窗口: 「{t}」({e}) -> {ROUTE_NAME[mode]}")
            return
        if args.tts_test:
            sp = Speaker(cfg)
            print(f"结果: 试听 ({'中文' if tts_lang(args.tts_test) == 'zh' else '英文'})")
            sp.say(args.tts_test)
            time.sleep(0.5)
            while sp.active:
                time.sleep(0.2)
            return
        if args.probe_reply:
            reply, why = read_ai_reply(cfg, 800, time_limit=5)
            print(f"结果: {why}")
            if reply:
                flat = " ".join(reply.split())
                print(f"开头: {flat[:60]}…")
                print(f"结尾: …{flat[-60:]}")
            return
        if args.gpu_check:
            add_nvidia_dlls()
            import ctranslate2
            print(f"ctranslate2 看到的 NVIDIA 显卡数量: {ctranslate2.get_cuda_device_count()}")
            m = load_whisper(dict(cfg, device="cuda"))
            if getattr(m.model, "device", "cpu") == "cuda":
                cfg["device"] = "auto"
                save_config(cfg)
                print("成功! config.json 已改成 device = auto, 重启 voice-trigger 后生效")
            else:
                print("显卡加速没开成, 继续用 CPU (config 没改)")
                sys.exit(1)
            return
        if not single_instance():
            print("已经有一个 voice-trigger 在运行了, 这个退出。")
            return
        try:
            run(cfg, args.dry_run)
        except KeyboardInterrupt:
            print("\n已退出")
        except Exception:
            import traceback
            traceback.print_exc()
            raise


if __name__ == "__main__":
    main()
