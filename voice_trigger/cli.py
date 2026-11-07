# -*- coding: utf-8 -*-
"""命令行参数解析与分发 (main.py 只是薄入口)。"""
import argparse
import sys
import time

from .asr import load_whisper
from .audio.gpu import add_nvidia_dlls
from .audio.mic import list_mics
from .config import load_config, save_config
from .journal import setup_log
from .reply import read_ai_reply
from .runtime import bench, run, single_instance
from .target import calibrate_click, describe_target, find_target_window, mark_target
from .tts import Speaker, tts_lang


def calibrate(cfg):
    """标定兜底点击点: 鼠标当前位置 -> 标记窗口客户区的相对坐标。"""
    import pyautogui
    if not cfg.get("target_window"):
        print("还没标记目标窗口, 先运行: python main.py --mark-target")
        return
    hwnd = find_target_window(cfg["target_window"])
    if not hwnd:
        print("标记的窗口不见了, 请重新运行: python main.py --mark-target")
        return
    print(f"目标窗口: {describe_target(cfg['target_window'])}")
    print("只在激活失败时用它兜底。把鼠标移到这个窗口的输入框上, 5 秒后记录位置...")
    for i in range(5, 0, -1):
        print(f"  {i} ...  当前位置 {tuple(pyautogui.position())}")
        time.sleep(1)
    x, y = pyautogui.position()
    ok, msg = calibrate_click(cfg, x, y)
    print(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mark-target", action="store_true",
                    help="把当前前台窗口标记为发送目标 (先点一下那个对话框再运行)")
    ap.add_argument("--calibrate", action="store_true",
                    help="标定兜底点击点 (标记窗口激活失败时点输入框, 先 --mark-target)")
    ap.add_argument("--list-mics", "--list-devices", action="store_true", help="列出麦克风")
    ap.add_argument("--dry-run", action="store_true", help="只打印不发送")
    ap.add_argument("--log", action="store_true", help="输出写到 voice-trigger.log (后台运行用)")
    ap.add_argument("--bench", action="store_true", help="测 small/medium 在 CPU/显卡上转一句话要几秒")
    ap.add_argument("--probe-reply", action="store_true", help="试试能不能读到 AI 窗口里的回复")
    ap.add_argument("--tts-test", help="试听: 念一句话")
    ap.add_argument("--gpu-check", action="store_true", help="测试显卡加速能不能用")
    ap.add_argument("--test-file", help="用 16kHz 单声道 wav 代替麦克风测试 (只打印不发送)")
    ap.add_argument("--mode", choices=["prefix", "suffix", "both", "auto"], help="临时指定触发方式")
    ap.add_argument("--vad", choices=["silero", "webrtc"], help="临时指定 VAD")
    ap.add_argument("--smart-turn", action="store_true", help="临时打开 Smart Turn")
    args = ap.parse_args()
    if args.log or sys.stdout is None:
        setup_log()

    cfg = load_config()
    if args.mode:
        cfg["trigger_mode"] = args.mode
    if args.vad:
        cfg["vad_engine"] = args.vad
    if args.smart_turn:
        cfg["smart_turn"] = True
    if args.list_mics:
        list_mics()
    elif args.mark_target:
        # 在终端里运行时前台是终端, 所以留 3 秒让你点到目标对话框上
        print("3 秒内点一下要发送到的那个对话框...")
        for i in (3, 2, 1):
            print(f"  {i} ...")
            time.sleep(1)
        sys.exit(0 if mark_target(cfg) else 1)
    elif args.calibrate:
        calibrate(cfg)
    elif args.test_file:
        run(cfg, True, args.test_file)  # 测试不占"只能开一个"的名额
    else:
        if args.bench:
            bench(cfg)
            return
        if args.tts_test:
            sp = Speaker(cfg)
            print(f"结果: 试听 ({'中文' if tts_lang(args.tts_test) == 'zh' else '英文'})")
            sp.say(args.tts_test)
            time.sleep(0.5)
            while sp.active:
                time.sleep(0.2)
            return
        if args.probe_reply:
            reply, why = read_ai_reply(cfg, 800, time_limit=5)
            print(f"结果: {why}")
            if reply:
                flat = " ".join(reply.split())
                print(f"开头: {flat[:60]}…")
                print(f"结尾: …{flat[-60:]}")
            return
        if args.gpu_check:
            add_nvidia_dlls()
            import ctranslate2
            print(f"ctranslate2 看到的 NVIDIA 显卡数量: {ctranslate2.get_cuda_device_count()}")
            m = load_whisper(dict(cfg, device="cuda"))
            if getattr(m.model, "device", "cpu") == "cuda":
                cfg["device"] = "auto"
                save_config(cfg)
                print("成功! config.json 已改成 device = auto, 重启 voice-trigger 后生效")
            else:
                print("显卡加速没开成, 继续用 CPU (config 没改)")
                sys.exit(1)
            return
        if not single_instance():
            print("已经有一个 voice-trigger 在运行了, 这个退出。")
            return
        try:
            run(cfg, args.dry_run)
        except KeyboardInterrupt:
            print("\n已退出")
        except Exception:
            import traceback
            traceback.print_exc()
            raise


if __name__ == "__main__":
    main()
