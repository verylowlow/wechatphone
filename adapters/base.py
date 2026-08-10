# -*- coding: utf-8 -*-
"""App adapter base: per-app configuration + the shared UIA automation engine.

Design (2026-08-07 多应用扩展):
  The audio bridge core (loopback capture + CABLE inject + Realtime) is
  device-level and completely app-agnostic. The ONLY layer that differs
  between WeChat / DingTalk / WeCom is UI automation: window titles,
  search hotkey, button names for incoming/hangup, and whether the app
  needs the system default mic switched (WeChat has no persistent
  in-app device setting; DingTalk/WeCom can pick devices inside the app).

So an "adapter" is a declarative AppConfig; all generic logic lives here.
If a future app needs special behavior, add a field or override a function.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AppConfig:
    key: str                          # "wechat" | "dingtalk" | "wecom"
    display_name: str                 # "微信" ...
    # 主窗口定位: 先按 partial 关键词收集候选, 再优先 exact 标题
    window_titles_exact: tuple = ()
    window_titles_partial: tuple = ()
    # 拨号流程参数
    search_hotkey: str = "^f"         # 主界面搜索快捷键 (实测可改)
    search_result_wait: float = 1.2   # 粘贴联系人名后等待搜索结果
    open_chat_wait: float = 1.5       # Enter 打开会话后等待渲染
    post_dial_wait: float = 2.0       # 点击拨号按钮后等待通话界面
    # 来电/挂断按钮的 UIA 名称 (实测后可扩充)
    answer_button_names: tuple = ("接听", "接受", "接起")
    hangup_button_names: tuple = ("挂断", "结束通话", "结束")
    video_keywords: tuple = ("视频",)
    # 从来电弹窗标题提取主叫人时要剔除的噪声词
    caller_noise_words: tuple = ("语音通话", "视频通话", "邀请你", "邀请与您", "来电")
    # True = 该 App 无应用内设备持久设置, 跟随系统默认麦克风 -> 需要 DefaultMicSwitch
    # (微信如此; 钉钉/企微可在应用内直接选麦克风/扬声器, 无需切系统默认)
    mic_follows_system_default: bool = False
    # UI 自动化引擎: "uia" = 旧版 pywinauto 控件树; "vision41" = 微信4.1+ 视觉方案
    # (模板匹配+OCR+颜色连通域; 4.1 起 UIA 树为空, 必须走视觉)
    ui_engine: str = "uia"
    # 给用户的设备配置提示
    setup_hint: str = ""
    # 校准数据文件名 (相对 data/ 目录)
    calib_filename: str = ""

    def __post_init__(self):
        if not self.calib_filename:
            self.calib_filename = f"autodial_calib_{self.key}.json"


# ---------------- 通用 UIA 引擎 ----------------

def _desktop_windows():
    from pywinauto import Desktop
    return Desktop(backend="uia").windows()


def find_main_window(cfg: AppConfig):
    """按配置定位 App 主窗口; 找不到返回 None (调用方决定抛什么错)。"""
    try:
        windows = _desktop_windows()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"枚举窗口失败: {e}")
    candidates = []
    for w in windows:
        try:
            title = w.window_text()
        except Exception:
            continue
        if title and any(k in title for k in cfg.window_titles_partial):
            candidates.append(w)
    if not candidates:
        return None
    for w in candidates:
        try:
            if w.window_text().strip() in cfg.window_titles_exact:
                return w
        except Exception:
            continue
    return candidates[0]


def _buttons(win):
    try:
        return win.descendants(control_type="Button")
    except Exception:
        return []


def find_incoming(cfg: AppConfig):
    """扫描所有 UIA 窗口, 找来电弹窗的接听按钮 (按钮名存在即视为有来电).

    Returns (popup_window, answer_button, is_video) or None.
    """
    try:
        windows = _desktop_windows()
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
            if name and any(k in name for k in cfg.answer_button_names):
                is_video = False
                try:
                    text_all = title + " " + w.text_block()
                except Exception:
                    text_all = title
                is_video = any(k in text_all for k in cfg.video_keywords)
                return w, b, is_video
    return None


def guess_caller_name(popup, cfg: AppConfig) -> str:
    """从来电弹窗标题提取主叫人昵称 (尽力而为, 失败返回 '对方')."""
    try:
        title = (popup.window_text() or "").strip()
        for noise in cfg.caller_noise_words:
            title = title.replace(noise, "")
        title = title.strip(" -·|:：\t")
        if title and len(title) <= 30:
            return title
    except Exception:
        pass
    return "对方"


def find_hangup_button(cfg: AppConfig):
    """Returns (window, button) of the hangup control, or None."""
    try:
        windows = _desktop_windows()
    except Exception:
        return None
    for w in windows:
        for b in _buttons(w):
            try:
                name = (b.window_text() or "").strip()
            except Exception:
                continue
            if name and any(k in name for k in cfg.hangup_button_names):
                return w, b
    return None
