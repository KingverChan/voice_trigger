# -*- coding: utf-8 -*-
"""读 AI 回复: UI Automation 读窗口 + 等回复稳定 + 读文件通道。"""
import re
import sys
import time
from pathlib import Path

from .config import DEFAULT_CONFIG
from .paths import PROJECT_ROOT
from .tts import TTS_DIR, clean_for_tts, tts_enabled, tts_lang
from .windows import window_info

def read_ai_reply(cfg, max_chars=800, time_limit=2.5):
    """
    用 Windows UI Automation 读 AI 聊天窗口里的文字, 取最后 max_chars 字 (大致就是 AI 最近的回复)。
    找窗口: 先看标记的对话窗口, 再按 reply_window_words 关键词找标题/程序名对得上的窗口。
    Claude / 浏览器是 Chromium 内核, 平时不开放页面文字, 第一次被问时才打开, 所以读不到会等 1 秒再读一次。
    只在判断时读一次。返回 (文字, 说明)。
    """
    if sys.platform != "win32":
        return "", "不是 Windows"
    try:
        import uiautomation as auto
    except ImportError:
        return "", "没装 uiautomation (在面板点「安装并测试读取 AI 回复」)"
    t0 = time.time()
    words = [w.lower() for w in cfg.get("reply_window_words", ["claude", "豆包", "deepseek", "chatgpt"])]

    def desc(w):
        try:
            title = w.Name or ""
            _, exe = window_info(w.NativeWindowHandle)
        except Exception:
            title, exe = "", ""
        return title, exe

    def poke(top):
        """给 Chromium 的页面子窗口发 WM_GETOBJECT, 让它打开无障碍接口 (页面文字才读得到)"""
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            hwnd = top.NativeWindowHandle
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

    def collect(top):
        """先试整页文字 (Document 的 TextPattern), 不行就收集所有 Text 控件的文字"""
        texts, n, docs = [], 0, 0
        t1 = time.time()
        for c, _ in auto.WalkControl(top, maxDepth=200):
            n += 1
            if time.time() - t1 > time_limit:
                break
            try:
                ct = c.ControlTypeName
                if ct == "DocumentControl":
                    docs += 1
                    try:
                        whole = c.GetTextPattern().DocumentRange.GetText(-1)
                        if whole and whole.strip():
                            return whole, n, docs
                    except Exception:
                        pass
                elif ct == "TextControl" and c.Name:
                    texts.append(c.Name)
            except Exception:
                pass
        return "\n".join(texts), n, docs

    with auto.UIAutomationInitializerInThread():
        cands = []
        if cfg.get("target_window"):
            try:
                from .target import find_target_window
                hwnd = find_target_window(cfg["target_window"])
                c = auto.ControlFromHandle(hwnd) if hwnd else None
                if c is not None:
                    cands.append(c)
            except Exception:
                pass
        try:
            for w in auto.GetRootControl().GetChildren():
                t, e = desc(w)
                if any(k in f"{t} {e}".lower() for k in words):
                    cands.append(w)
        except Exception:
            pass
        if not cands:
            return "", ("没找到可读的窗口 (标记的对话窗口不在, 也没有标题含 "
                        "claude/豆包/deepseek 的窗口)")
        seen, notes = set(), []
        for w in cands:
            try:
                h = w.NativeWindowHandle
            except Exception:
                h = id(w)
            if h in seen:
                continue
            seen.add(h)
            title, exe = desc(w)
            try:
                if w.GetWindowPattern().WindowVisualState == 2:  # 2 = 最小化
                    notes.append(f"「{title[:20]}」({exe}) 是最小化的, 读不到 (让它露出来, 被别的窗口挡住没关系)")
                    continue
            except Exception:
                pass
            text, n, docs = collect(w)
            tries = 0
            while not text.strip() and tries < 3:
                tries += 1
                poke(w)
                time.sleep(1.0)  # 让 Chromium 打开无障碍接口后再读
                text, n2, docs = collect(w)
                n = max(n, n2)
            if text.strip():
                took = time.time() - t0
                text = text.strip()
                return text[-max_chars:], (f"从「{title[:20]}」({exe}) 读到 {min(len(text), max_chars)} 字"
                                           f" ({took:.1f}s)")
            notes.append(f"「{title[:20]}」({exe}) 看了 {n} 个控件, 没有文字")
    hint = (" — 读不到页面文字。先确认 Claude 窗口没有最小化; 还不行就把 Claude 完全退出再打开一次"
            " (会重新开放文字); 再不行可以给 Claude 加启动参数 --force-renderer-accessibility")
    return "", "; ".join(notes) + hint

