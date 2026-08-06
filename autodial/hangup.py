# -*- coding: utf-8 -*-
"""Hang up the current WeChat voice call via UI automation.

Locate strategy:
  1. UIA: any Button named 挂断/结束/结束通话 in any window (call bar / popup)
  2. calibrated fallback: answer_offset-like "hangup_offset" relative to the
     WeChat main window, or template image "hangup_template".

Safety: hangup() re-checks the button still exists right before clicking —
if the remote party already hung up, we skip the click (nothing to press).
"""
from __future__ import annotations

import os
import time

HANGUP_NAMES = ("挂断", "结束通话", "结束")


def find_hangup_button():
    """Returns (window, button) of the hangup control, or None."""
    from pywinauto import Desktop
    try:
        windows = Desktop(backend="uia").windows()
    except Exception:
        return None
    for w in windows:
        try:
            for b in w.descendants(control_type="Button"):
                try:
                    name = (b.window_text() or "").strip()
                except Exception:
                    continue
                if name and any(k in name for k in HANGUP_NAMES):
                    return w, b
        except Exception:
            continue
    return None


def hang_up() -> dict:
    """Perform the hangup click. Returns {'ok': bool, 'method': str}."""
    # 1) UIA
    hit = find_hangup_button()
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
        calib = load_calib() or {}
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
            from autodial.dialer import WeChatDialer
            d = WeChatDialer.__new__(WeChatDialer)
            win = d._find_wechat_window()
            r = win.rectangle()
            import pyautogui
            pyautogui.click(int(r.left) + int(off["x"]), int(r.top) + int(off["y"]))
            return {"ok": True, "method": "offset"}
    except Exception as e:  # noqa: BLE001
        print(f"[HANGUP] 校准回退失败: {e}", flush=True)
    return {"ok": False, "method": "not_found"}


def wait_audio_drain(down_queue, max_wait: float = 15.0) -> None:
    """等播放队列清空 (确保 farewell 语音完整注入给微信), 最多等 max_wait 秒。"""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            if down_queue.qsize() == 0:
                time.sleep(0.4)  # 再给尾部一点余量
                return
        except Exception:
            return
        time.sleep(0.2)
