# -*- coding: utf-8 -*-
"""每日语音日志 (daily-journal) 与后台运行日志文件。"""
import json
import sys
import time
from pathlib import Path

from .paths import LOG_PATH, PROJECT_ROOT

def _journal_dir(cfg):
    d = Path(cfg.get("daily_journal_dir") or "../daily-journal")
    if not d.is_absolute():
        d = (PROJECT_ROOT / d).resolve()
    return d


def log_speech(cfg, text):
    """log_all_speech 打开时, 把识别出的一句话写进 daily-journal\\logs\\今天.jsonl。"""
    if not cfg.get("log_all_speech"):
        return
    d = _journal_dir(cfg)
    if (d / "PAUSED").exists():  # daily-journal 的 pause.bat 也会暂停语音记录
        return
    try:
        logs = d / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        ts = time.localtime()
        rec = {"t": time.strftime("%H:%M:%S", ts), "src": "voice", "text": text}
        with open(logs / f"{time.strftime('%Y-%m-%d', ts)}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"  写每日日志失败: {e}")


def mark_pasting(cfg):
    """告诉 daily-journal 的剪贴板记录器: 接下来几秒剪贴板变化是我们自己粘贴, 别记。"""
    try:
        d = _journal_dir(cfg)
        if d.exists():
            (d / ".voice_trigger_pasting").write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass

class TimeStampWriter:
    """日志每一行前面加上时间 (时:分:秒.毫秒), 看得出每一步的先后和间隔"""

    def __init__(self, f):
        self.f = f
        self.at_start = True
        self.lock = __import__("threading").Lock()

    def write(self, s):
        with self.lock:
            out = []
            for part in s.splitlines(keepends=True):
                if self.at_start and part.strip():
                    t = time.time()
                    out.append(time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d} ")
                out.append(part)
                self.at_start = part.endswith("\n") or (self.at_start and not part.strip())
            self.f.write("".join(out))
        return len(s)

    def flush(self):
        self.f.flush()

    def __getattr__(self, name):
        return getattr(self.f, name)

def setup_log():
    """后台运行(pythonw)没有窗口, 把输出写进 voice-trigger.log; 超过 5MB 就清空重来。"""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 5 * 1024 * 1024:
            LOG_PATH.unlink()
    except OSError:
        pass
    f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = TimeStampWriter(f)
    print(f"\n===== 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
