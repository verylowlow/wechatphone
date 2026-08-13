# -*- coding: utf-8 -*-
"""微信"发送语音消息"功能 (POC v4, 2026-08-12 实测通过 @ 微信 4.1)

验证结论:
  ① 微信语音消息录音吃 CABLE Output (= 系统默认麦克风), 与语音通话同源;
  ② 绿色按钮(发送左侧, 波纹图标) = 语音消息入口: 单击开始录音
     (圆条出现动态波形), 绿色箭头 = 发送, X = 取消;
  ③ 正确时序: 先切系统默认麦到 CABLE Output → 点绿按钮开录音
     → CABLE 播放音频 → 点绿箭头发送 → 还原默认麦。
     (先开录音后切麦会录进前半段环境静音。)

易混淆按钮 (勿用错):
  - 工具栏中部话筒图标 = 语音输入法 (ASR→文字落输入框);
  - Ctrl+Win = 微信"语音输入文字" (全局 ASR 弹窗);
  - 右 Alt 本机无响应 (未绑定), 不用。

用法:
  .venv/Scripts/python.exe tools/poc_voice_msg.py                      # 给 助理GPT 发
  .venv/Scripts/python.exe tools/poc_voice_msg.py --contact 张三 --text 你好
  .venv/Scripts/python.exe tools/poc_voice_msg.py --dry-run            # 只导航
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pyaudiowpatch as pyaudio  # noqa: E402
import pyautogui  # noqa: E402

pyautogui.FAILSAFE = True
SECTIONS = ("搜索网络结果", "最常使用", "联系人", "群聊", "功能", "收藏",
            "视频号", "朋友圈", "文章")


def log(*a):
    print(*a, flush=True)


# ---------------- 音频 ----------------

def load_wav(path):
    with wave.open(path, "rb") as w:
        ch, rate = w.getnchannels(), w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return data, rate


def resample(pcm, sr, dr):
    if sr == dr:
        return pcm
    return np.interp(np.linspace(0, 1, int(len(pcm) * dr / sr), endpoint=False),
                     np.linspace(0, 1, len(pcm), endpoint=False),
                     pcm.astype(np.float64)).astype(np.int16)


def play_to_cable(pa, dev_idx, pcm, rate):
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
        raise RuntimeError("无法打开 CABLE Input")
    data = pcm
    if channels == 2:
        stereo = np.empty(len(pcm) * 2, dtype=np.int16)
        stereo[0::2] = pcm
        stereo[1::2] = pcm
        data = stereo
    raw = data.tobytes()
    for i in range(0, len(raw), 960 * 2 * channels):
        stream.write(raw[i:i + 960 * 2 * channels])
    stream.stop_stream()
    stream.close()
    return len(pcm) / dev_rate


# ---------------- UI ----------------

def open_chat(contact):
    """搜索打开联系人会话 (OCR 分区定位 + 标题校验)。返回 hwnd。"""
    from autodial import wx41
    import pyperclip
    h = wx41.focus_main()
    x, y, w, hh = wx41._rect(h)
    pyautogui.click(x + 216, y + 112)
    time.sleep(0.8)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    pyperclip.copy(contact)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(1.8)

    crop = pyautogui.screenshot(region=(x + 100, y + 150, 560, 950))
    lines = wx41._ocr_lines(crop)
    last_sec, cands = "", []
    for l in lines:
        t = l["text"].strip()
        if t in SECTIONS:
            last_sec = t
            continue
        if t == contact:
            cands.append((last_sec, l["cx"] + 100, l["cy"] + 150))
    pick = None
    for pref in ("最常使用", "联系人", "群聊"):
        pick = next((c for c in cands if c[0] == pref), None)
        if pick:
            break
    if pick is None and cands:
        pick = cands[0]
    if pick is None:
        raise RuntimeError(f"搜索下拉未找到完全一致的 {contact}")
    pyautogui.click(x + int(pick[1]), y + int(pick[2]))
    time.sleep(1.5)
    for _ in range(4):
        crop = pyautogui.screenshot(region=(x + 560, y + 55, 900, 100))
        texts = " ".join(l["text"] for l in wx41._ocr_lines(crop))
        if contact in texts:
            log(f"[导航] 已打开会话: {contact}")
            return h
        time.sleep(0.8)
    raise RuntimeError(f"会话标题校验失败 (OCR: {texts[:60]}), 中止以防误发")


def locate_send(h):
    """OCR 找"发送"按钮, 返回窗口相对坐标。"""
    from autodial import wx41
    x, y, w, hh = wx41._rect(h)
    crop = pyautogui.screenshot(region=(x + w - 420, y + hh - 200, 420, 200))
    for l in wx41._ocr_lines(crop):
        if l["text"] == "发送":
            return (w - 420 + int(l["cx"]), hh - 200 + int(l["cy"]))
    return None


def main():
    ap = argparse.ArgumentParser(description="微信发送语音消息 (CABLE 注入)")
    ap.add_argument("--wav", default=os.path.join(ROOT, "tools", "test_voice.wav"))
    ap.add_argument("--contact", default="助理GPT")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rec-start", type=float, default=0.6,
                    help="开录音后多少秒开始播音频 (录音启动延迟)")
    ap.add_argument("--tail", type=float, default=0.4, help="播完后多少秒再发送")
    args = ap.parse_args()

    from autodial import wx41
    from bridge import DefaultMicSwitch, find_wasapi_device

    pa = pyaudio.PyAudio()
    cable = find_wasapi_device(pa, "CABLE Input", want_output=True)
    if cable is None:
        log("!! 未找到 CABLE Input")
        sys.exit(1)
    pcm, wav_rate = load_wav(args.wav)
    log(f"[音频] {len(pcm)/wav_rate:.1f}s, 目标={args.contact}")

    h = open_chat(args.contact)
    send = locate_send(h)
    if send is None:
        log("!! 未找到发送按钮 (输入区异常)")
        pa.terminate()
        sys.exit(2)
    sx, sy = send
    # 实测偏移: 绿色语音按钮=发送左移~130px; 录音态绿箭头≈发送位置
    btn_voice = (sx - 130, sy)
    btn_send_arrow = (sx - 9, sy - 16)
    log(f"[导航] 发送按钮 @ {send}, 语音按钮 @ {btn_voice}")

    if args.dry_run:
        log("(dry-run) 到此为止")
        pa.terminate()
        return

    # ---- 正确时序: 先切麦 → 开录音 → 播音频 → 发送 ----
    mic = DefaultMicSwitch()
    mic.activate()
    try:
        time.sleep(1.0)
        wx41.focus_main()
        h = wx41.find_main_hwnd()
        x, y, w, hh = wx41._rect(h)

        log("!! 2 秒后开录音+播放, 期间勿动鼠标键盘 (急停: 鼠标甩左上角)")
        time.sleep(2.0)

        pyautogui.click(x + btn_voice[0], y + btn_voice[1])
        log(f"[录音] 点击语音按钮, 等 {args.rec_start}s")
        time.sleep(args.rec_start)

        played = play_to_cable(pa, cable, pcm, wav_rate)
        log(f"[PLAY] 播完 {played:.1f}s, 尾随 {args.tail}s")
        time.sleep(args.tail)

        pyautogui.click(x + btn_send_arrow[0], y + btn_send_arrow[1])
        log("[发送] 点击绿箭头")
        time.sleep(2.0)
        log("===== 完成: 去微信会话里播放最新语音条确认内容 =====")
    except KeyboardInterrupt:
        log("!! 用户中断")
    finally:
        mic.restore()
        pa.terminate()


if __name__ == "__main__":
    main()
