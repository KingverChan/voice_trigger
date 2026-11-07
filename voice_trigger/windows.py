# -*- coding: utf-8 -*-
"""Windows 窗口: 读标题/程序名、前台窗口。

窗口路由已经去掉: 所有话都发到 --mark-target 标记的那个窗口。
找/激活标记窗口的逻辑在 voice_trigger/target.py。
"""
import sys


def _u32():
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    # 64 位系统上句柄是 64 位, 不声明参数类型会被截断
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.GetForegroundWindow.argtypes = []
    u.GetForegroundWindow.restype = wintypes.HWND
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                            wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return u, k


def window_info(hwnd):
    """窗口句柄 -> (标题, 程序名 如 WINWORD.EXE)"""
    import ctypes
    from ctypes import wintypes
    u, k = _u32()
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


def foreground_window():
    """现在最前面(你正在用)的窗口: (标题, 程序名)"""
    if sys.platform != "win32":
        return "", ""
    u, _ = _u32()
    h = u.GetForegroundWindow()
    return window_info(h) if h else ("", "")


def match_words(words, s):
    s = s.lower()
    return any(w.strip().lower() in s for w in words if w.strip())
