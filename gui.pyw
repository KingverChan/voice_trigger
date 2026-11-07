# -*- coding: utf-8 -*-
"""
发发 (voice-trigger) 控制面板
只负责: 启动/停止后台进程、开机自启开关、改 config.json、看最近日志。
识别逻辑都在 main.py 里, 这里不重复实现。
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
LOG = HERE / "voice-trigger.log"
STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup\voice-trigger.lnk"
MUTEX_NAME = "voice-trigger-fafa"
AUTO_FLAG = HERE / "AUTO_SEND_ON"  # 有这个文件 = 自动发送开 (main.py 每 0.3 秒看一次, 不用重启)
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


# ---------- 后台进程 ----------
def is_running():
    """main.py 运行时会占用一个命名互斥量, 能打开说明在运行。"""
    try:
        h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, MUTEX_NAME)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def run_ps1(name):
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(HERE / name)], creationflags=NO_WINDOW)


def start_bg():
    subprocess.Popen(["wscript", str(HERE / "start_hidden.vbs")], cwd=str(HERE))


def stop_bg():
    run_ps1("stop.ps1")


# ---------- 配置 ----------
def read_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_config(changes):
    """先读最新的 config.json, 只改界面上的这几项, 其他字段原样保留。"""
    cfg = read_config()
    cfg.update(changes)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def list_mics():
    try:
        import sounddevice as sd
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                n = d["name"]
                if any(h in n.lower() for h in ("立体声混音", "stereo mix", "loopback")):
                    continue
                out.append((i, n))
        return out
    except Exception:
        return []


def split_words(s):
    return [w.strip() for w in s.replace("，", ",").split(",") if w.strip()]


# ---------- 界面 ----------
class App:
    def __init__(self, root):
        self.root = root
        root.title("发发 · 语音唤醒")
        root.geometry("700x980")
        root.minsize(480, 560)
        pad = {"padx": 8, "pady": 4}

        # 状态 + 开关
        top = ttk.LabelFrame(root, text="运行")
        top.pack(fill="x", **pad)
        self.status = tk.StringVar(value="检测中...")
        self.status_lbl = tk.Label(top, textvariable=self.status, font=("Microsoft YaHei UI", 12, "bold"))
        self.status_lbl.grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.toggle_btn = ttk.Button(top, text="启动", command=self.toggle)
        self.toggle_btn.grid(row=0, column=1, padx=4)
        ttk.Button(top, text="重启", command=self.restart).grid(row=0, column=2, padx=4)
        self.autostart = tk.BooleanVar(value=STARTUP_LNK.exists())
        ttk.Checkbutton(top, text="开机自动启动", variable=self.autostart,
                        command=self.toggle_autostart).grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.auto_var = tk.StringVar()
        self.auto_lbl = tk.Label(top, textvariable=self.auto_var, font=("Microsoft YaHei UI", 11, "bold"))
        # 自动发送横幅不显示了 (用热键开关)
        self.auto_btn = ttk.Button(top, text="开/关自动发送", command=self.toggle_auto)
        # self.auto_btn 也不显示
        top.columnconfigure(0, weight=1)

        # 设置
        cfg = read_config()
        box = ttk.LabelFrame(root, text="设置 (改完点「保存」, 运行中会自动重启生效)")
        box.pack(fill="x", **pad)
        r = 0

        # 这些行在面板上不显示 (功能和设置照旧, 要改直接改 config.json; 想显示回来就从这里删掉那一项)
        HIDDEN = {"自动发送用 Smart Turn", "用 DeepSeek 辅助判断说完没有", "判断时带上对话上下文",
                  "读取 AI 回复", "朗读按钮", "朗读跳过这些行", "自动发送只发对 AI 说的话",
                  "按窗口自动选择发送方式", "听写窗口 (直接打字)", "刚才用的窗口", "说话时带上选中文字",
                  "粘贴后自动回车", "附带复制的文字/图片", "识别语言"}

        def row(label, widget, tip=""):
            nonlocal r
            if label in HIDDEN:
                return
            ttk.Label(box, text=label).grid(row=r, column=0, sticky="w", padx=8, pady=3)
            widget.grid(row=r, column=1, sticky="we", padx=8, pady=3)
            if tip:
                ttk.Label(box, text=tip, foreground="#888").grid(row=r, column=2, sticky="w")
            r += 1

        # 唤醒词 / 触发方式 / 断句 VAD 不在面板里显示了, 要改直接改 config.json
        self.st = tk.BooleanVar(value=cfg.get("smart_turn", False))
        row("自动发送用 Smart Turn", ttk.Checkbutton(box, variable=self.st), "听语气判断说完没 (实验)")

        self.aturn = tk.BooleanVar(value=cfg.get("ai_turn", False))
        row("用 DeepSeek 辅助判断说完没有", ttk.Checkbutton(box, variable=self.aturn), "Smart Turn 拿不准时问 (要 key)")
        self.actx = tk.BooleanVar(value=cfg.get("ai_context", True))
        row("判断时带上对话上下文", ttk.Checkbutton(box, variable=self.actx), "刚跟 AI 说过话, 追问更容易判为对 AI 说")
        self.probe_msg = tk.StringVar(value="没测过")
        f3 = ttk.Frame(box)
        self.probe_btn = ttk.Button(f3, text="安装并测试读取 AI 回复", command=self.probe_reply)
        self.probe_btn.pack(side="left")
        ttk.Label(f3, textvariable=self.probe_msg, wraplength=380, foreground="#555").pack(side="left", padx=6)
        row("读取 AI 回复", f3)
        self.tts = tk.BooleanVar(value=cfg.get("tts", False))
        self.tts_en = tk.BooleanVar(value=cfg.get("tts_only_english", False))
        f4 = ttk.Frame(box)
        ttk.Checkbutton(f4, text="开", variable=self.tts).pack(side="left")
        self.rate = tk.StringVar(value=cfg.get("tts_rate", "+0%"))
        ttk.Label(f4, text="语速").pack(side="left")
        ttk.Combobox(f4, textvariable=self.rate, width=6, state="readonly",
                     values=["-30%", "-20%", "-10%", "+0%", "+10%", "+20%", "+40%"]).pack(side="left", padx=2)
        self.voice = tk.StringVar(value=cfg.get("tts_voice_en", "en-US-AriaNeural"))
        ttk.Combobox(f4, textvariable=self.voice, width=20, state="readonly",
                     values=["en-US-AriaNeural", "en-US-JennyNeural", "en-US-GuyNeural",
                             "en-GB-SoniaNeural", "en-GB-RyanNeural", "en-AU-NatashaNeural",
                             "en-AU-WilliamNeural"]).pack(side="left", padx=2)
        row("念出 AI 回复", f4)

        def tts_live(*_):  # 朗读的开关/语速/声音一改就存, 后台下一次念就用 (不用点保存, 不重启)
            try:
                write_config({"tts": bool(self.tts.get()), "tts_rate": self.rate.get(),
                              "tts_voice_en": self.voice.get()})
                self._tts_seen = bool(self.tts.get())
                self.status.set(f"朗读已改: {'开' if self.tts.get() else '关'}, 语速 {self.rate.get()} (下一次念生效)")
            except Exception:
                pass
        for w in f4.winfo_children():
            if isinstance(w, ttk.Combobox):
                w.bind("<<ComboboxSelected>>", tts_live)
            elif isinstance(w, ttk.Checkbutton):
                w.configure(command=tts_live)
        f5 = ttk.Frame(box)
        self.tts_btn = ttk.Button(f5, text="安装并试听", command=self.tts_test)
        self.tts_btn.pack(side="left")
        ttk.Button(f5, text="再念一遍", command=lambda: self.tts_cmd("repeat")).pack(side="left", padx=2)
        ttk.Button(f5, text="停止念", command=lambda: self.tts_cmd("stop")).pack(side="left", padx=2)
        self.tts_msg = tk.StringVar(value="热键: Ctrl+Alt+R 再念, Ctrl+Alt+S 停止")
        ttk.Label(f5, textvariable=self.tts_msg, foreground="#555", wraplength=260).pack(side="left", padx=6)
        row("朗读按钮", f5)
        self.skipw = tk.StringVar(value=", ".join(cfg.get("tts_skip_words", [
            "Claude", "You", "你", "Copy", "Retry", "Edit", "Share", "复制", "重试", "编辑", "分享",
            "Thought for", "Thinking", "思考", "已思考", "Searched", "Claude can make mistakes",
            "Claude 可能会出错", "Reply to Claude", "回复 Claude", "【选中的内容】", "【附带的复制内容】", "已发送"])))
        row("朗读跳过这些行", ttk.Entry(box, textvariable=self.skipw, width=40), "逗号隔开")
        self.aif = tk.BooleanVar(value=cfg.get("ai_filter", False))
        row("自动发送只发对 AI 说的话", ttk.Checkbutton(box, variable=self.aif), "每句先问 DeepSeek (要 key)")
        self.key = tk.StringVar(value=cfg.get("ai_filter_api_key", ""))
        row("DeepSeek key", ttk.Entry(box, textvariable=self.key, show="•"), "只存在本机 config.json")
        self.pcall = tk.BooleanVar(value=cfg.get("pause_on_call", False))
        row("通话软件用麦克风时暂停", ttk.Checkbutton(box, variable=self.pcall), "Teams/Zoom/微信等")

        self.route = tk.BooleanVar(value=cfg.get("window_routing", False))
        row("按窗口自动选择发送方式", ttk.Checkbutton(box, variable=self.route), "Word/微信=直接打字, AI 窗口=发送")
        self.dic = tk.StringVar(value=", ".join(cfg.get("dictation_windows", [
            "winword.exe", "wechat.exe", "weixin.exe", "typora", "code.exe", "obsidian", "notepad"])))
        row("听写窗口 (直接打字)", ttk.Entry(box, textvariable=self.dic), "程序名或标题关键词")
        self.aiw = tk.StringVar(value=", ".join(cfg.get("ai_windows", ["claude", "豆包", "deepseek", "chatgpt"])))
        row("AI 窗口 (点聊天框发送)", ttk.Entry(box, textvariable=self.aiw), "其他窗口不发")
        self.fg = tk.StringVar(value="(切到别的窗口再切回来就会显示)")
        self.last_fg = ("", "")
        f2 = ttk.Frame(box)
        ttk.Label(f2, textvariable=self.fg, width=34).pack(side="left")
        ttk.Button(f2, text="加入听写", command=lambda: self.add_window(self.dic)).pack(side="left", padx=2)
        ttk.Button(f2, text="加入 AI", command=lambda: self.add_window(self.aiw)).pack(side="left", padx=2)
        row("刚才用的窗口", f2)
        self.sel = tk.BooleanVar(value=cfg.get("attach_selection", False))
        row("说话时带上选中文字", ttk.Checkbutton(box, variable=self.sel), "框选的文字/代码一起发给 AI")

        self.mics = list_mics()
        names = ["系统默认麦克风"] + [f"{i}: {n}" for i, n in self.mics]
        self.mic = tk.StringVar()
        cur = cfg.get("mic_device")
        self.mic.set(next((f"{i}: {n}" for i, n in self.mics if i == cur), names[0]))
        row("麦克风", ttk.Combobox(box, textvariable=self.mic, values=names, state="readonly"))

        self.delay = tk.DoubleVar(value=round(cfg.get("smart_turn_check_ms", 500) / 1000, 1))
        row("停顿多久开始判断说完", ttk.Spinbox(box, from_=0.2, to=2.5, increment=0.1, textvariable=self.delay, width=8),
            "秒 (自动发送; 越大越不容易把一句话切断)")

        self.enter = tk.BooleanVar(value=cfg.get("press_enter", True))
        row("粘贴后自动回车", ttk.Checkbutton(box, variable=self.enter))

        self.attach = tk.BooleanVar(value=cfg.get("attach_clipboard", True))
        row("附带复制的文字/图片", ttk.Checkbutton(box, variable=self.attach))

        self.window = tk.DoubleVar(value=cfg.get("attach_window_s", 10))
        row("复制后几秒内说话才附带", ttk.Spinbox(box, from_=1, to=120, increment=1, textvariable=self.window, width=8), "秒")

        self.img_wait = tk.DoubleVar(value=cfg.get("image_wait_s", 1.5))
        row("贴图后等几秒再回车", ttk.Spinbox(box, from_=0.5, to=10, increment=0.5, textvariable=self.img_wait, width=8), "秒")

        self.energy = tk.DoubleVar(value=cfg.get("energy_ratio", 2.5))
        row("抗背景音乐强度", ttk.Spinbox(box, from_=1.5, to=6, increment=0.5, textvariable=self.energy, width=8), "越大越不容易被音乐触发")

        self.langs = {"auto": "自动 (中文/英文)", "zh": "中文", "en": "英文 (英语练习)"}
        self.lang = tk.StringVar(value=self.langs.get(cfg.get("language", "auto"), self.langs["auto"]))
        row("识别语言", ttk.Combobox(box, textvariable=self.lang, values=list(self.langs.values()),
                                   state="readonly", width=16), "说英语写成中文时选自动或英文")

        self.model = tk.StringVar(value=cfg.get("model_size", "small"))
        row("识别模型", ttk.Combobox(box, textvariable=self.model, values=["base", "small", "medium"],
                                    state="readonly", width=10), "medium 更准但更慢")

        box_pos = cfg.get("input_box")
        self.pos = tk.StringVar(value=f"{box_pos[0]}, {box_pos[1]}" if box_pos else "还没记录")
        f = ttk.Frame(box)
        ttk.Label(f, textvariable=self.pos).pack(side="left")
        ttk.Button(f, text="重新记录", command=self.calibrate).pack(side="left", padx=8)
        row("聊天输入框位置", f)
        box.columnconfigure(1, weight=1)

        ttk.Button(box, text="保存", command=self.save).grid(row=r, column=1, sticky="e", padx=8, pady=6)

        # 日志
        logf = ttk.LabelFrame(root, text="最近识别记录 (voice-trigger.log)")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=5, wrap="word", font=("Microsoft YaHei UI", 9))
        self.log.pack(fill="both", expand=True, padx=4, pady=4)
        self.log.configure(state="disabled")
        self._log_mtime = 0

        self.refresh()

    # --- 动作 ---
    def busy(self, fn, then=None):
        """在后台线程里执行(启动/停止要几秒), 界面不卡。"""
        self.toggle_btn.configure(state="disabled")

        def work():
            fn()
            time.sleep(1.5)
            self.root.after(0, lambda: (self.toggle_btn.configure(state="normal"), then and then()))
        threading.Thread(target=work, daemon=True).start()

    def toggle(self):
        if is_running():
            self.status.set("正在停止...")
            self.busy(stop_bg)
        else:
            self.status.set("正在启动... (加载模型约半分钟)")
            self.busy(start_bg)

    def restart(self):
        self.status.set("正在重启...")
        self.busy(lambda: (stop_bg(), time.sleep(1), start_bg()))

    def toggle_auto(self):
        try:
            if AUTO_FLAG.exists():
                AUTO_FLAG.unlink()
            else:
                AUTO_FLAG.write_text("on", encoding="utf-8")
        except OSError as e:
            messagebox.showwarning("发发", f"切换失败: {e}")
        self.show_auto()

    def show_auto(self):
        hk = read_config().get("auto_hotkey", "ctrl+alt+f") or "无"
        if AUTO_FLAG.exists():
            self.auto_var.set(f"⚡ 自动发送: 开 (说完就发, 所有话都会发出去)   热键 {hk}")
            self.auto_lbl.configure(fg="#b45309")
        else:
            self.auto_var.set(f"自动发送: 关 (要说发发)   热键 {hk}")
            self.auto_lbl.configure(fg="#555")

    def add_window(self, var):
        """把刚才用的那个窗口加进列表: 普通软件加程序名; 浏览器加网页标题里最后一段 (比如 Claude)"""
        title, exe = self.last_fg
        if not exe and not title:
            messagebox.showinfo("发发", "先切到那个窗口用一下, 再切回面板点这个按钮")
            return
        browsers = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe")
        if exe.lower() in browsers or not exe:
            t = title.rsplit(" - ", 1)[0] if " - " in title else title
            word = t.rsplit(" - ", 1)[-1].strip() or title
        else:
            word = exe.lower()
        words = split_words(var.get())
        if word.lower() not in [w.lower() for w in words]:
            words.append(word)
        var.set(", ".join(words))
        self.status.set(f"已加入「{word}」, 记得点保存")

    def probe_reply(self):
        """后台: 没装 uiautomation 就装上, 倒数 3 秒, 试读聊天框所在窗口的文字, 结果显示在面板上。"""
        py = str(Path(sys.executable).with_name("python.exe"))
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")  # 中文输出不出错
        self.probe_btn.configure(state="disabled")

        def say(msg):
            self.root.after(0, lambda: self.probe_msg.set(msg))

        def work():
            try:
                ok = subprocess.run([py, "-c", "import uiautomation"], cwd=str(HERE),
                                    capture_output=True, env=env, creationflags=NO_WINDOW).returncode == 0
                if not ok:
                    say("正在安装 uiautomation (第一次要等一会)...")
                    p = subprocess.Popen([py, "-m", "pip", "install", "uiautomation"], cwd=str(HERE),
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace",
                                         env=env, creationflags=NO_WINDOW)
                    for line in p.stdout:
                        line = line.strip()
                        if line:
                            say("安装中: " + line[:60])
                    if p.wait() != 0:
                        say("安装失败, 把这一行截图发给 Claude")
                        return
                for i in (3, 2, 1):
                    say(f"{i} 秒后读取, 让 AI 聊天窗口露出来 (别挡住输入框)...")
                    time.sleep(1)
                say("正在读取...")
                r = subprocess.run([py, str(HERE / "main.py"), "--probe-reply"], cwd=str(HERE),
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env=env, creationflags=NO_WINDOW, timeout=60)
                res = "\n".join(l for l in r.stdout.splitlines()
                                if l.startswith(("结果", "开头", "结尾")))
                err = (r.stderr.strip().splitlines() or ["未知错误"])[-1]
                say(res.replace("结果: ", "") or ("出错了: " + err[:300]))
            except Exception as e:
                say(f"出错了: {type(e).__name__}: {e}"[:300])
            finally:
                self.root.after(0, lambda: self.probe_btn.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    def tts_cmd(self, c):
        """告诉后台: 再念一遍 / 停止 (后台每 0.3 秒看一次这个文件)"""
        if not is_running():
            self.tts_msg.set("后台没在运行")
            return
        try:
            (HERE / "TTS_CMD").write_text(c, encoding="utf-8")
            self.tts_msg.set("已让后台" + ("再念一遍" if c == "repeat" else "停止朗读"))
        except OSError as e:
            self.tts_msg.set(f"出错了: {e}")

    def tts_test(self):
        """没装 edge-tts 就装上, 然后用选的声音和语速念一句英文"""
        py = str(Path(sys.executable).with_name("python.exe"))
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        write_config({"tts_rate": self.rate.get(), "tts_voice_en": self.voice.get()})
        self.tts_btn.configure(state="disabled")

        def say(msg):
            self.root.after(0, lambda: self.tts_msg.set(msg))

        def work():
            try:
                ok = subprocess.run([py, "-c", "import edge_tts"], cwd=str(HERE), capture_output=True,
                                    env=env, creationflags=NO_WINDOW).returncode == 0
                if not ok:
                    say("正在安装 edge-tts...")
                    p = subprocess.Popen([py, "-m", "pip", "install", "edge-tts"], cwd=str(HERE),
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace", env=env,
                                         creationflags=NO_WINDOW)
                    for line in p.stdout:
                        if line.strip():
                            say("安装中: " + line.strip()[:50])
                    if p.wait() != 0:
                        say("安装失败, 截图发给 Claude")
                        return
                say("正在试听...")
                r = subprocess.run([py, str(HERE / "main.py"), "--tts-test",
                                    "Hi Siyun, this is how I will read the AI replies to you."],
                                   cwd=str(HERE), capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", env=env, creationflags=NO_WINDOW, timeout=120)
                errs = [l for l in r.stdout.splitlines() if "出错" in l or "没装" in l]
                if errs:
                    say(errs[-1][:200])
                elif r.returncode != 0:
                    say("出错了: " + ((r.stderr.strip().splitlines() or ["未知错误"])[-1])[:200])
                else:
                    say("试听完了, 听到声音就说明可以用")
            except Exception as e:
                say(f"出错了: {type(e).__name__}: {e}"[:200])
            finally:
                self.root.after(0, lambda: self.tts_btn.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    def toggle_autostart(self):
        run_ps1("autostart_on.ps1" if self.autostart.get() else "autostart_off.ps1")
        self.autostart.set(STARTUP_LNK.exists())

    def calibrate(self):
        import pyautogui
        win = tk.Toplevel(self.root)
        win.attributes("-topmost", True)
        win.title("记录输入框位置")
        msg = tk.StringVar()
        tk.Label(win, textvariable=msg, font=("Microsoft YaHei UI", 12), padx=20, pady=20).pack()

        def tick(n):
            if n > 0:
                msg.set(f"把鼠标移到聊天输入框上, 别动\n{n} 秒后记录")
                win.after(1000, tick, n - 1)
            else:
                x, y = pyautogui.position()
                write_config({"input_box": [int(x), int(y)]})
                self.pos.set(f"{x}, {y}")
                win.destroy()
                self.ask_restart("输入框位置已保存")
        tick(5)

    def save(self):
        try:
            mic = self.mic.get()
            mic_dev = int(mic.split(":")[0]) if mic[0].isdigit() else None
            before = read_config()
            write_config({
                "mic_device": mic_dev,
                "smart_turn_check_ms": int(round(float(self.delay.get()) * 1000)),
                "smart_turn_max_wait_ms": max(int(read_config().get("smart_turn_max_wait_ms", 3000)),
                                              int(round(float(self.delay.get()) * 1000)) + 1000),
                "press_enter": bool(self.enter.get()),
                "attach_clipboard": bool(self.attach.get()),
                "attach_window_s": float(self.window.get()),
                "image_wait_s": float(self.img_wait.get()),
                "energy_ratio": float(self.energy.get()),
                "model_size": self.model.get(),
                "language": next(k for k, v in self.langs.items() if v == self.lang.get()),
                "smart_turn": bool(self.st.get()),
                "tts": bool(self.tts.get()),
                "tts_only_english": False,
                "tts_rate": self.rate.get(),
                "tts_voice_en": self.voice.get(),
                "tts_skip_words": split_words(self.skipw.get()),
                "ai_context": bool(self.actx.get()),
                "window_routing": bool(self.route.get()),
                "dictation_windows": split_words(self.dic.get()),
                "ai_windows": split_words(self.aiw.get()),
                "attach_selection": bool(self.sel.get()),
                "ai_turn": bool(self.aturn.get()),
                "ai_filter": bool(self.aif.get()),
                "ai_filter_api_key": self.key.get().strip(),
                "pause_on_call": bool(self.pcall.get()),
            })
        except (ValueError, tk.TclError):
            messagebox.showwarning("发发", "有数字没填对, 检查一下")
            return
        after = read_config()
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        if not changed:
            self.status.set("已保存 (没有改动)")
        elif all(k == "tts" or (k.startswith("tts_") and not k.endswith("hotkey")) for k in changed):
            self.status.set("已保存, 朗读设置下一次念就生效 (不用重启)")
        else:
            self.ask_restart("已保存")

    def ask_restart(self, msg):
        if is_running():
            self.status.set(f"{msg}, 正在重启生效...")
            self.busy(lambda: (stop_bg(), time.sleep(1), start_bg()))
        else:
            self.status.set(f"{msg} (下次启动生效)")

    # --- 定时刷新 ---
    def refresh(self):
        try:  # 「我的工具」里切换了朗读开关, 面板上的勾也跟着变
            on = bool(read_config().get("tts", False))
            if on != getattr(self, "_tts_seen", on):
                self.tts.set(on)
            self._tts_seen = on
        except Exception:
            pass
        try:
            from main import foreground_window
            t, e = foreground_window()
            if e and "发发" not in t and e.lower() not in ("pythonw.exe", "python.exe"):
                self.last_fg = (t, e)
                self.fg.set(f"{t[:22]} ({e})")
        except Exception:
            pass
        self.show_auto()
        if str(self.toggle_btn.cget("state")) != "disabled":
            if is_running():
                self.status.set("● 运行中 (正在听)")
                self.status_lbl.configure(fg="#1a7f37")
                self.toggle_btn.configure(text="停止")
            else:
                self.status.set("○ 已停止")
                self.status_lbl.configure(fg="#b42318")
                self.toggle_btn.configure(text="启动")
        try:
            m = LOG.stat().st_mtime
            if m != self._log_mtime:
                self._log_mtime = m
                with open(LOG, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()[-40:]
                self.log.configure(state="normal")
                self.log.delete("1.0", "end")
                self.log.insert("end", "".join(lines))
                self.log.see("end")
                self.log.configure(state="disabled")
        except OSError:
            pass
        self.root.after(1500, self.refresh)


def already_open():
    """面板只开一个: 已经开着就把那个窗口拿到最前面, 返回 True"""
    try:
        k = ctypes.windll.kernel32
        k.SetLastError(0)
        already_open.mutex = k.CreateMutexW(None, False, "Local\\fafa-voice-trigger-gui")
        if k.GetLastError() != 183:   # 183 = 已经存在
            return False
        u = ctypes.windll.user32
        h = u.FindWindowW(None, "发发 · 语音唤醒")
        if h:
            u.ShowWindow(h, 9)            # SW_RESTORE (最小化了也恢复)
            u.keybd_event(0x12, 0, 0, 0)  # 按一下 Alt, Windows 才允许把别的窗口拿到前台
            u.SetForegroundWindow(h)
            u.keybd_event(0x12, 0, 2, 0)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if already_open():
        sys.exit(0)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 高分屏不模糊
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
