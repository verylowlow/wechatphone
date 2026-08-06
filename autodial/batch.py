"""Batch dialer: dial a list of (contact, task) sequentially.

Between calls it waits for the previous call to actually finish by watching
the calllog store:
  - a new call row appears (dial succeeded & someone answered / AI engaged)
  - that call then closes (ended_at set), or a hard timeout expires
This keeps the module decoupled: it only reads data/calls.sqlite.
"""
from __future__ import annotations

import time

from autodial.dialer import WeChatDialer, DialError
from autodial.taskfile import TASK_FILE


def _calllog_store():
    try:
        from calllog.store import CallStore
        return CallStore()
    except Exception as e:  # noqa: BLE001
        print(f"[BATCH] calllog 不可用({e}), 改用固定间隔等待", flush=True)
        return None


def _calls_after(store, ts: float) -> list:
    """calls whose started_at is on/after ts (local time strings)."""
    if store is None:
        return []
    mark = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    return [c for c in store.list_calls(limit=50) if c["started_at"] >= mark]


class BatchDialer:
    def __init__(self, dry_run: bool = False,
                 fixed_gap_sec: float = 30.0,
                 call_timeout_sec: float = 600.0):
        self.dialer = WeChatDialer(dry_run=dry_run)
        self.store = _calllog_store()
        self.fixed_gap_sec = fixed_gap_sec
        self.call_timeout_sec = call_timeout_sec

    def _wait_for_call_end(self, started_after: float) -> None:
        """Block until the call that started after `started_after` closes."""
        if self.store is None:
            print(f"[BATCH] 固定等待 {self.fixed_gap_sec:.0f}s", flush=True)
            time.sleep(self.fixed_gap_sec)
            return
        deadline = time.time() + self.call_timeout_sec
        seen = None
        while time.time() < deadline:
            calls = _calls_after(self.store, started_after)
            if calls:
                newest = calls[0]  # list_calls 按 started_at DESC
                if newest["ended_at"]:
                    print(f"[BATCH] 通话 {newest['call_id']} 已结束, 继续下一个", flush=True)
                    time.sleep(2.0)  # 喘息, 让微信回到可搜索状态
                    return
                seen = newest["call_id"]
            time.sleep(2.0)
        print(f"[BATCH] 等待超时({self.call_timeout_sec:.0f}s)"
              f"{' (通话 ' + seen + ' 仍未结束)' if seen else ' (未检测到通话开始)'}, 继续下一个", flush=True)

    def run(self, items: list[dict]) -> list[dict]:
        """items: [{'contact': str, 'task': str, 'note': str?}, ...]"""
        results = []
        total = len(items)
        for i, it in enumerate(items, 1):
            contact = it.get("contact", "").strip()
            task = it.get("task", "")
            note = it.get("note", "")
            if not contact:
                results.append({"contact": contact, "status": "skipped", "error": "空联系人名"})
                continue
            print(f"\n[BATCH] ({i}/{total}) 拨打: {contact}", flush=True)
            started_after = time.time() - 1.0
            try:
                info = self.dialer.dial(contact, task=task, note=note)
                results.append({"contact": contact, "status": "dialed", **info})
                if not self.dialer.dry_run and i < total:
                    self._wait_for_call_end(started_after)
            except DialError as e:
                print(f"[BATCH] 拨号失败: {e}", flush=True)
                results.append({"contact": contact, "status": "error", "error": str(e)})
            except Exception as e:  # noqa: BLE001
                print(f"[BATCH] 异常: {e}", flush=True)
                results.append({"contact": contact, "status": "error", "error": str(e)})
        return results
