# -*- coding: utf-8 -*-
"""让 Windows 找到 pip 装的 NVIDIA DLL (CUDA 加速用)。"""
from pathlib import Path

def add_nvidia_dlls():
    """
    pip 装的 nvidia-cublas-cu12 / nvidia-cudnn-cu12 把 DLL 放在 site-packages\\nvidia\\*\\bin,
    Windows 默认找不到, 这里手动告诉它去哪找。没装就什么都不做。
    """
    import os
    import site
    roots = []
    try:
        roots = site.getsitepackages() + [site.getusersitepackages()]
    except Exception:
        pass
    for r in roots:
        nv = Path(r) / "nvidia"
        if not nv.is_dir():
            continue
        for b in nv.glob("*/bin"):
            try:
                os.add_dll_directory(str(b))
            except (AttributeError, OSError):
                pass
            os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")
