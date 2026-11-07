# -*- coding: utf-8 -*-
"""麦克风设备、音频帧来源、正在使用麦克风的程序。"""
import queue
import sys

from ..constants import SAMPLE_RATE

LOOPBACK_HINTS = ("立体声混音", "stereo mix", "loopback", "what u hear", "wave out")

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
