# -*- coding: utf-8 -*-
"""Windows 全局热键 (RegisterHotKey) 与提示音。"""
import sys

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
