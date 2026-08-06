# -*- coding: utf-8 -*-
"""Calibration wizard for autodial.

Records two things into data/autodial_calib.json:
  1. button_offset: voice-call button position relative to the WeChat window's
     top-left corner (coordinate fallback).
  2. button_template: a small screenshot of the button for template matching
     (primary locating method; robust to window moving/resizing).

UX: user manually moves the mouse over the button, presses Ctrl+C in the
terminal -> we capture the position and crop the template from the screen.
"""
from __future__ import annotations

import json
import os
import sys
import time

from autodial.taskfile import CALIB_FILE, ensure_data_dir, save_calib

TMPL_PATH = os.path.join(os.path.dirname(CALIB_FILE), "call_button_template.png")
TMPL_HANGUP_PATH = os.path.join(os.path.dirname(CALIB_FILE), "hangup_button_template.png")
CROP = 28  # 模板裁剪半径 (像素): 以按钮为中心裁 2CROP x 2CROP


def find_wechat_window():
    from pywinauto import Desktop
    for w in Desktop(backend="uia").windows():
        try:
            title = w.window_text()
        except Exception:
            continue
        if title and title.strip() == "微信":
            return w
    for w in Desktop(backend="uia").windows():
        try:
            title = w.window_text()
        except Exception:
            continue
        if title and ("微信" in title or "WeChat" in title):
            return w
    return None


def capture_button(label: str) -> tuple[int, int] | None:
    """让用户把鼠标悬停到目标按钮上, 按 Ctrl+C 捕获位置。"""
    print(f"\n【捕获 {label}】")
    print(f"1. 把鼠标移动到【{label}】按钮上(不要点);")
    print("2. 回到本终端窗口, 按 Ctrl+C 捕获位置。")
    input("准备好后按 Enter 开始捕获模式...")
    try:
        while True:
            time.sleep(0.05)  # Ctrl+C 随时可打断
    except KeyboardInterrupt:
        pass
    import pyautogui
    x, y = pyautogui.position()
    print(f"捕获到鼠标位置: ({x}, {y})")
    return x, y


def calibrate_dial_button():
    ensure_data_dir()
    print("=" * 60)
    print("微信自动拨号 - 校准向导 (语音通话拨号按钮)")
    print("=" * 60)

    w = find_wechat_window()
    if w is None:
        print("[错误] 未找到微信窗口。请先登录 PC 微信并保持主窗口打开。")
        sys.exit(1)
    r = w.rectangle()
    print(f"找到微信窗口: '{w.window_text()}' @ ({r.left},{r.top}) {r.width()}x{r.height()}")
    print("\n请先在微信里随便打开一个聊天会话。")

    x, y = capture_button("语音通话(拨号)")

    # 重新获取窗口矩形 (用户可能移动了窗口)
    w2 = find_wechat_window()
    if w2 is None:
        print("[错误] 捕获时找不到微信窗口了。")
        sys.exit(1)
    r2 = w2.rectangle()
    offset_x = x - int(r2.left)
    offset_y = y - int(r2.top)
    if not (0 <= offset_x <= r2.width() and 0 <= offset_y <= r2.height()):
        print(f"[警告] 鼠标位置不在微信窗口内 (相对偏移 {offset_x},{offset_y}), "
              f"请确认微信窗口是前台活动窗口; 仍将保存。")

    # 裁剪按钮模板图
    tmpl_ok = False
    try:
        import pyautogui
        shot = pyautogui.screenshot(region=(x - CROP, y - CROP, CROP * 2, CROP * 2))
        shot.save(TMPL_PATH)
        tmpl_ok = True
        print(f"按钮模板已保存: {TMPL_PATH} ({CROP*2}x{CROP*2}px)")
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 模板截图失败({e}), 将只使用坐标回退。")

    calib = {
        "button_offset": {"x": offset_x, "y": offset_y},
        "button_template": TMPL_PATH if tmpl_ok else None,
        "window_title": w2.window_text(),
        "calibrated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 保留已有的挂断按钮校准
    from autodial.taskfile import load_calib
    old = load_calib() or {}
    for k in ("hangup_offset", "hangup_template", "answer_offset"):
        if old.get(k):
            calib[k] = old[k]
    save_calib(calib)
    print(f"\n校准完成 -> {CALIB_FILE}")
    print(json.dumps(calib, ensure_ascii=False, indent=2))
    print("\n测试拨号(不实际点击): python -m autodial.cli dial 测试联系人 --dry-run")
    print("如需校准挂断按钮(一般无需): python -m autodial.cli calibrate --hangup")


def calibrate_hangup_button():
    """在真实通话界面捕获挂断按钮 (备用回退; 通常 UIA 能直接找到无需校准)。"""
    ensure_data_dir()
    print("=" * 60)
    print("微信自动拨号 - 校准向导 (挂断按钮)")
    print("=" * 60)
    print("注意: 请先发起或接听一通真实通话, 让界面上出现【挂断】按钮。")

    w = find_wechat_window()
    if w is None:
        print("[错误] 未找到微信窗口。")
        sys.exit(1)

    x, y = capture_button("挂断")
    w2 = find_wechat_window()
    if w2 is None:
        print("[错误] 捕获时找不到微信窗口了。")
        sys.exit(1)
    r2 = w2.rectangle()
    offset = {"x": x - int(r2.left), "y": y - int(r2.top)}

    tmpl = None
    try:
        import pyautogui
        shot = pyautogui.screenshot(region=(x - CROP, y - CROP, CROP * 2, CROP * 2))
        shot.save(TMPL_HANGUP_PATH)
        tmpl = TMPL_HANGUP_PATH
        print(f"挂断按钮模板已保存: {tmpl}")
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 模板截图失败({e}), 只保存坐标。")

    from autodial.taskfile import load_calib
    calib = load_calib() or {}
    calib["hangup_offset"] = offset
    if tmpl:
        calib["hangup_template"] = tmpl
    save_calib(calib)
    print(f"\n挂断校准完成 -> {CALIB_FILE}")
    print(json.dumps(calib, ensure_ascii=False, indent=2))


def run(hangup: bool = False):
    if hangup:
        calibrate_hangup_button()
    else:
        calibrate_dial_button()


if __name__ == "__main__":
    run()
