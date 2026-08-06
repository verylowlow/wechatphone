# -*- coding: utf-8 -*-
"""
wechatphone - 微信语音电话 AI 桥接 (最小闭环)

音频链路 (单条 VB-Cable, 物理隔离回声):
  微信对方声音 -> 微信扬声器(物理扬声器) --loopback捕获--> 本程序 --> 阿里云 Realtime
  阿里云 AI 语音 --> 本程序 --写入--> CABLE Input ==> CABLE Output --> 微信麦克风 --> 对方

  回声隔离原理: AI 声音只进 CABLE, 永远不上物理扬声器, 因此 loopback 捕获不到 AI 自己的声音。

微信端设置:
  麦克风 = CABLE Output (VB-Audio Virtual Cable)
  扬声器 = 本机扬声器 (物理设备, 你自己也能听到对方)

用法:
  python bridge.py --list                 # 列出所有音频设备
  python bridge.py                        # 自动识别设备并启动
  python bridge.py --capture-idx 16 --inject-idx 13   # 手动指定
"""
import argparse
import asyncio
import base64
import json
import os
import queue
import sys
import threading
import time

import numpy as np
try:
    import pyaudiowpatch as pyaudio  # 原生支持 WASAPI loopback
    _PW = True
except Exception:
    import pyaudio
    _PW = False
import websockets

# ---------------- 配置 ----------------
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()

API_KEY = os.environ.get("ALIYUN_REALTIME_API_KEY", "")
BASE_URL = os.environ.get(
    "ALIYUN_REALTIME_BASE_URL",
    "https://llm-eef79bxxd42lvkdz.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)
MODEL = os.environ.get("ALIYUN_REALTIME_MODEL", "qwen-audio-3.0-realtime-plus")
VOICE = os.environ.get("ALIYUN_REALTIME_VOICE", "longanqian")
# 交互模式: server_vad(官方demo默认,参数可控,诊断友好) | smart_turn(语义轮次,过滤嗯啊)
TURN_DETECTION = os.environ.get("ALIYUN_TURN_DETECTION", "server_vad")
VAD_THRESHOLD = float(os.environ.get("ALIYUN_VAD_THRESHOLD", "0.5"))        # [-1.0, 1.0]
SILENCE_MS = int(os.environ.get("ALIYUN_SILENCE_DURATION_MS", "800"))     # [200, 6000], 对话推荐400-800
# 噪声门(官方demo同款): 平均振幅低于该值的音频块直接丢弃, 防止环境噪声让VAD常开
NOISE_GATE = int(os.environ.get("ALIYUN_NOISE_GATE", "500"))

# ---------------- 知识库 ----------------
# KNOWLEDGE_ENABLED=0 可整体关闭; KNOWLEDGE_BACKEND=local (SQLite+numpy 嵌入式混合检索)
KNOWLEDGE_ENABLED = os.environ.get("KNOWLEDGE_ENABLED", "1").strip() not in ("0", "false", "no")

# ---------------- 通话记录 ----------------
# CALLLOG_ENABLED=0 可关闭; 数据落在 data/calls.sqlite, 查看: python calllog_app.py
CALLLOG_ENABLED = os.environ.get("CALLLOG_ENABLED", "1").strip() not in ("0", "false", "no")
# 外呼(autodial)开场: 任务注入后多少秒无人出声, 就让 AI 先开口(播种一条用户文本触发回复)
OUTBOUND_OPEN_DELAY = float(os.environ.get("OUTBOUND_OPEN_DELAY", "5"))
# 来电自动接听: AUTO_ANSWER=1 开启; AUTO_ANSWER_VIDEO=1 时连视频通话也接(默认只接语音)
AUTO_ANSWER = os.environ.get("AUTO_ANSWER", "1").strip() not in ("0", "false", "no")
AUTO_ANSWER_VIDEO = os.environ.get("AUTO_ANSWER_VIDEO", "0").strip() in ("1", "true", "yes")
AUTO_ANSWER_POLL = float(os.environ.get("AUTO_ANSWER_POLL", "1.0"))
# 来电监听线程 <-> WS 客户端 的线程间共享状态:
#   answer_seed_at: 非 None 且到期时, 播种让 AI 对来电先开口
_shared = {"answer_seed_at": None, "answer_caller": "对方"}
_incoming_watcher = None


def _hangup_drain(down_queue, max_wait: float = 15.0) -> None:
    """同步等待播放队列清空 (farewell 完整注入微信), 供 asyncio executor 调用。"""
    try:
        from autodial.hangup import wait_audio_drain
        wait_audio_drain(down_queue, max_wait)
    except Exception as e:  # noqa: BLE001
        print(f"[HANGUP] drain 异常(忽略): {e}", flush=True)


def _hangup_click() -> dict:
    """同步点击挂断按钮, 供 asyncio executor 调用。pywinauto 缺失时降级返回。"""
    try:
        from autodial.hangup import hang_up
        return hang_up()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "method": f"unavailable: {e}"}
_call_recorder = None
if CALLLOG_ENABLED:
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from calllog.recorder import CallRecorder
        from autodial.taskfile import clear_current_task
        _call_recorder = CallRecorder(on_call_closed=clear_current_task)
        print("[CALLLOG] 通话记录已启用 (data/calls.sqlite)", flush=True)
    except Exception as _e:  # noqa: BLE001
        print(f"[CALLLOG] 通话记录初始化失败, 本次不记录: {_e}", flush=True)
        _call_recorder = None
