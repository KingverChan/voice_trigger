# -*- coding: utf-8 -*-
"""唤醒词 (fafa 及别名) 匹配。"""
from .config import _PUNCT, normalize

def find_wake(text, wake_words):
    """
    找第一个唤醒词。没有返回 None, 有返回 (唤醒词前面的话, 唤醒词后面的话)。
    """
    # 在"去标点小写"的文本里找, 再映射回原文位置, 这样 "Fa, fa" 也能命中
    keep = [i for i, ch in enumerate(text) if not _PUNCT.fullmatch(ch)]
    norm = "".join(text[i] for i in keep).lower()
    best = None  # (结束位置)
    for w in wake_words:
        nw = normalize(w)
        if not nw:
            continue
        pos = norm.find(nw)
        if pos != -1:
            end = pos + len(nw)
            if best is None or pos < best[0]:
                best = (pos, end)
    if best is None:
        return None
    before = text[:keep[best[0]]] if best[0] < len(keep) else ""
    before = _PUNCT.sub(" ", before).strip(" ，,。.")
    end_in_norm = best[1]
    if end_in_norm >= len(keep):
        return before, ""
    start_in_text = keep[end_in_norm]
    return before, _PUNCT.sub(" ", text[start_in_text:]).strip(" ，,。.")

def find_command(text, wake_words):
    """
    返回 None: 没有唤醒词
    返回 "":   有唤醒词但后面没话 (等下一句当指令)
    返回 "xxx": 唤醒词后面的指令
    """
    r = find_wake(text, wake_words)
    return None if r is None else r[1]
