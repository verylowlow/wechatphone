# -*- coding: utf-8 -*-
"""纯文本 API 验证: 不依赖任何音频, 直接向阿里云 Realtime 注入一条文本并请求回复。
用于隔离判断: API 本身能否正常生成语音+文本回复。

用法:
  .venv/Scripts/python.exe test_api_text.py
  .venv/Scripts/python.exe test_api_text.py --text "今天北京天气怎么样"
"""
import argparse
import asyncio
import base64
import json
import sys

import websockets

# 复用 bridge.py 的配置加载与 URL 构造
from bridge import API_KEY, BASE_URL, MODEL, VOICE, build_ws_url


async def run(text: str):
    url = build_ws_url(BASE_URL, MODEL)
    print(f"[连接] {url}")
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "x-dashscope-dataInspection": "disable",
    }
    audio_bytes = 0
    async with websockets.connect(
        url, additional_headers=headers, open_timeout=15, max_size=8 * 1024 * 1024
    ) as ws:
        # 1. 配置会话
        await ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": VOICE,
                "input_audio_format": "pcm",
                "output_audio_format": "pcm",
                "turn_detection": None,  # push-to-talk: 完全手动控制, 最确定
            },
        }, ensure_ascii=False))
        while True:
            m = json.loads(await ws.recv())
            if m.get("type") == "session.updated":
                print("[会话] 就绪")
                break
            if m.get("type") == "error":
                print("[错误]", m); sys.exit(1)

        # 2. 注入用户文本轮次
        await ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        }, ensure_ascii=False))
        print(f"[注入] 用户文本: {text}")

        # 3. 触发推理
        await ws.send(json.dumps({
            "type": "response.create",
            "response": {"modalities": ["text", "audio"]},
        }))
        print("[触发] response.create")

        # 4. 收事件直到 response.done
        transcript = []
        while True:
            m = json.loads(await ws.recv())
            t = m.get("type", "")
            if t == "response.audio.delta":
                audio_bytes += len(base64.b64decode(m["delta"]))
            elif t == "response.audio_transcript.delta":
                print(m.get("delta", ""), end="", flush=True)
                transcript.append(m.get("delta", ""))
            elif t == "response.audio_transcript.done":
                print()
            elif t == "response.done":
                status = (m.get("response") or {}).get("status", "")
                print(f"[完成] status={status}  音频总字节={audio_bytes} (~{audio_bytes/48000:.1f}s@24k)")
                break
            elif t == "error":
                print("[错误]", m)
                break
    print("\n=== 结论 ===")
    if audio_bytes > 0:
        print("API 正常: 成功生成语音回复。问题出在音频捕获链路。")
    else:
        print("API 未生成语音。需检查模型/参数。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="你好，请用一句话介绍一下你自己。")
    args = ap.parse_args()
    if not API_KEY:
        print("错误: 未配置 ALIYUN_REALTIME_API_KEY")
        sys.exit(1)
    asyncio.run(run(args.text))


if __name__ == "__main__":
    main()
