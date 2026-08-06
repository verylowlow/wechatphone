# -*- coding: utf-8 -*-
"""autodial CLI.

Examples:
  python -m autodial.cli calibrate
  python -m autodial.cli windows
  python -m autodial.cli dial 张三 --task "回访确认收货" --dry-run
  python -m autodial.cli batch names.txt --task "统一通知话术"
  python -m autodial.cli batch tasks.json          # [{"contact":..,"task":..}, ...]

Batch file formats:
  names.txt : one contact per line, shared --task
  tasks.json: list of {"contact": str, "task": str, "note": str?}
"""
from __future__ import annotations

import argparse
import json
import sys


def cmd_calibrate(args):
    from autodial.calibrate import run
    run(hangup=args.hangup)


def cmd_windows(_args):
    from pywinauto import Desktop
    print("=== 窗口列表 (UIA) ===")
    for w in Desktop(backend="uia").windows():
        try:
            t = w.window_text()
            if t and t.strip():
                print(f"  {t}")
        except Exception:
            continue


def cmd_dial(args):
    from autodial.dialer import WeChatDialer, DialError
    try:
        d = WeChatDialer(dry_run=args.dry_run)
        info = d.dial(args.contact, task=args.task or "", note=args.note or "")
        print(json.dumps(info, ensure_ascii=False, indent=2))
    except DialError as e:
        print(f"[错误] {e}")
        sys.exit(1)


def _load_batch_items(path: str, shared_task: str) -> list[dict]:
    if path.endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = []
        for d in data:
            if isinstance(d, str):
                items.append({"contact": d, "task": shared_task})
            else:
                items.append({"contact": d.get("contact", ""),
                              "task": d.get("task", shared_task),
                              "note": d.get("note", "")})
        return items
    # 纯文本: 一行一个联系人名
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            name = line.strip()
            if name and not name.startswith("#"):
                items.append({"contact": name, "task": shared_task})
    return items


def cmd_batch(args):
    from autodial.batch import BatchDialer
    from autodial.dialer import DialError
    items = _load_batch_items(args.file, args.task or "")
    if not items:
        print("没有可拨打的联系人。")
        sys.exit(1)
    print(f"共 {len(items)} 个联系人待拨打:")
    for it in items:
        print(f"  - {it['contact']}  任务: {it['task'][:30]}")
    try:
        b = BatchDialer(dry_run=args.dry_run,
                        fixed_gap_sec=args.gap,
                        call_timeout_sec=args.timeout)
    except DialError as e:
        print(f"[错误] {e}")
        sys.exit(1)
    results = b.run(items)
    print("\n=== 批量拨号结果 ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(prog="autodial", description="微信语音自动拨号")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("calibrate", help="校准向导: 记录语音通话按钮位置")
    p.add_argument("--hangup", action="store_true",
                   help="改为校准挂断按钮(需先有一通真实通话)")
    p.set_defaults(fn=cmd_calibrate)
    sub.add_parser("windows", help="列出窗口(调试用)").set_defaults(fn=cmd_windows)

    p = sub.add_parser("dial", help="拨打单个联系人")
    p.add_argument("contact")
    p.add_argument("--task", default="", help="本次通话任务内容(注入给 AI)")
    p.add_argument("--note", default="", help="备注")
    p.add_argument("--dry-run", action="store_true", help="走全流程但不实际点击")
    p.set_defaults(fn=cmd_dial)

    p = sub.add_parser("batch", help="批量拨打")
    p.add_argument("file", help="names.txt 或 tasks.json")
    p.add_argument("--task", default="", help="共享任务(纯文本名单时用)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--gap", type=float, default=30.0, help="无 calllog 时两通之间固定等待秒数")
    p.add_argument("--timeout", type=float, default=600.0, help="单通等待结束超时秒数")
    p.set_defaults(fn=cmd_batch)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
