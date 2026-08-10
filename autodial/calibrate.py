# -*- coding: utf-8 -*-
"""Calibration wizard for autodial (per-app).

Records into data/autodial_calib_<app>.json:
  1. button_offset: voice-call button position relative to the app window's
     top-left corner (coordinate fallback).
  2. button_template: a small screenshot of the button for template matching
     (primary locating method; robust to window moving/resizing).

UX: user manually moves the mouse over the button, presses Ctrl+C in the
terminal -> we capture the position and crop the template from the screen.
"""
from __future__ import annotations

import json
import sys
import time

from adapters.base import AppConfig, find_main_window
from adapters import get_app, DEFAULT_APP
from autodial.taskfile import ensure_data_dir, save_calib, load_calib, tmpl_path

CROP = 28  # 模板裁剪半径 (像素): 以按钮为中心裁 2CROP x 2CROP


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


def calibrate_dial_button(cfg: AppConfig):
    ensure_data_dir()
    print("=" * 60)
    print(f"{cfg.display_name}自动拨号 - 校准向导 (语音通话拨号按钮)")
    print("=" * 60)

    w = find_main_window(cfg)
    if w is None:
        print(f"[错误] 未找到 {cfg.display_name} 窗口。请先登录并保持主窗口打开。")
        print(f"       可用 `python -m autodial.cli windows` 查看所有窗口标题来排查。")
        sys.exit(1)
    r = w.rectangle()
    print(f"找到窗口: '{w.window_text()}' @ ({r.left},{r.top}) {r.width()}x{r.height()}")
    print(f"\n请先在 {cfg.display_name} 里随便打开一个聊天会话。")

    x, y = capture_button("语音通话(拨号)")

    # 重新获取窗口矩形 (用户可能移动了窗口)
    w2 = find_main_window(cfg)
    if w2 is None:
        print("[错误] 捕获时找不到应用窗口了。")
        sys.exit(1)
    r2 = w2.rectangle()
    offset_x = x - int(r2.left)
    offset_y = y - int(r2.top)
    if not (0 <= offset_x <= r2.width() and 0 <= offset_y <= r2.height()):
        print(f"[警告] 鼠标位置不在应用窗口内 (相对偏移 {offset_x},{offset_y}), "
              f"请确认该窗口是前台活动窗口; 仍将保存。")

    # 裁剪按钮模板图
    tm_path = tmpl_path(cfg.key, "call")
    tmpl_ok = False
    try:
        import pyautogui
        shot = pyautogui.screenshot(region=(x - CROP, y - CROP, CROP * 2, CROP * 2))
        shot.save(tm_path)
        tmpl_ok = True
        print(f"按钮模板已保存: {tm_path} ({CROP*2}x{CROP*2}px)")
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 模板截图失败({e}), 将只使用坐标回退。")

    calib = {
        "app": cfg.key,
        "button_offset": {"x": offset_x, "y": offset_y},
        "button_template": tm_path if tmpl_ok else None,
        "window_title": w2.window_text(),
        "calibrated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 保留已有的挂断/接听按钮校准
    old = load_calib(cfg.key) or {}
    for k in ("hangup_offset", "hangup_template", "answer_offset"):
        if old.get(k):
            calib[k] = old[k]
    save_calib(calib, cfg.key)
    from autodial.taskfile import calib_path
    print(f"\n校准完成 -> {calib_path(cfg.key)}")
    print(json.dumps(calib, ensure_ascii=False, indent=2))
    print(f"\n测试拨号(不实际点击): python -m autodial.cli dial 测试联系人 --app {cfg.key} --dry-run")
    print(f"如需校准挂断按钮(一般无需): python -m autodial.cli calibrate --app {cfg.key} --hangup")


def calibrate_hangup_button(cfg: AppConfig):
    """在真实通话界面捕获挂断按钮 (备用回退; 通常 UIA 能直接找到无需校准)。"""
    ensure_data_dir()
    print("=" * 60)
    print(f"{cfg.display_name}自动拨号 - 校准向导 (挂断按钮)")
    print("=" * 60)
    print("注意: 请先发起或接听一通真实通话, 让界面上出现【挂断】按钮。")

    w = find_main_window(cfg)
    if w is None:
        print(f"[错误] 未找到 {cfg.display_name} 窗口。")
        sys.exit(1)

    x, y = capture_button("挂断")
    w2 = find_main_window(cfg)
    if w2 is None:
        print("[错误] 捕获时找不到应用窗口了。")
        sys.exit(1)
    r2 = w2.rectangle()
    offset = {"x": x - int(r2.left), "y": y - int(r2.top)}

    tmpl = None
    tm_path = tmpl_path(cfg.key, "hangup")
    try:
        import pyautogui
        shot = pyautogui.screenshot(region=(x - CROP, y - CROP, CROP * 2, CROP * 2))
        shot.save(tm_path)
        tmpl = tm_path
        print(f"挂断按钮模板已保存: {tmpl}")
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 模板截图失败({e}), 只保存坐标。")

    calib = load_calib(cfg.key) or {}
    calib["hangup_offset"] = offset
    if tmpl:
        calib["hangup_template"] = tmpl
    save_calib(calib, cfg.key)
    from autodial.taskfile import calib_path
    print(f"\n挂断校准完成 -> {calib_path(cfg.key)}")
    print(json.dumps(calib, ensure_ascii=False, indent=2))


def run(hangup: bool = False, app: str = DEFAULT_APP):
    cfg = get_app(app)
    if hangup:
        calibrate_hangup_button(cfg)
    else:
        calibrate_dial_button(cfg)


if __name__ == "__main__":
    run()