_kb = None
if KNOWLEDGE_ENABLED:
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from knowledge import create_knowledge
        _kb = create_knowledge()
        _stats = _kb.get_stats()
        print(f"[KB] 知识库已加载: backend={_stats['backend']}, docs={_stats['documents']}, "
              f"chunks={_stats['chunks']}, embedding={_stats['embedding']}", flush=True)
    except Exception as _e:  # noqa: BLE001
        print(f"[KB] 知识库加载失败, 本次通话不启用知识库: {_e}", flush=True)
        _kb = None

# search_knowledge 工具 (Qwen Realtime function calling, OpenAI 兼容格式)
SEARCH_KNOWLEDGE_TOOL = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": (
            "搜索本地知识库(业务资料/FAQ/产品说明)。"
            "当对方问到你不确定的业务信息(价格、政策、流程、产品信息)时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题"},
                "top_k": {"type": "integer", "description": "返回条数(默认5)", "default": 5},
            },
            "required": ["query"],
        },
    },
}

# hang_up 工具: AI 识别到对方明确挂断意图时调用, 先道别再挂断
HANG_UP_TOOL = {
    "type": "function",
    "function": {
        "name": "hang_up",
        "description": (
            "挂断当前语音通话。仅当对方明确表示要结束通话时调用"
            "(如: '先这样吧'、'挂了'、'拜拜'、'没事了再见')。"
            "调用之后, 系统会自动挂断电话, 请在收到工具结果后立即用一句简短口语化的话礼貌道别。"
            "对方只是沉默或犹豫时不要调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "挂断原因(一句话)"},
            },
            "required": ["reason"],
        },
    },
}

# 采样率: 捕获端跟随 loopback 设备原生采样率, 上行重采样到 16k; 下行 API 24k -> 注入设备原生采样率
CAPTURE_RATE_FALLBACK = 48000
UP_RATE = 16000      # 上行给 API
DOWN_API_RATE = 24000  # API 返回
INJECT_RATE_FALLBACK = 48000  # CABLE Input 原生采样率

CHUNK = 960          # ~20ms @48k
PLAY_QUEUE_MAX = 300 # ~6s @20ms 缓冲上限

INSTRUCTIONS = (
    "你是一位自然的中文电话语音助手。用户正在和你打电话。"
    "用简短、口语化的中文回答,像真人聊天一样,不要长篇大论。"
    "每次回复尽量控制在一两句话以内。"
)


def build_ws_url(base_url: str, model: str) -> str:
    """复用 newcallcall 的 URL 构造逻辑: scheme -> wss, path -> /api-ws/v1/realtime"""
    from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl
    u = urlparse(base_url)
    scheme = "wss" if u.scheme in ("https", "http") else u.scheme
    q = dict(parse_qsl(u.query))
    q["model"] = model
    return urlunparse((scheme, u.netloc, "/api-ws/v1/realtime", "", urlencode(q), ""))


# ---------------- 音频重采样 ----------------
def resample_linear(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """简单线性插值重采样, 足够实时语音用 (原型阶段)"""
    if src_rate == dst_rate or len(pcm) == 0:
        return pcm
    duration = len(pcm) / src_rate
    dst_len = max(1, int(round(duration * dst_rate)))
    x_old = np.linspace(0, duration, num=len(pcm), endpoint=False)
    x_new = np.linspace(0, duration, num=dst_len, endpoint=False)
    return np.interp(x_new, x_old, pcm).astype(np.int16)


# ---------------- 设备枚举 ----------------
def _wasapi_index(pa):
    return pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]


def list_devices(pa: pyaudio.PyAudio):
    wasapi_idx = _wasapi_index(pa)
    print("\n=== 音频设备列表 (WASAPI) ===")
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d["hostApi"] != wasapi_idx:
            continue
        kind = []
        if d["maxInputChannels"] > 0:
            kind.append("IN ")
        if d["maxOutputChannels"] > 0:
            kind.append("OUT")
        print(f"  [{i:2d}] {'/'.join(kind):8s} {int(d['defaultSampleRate']):6d}Hz  {d['name']}")
    print("提示: 捕获用 [Loopback] 设备(物理扬声器的伴侣); 注入用 CABLE Input(输出端), 微信麦克风选 CABLE Output")


def find_wasapi_device(pa, keyword, want_output):
    """只在 WASAPI 设备里按关键词查找. want_output=True 找输出设备, False 找输入设备."""
    wasapi_idx = _wasapi_index(pa)
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d["hostApi"] != wasapi_idx:
            continue
        if keyword.lower() not in d["name"].lower():
            continue
        if want_output and d["maxOutputChannels"] > 0:
            return i
        if not want_output and d["maxInputChannels"] > 0:
            return i
    return None


def find_loopback_of(pa, output_idx):
    """找到某个输出设备对应的 [Loopback] 输入设备 (pyaudiowpatch 提供)."""
    out_name = pa.get_device_info_by_index(output_idx)["name"]
    wasapi_idx = _wasapi_index(pa)
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if (d["hostApi"] == wasapi_idx and d["maxInputChannels"] > 0
                and "Loopback" in d["name"] and out_name in d["name"]):
            return i
    return None


def find_source_output_of_loopback(pa, loopback_idx):
    """loopback 设备名形如 'XXX [Loopback]', 找到名字去掉 [Loopback] 后缀的源输出设备."""
    lb_name = pa.get_device_info_by_index(loopback_idx)["name"]
    base = lb_name.replace(" [Loopback]", "").replace("[Loopback]", "").strip()
    wasapi_idx = _wasapi_index(pa)
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if (d["hostApi"] == wasapi_idx and d["maxOutputChannels"] > 0
                and d["name"].strip() == base):
            return i
    return None


