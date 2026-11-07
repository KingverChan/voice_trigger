# -*- coding: utf-8 -*-
"""自测: 发送目标窗口的错误路径和 dry-run。

不碰真实键盘/鼠标: pyautogui/pyperclip 全部用假模块替换, 窗口函数用 mock。
跑法: <venv-python> -m unittest discover -s tests -v
"""
import sys
import types
import unittest
from unittest import mock

from PIL import Image

import voice_trigger.input.paste as paste
import voice_trigger.target as target

TARGET = {
    "exe": "claude.exe",
    "title_substr": "Claude",
    "hwnd": 4321,
    "client_rect": [100, 200, 900, 800],
    "click_rel_x": None,
    "click_rel_y": None,
}


def cfg_with(target=None, **extra):
    cfg = {"target_window": target, "press_enter": True, "image_wait_s": 0.01}
    cfg.update(extra)
    return cfg


class FakeGUI:
    def __init__(self):
        self.calls = []

    def position(self):
        return (11, 22)

    def click(self, x, y):
        self.calls.append(("click", x, y))

    def hotkey(self, *keys):
        self.calls.append(("hotkey",) + keys)

    def press(self, key):
        self.calls.append(("press", key))

    def moveTo(self, x, y):
        self.calls.append(("moveTo", x, y))


class FakeClip:
    def __init__(self):
        self.text = "原来的剪贴板"

    def paste(self):
        return self.text

    def copy(self, value):
        self.text = value


class FakeModules:
    """把 pyautogui/pyperclip 塞进 sys.modules, 退出时还原。"""

    def __enter__(self):
        self.gui, self.clip = FakeGUI(), FakeClip()
        self._patch = mock.patch.dict(sys.modules, {
            "pyautogui": self.gui,
            "pyperclip": self.clip,
        })
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


def no_print():
    return mock.patch("builtins.print")


class ErrorPaths(unittest.TestCase):
    def test_no_target(self):
        with no_print() as p:
            ok = paste.send_text(cfg_with(None), "你好")
        self.assertFalse(ok)
        printed = " ".join(str(a) for c in p.call_args_list for a in c.args)
        self.assertIn("还没标记目标窗口", printed)

    def test_target_window_gone(self):
        with mock.patch.object(paste, "find_target_window", return_value=None), no_print() as p:
            ok = paste.send_text(cfg_with(TARGET), "你好")
        self.assertFalse(ok)
        printed = " ".join(str(a) for c in p.call_args_list for a in c.args)
        self.assertIn("标记的窗口不见了", printed)

    def test_activation_fails_and_no_calibration_point(self):
        with mock.patch.object(paste, "find_target_window", return_value=4321), \
                mock.patch.object(paste, "window_info", return_value=("Claude", "claude.exe")), \
                mock.patch.object(paste, "activate_window", return_value=False), \
                mock.patch.object(paste, "click_point", return_value=None), \
                no_print() as p:
            ok = paste.send_text(cfg_with(TARGET), "你好")
        self.assertFalse(ok)
        printed = " ".join(str(a) for c in p.call_args_list for a in c.args)
        self.assertIn("--calibrate", printed)
        self.assertNotIn("已发送", printed)


class DryRun(unittest.TestCase):
    def test_dry_run_touches_nothing(self):
        with mock.patch.object(paste, "find_target_window") as find, \
                mock.patch.object(paste, "activate_window") as activate, no_print() as p:
            ok = paste.send_text(cfg_with(TARGET), "你好", Image.new("RGB", (3, 4)), dry_run=True)
        self.assertTrue(ok)
        find.assert_not_called()
        activate.assert_not_called()
        printed = " ".join(str(a) for c in p.call_args_list for a in c.args)
        self.assertIn("[dry-run]", printed)
        self.assertIn("3x4", printed)


