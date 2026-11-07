# -*- coding: utf-8 -*-
"""
把 AI 的回复写进 claude_reply.txt, 发发看到更新就会直接念。
用法:
  python write_reply.py "要念的文字"
  echo 要念的文字 | python write_reply.py
先写临时文件再替换, 发发不会读到写了一半的内容。
"""
import os
import sys
import time
from pathlib import Path

OUT = Path(__file__).with_name("claude_reply.txt")


def write_reply(text):
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(f"# {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text.strip()}\n", encoding="utf-8")
    os.replace(tmp, OUT)


if __name__ == "__main__":
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read()
    if text.strip():
        write_reply(text)
