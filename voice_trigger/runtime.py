# -*- coding: utf-8 -*-
"""常驻主循环: 听音 -> 识别 -> 唤醒词 -> 发送; 以及 bench 和单实例检查。"""
import sys
import time

from .ai import AI_TURN_PROMPT, ai_is_for_ai, ask_deepseek, unfinished_reason
from .asr import load_whisper
from .audio.gpu import add_nvidia_dlls
from .audio.mic import check_mic, file_frames, mic_users
from .audio.vad import speech_events
from .constants import SAMPLE_RATE
from .input.autoflag import auto_on, set_auto
from .input.clipboard import ClipWatch, read_clipboard, with_clipboard
from .input.hotkey import beep, start_hotkey
from .input.paste import read_selection, send_text
from .journal import log_speech
from .paths import BENCH_WAV, TTS_CMD
from .reply import watch_reply, watch_reply_file
from .tts import Speaker, tts_enabled
from .wake import find_command, find_wake, normalize

def bench(cfg):
    """用 bench.wav (约 15 秒, 3 句话) 测速度: 每种组合先热身一次, 再计时一次。"""
    import wave
    import numpy as np
    add_nvidia_dlls()
    from faster_whisper import WhisperModel
    with wave.open(str(BENCH_WAV), "rb") as w:
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

    def flush():
        nonlocal pending, send_at, clip_baseline, pending_suffix
        text = "，".join(pending)
        pending, send_at, pending_suffix = [], None, False
        t = clock()
        watch.poll(t)
        sel = ""
        if not dry_run and sys.platform == "win32" and cfg.get("attach_selection"):
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
                sel = got
            else:
                print(f"  [选中文字] {where}")
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
        if send_text(cfg, full, image, dry_run):
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
            # 自动发送: 这句话直接发到标记的窗口 (去掉里面的"发发"), 不等
            w = find_wake(text, words)
            piece = " ".join(x for x in w if x) if w else text
            piece = piece.strip(" ，,。.?？!！")
            if len(normalize(piece)) < cfg.get("auto_min_chars", 2):
                recent.append(text)
                continue
            if ai_filter:
                ok, why = ai_is_for_ai(cfg, piece, recent[-int(cfg.get("ai_filter_context", 3)):], last_ai)
                recent.append(text)
                del recent[:-10]
                if ok is None:
                    send = cfg.get("ai_filter_on_error", "drop") == "send"
                    print(f"  [AI 判断] 没判断成: {why} -> {'照样发' if send else '不发'}")
                    if not send:
                        continue
                elif not ok:
                    print(f"  [AI 判断] 不是对 AI 说的: {why} -> 不发")
                    continue
                else:
                    print(f"  [AI 判断] 是对 AI 说的: {why} -> 发送")
            pending, pending_suffix, cmd_start = [piece], True, last_start
            flush()
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

def single_instance():
    """防止开了两个(比如开机自启了, 又手动双击一次)。"""
    if sys.platform != "win32":
        return True
    import ctypes
    k = ctypes.windll.kernel32
    single_instance.handle = k.CreateMutexW(None, False, "voice-trigger-fafa")
    return k.GetLastError() != 183  # 183 = 已经存在
