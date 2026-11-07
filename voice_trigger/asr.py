# -*- coding: utf-8 -*-
"""faster-whisper 模型加载 (显卡优先, 失败退回 CPU)。"""
from .audio.gpu import add_nvidia_dlls

def load_whisper(cfg):
    """按 config 的 device 加载 Whisper。用显卡失败(没 N 卡/缺 DLL/显存不够)就退回 CPU。"""
    import numpy as np
    dev = str(cfg.get("device", "cpu")).lower()
    if dev in ("auto", "cuda", "gpu"):
        add_nvidia_dlls()
    from faster_whisper import WhisperModel
    size = cfg["model_size"]
    if dev in ("auto", "cuda", "gpu"):
        try:
            import ctranslate2
            n = ctranslate2.get_cuda_device_count()
            if n == 0:
                raise RuntimeError("没检测到能用的 NVIDIA 显卡 (或驱动太旧)")
            ct = cfg.get("gpu_compute_type", "float16")
            print(f"加载 Whisper 模型 {size} 到显卡 (cuda, {ct})...")
            model = WhisperModel(size, device="cuda", compute_type=ct)
            # 先试转一段静音: 缺 cuBLAS/cuDNN 的话这里就会报错, 好及时退回 CPU
            list(model.transcribe(np.zeros(16000, dtype=np.float32), language="zh")[0])
            print("Whisper: 显卡加速 (GPU)")
            return model
        except Exception as e:
            print(f"显卡加速用不了, 改用 CPU。原因: {e}")
    print(f"加载 Whisper 模型 {size} (CPU, 第一次会下载, 请等待)...")
    model = WhisperModel(size, device="cpu", compute_type=cfg.get("compute_type", "int8"))
    print("Whisper: CPU")
    return model
