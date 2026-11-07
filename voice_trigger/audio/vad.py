# -*- coding: utf-8 -*-
"""VAD 切句: Silero / webrtc 两种引擎, 以及 Smart Turn。"""
import time
from pathlib import Path

from ..constants import FRAME_SAMPLES, SAMPLE_RATE
from ..paths import MODELS_DIR
from .mic import mic_frames

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
        path = MODELS_DIR / "smart-turn-v3.2-cpu.onnx"
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
