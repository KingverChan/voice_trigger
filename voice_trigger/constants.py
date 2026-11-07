# -*- coding: utf-8 -*-
"""全局采样参数 (16kHz / 30ms 一帧)。"""

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480
