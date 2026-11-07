# -*- coding: utf-8 -*-
"""标记的目标对话窗口: 记住它 / 重新找到它 / 把它切到前台 / 标定兜底点击点。

config.json 里的结构:
  "target_window": {
      "exe": "claude.exe",            # 程序名 (只要这一项也能找)
      "title_substr": "Claude",       # 标题里的一段 (标题变了会退回"程序只有一个窗口"的匹配)
      "hwnd": 123456,                 # 标记时的句柄 (只用来优先选窗口, 窗口重开会换成新的)
      "client_rect": [l, t, r, b],    # 标记时的客户区 (屏幕坐标, 只作参考)
      "click_rel_x": 420,             # 兜底点击点: 相对客户区左上角的像素, 没标定就是 null
      "click_rel_y": 610,
      "marked_at": 1762500000
  }
"""
import os
import sys
import time

from .config import CONFIG_PATH, save_config
from .windows import is_panel, window_info

SW_RESTORE = 9
GA_ROOT = 2


def _win32():
    """返回 (ctypes, wintypes, user32, kernel32); 非 Windows 报错。"""
    if sys.platform != "win32":
        raise RuntimeError("标记窗口需要 Windows")
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    # 64 位系统上句柄是 64 位, 不声明参数类型会被截断, 所以这里都写清楚
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.GetForegroundWindow.argtypes = []
    u.GetForegroundWindow.restype = wintypes.HWND
    u.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    u.EnumWindows.restype = wintypes.BOOL
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindowVisible.restype = wintypes.BOOL
    u.IsWindow.argtypes = [wintypes.HWND]
    u.IsWindow.restype = wintypes.BOOL
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.ShowWindow.restype = wintypes.BOOL
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.SetForegroundWindow.restype = wintypes.BOOL
    u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetAncestor.restype = wintypes.HWND
    u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetClientRect.restype = wintypes.BOOL
    u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    u.ClientToScreen.restype = wintypes.BOOL
    u.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
    u.keybd_event.restype = None
    return ctypes, wintypes, u, k


def _hwnd(v):
    return int(v or 0)


def _pid_of(hwnd):
    ctypes, wintypes, u, _ = _win32()
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def client_rect(hwnd):
    """窗口的客户区 (屏幕上输入框所在的区域): [左, 上, 右, 下]; 读不到返回 None。"""
    try:
        ctypes, wintypes, u, _ = _win32()
        hwnd = _hwnd(hwnd)
        r = wintypes.RECT()
        if not u.GetClientRect(hwnd, ctypes.byref(r)):
            return None
        pt = wintypes.POINT(0, 0)
        if not u.ClientToScreen(hwnd, ctypes.byref(pt)):
            return None
        return [int(pt.x), int(pt.y), int(pt.x + r.right), int(pt.y + r.bottom)]
    except Exception:
        return None


def list_windows():
    """枚举有标题的顶层窗口: [(hwnd, 标题, 程序名)]。"""
    try:
        ctypes, wintypes, u, _ = _win32()
    except RuntimeError:
        return []
    out = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        try:
            if not u.IsWindowVisible(h):
                return True
            title, exe = window_info(h)
            if title:
                out.append((_hwnd(h), title, exe))
        except Exception:
            pass
        return True

    try:
        u.EnumWindows(proc(cb), 0)
    except Exception:
        return []
    return out


def find_target_window(target):
    """
    按 target_window 里的 exe + 标题子串重新找窗口 (窗口关了再开、hwnd 变了也能找到)。
    找不到返回 None。标题变了但程序只有一个窗口时, 仍然用它。
    """
    if not target:
        return None
    exe = (target.get("exe") or "").strip().lower()
    sub = (target.get("title_substr") or "").strip().lower()
    old = _hwnd(target.get("hwnd"))
    wins = list_windows()
    cands = [w for w in wins if not exe or w[2].strip().lower() == exe]
    if not cands:
        return None
    if sub:
        hit = [w for w in cands if sub in w[1].lower()]
        if hit:
            cands = hit
        elif exe and len(cands) == 1:
            cands = cands  # 标题变了 (浏览器换标签页等), 程序只有一个窗口就用它
        else:
            return None
    for h, _, _ in cands:
        if h == old:
            return h
    return cands[0][0]


def is_foreground(hwnd):
    """现在最前面的是不是这个窗口 (或是它的子窗口/弹窗)。"""
    try:
        _, _, u, _ = _win32()
        fg = _hwnd(u.GetForegroundWindow())
        if not fg:
            return False
        target = _hwnd(hwnd)
        if fg == target:
            return True
        root = _hwnd(u.GetAncestor(fg, GA_ROOT))
        return root == target
    except Exception:
        return False


