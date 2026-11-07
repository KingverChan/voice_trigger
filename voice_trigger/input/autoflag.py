# -*- coding: utf-8 -*-
"""自动发送开关: AUTO_SEND_ON 文件存在 = 开 (后台每 0.3 秒看一次)。"""
from ..paths import AUTO_FLAG

def auto_on():
    return AUTO_FLAG.exists()


def set_auto(on):
    try:
        if on:
            AUTO_FLAG.write_text("on", encoding="utf-8")
        elif AUTO_FLAG.exists():
            AUTO_FLAG.unlink()
    except OSError as e:
        print(f"  切换自动发送失败: {e}")