def _flat(s):
    return " ".join(s.split())


def _lines(s):
    """每行内部压缩空格, 去掉空行, 但保留分行 (分行才能认出界面标签行和代码行)"""
    return "\n".join(" ".join(l.split()) for l in s.splitlines() if l.strip())


def _common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _common_suffix(a, b):
    n = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x != y:
            break
        n += 1
    return n


def maybe_summarize_for_tts(cfg, text):
    """朗读前精简长回复: 清洗后超过 tts_summarize_min_chars 字就问 DeepSeek 总结。
    开关关掉 / 短回复 / 总结失败 (超时、没 key、出错) 都念原文。
    """
    n = len(text)
    if not cfg.get("tts_summarize", True):
        return text
    try:
        if n <= int(cfg.get("tts_summarize_min_chars", 400)):
            return text
        max_chars = int(cfg.get("tts_summarize_max_chars", 200))
    except (TypeError, ValueError):
        return text
    try:
        from .ai import summarize_for_tts
        summary, why = summarize_for_tts(cfg, text, max_chars)
    except Exception as e:
        summary, why = None, type(e).__name__
    if not summary:
        print(f"[朗读] 总结失败({why}), 念原文")
        return text
    print(f"[朗读] 原文 {n} 字, 精简成 {len(summary)} 字再念")
    return summary

def watch_reply(cfg, sent_text, speaker, rt):
    """
    发给 AI 后在后台等回复: 每秒读一次聊天窗口的全部文字,
    找到你发的那句, 它后面新出现的文字就是回复; 回复 2 秒不再变化就念出来。最多等 3 分钟。
    """
    import threading

    def run():
        key = _flat(re.sub(r"【附带的复制内容】.*|【选中的内容】.*", "", sent_text, flags=re.S))[:14]
        if len(key) < 2:
            return
        my_id = rt["tts_watch"] = rt.get("tts_watch", 0) + 1
        time.sleep(0.6)

        def after_msg():
            full, why = read_ai_reply(cfg, 200000, time_limit=3)
            full = _lines(full)
            # 你那句在页面上可能被折成几行: 允许 key 的字之间有换行/空格
            pat = r"\s*".join(re.escape(ch) for ch in key.replace(" ", ""))
            ms = list(re.finditer(pat, full))
            if not ms:
                return None, why
            rest = full[ms[-1].end():]
            nl = rest.find("\n")   # 你那句剩下的部分 (同一行) 跳过, 从下一行开始算
            return (rest[nl + 1:] if nl >= 0 else ""), why

        base, why = None, ""
        for _ in range(5):  # 读不到就多试几次 (窗口刚切换时 Chromium 可能还没准备好)
            base, why = after_msg()
            if base is not None:
                break
            time.sleep(1.0)
        if base is None:
            print(f"[朗读] 在聊天窗口里没找到你刚发的那句, 不念 ({why[:160]})")
            return
        last, same_since, t0 = None, time.time(), time.time()
        t_first = None   # 回复第一次出现的时间
        while time.time() - t0 < 180:
            time.sleep(0.3)
            if rt.get("tts_watch") != my_id:
                return  # 又发了新消息, 这个不等了
            cur, _ = after_msg()
            if cur is None:
                continue
            p = _common_prefix(base, cur)
            sfx = _common_suffix(base[p:], cur[p:])
            reply = cur[p:len(cur) - sfx].strip()
            if reply != last:
                last, same_since = reply, time.time()
                if t_first is None and len(reply) >= 5:
                    t_first = time.time()
                    speaker.prewarm()   # 回复开始出现了, 再热一次 (离开念只差一两秒)
                continue
            if len(reply) >= 5 and time.time() - same_since >= float(cfg.get("tts_stable_s", 1.0)):
                if rt.get("file_spoke_at", 0) > rt.get("last_send_at", 0):
                    return  # 文件通道已经念过这条了
                rt["uia_spoke_at"] = time.time()
                text = clean_for_tts(reply, cfg.get("tts_skip_words", DEFAULT_CONFIG["tts_skip_words"]))
                lang = tts_lang(text)
                if cfg.get("tts_only_english") and lang != "en":
                    print(f"[朗读] 回复是中文 ({len(text)} 字), 设置了只念英文, 不念")
                    return
                limit = int(cfg.get("tts_max_chars", 2000))
                text = text[:limit]
                text = maybe_summarize_for_tts(cfg, text)
                try:  # 把这次要念的全文存一份, 方便你核对念的是什么 (只存最后一次)
                    TTS_DIR.mkdir(exist_ok=True)
                    (TTS_DIR / "last_reply.txt").write_text(
                        "=== 从窗口取到的原文 ===\n" + reply + "\n\n=== 过滤后要念的 ===\n" + text, encoding="utf-8")
                except Exception:
                    pass
                print(f"[朗读] {len(text)} 字, {'英文' if lang == 'en' else '中文'}, 开头: {_flat(text)[:40]}")
                t_now = time.time()
                print(f"[朗读] 从回复出现到确认写完 {t_now - (t_first or t_now):.1f}s "
                      f"(停 {cfg.get('tts_stable_s', 1.0)}s 不变才算写完)")
                speaker.say(text, t_now)
                return
        print("[朗读] 3 分钟没等到稳定的回复, 不念了")

    threading.Thread(target=run, daemon=True).start()


