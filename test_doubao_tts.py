# -*- coding: utf-8 -*-
"""test_doubao_tts — 豆包 Seed TTS 原生接口调试脚本 (2026-08-13)

接口: POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
鉴权 (新版控制台, 非 Bearer):
  X-Api-Key:          控制台 API Key
  X-Api-Resource-Id:  模型版本, 必须与音色配套:
                        *_uranus_bigtts / saturn_*  -> seed-tts-2.0
                        *_mars_bigtts / *_moon_bigtts -> seed-tts-1.0
                        S_xxx (复刻音色)             -> seed-icl-2.0
  X-Api-Request-Id:   uuid (可选, 建议带, 排查问题用)
请求体: {"user":{"uid"}, "req_params":{"text","speaker","audio_params":{...}}}
响应: NDJSON 流, 每行一个 JSON:
  {"code":0,"data":"<base64音频片段>"}   -> 解码拼接
  {"code":20000000,"message":"ok",...}   -> 合成结束
  其他 code                               -> 错误 (45000000=音色未授权等)

用法:
  .venv/Scripts/python.exe test_doubao_tts.py
  .venv/Scripts/python.exe test_doubao_tts.py --speaker zh_female_tvbnv_uranus_bigtts
  .venv/Scripts/python.exe test_doubao_tts.py --text "你好" --out data/voice_out/t.wav
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import sys
import time
import uuid

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))

# 默认从 .env 读 key (与 voice_msg 一致), 也可 --key 覆盖
def _env_key() -> str:
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line.startswith("TTS_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


def resource_id_for(speaker: str) -> str:
    """按音色 ID 特征路由 Resource-Id (配错报 55000000)。"""
    if speaker.startswith("S_"):
        return "seed-icl-2.0"
    if "_uranus_" in speaker or speaker.startswith("saturn_"):
        return "seed-tts-2.0"
    return "seed-tts-1.0"


def pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    hdr = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " \
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) \
        + b"data" + struct.pack("<I", len(pcm))
    return hdr + pcm


def synthesize(text: str, speaker: str, api_key: str,
               out_path: str, rate: int = 24000,
               timeout: float = 60.0) -> dict:
    rid = resource_id_for(speaker)
    req_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": rid,
        "X-Api-Request-Id": req_id,
    }
    payload = {
        "user": {"uid": "wechatphone"},
        "req_params": {
            "text": text,
            "speaker": speaker,
            "audio_params": {"format": "pcm", "sample_rate": rate},
        },
    }
    print(f"[req] resource={rid} speaker={speaker} reqid={req_id}", flush=True)
    t0 = time.time()
    chunks: list[bytes] = []
    usage = None
    finished = False

    with requests.post(URL, headers=headers, json=payload,
                       timeout=timeout, stream=True) as resp:
        logid = resp.headers.get("X-Tt-Logid", "")
        if resp.status_code >= 400:
            body = resp.text[:500]
            raise RuntimeError(f"HTTP {resp.status_code} logid={logid}: {body}")
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            obj = json.loads(raw)
            code = obj.get("code")
            if code == 0 and obj.get("data"):
                chunks.append(base64.b64decode(obj["data"]))
            elif code == 20000000:
                usage = obj.get("usage")
                finished = True
                break
            elif code is not None and code != 0:
                raise RuntimeError(
                    f"TTS 错误 code={code} message={obj.get('message')} "
                    f"logid={logid}")
    dt = time.time() - t0
    if not finished:
        print("[warn] 流结束未见 code=20000000 (可能中途断开)", flush=True)
    pcm = b"".join(chunks)
    if len(pcm) < 100:
        raise RuntimeError("返回音频为空")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(pcm_to_wav(pcm, rate))
    dur = len(pcm) / 2 / rate
    print(f"[ok] {dur:.1f}s 音频, 耗时 {dt:.2f}s, "
          f"计费字符={usage or 'n/a'}, 已存 {out_path}", flush=True)
    return {"duration": dur, "elapsed": dt, "out": out_path, "logid": logid}


def main():
    ap = argparse.ArgumentParser(description="豆包 Seed TTS 原生接口调试")
    ap.add_argument("--key", default=_env_key(), help="API Key (默认读 .env TTS_API_KEY)")
    ap.add_argument("--speaker", default="zh_female_jiaochuannv_uranus_bigtts")
    ap.add_argument("--text", default="你好, 这是一条豆包语音合成测试, 声音清楚吗?")
    ap.add_argument("--out", default=os.path.join(
        ROOT, "data", "voice_out", "test_doubao.wav"))
    args = ap.parse_args()
    if not args.key:
        print("[fail] 没有 API Key: .env 无 TTS_API_KEY, 也没传 --key", flush=True)
        sys.exit(2)
    try:
        synthesize(args.text, args.speaker, args.key, args.out)
    except Exception as e:  # noqa: BLE001
        print(f"[fail] {type(e).__name__}: {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
