# -*- coding: utf-8 -*-
"""剪贴板: 读文字/图片、附带复制内容、监视剪贴板变化。"""
from ..paths import SCREENSHOT_DIR


def set_clipboard_image(img):
    """把 PIL 图片放进 Windows 剪贴板 (CF_DIB 格式), 聊天框就能 Ctrl+V 粘贴图片。"""
    import io
    import win32clipboard
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    data = buf.getvalue()[14:]  # 去掉 BMP 文件头, 剩下的就是 DIB
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    finally:
        win32clipboard.CloseClipboard()

def read_clipboard():
    """
    读剪贴板, 返回 (文字, 图片, 指纹)。
    文字: 没有就 ""; 图片: PIL Image 或 None; 指纹: 用来判断是不是"新复制"的。
    """
    try:
        from PIL import ImageGrab, Image
        g = ImageGrab.grabclipboard()
        if isinstance(g, Image.Image):
            import hashlib
            return "", g, "img:" + hashlib.md5(g.convert("RGB").tobytes()).hexdigest()
    except Exception:
        pass
    try:
        import pyperclip
        t = pyperclip.paste()
        t = t if isinstance(t, str) else ""
    except Exception:
        t = ""
    return t, None, "txt:" + t


def clipboard_text():
    """只读剪贴板里的文字。

    复制图片时, 浏览器通常把图片地址 (URL) 也放进文字格式; 资源管理器复制
    文件时是文件列表格式, 这里读不到, 返回 ""。
    """
    try:
        import pyperclip
        t = pyperclip.paste()
        return t if isinstance(t, str) else ""
    except Exception:
        return ""


def save_screenshot(img):
    """把剪贴板里的截图 (Win+Shift+S 只有像素, 没有地址文字) 存成 PNG。

    存到项目下的 screenshot_cache 目录, 文件名带时间戳; 返回绝对路径,
    失败返回 None。
    """
    try:
        import time
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / time.strftime("shot_%Y-%m-%d_%H-%M-%S.png")
        img.save(path, "PNG")
        return path
    except Exception:
        return None


def with_clipboard(cfg, text, baseline_key, in_window=True, ago=None, terminal=False):
    """
    如果剪贴板里有新复制的文字/图片, 一起带上。
    返回 (要发送的文字, 要粘贴的图片或 None)。

    目标是终端时贴不了图: 图片降级成"图片地址文字"附上 (浏览器复制图片时,
    剪贴板里通常同时有 URL); 没有地址文字 (比如 Win+Shift+S 截图) 就存成本地
    PNG, 把文件路径附上。
    """
    if not cfg.get("attach_clipboard", True):
        return text, None
    clip, img, key = read_clipboard()
    if cfg.get("attach_only_new", True) and key == baseline_key:
        return text, None  # 上次发送/启动之后没有新复制, 不附带
    if not in_window:
        if ago is not None and (img is not None or clip.strip()):
            print(f"  剪贴板内容是说话前 {ago:.0f} 秒复制的, 超过 "
                  f"{cfg.get('attach_window_s', 10)} 秒, 认为无关, 不附带")
        return text, None
    if img is not None:
        if terminal:
            addr = clipboard_text().strip()
            if addr:
                limit = int(cfg.get("attach_max_chars", 4000))
                if len(addr) > limit:
                    addr = addr[:limit] + "\n...(地址太长, 已截断)"
                print(f"[附带] 目标是终端, 图片降级为贴地址: {addr}")
                return f"{text}\n\n【图片地址】\n{addr}", None
            path = save_screenshot(img)
            if path is None:
                print("[附带] 截图保存失败, 跳过图片")
                return text, None
            print(f"[附带] 剪贴板是截图, 已保存到 {path}, 附带路径")
            return f"{text}\n\n【图片地址】\n{path}", None
        print(f"  附带复制的图片 {img.size[0]}x{img.size[1]}")
        return text, img
    if not clip.strip():
        return text, None
    limit = int(cfg.get("attach_max_chars", 4000))
    if len(clip) > limit:
        print(f"  复制的内容有 {len(clip)} 字, 只附带前 {limit} 字")
        clip = clip[:limit] + "\n...(内容太长, 已截断)"
    print(f"  附带复制内容 {len(clip)} 字")
    return f"{text}\n\n【附带的复制内容】\n{clip}", None

class ClipWatch:
    """记录剪贴板最后一次被(你)改动的时间。Windows 用剪贴板序号, 很便宜, 可以一直查。"""

    def __init__(self):
        try:
            import ctypes
            self._seq = ctypes.windll.user32.GetClipboardSequenceNumber
        except Exception:
            self._seq = None  # 非 Windows: 不做时间限制
        self.last = self._seq() if self._seq else 0
        self.changed_at = None  # 启动前就有的内容不算

    def poll(self, now):
        if self._seq:
            n = self._seq()
            if n != self.last:
                self.last = n
                self.changed_at = now

    def ignore(self):
        """我们自己改的剪贴板 (读选中文字), 不算你复制, 但保留你之前复制的时间。"""
        if self._seq:
            self.last = self._seq()

    def consume(self):
        """我们自己发送时改了剪贴板, 不算你复制; 已附带过的也清掉。"""
        if self._seq:
            self.last = self._seq()
        self.changed_at = None

    def recent(self, cmd_start, window):
        """返回 (是否在时间窗口内, 复制时间比开口早了几秒)。

        只认开口之前的复制: 说话开始后剪贴板再变 (微信输入法等语音听写
        会把刚说的话写进剪贴板) 一律不算新复制, ago < 0 表示这种。
        """
        if not self._seq:
            return True, None
        if self.changed_at is None:
            return False, None
        ago = cmd_start - self.changed_at
        if ago <= 0:
            return False, ago
        return ago <= window, ago
