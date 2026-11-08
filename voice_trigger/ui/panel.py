# -*- coding: utf-8 -*-
"""控制面板的后台小工具: 判断是否在运行、启动/停止、读写 config.json、列麦克风。

界面只负责显示和收集设置; 真正识别逻辑都在 voice_trigger 包里, 这里不重复实现。
"""
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

from ..paths import AUTO_FLAG, PROJECT_ROOT

HERE = PROJECT_ROOT
CONFIG = HERE / "config.json"
LOG = HERE / "voice-trigger.log"
STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / r"Microsoft\\Windows\\Start Menu\\Programs\\Startup\\voice-trigger.lnk"
MUTEX_NAME = "voice-trigger-fafa"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


# ---------- 后台进程 ----------
def is_running():
    """main.py 运行时会占用一个命名互斥量, 能打开说明在运行。"""
    try:
        h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, MUTEX_NAME)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def run_ps1(name):
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(HERE / name)], creationflags=NO_WINDOW)


def start_bg():
    subprocess.Popen(["wscript", str(HERE / "start_hidden.vbs")], cwd=str(HERE))


def stop_bg():
    run_ps1("stop.ps1")


# ---------- 配置 ----------
def read_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_config(changes):
    """先读最新的 config.json, 只改界面上的这几项, 其他字段原样保留。"""
    cfg = read_config()
    cfg.update(changes)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def list_mics():
    try:
        import sounddevice as sd
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                n = d["name"]
                if any(h in n.lower() for h in ("立体声混音", "stereo mix", "loopback")):
                    continue
                out.append((i, n))
        return out
    except Exception:
        return []


def split_words(s):
    return [w.strip() for w in s.replace("，", ",").split(",") if w.strip()]


def already_open():
    """面板只开一个: 已经开着就把那个窗口拿到最前面, 返回 True"""
    try:
        k = ctypes.windll.kernel32
        k.SetLastError(0)
        already_open.mutex = k.CreateMutexW(None, False, "Local\\\\fafa-voice-trigger-gui")
        if k.GetLastError() != 183:   # 183 = 已经存在
            return False
        u = ctypes.windll.user32
        u.IsIconic.argtypes = [ctypes.c_void_p]
        u.IsIconic.restype = ctypes.c_int
        h = u.FindWindowW(None, "发发 · 语音唤醒")
        if h:
            if u.IsIconic(h):
                u.ShowWindow(h, 9)        # 只有最小化才恢复; 最大化时 SW_RESTORE 会取消最大化
            u.keybd_event(0x12, 0, 0, 0)  # 按一下 Alt, Windows 才允许把别的窗口拿到前台
            u.SetForegroundWindow(h)
            u.keybd_event(0x12, 0, 2, 0)
        return True
    except Exception:
        return False
