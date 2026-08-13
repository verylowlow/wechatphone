# -*- coding: utf-8 -*-
"""voice_msg — 微信"发送语音消息"标准功能 (CABLE 虚拟麦克风注入, 2026-08-13)

原理 (2026-08-12 实测通过 @ 微信 4.1.12.26):
  微信语音消息 = 本地录音后自行编码上传, 录音吃**系统默认麦克风**。
  把默认麦切到 CABLE Output, 往 CABLE Input 播放音频, 微信就录到我们播的声音。
  全程无需转 silk —— silk/mp3 等只是"输入格式", 统一解码成 PCM 再播。

发送时序 (顺序不可颠倒):
  1. 准备音频 (TTS 合成 / 文件解码) —— **失败即报错退出, 绝不碰微信**
  2. 搜索打开会话 (OCR 定位 + 标题校验, 防误发)
  3. 切系统默认麦 -> CABLE Output
  4. 单击绿色语音按钮 (发送键左侧) 进入录音态
  5. 等 VOICE_REC_START_MS 后 CABLE 播放音频
  6. 播完等 VOICE_TAIL_MS -> 点绿色箭头发送
  7. 还原默认麦克风

支持输入:
  --text "你好"        文本 -> TTS -> 音频 (volc 主引擎, edge-tts 免费兜底)
  xxx.wav              直接读取
  xxx.mp3/.m4a/...     ffmpeg 解码 (FFMPEG_PATH 可配)
  xxx.silk             silk-wasm 解码 (Node WASM, 无需编译器)

用法 (模块):
  from voice_msg import send_voice_msg
  send_voice_msg("小芳", source="D:/a.silk")
  send_voice_msg("小芳", text="明天上午十点开会")

用法 (命令行):
  .venv/Scripts/python.exe voice_msg.py 小芳 "D:/a.silk"
  .venv/Scripts/python.exe voice_msg.py 小芳 --text "你好"
  .venv/Scripts/python.exe voice_msg.py 小芳 "D:/a.mp3" --dry-run
  项目根目录 sendvoice.cmd 可任意目录调用:  sendvoice 小芳 "D:/a.silk"

.env 配置:
  TTS_ENGINE=volc          主引擎: volc (豆包 Seed TTS 原生接口) / edge (免费, 无需 key)
  TTS_FALLBACK_ENABLED=1   volc 失败时自动降级 edge
  TTS_API_KEY / TTS_BASE_URL / TTS_MODEL / TTS_VOICE / TTS_SPEED / TTS_TIMEOUT
    注: 原生接口鉴权用 X-Api-Key 头 (非 Bearer); TTS_MODEL 即 X-Api-Resource-Id,
    uranus(2.0)音色须配 seed-tts-2.0, 详见 test_doubao_tts.py
  TTS_EDGE_VOICE=zh-CN-XiaoxiaoNeural   edge 音色 (可 --list-voices 查询)
  VOICE_REC_START_MS=250   开录音后多少毫秒开始播放
  VOICE_TAIL_MS=250        播完后多少毫秒点发送
  FFMPEG_PATH / SILK_NODE_EXE / SILK_NODE_MODULES (一般不用改)
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import wave

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import pyaudiowpatch as pyaudio  # noqa: E402
import pyautogui  # noqa: E402

pyautogui.FAILSAFE = True  # 鼠标甩到屏幕左上角紧急中止

MAX_VOICE_SECONDS = 60  # 微信语音消息上限
SECTIONS = ("搜索网络结果", "最常使用", "联系人", "群聊", "功能", "收藏",
            "视频号", "朋友圈", "文章")


def log(*a):
    print(*a, flush=True)


# ---------------- 配置 ----------------

def _load_dotenv():
    env_path = os.path.join(ROOT, ".env")
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

TTS_ENABLED = os.environ.get("TTS_ENABLED", "1") == "1"
TTS_ENGINE = os.environ.get("TTS_ENGINE", "volc").strip().lower()
TTS_FALLBACK_ENABLED = os.environ.get("TTS_FALLBACK_ENABLED", "1") == "1"
TTS_API_KEY = os.environ.get("TTS_API_KEY", "")
TTS_BASE_URL = os.environ.get(
    "TTS_BASE_URL", "https://openspeech.bytedance.com/api/v3/tts/unidirectional")
# TTS_MODEL = X-Api-Resource-Id: uranus 2.0音色配 seed-tts-2.0,
# mars/moon 1.0音色配 seed-tts-1.0, 复刻音色(S_开头)配 seed-icl-2.0
TTS_MODEL = os.environ.get("TTS_MODEL", "seed-tts-2.0")
TTS_VOICE = os.environ.get("TTS_VOICE", "zh_female_jiaochuannv_uranus_bigtts")
TTS_EDGE_VOICE = os.environ.get("TTS_EDGE_VOICE", "zh-CN-XiaoxiaoNeural")
TTS_SPEED = float(os.environ.get("TTS_SPEED", "1.0"))
TTS_TIMEOUT = float(os.environ.get("TTS_TIMEOUT", "30"))
REC_START_S = int(os.environ.get("VOICE_REC_START_MS", "250")) / 1000.0
TAIL_S = int(os.environ.get("VOICE_TAIL_MS", "250")) / 1000.0

FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") \
    or r"D:\programes\ffmpeg\bin\ffmpeg.exe"
NODE_EXE = os.environ.get("SILK_NODE_EXE") \
    or r"C:\Users\automann\.workbuddy\binaries\node\versions\22.22.2\node.exe"
NODE_MODULES = os.environ.get("SILK_NODE_MODULES") \
    or r"C:\Users\automann\.workbuddy\binaries\node\workspace\node_modules"

VOICE_OUT_DIR = os.path.join(ROOT, "data", "voice_out")


# ---------------- 音频加载 (wav / mp3 / silk) ----------------

def load_wav(path: str):
    """返回 (mono int16 ndarray, 采样率)。"""
    with wave.open(path, "rb") as w:
        ch, rate = w.getnchannels(), w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return data, rate


def decode_with_ffmpeg(path: str, rate: int = 24000):
    """mp3/m4a/aac/ogg/flac 等 -> mono int16 PCM。"""
    if not os.path.exists(FFMPEG):
        raise FileNotFoundError(f"ffmpeg 不存在: {FFMPEG} (可设 FFMPEG_PATH)")
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", path,
           "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"ffmpeg 解码失败: {r.stderr.decode('utf-8', 'replace')[:200]}")
    return np.frombuffer(r.stdout, dtype=np.int16), rate


def decode_silk(path: str):
    """微信 .silk -> mono int16 PCM (silk-wasm, Node)。"""
    script = os.path.join(ROOT, "tools", "silk_decode.js")
    env = dict(os.environ)
    env["NODE_PATH"] = NODE_MODULES + os.pathsep + env.get("NODE_PATH", "")
    r = subprocess.run([NODE_EXE, script, path], capture_output=True,
                       timeout=120, env=env)
    if r.returncode != 0 or len(r.stdout) < 8:
        raise RuntimeError(f"silk 解码失败: {r.stderr.decode('utf-8', 'replace')[:200]}")
    out = r.stdout
    if out[:4] != b"RATE":
        raise RuntimeError("silk 解码输出格式异常")
    rate = int.from_bytes(out[4:8], "little")
    return np.frombuffer(out[8:], dtype=np.int16), rate


def load_audio(path: str):
    """按扩展名分发解码, 返回 (pcm int16 mono, rate)。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"音频文件不存在: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wav":
        return load_wav(path)
    if ext == ".silk":
        return decode_silk(path)
    if ext in (".mp3", ".m4a", ".aac", ".ogg", ".flac", ".amr", ".wma"):
        return decode_with_ffmpeg(path)
    raise ValueError(f"不支持的音频格式: {ext} (支持 wav/silk/mp3/m4a/aac/ogg/flac)")


