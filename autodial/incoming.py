# -*- coding: utf-8 -*-
"""Incoming-call watcher: detect an app's voice-call popup and auto-answer.

Detection strategy (no calibration needed):
  scan all UIA windows for a Button whose name matches the app's configured
  answer_button_names. The presence of such a button IS the incoming-call signal.

Answer strategy:
  1. UIA invoke/click_input on the found button (preferred);
  2. calibrated template/coordinate fallback (from autodial_calib_<app>.json).

Video calls are detected via the app's video_keywords and skipped unless
allow_video=True.
"""
from __future__ import annotations

import threading
import time

from adapters.base import AppConfig, find_incoming, guess_caller_name
from adapters import get_app, DEFAULT_APP


class IncomingWatcher(threading.Thread):
    """轮询来电弹窗, 发现即接听, 并通过 on_answered(caller_name) 通知桥接。"""

    def __init__(self, on_answered=None, allow_video: bool = False,
                 poll_sec: float = 1.0, cooldown_sec: float = 12.0,
                 app: str | AppConfig = DEFAULT_APP):
        super().__init__(daemon=True, name="IncomingWatcher")
        self.cfg = app if isinstance(app, AppConfig) else get_app(app)
        self.on_answered = on_answered
        self.allow_video = allow_video
        self.poll_sec = poll_sec
        self.cooldown_sec = cooldown_sec
        self.stop_event = threading.Event()
        self._busy_until = 0.0

    def set_busy(self, sec: float | None = None) -> None:
        """通话进行中/刚挂断时暂停检测, 防止误触。"""
        self._busy_until = time.time() + (sec if sec is not None else self.cooldown_sec)

    def run(self) -> None:
        print(f"[INCOMING] 来电监听已启动 ({self.cfg.display_name}, poll={self.poll_sec}s, "
              f"video={'允许' if self.allow_video else '不接'})", flush=True)
        while not self.stop_event.is_set():
            time.sleep(self.poll_sec)
            if time.time() < self._busy_until:
                continue
            try:
                hit = self._detect()
            except Exception as e:  # noqa: BLE001
                print(f"[INCOMING] 检测异常: {e}", flush=True)
                continue
            if not hit:
                continue
            popup, btn, is_video = hit
            if is_video and not self.allow_video:
                print("[INCOMING] 检测到【视频】来电, 已配置不接, 跳过", flush=True)
                self.set_busy(8)
                continue
            if self.cfg.ui_engine == "vision41":
                from autodial import wx41
                caller = wx41.incoming_caller_name() or "对方"
            else:
                caller = guess_caller_name(popup, self.cfg)
            print(f"[INCOMING] 检测到来电: {caller} ({'视频' if is_video else '语音'}), 自动接听...",
                  flush=True)
            ok = self._answer(popup, btn)
            if ok:
                print(f"[INCOMING] 已接听: {caller}", flush=True)
                self.set_busy()
                if self.on_answered:
                    try:
                        self.on_answered(caller)
                    except Exception as e:  # noqa: BLE001
                        print(f"[INCOMING] on_answered 回调异常: {e}", flush=True)
            else:
                print("[INCOMING] 接听失败, 等待重试", flush=True)
                self.set_busy(5)

    def _detect(self):
        """vision41: 全屏找大绿圆; 否则: UIA 扫描接听按钮。

        vision41 返回 (popup=None, btn='green_circle', is_video=False)。
        """
        if self.cfg.ui_engine in ("vision41", "wecom_vision"):
            # 来电大绿圆 (微信4.1/企微 视觉方案通用)
            from autodial import wx41
            import pyautogui
            pos = wx41._find_circle(pyautogui.screenshot(), "green")
            if pos:
                return None, "green_circle", False
            return None
        return find_incoming(self.cfg)

    def _answer(self, popup, btn) -> bool:
        # 0) vision41: 大绿圆
        if btn == "green_circle":
            from autodial import wx41
            return bool(wx41.answer_incoming().get("ok"))
        # 1) UIA 直接点按钮
        try:
            try:
                btn.invoke()
            except Exception:
                btn.click_input()
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[INCOMING] UIA 点击失败({e}), 尝试校准坐标回退", flush=True)
        # 2) 校准回退 (answer_offset 相对应用主窗口)
        try:
            from adapters.base import find_main_window
            from autodial.taskfile import load_calib
            calib = load_calib(self.cfg.key) or {}
            off = calib.get("answer_offset")
            if off:
                win = find_main_window(self.cfg)
                if win is not None:
                    r = win.rectangle()
                    import pyautogui
                    pyautogui.click(int(r.left) + int(off["x"]), int(r.top) + int(off["y"]))
                    return True
        except Exception as e:  # noqa: BLE001
            print(f"[INCOMING] 坐标回退失败: {e}", flush=True)
        return False