def activate_window(hwnd, tries=10, interval=0.1):
    """
    把窗口恢复并切到前台, 然后轮询 GetForegroundWindow 确认 (10 次 x 100ms)。
    返回 True = 已确认在前台; False = 没切过去 (调用方走兜底点击)。
    """
    try:
        _, _, u, _ = _win32()
        hwnd = _hwnd(hwnd)
        if not hwnd or not u.IsWindow(hwnd):
            return False
        u.ShowWindow(hwnd, SW_RESTORE)
        # 先松/按一下 Alt, Windows 才允许把别的窗口抢到前台
        u.keybd_event(0x12, 0, 0, None)
        ok = bool(u.SetForegroundWindow(hwnd))
        u.keybd_event(0x12, 0, 2, None)
    except Exception:
        return False
    if ok and is_foreground(hwnd):
        return True
    for _ in range(max(1, int(tries))):  # 10 次 x 100ms
        time.sleep(interval)
        if is_foreground(hwnd):
            return True
    return False


def is_own_window(hwnd):
    """前台窗口是不是发发自己的 (面板/控制台)。"""
    try:
        title, exe = window_info(hwnd)
        return is_panel(title, exe) or _pid_of(hwnd) == os.getpid()
    except Exception:
        return False


def describe_target(target):
    if not target:
        return "(还没标记目标窗口)"
    return f"「{target.get('title_substr') or '(无标题)'}」({target.get('exe') or '?'})"


def mark_target(cfg):
    """
    把当前前台窗口记成目标窗口并保存。返回 True/False。
    前台是发发自己 (面板、本进程窗口) 时不保存, 提示用户先点目标窗口。
    """
    try:
        _, _, u, _ = _win32()
    except RuntimeError as e:
        print(f"  标记失败: {e}")
        return False
    hwnd = _hwnd(u.GetForegroundWindow())
    if not hwnd:
        print("  标记失败: 读不到前台窗口")
        return False
    title, exe = window_info(hwnd)
    if is_own_window(hwnd):
        print("  现在前台是发发自己 (面板/本程序), 不能标记它。")
        print("  请先把鼠标点到要发送的那个对话窗口, 再运行: python main.py --mark-target")
        return False
    rect = client_rect(hwnd)
    if not rect:
        print(f"  标记失败: 读不到「{title}」的客户区")
        return False
    old = cfg.get("target_window") or {}
    target = {
        "exe": exe,
        "title_substr": title,
        "hwnd": hwnd,
        "client_rect": rect,
        "click_rel_x": None,
        "click_rel_y": None,
        "marked_at": int(time.time()),
    }
    # 同一个窗口重新标记时保留已经标定好的兜底点击点
    if old.get("exe", "").lower() == exe.lower() and old.get("title_substr") == title:
        target["click_rel_x"] = old.get("click_rel_x")
        target["click_rel_y"] = old.get("click_rel_y")
    cfg["target_window"] = target
    save_config(cfg)
    print(f"已标记目标窗口: 「{title}」")
    print(f"  程序: {exe}   hwnd: {hwnd}")
    print(f"  客户区: {rect}   已保存到 {CONFIG_PATH.name}")
    print("  如果以后激活不了它, 再运行: python main.py --calibrate (标定兜底点击点)")
    return True


def calibrate_click(cfg, x, y):
    """
    把鼠标位置换算成目标窗口客户区的相对坐标, 存进 target_window.click_rel_x/y。
    只在激活失败时当兜底: 点一下输入框再粘贴。
    返回 (是否成功, 提示文字)。
    """
    target = cfg.get("target_window")
    if not target:
        return False, "还没标记目标窗口, 先运行: python main.py --mark-target"
    hwnd = find_target_window(target)
    if not hwnd:
        return False, "标记的窗口不见了, 请重新运行: python main.py --mark-target"
    rect = client_rect(hwnd)
    if not rect:
        return False, "读不到标记窗口的客户区, 请重新运行: python main.py --mark-target"
    left, top, right, bottom = rect
    x, y = int(x), int(y)
    if not (left <= x < right and top <= y < bottom):
        return False, (f"鼠标位置 ({x}, {y}) 不在标记窗口的客户区 {rect} 里。"
                       "顺序: 先 --mark-target 标记窗口, 再 --calibrate 点输入框")
    rel_x, rel_y = x - left, y - top
    target["hwnd"] = hwnd
    target["client_rect"] = rect
    target["click_rel_x"] = rel_x
    target["click_rel_y"] = rel_y
    cfg["target_window"] = target
    save_config(cfg)
    return True, (f"已保存兜底点击点: 相对客户区 ({rel_x}, {rel_y}) "
                  f"-> {CONFIG_PATH.name}")


def window_at_point(x, y):
    """屏幕坐标 (x, y) 上最上层的顶层窗口句柄; 读不到返回 0。"""
    try:
        ctypes, wintypes, u, _ = _win32()
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.WindowFromPoint.restype = wintypes.HWND
        h = _hwnd(u.WindowFromPoint(wintypes.POINT(int(x), int(y))))
        if not h:
            return 0
        root = _hwnd(u.GetAncestor(h, GA_ROOT))
        return root or h
    except Exception:
        return 0


def click_point(target, hwnd):
    """兜底点击点在当前屏幕上的坐标; 没标定或窗口没了返回 None。"""
    if not target or target.get("click_rel_x") is None or target.get("click_rel_y") is None:
        return None
    rect = client_rect(hwnd) or target.get("client_rect")
    if not rect:
        return None
    try:
        return (int(rect[0] + target["click_rel_x"]), int(rect[1] + target["click_rel_y"]))
    except (TypeError, ValueError, IndexError):
        return None