# ---------------- 静音保活线程 (防止 loopback 在扬声器空闲时阻塞) ----------------
class KeepAliveThread(threading.Thread):
    """WASAPI loopback 在输出设备完全空闲(无任何渲染流)时 read() 会阻塞不吐数据。
    通话中对方一旦停顿, 捕获就会卡死, VAD 收不到静音, AI 永远不回复。
    解法: 向 loopback 对应的物理扬声器持续写静音帧, 保持渲染会话活跃,
    loopback 即可持续吐数据(静音+真实声音); 静音本身听不见。"""
    def __init__(self, pa, output_idx, stop_event):
        super().__init__(daemon=True)
        self.pa = pa
        self.output_idx = output_idx
        self.stop_event = stop_event

    def run(self):
        dev = self.pa.get_device_info_by_index(self.output_idx)
        rate = int(dev["defaultSampleRate"])
        max_ch = int(dev["maxOutputChannels"]) or 2
        # 探测可用的 (声道数, 采样率) 组合: 该设备可能要求特定声道数(如4ch)
        stream = None
        channels = 0
        for ch in sorted({max_ch, 4, 2, 1}, reverse=True):
            if ch > max_ch:
                continue
            for r in sorted({rate, 48000, 44100}, reverse=True):
                try:
                    stream = self.pa.open(
                        format=pyaudio.paInt16,
                        channels=ch,
                        rate=r,
                        output=True,
                        output_device_index=self.output_idx,
                        frames_per_buffer=CHUNK,
                    )
                    channels = ch
                    rate = r
                    break
                except Exception:
                    stream = None
                    continue
            if stream is not None:
                break
        if stream is None:
            print(f"[KEEPALIVE] 打开失败(不影响主流程)", flush=True)
            return
        silence = b"\x00" * (CHUNK * 2 * channels)
        print(f"[KEEPALIVE] 静音保活启动: {dev['name']} @ {rate}Hz ch={channels}", flush=True)
        while not self.stop_event.is_set():
            try:
                stream.write(silence)
            except Exception:
                break
        stream.stop_stream()
        stream.close()


# ---------------- 捕获线程 (loopback) ----------------
class CaptureThread(threading.Thread):
    def __init__(self, pa, device_idx, out_queue, stop_event):
        super().__init__(daemon=True)
        self.pa = pa
        self.device_idx = device_idx
        self.out_queue = out_queue
        self.stop_event = stop_event
        self.native_rate = CAPTURE_RATE_FALLBACK

    def run(self):
        dev = self.pa.get_device_info_by_index(self.device_idx)
        self.native_rate = int(dev["defaultSampleRate"])
        # 选中的就是 [Loopback] 输入设备, 直接当 input 打开
        channels = int(dev["maxInputChannels"]) or 2
        try:
            stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=self.native_rate,
                input=True,
                input_device_index=self.device_idx,
                frames_per_buffer=CHUNK,
            )
        except Exception as e:
            print(f"[CAPTURE] 打开失败: {e}", flush=True)
            return
        print(f"[CAPTURE] 启动: {dev['name']} @ {self.native_rate}Hz ch={channels}", flush=True)
        last_level_time = time.time()
        level_acc = []
        while not self.stop_event.is_set():
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
            except Exception:
                continue
            pcm = np.frombuffer(data, dtype=np.int16)
            # 每秒打印一次 RMS 电平, 便于判断捕获的是人声还是噪声/静音
            level_acc.append(float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))))
            now = time.time()
            if now - last_level_time >= 1.0:
                rms = sum(level_acc) / len(level_acc)
                bar = "#" * min(40, int(rms / 200))
                print(f"[电平] RMS={rms:8.0f} {bar}", flush=True)
                level_acc = []
                last_level_time = now
            if channels > 1:
                pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
            try:
                self.out_queue.put_nowait(pcm.tobytes())
            except queue.Full:
                pass
        stream.stop_stream()
        stream.close()
        print("[CAPTURE] 停止", flush=True)


# ---------------- 播放/注入线程 (写入 CABLE Input -> 微信从 CABLE Output 读取) ----------------
class PlayThread(threading.Thread):
    def __init__(self, pa, device_idx, in_queue, stop_event):
        super().__init__(daemon=True)
        self.pa = pa
        self.device_idx = device_idx
        self.in_queue = in_queue
        self.stop_event = stop_event

    def run(self):
        dev = self.pa.get_device_info_by_index(self.device_idx)
        rate = int(dev["defaultSampleRate"]) or INJECT_RATE_FALLBACK
        # CABLE Output 是麦克风设备(maxOutputChannels=0), 尝试用2声道, 失败退回1声道
        stream = None
        channels = 2
        for ch in (2, 1):
            try:
                stream = self.pa.open(
                    format=pyaudio.paInt16,
                    channels=ch,
                    rate=rate,
                    output=True,
                    output_device_index=self.device_idx,
                    frames_per_buffer=CHUNK,
                )
                channels = ch
                break
            except Exception:
                stream = None
                continue
        if stream is None:
            print(f"[PLAY] 打开失败 (设备可能不支持作为输出)", flush=True)
            return
        self.rate = rate
        self.channels = channels
        print(f"[PLAY] 启动: {dev['name']} @ {rate}Hz ch={channels}", flush=True)
        silence = b"\x00" * (CHUNK * 2 * channels)
        while not self.stop_event.is_set():
            try:
                data = self.in_queue.get(timeout=0.05)
            except queue.Empty:
                data = silence
            if channels == 2:
                pcm = np.frombuffer(data, dtype=np.int16)
                stereo = np.empty(len(pcm) * 2, dtype=np.int16)
                stereo[0::2] = pcm
                stereo[1::2] = pcm
                data = stereo.tobytes()
            try:
                stream.write(data)
            except Exception:
                pass
        stream.stop_stream()
        stream.close()
        print("[PLAY] 停止", flush=True)


