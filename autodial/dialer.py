"""WeChatDialer: UI-automation voice-call placement for PC WeChat.

Flow (per contact):
  1. find & activate the WeChat main window (pywinauto/UIA)
  2. Ctrl+F to focus the search box
  3. paste contact name via clipboard (中文输入法安全)
  4. Enter -> open the chat
  5. click the voice-call button: template match first (calibrated screenshot),
     window-offset coordinate fallback
Calibration data lives in data/autodial_calib.json (see calibrate.py).

NOTE: UI automation is inherently brittle across WeChat versions. That's why
every step is logged, `dry_run` skips clicks, and the batch runner waits for
the previous call to end via the calllog store.
"""
from __future__ import annotations

import os
import time

from autodial.taskfile import load_calib

SEARCH_HOTKEY = "^f"          # 微信主界面搜索快捷键
SEARCH_RESULT_WAIT = 1.2      # 输入联系人后等待搜索结果
OPEN_CHAT_WAIT = 1.5          # Enter 打开会话后等待界面渲染
POST_DIAL_WAIT = 2.0


class DialError(Exception):
    pass


class WeChatDialer:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.calib = load_calib()
        if self.calib is None or not self.calib.get("calibrated_at"):
            raise DialError("未找到有效校准数据 data/autodial_calib.json, 请先运行: "
                            "python -m autodial.cli calibrate")

    # ---------- window management ----------

    def _find_wechat_window(self):
        """Return the active pywinauto UIA wrapper for the WeChat main window."""
        from pywinauto import Desktop
        candidates = []
        try:
            for w in Desktop(backend="uia").windows():
                try:
                    title = w.window_text()
                except Exception:
                    continue
                if title and ("微信" in title or "WeChat" in title):
                    candidates.append(w)
        except Exception as e:  # noqa: BLE001
            raise DialError(f"枚举窗口失败: {e}")
        if not candidates:
            raise DialError("未找到微信窗口, 请确认微信已登录且主窗口未最小化")
        # 优先精确标题 "微信"
        for w in candidates:
            if w.window_text().strip() == "微信":
                return w
        return candidates[0]

    def _activate(self, w) -> None:
        try:
            w.restore()
        except Exception:
            pass
        w.set_focus()
        time.sleep(0.5)

    # ---------- dial ----------

    def dial(self, contact: str, task: str = "", note: str = "") -> dict:
        """Place a voice call to `contact`. Returns an info dict."""
        if task:
            from autodial.taskfile import write_current_task
            write_current_task(contact, task, note)
            print(f"[AUTODIAL] 任务已写入 current_task.json: {contact} -> {task[:40]}", flush=True)

        print(f"[AUTODIAL] 查找微信窗口...", flush=True)
        w = self._find_wechat_window()
        print(f"[AUTODIAL] 找到窗口: '{w.window_text()}'", flush=True)
        self._activate(w)

        # 1) 搜索联系人
        print(f"[AUTODIAL] Ctrl+F 打开搜索", flush=True)
        if not self.dry_run:
            w.type_keys(SEARCH_HOTKEY, with_spaces=False)
            time.sleep(0.6)

        # 2) 剪贴板粘贴联系人名 (避免中文输入法问题)
        print(f"[AUTODIAL] 粘贴联系人名: {contact}", flush=True)
        if not self.dry_run:
            import pyperclip
            pyperclip.copy(contact)
            time.sleep(0.15)
            w.type_keys("^v")
            time.sleep(SEARCH_RESULT_WAIT)

        # 3) Enter 打开会话
        print(f"[AUTODIAL] Enter 打开会话", flush=True)
        if not self.dry_run:
            w.type_keys("{ENTER}")
            time.sleep(OPEN_CHAT_WAIT)

        # 4) 点击语音通话按钮
        pos = self._locate_call_button(w)
        if pos is None:
            raise DialError("未找到语音通话按钮 (模板匹配失败且无坐标回退); "
                            "请重新校准或检查微信窗口是否被遮挡")
        print(f"[AUTODIAL] 点击语音通话按钮 @ {pos}", flush=True)
        if not self.dry_run:
            import pyautogui
            pyautogui.moveTo(pos[0], pos[1], duration=0.2)
            time.sleep(0.15)
            pyautogui.click()
            time.sleep(POST_DIAL_WAIT)

        print(f"[AUTODIAL] 已向 {contact} 发起语音通话" + (" (dry-run 未实际点击)" if self.dry_run else ""),
              flush=True)
        return {"contact": contact, "clicked": pos, "dry_run": self.dry_run}

    # ---------- button location ----------

    def _locate_call_button(self, w):
        """Template match on screen first; fall back to calibrated offset."""
        tmpl = self.calib.get("button_template")
        if tmpl and os.path.exists(tmpl):
            pos = self._template_match(tmpl)
            if pos:
                return pos
            print(f"[AUTODIAL] 模板匹配失败, 尝试坐标回退", flush=True)
        offset = self.calib.get("button_offset")  # {x, y} 相对窗口左上角
        if offset:
            try:
                r = w.rectangle()
                return (int(r.left) + int(offset["x"]), int(r.top) + int(offset["y"]))
            except Exception as e:  # noqa: BLE001
                print(f"[AUTODIAL] 坐标回退失败: {e}", flush=True)
        return None

    @staticmethod
    def _template_match(tmpl_path: str):
        try:
            import pyautogui
            box = pyautogui.locateOnScreen(tmpl_path, confidence=0.8)
            if box:
                c = pyautogui.center(box)
                return (c.x, c.y)
        except Exception as e:  # noqa: BLE001
            print(f"[AUTODIAL] 模板匹配异常: {e}", flush=True)
        return None
