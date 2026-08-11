# -*- coding: utf-8 -*-
"""DingTalk adapter。

2026-08-10 实测 (DingTalk.exe, 含任意改名的定制版; 主窗口按 Win32 类名
StandardFrame_DingTalk 定位, 不依赖窗口标题; 2048x1280 非最大化 & 最大化均验证通过):
  1. 左侧导航 UIA 可访问 (auto_id=navigator_view.contact_contact 等),
     内容区为内嵌 Chrome 自绘 -> 视觉方案 ui_engine="dingtalk_vision"
     (autodial/dingtalk_ui.py):
     激活(物理点击标题栏) → 左栏第5项通讯录(UIA) → Ctrl+Shift+F 搜索(清除+粘贴) →
     【联系人】tab → 首条结果行尾【语音通话】图标(模板) → 小通话面板
     → 挂断 = OCR"挂断"文本上方红圆。
  2. 坐标统一用屏幕坐标系 (不依赖窗口 rect), 窗口形态变化不影响。
  3. 钉钉应用内可选麦克风/扬声器 -> mic_follows_system_default=False,
     通话时在钉钉音频设置里选: 麦克风=CABLE Output, 扬声器=本机物理扬声器。
"""
from __future__ import annotations

from adapters.base import AppConfig

DINGTALK = AppConfig(
    key="dingtalk",
    display_name="钉钉",
    # 视觉引擎(dingtalk_vision)按类名 StandardFrame_DingTalk 定位, 不依赖标题;
    # 以下标题仅服务 UIA 通用回退路径 (如 cli windows / find_main_window)。
    window_titles_exact=("钉钉", "DingTalk"),
    window_titles_partial=("钉钉", "DingTalk"),
    search_hotkey="^+f",              # Ctrl+Shift+F 全局搜索 (视觉方案不依赖)
    search_result_wait=1.8,
    open_chat_wait=1.2,
    post_dial_wait=2.5,
    answer_button_names=("接听", "接受", "接起", "Answer"),
    hangup_button_names=("挂断", "结束通话", "结束", "Hang up"),
    video_keywords=("视频",),
    caller_noise_words=("语音通话", "视频通话", "邀请你", "邀请与您", "来电", "呼叫"),
    ui_engine="dingtalk_vision",
    mic_follows_system_default=False,
    setup_hint=(
        "钉钉设置: 进入通话后打开音频/设备设置, 选 麦克风=CABLE Output, "
        "扬声器=本机物理扬声器 (钉钉支持应用内选择, 无需切系统默认设备)。"
    ),
)
