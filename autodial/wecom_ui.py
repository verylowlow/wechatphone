# -*- coding: utf-8 -*-
"""WeCom (企业微信, WXWork.exe) vision-based UI automation.

2026-08-10 实测:
  - 主窗口 class=WeWorkWindow / title=企业微信;
  - UIA 树只有 4 个空 Pane (自绘渲染), 与微信 4.1 同病 -> 走视觉方案:
    截图 + OCR + 颜色连通域 + 真实坐标点击 (复用 wx41 的底层工具函数)。

拨号流程 (用户确认版):
  1. 激活应用到前台
  2. 点击主程序左侧目录树的【通讯录】(第 10 项)
  3. 通讯录页【搜索】输入框: 先清除 (Ctrl+A + Delete), 再粘贴精确用户名
  4. 点击下拉结果的第一条 (精确命中)
  5. 客户面板中点击明显的【语音通话】按钮
  6. 进入语音通话界面
  7. 通话界面下方的【大红圆】(红色连通域) = 挂断按钮

坐标均为相对主窗口左上角的实测值 (2560x1600 默认布局), 每一步优先用
OCR 自校正, 坐标仅作回退。
"""
from __future__ import annotations

import ctypes
import time

import cv2
import numpy as np
import pyautogui

from autodial import wx41  # 复用: _rect/_shot/_frame/_click/_ocr_lines/_find_circle

user32 = ctypes.windll.user32
MAIN_CLASS = "WeWorkWindow"
MAIN_TITLE = "企业微信"

# ---- 实测坐标 (相对主窗口左上角, 2026-08-10 OCR 地面真相) ----
POS_CONTACTS_ITEM = (135, 738)    # 左栏第10项 通讯录 行中心
POS_SEARCH_BOX = (500, 75)        # 通讯录页 搜索输入框中心 (OCR "搜索"≈(428,75))
# 客户面板 语音通话 按钮 (OCR 首选, 此为回退; 像素验证过按钮底色)
POS_VOICE_CALL_BTN = (1739, 658)


class WeComUIError(Exception):
    pass


class DuplicateContactError(WeComUIError):
    """搜索出现多条完全一致名称, 终止该通呼叫。"""


# ---------------- window ----------------

def find_main_hwnd():
    h = user32.FindWindowW(MAIN_CLASS, MAIN_TITLE)
    if not h:
        # 兜底: 枚举所有 WeWorkWindow 顶层可见窗口
        import ctypes as _c
        found = []

        @_c.WINFUNCTYPE(_c.c_bool, _c.c_void_p, _c.c_void_p)
        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                buf = _c.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, buf, 64)
                if buf.value == MAIN_CLASS:
                    found.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        h = found[0] if found else 0
    return h or None


def focus_main() -> int:
    h = find_main_hwnd()
    if not h:
        raise WeComUIError("未找到企业微信主窗口 (class=%s)" % MAIN_CLASS)
    wb = user32.FindWindowW("Chrome_WidgetWin_1", "WorkBuddy")
    if wb and user32.IsWindowVisible(wb):
        user32.ShowWindow(wb, 6)  # SW_MINIMIZE, 防置顶遮挡
        time.sleep(0.2)
    for _ in range(3):
        user32.ShowWindow(h, 9)  # SW_RESTORE
        time.sleep(0.15)
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 0x0002, 0)
        user32.SetForegroundWindow(h)
        time.sleep(0.35)
        if user32.GetForegroundWindow() == h:
            return h
    raise WeComUIError("无法把企业微信窗口切到前台")


# ---------------- steps ----------------

def _ocr_window(h):
    return wx41._ocr_lines(wx41._shot(h))


def open_contacts(dry_run: bool = False) -> dict:
    """步骤2: 点击左侧目录树【通讯录】。OCR 找该文本行自校正坐标。"""
    h = focus_main()
    # 菜单开着会挡住点击: 检测到头像菜单则 Escape 隐藏再激活
    lines = _ocr_window(h)
    if any("休息一下" in l["text"] or "管理企业" in l["text"] for l in lines):
        import pyautogui as _pa
        _pa.press("escape")
        time.sleep(0.5)
        h = focus_main()
        time.sleep(0.4)
        lines = _ocr_window(h)
    pos = None
    for l in lines:
        if l["text"] == "通讯录" and l["cx"] < 300:  # 左栏区域
            pos = (int(l["cx"]), int(l["cy"]))
            break
    dx, dy = pos or POS_CONTACTS_ITEM
    if dry_run:
        return {"step": "open_contacts", "pos": [dx, dy], "dry_run": True}
    wx41._click(h, dx, dy)
    time.sleep(1.0)
    return {"step": "open_contacts", "pos": [dx, dy], "ocr_hit": pos is not None}


