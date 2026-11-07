# -*- coding: utf-8 -*-
"""把文字/图片粘贴到目标窗口 (聊天框, 或当前听写位置)。"""
import time

from ..journal import mark_pasting
from ..windows import foreground_window, match_words
from .clipboard import read_clipboard, set_clipboard_image

def paste_here(cfg, text, dry_run=False):
    """听写模式: 粘贴到你正在打字的地方。不点击、不移动鼠标、不回车, 剪贴板用完还原。"""
    if dry_run:
        print(f"  [dry-run] 会粘贴到当前位置: {text}")
        return True
    import pyautogui
    import pyperclip
    try:
        old = pyperclip.paste()
    except Exception:
        old = None
    mark_pasting(cfg)
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    if old is not None:
        pyperclip.copy(old)
    mark_pasting(cfg)
    print(f"  已粘贴: {text}")
    return True

def read_selection(cfg):
    """
    读你在前台窗口里选中(框选)的文字: 模拟按一下 Ctrl+C, 读剪贴板, 再把原来的剪贴板放回去。
    没选中东西时剪贴板不会变, 返回 ""。只在发送的那一刻读一次, 不会在后台一直读。
    """
    import ctypes
    import pyautogui
    import pyperclip
    title, exe = foreground_window()
    if match_words(cfg.get("selection_skip_apps", []), exe):
        return "", f"{exe} 里按 Ctrl+C 会中断程序, 不读"
    seq = ctypes.windll.user32.GetClipboardSequenceNumber
    old_text, old_img, _ = read_clipboard()
    before = seq()
    mark_pasting(cfg)
    # 先松开可能还按着的修饰键, 再按 Ctrl+C
    for k in ("alt", "shift", "win"):
        pyautogui.keyUp(k)
    pyautogui.hotkey("ctrl", "c")
    for _ in range(10):  # 最多等 0.5 秒让程序把内容放进剪贴板
        time.sleep(0.05)
        if seq() != before:
            break
    if seq() == before:
        return "", "没有选中内容"
    time.sleep(0.05)
    try:
        sel = pyperclip.paste()
        sel = sel if isinstance(sel, str) else ""
    except Exception:
        sel = ""
    if match_words(cfg.get("selection_line_copy_apps", []), exe) and sel.endswith("\n") \
            and sel.rstrip("\r\n").count("\n") == 0:
        sel, exe = "", f"{exe} 没选中 (复制到的是光标所在的整行)"
    # 还原原来的剪贴板
    try:
        if old_img is not None:
            set_clipboard_image(old_img)
        else:
            pyperclip.copy(old_text or "")
    except Exception:
        pass
    mark_pasting(cfg)
    return sel, f"{exe}"

def send_text(cfg, text, image=None, dry_run=False):
    """粘贴文字 (有图片的话再粘贴图片), 然后回车。"""
    if dry_run:
        print(f"  [dry-run] 会发送:\n{text}")
        if image is not None:
            print(f"  [dry-run] 还会粘贴一张图片 {image.size[0]}x{image.size[1]}")
        return
    if not cfg.get("input_box"):
        print("  还没记录输入框坐标, 先运行: python main.py --calibrate")
        return
    import pyautogui
    import pyperclip
    old_text = None
    if image is None:  # 剪贴板是图片时 old 就是这张图, 最后会放回去
        try:
            old_text = pyperclip.paste()
        except Exception:
            old_text = None
    mark_pasting(cfg)
    old_mouse = pyautogui.position()  # 记住鼠标原来的位置
    x, y = cfg["input_box"]
    pyautogui.click(x, y)
    time.sleep(0.2)
    # 1. 粘贴文字
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    # 2. 粘贴图片, 等它出现在输入框里
    if image is not None:
        try:
            set_clipboard_image(image)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(float(cfg.get("image_wait_s", 1.5)))
        except Exception as e:
            print(f"  图片粘贴失败: {e}")
    # 3. 回车发送
    if cfg.get("press_enter", True):
        pyautogui.press("enter")
    time.sleep(0.2)
    pyautogui.moveTo(old_mouse[0], old_mouse[1])  # 鼠标移回原位
    # 4. 还原剪贴板 (图片的情况剪贴板里已经是原来那张图)
    if image is None and old_text is not None:
        pyperclip.copy(old_text)
    mark_pasting(cfg)
    print(f"  已发送: {text}" + ("  + 1 张图片" if image is not None else ""))
