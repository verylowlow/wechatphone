# -*- coding: utf-8 -*-
"""AppDialer: UI-automation voice-call placement, driven by an AppConfig.

Flow (per contact):
  1. find & activate the app main window (pywinauto/UIA, via adapter)
  2. cfg.search_hotkey to focus the search box
  3. paste contact name via clipboard (中文输入法安全)
  4. Enter -> open the chat
  5. click the voice-call button: template match first (calibrated screenshot),
     window-offset coordinate fallback
Calibration data lives in data/autodial_calib_<app>.json (see calibrate.py).

NOTE: UI automation is inherently brittle across app versions. That's why
every step is logged, `dry_run` skips clicks, and the batch runner waits for
the previous call to end via the calllog store.
"""
from __future__ import annotations

import os
import time

from adapters.base import AppConfig, find_main_window
from adapters import get_app, DEFAULT_APP
from autodial.taskfile import load_calib, tmpl_path


class DialError(Exception):
    pass


class AppDialer:
    def __init__(self, dry_run: bool = False, app: str | AppConfig = DEFAULT_APP):
        self.dry_run = dry_run
        self.cfg = app if isinstance(app, AppConfig) else get_app(app)
        self.calib = load_calib(self.cfg.key)
        if self.cfg.ui_engine in ("vision41", "wecom_vision", "dingtalk_vision"):
            return  # 视觉方案无需校准
        if self.calib is None or not self.calib.get("calibrated_at"):
            raise DialError(
                f"未找到 {self.cfg.display_name} 的有效校准数据, 请先运行: "
                f"python -m autodial.cli calibrate --app {self.cfg.key}"
            )

    # ---------- window management ----------

    def _find_window(self):
        """Return the active pywinauto UIA wrapper for the app main window."""
        try:
            w = find_main_window(self.cfg)
        except RuntimeError as e:
            raise DialError(str(e))
        if w is None:
            raise DialError(f"未找到 {self.cfg.display_name} 窗口, "
                            f"请确认已登录且主窗口未最小化")
        return w

    def _activate(self, w) -> None:
        try:
            w.restore()
        except Exception:
            pass
        w.set_focus()
        time.sleep(0.5)

    # ---------- dial ----------

    def dial(self, contact: str, task: str = "", note: str = "",
             opening: str = "") -> dict:
        """Place a voice call to `contact`. Returns an info dict.

        opening: 任务发起人指定的开场白文本 (注入 instructions, AI 接通后第一句说它)。
        """
        if task or opening:
            from autodial.taskfile import write_current_task
            write_current_task(contact, task, note, app=self.cfg.key, opening=opening)
            print(f"[AUTODIAL] 任务已写入 current_task.json: {contact} -> {task[:40]}", flush=True)

        if self.cfg.ui_engine == "wecom_vision":
            return self._dial_wecom_vision(contact)
        if self.cfg.ui_engine == "dingtalk_vision":
            return self._dial_dingtalk_vision(contact)
        if self.cfg.ui_engine == "vision41":
            return self._dial_vision41(contact)

        print(f"[AUTODIAL] 查找 {self.cfg.display_name} 窗口...", flush=True)
        w = self._find_window()
        print(f"[AUTODIAL] 找到窗口: '{w.window_text()}'", flush=True)
        self._activate(w)

        # 1) 搜索联系人
        print(f"[AUTODIAL] {self.cfg.search_hotkey} 打开搜索", flush=True)
        if not self.dry_run:
            w.type_keys(self.cfg.search_hotkey, with_spaces=False)
            time.sleep(0.6)

        # 2) 剪贴板粘贴联系人名 (避免中文输入法问题)
        print(f"[AUTODIAL] 粘贴联系人名: {contact}", flush=True)
        if not self.dry_run:
            import pyperclip
            pyperclip.copy(contact)
            time.sleep(0.15)
            w.type_keys("^v")
            time.sleep(self.cfg.search_result_wait)

        # 3) Enter 打开会话
        print(f"[AUTODIAL] Enter 打开会话", flush=True)
        if not self.dry_run:
            w.type_keys("{ENTER}")
            time.sleep(self.cfg.open_chat_wait)

        # 4) 点击语音通话按钮
        pos = self._locate_call_button(w)
        if pos is None:
            raise DialError("未找到语音通话按钮 (模板匹配失败且无坐标回退); "
                            f"请重新校准或检查 {self.cfg.display_name} 窗口是否被遮挡")
        print(f"[AUTODIAL] 点击语音通话按钮 @ {pos}", flush=True)
        if not self.dry_run:
            import pyautogui
            pyautogui.moveTo(pos[0], pos[1], duration=0.2)
            time.sleep(0.15)
            pyautogui.click()
            time.sleep(self.cfg.post_dial_wait)

        print(f"[AUTODIAL] 已向 {contact} 发起语音通话" + (" (dry-run 未实际点击)" if self.dry_run else ""),
              flush=True)
        return {"contact": contact, "clicked": pos, "dry_run": self.dry_run}

    # ---------- vision41 (微信 4.1+ 视觉拨号) ----------

    def _dial_vision41(self, contact: str) -> dict:
        """2026-08-10 实测流程: 通讯录→搜索(OCR)→点完全命中→语音通话→菜单第一项。"""
        from autodial import wx41
        if self.dry_run:
            print(f"[AUTODIAL] (dry-run) vision41 预演: 通讯录→搜索 {contact} "
                  f"→点击首条命中→语音通话", flush=True)
            return {"contact": contact, "clicked": None, "dry_run": True}
        print(f"[AUTODIAL] vision41: 激活微信, 进入通讯录...", flush=True)
        wx41.open_contacts()
        print(f"[AUTODIAL] vision41: 精确搜索 {contact}", flush=True)
        try:
            hits = wx41.search_contact(contact)
        except wx41.DuplicateContactError as e:
            self._log_duplicate(str(e), contact)
            raise DialError(str(e))
        if not hits:
            raise DialError(f"通讯录搜索无完全命中记录: {contact}")
        wx41.open_first_result(hits)
        wx41.start_voice_call()
        print(f"[AUTODIAL] 已向 {contact} 发起语音通话 (vision41)", flush=True)
        return {"contact": contact, "clicked": "vision41", "dry_run": False}

    # ---------- wecom_vision (企业微信 视觉拨号) ----------

    def _dial_wecom_vision(self, contact: str) -> dict:
        """2026-08-10 实测流程: 通讯录→搜索清除+精确输入→点第一条→语音通话。"""
        from autodial import wecom_ui
        if self.dry_run:
            print(f"[AUTODIAL] (dry-run) wecom 预演: 通讯录→搜索 {contact} "
                  f"→点击首条命中→语音通话", flush=True)
            return {"contact": contact, "clicked": None, "dry_run": True}
        print(f"[AUTODIAL] wecom: 激活企业微信, 进入通讯录...", flush=True)
        wecom_ui.open_contacts()
        print(f"[AUTODIAL] wecom: 清除并精确搜索 {contact}", flush=True)
        try:
            hits = wecom_ui.search_contact(contact)
        except wecom_ui.DuplicateContactError as e:
            self._log_duplicate(str(e), contact)
            raise DialError(str(e))
        if not hits:
            raise DialError(f"通讯录搜索无命中记录: {contact}")
        wecom_ui.open_first_result(hits)
        info = wecom_ui.start_voice_call()
        print(f"[AUTODIAL] 已向 {contact} 发起语音通话 (wecom_vision)", flush=True)
        return {"contact": contact, "clicked": "wecom_vision", "info": info,
                "dry_run": False}

    # ---------- dingtalk_vision (钉钉 视觉拨号) ----------

    def _dial_dingtalk_vision(self, contact: str) -> dict:
        """2026-08-10 实测流程: 通讯录(UIA)→搜索清除+精确→联系人tab→
        首条行尾语音图标→小通话面板。"""
        from autodial import dingtalk_ui
        if self.dry_run:
            print(f"[AUTODIAL] (dry-run) dingtalk 预演: 通讯录→搜索 {contact} "
                  f"→联系人tab→行尾语音图标", flush=True)
            return {"contact": contact, "clicked": None, "dry_run": True}
        print(f"[AUTODIAL] dingtalk: 激活钉钉, 进入通讯录...", flush=True)
        dingtalk_ui.open_contacts()
        print(f"[AUTODIAL] dingtalk: 清除并精确搜索 {contact} + 联系人tab", flush=True)
        try:
            hits = dingtalk_ui.search_contact(contact)
        except dingtalk_ui.DuplicateContactError as e:
            self._log_duplicate(str(e), contact)
            raise DialError(str(e))
        if not hits:
            raise DialError(f"通讯录搜索无命中记录: {contact}")
        info = dingtalk_ui.start_voice_call(hits)
        if not info.get("call_up"):
            raise DialError("点击语音通话后未检测到通话面板")
        print(f"[AUTODIAL] 已向 {contact} 发起语音通话 (dingtalk_vision)", flush=True)
        return {"contact": contact, "clicked": "dingtalk_vision", "info": info,
                "dry_run": False}

    def _log_duplicate(self, msg: str, contact: str) -> None:
        """同名重复 → 终止呼叫, 并记入通话记录 (note 事件 + 摘要)。"""
        try:
            from calllog.store import CallStore
            st = CallStore()
            cid = time.strftime("%Y%m%d-%H%M%S") + "-dup"
            st.create_call(cid, app=self.cfg.key, contact=contact)
            st.add_event(cid, "note", msg)
            st.set_summary(cid, msg)
            st.end_call(cid)
            print(f"[AUTODIAL] {msg} (已记入通话记录 {cid})", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[AUTODIAL] 记录重复名称失败: {e}", flush=True)

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


class WeChatDialer(AppDialer):
    """向后兼容: 旧代码直接 `WeChatDialer(...)` 仍可用 (固定 wechat)。"""
    def __init__(self, dry_run: bool = False):
        super().__init__(dry_run=dry_run, app="wechat")
