# -*- coding: utf-8 -*-
"""把文字/图片发到标记的那个对话窗口。

路径 A (首选): 重新找到窗口 -> 激活并确认 -> 剪贴板设文字 -> Ctrl+V -> Enter
路径 B (兜底): 激活失败时, 用 --calibrate 标定的相对客户区坐标点一下输入框 -> 粘贴 -> Enter
图片永远单独作为第二条消息发: 文字一条, 图片一条, 不硬混在一起。
"""
import time

from ..journal import mark_pasting
from ..target import (activate_window, click_point, describe_target,
                      find_target_window, foreground_hwnd, is_foreground,
                      is_window, is_window_visible, window_at_point)
from ..windows import match_words, window_info
from .clipboard import read_clipboard, set_clipboard_image


def read_selection(cfg):
    """
    读你在前台窗口里选中(框选)的文字: 模拟按一下 Ctrl+C, 读剪贴板, 再把原来的剪贴板放回去。
    没选中东西时剪贴板不会变, 返回 ""。只在发送的那一刻读一次, 不会在后台一直读。
    前台窗口不可见 / 是工具自身 (pythonw.exe) / 是 Ctrl+C 会中断程序的应用时, 不读。
    """
    hwnd = foreground_hwnd()
    if not hwnd or not is_window_visible(hwnd):
        return "", "前台窗口不可见, 不读"
    _, exe = window_info(hwnd)
    if exe.strip().lower() in ("python.exe", "pythonw.exe"):
        return "", "前台是工具自身进程, 不读"
    if match_words(cfg.get("selection_skip_apps", []), exe):
        return "", f"{exe} 里按 Ctrl+C 会中断程序, 不读"
    import ctypes
    import pyautogui
    import pyperclip
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
    """
    发到 config.target_window 标记的窗口。返回 True = 粘贴/回车都做了；
    False = 没发出去 (具体原因已经打印)。注意: 这里只能确认窗口激活了,
    确认不了输入框里真的收到了, 所以打印的是「已粘贴并回车 (未校验)」。
    """
    target = cfg.get("target_window")
    auto = cfg.get("auto_target_window")
    if dry_run:
        if auto:
            print(f"  [dry-run] 目标窗口: 自动跟随的终端 ({auto.get('exe')}, hwnd={auto.get('hwnd')})")
        else:
            print(f"  [dry-run] 目标窗口: {describe_target(target)}")
        if text:
            print(f"  [dry-run] 会粘贴文字并回车: {text}")
        if image is not None:
            print(f"  [dry-run] 图片会单独作为第二条消息发: {image.size[0]}x{image.size[1]}")
        if not text and image is None:
            print("  [dry-run] 没有要发送的内容")
        if not auto and not target:
            print("  [dry-run] 注意: 还没有发送目标, 正式发送会失败 (点一下终端, 或先 --mark-target)")
        return True

    if not text and image is None:
        print("  没有要发送的内容")
        return False

    # 1. 优先用最近点击的终端; 自动目标没了 (或为空) 就回退 --mark-target 标记的窗口
    hwnd = None
    use_config_target = False
    if auto:
        h = int(auto.get("hwnd") or 0)
        if h and is_window(h):
            hwnd = h
        else:
            print("[目标] 自动跟随的终端不见了, 点击一个新终端即可")
    if hwnd is None:
        if not target:
            print("  还没标记目标窗口, 先运行: python main.py --mark-target")
            return False
        # 按 exe + 标题重新找窗口 (窗口重开、hwnd 变了也能找到)
        hwnd = find_target_window(target)
        if not hwnd:
            print("  标记的窗口不见了, 请重新运行: python main.py --mark-target")
            return False
        use_config_target = True
    title, exe = window_info(hwnd)

    # 2. 激活 + 确认; 配置目标激活失败还能用标定点点一下, 自动目标没有标定
    point = None
    if activate_window(hwnd):
        print(f"  已激活「{title}」({exe})")
        if not use_config_target:
            print("  自动跟随的终端, 直接粘贴 (靠窗口焦点)")
        else:
            # 激活只保证窗口在前台, 不保证输入框有焦点; 标定过就顺手点一下输入框
            has_point = (target.get("click_rel_x") is not None
                         and target.get("click_rel_y") is not None)
            if not has_point:
                print("  没有标定点击点, 直接粘贴 (靠窗口焦点)")
            elif cfg.get("click_before_paste", True):
                point = click_point(target, hwnd)
                if point is None:
                    print("  读不到标定点击点对应的客户区, 直接粘贴 (靠窗口焦点)")
            else:
                print("  click_before_paste 已关, 不点输入框, 直接粘贴")
    else:
        point = click_point(target, hwnd) if use_config_target else None
        if not point:
            print(f"  激活「{title}」({exe}) 失败 (Windows 不让切到前台), 也没有标定兜底点击点")
            if use_config_target:
                print("  先运行: python main.py --calibrate (把鼠标移到它的输入框上)")
            return False
        print(f"  激活「{title}」失败, 用标定的位置 {point} 点一下输入框")

    try:
        import pyautogui
        import pyperclip
    except Exception as e:
        print(f"  缺少键鼠/剪贴板库, 发不出去: {e}")
        return False

    old_mouse = pyautogui.position()  # 记住鼠标原来的位置
    old_text = None
    if image is None:  # 剪贴板是图片时 old 就是这张图, 最后不用还原文字
        try:
            old_text = pyperclip.paste()
        except Exception:
            old_text = None
    mark_pasting(cfg)
    ok = False
    try:
        if point is not None:
            # 兜底点击前先确认那个点上还是标记窗口, 别点到盖在上面的面板/别的程序
            cover = window_at_point(point[0], point[1])
            if cover != int(hwnd):
                print(f"  兜底点击点被别的窗口盖住了 (那个位置现在是句柄 {cover or '空'}), 取消发送")
                print("  把挡住的窗口挪开, 或重新 --calibrate 标定点击点")
                return False
            pyautogui.click(point[0], point[1])
            print(f"  已点击标定位置 {point}")
            time.sleep(0.2)
            if not is_foreground(hwnd):
                print("  点了之后前台还不是标记窗口, 取消发送 (没粘贴)")
                return False
        enter = bool(cfg.get("press_enter", True))
        if text:
            # 3. 文字: 窗口已在前台 (需要时点过输入框), 直接走剪贴板粘贴
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.3)
            if enter:
                pyautogui.press("enter")
            time.sleep(0.2)
            print(f"  已粘贴文字{'并回车' if enter else ' (没回车)'} (未校验): {text}")
        if image is not None:
            # 4. 图片: 单独一条消息
            set_clipboard_image(image)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(float(cfg.get("image_wait_s", 1.5)))
            if enter:
                pyautogui.press("enter")
            time.sleep(0.2)
            print(f"  已粘贴图片{'并回车' if enter else ' (没回车)'} (未校验): "
                  f"{image.size[0]}x{image.size[1]}")
        ok = True
    except Exception as e:
        print(f"  发送失败: {type(e).__name__}: {e}")
    finally:
        try:
            pyautogui.moveTo(old_mouse[0], old_mouse[1])  # 鼠标移回原位
        except Exception:
            pass
        if image is None and old_text is not None:
            try:
                pyperclip.copy(old_text)  # 还原剪贴板
            except Exception:
                pass
        mark_pasting(cfg)
    return ok