def resample(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return pcm
    n_out = int(len(pcm) * dst_rate / src_rate)
    return np.interp(np.linspace(0, 1, n_out, endpoint=False),
                     np.linspace(0, 1, len(pcm), endpoint=False),
                     pcm.astype(np.float64)).astype(np.int16)


# ---------------- 文本 -> 语音 (volc 主引擎 + edge 免费兜底) ----------------

class TTSError(Exception):
    pass


def _tts_volc(text: str) -> str:
    """豆包 Seed TTS 原生接口 -> WAV 文件路径。失败抛 TTSError。

    POST {TTS_BASE_URL} (https://openspeech.bytedance.com/api/v3/tts/unidirectional)
      鉴权头 (新版控制台, 非 Bearer):
        X-Api-Key           = TTS_API_KEY
        X-Api-Resource-Id   = TTS_MODEL (uranus音色须 seed-tts-2.0;
                              mars/moon 1.0音色用 seed-tts-1.0; 复刻S_用 seed-icl-2.0)
        X-Api-Request-Id    = uuid
      body: {"user":{"uid"}, "req_params":{"text","speaker","audio_params"}}
      返回: NDJSON 流, 每行一个 JSON:
        {"code":0,"data":"<base64 pcm 片段>"}  拼接
        {"code":20000000,...}                  合成结束
        其他 code                              错误 (45000000=音色未授权等)
    音频按 pcm 请求, 本地补 WAV 头落盘 (流式 wav 会重复 header, 官方建议 pcm)。
    """
    import base64
    import json
    import struct
    import uuid
    import requests
    if not TTS_ENABLED:
        raise TTSError("TTS 未启用 (TTS_ENABLED=0)")
    if not TTS_API_KEY:
        raise TTSError("未配置 TTS_API_KEY (.env)")

    rate = 24000
    audio_params = {"format": "pcm", "sample_rate": rate}
    speech_rate = int(round((TTS_SPEED - 1.0) * 100))   # 100=2倍速, -50=0.5倍速
    if speech_rate:
        audio_params["speech_rate"] = max(-50, min(100, speech_rate))
    payload = {
        "user": {"uid": "wechatphone"},
        "req_params": {
            "text": text,
            "speaker": TTS_VOICE,
            "audio_params": audio_params,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": TTS_API_KEY,
        "X-Api-Resource-Id": TTS_MODEL,
        "X-Api-Request-Id": str(uuid.uuid4()),
    }
    try:
        resp = requests.post(TTS_BASE_URL, json=payload, headers=headers,
                             timeout=TTS_TIMEOUT, stream=True)
    except Exception as e:  # noqa: BLE001
        raise TTSError(f"TTS 请求失败 ({type(e).__name__}): {e}") from e

    try:
        if resp.status_code >= 400:
            raise TTSError(f"TTS HTTP {resp.status_code}: {resp.text[:300]}")
        chunks = []
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception as e:  # noqa: BLE001
                raise TTSError(f"TTS 响应非 JSON: {raw[:200]}") from e
            code = obj.get("code")
            if code == 0 and obj.get("data"):
                chunks.append(base64.b64decode(obj["data"]))
            elif code == 20000000:
                break
            elif code is not None and code != 0:
                raise TTSError(f"TTS 错误 code={code}: {obj.get('message')}")
    finally:
        resp.close()

    pcm = b"".join(chunks)
    if len(pcm) < 100:
        raise TTSError("TTS 返回音频为空")
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
           + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
           + b"data" + struct.pack("<I", len(pcm)))
    return _save_tts_out(hdr + pcm, ".wav")


def _save_tts_out(audio: bytes, ext: str) -> str:
    os.makedirs(VOICE_OUT_DIR, exist_ok=True)
    out = os.path.join(VOICE_OUT_DIR,
                       time.strftime("tts_%Y%m%d-%H%M%S") + f"-{os.getpid()}{ext}")
    with open(out, "wb") as f:
        f.write(audio)
    return out


def _tts_edge(text: str) -> str:
    """免费兜底引擎: Microsoft Edge 在线 TTS (无需 API key) -> MP3 路径。

    输出 mp3, 由 load_audio 统一走 ffmpeg 解码, 与 wav 同待遇。
    """
    import asyncio
    try:
        import edge_tts
    except ImportError as e:
        raise TTSError(f"edge-tts 未安装 (pip install edge-tts): {e}") from e

    rate = f"+{int((TTS_SPEED - 1) * 100)}%" if TTS_SPEED >= 1 \
        else f"{int((TTS_SPEED - 1) * 100)}%"
    comm = edge_tts.Communicate(text, TTS_EDGE_VOICE, rate=rate)

    async def _run():
        os.makedirs(VOICE_OUT_DIR, exist_ok=True)
        out = os.path.join(VOICE_OUT_DIR,
                           time.strftime("tts_%Y%m%d-%H%M%S")
                           + f"-{os.getpid()}-edge.mp3")
        await comm.save(out)
        return out

    try:
        out = asyncio.run(_run())
    except Exception as e:  # noqa: BLE001
        raise TTSError(f"edge TTS 失败 ({type(e).__name__}): {e}") from e
    if not os.path.exists(out) or os.path.getsize(out) < 100:
        raise TTSError("edge TTS 输出为空")
    return out


def tts_synthesize(text: str) -> str:
    """文本 -> 音频文件路径 (wav 或 mp3)。任何失败抛 TTSError
    (调用方不得继续操作微信)。

    引擎编排: TTS_ENGINE=volc 时先走火山方舟; 失败且
    TTS_FALLBACK_ENABLED=1 时自动降级 edge 免费引擎。
    TTS_ENGINE=edge 时直接走 edge。
    """
    if not TTS_ENABLED:
        raise TTSError("TTS 未启用 (TTS_ENABLED=0)")

    if TTS_ENGINE == "edge":
        log(f"[TTS] 引擎=edge 音色={TTS_EDGE_VOICE}")
        return _tts_edge(text)

    if not TTS_API_KEY:
        log("[TTS] 未配置 TTS_API_KEY, 主引擎 volc 不可用")
    else:
        try:
            return _tts_volc(text)
        except TTSError as e:
            if not TTS_FALLBACK_ENABLED:
                raise
            log(f"[TTS] volc 失败, 降级 edge 兜底: {e}")

    return _tts_edge(text)


def list_edge_voices(lang: str = "zh-CN") -> list:
    """列出 edge-tts 可用音色 (默认中文)。"""
    import asyncio
    import edge_tts
    return asyncio.run(edge_tts.list_voices())


# ---------------- CABLE 播放 ----------------

def play_to_cable(pa, dev_idx: int, pcm: np.ndarray, rate: int) -> float:
    """阻塞式把 pcm 写进 CABLE Input, 返回音频时长(秒)。"""
    dev = pa.get_device_info_by_index(dev_idx)
    dev_rate = int(dev["defaultSampleRate"]) or 48000
    pcm = resample(pcm, rate, dev_rate)
    stream, channels = None, 0
    for ch in (2, 1):
        try:
            stream = pa.open(format=pyaudio.paInt16, channels=ch, rate=dev_rate,
                             output=True, output_device_index=dev_idx,
                             frames_per_buffer=960)
            channels = ch
            break
        except Exception:
            stream = None
    if stream is None:
        raise RuntimeError("无法打开 CABLE Input 输出流 (VB-Cable 未装?)")
    data = pcm
    if channels == 2:
        stereo = np.empty(len(pcm) * 2, dtype=np.int16)
        stereo[0::2] = pcm
        stereo[1::2] = pcm
        data = stereo
    raw = data.tobytes()
    chunk = 960 * 2 * channels
    for i in range(0, len(raw), chunk):
        stream.write(raw[i:i + chunk])
    stream.stop_stream()
    stream.close()
    return len(pcm) / dev_rate


# ---------------- UI 自动化 (微信 4.1 视觉方案) ----------------

def _norm(s: str) -> str:
    """归一化: 只保留中英文数字, 去 emoji/空格/标点, 小写, 易混字归一。

    微信搜索下拉真实条目前常带图标字符 (群头像/表情),
    OCR 会读成 '』AI共学' / '📖AI共学' 等, 归一化后才能匹配 'AI共学'。
    易混字: OCR 常把 I 读成 l/1, 统一 l/1→i (两侧同归一, 不影响精确性)。
    """
    s = re.sub(r"[^0-9a-zA-Z一-鿿]", "", s).lower()
    return s.replace("l", "i").replace("1", "i")


def _row_is_real(arr, box) -> bool:
    """真实条目 vs 建议行 的结构判别: 行文本起点 x0。

    真实条目左侧有 40px 头像, 文本起点 x0≈175-225;
    建议行 (灰色小放大镜) 文本起点 x0≈120-130。
    实测: 建议行 122-125, 真实条目 177-223。阈值 155 稳定区分。
    (曾用头像颜色 std 判别, 对单色联系人头像失效, 弃用。)
    用于在 OCR 分区标题漏读时仍能排除"搜索网络结果"下的建议行
    (点建议行会进搜一搜网页, 绝不能点)。
    """
    x0 = min(p[0] for p in box)
    return x0 >= 155


def open_chat(contact: str):
    """搜索并打开联系人/群会话 (头像判别 + 归一化匹配 + 标题校验防误发)。

    匹配规则:
      - 只有"带彩色头像"的真实条目参与匹配; 建议行 (灰色放大镜,
        点击会进搜一搜) 一律排除。
      - 归一化 (去图标字符/空格/标点, 小写) 后完全一致优先, 其次互相包含
        (群名带图标前缀如 '』AI共学', 或搜索词是群名子串)。
      - 分区 (最常使用/联系人/群聊) 用于排序优先; 分区标题 OCR 偶尔漏读,
        漏读时按下拉自然顺序 (上→下) 取第一个匹配。
    """
    from autodial import wx41
    import pyperclip
    import ctypes
    h = wx41.focus_main()
    x, y, w, hh = wx41._rect(h)
    if w < 1500:  # 窗口被缩小时布局自适应, 坐标全乱; 最大化锁回校准布局
        ctypes.windll.user32.ShowWindow(h, 3)  # SW_MAXIMIZE
        time.sleep(0.8)
        h = wx41.focus_main()
        x, y, w, hh = wx41._rect(h)
    pyautogui.click(x + 216, y + 112)          # 主搜索栏 (实测坐标)
    time.sleep(0.8)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    pyperclip.copy(contact)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(1.8)

    from PIL import Image
    want = _norm(contact)
    last_sec, pick = "", None
    # 下拉可能超一屏, 未命中就在下拉内滚动继续找, 最多 3 屏。
    # OCR 前 2x 放大: 绿色匹配文字 ("原总") 与小号分区标题在原图常漏读,
    # 放大后稳定读出 (实测验证)。
    for screen in range(3):
        crop = pyautogui.screenshot(region=(x + 100, y + 150, 560, 620))
        arr = np.array(crop)
        big = np.array(crop.resize((crop.width * 2, crop.height * 2),
                                   Image.LANCZOS))
        result, _ = wx41._get_ocr()(big)
        rows = []
        for box, text, _sc in (result or []):
            text = (text or "").strip()
            if not text:
                continue
            xs = [p[0] / 2 for p in box]
            ys = [p[1] / 2 for p in box]
            rows.append({"text": text, "cx": sum(xs) / 4, "cy": sum(ys) / 4,
                         "box": list(zip(xs, ys))})
        rows.sort(key=lambda r: r["cy"])

        exact, fuzzy = [], []
        for r in rows:
            t = r["text"]
            if t in SECTIONS:
                last_sec = t
                continue
            if not _row_is_real(arr, r["box"]):
                continue                  # 无头像 = 建议行/杂项, 不点
            nt = _norm(t)
            if not nt or nt.startswith("包含"):
                continue  # "包含:xxx" 是成员模糊搜行, 点了开的是群不是目标
            pos = (last_sec, r["cx"] + 100, r["cy"] + 150)
            if nt == want:
                exact.append(pos)
            elif want in nt or nt in want:
                fuzzy.append(pos)

        def _pick(pool):
            for pref in ("最常使用", "联系人", "群聊"):
                p = next((c for c in pool if c[0] == pref), None)
                if p:
                    return p
            return pool[0] if pool else None

        pick = _pick(exact) or _pick(fuzzy)
        if pick:
            break
        if screen < 2:
            pyautogui.scroll(-4, x + 380, y + 450)   # 下拉内向下滚
            time.sleep(0.9)
    if pick is None:
        raise RuntimeError(f"搜索下拉未找到匹配的联系人/群: {contact}")
    log(f"[导航] 命中分区: {pick[0] or '(下拉首位)'}")
    pyautogui.click(x + int(pick[1]), y + int(pick[2]))
    time.sleep(1.5)

    from PIL import Image
    texts = ""
    for _ in range(4):
        # 顶栏标题扫描 (标题在搜索框右侧, 形如 "AI共学 (96)")。
        # 实测: 窄条截图 OCR 检测器失效 (2 字标题尤甚), 上下加 20px
        # 背景 padding + 3x 放大后稳定识别。
        crop = pyautogui.screenshot(region=(x + 380, y + 70,
                                            min(800, w - 460), 80))
        pad = Image.new("RGB", (crop.width, crop.height + 40), (26, 26, 26))
        pad.paste(crop, (0, 20))
        pad = pad.resize((pad.width * 3, pad.height * 3), Image.LANCZOS)
        texts = " ".join(l["text"] for l in wx41._ocr_lines(pad))
        if _norm(contact) in _norm(texts):
            log(f"[导航] 已打开会话: {contact}")
            return h
        time.sleep(0.8)
    raise RuntimeError(f"会话标题校验失败 (OCR: {texts[:60]}), 中止以防误发")


def locate_send(h):
    """OCR 找"发送"按钮, 返回窗口相对坐标; 找不到返回 None。"""
    from autodial import wx41
    x, y, w, hh = wx41._rect(h)
    crop = pyautogui.screenshot(region=(x + w - 420, y + hh - 200, 420, 200))
    for l in wx41._ocr_lines(crop):
        if l["text"] == "发送":
            return (w - 420 + int(l["cx"]), hh - 200 + int(l["cy"]))
    return None


# ---------------- 对外 API ----------------

def send_voice_msg(contact: str, source: str = None, text: str = None,
                   dry_run: bool = False) -> dict:
    """给微信联系人发送语音消息。

    contact: 微信联系人名 (搜索框能搜到的名字)
    source:  音频文件路径 (.wav/.silk/.mp3/.m4a/...) —— 与 text 二选一
    text:    文本内容, 先走 Seed TTS 合成再发送
    返回 {'ok', 'contact', 'duration', ...}; 失败抛异常。
    """
    from autodial import wx41
    from bridge import DefaultMicSwitch, find_wasapi_device

    if (source is None) == (text is None):
        raise ValueError("source 与 text 必须且只能提供一个")

    # ---- 阶段1: 音频准备 (失败绝不碰微信) ----
    if text is not None:
        text = text.strip()
        if not text:
            raise ValueError("text 为空")
        log(f"[TTS] 合成文本 ({len(text)} 字): {text[:50]}...")
        wav_path = tts_synthesize(text)   # 失败抛 TTSError
        log(f"[TTS] 合成成功: {wav_path}")
        src = wav_path
    else:
        src = source
    pcm, rate = load_audio(src)
    duration = len(pcm) / rate
    log(f"[音频] {os.path.basename(src)}: {duration:.1f}s @ {rate}Hz")
    if duration > MAX_VOICE_SECONDS:
        raise ValueError(f"音频 {duration:.0f}s 超过微信 {MAX_VOICE_SECONDS}s 上限, "
                         f"请切短后重试")
    if duration < 0.5:
        raise ValueError("音频太短 (<0.5s), 微信可能拒发")

    if dry_run:
        log("(dry-run) 音频已就绪, 不操作微信")
        return {"ok": True, "contact": contact, "duration": round(duration, 1),
                "dry_run": True}

    # ---- 阶段2: 微信 UI (音频已确认就绪) ----
    pa = pyaudio.PyAudio()
    cable = find_wasapi_device(pa, "CABLE Input", want_output=True)
    if cable is None:
        pa.terminate()
        raise RuntimeError("未找到 CABLE Input 设备 (VB-Cable 未安装?)")

    h = open_chat(contact)
    send = locate_send(h)
    if send is None:
        pa.terminate()
        raise RuntimeError("未找到发送按钮, 输入区状态异常, 中止")
    sx, sy = send
    btn_voice = (sx - 130, sy)          # 绿色语音按钮 (实测偏移)
    btn_arrow = (sx - 9, sy - 16)       # 录音态绿色发送箭头

    mic = DefaultMicSwitch()
    mic.activate()
    try:
        time.sleep(1.0)
        wx41.focus_main()
        h = wx41.find_main_hwnd()
        x, y, _w, _hh = wx41._rect(h)
        log("!! 2 秒后开录音+播放, 期间勿动鼠标键盘 (急停: 鼠标甩屏幕左上角)")
        time.sleep(2.0)

        pyautogui.click(x + btn_voice[0], y + btn_voice[1])
        log(f"[录音] 点击语音按钮, 等 {REC_START_S*1000:.0f}ms 启动")
        time.sleep(REC_START_S)

        played = play_to_cable(pa, cable, pcm, rate)
        log(f"[播放] {played:.1f}s 完成, 尾随 {TAIL_S*1000:.0f}ms")
        time.sleep(TAIL_S)

        pyautogui.click(x + btn_arrow[0], y + btn_arrow[1])
        log("[发送] 已点击发送箭头")
        time.sleep(2.0)
        return {"ok": True, "contact": contact,
                "duration": round(duration, 1), "source": src}
    finally:
        mic.restore()
        pa.terminate()


# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(
        description="微信发送语音消息 (文本TTS / wav / silk / mp3)",
        epilog='示例: sendvoice 小芳 "D:/a.silk"  |  sendvoice 小芳 --text "你好"')
    ap.add_argument("contact", nargs="?", default="", help="微信联系人名")
    ap.add_argument("audio", nargs="?", default="", help="音频文件路径 (wav/silk/mp3/...)")
    ap.add_argument("--text", default="", help="文本内容 (TTS 合成后发送)")
    ap.add_argument("--dry-run", action="store_true", help="只准备音频, 不操作微信")
    ap.add_argument("--list-voices", action="store_true",
                    help="列出 edge-tts 中文音色后退出")
    args = ap.parse_args()

    if args.list_voices:
        for v in list_edge_voices():
            if v.get("Locale", "").startswith("zh-"):
                print(f"{v['ShortName']:32s} {v.get('Gender', '')}", flush=True)
        return

    if not args.contact:
        log("[失败] 缺少联系人名, 例: sendvoice 小芳 --text \"你好\"")
        sys.exit(2)

    try:
        info = send_voice_msg(args.contact, source=args.audio or None,
                              text=args.text or None, dry_run=args.dry_run)
        log(f"[完成] {info}")
    except TTSError as e:
        log(f"[失败] TTS 错误 (未触碰微信): {e}")
        sys.exit(3)
    except Exception as e:  # noqa: BLE001
        log(f"[失败] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