def search_contact(contact: str, dry_run: bool = False) -> list[dict]:
    """步骤3: 搜索框清除后精确输入; 返回下拉精确命中项 (窗口内坐标)。"""
    h = focus_main()
    if not dry_run:
        wx41._click(h, *POS_SEARCH_BOX)
        time.sleep(0.4)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")     # 清除操作
        time.sleep(0.2)
        import pyperclip
        pyperclip.copy(contact)
        pyautogui.hotkey("ctrl", "v")  # 粘贴精确用户名 (中文安全)
        time.sleep(1.5)
    # 全窗口 OCR 找结果行: 中间列表区 (100<cy<600, 320<cx<1000),
    # 排除搜索框自身(cy≈75)与右侧客户面板标题(cx≈1200)
    lines = _ocr_window(h)
    exact = [l for l in lines
             if 100 < l["cy"] < 600 and 320 < l["cx"] < 1000 and
             (l["text"] == contact or
              (contact in l["text"] and len(l["text"]) <= len(contact) + 4))]
    exact.sort(key=lambda l: l["cy"])
    if len(exact) > 1:
        raise DuplicateContactError(f"{contact}名称重复, 未执行呼叫")
    return exact


def open_first_result(hits: list[dict], dry_run: bool = False) -> dict:
    """步骤4: 点击查询结果第一项。"""
    if not hits:
        raise WeComUIError("搜索结果中无命中记录")
    h = focus_main()
    first = hits[0]
    if dry_run:
        return {"step": "open_first_result", "pos": [first["cx"], first["cy"]],
                "dry_run": True}
    wx41._click(h, int(first["cx"]), int(first["cy"]))
    time.sleep(1.0)
    return {"step": "open_first_result", "pos": [first["cx"], first["cy"]]}


def _find_voice_call_btn(h):
    """OCR 找客户面板上的【语音通话】按钮文本 (实测 (2020,732)); 回退坐标。"""
    lines = _ocr_window(h)
    cands = [l for l in lines if l["text"] == "语音通话" and l["cx"] > 1500]
    if cands:
        cands.sort(key=lambda l: -l["cx"])
        return int(cands[0]["cx"]), int(cands[0]["cy"]), True
    return POS_VOICE_CALL_BTN[0], POS_VOICE_CALL_BTN[1], False


def start_voice_call(dry_run: bool = False) -> dict:
    """步骤5+6: 点击【语音通话】, 等待通话界面 (大红圆) 出现。"""
    h = focus_main()
    bx, by, ocr_hit = _find_voice_call_btn(h)
    if dry_run:
        return {"step": "start_voice_call", "pos": [bx, by], "dry_run": True}
    wx41._click(h, bx, by)
    # 轮询通话窗口: 大红圆出现
    for _ in range(10):
        time.sleep(0.8)
        if wx41._find_circle(pyautogui.screenshot(), "red"):
            return {"step": "start_voice_call", "pos": [bx, by],
                    "ocr_hit": ocr_hit, "call_up": True}
    # 没红圆也可能在呼叫中, OCR 看有没有呼叫文案
    txt = " ".join(l["text"] for l in wx41._ocr_lines(pyautogui.screenshot()))
    calling = any(k in txt for k in ("正在呼叫", "等待对方", "响铃", "挂断", "取消"))
    return {"step": "start_voice_call", "pos": [bx, by],
            "ocr_hit": ocr_hit, "call_up": calling, "screen_text": txt[:200]}


def hang_up() -> dict:
    """步骤7: 通话界面下方大红圆 (红连通域) = 挂断。红圆找不到时退 OCR 找'挂断'。"""
    for _ in range(3):
        shot = pyautogui.screenshot()
        hit = wx41._find_circle(shot, "red")
        if hit:
            pyautogui.click(*hit)
            time.sleep(1.0)
            if not wx41._find_circle(pyautogui.screenshot(), "red"):
                return {"ok": True, "method": "wecom_red_circle", "pos": hit}
            continue
        # 回退: OCR 找 挂断 文本
        for l in wx41._ocr_lines(shot):
            if l["text"] in ("挂断", "结束通话"):
                pyautogui.click(int(l["cx"]), int(l["cy"]))
                time.sleep(1.0)
                return {"ok": True, "method": "wecom_ocr_hangup",
                        "pos": [int(l["cx"]), int(l["cy"])]}
        time.sleep(0.8)
    return {"ok": False, "method": "wecom_red_circle"}


def in_call() -> bool:
    """当前屏幕上是否存在通话红圆 (供状态判断)。"""
    return wx41._find_circle(pyautogui.screenshot(), "red") is not None