class SendPaths(unittest.TestCase):
    def test_text_path_a_pastes_and_enters(self):
        with FakeModules() as f, \
                mock.patch.object(paste, "find_target_window", return_value=4321), \
                mock.patch.object(paste, "window_info", return_value=("Claude", "claude.exe")), \
                mock.patch.object(paste, "activate_window", return_value=True), \
                mock.patch.object(paste, "click_point") as click_point, \
                no_print() as p:
            ok = paste.send_text(cfg_with(TARGET), "你好")
        self.assertTrue(ok)
        click_point.assert_not_called()
        self.assertIn(("hotkey", "ctrl", "v"), f.gui.calls)
        self.assertIn(("press", "enter"), f.gui.calls)
        self.assertEqual(f.clip.text, "原来的剪贴板")  # 剪贴板还原了
        printed = " ".join(str(a) for c in p.call_args_list for a in c.args)
        self.assertIn("未校验", printed)
        self.assertNotIn("已发送", printed)

    def test_fallback_clicks_calibrated_point_then_pastes(self):
        target = dict(TARGET, click_rel_x=20, click_rel_y=30)
        with FakeModules() as f, \
                mock.patch.object(paste, "find_target_window", return_value=4321), \
                mock.patch.object(paste, "window_info", return_value=("Claude", "claude.exe")), \
                mock.patch.object(paste, "activate_window", return_value=False), \
                mock.patch.object(paste, "click_point", return_value=(120, 230)), \
                mock.patch.object(paste, "window_at_point", return_value=4321), \
                mock.patch.object(paste, "is_foreground", return_value=True), \
                no_print() as p:
            ok = paste.send_text(cfg_with(target), "你好")
        self.assertTrue(ok)
        self.assertIn(("click", 120, 230), f.gui.calls)
        self.assertIn(("hotkey", "ctrl", "v"), f.gui.calls)
        printed = " ".join(str(a) for c in p.call_args_list for a in c.args)
        self.assertIn("未校验", printed)

    def test_fallback_aborts_when_point_covered(self):
        target = dict(TARGET, click_rel_x=20, click_rel_y=30)
        with FakeModules() as f, \
                mock.patch.object(paste, "find_target_window", return_value=4321), \
                mock.patch.object(paste, "window_info", return_value=("Claude", "claude.exe")), \
                mock.patch.object(paste, "activate_window", return_value=False), \
                mock.patch.object(paste, "click_point", return_value=(120, 230)), \
                mock.patch.object(paste, "window_at_point", return_value=9999), \
                no_print() as p:
            ok = paste.send_text(cfg_with(target), "你好")
        self.assertFalse(ok)
        self.assertEqual([c for c in f.gui.calls if c[0] in ("click", "hotkey", "press")], [])
        printed = " ".join(str(a) for c in p.call_args_list for a in c.args)
        self.assertIn("盖住", printed)

    def test_image_is_a_second_message(self):
        image = Image.new("RGB", (2, 2))
        with FakeModules() as f, \
                mock.patch.object(paste, "find_target_window", return_value=4321), \
                mock.patch.object(paste, "window_info", return_value=("Claude", "claude.exe")), \
                mock.patch.object(paste, "activate_window", return_value=True), \
                mock.patch.object(paste, "set_clipboard_image") as set_img, no_print():
            ok = paste.send_text(cfg_with(TARGET), "文字", image)
        self.assertTrue(ok)
        set_img.assert_called_once_with(image)
        self.assertEqual([c for c in f.gui.calls if c[0] == "hotkey"], [("hotkey", "ctrl", "v")] * 2)
        self.assertEqual([c for c in f.gui.calls if c[0] == "press"], [("press", "enter")] * 2)


class WindowAndCalibration(unittest.TestCase):
    def test_find_prefers_old_hwnd_and_falls_back_to_single_exe_window(self):
        windows = [(1, "别的", "other.exe"), (2, "Claude", "claude.exe")]
        with mock.patch.object(target, "list_windows", return_value=windows):
            self.assertEqual(target.find_target_window(TARGET), 2)
        # 标题变了 (浏览器换标签页), 程序只有一个窗口 -> 仍然找到
        renamed = [(2, "Claude - 新对话", "claude.exe")]
        with mock.patch.object(target, "list_windows", return_value=renamed):
            self.assertEqual(target.find_target_window(TARGET), 2)
        # 程序对不上 -> 找不到
        with mock.patch.object(target, "list_windows", return_value=[(9, "Claude", "chrome.exe")]):
            self.assertIsNone(target.find_target_window(TARGET))

    def test_calibrate_requires_marked_target(self):
        ok, msg = target.calibrate_click({"target_window": None}, 10, 20)
        self.assertFalse(ok)
        self.assertIn("--mark-target", msg)

    def test_calibrate_rejects_point_outside_client_rect(self):
        with mock.patch.object(target, "find_target_window", return_value=4321), \
                mock.patch.object(target, "client_rect", return_value=[100, 200, 900, 800]):
            ok, msg = target.calibrate_click(cfg_with(TARGET), 5, 5)
        self.assertFalse(ok)
        self.assertIn("不在标记窗口", msg)


if __name__ == "__main__":
    unittest.main()
