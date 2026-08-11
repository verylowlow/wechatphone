# -*- coding: utf-8 -*-
"""钉钉全流程实测: 激活→通讯录(UIA)→搜索(清除+精确)→联系人tab→行尾语音图标→挂断。

每步截图存 data/dt_step*.png 便于校验。拨打后立即挂断, 不做真实通话。
用法: python tools/dingtalk_e2e_test.py [联系人名]  (默认 北五7班16号张若初)
"""
import sys
import time

import pyautogui

sys.path.insert(0, "D:/dev/wechatphone")
from autodial import dingtalk_ui

CONTACT = sys.argv[1] if len(sys.argv) > 1 else "北五7班16号张若初"


def save(name):
    shot = pyautogui.screenshot()
    path = f"data/{name}.png"
    shot.save(path)
    print(f"  [shot] {path} {shot.size}", flush=True)


def main():
    print("=== 步骤1: 激活钉钉到前台 ===", flush=True)
    h = dingtalk_ui.focus_main()
    print("hwnd:", h, "foreground ok")
    save("dt_step1_main")

    print("=== 步骤2: 左侧目录树【通讯录】(UIA) ===", flush=True)
    r = dingtalk_ui.open_contacts()
    print("  result:", r)
    save("dt_step2_contacts")

    print(f"=== 步骤3: 搜索框清除 + 精确输入 [{CONTACT}] + 联系人tab ===", flush=True)
    hits = dingtalk_ui.search_contact(CONTACT)
    print("  精确命中:", [(x["text"], round(x["cx"]), round(x["cy"])) for x in hits])
    save("dt_step3_results")
    if not hits:
        print("  (精确命中为空: 回退模板匹配首行结果)", flush=True)

    print("=== 步骤4+5: 首条结果行尾【语音通话】图标 → 通话面板 ===", flush=True)
    info = dingtalk_ui.start_voice_call(hits)
    print("  result:", info)
    save("dt_step4_calling")
    if not info.get("call_up"):
        print("[FAIL] 未进入通话面板, 终止")
        sys.exit(1)

    print("=== 步骤6: 通话面板确认 (OCR 挂断) ===", flush=True)
    inc = dingtalk_ui.in_call()
    print("  in_call:", inc)

    print("=== 步骤7: 红圆挂断 ===", flush=True)
    r = dingtalk_ui.hang_up()
    print("  hangup:", r)
    time.sleep(1.0)
    save("dt_step5_after_hangup")

    still = dingtalk_ui.in_call()
    print("  挂断后仍在通话面板:", still)
    print("=== E2E 完成 ===", flush=True)
    print("RESULT:", "OK" if r.get("ok") and not still else "CHECK")


if __name__ == "__main__":
    main()
