# -*- coding: utf-8 -*-
"""
发发 (voice-trigger) 控制面板 —— 入口。

界面和后台小工具都在 voice_trigger/ui/ 里 (app.py 是分页窗口, panel.py 是
启动/停止/读写配置的辅助函数)。这个文件只负责把面板拉起来, 文件名不变。
"""
import sys

from voice_trigger.ui.app import launch

if __name__ == "__main__":
    sys.exit(launch())
