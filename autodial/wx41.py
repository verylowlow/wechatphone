# -*- coding: utf-8 -*-
"""WeChat 4.1+ (Weixin.exe) vision-based UI automation.

2026-08-10 实测于微信 4.1.12.26:
  - 主窗口 class=Qt51514QWindowIcon / title=微信, 但内容区为 MMUI 自绘渲染,
    UIA 树只剩两个空 Pane —— 旧版"按按钮名找控件"全部失效;
  - 因此本模块全部走"截图 + 模板匹配/颜色连通域 + 真实坐标点击"。

拨号流程 (用户确认版):
  1. 激活微信主窗口到前台
  2. 点击左侧栏【通讯录】图标
  3. 在通讯录【搜索】栏精确输入联系人名 (OCR 校验, 多条完全一致 → 终止并记入通话记录)
  4. 点击完全命中的第一条记录
  5. 聊天框右上角【语音通话】按钮
  6. 下拉菜单第一项【语音通话】(第二项是视频通话, 勿点)
挂断: 通话窗口底部中央的【大红色圆圈】(红色连通域定位)。
来电接听: 来电窗口的【大绿色圆圈】(同原理, 颜色不同)。

所有控件优先用 data/wx41_*.png 模板匹配, 失败回退实测坐标常量。
"""
from __future__ import annotations

import ctypes
import os
import time

import cv2
import numpy as np
import pyautogui

user32 = ctypes.windll.user32
MAIN_CLASS = "Qt51514QWindowIcon"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# ---- 实测坐标 (相对主窗口左上角, 微信 4.1.12 默认布局) ----
POS_CONTACTS_ICON = (45, 324)      # 左栏 通讯录 图标中心
POS_CONTACTS_SEARCH = (360, 109)   # 通讯录页 搜索栏中心
POS_CALL_BUTTON = (1624, 106)      # 聊天页右上 语音通话 按钮中心
POS_CALL_MENU_VOICE = (1449, 185)  # 下拉菜单第一项 语音通话

# 通话红/绿圆按钮的最小连通域面积 (实测挂断红圆≈8800; 任务栏微信绿标≈1500, 需排除)
MIN_CIRCLE_AREA = 2500

_ocr = None


class WeChatUIError(Exception):
    pass


class DuplicateContactError(WeChatUIError):
    """搜索出现多条完全一致名称, 按规则终止该通呼叫。"""


# ---------------- window helpers ----------------

def _rect(hwnd):
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    rc = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    return rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top


def find_main_hwnd():
    h = user32.FindWindowW(MAIN_CLASS, "微信")
    if not h:
        h = user32.FindWindowW(MAIN_CLASS, "Weixin")
    return h or None


def focus_main() -> int:
    """把微信主窗口拉到前台, 返回 hwnd; 失败抛异常。"""
    h = find_main_hwnd()
    if not h:
        raise WeChatUIError("未找到微信主窗口 (class=%s)" % MAIN_CLASS)
    # WorkBuddy IDE 置顶会抢焦点/遮屏导致点击落空, 自动化期间先最小化它
    wb = user32.FindWindowW("Chrome_WidgetWin_1", "WorkBuddy")
    if wb and user32.IsWindowVisible(wb):
        user32.ShowWindow(wb, 6)  # SW_MINIMIZE
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
    raise WeChatUIError("无法把微信窗口切到前台 (可能被置顶窗口遮挡)")


def _shot(hwnd):
    x, y, w, hh = _rect(hwnd)
    left, top = max(0, x), max(0, y)
    return pyautogui.screenshot(region=(left, top,
                                        min(x + w, _screen_w()) - left,
                                        min(y + hh, _screen_h()) - top))


def _screen_w():
    return user32.GetSystemMetrics(0)


def _screen_h():
    return user32.GetSystemMetrics(1)


