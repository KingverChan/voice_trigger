# -*- coding: utf-8 -*-
"""
voice-trigger 最小版
麦克风持续监听 -> VAD 切句 (默认 Silero, 可退回 webrtc) -> 本地 faster-whisper 转文字
-> 句子里有唤醒词(fafa 及别名)时, 把后面的话粘贴到聊天输入框并回车。
录音只在内存里, 不存文件。

用法:
  python main.py --calibrate     第一次: 记录聊天输入框坐标
  python main.py                 开始监听 (Ctrl+C 退出)
  python main.py --list-mics     查看麦克风编号
  python main.py --dry-run       只打印, 不点击不粘贴 (调唤醒词用)
  python main.py --test-file a.wav [--mode both] [--vad silero]
                                 用录音文件测试切句+识别+唤醒 (只打印)

实现已经按职责拆到 voice_trigger/ 包里, 这个文件只保留原来的文件名和命令行入口。
"""
# 兼容旧代码: 以前 from main import xxx 能拿到的名字, 现在仍然能拿到
from voice_trigger.ai import (AI_PROMPT, AI_TURN_PROMPT, AI_USAGE, ai_is_for_ai,
                              ask_deepseek, unfinished_reason)
from voice_trigger.asr import load_whisper
from voice_trigger.audio.gpu import add_nvidia_dlls
from voice_trigger.audio.mic import (LOOPBACK_HINTS, check_mic, file_frames,
                                     list_mics, mic_frames, mic_users)
from voice_trigger.audio.vad import SileroVAD, SmartTurn, speech_events
from voice_trigger.cli import calibrate, main
from voice_trigger.config import (CONFIG_PATH, DEFAULT_CONFIG, load_config,
                                  normalize, save_config)
from voice_trigger.constants import FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE
from voice_trigger.input.autoflag import AUTO_FLAG, auto_on, set_auto
from voice_trigger.input.clipboard import (ClipWatch, read_clipboard,
                                           set_clipboard_image, with_clipboard)
from voice_trigger.input.hotkey import VK_NAMES, beep, start_hotkey
from voice_trigger.input.paste import paste_here, read_selection, send_text
from voice_trigger.journal import (LOG_PATH, TimeStampWriter, log_speech,
                                   mark_pasting, setup_log)
from voice_trigger.paths import TTS_CMD
from voice_trigger.reply import read_ai_reply, watch_reply, watch_reply_file
from voice_trigger.runtime import bench, run, single_instance
from voice_trigger.tts import (TTS_DIR, EdgeWS, Speaker, clean_for_tts,
                               drop_repeats, edge_conn, skip_ui_lines,
                               split_chunks, tts_enabled, tts_lang)
from voice_trigger.wake import find_command, find_wake
from voice_trigger.windows import (LAST_FG, ROUTE_NAME, foreground_window,
                                   is_panel, remember_foreground, route_for,
                                   window_info)

if __name__ == "__main__":
    main()
