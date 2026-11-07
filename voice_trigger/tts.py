# -*- coding: utf-8 -*-
"""朗读 AI 回复: edge-tts 合成 + Windows MCI 播放, 以及念之前的文字过滤。"""
import json
import re
import time

from .config import CONFIG_PATH
from .paths import TTS_DIR

# ---------------------------------------------------------------
# 朗读 AI 回复 (TTS): edge-tts 生成 mp3, Windows 自带的 MCI 播放 (不用装播放器)
# ---------------------------------------------------------------
_TTS_LIVE = {"mtime": None, "on": None}


def tts_enabled(cfg):
    """念不念 AI 回复: 每次发完都看一眼 config.json 的 "tts" 和 tts_* 朗读设置 (改了立刻生效, 不用重启)"""
    try:
        m = CONFIG_PATH.stat().st_mtime
        if m != _TTS_LIVE["mtime"]:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                new = json.load(f)
            _TTS_LIVE["on"] = bool(new.get("tts", False))
            for k, v in new.items():   # 语速/声音/跳过词这些朗读设置也一起换成新的 (Speaker 用的是同一个 cfg)
                if k.startswith("tts_") and not k.endswith("hotkey"):
                    cfg[k] = v
            cfg["tts"] = _TTS_LIVE["on"]
            _TTS_LIVE["mtime"] = m
        return _TTS_LIVE["on"]
    except Exception:
        return bool(cfg.get("tts"))

def tts_lang(text):
    """中文字超过 20% 算中文, 否则算英文"""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    letters = sum(1 for ch in text if ch.isalpha())
    return "zh" if letters and cjk / letters > 0.2 else "en"


TIME_RE = re.compile(
    r"(?:(?:今天|昨天|前天|上午|下午|晚上|凌晨|中午|早上)\s*)*\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:AM|PM|am|pm))?"
    r"|\d+\s*(?:秒|分钟|小时|天)前|\b\d+\s*(?:s|sec|min|mins|minutes?|h|hr|hours?)\s+ago\b|刚刚|\bjust now\b",
    re.I)


DATE_RE = re.compile(
    r"\d{4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*日?|\d{1,2}\s*月\s*\d{1,2}\s*日|(?:星期|礼拜|周)[一二三四五六日天]")
LABEL_PREFIX = re.compile(r"^(?:Claude\s*)?(?:已回复|回复了|Claude replied|Replied|You said|你说)\s*[:：]\s*", re.I)


# 聊天界面上的按钮文字, 不管 config 里怎么写都会跳过 (一整行就是这些词才跳过)
UI_BUTTON_WORDS = ["添加回应", "回复", "复制", "复制消息", "编辑", "编辑消息", "更多选项", "重试", "分享",
                   "Add reaction", "Reply", "Copy", "Copy message", "Edit", "Edit message", "More options",
                   "Retry", "Share"]
# 无障碍提示 (给读屏软件的操作说明): 出现这些说法的短行整行跳过
A11Y_HINT_RE = re.compile(r"按\s*(Tab|Esc|Escape|Enter|回车|空格|方向)\s*键|快速回应|焦点(进入|移到|离开)|"
                          r"\bpress (tab|escape|esc|enter)\b|quick reactions?|keyboard shortcut|screen reader", re.I)


# 任务/线程卡片上的状态文字: 这一行和它上面紧挨着的短标题行都不念
STATUS_RE = re.compile(r"^[\s\-–—·•|]*(处理中|进行中|已完成|已暂停|排队中|思考中|正在思考|Working|Processing|"
                       r"In progress|Done|Completed|Queued|Thinking)[.。…\s]*$", re.I)


def drop_repeats(lines, min_len=6):
    """
    大段重复只念一遍: 一行的内容如果已经完整出现在前面念过的内容里 (哪怕前面是分好几行、
    或者这行只是前面某段被截断的开头), 就跳过; 如果这行比前面某行更完整 (前面那行是它的截断版), 用这行替换。
    """
    def key(x):
        return re.sub(r"[\W_]+", "", x).lower()
    out = []
    for x in lines:
        if STATUS_RE.match(x):
            # 状态行上面那行通常是卡片标题 (短、没有句号), 一起去掉
            if out and len(out[-1]) <= 30 and not re.search(r"[。！？.!?]$", out[-1]):
                out.pop()
            continue
        k = key(x)
        if len(k) >= min_len and k in "".join(key(o) for o in out):
            continue
        out = [o for o in out if not (len(key(o)) >= min_len and key(o) in k)]  # 前面的截断版换成完整版
        out.append(x)
    return out