def watch_reply_file(cfg, speaker, rt):
    """
    读文件通道: 每 0.15 秒看一眼 reply_file 有没有更新, 更新了就直接念 (不用等窗口稳定)。
    只念你发出去以后 reply_file_max_age_s 秒内的更新; 窗口那边已经念过的不重复念。
    """
    import threading
    name = cfg.get("reply_file")
    if not name:
        return
    path = Path(name)
    if not path.is_absolute():
        path = PROJECT_ROOT / name
    print(f"[朗读] 读文件通道: 开 (看 {path.name}, 没更新时用读窗口兜底)")

    def run():
        try:
            last = path.stat().st_mtime_ns
        except OSError:
            last = None
        while True:
            time.sleep(0.15)
            try:
                m = path.stat().st_mtime_ns
            except OSError:
                continue
            if m == last:
                continue
            last = m
            t_seen = time.time()
            try:
                raw = path.read_text(encoding="utf-8-sig")
            except Exception as e:
                print(f"[朗读] 读回复文件失败: {e}")
                continue
            lines = raw.splitlines()
            if lines and lines[0].startswith("#"):   # 第一行可以是 "# 2026-11-06 16:20:01" 之类的标记
                lines = lines[1:]
            body = "\n".join(lines).strip()
            if len(body) < 2 or not tts_enabled(cfg):
                continue
            sent = rt.get("last_send_at", 0)
            if not sent or t_seen - sent > float(cfg.get("reply_file_max_age_s", 180)):
                print("[朗读] 回复文件更新了, 但最近没发过消息, 不念")
                continue
            if rt.get("uia_spoke_at", 0) > sent:
                print("[朗读] 回复文件更新了, 但窗口那边已经念过这条, 不重复念")
                continue
            text = clean_for_tts(body)
            if cfg.get("tts_only_english") and tts_lang(text) != "en":
                print(f"[朗读] 回复是中文 ({len(text)} 字), 设置了只念英文, 不念")
                continue
            text = text[:int(cfg.get("tts_max_chars", 2000))]
            rt["file_spoke_at"] = t_seen
            rt["tts_watch"] = rt.get("tts_watch", 0) + 1   # 让读窗口的那个停下
            print(f"[朗读] 从文件读到回复 {len(text)} 字 (发出后 {t_seen - sent:.1f}s), 开头: {_flat(text)[:40]}")
            text = maybe_summarize_for_tts(cfg, text)
            speaker.say(text, t_seen)

    threading.Thread(target=run, daemon=True).start()
