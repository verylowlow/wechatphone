"""Shared paths and the "current task" handoff file between autodial and bridge.

autodial writes data/current_task.json right before placing a call;
bridge.py detects the change and injects contact+task into Realtime instructions.

Calibration files are per-app: data/autodial_calib_<app>.json
(legacy data/autodial_calib.json is auto-read as the wechat file).
"""
from __future__ import annotations

import json
import os
import time

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TASK_FILE = os.path.join(DATA_DIR, "current_task.json")
# 旧版单应用校准文件 (微信), 保留向后兼容
LEGACY_CALIB_FILE = os.path.join(DATA_DIR, "autodial_calib.json")


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def calib_path(app: str) -> str:
    """校准文件路径 (按应用分存)。wechat 兼容旧文件名。"""
    app = (app or "wechat").strip().lower()
    if app == "wechat" and os.path.exists(LEGACY_CALIB_FILE):
        return LEGACY_CALIB_FILE
    return os.path.join(DATA_DIR, f"autodial_calib_{app}.json")


def tmpl_path(app: str, kind: str) -> str:
    """按钮模板图路径 (按应用+用途分存): kind = "call" | "hangup"."""
    app = (app or "wechat").strip().lower()
    if app == "wechat":
        # 与旧版文件名保持一致
        name = "call_button_template.png" if kind == "call" else "hangup_button_template.png"
    else:
        name = f"{app}_{kind}_button_template.png"
    return os.path.join(DATA_DIR, name)


def write_current_task(contact: str, task: str, note: str = "", app: str = "wechat",
                       opening: str = "") -> None:
    """opening: 任务发起人指定的外呼开场白; 空则 bridge 用 OUTBOUND_DEFAULT_OPENING 回退。"""
    ensure_data_dir()
    payload = {
        "app": app,
        "contact": contact,
        "task": task,
        "note": note,
        "opening": opening,
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


def load_calib(app: str = "wechat") -> dict | None:
    try:
        with open(calib_path(app), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_calib(calib: dict, app: str = "wechat") -> None:
    ensure_data_dir()
    with open(calib_path(app), "w", encoding="utf-8") as f:
        json.dump(calib, f, ensure_ascii=False, indent=2)