def skip_ui_lines(text, words):
    """去掉聊天界面上的标签行 (发言人名字、时间日期、按钮、无障碍朗读标签), 并去掉重复的段落"""
    ws = [w.strip() for w in list(words) + UI_BUTTON_WORDS if w.strip()]
    rows = []   # (正文, 是否从「已回复：」这类标签里拆出来的)
    for line in text.splitlines():
        # ￼ (U+FFFC) 是图标/按钮占位符, 零宽字符也去掉
        s = re.sub(r"[\ufffc\ufffd\u200b-\u200f\u2060\ufeff]", "", line).strip()
        if not s:
            continue
        if len(s) <= 80 and A11Y_HINT_RE.search(s):
            continue
        # 无障碍按钮标签: 「显示 Claude 已回复：… 的消息操作」/ "Show message actions"
        if s.endswith("的消息操作") or re.search(r"message actions$", s, re.I):
            continue
        # 时间日期: 14:49 / 2:49 PM / 下午 2:49 / 昨天 / 刚刚 / 3 分钟前 / 2026年11月7日星期六
        s = DATE_RE.sub(" ", TIME_RE.sub(" ", s)).strip(" ·•|,，-–—")
        s = " ".join(s.split())
        if not s or re.fullmatch(r"(今天|昨天|前天|刚刚|现在|Just now|Now|Today|Yesterday|"
                                 r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*|[年月日\s\d]+)", s, re.I):
            continue
        low = s.lower()
        if any(low == w.lower() or (len(w) >= 4 and low.startswith(w.lower()) and len(s) <= 40) for w in ws):
            continue
        m = LABEL_PREFIX.match(s)
        if m:
            s = s[m.end():].strip()
            if not s:
                continue
        rows.append((s, bool(m)))

    def key(x):
        return re.sub(r"[\W_]+", "", x).lower()
    plain = {key(x) for x, lab in rows if not lab}
    out, seen = [], set()
    for x, lab in rows:
        k = key(x)
        if lab and any(k in pk for pk in plain):   # 标签里那份 (常常只截了第一句) 和正文重复, 用正文那份
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return "\n".join(drop_repeats(out))


def clean_for_tts(text, skip_words=None):
    """去掉代码、链接、markdown 符号, 只留正文"""
    if skip_words:
        text = skip_ui_lines(text, skip_words)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    keep = []
    for line in text.splitlines():
        s = line.strip().replace("**", "").replace("__", "")
        if not s:
            continue
        sym = sum(1 for ch in s if ch in "{}[]();=<>/\\|$&^%~")
        if len(s) > 8 and sym / len(s) > 0.15:   # 像代码的行不念
            continue
        s = re.sub(r"^[#>*\-+\d.)\s]+", "", s)    # 标题/列表/引用符号
        s = s.replace("*", "")
        keep.append(s)
    out = "\n".join(keep).strip()
    # 去掉回复末尾聊天界面上的按钮文字 (Copy / Retry 之类)
    ui = ("Copy", "Retry", "Edit", "Share", "复制", "重试", "编辑", "分享")
    changed = True
    while changed:
        changed = False
        for w in ui:
            if out.endswith(w):
                out = out[:-len(w)].rstrip()
                changed = True
    return out


def split_chunks(text, size=220, first=15):
    """按句子切成一小段一小段; 第一段特别短 (一句话), 合成快, 能早点开口"""
    parts = re.split(r"(?<=[。！？!?.;；，,\n])", text)
    out, cur = [], ""
    for p in parts:
        lim = first if not out else size
        if len(cur) + len(p) > lim and cur:
            out.append(cur)
            cur = ""
        cur += p
    if cur.strip():
        out.append(cur)
    return [c.strip() for c in out if c.strip()]

