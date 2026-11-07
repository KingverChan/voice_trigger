
## Silero VAD + 两种触发方式（2026-11-06 新增）

### 断句换成了 Silero VAD

以前用 webrtcvad 判断"是不是在说话"，现在默认用 **Silero VAD**（和 vad-lab 里学的是同一个神经网络模型），对键盘声、风扇声更不容易误判。

- 不用另外装东西：直接用 faster-whisper 自带的 `silero_vad_v6.onnx` 模型，用 onnxruntime 跑，每 32ms 一帧
- 流程：麦克风 → 音量门限（抗背景音乐，保留）→ Silero 给每帧打人声概率 → 状态机切成一句一句（连续 3 帧有声才算开口，安静 0.8 秒算说完，太短的丢掉）→ Whisper 转文字 → 找「发发」
- 日志里启动时会写 `VAD: Silero (神经网络)`；如果 Silero 加载失败会自动退回 `VAD: webrtc`
- 想换回原来的：`config.json` 里 `"vad_engine": "webrtc"`，或者在面板「断句 VAD」里选 webrtc

### 触发方式（config.json 的 `trigger_mode`，面板里也能选）

| 值 | 怎么说 | 例子 |
|---|---|---|
| `prefix` | 先说发发，再说指令（原来的方式） | 「发发，帮我查天气」 |
| `suffix` | 先说话，最后说发发 → 前面攒的话一起发 | 「今晚吃什么好……帮我查天气……发发」 |
| `both`（现在的设置） | 两种都行：发发在句首且后面有话 = prefix；发发前面有话或已经攒了话 = suffix | 上面两种都可以 |

suffix 的规则：
- 每句话识别出来先放在**内存**里（不存文件），日志里显示 `[缓冲] 已攒 N 句`
- 说「发发」时，把攒的话加上「发发」前面那半句，用逗号连成一条，等 2 秒发送（剪贴板附带、去重照旧）；发完清空
- 只攒最近 **60 秒**（`buffer_max_s`）；两句之间停顿超过 **8 秒**（`buffer_gap_s`）就清空重来，日志写「停顿超过 8 秒，清空」
- 结束词用「发发」不用单字「发」：「发」太常见（发现、发烧、发消息），会一直误触发

取舍：
- 好处：说话更自然，想好了再说，最后一个「发发」确认
- 风险：跟别人聊天时随口说了「发发」，前面 60 秒内的闲聊也会被发出去。60 秒 / 8 秒两个保护就是为了减少这种情况；经常误发就把 `buffer_gap_s` 调小（比如 4），或者改回 `prefix`
- `both` 模式下，如果已经攒了话，单独说一句「发发」会把攒的话发出去，而不是"等下一句当指令"

### 测试命令（不会真的发送）

```
.venv\Scripts\python.exe main.py --dry-run                      用麦克风试，只打印
.venv\Scripts\python.exe main.py --test-file a.wav --mode both  用录音文件试（16kHz 单声道 wav）
```

`--mode prefix/suffix/both` 和 `--vad silero/webrtc` 可以临时覆盖 config.json。

### 改完以后怎么生效

1. 双击 `stop.bat` 停掉正在跑的旧版本
2. 双击 `start_hidden.bat` 重新启动（或者在面板里点「重启」）
3. 等半分钟，打开 `voice-trigger.log`，看到 `VAD: Silero (神经网络)` 和 `触发方式: both` 就成功了

备份：`main.py.before-silero.bak`、`gui.pyw.before-silero.bak`（改坏了把它们改回原名就能恢复）。