# ---------------- 阿里云 Realtime 客户端 ----------------
class AliyunRealtime:
    def __init__(self, up_queue, down_queue, stop_event, kb=None):
        self.up_queue = up_queue      # bytes (native capture rate pcm16 mono)
        self.down_queue = down_queue  # bytes (inject rate pcm16 mono)
        self.stop_event = stop_event
        self.ws = None
        self.capture_rate = CAPTURE_RATE_FALLBACK
        self.play_rate = INJECT_RATE_FALLBACK
        # 服务端是否正在生成回复 (噪声门/打断判断用)
        self.is_responding = False
        # 打断后抑制残余音频, 直到下一个 response.created
        self.audio_suppressed = False
        # 知识库: 通话建立时一次性决定注入策略 (移植 newcallcall kb_injection 分层思想)
        self.kb = kb
        self.kb_injection = None
        if kb is not None:
            try:
                self.kb_injection = kb.build_injection()
                print(f"[KB] 注入策略: tier={self.kb_injection['tier']}, "
                      f"search_tool={'开' if self.kb_injection['allow_search_tool'] else '关'}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[KB] 注入策略计算失败, 退化为无知识库: {e}", flush=True)
                self.kb_injection = None
        self._completed_call_ids = set()
        # 累积流式 AI 回复文本 (response.audio_transcript.delta), done 时落库
        self._ai_turn_buf = ""
        # autodial 任务注入状态 (_configure_session 里初始化)
        self._base_instructions = INSTRUCTIONS
        self._instructions = INSTRUCTIONS
        self._tools = []
        self._task_seq = 0
        self._task_seq_ts = 0.0
        self._task_seed_pending = False  # 外呼任务: 对方首次出声时播种让 AI 先开口
        # 挂断工具状态: AI 调用 hang_up 后置 pending, 等 farewell 语音播完再执行点击
        self._hangup_pending = None      # {"reason": str} or None
        self._hangup_done = False        # 防止重复执行

    async def run(self):
        url = build_ws_url(BASE_URL, MODEL)
        print(f"[WS] 连接: {url}", flush=True)
        async with websockets.connect(
            url,
            additional_headers={
                "Authorization": f"Bearer {API_KEY}",
                "x-dashscope-dataInspection": "disable",
            },
            open_timeout=15,
            ping_interval=15,
            ping_timeout=10,
            max_size=8 * 1024 * 1024,
        ) as ws:
            self.ws = ws
            print("[WS] 已连接", flush=True)
            await self._configure_session()
            sender = asyncio.create_task(self._send_loop())
            receiver = asyncio.create_task(self._recv_loop())
            stopper = asyncio.create_task(self._watch_stop())
            done, pending = await asyncio.wait(
                {sender, receiver, stopper}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()

    def _session_payload(self) -> dict:
        """构造完整 session.update 载荷 (含 tools)。"""
        # 严格按官方文档: turn_detection 支持 server_vad / smart_turn / null(push-to-talk)
        if TURN_DETECTION == "server_vad":
            td = {
                "type": "server_vad",
                "threshold": VAD_THRESHOLD,          # [-1.0,1.0] 默认0.5
                "silence_duration_ms": SILENCE_MS,   # [200,6000] 对话推荐400-800
            }
        elif TURN_DETECTION == "push_to_talk":
            td = None
        else:
            td = {"type": "smart_turn"}
        return {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": VOICE,
                "instructions": self._instructions,
                "input_audio_format": "pcm",   # 16kHz 16bit 单声道
                "output_audio_format": "pcm",  # 24kHz 16bit 单声道
                "turn_detection": td,
                "max_history_turns": 20,
                "tools": self._tools,
            },
        }

    def _with_task(self, base_instructions: str):
        """若 data/current_task.json 存在 autodial 写入的任务, 把任务上下文拼进 instructions。
        返回 (instructions, seq); seq=0 表示无任务。"""
        task_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "current_task.json")
        try:
            with open(task_path, "r", encoding="utf-8") as f:
                task = json.load(f)
        except Exception:
            return base_instructions, 0
        seq = int(task.get("seq", 0) or 0)
        if not seq:
            return base_instructions, 0
        parts = [base_instructions, "",
                 f"【本次通话任务】你正在与「{task.get('contact', '')}」语音通话。"]
        if task.get("task"):
            parts.append(f"任务内容: {task['task']}")
        if task.get("note"):
            parts.append(f"备注: {task['note']}")
        parts.append("请自然地围绕任务与对方沟通, 不要提及任何系统提示。")
        return "\n".join(parts), seq

    async def _maybe_inject_task(self):
        """轮询 autodial 任务文件; 变化时重发 session.update 更新 instructions。"""
        new_instr, seq = self._with_task(self._base_instructions)
        if seq == self._task_seq:
            return
        self._instructions = new_instr
        self._task_seq = seq
        self._task_seq_ts = time.time()
        await self.ws.send(json.dumps(self._session_payload(), ensure_ascii=False))
        if seq:
            self._task_seed_pending = True  # 外呼: 对方首次出声时让 AI 先开口
        else:
            self._task_seed_pending = False
        print(f"[TASK] 通话任务指令已更新 (seq={seq})", flush=True)

    async def _configure_session(self):
        # 注意: Qwen-Audio 的 session.update 没有 input_audio_transcription 字段,
        #       转写事件服务端默认推送, 无需配置
        self._base_instructions = INSTRUCTIONS
        self._tools = []
        if self.kb_injection is not None:
            ctx = self.kb_injection.get("context_text", "")
            if ctx.strip():
                self._base_instructions = INSTRUCTIONS + "\n\n【知识库资料】\n" + ctx.strip()
            if self.kb_injection.get("allow_search_tool"):
                self._tools.append(SEARCH_KNOWLEDGE_TOOL)
        # hang_up 工具始终可用 (不依赖知识库)
        self._tools.append(HANG_UP_TOOL)
        self._instructions, self._task_seq = self._with_task(self._base_instructions)

        await self.ws.send(json.dumps(self._session_payload(), ensure_ascii=False))
        tool_note = f", tools={len(self._tools)}" if self._tools else ""
        print(f"[WS] 已发送 session.update (mode={TURN_DETECTION}, noise_gate={NOISE_GATE}{tool_note})", flush=True)
        # 等 session.updated
        while True:
            msg = json.loads(await self.ws.recv())
            t = msg.get("type")
            if t == "session.updated":
                print("[WS] session 就绪", flush=True)
                break
            if t == "error":
                raise RuntimeError(f"session.update 失败: {msg}")

    async def _send_loop(self):
        """从捕获队列取音频 -> 重采样到16k -> 攒成~100ms -> base64 append

        噪声门(官方demo同款, 仅在AI说话期间生效): AI 播放时只放行高能量音频,
        防止环境噪声误触发打断; 空闲时发送全部音频(含静音),
        让服务端 VAD 能收到静音从而判定"用户说完", 触发回复。
        """
        send_buf = bytearray()
        # 官方建议每次发送 ~100ms 音频: 16kHz*2bytes*0.1s = 3200 bytes
        TARGET = 3200
        loop = asyncio.get_event_loop()
        while not self.stop_event.is_set():
            # 挂断流程中/已完成: 停止上行, 避免触发新的 VAD 事件
            if self._hangup_pending is not None or self._hangup_done:
                await asyncio.sleep(0.3)
                continue
            try:
                chunk = await loop.run_in_executor(None, self.up_queue.get, True, 0.1)
            except queue.Empty:
                # 队列空时把缓冲里剩余的音频发出去, 避免尾部音频丢失
                if send_buf:
                    b64 = base64.b64encode(bytes(send_buf)).decode("ascii")
                    await self.ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": b64}))
                    send_buf.clear()
                continue
            pcm = np.frombuffer(chunk, dtype=np.int16)
            # 噪声门仅在 AI 正在响应/播放时启用(官方 demo 行为)
            ai_active = self.is_responding or self.down_queue.qsize() > 0
            if ai_active and float(np.abs(pcm).mean()) < NOISE_GATE:
                continue
            pcm16k = resample_linear(pcm, self.capture_rate, UP_RATE)
            send_buf.extend(pcm16k.tobytes())
            if len(send_buf) >= TARGET:
                b64 = base64.b64encode(bytes(send_buf)).decode("ascii")
                await self.ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": b64}))
                send_buf.clear()

    async def _recv_loop(self):
        """收 API 事件: response.audio.delta -> 重采样 -> 播放队列; 其余事件打印用于诊断。
        事件语义按官方文档对齐:
          speech_started -> 打断: 清空播放缓冲 + 抑制残余音频
          response.created -> is_responding=True, 解除抑制
          response.done -> is_responding=False (status 可能为 cancelled)
        """
        async for raw in self.ws:
            if self.stop_event.is_set():
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            t = msg.get("type", "")
            if t == "response.audio.delta":
                if self.audio_suppressed:
                    continue  # 已被打断, 丢弃残余音频
                pcm = np.frombuffer(base64.b64decode(msg["delta"]), dtype=np.int16)
                pcm_out = resample_linear(pcm, DOWN_API_RATE, self.play_rate)
                if self.down_queue.qsize() < PLAY_QUEUE_MAX:
                    self.down_queue.put_nowait(pcm_out.tobytes())
            elif t == "input_audio_buffer.speech_started":
                # 官方打断处理: 清空播放缓冲 + 抑制后续残余音频
                self.audio_suppressed = True
                while not self.down_queue.empty():
                    try:
                        self.down_queue.get_nowait()
                    except queue.Empty:
                        break
                # 对方先开口 -> 取消外呼开场播种
                self._task_seed_pending = False
                if _call_recorder:
                    _call_recorder.on_remote_speech()
                print("[WS] 检测到说话(speech_started)", flush=True)
            elif t == "input_audio_buffer.speech_stopped":
                reason = msg.get("reason", "")
                print(f"[WS] 检测到停止(speech_stopped{' reason='+reason if reason else ''})", flush=True)
            elif t == "input_audio_buffer.committed":
                print("[WS] 音频缓冲已提交(committed)", flush=True)
            elif t == "conversation.item.input_audio_transcription.delta":
                print(f"⟨{msg.get('delta','')}⟩", end="", flush=True)
            elif t == "conversation.item.input_audio_transcription.completed":
                txt = msg.get('transcript', '').strip()
                print(f"\n[对方说] {txt}", flush=True)
                if _call_recorder and txt:
                    _call_recorder.on_remote_transcript(txt)
            elif t == "conversation.item.ambient_audio_transcription.completed":
                txt = msg.get('text', '').strip()
                print(f"[环境音] {txt}", flush=True)
                if _call_recorder and txt:
                    _call_recorder.on_ambient(txt)
            elif t == "response.created":
                self.is_responding = True
                self.audio_suppressed = False
                self._ai_turn_buf = ""
                print("[WS] 开始生成回复(response.created)", flush=True)
            elif t == "response.function_call_arguments.done":
                await self._handle_function_call(msg)
            elif t == "response.audio_transcript.delta":
                self._ai_turn_buf += msg.get("delta", "")
                print(msg.get("delta", ""), end="", flush=True)
            elif t == "response.audio_transcript.done":
                print(flush=True)
                if _call_recorder and self._ai_turn_buf.strip():
                    _call_recorder.on_ai_transcript(self._ai_turn_buf)
                self._ai_turn_buf = ""
            elif t == "response.done":
                self.is_responding = False
                resp = msg.get("response", {})
                print(f"[WS] 回复完成(response.done, status={resp.get('status','')})", flush=True)
            elif t == "error":
                print(f"[WS] 错误: {msg}", flush=True)

    async def _handle_function_call(self, event: dict):
        """Realtime function calling: 收到工具调用 -> 执行 -> 回传 function_call_output -> 触发继续生成。
        协议与 newcallcall 一致 (Qwen Realtime 兼容 OpenAI Realtime 格式)。"""
        call_id = str(event.get("call_id") or "")
        name = str(event.get("name") or "")
        arguments = str(event.get("arguments") or "{}")
        if not call_id or not name:
            return
        if call_id in self._completed_call_ids:
            return
        self._completed_call_ids.add(call_id)
        try:
            args = json.loads(arguments)
        except Exception:
            args = {}
        print(f"[KB] 工具调用: {name}({arguments})", flush=True)

        if name == "search_knowledge" and self.kb is not None:
            loop = asyncio.get_event_loop()
            try:
                snippets = await loop.run_in_executor(
                    None, lambda: self.kb.query(args.get("query", ""),
                                                int(args.get("top_k", 5) or 5))
                )
                result = {
                    "status": "ok",
                    "count": len(snippets),
                    "snippets": [{"text": s.text, "score": round(s.score, 3),
                                  "source": s.source} for s in snippets],
                }
            except Exception as e:  # noqa: BLE001
                result = {"status": "error", "message": str(e), "snippets": []}
            await self.ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                },
            }, ensure_ascii=False))
            # 让模型基于工具结果继续生成回复
            await self.ws.send(json.dumps({"type": "response.create"}))
            if _call_recorder:
                _call_recorder.on_tool_call(name, arguments, result.get("count"))
            print(f"[KB] 已回传工具结果 ({result.get('count', 0)} 条)", flush=True)
            return

        if name == "hang_up":
            reason = str(args.get("reason", "")).strip() or "AI 判定通话结束"
            print(f"[HANGUP] AI 请求挂断: {reason}", flush=True)
            if _call_recorder:
                _call_recorder.on_tool_call(name, arguments)
                _call_recorder.on_note(f"[挂断] {reason}")
            result = {"status": "ok", "message": "通话将在道别语音播放完毕后自动挂断"}
            await self.ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                },
            }, ensure_ascii=False))
            # 触发模型生成道别语(farewell); 播完后由 _execute_hangup 执行点击
            await self.ws.send(json.dumps({"type": "response.create"}))
            if not self._hangup_done:
                self._hangup_pending = {"reason": reason}
                asyncio.create_task(self._execute_hangup())
            return

        result = {"status": "error", "message": f"unknown tool: {name}", "snippets": []}
        await self.ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result, ensure_ascii=False),
            },
        }, ensure_ascii=False))
        await self.ws.send(json.dumps({"type": "response.create"}))

    async def _seed_opening(self, text: str = "(电话已接通)"):
        """播一条用户文本种子并触发回复, 让 AI 先开口。
        (newcallcall opening-seed 技巧: Qwen Realtime 在无用户消息时拒绝 response.create)"""
        self._task_seed_pending = False
        await self.ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        }, ensure_ascii=False))
        await self.ws.send(json.dumps({
            "type": "response.create",
            "response": {"modalities": ["text", "audio"]},
        }, ensure_ascii=False))
        print(f"[TASK] 开场播种: {text} -> AI 先开口", flush=True)

    async def _execute_hangup(self):
        """挂断流程: 等 farewell 响应生成并播完 -> UI 点击挂断 -> 收尾通话记录。"""
        if self._hangup_done:
            return
        self._hangup_done = True
        reason = (self._hangup_pending or {}).get("reason", "")
        # 1) 等 farewell 响应开始 (response.created -> is_responding=True), 最多等 8s
        t0 = time.time()
        while not self.is_responding and time.time() - t0 < 8.0:
            await asyncio.sleep(0.1)
        # 2) 等 farewell 响应结束 (response.done -> is_responding=False), 最多等 20s
        t0 = time.time()
        while self.is_responding and time.time() - t0 < 20.0:
            await asyncio.sleep(0.1)
        # 3) 等播放队列清空, 确保道别语音完整注入微信
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _hangup_drain, self.down_queue)
        await asyncio.sleep(0.6)
        # 4) 点击挂断按钮 (UIA 优先, 校准回退)
        try:
            res = await loop.run_in_executor(None, _hangup_click)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "method": f"exception: {e}"}
        print(f"[HANGUP] 执行挂断: ok={res.get('ok')} method={res.get('method')} reason={reason}",
              flush=True)
        if not res.get("ok") and _call_recorder:
            _call_recorder.on_note(f"[挂断] UI点击失败({res.get('method')}), 请手动挂断")
        # 5) 通知来电监听暂停检测(防止通话窗口消失过程误判)
        if _incoming_watcher is not None:
            _incoming_watcher.set_busy(15)
        # 6) 收尾通话记录 (清任务文件由 on_call_closed 回调完成)
        if _call_recorder:
            _call_recorder.close()

    async def _watch_stop(self):
        while not self.stop_event.is_set():
            await asyncio.sleep(0.2)
            if _call_recorder:
                _call_recorder.check_idle()
            # 轮询 autodial 任务文件, 变化时重发 session.update
            try:
                await self._maybe_inject_task()
                # 外呼开场: 任务注入后 N 秒仍无人出声 -> 播种让 AI 先开口
                if (self._task_seed_pending and not self.is_responding
                        and time.time() - self._task_seq_ts > OUTBOUND_OPEN_DELAY):
                    await self._seed_opening()
                # 来电接听后开场: 监听线程置位 answer_seed_at, 到期即播种
                seed_at = _shared.get("answer_seed_at")
                if seed_at is not None and not self.is_responding:
                    if time.time() >= seed_at:
                        _shared["answer_seed_at"] = None
                        caller = _shared.get("answer_caller", "对方")
                        await self._seed_opening(f"(来电已自动接听, 来电人是 {caller})")
            except Exception as e:  # noqa: BLE001
                print(f"[TASK] 轮询异常: {e}", flush=True)


