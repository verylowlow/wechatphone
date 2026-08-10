# -*- coding: utf-8 -*-
"""企业微信全流程实测: 激活→通讯录→搜索(清除+精确)→点首条→语音通话→挂断。

每步截图存 data/wecom_step*.png 便于校验。拨打后立即挂断, 不做真实通话。
"""
import sys
import time

import pyautogui

sys.path.insert(0, "D:/dev/wechatphone")
from autodial import wecom_ui, wx41

CONTACT = "高原"


def save(h, name):
    shot = wx41._shot(h)
    path = f"data/{name}.png"
    shot.save(path)
    print(f"  [shot] {path} {shot.size}", flush=True)


def main():
    print("=== 步骤1: 激活企业微信到前台 ===", flush=True)
    h = wecom_ui.focus_main()
    print("hwnd:", h, "foreground ok")
    save(h, "wecom_step1_main")

    print("=== 步骤2: 点击左侧目录树【通讯录】 ===", flush=True)
    r = wecom_ui.open_contacts()
    print("  result:", r)
    save(h, "wecom_step2_contacts")

    print(f"=== 步骤3: 搜索框清除 + 精确输入 [{CONTACT}] ===", flush=True)
    hits = wecom_ui.search_contact(CONTACT)
    print("  精确命中:", [(x["text"], round(x["cx"]), round(x["cy"])) for x in hits])
    save(h, "wecom_step3_search")
    if not hits:
        print("[FAIL] 无精确命中, 终止")
        sys.exit(1)

    print("=== 步骤4: 点击查询结果第一项 ===", flush=True)
    r = wecom_ui.open_first_result(hits)
    print("  result:", r)
    save(h, "wecom_step4_panel")

    print("=== 步骤5: 点击客户面板【语音通话】 ===", flush=True)
    info = wecom_ui.start_voice_call()
    print("  result:", info)
    save(h, "wecom_step5_calling")

    print("=== 步骤6: 通话/呼叫界面确认 ===", flush=True)
    in_call = wecom_ui.in_call()
    print("  大红圆(通话中):", in_call)
    # 给呼叫界面一点时间渲染再截图
    time.sleep(1.0)
    save(h, "wecom_step6_incall")

    print("=== 步骤7: 点击大红圆挂断 ===", flush=True)
    r = wecom_ui.hang_up()
    print("  hangup:", r)
    time.sleep(1.0)
    save(h, "wecom_step7_after_hangup")

    still = wecom_ui.in_call()
    print("  挂断后仍有红圆:", still)
    print("=== E2E 完成 ===", flush=True)
    print("RESULT:", "OK" if r.get("ok") and not still else "CHECK")


if __name__ == "__main__":
    main()