def _frame(pil_img):
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def _match(frame, tmpl_name, need=0.7):
    """模板匹配, 返回 (cx, cy, score); 低于 need 时位置为 None。"""
    path = os.path.join(DATA_DIR, tmpl_name)
    if not os.path.exists(path):
        return None, 0.0
    t = cv2.imread(path)
    res = cv2.matchTemplate(frame, t, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    th, tw = t.shape[:2]
    pos = (int(loc[0] + tw / 2), int(loc[1] + th / 2)) if score >= need else None
    return pos, float(score)


def _click(hwnd, dx, dy):
    x, y, _, _ = _rect(hwnd)
    pyautogui.click(x + dx, y + dy)


# ---------------- OCR ----------------

def _get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def _ocr_lines(pil_img):
    """返回 [{'text', 'cx', 'cy'}], 坐标相对传入图片。"""
    try:
        ocr = _get_ocr()
        result, _ = ocr(np.array(pil_img))
    except Exception as e:  # noqa: BLE001
        print(f"[WX41] OCR 异常: {e}", flush=True)
        return []
    out = []
    for item in (result or []):
        box, text, _score = item
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append({"text": (text or "").strip(),
                    "cx": sum(xs) / 4.0, "cy": sum(ys) / 4.0})
    return out


# ---------------- color blob (hangup / answer circles) ----------------

def _find_circle(full_img, kind: str):
    """在全屏截图里找大圆按钮。kind='red'(挂断) / 'green'(接听)。
    返回屏幕坐标 (cx, cy) 或 None。"""
    a = np.array(full_img.convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    if kind == "red":
        mask = (r > 190) & (g < 120) & (b < 120)
    else:
        mask = (r < 120) & (g > 140) & (b < 140)
    m = (mask * 255).astype(np.uint8)
    n, _labels, stats, cents = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        w = stats[i, cv2.CC_STAT_WIDTH]
        hh = stats[i, cv2.CC_STAT_HEIGHT]
        if area >= MIN_CIRCLE_AREA and 0.8 <= w / max(1, hh) <= 1.25:
            if best is None or area > best[2]:
                best = (int(cents[i][0]), int(cents[i][1]), int(area))
    return (best[0], best[1]) if best else None


# ---------------- steps ----------------

def open_contacts(dry_run: bool = False) -> None:
    """步骤2: 进入通讯录页。"""
    h = focus_main()
    frame = _frame(_shot(h))
    pos, sc = _match(frame, "wx41_contacts_page.png", need=0.6)
    if pos:
        return  # 已在通讯录页
    pos, _sc = _match(frame, "wx41_contacts.png", need=0.5)
    dx, dy = pos or POS_CONTACTS_ICON
    if dry_run:
        print(f"[WX41] (dry-run) 将点击通讯录图标 @ ({dx},{dy}) score={_sc:.2f}", flush=True)
        return
    for attempt in range(3):
        _click(h, dx, dy)
        time.sleep(1.2)
        frame = _frame(_shot(h))
        _, vsc = _match(frame, "wx41_contacts_page.png", need=0.6)
        if vsc >= 0.6:
            return
        # 已选中态图标变绿, 直接看搜索栏模板(通讯录页特有布局)
        _, ssc = _match(frame, "wx41_csearch.png", need=0.6)
        if ssc >= 0.6:
            return
        time.sleep(0.5)
    raise WeChatUIError("无法进入通讯录页 (点击后未检测到通讯录界面)")


def search_contact(contact: str, dry_run: bool = False) -> list[dict]:
    """步骤3: 通讯录搜索栏精确搜索。返回完全命中项 [{'text','cx','cy'}](相对窗口)。

    多条完全一致 → 抛 DuplicateContactError。
    """
    h = focus_main()
    frame = _frame(_shot(h))
    pos, _sc = _match(frame, "wx41_csearch.png", need=0.6)
    sx, sy = pos or POS_CONTACTS_SEARCH
    if not dry_run:
        _click(h, sx, sy)
        time.sleep(0.5)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        time.sleep(0.2)
        import pyperclip
        pyperclip.copy(contact)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(1.6)
    # OCR 下拉结果区 (搜索栏下方, 列表宽约 680px)
    x, y, w, hh = _rect(h)
    left, top = max(0, x), max(0, y)
    crop = pyautogui.screenshot(region=(left + 120, top + 140,
                                        min(700, w - 120), min(1120, hh - 140)))
    lines = _ocr_lines(crop)
    # 只在联系人区块内判断: 上界=搜索栏下, 下界="群聊"分组行(若识别到), 否则固定 340
    limit = 340.0
    for l in lines:
        if "群聊" in l["text"]:
            limit = l["cy"]
            break
    exact = [l for l in lines if l["cy"] < limit and
             (l["text"] == contact or
              (contact in l["text"] and len(l["text"]) <= len(contact) + 2))]
    for l in exact:  # 转成窗口内坐标
        l["cx"] += 120
        l["cy"] += 140
    if len(exact) > 1:
        raise DuplicateContactError(f"{contact}名称重复, 未执行呼叫")
    return exact


def open_first_result(hits: list[dict], dry_run: bool = False) -> None:
    """步骤4: 点击完全命中的第一条记录。"""
    if not hits:
        raise WeChatUIError("搜索结果中无完全命中的记录")
    h = focus_main()
    if dry_run:
        print(f"[WX41] (dry-run) 将点击首条结果 @ ({hits[0]['cx']:.0f},{hits[0]['cy']:.0f})",
              flush=True)
        return
    _click(h, int(hits[0]["cx"]), int(hits[0]["cy"]))
    time.sleep(1.2)


def start_voice_call(dry_run: bool = False) -> None:
    """步骤5+6: 聊天右上【语音通话】→ 菜单第一项。成功标志: 出现大红色圆圈。"""
    h = focus_main()
    frame = _frame(_shot(h))
    pos, _sc = _match(frame, "wx41_callbtn.png", need=0.6)
    bx, by = pos or POS_CALL_BUTTON
    if dry_run:
        print(f"[WX41] (dry-run) 将点击语音通话按钮 @ ({bx},{by}) 及菜单第一项", flush=True)
        return
    _click(h, bx, by)
    time.sleep(1.0)
    _click(h, *POS_CALL_MENU_VOICE)
    # 等待通话窗口 (大红圆) 出现
    for _ in range(8):
        time.sleep(1.0)
        if _find_circle(pyautogui.screenshot(), "red"):
            return
    raise WeChatUIError("点击语音通话后未检测到通话窗口 (大红圆)")


def hang_up() -> dict:
    """点击通话窗口的大红圆挂断。"""
    for _ in range(3):
        hit = _find_circle(pyautogui.screenshot(), "red")
        if hit:
            pyautogui.click(*hit)
            time.sleep(1.0)
            # 确认消失
            if not _find_circle(pyautogui.screenshot(), "red"):
                return {"ok": True, "method": "wx41_red_circle"}
            continue
        time.sleep(0.8)
    return {"ok": False, "method": "wx41_red_circle"}


def answer_incoming() -> dict:
    """来电窗口的大绿圆 = 接听。"""
    hit = _find_circle(pyautogui.screenshot(), "green")
    if hit:
        pyautogui.click(*hit)
        return {"ok": True, "method": "wx41_green_circle", "pos": hit}
    return {"ok": False, "method": "wx41_green_circle"}


def has_incoming_popup() -> bool:
    """是否存在来电接听绿圆 (供 IncomingWatcher 使用)。"""
    return _find_circle(pyautogui.screenshot(), "green") is not None


def incoming_caller_name() -> str:
    """来电窗口头像下方的联系人名称 (OCR 屏幕中心区域)。"""
    sw, sh = _screen_w(), _screen_h()
    crop = pyautogui.screenshot(region=(sw // 2 - 300, sh // 2 - 200, 600, 400))
    noise = ("语音通话", "视频通话", "邀请你", "接听", "拒绝", "等待", "接受", "挂断")
    for l in _ocr_lines(crop):
        t = l["text"]
        if t and len(t) <= 20 and not any(n in t for n in noise):
            return t
    return ""