class EdgeWS:
    """
    常驻的 edge-tts 连接: 连一次, 之后每一句都在同一条 websocket 上合成。
    edge-tts 自带的 Communicate 每句都新建连接 (你那边每次要 ~1 秒); 连着的时候一句只要 0.1~0.4 秒。
    服务器闲置大约 30~60 秒会断开, 断了就自动重连再试一次。
    """

    def __init__(self):
        import asyncio
        import threading
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.ws = self.session = None
        self.last_used = 0.0
        self.lock = None

    def _run(self, coro, timeout=25):
        import asyncio
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _close(self):
        try:
            if self.ws is not None:
                await self.ws.close()
            if self.session is not None:
                await self.session.close()
        except Exception:
            pass
        self.ws = self.session = None

    async def _connect(self):
        import aiohttp
        from edge_tts import communicate as C
        from edge_tts.drm import DRM
        await self._close()
        self.session = aiohttp.ClientSession(trust_env=True)
        self.ws = await self.session.ws_connect(
            f"{C.WSS_URL}&ConnectionId={C.connect_id()}"
            f"&Sec-MS-GEC={DRM.generate_sec_ms_gec()}&Sec-MS-GEC-Version={C.SEC_MS_GEC_VERSION}",
            compress=15, headers=DRM.headers_with_muid(C.WSS_HEADERS), ssl=C._SSL_CTX)
        await self.ws.send_str(
            f"X-Timestamp:{C.date_to_string()}\r\nContent-Type:application/json; charset=utf-8\r\n"
            "Path:speech.config\r\n\r\n"
            '{"context":{"synthesis":{"audio":{"metadataoptions":{"sentenceBoundaryEnabled":"false",'
            '"wordBoundaryEnabled":"false"},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n')
        self.last_used = time.time()

    def _stale(self):
        return self.ws is None or self.ws.closed or time.time() - self.last_used > 25

    async def _synth_once(self, text, voice, rate):
        import asyncio
        import aiohttp
        from xml.sax.saxutils import escape
        from edge_tts import communicate as C
        ssml = ("<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
                f"<voice name='{voice}'><prosody pitch='+0Hz' rate='{rate}' volume='+0%'>"
                f"{escape(C.remove_incompatible_characters(text))}</prosody></voice></speak>")
        t0 = time.time()
        await self.ws.send_str(C.ssml_headers_plus_data(C.connect_id(), C.date_to_string(), ssml))
        audio, first = bytearray(), None
        while True:
            m = await asyncio.wait_for(self.ws.receive(), 15)
            if m.type == aiohttp.WSMsgType.TEXT:
                if "Path:turn.end" in m.data:
                    break
            elif m.type == aiohttp.WSMsgType.BINARY:
                hl = int.from_bytes(m.data[:2], "big")
                if b"Path:audio" in m.data[2:2 + hl] and len(m.data) > hl + 2:
                    if first is None:
                        first = time.time() - t0
                    audio += m.data[2 + hl:]
            else:
                raise ConnectionError(f"连接断了 ({m.type})")
        if not audio:
            raise ConnectionError("没收到声音")
        self.last_used = time.time()
        return bytes(audio), first or 0.0

    async def _synth(self, text, voice, rate):
        import asyncio
        if self.lock is None:
            self.lock = asyncio.Lock()
        async with self.lock:   # 同一条连接一次只合成一句
            t_conn = 0.0
            if self._stale():
                t = time.time()
                await self._connect()
                t_conn = time.time() - t
            try:
                audio, first = await self._synth_once(text, voice, rate)
            except Exception:
                if t_conn:
                    raise
                t = time.time()
                await self._connect()
                t_conn = time.time() - t
                audio, first = await self._synth_once(text, voice, rate)
            return audio, t_conn, first

    def synth(self, text, voice, rate="+0%"):
        """返回 (mp3 字节, 建连耗时, 首包耗时)"""
        return self._run(self._synth(text, voice, rate))

    def warm(self):
        """没连着 (或闲太久可能被断了) 就连上; 返回建连耗时, 已经连着返回 None"""
        async def go():
            if self.lock is None:
                import asyncio
                self.lock = asyncio.Lock()
            async with self.lock:
                if not self._stale():
                    return None
                t = time.time()
                await self._connect()
                return time.time() - t
        return self._run(go())

_EDGE = None


def edge_conn():
    global _EDGE
    if _EDGE is None:
        _EDGE = EdgeWS()
    return _EDGE

