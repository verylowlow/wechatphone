# -*- coding: utf-8 -*-
"""DingTalk (钉钉, DingTalk.exe, 含任意改名的定制版) vision-based UI automation.

主窗口按 Win32 类名 StandardFrame_DingTalk 定位 (FindWindowW), 不依赖窗口
标题 —— 定制版标题可任意修改, 类名不变。

2026-08-10 实测 (StandardFrame_DingTalk 主窗口, 2048x1280 非最大化 & 最大化均验证):
  - 左侧导航栏 UIA 可访问 (auto_id=navigator_view.contact_contact 等), 内容区为
    内嵌 Chrome 自绘 -> 导航用 UIA, 其余走视觉 (截图+OCR+模板匹配+真实坐标点击);
  - 坐标一律使用【屏幕坐标系】(全屏截图 OCR/模板匹配), 不依赖窗口 rect 换算,
    窗口最大化/还原两种形态通用。

拨号流程 (用户确认版):
  1. 激活应用到前台 (物理点击标题栏最稳)
  2. 左侧目录树第5项【通讯录】(UIA auto_id 直点, 回退 OCR 左栏文本)
  3. 顶部搜索框: Ctrl+Shift+F 聚焦 (回退 OCR 点 placeholder), 清除后粘贴精确用户名
  4. 结果弹页点【联系人】tab
  5. 第一条结果行最右侧【语音通话】小图标 (data/dt_voice_btn.png 模板匹配)
  6. 弹出小通话面板: "语音通话 / 正在呼叫..." + 红圆挂断
  7. 挂断 = OCR 找【挂断】文本, 点其正上方红圆 (~70px); 颜色连通域仅作回退
     (注意: 窗口内橙色公司 LOGO 会污染宽松红色阈值, 故主路不用纯颜色)。

来电接听: 复用视觉绿圆 (incoming.py 分流), 待真机来电验证。
"""
from __future__ import annotations

import ctypes
import os
import time

import cv2
import numpy as np
import pyautogui

from autodial import wx41  # 复用: _frame/_ocr_lines/_find_circle/_match 等底层

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
MAIN_CLASS = "StandardFrame_DingTalk"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
VOICE_TMPL = os.path.join(DATA_DIR, "dt_voice_btn.png")

# 挂断红圆在 "挂断" 文本正上方的像素距离 (实测 面板内 红圆中心-文本中心 ≈ 70)
HANGUP_CIRCLE_ABOVE_TEXT = 70
# 通话面板红圆的最小连通域面积 (小面板圆比微信大圆小很多)
DT_MIN_RED_AREA = 500


class DingTalkUIError(Exception):
    pass


class DuplicateContactError(DingTalkUIError):
    """搜索出现多条完全一致名称, 终止该通呼叫。"""


# ---------------- window ----------------

def _rect(hwnd):
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    rc = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    return rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top


def _pid(hwnd):
    pid = ctypes.c_uint()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def find_main_hwnd():
    h = user32.FindWindowW(MAIN_CLASS, None)
    if h:
        return h
    # 兜底: 按进程名 DingTalk.exe 枚举顶层可见窗取面积最大
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value == MAIN_CLASS:
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def focus_main() -> int:
    h = find_main_hwnd()
    if not h:
        raise DingTalkUIError("未找到钉钉主窗口 (class=%s)" % MAIN_CLASS)
    wb = user32.FindWindowW("Chrome_WidgetWin_1", "WorkBuddy")
    if wb and user32.IsWindowVisible(wb):
        user32.ShowWindow(wb, 6)  # SW_MINIMIZE, 防置顶遮挡
        time.sleep(0.2)
    for _ in range(3):
        user32.ShowWindow(h, 9)  # SW_RESTORE
        time.sleep(0.2)
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 0x0002, 0)
        user32.SetForegroundWindow(h)
        time.sleep(0.3)
        x, y, w, _hh = _rect(h)
        pyautogui.click(x + w // 2, y + 20)  # 物理点击标题栏, 最可靠的激活方式
        time.sleep(0.3)
        if user32.GetForegroundWindow() == h:
            return h
    # 兜底: AttachThreadInput 强抢前台
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None)
    tid_me = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(tid_me, tid_fg, True)
    user32.BringWindowToTop(h)
    user32.SetForegroundWindow(h)
    user32.AttachThreadInput(tid_me, tid_fg, False)
    time.sleep(0.4)
    if user32.GetForegroundWindow() == h:
        return h
    raise DingTalkUIError("无法把钉钉窗口切到前台")


# ---------------- ocr helpers ----------------