def ws_thread_entry(up_queue, down_queue, stop_event, capture_rate_holder, play_rate_holder, kb=None):
    client = AliyunRealtime(up_queue, down_queue, stop_event, kb=kb)
    # 等捕获线程确定 native rate
    for _ in range(50):
        if capture_rate_holder.get("rate"):
            client.capture_rate = capture_rate_holder["rate"]
            break
        time.sleep(0.1)
    for _ in range(50):
        if play_rate_holder.get("rate"):
            client.play_rate = play_rate_holder["rate"]
            break
        time.sleep(0.1)
    try:
        asyncio.run(client.run())
    except Exception as e:
        print(f"[WS] 异常退出: {e}", flush=True)
        stop_event.set()


# ---------------- 系统默认麦克风切换 (微信无持久设备设置, 跟随系统默认麦克风) ----------------
class DefaultMicSwitch:
    """启动时: 把系统默认麦克风切到 CABLE Output, 微信发起通话即自动使用;
    退出时: 还原原默认麦克风。"""

    def __init__(self):
        self.active = False
        self.prev_mic_id = None
        self._com_inited = False

    def activate(self):
        try:
            import warnings
            warnings.filterwarnings("ignore")
            import comtypes
            try:
                comtypes.CoInitialize()
                self._com_inited = True
            except Exception:
                pass  # 已初始化过
            from pycaw.pycaw import AudioUtilities, ERole

            # 1. 找 CABLE Output (capture 端点, id 以 {0.0.1. 开头)
            target = None
            for d in AudioUtilities.GetAllDevices():
                name = d.FriendlyName or ""
                if "CABLE Output" in name and d.id.startswith("{0.0.1."):
                    target = d
                    break
            if target is None:
                print("[默认设备] 未找到 CABLE Output, 跳过切换 (需在通话中手动选麦克风)", flush=True)
                return False

            # 2. 记录当前默认麦克风 (eCommunications 角色, 微信跟随它), 用于退出还原
            try:
                prev = AudioUtilities.GetMicrophone()
                self.prev_mic_id = str(prev.GetId())
            except Exception:
                self.prev_mic_id = None
            if self.prev_mic_id == target.id:
                self.prev_mic_id = None  # 本来就是 CABLE Output, 退出时不还原
                self.active = True
                print("[默认设备] 默认麦克风已是 CABLE Output", flush=True)
                return True

            # 3. 设为默认 (Console + Communications 双角色, 微信走 Communications)
            AudioUtilities.SetDefaultDevice(
                target.id, roles=[ERole.eConsole, ERole.eCommunications]
            )
            self.active = True
            print("[默认设备] 系统默认麦克风 -> CABLE Output (微信通话将自动使用)", flush=True)
            return True
        except Exception as e:
            print(f"[默认设备] 切换失败: {e} (可在通话窗口手动选麦克风)", flush=True)
            return False

    def restore(self):
        if not self.active or self.prev_mic_id is None:
            return
        try:
            from pycaw.pycaw import AudioUtilities, ERole
            AudioUtilities.SetDefaultDevice(
                self.prev_mic_id, roles=[ERole.eConsole, ERole.eCommunications]
            )
            print("[默认设备] 已还原系统默认麦克风", flush=True)
        except Exception as e:
            print(f"[默认设备] 还原失败: {e}", flush=True)


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列出音频设备后退出")
    ap.add_argument("--capture-idx", type=int, default=None,
                    help="捕获设备索引 (某个输出设备的 [Loopback] 伴侣)")
    ap.add_argument("--inject-idx", type=int, default=None,
                    help="注入设备索引 (CABLE Input, 作为输出写入)")
    ap.add_argument("--no-default-mic", action="store_true",
                    help="不自动切换系统默认麦克风 (默认会自动切到 CABLE Output)")
    ap.add_argument("--no-auto-answer", action="store_true",
                    help="关闭来电自动接听 (默认按 AUTO_ANSWER 配置开启)")
    args = ap.parse_args()
    global AUTO_ANSWER
    if args.no_auto_answer:
        AUTO_ANSWER = False

    if not API_KEY:
        print("错误: 未配置 ALIYUN_REALTIME_API_KEY (检查 .env)")
        sys.exit(1)

    pa = pyaudio.PyAudio()
    list_devices(pa)

    if args.list:
        pa.terminate()
        return

    wasapi_idx = _wasapi_index(pa)

    # ---- 捕获设备: 微信扬声器(物理设备) 的 loopback ----
    # 注意: 必须选【物理】输出设备的 loopback, 绝不能选 CABLE 的 loopback,
    #       否则 AI 注入 CABLE Input 的声音会被重新捕获, 形成自激回声。
    capture_idx = args.capture_idx
    if capture_idx is None:
        # 优先: 系统默认输出设备的 loopback (前提: 它不是 CABLE)
        try:
            default_out = pa.get_default_output_device_info()
            if "CABLE" not in default_out["name"]:
                capture_idx = find_loopback_of(pa, default_out["index"])
                if capture_idx is not None:
                    print(f"\n自动选中捕获设备 [{capture_idx}] (默认输出 '{default_out['name']}' 的 loopback)")
        except Exception:
            capture_idx = None
        if capture_idx is None:
            # 回退: 找第一个【物理非 CABLE】输出设备的 loopback
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if (d["hostApi"] == wasapi_idx and d["maxOutputChannels"] > 0
                        and "CABLE" not in d["name"]):
                    lb = find_loopback_of(pa, i)
                    if lb is not None:
                        capture_idx = lb
                        print(f"\n自动选中捕获设备 [{capture_idx}] ('{d['name']}' 的 loopback)")
                        break
        if capture_idx is None:
            capture_idx = int(input("\n输入捕获设备索引 ([Loopback] 设备, 须为物理扬声器): ").strip())

    # ---- 注入设备: CABLE Input (输出端) ----
    inject_idx = args.inject_idx
    if inject_idx is None:
        inject_idx = find_wasapi_device(pa, "CABLE Input", want_output=True)
        if inject_idx is not None:
            print(f"自动选中注入设备 [{inject_idx}] {pa.get_device_info_by_index(inject_idx)['name']}")
        else:
            inject_idx = int(input("输入注入设备索引 (CABLE Input): ").strip())

    up_q = queue.Queue(maxsize=500)
    down_q = queue.Queue(maxsize=PLAY_QUEUE_MAX)
    stop_event = threading.Event()

    # 自动切换系统默认麦克风 -> CABLE Output (微信无持久设备设置, 跟随系统默认)
    mic_switch = DefaultMicSwitch()
    if not args.no_default_mic:
        mic_switch.activate()

    # 静音保活: 防止 loopback 在扬声器空闲时阻塞 (通话中对方停顿的致命场景)
    src_out = find_source_output_of_loopback(pa, capture_idx)
    if src_out is not None:
        keepalive = KeepAliveThread(pa, src_out, stop_event)
        keepalive.start()
    else:
        print("[MAIN] 警告: 未找到 loopback 的源输出设备, 无法启用静音保活", flush=True)

    cap = CaptureThread(pa, capture_idx, up_q, stop_event)
    cap.start()
    rate_holder = {}
    for _ in range(50):
        if cap.native_rate and cap.is_alive():
            rate_holder["rate"] = cap.native_rate
            break
        time.sleep(0.1)
    if "rate" not in rate_holder:
        rate_holder["rate"] = CAPTURE_RATE_FALLBACK

    play = PlayThread(pa, inject_idx, down_q, stop_event)
    play.start()
    play_rate_holder = {"rate": INJECT_RATE_FALLBACK}
    for _ in range(50):
        if getattr(play, "rate", None):
            play_rate_holder["rate"] = play.rate
            break
        time.sleep(0.1)

    ws_t = threading.Thread(
        target=ws_thread_entry, args=(up_q, down_q, stop_event, rate_holder, play_rate_holder, _kb), daemon=True
    )
    ws_t.start()

    # ---- 来电自动接听 ----
    global _incoming_watcher
    if AUTO_ANSWER:
        def _on_incoming_answered(caller: str):
            # 接通 3 秒后若无人出声, 播种让 AI 先开口 (watch_stop 里消费)
            _shared["answer_seed_at"] = time.time() + 3.0
            _shared["answer_caller"] = caller or "对方"
            if _call_recorder:
                _call_recorder.on_note(f"[来电] 自动接听: {caller or '未知'}")
        try:
            from autodial.incoming import IncomingWatcher
            _incoming_watcher = IncomingWatcher(
                on_answered=_on_incoming_answered,
                allow_video=AUTO_ANSWER_VIDEO,
                poll_sec=AUTO_ANSWER_POLL,
            )
            _incoming_watcher.start()
        except Exception as e:  # noqa: BLE001
            print(f"[MAIN] 来电监听启动失败(不影响桥接): {e}", flush=True)
    else:
        print("[MAIN] 来电自动接听已关闭 (AUTO_ANSWER=0)", flush=True)

    print("\n=== 桥接已启动, 按 Ctrl+C 退出 ===\n")
    try:
        while not stop_event.is_set():
            time.sleep(0.5)
            if not ws_t.is_alive():
                print("[MAIN] WS 线程已退出")
                break
    except KeyboardInterrupt:
        print("\n退出中...")
    finally:
        stop_event.set()
        if _incoming_watcher is not None:
            _incoming_watcher.stop_event.set()
        if _call_recorder:
            _call_recorder.close()  # 收尾未关闭的通话记录
        mic_switch.restore()
        time.sleep(0.3)
        pa.terminate()


if __name__ == "__main__":
    main()
