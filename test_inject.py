# -*- coding: utf-8 -*-
"""注入链路验证: 往 CABLE Input 写 440Hz 测试音, 从 CABLE Output 读回, 验证数据穿透"""
import threading
import time

import numpy as np
import pyaudiowpatch as pyaudio

pa = pyaudio.PyAudio()
wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
inject = read = None
for i in range(pa.get_device_count()):
    d = pa.get_device_info_by_index(i)
    if d["hostApi"] != wasapi:
        continue
    if "CABLE Input" in d["name"] and "Loopback" not in d["name"] and d["maxOutputChannels"] > 0:
        inject = i
    if "CABLE Output" in d["name"] and "Loopback" not in d["name"] and d["maxInputChannels"] > 0:
        read = i
print("inject idx =", inject, " read idx =", read)
rate_in = int(pa.get_device_info_by_index(inject)["defaultSampleRate"])
rate_out = int(pa.get_device_info_by_index(read)["defaultSampleRate"])
print("inject rate =", rate_in, " read rate =", rate_out)

# 1s 440Hz sine
t = np.linspace(0, 1.0, rate_in, endpoint=False)
tone = (np.sin(2 * np.pi * 440 * t) * 0.5 * 32767).astype(np.int16)
stereo = np.empty(len(tone) * 2, dtype=np.int16)
stereo[0::2] = tone
stereo[1::2] = tone

out_s = pa.open(format=pyaudio.paInt16, channels=2, rate=rate_in, output=True,
                output_device_index=inject, frames_per_buffer=960)
in_s = pa.open(format=pyaudio.paInt16, channels=2, rate=rate_out, input=True,
               input_device_index=read, frames_per_buffer=960)

def writer():
    for _ in range(rate_in // 960):
        out_s.write(stereo.tobytes())

th = threading.Thread(target=writer)
th.start()
time.sleep(0.2)
captured = []
for _ in range(rate_out // 960):
    captured.append(in_s.read(960, exception_on_overflow=False))
th.join()
out_s.close()
in_s.close()
pa.terminate()

data = np.frombuffer(b"".join(captured), dtype=np.int16)
energy = float(np.abs(data).mean())
print(f"samples={len(data)}  mean_abs_energy={energy:.1f}  (silence ~0, tone >1000)")
print("INJECT TEST:", "PASS (CABLE Input -> CABLE Output carries audio)" if energy > 1000 else "FAIL (no audio)")
