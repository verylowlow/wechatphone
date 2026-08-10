# -*- coding: utf-8 -*-
"""WeCom (企业微信) adapter。

2026-08-10 实测 (WXWork.exe, 2560x1600 默认布局):
  1. 主窗口 class=WeWorkWindow / title=企业微信 (FindWindow 直达);
  2. UIA 树只有 4 个空 Pane (自绘渲染), 与微信 4.1 同病 -> 视觉方案
     ui_engine="wecom_vision" (autodial/wecom_ui.py):
     激活 → 左栏第10项通讯录 → 搜索框清除+粘贴精确名 → 点第一条结果 →
     客户面板【语音通话】 → 通话界面下方大红圆 = 挂断。
  3. 企业微信应用内可选麦克风/扬声器 -> mic_follows_system_default=False,
     通话时在企微音频设置里选: 麦克风=CABLE Output, 扬声器=本机物理扬声器。
"""
from __future__ import annotations

from adapters.base import AppConfig

WECOM = AppConfig(
    key="wecom",
    display_name="企业微信",
    window_titles_exact=("企业微信", "WeCom"),
    window_titles_partial=("企业微信", "WeCom", "WXWork"),
    search_hotkey="^f",               # 视觉方案不依赖
    search_result_wait=1.5,
    open_chat_wait=1.5,
    post_dial_wait=2.5,
    answer_button_names=("接听", "接受", "接起", "Answer"),
    hangup_button_names=("挂断", "结束通话", "结束", "Hang up"),
    video_keywords=("视频",),
    caller_noise_words=("语音通话", "视频通话", "邀请你", "邀请与您", "来电", "呼叫"),
    ui_engine="wecom_vision",
    mic_follows_system_default=False,
    setup_hint=(
        "企业微信设置: 进入通话后打开音频/设备设置, 选 麦克风=CABLE Output, "
        "扬声器=本机物理扬声器 (企微支持应用内选择, 无需切系统默认设备)。"
    ),
)
