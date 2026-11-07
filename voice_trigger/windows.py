# -*- coding: utf-8 -*-
"""Windows 窗口: 读标题/程序名、前台窗口、按窗口决定发送方式。"""
import sys

LAST_FG = {"title": "", "exe": ""}   # 最近一个不是发发面板的前台窗口 (主循环每 0.3 秒记一次)

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

def match_words(words, s):
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
    if match_words(dic, exe):           # 先看程序名: VS Code 里打开 claude.md 也算听写
        mode = "dictation"
    elif match_words(ai, f"{title} {exe}"):
        mode = "ai"
    elif match_words(dic, title):
        mode = "dictation"
    else:
        mode = "other"
    return mode, title, exe


ROUTE_NAME = {"ai": "AI 模式 (点聊天框发送)", "dictation": "听写模式 (粘贴到当前位置)",
              "skip": "不在列表里, 不发",
              "other": "其他窗口"}
