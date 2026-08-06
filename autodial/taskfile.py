"""Shared paths and the "current task" handoff file between autodial and bridge.

autodial writes data/current_task.json right before placing a call;
bridge.py detects the change and injects contact+task into Realtime instructions.
"""
from __future__ import annotations

import json
import os
import time

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TASK_FILE = os.path.join(DATA_DIR, "current_task.json")
CALIB_FILE = os.path.join(DATA_DIR, "autodial_calib.json")


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def write_current_task(contact: str, task: str, note: str = "") -> None:
    ensure_data_dir()
    payload = {
        "contact": contact,
        "task": task,
        "note": note,
        "seq": int(time.time() * 1000),   # bridge 用它判断文件是否更新
        "written_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(TASK_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def read_current_task() -> dict | None:
    try:
        with open(TASK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def clear_current_task() -> None:
    """把任务文件重置为空任务(seq=0); bridge 检测到 seq 归零后还原干净 instructions。
    用覆盖写而非删除文件, 更原子, 也避免文件句柄竞争。"""
    ensure_data_dir()
    try:
        with open(TASK_FILE, "w", encoding="utf-8") as f:
            json.dump({"seq": 0, "contact": "", "task": "",
                       "cleared_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False)
    except Exception:
        pass


def load_calib() -> dict | None:
    try:
        with open(CALIB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_calib(calib: dict) -> None:
    ensure_data_dir()
    with open(CALIB_FILE, "w", encoding="utf-8") as f:
        json.dump(calib, f, ensure_ascii=False, indent=2)
