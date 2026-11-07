# -*- coding: utf-8 -*-
"""voice-trigger 功能包。

原 main.py 里的功能按职责拆到各子模块:
  config / wake / journal / windows / ai / tts / reply / asr / runtime / cli
  audio/ (VAD、麦克风、NVIDIA DLL)、input/ (剪贴板、粘贴、热键、自动发送开关)、ui/ (控制面板)
入口仍是 main.py 与 gui.pyw, 行为不变。
"""
__version__ = "1.0.0"

from .config import CONFIG_PATH, DEFAULT_CONFIG, load_config, normalize, save_config
from .wake import find_command, find_wake


