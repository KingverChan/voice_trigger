# -*- coding: utf-8 -*-
"""
给本机的 Claude Code 用的 Stop hook: Claude 每次回复完, 把最后一条回复的文字写进 claude_reply.txt。
在 %USERPROFILE%\\.claude\\settings.json 里加 (路径按你的改):
{
  "hooks": {
    "Stop": [{"hooks": [{"type": "command",
      "command": "C:\\\\Users\\\\Jimmy\\\\Documents\\\\GitHub\\\\voice-trigger\\\\.venv\\\\Scripts\\\\python.exe C:\\\\Users\\\\Jimmy\\\\Documents\\\\GitHub\\\\voice-trigger\\\\claude_code_stop_hook.py"}]}]
  }
}
"""
import json
import sys

from write_reply import write_reply


def last_assistant_text(transcript_path):
    last = ""
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") or {}
            if rec.get("type") != "assistant" or msg.get("role") != "assistant":
                continue
            parts = msg.get("content")
            if isinstance(parts, str):
                text = parts
            else:
                text = "\n".join(p.get("text", "") for p in parts or [] if p.get("type") == "text")
            if text.strip():
                last = text
    return last


if __name__ == "__main__":
    try:
        data = json.load(sys.stdin)
        text = last_assistant_text(data["transcript_path"])
        if text:
            write_reply(text)
    except Exception:
        pass  # hook 出错不影响 Claude Code