class Speaker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.active = False
        self.last_text = ""
        self._stop = False
        self._thread = None

    def say(self, text, t_ready=None):
        import threading
        self.stop()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.last_text = text
        self._stop = False
        self.active = True
        self._t_ready = t_ready or time.time()
        self._thread = threading.Thread(target=self._run, args=(text,), daemon=True)
        self._thread.start()

    def prewarm(self):
        """你话一发出去就先连上语音服务器 (连接一直留着), 等 AI 回复的这几秒里把连接准备好"""
        import threading
        if time.time() - getattr(self, "_warm_at", 0) < 3:
            return
        self._warm_at = time.time()

        def run():
            try:
                t = edge_conn().warm()
                if t is not None:
                    print(f"[朗读] 预热: 连上语音服务器 {t:.1f}s (连接留着, 念的时候直接用)")
            except Exception as e:
                print(f"[朗读] 预热失败: {type(e).__name__}: {str(e)[:80]}")
        threading.Thread(target=run, daemon=True).start()

    def repeat(self):
        if self.last_text:
            print("[朗读] 再念一遍")
            self.say(self.last_text)
        else:
            print("[朗读] 还没有念过东西")

    def stop(self):
        if self.active:
            self._stop = True

    def _run(self, text):
        import asyncio
        import ctypes
        try:
            import edge_tts
        except ImportError:
            print("[朗读] 没装 edge-tts (在面板点「安装并试听朗读」)")
            self.active = False
            return
        mci = ctypes.windll.winmm.mciSendStringW
        buf = ctypes.create_unicode_buffer(64)
        tts_enabled(self.cfg)   # 先读一下最新的语速/声音 (面板刚改的)
        lang = tts_lang(text)
        voice = self.cfg.get("tts_voice_zh" if lang == "zh" else "tts_voice_en",
                             "zh-CN-XiaoxiaoNeural" if lang == "zh" else "en-US-AriaNeural")
        rate = str(self.cfg.get("tts_rate", "+0%"))
        if not re.fullmatch(r"[+-]\d+%", rate):
            rate = "+0%"
        print(f"[朗读] 用 {voice}, 语速 {rate}")
        TTS_DIR.mkdir(exist_ok=True)
        try:
            import threading
            chunks = split_chunks(text)
            files, errs = {}, {}

            stats = {}

            def synth(i):  # 后台合成第 i 段 (念第 i-1 段的时候就在合成, 段和段之间不用等)
                f = TTS_DIR / f"part{i % 3}.mp3"
                try:
                    try:   # 用常驻连接 (快)
                        audio, t_conn, t_first = edge_conn().synth(chunks[i], voice, rate)
                        f.write_bytes(audio)
                        stats[i] = (t_conn, t_first)
                    except Exception as e1:   # 不行就用 edge-tts 原来的方式
                        print(f"[朗读] 常驻连接出错, 改用普通方式: {type(e1).__name__}: {str(e1)[:60]}")
                        asyncio.run(edge_tts.Communicate(chunks[i], voice, rate=rate).save(str(f)))
                    files[i] = f
                except Exception as e:
                    errs[i] = e

            mci("close vt_tts", None, 0, None)
            t_syn = time.time()
            th = threading.Thread(target=synth, args=(0,), daemon=True)
            th.start()
            for i in range(len(chunks)):
                th.join()
                if self._stop:
                    break
                if i in errs:
                    raise errs[i]
                if i == 0:
                    now = time.time()
                    tc, tf = stats.get(0, (None, None))
                    detail = (f" (建连 {tc:.2f}s, 首包 {tf:.2f}s, 第一句 {len(chunks[0])} 字)"
                              if tc is not None else "")
                    print(f"[朗读] 合成第一句 {now - t_syn:.1f}s{detail}, 从回复写完到开口 {now - self._t_ready:.1f}s")
                if i + 1 < len(chunks):
                    th = threading.Thread(target=synth, args=(i + 1,), daemon=True)
                    th.start()
                f = files[i]
                mci(f'open "{f}" type mpegvideo alias vt_tts', None, 0, None)
                mci("play vt_tts", None, 0, None)
                while not self._stop:
                    time.sleep(0.05)
                    mci("status vt_tts mode", buf, 64, None)
                    if buf.value != "playing":
                        break
                mci("stop vt_tts", None, 0, None)
                mci("close vt_tts", None, 0, None)
        except Exception as e:
            print(f"[朗读] 出错: {type(e).__name__}: {str(e)[:100]}")
        finally:
            mci("close vt_tts", None, 0, None)
            if self._stop:
                print("[朗读] 停止")
            self.active = False
