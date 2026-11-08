# -*- coding: utf-8 -*-
"""DeepSeek 判断 (是不是对 AI 说的 / 说完整没有) 与文字规则。"""
import json
import re
import time

from .paths import AI_USAGE
from .reply import read_ai_reply

AI_PROMPT = ("用户开着麦克风, 说的每句话都会被转成文字 (可能有错别字)。"
             "判断【最后一句】是不是用户想发给 AI 助手的话: 提问、下指令、请 AI 帮忙、跟 AI 讨论问题都算「是」。"
             "跟旁边的人聊天、打电话、自言自语、感叹、背景里别人的声音算「否」。只回答一个字: 是 或 否。")


def unfinished_reason(cfg, text):
    """用文字规则看这句话是不是明显没说完, 是的话返回原因 (给日志看), 否则返回 None"""
    if not cfg.get("turn_rules", True):
        return None
    t = text.strip()
    if not t:
        return None
    if re.search(r"(…|\.\.\.|。。|，|,|、)$", t):
        return "句末是省略号/逗号"
    core = re.sub(r"[\s。．.!！?？，,、…~～:：;；\"'“”‘’]+", "", t)
    if not core:
        return None
    fill = sum(1 for ch in core if ch in "嗯呃额啊哦呀")
    if len(core) <= 12 and fill / len(core) >= 0.4:
        return "几乎都是「嗯/啊」"
    tail = re.sub(r"[\s。．.!！?？~～\"'“”‘’]+$", "", t)
    m = re.search(r"([A-Za-z']+)$", tail)
    if m:
        w = m.group(1).lower()
        if w in [x.lower() for x in cfg.get("turn_hang_words_en", [])]:
            return f"句末是「{w}」"
    else:
        for w in sorted(cfg.get("turn_hang_words_zh", []), key=len, reverse=True):
            if w and tail.endswith(w):
                return f"句末是「{w}」"
    return None


AI_TURN_PROMPT = ("下面是语音转成的文字 (可能有错别字), 说话人停顿了一下。"
                  "判断这句话是不是已经说完整了: 意思完整、可以直接发出去算「是」;"
                  "话说到一半、明显后面还有内容 (比如以「然后」「因为」「就是」「帮我把」结尾) 算「否」。"
                  "只回答一个字: 是 或 否。")

def ai_is_for_ai(cfg, text, context, last_ai=None):
    """
    问 DeepSeek 这句话是不是对 AI 说的。
    last_ai = {"text": 上次发给 AI 的话, "at": 时间}: 刚跟 AI 说过话时一起给 DeepSeek 参考。
    """
    ctx = "\n".join(context) if context else "(没有)"
    extra = ""
    if cfg.get("ai_context", True) and last_ai and last_ai.get("text"):
        ago = time.time() - last_ai["at"]
        if ago <= float(cfg.get("ai_context_max_s", 120)):
            extra = f"她 {ago:.0f} 秒前刚对 AI 说了: {last_ai['text'][:300]}\n"
            note = f"带上 {ago:.0f} 秒前发给 AI 的话"
            if cfg.get("ai_context_read_reply", False):
                try:
                    reply, why = read_ai_reply(cfg, int(cfg.get("ai_context_reply_chars", 800)))
                except Exception as e:
                    reply, why = "", f"出错: {type(e).__name__}"
                if reply:
                    extra += f"AI 窗口里最近的内容 (末尾):\n{reply}\n"
                    tail = " ".join(reply.split())[-20:]
                    why += f", 结尾: …{tail}"
                note += f"; AI 回复: {why}"
            print(f"  [上下文] {note}")
    if extra:
        user = (f"{extra}\n她刚和 AI 对话过, 紧接着的追问、补充、对 AI 回复的回应 (比如「那…呢」「为什么」「再详细点」「不对」)"
                f"应该判为「是」。\n\n前面几句 (参考):\n{ctx}\n\n她现在说: {text}")
    else:
        user = f"前面几句 (参考):\n{ctx}\n\n最后一句: {text}"
    return ask_deepseek(cfg, AI_PROMPT, user)


def ask_deepseek(cfg, system, user):
    """
    问 DeepSeek 一个"是/否"问题。
    返回 (结果 True/False/None, 说明)。None = 没问成 (超时/出错/没 key/超过每日上限)。
    """
    import os
    import urllib.request
    key = (cfg.get("ai_filter_api_key") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        return None, "没填 DeepSeek key"
    today = time.strftime("%Y-%m-%d")
    try:
        usage = json.loads(AI_USAGE.read_text(encoding="utf-8"))
    except Exception:
        usage = {}
    n = usage.get(today, 0)
    if n >= int(cfg.get("ai_filter_daily_limit", 500)):
        return None, f"今天已经问了 {n} 次, 到上限了"
    usage = {today: n + 1}
    try:
        AI_USAGE.write_text(json.dumps(usage), encoding="utf-8")
    except OSError:
        pass
    body = {
        "model": cfg.get("ai_filter_model", "deepseek-flash"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 3, "temperature": 0, "stream": False,
    }
    if cfg.get("ai_filter_thinking", "disabled"):
        body["thinking"] = {"type": cfg.get("ai_filter_thinking", "disabled")}
    url = cfg.get("ai_filter_base_url", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get("ai_filter_timeout_s", 3))) as r:
            ans = json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"].strip()
    except Exception as e:
        msg = type(e).__name__
        code = getattr(e, "code", None)
        if code:
            msg += f" {code}" + (" (key 不对?)" if code == 401 else " (余额不足?)" if code == 402 else "")
        elif "timed out" in str(e).lower():
            msg = "超时"
        return None, msg
    dt = time.time() - t0
    return ans.startswith("是"), f"{ans} ({dt:.1f}s, 今天第 {n + 1} 次)"
