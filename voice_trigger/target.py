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
    u.IsIconic.argtypes = [wintypes.HWND]
    u.IsIconic.restype = wintypes.BOOL
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


def _is_window(hwnd):
    """这个句柄现在还是不是一个存在的窗口。"""
    try:
        _, _, u, _ = _win32()
        return bool(u.IsWindow(_hwnd(hwnd)))
    except Exception:
        return False


def find_target_window(target):
    """
    按 target_window 找窗口, 三级回退:
    1. exe + 标题子串匹配 (标题会随终端里跑的对话变化, 所以先按标记时的标题找)
    2. 标题没命中时, 标记时的 hwnd 还活着 (IsWindow) 就用它
    3. 还不行就取同 exe 最后一个可见顶层窗口 (EnumWindows 顺序, 通常是最新开的)
    找不到返回 None。每级命中都打日志说明走的哪一级。
    """
    if not target:
        return None
    exe = (target.get("exe") or "").strip().lower()
    sub = (target.get("title_substr") or "").strip().lower()
    old = _hwnd(target.get("hwnd"))
    same_exe = [w for w in list_windows()
                if not exe or w[2].strip().lower() == exe]

    # 1. exe + 标题子串
    hit = [w for w in same_exe if not sub or sub in w[1].lower()]
    if hit:
        print("[目标] 标题匹配")
        for h, _, _ in hit:
            if h == old:
                return h
        return hit[0][0]

    # 2. 标题失配: 标记时的句柄还活着就用它 (窗口没关, 只是标题变了)
    if old and _is_window(old):
        print("[目标] 标题失配, 用标记句柄")
        return old

    # 3. 还没有: 取同 exe 最后一个可见窗口 (通常是最新打开的那个)
    if exe and same_exe:
        print("[目标] 标题失配, 回退同 exe 窗口")
        return same_exe[-1][0]

    return None


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
    把窗口切到前台 (最小化了才恢复), 然后轮询 GetForegroundWindow 确认 (10 次 x 100ms)。
    返回 True = 已确认在前台; False = 没切过去 (调用方走兜底点击)。
    """
    try:
        _, _, u, _ = _win32()
        hwnd = _hwnd(hwnd)
        if not hwnd or not u.IsWindow(hwnd):
            return False
        if u.IsIconic(hwnd):
            # 只有最小化才恢复。SW_RESTORE 对最大化窗口等于取消最大化 (窗口会被缩回普通尺寸),
            # 所以最大化/普通状态一律不动, 交给 SetForegroundWindow 切前台。
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


def _poke_chromium(hwnd):
    """
    给窗口内所有 Chrome_RenderWidgetHostHWND 子窗口发 WM_GETOBJECT (OBJID_CLIENT / UiaRootObjectId),
    强制 Chromium 打开无障碍树 (跟 reply.py 的 poke() 是同一个套路)。
    Electron/Chromium 默认不开放页面的 UIA 树, 第一次被问才打开, 所以找 Composer 前要先 poke 一下。
    返回 poke 到的子窗口数 (0 = 没找到, 可能不是 Chromium 窗口, 或者还没加载)。
    """
    try:
        ctypes, wintypes, u, _ = _win32()
        hwnd = _hwnd(hwnd)
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int
        u.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]
        u.EnumChildWindows.restype = wintypes.BOOL
        u.SendMessageTimeoutW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM,
                                           wintypes.LPARAM, ctypes.c_uint, ctypes.c_uint,
                                           ctypes.POINTER(wintypes.DWORD)]
        u.SendMessageTimeoutW.restype = ctypes.c_long
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


_COMPOSER_TYPES = ("EditControl", "DocumentControl")


def _looks_like_composer(c, win_rect):
    """粗判一个 UIA 控件像不像聊天输入框: 可编辑类型 + 有实际大小 + 不在屏幕外 + 大致在窗口下半部分
    (聊天输入框习惯贴在窗口底部; 上半部分大多是历史消息, 同样可能是 EditControl/DocumentControl)。"""
    try:
        if c.ControlTypeName not in _COMPOSER_TYPES:
            return False
    except Exception:
        return False
    try:
        r = c.BoundingRectangle
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            return False
    except Exception:
        return False
    if win_rect:
        try:
            _, top, _, bottom = win_rect
            height = max(1, bottom - top)
            if r.top < top + height * 0.35:
                return False
        except Exception:
            pass
    try:
        if c.IsOffscreen:
            return False
    except Exception:
        pass
    return True


def find_composer(hwnd, max_depth=200, time_limit=2.0):
    """
    用 UIA 在目标窗口里找聊天输入框 (Composer): 先 poke 打开 Chromium 的无障碍树,
    再整棵树找「可编辑类型 + 在窗口下半部分」的控件, 多个候选取面积最大的一个
    (真正的输入框通常比旁边的小按钮/提示文字大)。

    找不到 / 没装 uiautomation / 窗口已经不存在 / 不是 Chromium 窗口, 都返回 None ——
    调用方 (focus_composer / try_set_composer_value) 据此退回旧的点击+粘贴流程, 不会抛异常。
    """
    try:
        import uiautomation as auto
    except ImportError:
        return None
    hwnd = _hwnd(hwnd)
    if not hwnd:
        return None
    try:
        _, _, u, _ = _win32()
        if not u.IsWindow(hwnd):
            return None
    except Exception:
        return None
    try:
        _poke_chromium(hwnd)
        win_rect = client_rect(hwnd)
        t0 = time.time()
        with auto.UIAutomationInitializerInThread():
            top = auto.ControlFromHandle(hwnd)
            if top is None:
                return None
            best, best_area = None, 0
            for c, _depth in auto.WalkControl(top, maxDepth=max_depth):
                if time.time() - t0 > time_limit:
                    break
                if not _looks_like_composer(c, win_rect):
                    continue
                try:
                    r = c.BoundingRectangle
                    area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
                except Exception:
                    area = 0
                if area > best_area:
                    best, best_area = c, area
            return best
    except Exception:
        return None


def focus_composer(hwnd):
    """
    找到 Composer 并让它拿到键盘焦点 —— 解决"窗口在前台但输入框没焦点,
    Ctrl+V/Enter 全部落空"的问题 (Chromium 的粘贴/回车只作用于真正聚焦的可编辑元素)。
    先试 UIA 的 SetFocus() (不用动鼠标), 不行就点它的 BoundingRectangle 中心
    (比旧的手动标定坐标更准: 跟着窗口当前的实际位置/大小算, 窗口挪动或缩放也不会跟标定时的坐标脱节)。
    返回 True = 认为已经把焦点放进输入框; False = 两种办法都不行, 调用方退回原来的
    click_before_paste 兜底坐标 / 直接粘贴流程。
    """
    c = find_composer(hwnd)
    if c is None:
        return False
    try:
        c.SetFocus()
        return True
    except Exception:
        pass
    try:
        r = c.BoundingRectangle
        cx, cy = int((r.left + r.right) / 2), int((r.top + r.bottom) / 2)
        import pyautogui
        pyautogui.click(cx, cy)
        return True
    except Exception:
        return False


def try_set_composer_value(hwnd, text):
    """
    如果 Composer 支持 ValuePattern (比如纯 <textarea>, 不是常见的 contenteditable div),
    直接把文字写进去, 完全绕开剪贴板和"谁有键盘焦点"的问题。
    大多数聊天框是 contenteditable, 不支持 ValuePattern, 这时规规矩矩返回 False,
    调用方再走 focus_composer() + 剪贴板 Ctrl+V 的老路。
    """
    if not text:
        return False
    c = find_composer(hwnd)
    if c is None:
        return False
    try:
        vp = c.GetValuePattern()
        if vp is None:
            return False
        vp.SetValue(text)
        return True
    except Exception:
        return False


def read_composer_text(hwnd):
    """
    读 Composer 当前的文字 (ValuePattern.Value, 不行就 TextPattern 整段, 再不行用 Name)。
    只用来诊断/校验 (比如确认写进去了没有), 不在发送流程里调用。读不到返回 None。
    """
    c = find_composer(hwnd)
    if c is None:
        return None
    try:
        vp = c.GetValuePattern()
        if vp is not None:
            return vp.Value
    except Exception:
        pass
    try:
        tp = c.GetTextPattern()
        if tp is not None:
            return tp.DocumentRange.GetText(-1)
    except Exception:
        pass
    try:
        return c.Name
    except Exception:
        return None


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
