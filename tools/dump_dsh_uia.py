# -*- coding: utf-8 -*-
"""
诊断脚本: 找到标记的目标窗口 (或命令行给的标题子串), poke 它的 Chromium 子窗口打开
无障碍树, 然后用 UIA 整棵树地遍历, 打印每个控件的关键信息 (重点看聊天输入框/Composer
到底是什么控件、支持哪些 UIA 模式)。

跑法 (项目自带的 venv, 或直接双击同目录的 dump_dsh_uia.bat):
    C:\\Users\\Jimmy\\Documents\\GitHub\\voice-trigger\\.venv\\Scripts\\python.exe tools\\dump_dsh_uia.py
    # 或指定标题子串 / exe:
    ...\\python.exe tools\\dump_dsh_uia.py --title "DeepSeek Harness" --exe "DeepSeek Harness.exe"

只读: 不点不敲键盘, 不会碰真实对话内容。
输出同时写到 tools/uia_dump.txt (UTF-8), 方便直接发给别人看。
"""
import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_trigger.target import list_windows  # noqa: E402

OUT = Path(__file__).resolve().parent / "uia_dump.txt"


def poke(top_hwnd):
    """给窗口内所有 Chrome_RenderWidgetHostHWND 子窗口发 WM_GETOBJECT, 强制打开无障碍树。
    返回找到的子窗口数量。"""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        b = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, b, 64)
        if b.value.startswith("Chrome_RenderWidgetHostHWND"):
            found.append(h)
        return True

    u.EnumChildWindows(top_hwnd, proc(cb), 0)
    res = wintypes.DWORD()
    for h in found:
        for obj in (-4, -25):  # OBJID_CLIENT, UiaRootObjectId
            u.SendMessageTimeoutW(h, 0x003D, 0, obj, 2, 500, ctypes.byref(res))
    return len(found)


def find_hwnd(title_substr, exe):
    title_substr = (title_substr or "").lower()
    exe = (exe or "").lower()
    for h, title, e in list_windows():
        if exe and e.lower() != exe:
            continue
        if title_substr and title_substr not in title.lower():
            continue
        return h, title, e
    return None, None, None


def patterns_of(c):
    """返回控件支持的 UIA 模式名列表 (只关心 Value / Text / Scroll)。"""
    pats = []
    for name, getter in (("Value", "GetValuePattern"),
                         ("Text", "GetTextPattern"),
                         ("Scroll", "GetScrollPattern")):
        try:
            if getattr(c, getter)() is not None:
                pats.append(name)
        except Exception:
            pass
    return pats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="DeepSeek Harness", help="标题包含这段文字")
    ap.add_argument("--exe", default="DeepSeek Harness.exe", help="程序名")
    ap.add_argument("--max-depth", type=int, default=200)
    args = ap.parse_args()

    lines = []

    def emit(s=""):
        print(s)
        lines.append(s)

    try:
        import uiautomation as auto
    except ImportError:
        emit("没装 uiautomation (pip install uiautomation, 或用 .venv 的 python 跑)")
        return 1

    hwnd, title, exe = find_hwnd(args.title, args.exe)
    if not hwnd:
        emit(f"没找到窗口 (title含『{args.title}』, exe={args.exe})。当前可见窗口:")
        for h, t, e in list_windows():
            emit(f"  hwnd={h} exe={e!r} title={t!r}")
        return 1
    emit(f"找到窗口: hwnd={hwnd} title={title!r} exe={exe!r}")

    n_poked = poke(hwnd)
    emit(f"poke 了 {n_poked} 个 Chrome_RenderWidgetHostHWND 子窗口")

    with auto.UIAutomationInitializerInThread():
        top = auto.ControlFromHandle(hwnd)
        if top is None:
            emit("auto.ControlFromHandle(hwnd) 返回 None —— 本进程拿不到这个窗口的 UIA 控件。")
            emit("可能是会话/权限隔离 (本进程和 DSH 不在同一个桌面/完整性级别)。")
            return 1

        rows = []
        for c, depth in auto.WalkControl(top, maxDepth=args.max_depth):
            try:
                ct = c.ControlTypeName
            except Exception:
                ct = "?"
            try:
                name = c.Name or ""
            except Exception:
                name = ""
            try:
                aid = c.AutomationId or ""
            except Exception:
                aid = ""
            try:
                cls = c.ClassName or ""
            except Exception:
                cls = ""
            try:
                rect = c.BoundingRectangle
                rect_t = (rect.left, rect.top, rect.right, rect.bottom)
            except Exception:
                rect_t = None
            try:
                focusable = c.IsKeyboardFocusable
            except Exception:
                focusable = None
            try:
                focused = c.HasKeyboardFocus
            except Exception:
                focused = None
            pats = patterns_of(c)
            rows.append((depth, ct, name, aid, cls, rect_t, focusable, focused, pats))

        emit(f"遍历了 {len(rows)} 个控件 (maxDepth={args.max_depth})\n")

        emit("--- 全量清单 (缩进按深度) ---")
        for depth, ct, name, aid, cls, rect_t, focusable, focused, pats in rows:
            indent = "  " * depth
            name_s = (name[:60] + "...") if len(name) > 60 else name
            aid_s = (aid[:40] + "...") if len(aid) > 40 else aid
            pat_s = ",".join(pats)
            emit(f"{indent}[{ct}] name={name_s!r} aid={aid_s!r} cls={cls!r} "
                 f"rect={rect_t} focusable={focusable} focused={focused} pat={pat_s}")

        emit("\n--- 疑似聊天输入框 (Composer) 候选 ---")
        keys = ("edit", "compose", "input", "message", "prompt", "textbox",
                "textarea", "发送", "输入", "提问")
        hit_any = False
        for depth, ct, name, aid, cls, rect_t, focusable, focused, pats in rows:
            blob = f"{ct} {name} {aid} {cls}".lower()
            is_editish = ct in ("EditControl", "DocumentControl") or any(k in blob for k in keys)
            if is_editish:
                hit_any = True
                emit(f"  [{ct}] name={name!r} aid={aid!r} cls={cls!r} rect={rect_t} "
                     f"focusable={focusable} focused={focused} pat={','.join(pats)} depth={depth}")
        if not hit_any:
            emit("  (没有匹配到任何疑似输入框的控件 —— 页面可能没打开无障碍树, 需要换个进程跑)")

    try:
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        emit(f"\n(已写入 {OUT})")
    except Exception as e:
        emit(f"(写文件失败: {e})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
