# -*- coding: utf-8 -*-
"""WeChat adapter.

历史:
  - 2026-08-05 微信 3.9.x 实测通过 (UIA 控件树方案)。
  - 2026-08-10 微信升级到 4.1.12.26: 主窗口 UIA 树只剩空 Pane (MMUI 自绘渲染),
    旧方案全废, 改用视觉方案 ui_engine="vision41" (autodial/wx41.py),
    拨号走 通讯录→搜索→点击命中→语音通话→菜单第一项, 挂断=大红圆, 接听=大绿圆。
    同名重复检测: 搜索结果 OCR, 多条完全一致 → 终止并记入通话记录。
"""
from __future__ import annotations

from adapters.base import AppConfig

WECHAT = AppConfig(
    key="wechat",
    display_name="微信",
    window_titles_exact=("微信",),
    window_titles_partial=("微信", "WeChat"),
    search_hotkey="^f",               # 3.9 实测可用; 4.1 视觉方案不依赖它
    search_result_wait=1.2,
    open_chat_wait=1.5,
    post_dial_wait=2.0,
    # 4.1 视觉方案不依赖按钮名; 保留供其它引擎/日志参考
    answer_button_names=("接听", "接受", "接起"),
    hangup_button_names=("挂断", "结束通话", "结束"),
    video_keywords=("视频",),
    caller_noise_words=("微信语音通话", "微信视频通话", "语音通话", "视频通话",
                        "邀请你", "邀请与您", "来电"),
    # 微信无应用内设备持久设置, 跟随系统默认麦克风(eCommunications) -> 需要切换
    mic_follows_system_default=True,
    ui_engine="vision41",
    setup_hint=(
        "微信设置: 发起通话后在通话窗口选 麦克风=CABLE Output, 扬声器=本机物理扬声器; "
        "系统默认输出保持物理扬声器。bridge 启动时会自动把系统默认麦克风切到 CABLE Output。"
    ),
)
