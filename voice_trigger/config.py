# -*- coding: utf-8 -*-
"""config.json 读写与默认值。schema 与重构前完全一致。"""
import json
import re

from .paths import CONFIG_PATH

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
    # 目标对话窗口: 所有话都发到这里 (不再识别微信/Word/听写窗口, 也没有窗口路由)。
    # 用 python main.py --mark-target 标记当前前台窗口; 结构见 voice_trigger/target.py。
    # 激活失败时用 --calibrate 标定的 click_rel_x/click_rel_y 点一下输入框兜底。
    "target_window": None,
    # 激活窗口只保证窗口在前台, 不保证输入框有焦点 (Electron 里焦点停在上次点的地方)。
    # 开: 标定过点击点的话, 激活成功后也先点一下输入框再粘贴。 关: 只靠激活后的焦点。
    "click_before_paste": True,
    # 说话时带上选中文字: 发送前在你正在用的窗口按一下 Ctrl+C 读选中的内容, 读完还原剪贴板
    "attach_selection": False,
    "selection_max_chars": 6000,
    # 这些程序没选中东西时按 Ctrl+C 会复制整行 (VS Code), 复制到的正好是"一整行"就当没选中
    "selection_line_copy_apps": ["code.exe", "cursor.exe", "pycharm64.exe", "pycharm.exe", "idea64.exe",
                                 "clion64.exe", "webstorm64.exe", "goland64.exe", "rider64.exe", "studio64.exe"],
    # 这些程序里 Ctrl+C 是"中断", 不去读选中内容
    "selection_skip_apps": ["windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
                            "conhost.exe", "wt.exe", "mintty.exe", "putty.exe"],
    # 判断"是不是对 AI 说的"时带上对话上下文: 你上次发给 AI 的话 (和距今几秒),
    # 还会试着读 AI 窗口里最后的回复 (要装 uiautomation, 读不到就只带你自己的话)
    "ai_context": True,
    # 念回复时按这些关键词找 AI 窗口 (只是读窗口文字用, 跟发到哪里无关)
    "reply_window_words": ["claude", "豆包", "deepseek", "chatgpt"],
    "ai_context_max_s": 120,        # 上次发给 AI 超过 120 秒就不带了
    "ai_context_read_reply": False,  # UIA 读窗口在终端场景容易读错窗口, 默认关 (聊天窗口可手动开)
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
    "press_enter": True,
    # True = 你说的每一句话(不只是 fafa 指令)都带时间戳存进 daily-journal 的每日日志,
    # 会长期保存在硬盘上, 半夜会发给 DeepSeek 总结成日记。默认关, 想用自己改成 true。
    "log_all_speech": False,
    "daily_journal_dir": "../daily-journal",
}

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
