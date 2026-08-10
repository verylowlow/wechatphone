# -*- coding: utf-8 -*-
"""DingTalk adapter — 未经实测, 全部参数为尽力猜测, 请真机验证后修改本文件。

需要真机确认的点:
  1. 主窗口标题: 新版钉钉主窗口标题可能就是 "钉钉", 也可能带版本号后缀;
     用 `python -m autodial.cli windows --app dingtalk` 探测。
  2. 搜索快捷键: 钉钉常用 Ctrl+F / Ctrl+K, 不确定哪个聚焦联系人搜索;
     实测不对就改 search_hotkey ("^k" 即 Ctrl+K)。
  3. 来电弹窗按钮名: 可能是 "接听"/"接受", 也可能是图标按钮无文本
     (UIA 抓不到文本时, 接听会自动失败并打印日志, 可用校准坐标回退)。
  4. 挂断按钮名: 通话条可能是 "结束通话"/"挂断"。
  5. 钉钉应用内可选麦克风/扬声器 -> mic_follows_system_default=False,
     通话时请在钉钉音频设置里手动选: 麦克风=CABLE Output, 扬声器=本机物理扬声器。
"""
from __future__ import annotations

from adapters.base import AppConfig

DINGTALK = AppConfig(
    key="dingtalk",
    display_name="钉钉",
    window_titles_exact=("钉钉", "DingTalk"),
    window_titles_partial=("钉钉", "DingTalk"),
    search_hotkey="^f",               # 待验证; 可能是 ^k
    search_result_wait=1.5,
    open_chat_wait=1.5,
    post_dial_wait=2.5,
    answer_button_names=("接听", "接受", "接起", "Answer"),
    hangup_button_names=("挂断", "结束通话", "结束", "Hang up"),
    video_keywords=("视频",),
    caller_noise_words=("语音通话", "视频通话", "邀请你", "邀请与您", "来电", "呼叫"),
    mic_follows_system_default=False,
    setup_hint=(
        "钉钉设置: 进入通话后打开音频/设备设置, 选 麦克风=CABLE Output, "
        "扬声器=本机物理扬声器 (钉钉支持应用内选择, 无需切系统默认设备)。"
    ),
)
