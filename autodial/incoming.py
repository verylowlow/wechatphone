# -*- coding: utf-8 -*-
"""Incoming-call watcher: detect WeChat voice-call popups and auto-answer.

Detection strategy (no calibration needed):
  scan all UIA windows for a Button whose name contains 接听/接受/接起.
  The presence of such a button IS the incoming-call signal.

Answer strategy:
  1. UIA invoke/click_input on the found button (preferred);
  2. calibrated template/coordinate fallback (from autodial_calib.json).

Video calls are detected by "视频" in the popup text and skipped unless
allow_video=True.
"""
from __future__ import annotations

import threading
import time

ANSWER_NAMES = ("接听", "接受", "接起")


def _buttons(win):
    try:
        return win.descendants(control_type="Button")
    except Exception:
        return []


def find_incoming():
    """Scan UIA windows for an incoming-call popup.

    Returns (popup_window, answer_button, is_video) or None.
    """
    from pywinauto import Desktop
    try:
        windows = Desktop(backend="uia").windows()
    except Exception as e:  # noqa: BLE001
        print(f"[INCOMING] 枚举窗口失败: {e}", flush=True)
        return None
    for w in windows:
        try:
            title = w.window_text() or ""
        except Exception:
            continue
        for b in _buttons(w):
            try:
                name = (b.window_text() or "").strip()
            except Exception:
                continue
            if name and any(k in name for k in ANSWER_NAMES):
                # 弹窗全文里出现"视频"则视为视频来电
                is_video = False
                try:
                    is_video = "视频" in (title + " " + w.text_block())
                except Exception:
                    is_video = "视频" in title
                return w, b, is_video
    return None


def guess_caller_name(popup) -> str:
    """从来电弹窗提取主叫人昵称 (尽力而为, 失败返回 '对方')."""
    try:
        title = (popup.window_text() or "").strip()
        for noise in ("微信语音通话", "微信视频通话", "语音通话", "视频通话",
                      "邀请你", "邀请与您", "来电"):
            title = title.replace(noise, "")
        title = title.strip(" -·|:：\t")
        if title and len(title) <= 30:
            return title
    except Exception:
        pass
    return "对方"


class IncomingWatcher(threading.Thread):
    """轮询来电弹窗, 发现即接听, 并通过 on_answered(caller_name) 通知桥接。"""

    def __init__(self, on_answered=None, allow_video: bool = False,
                 poll_sec: float = 1.0, cooldown_sec: float = 12.0):
        super().__init__(daemon=True, name="IncomingWatcher")
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
        print(f"[INCOMING] 来电监听已启动 (poll={self.poll_sec}s, "
              f"video={'允许' if self.allow_video else '不接'})", flush=True)
        while not self.stop_event.is_set():
            time.sleep(self.poll_sec)
            if time.time() < self._busy_until:
                continue
            try:
                hit = find_incoming()
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
            caller = guess_caller_name(popup)
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

    def _answer(self, popup, btn) -> bool:
        # 1) UIA 直接点按钮
        try:
            try:
                btn.invoke()
            except Exception:
                btn.click_input()
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[INCOMING] UIA 点击失败({e}), 尝试校准坐标回退", flush=True)
        # 2) 校准回退 (answer_offset 相对微信主窗口)
        try:
            from autodial.taskfile import load_calib
            from autodial.dialer import WeChatDialer
            calib = load_calib() or {}
            off = calib.get("answer_offset")
            if off:
                w = WeChatDialer.__new__(WeChatDialer)  # 不触发校准校验
                win = w._find_wechat_window()
                r = win.rectangle()
                import pyautogui
                pyautogui.click(int(r.left) + int(off["x"]), int(r.top) + int(off["y"]))
                return True
        except Exception as e:  # noqa: BLE001
            print(f"[INCOMING] 坐标回退失败: {e}", flush=True)
        return False
