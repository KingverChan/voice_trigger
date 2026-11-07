# -*- coding: utf-8 -*-
"""项目根目录与运行时文件路径。

原来各函数用 Path(__file__).with_name(...) 找文件; 拆成包以后统一放这里,
保证 config.json / 日志 / 缓存仍然在项目根目录 (和重构前一致)。
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = PROJECT_ROOT / "config.json"
LOG_PATH = PROJECT_ROOT / "voice-trigger.log"
AI_USAGE = PROJECT_ROOT / "ai_filter_usage.json"
AUTO_FLAG = PROJECT_ROOT / "AUTO_SEND_ON"
TTS_CMD = PROJECT_ROOT / "TTS_CMD"
TTS_DIR = PROJECT_ROOT / "tts_cache"
MODELS_DIR = PROJECT_ROOT / "models"
BENCH_WAV = PROJECT_ROOT / "bench.wav"


