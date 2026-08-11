# -*- coding: utf-8 -*-
"""Hang up the current voice call via UI automation (app-agnostic, adapter-driven).

Locate strategy:
  1. UIA: any Button matching cfg.hangup_button_names in any window (call bar / popup)
  2. calibrated fallback: "hangup_offset" relative to the app main window,
     or template image "hangup_template".

Safety: hang_up() re-checks the button still exists right before clicking —
if the remote party already hung up, we skip the click (nothing to press).
"""
from __future__ import annotations

import os
import time

from adapters.base import AppConfig, find_hangup_button, find_main_window
from adapters import get_app, DEFAULT_APP


def hang_up(app: str | AppConfig = DEFAULT_APP) -> dict:
    """Perform the hangup click. Returns {'ok': bool, 'method': str}."""
    cfg = app if isinstance(app, AppConfig) else get_app(app)
    # 0) 微信 4.1+ 视觉方案: 通话窗口大红圆
    if cfg.ui_engine == "vision41":
        try:
            from autodial import wx41
            return wx41.hang_up()
        except Exception as e:  # noqa: BLE001
            print(f"[HANGUP] vision41 异常: {e}", flush=True)
            return {"ok": False, "method": "vision41_error"}
    # 0b) 企业微信视觉方案: 通话界面大红圆, 回退 OCR 挂断文本
    if cfg.ui_engine == "wecom_vision":
        try:
            from autodial import wecom_ui
            return wecom_ui.hang_up()
        except Exception as e:  # noqa: BLE001
            print(f"[HANGUP] wecom 异常: {e}", flush=True)
            return {"ok": False, "method": "wecom_error"}
    # 0c) 钉钉视觉方案: OCR"挂断"上方红圆, 回退红连通域
    if cfg.ui_engine == "dingtalk_vision":
        try:
            from autodial import dingtalk_ui
            return dingtalk_ui.hang_up()
        except Exception as e:  # noqa: BLE001
            print(f"[HANGUP] dingtalk 异常: {e}", flush=True)
            return {"ok": False, "method": "dingtalk_error"}
    # 1) UIA
    hit = find_hangup_button(cfg)
    if hit:
        _w, btn = hit
        try:
            try:
                btn.invoke()
            except Exception:
                btn.click_input()
            return {"ok": True, "method": "uia"}
        except Exception as e:  # noqa: BLE001
            print(f"[HANGUP] UIA 点击失败: {e}", flush=True)
    # 2) 校准回退
    try:
        from autodial.taskfile import load_calib
        calib = load_calib(cfg.key) or {}
        off = calib.get("hangup_offset")
        tmpl = calib.get("hangup_template")
        if tmpl and os.path.exists(tmpl):
            import pyautogui
            box = pyautogui.locateOnScreen(tmpl, confidence=0.8)
            if box:
                c = pyautogui.center(box)
                pyautogui.click(c.x, c.y)
                return {"ok": True, "method": "template"}
        if off:
            win = find_main_window(cfg)
            if win is not None:
                r = win.rectangle()
                import pyautogui
                pyautogui.click(int(r.left) + int(off["x"]), int(r.top) + int(off["y"]))
                return {"ok": True, "method": "offset"}
    except Exception as e:  # noqa: BLE001
        print(f"[HANGUP] 校准回退失败: {e}", flush=True)
    return {"ok": False, "method": "not_found"}


def wait_audio_drain(down_queue, max_wait: float = 15.0) -> None:
    """等播放队列清空 (确保 farewell 语音完整注入给应用), 最多等 max_wait 秒。"""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            if down_queue.qsize() == 0:
                time.sleep(0.4)  # 再给尾部一点余量
                return
        except Exception:
            return
        time.sleep(0.2)