def _ocr_screen():
    return wx41._ocr_lines(pyautogui.screenshot())


def _in_window(p, h, margin=40):
    x, y, w, hh = _rect(h)
    return x - margin <= p[0] <= x + w + margin and y - margin <= p[1] <= y + hh + margin


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _ding_on_top(h) -> bool:
    """WindowFromPoint 自检: 窗口内部一点的顶层窗口是否属于钉钉 (防共享桌面并发遮挡)。"""
    try:
        x, y, w, hh = _rect(h)
        pt = POINT(x + w // 2, y + hh // 2)
        top = user32.WindowFromPoint(pt)
        cur = top
        for _ in range(6):
            if cur == h or _pid(cur) == _pid(h):
                return True
            cur = user32.GetAncestor(cur, 2)  # GA_ROOT
        return False
    except Exception:  # noqa: BLE001
        return True


# ---------------- steps ----------------

def open_contacts(dry_run: bool = False) -> dict:
    """步骤2: 点左侧导航【通讯录】。UIA auto_id 直点, 回退 OCR 左栏文本。"""
    h = focus_main()
    pos, method = None, None
    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(handle=h)
        w = app.window(handle=h)
        btn = w.child_window(auto_id="navigator_view.contact_contact",
                             control_type="Button")
        r = btn.rectangle()
        pos = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
        method = "uia"
    except Exception as e:  # noqa: BLE001
        print(f"[DINGTALK] UIA 通讯录按钮失败({e}), 回退 OCR", flush=True)
    if pos is None:
        x, y, w, _hh = _rect(h)
        for l in _ocr_screen():
            if l["text"] == "通讯录" and l["cx"] < x + w * 0.2:
                pos = (int(l["cx"]), int(l["cy"]))
                method = "ocr"
                break
    if pos is None:
        raise DingTalkUIError("未找到左侧导航【通讯录】")
    if dry_run:
        return {"step": "open_contacts", "pos": list(pos), "method": method,
                "dry_run": True}
    pyautogui.click(*pos)
    time.sleep(1.5)  # 通讯录页渲染需要时间, 过短会导致后续热键被吞
    return {"step": "open_contacts", "pos": list(pos), "method": method}


def _popup_open(lines):
    # "综合/确认结果/退出搜索" 只出现在搜索弹页 (主界面和通讯录页都没有),
    # 底部提示小字 OCR 易漏, 用 tab 词 "综合" 作最稳的弹页特征。
    return any("综合" in l["text"] or "确认结果" in l["text"] or
               "退出搜索" in l["text"] for l in lines)


def _focus_search_box(h) -> None:
    """确保搜索弹页打开且输入焦点在搜索框。

    注意: Ctrl+Shift+F 是 toggle —— 弹页已开时按它会关掉弹页, 所以必须先
    探测状态: 已开 → 直接点搜索框文本(已输入的词/placeholder)取焦;
    未开 → 热键打开 (自动聚焦), 失败再 OCR 点 placeholder。
    """
    if _open_search_fresh(h):
        return
    raise DingTalkUIError("无法打开钉钉全局搜索")


def _click_topbar_search(h) -> bool:
    """点击顶部搜索文本 (placeholder 或已输入词)。弹页开时=取焦 (e2e11 链路)。"""
    tl = [l for l in _ocr_screen() if "搜索" in l["text"] and
          _in_window((l["cx"], l["cy"]), h)]
    tl.sort(key=lambda l: l["cy"])
    if not tl:
        return False
    pyautogui.click(int(tl[0]["cx"]), int(tl[0]["cy"]))
    time.sleep(0.8)
    return True


def _open_search_fresh(h) -> bool:
    """唯一实测验证过的取焦链路: 若弹页已开先 Escape 关掉, 再用热键打开 ——
    热键打开的瞬间输入框必然聚焦 (probe5/probe7/probe8C 实测)。
    弹页已开时点击顶部搜索框【不会】聚焦输入框 (probe8B 实测), 禁止用。
    """
    def _st(tag):
        if os.environ.get("DT_DEBUG_SHOTS"):
            print(f"[DINGTALK] open_search {tag}: popup={_popup_open(_ocr_screen())}",
                  flush=True)

    _st("start")
    if _popup_open(_ocr_screen()):
        # 可能是 UIA 点击通讯录后的"关闭过渡态" (OCR 仍见 tab) —— 先等它自然
        # 关掉 (probe11 实测: 过渡态按 Escape 会搅乱后续热键注册)
        for _ in range(4):
            time.sleep(0.8)
            if not _popup_open(_ocr_screen()):
                break
        else:
            # 真·打开状态才 Escape
            pyautogui.press("escape")
            time.sleep(2.0)
        _st("after_esc")
    pyautogui.hotkey("ctrl", "shift", "f")
    time.sleep(2.0)
    _st("after_hotkey")
    if _popup_open(_ocr_screen()):
        return True
    # 间隔重试一次热键 (非连发: 等足恢复时间)
    time.sleep(1.5)
    pyautogui.hotkey("ctrl", "shift", "f")
    time.sleep(2.0)
    if _popup_open(_ocr_screen()):
        return True
    # 末级回退: 点击顶部橙色搜索框一次 (主界面=打开弹页, 未验证聚焦, 仅兜底)
    return _click_topbar_search(h) and _popup_open(_ocr_screen())


def _input_text(h, contact: str) -> bool:
    """清除+粘贴; 用顶栏是否出现搜索词校验输入落地, 失败重试一次。"""
    import pyperclip
    pyperclip.copy(contact)

    def _typed():
        top = [l for l in _ocr_screen() if l["cy"] < _rect(h)[1] + 90]
        return any(contact[:4] in l["text"] for l in top)

    # 输入是焦点竞争, 用自校验梯: 多种"取焦+键入"组合, 每步都验落地。
    for attempt in range(3):
        # 共享桌面下人工会并发操作 (切窗/拖窗), 每轮输入前重新激活钉钉
        # 并确认钉钉内容确实可见 (无其它窗口遮挡)
        if not _ding_on_top(h):
            try:
                h = focus_main()
            except DingTalkUIError:
                continue
        if not _popup_open(_ocr_screen()) and not _open_search_fresh(h):
            continue
        # 取焦组合: A=直接键入 (新打开已聚焦); B=点顶栏再键入
        for mode in ("A", "B"):
            if mode == "B" and not _click_topbar_search(h):
                break
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("delete")
            time.sleep(0.2)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(1.5)
            if _typed():
                return True
            if os.environ.get("DT_DEBUG_SHOTS"):
                print(f"[DINGTALK] _input_text try{attempt}/{mode} fg_ok="
                      f"{user32.GetForegroundWindow() == h}", flush=True)
                pyautogui.screenshot().save(
                    os.path.join(DATA_DIR, f"_dbg_input_{attempt}{mode}.png"))
        # 整轮失败: 重新新鲜打开再来
        _open_search_fresh(h)
    return False


def search_contact(contact: str, dry_run: bool = False) -> list[dict]:
    """步骤3+4: 搜索框清除+精确输入, 点【联系人】tab; 返回精确命中行 (屏幕坐标)。"""
    h = find_main_hwnd()
    if not h:
        raise DingTalkUIError("未找到钉钉主窗口")
    if not dry_run:
        # 注意: 不在此二次激活 —— focus_main 的物理点击标题栏会干扰紧随其后的
        # Ctrl+Shift+F 热键注册 (probe11 对照实测)。前台由 open_contacts 保证。
        _focus_search_box(h)
        if not _input_text(h, contact):
            raise DingTalkUIError(f"搜索词未成功输入到搜索框: {contact}")
        time.sleep(0.8)
        # 点【联系人】tab
        tab = next((l for l in _ocr_screen() if l["text"] == "联系人"), None)
        if tab:
            pyautogui.click(int(tab["cx"]), int(tab["cy"]))
            time.sleep(1.0)
    lines = _ocr_screen()
    y_top = _rect(h)[1]
    # 排除顶栏搜索框自身 (已输入词, cy<120), 只认结果区行;
    # 行标题可能与"(姓名:xxx)"合并成一条, 故包含匹配不限长度。
    exact = [l for l in lines if _in_window((l["cx"], l["cy"]), h) and
             l["cy"] > y_top + 120 and
             (l["text"] == contact or contact in l["text"])]
    exact.sort(key=lambda l: l["cy"])
    if len(exact) > 1:
        raise DuplicateContactError(f"{contact}名称重复, 未执行呼叫")
    return exact


def _find_voice_btn(h, near_y=None):
    """全屏模板匹配【语音通话】小图标; near_y 给定时取垂直距离最近者。"""
    frame = wx41._frame(pyautogui.screenshot())
    if not os.path.exists(VOICE_TMPL):
        raise DingTalkUIError(f"缺少模板图 {VOICE_TMPL}")
    t = cv2.imread(VOICE_TMPL)
    res = cv2.matchTemplate(frame, t, cv2.TM_CCOEFF_NORMED)
    th, tw = t.shape[:2]
    hits = []
    while True:
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx < 0.7:
            break
        cx, cy = loc[0] + tw / 2, loc[1] + th / 2
        if _in_window((cx, cy), h):
            hits.append((cx, cy, mx))
        cv2.floodFill(res, None, (loc[0], loc[1]), 0)
    if not hits:
        return None, 0.0
    if near_y is not None:
        hits.sort(key=lambda p: abs(p[1] - near_y))
    else:
        hits.sort(key=lambda p: p[1])
    best = hits[0]
    return (int(best[0]), int(best[1])), best[2]


def start_voice_call(hits: list[dict] | None = None, dry_run: bool = False) -> dict:
    """步骤5+6: 点首条结果行尾【语音通话】图标, 等通话面板 (OCR '挂断' 出现)。"""
    h = focus_main()
    near_y = hits[0]["cy"] if hits else None
    pos, score = _find_voice_btn(h, near_y=near_y)
    if pos is None:
        raise DingTalkUIError("搜索结果行未匹配到【语音通话】图标 (模板匹配失败)")
    if dry_run:
        return {"step": "start_voice_call", "pos": list(pos), "score": round(score, 2),
                "dry_run": True}
    pyautogui.click(*pos)
    time.sleep(1.2)
    # 轮询通话面板: OCR 见 "挂断" 或 "正在呼叫"
    for _ in range(10):
        txt = [l["text"] for l in _ocr_screen()]
        if any("挂断" in t for t in txt) or any("正在呼叫" in t for t in txt):
            return {"step": "start_voice_call", "pos": list(pos),
                    "score": round(score, 2), "call_up": True}
        time.sleep(0.8)
    return {"step": "start_voice_call", "pos": list(pos),
            "score": round(score, 2), "call_up": False}


def _hangup_text_pos(h):
    for l in _ocr_screen():
        if l["text"] in ("挂断", "结束通话") and _in_window((l["cx"], l["cy"]), h):
            return int(l["cx"]), int(l["cy"])
    return None


def _red_circle_in_window(h):
    """严格红色阈值 (排除橙色 LOGO): r>200,g<100,b<100; 限钉钉窗口内。"""
    a = np.array(pyautogui.screenshot().convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mask = ((r > 200) & (g < 100) & (b < 100)).astype(np.uint8) * 255
    x, y, w, hh = _rect(h)
    m2 = np.zeros_like(mask)
    y0, x0 = max(0, y), max(0, x)
    m2[y0:y + hh, x0:x + w] = mask[y0:y + hh, x0:x + w]
    n, _lb, stats, cents = cv2.connectedComponentsWithStats(m2, 8)
    best = None
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        ww = stats[i, cv2.CC_STAT_WIDTH]
        hhh = stats[i, cv2.CC_STAT_HEIGHT]
        if area >= DT_MIN_RED_AREA and 0.7 <= ww / max(1, hhh) <= 1.4:
            if best is None or area > best[2]:
                best = (int(cents[i][0]), int(cents[i][1]), int(area))
    return best


def hang_up() -> dict:
    """步骤7: OCR '挂断' 文本 → 点其上方红圆; 回退直接点文本/红圆连通域。"""
    h = find_main_hwnd()
    if not h:
        return {"ok": False, "method": "dingtalk_no_window"}
    for _ in range(3):
        tp = _hangup_text_pos(h)
        if tp:
            cx, cy = tp[0], tp[1] - HANGUP_CIRCLE_ABOVE_TEXT
            pyautogui.click(cx, cy)
            time.sleep(1.0)
            if not _hangup_text_pos(h):
                return {"ok": True, "method": "dingtalk_red_circle",
                        "pos": [cx, cy]}
            # 面板还在: 直接点文本本身再试
            pyautogui.click(*tp)
            time.sleep(1.0)
            if not _hangup_text_pos(h):
                return {"ok": True, "method": "dingtalk_ocr_hangup", "pos": list(tp)}
            continue
        rb = _red_circle_in_window(h)
        if rb:
            pyautogui.click(rb[0], rb[1])
            time.sleep(1.0)
            if not _hangup_text_pos(h) and not _red_circle_in_window(h):
                return {"ok": True, "method": "dingtalk_red_blob",
                        "pos": [rb[0], rb[1]]}
            continue
        time.sleep(0.8)
    return {"ok": False, "method": "dingtalk_red_circle"}


def in_call() -> bool:
    """当前屏幕是否存在钉钉通话面板 (OCR '挂断' 且在窗口内)。"""
    h = find_main_hwnd()
    if not h:
        return False
    return _hangup_text_pos(h) is not None
