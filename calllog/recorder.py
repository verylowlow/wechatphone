"""Call session recorder: segments continuous voice activity into calls and
persists the conversation timeline.

Call boundary detection (no WeChat API available, activity-based):
- a call starts at the first remote speech / transcript event when no call
  is active;
- it stays open while activity keeps arriving;
- after IDLE_END_SEC without any activity the call is closed automatically;
- recorder.close() flushes any open call (bridge exit).

Thread safety: all state guarded by a lock; sqlite writes go through CallStore.
"""
from __future__ import annotations

import threading
import time

from calllog.store import CallStore

IDLE_END_SEC = 90  # 90s 无任何活动 -> 判定通话结束


class CallRecorder:
    def __init__(self, store: CallStore | None = None, on_call_closed=None,
                 app: str = ""):
        self.store = store or CallStore()
        self.on_call_closed = on_call_closed  # 通话收尾时的回调 (如清理 autodial 任务文件)
        self.app = app or ""                  # 当前服务的应用端 (wechat/dingtalk/wecom)
        self._lock = threading.Lock()
        self.call_id: str | None = None
        self.last_activity = 0.0
        self.pending_contact = ""  # 通话开始前已知对端(如 autodial 外呼)时暂存
        # 累积本轮 AI 回复文本, 用于生成通话摘要
        self._ai_turns: list[str] = []
        self._remote_turns: list[str] = []

    # ---------- lifecycle ----------

    def _ensure_call(self) -> str:
        """Must be called with self._lock held. Returns active call_id."""
        if self.call_id is None:
            self.call_id = time.strftime("%Y%m%d-%H%M%S")
            self.store.create_call(self.call_id, app=self.app,
                                   contact=self.pending_contact)
            self._ai_turns = []
            self._remote_turns = []
            print(f"[CALLLOG] 通话开始: {self.call_id} "
                  f"(app={self.app or '-'}, contact={self.pending_contact or '-'})", flush=True)
        self.last_activity = time.time()
        return self.call_id

    def set_contact(self, contact: str) -> None:
        """确定对端身份后回填 (来电自动接听识别主叫人 / 外呼任务注入联系人)。
        通话尚未开始时暂存, 随 create_call 一并落库。"""
        contact = (contact or "").strip()
        if not contact or contact == "对方":
            return
        with self._lock:
            if self.call_id is None:
                self.pending_contact = contact
                return
            cid = self.call_id
        self.store.update_call_meta(cid, contact=contact)

    def check_idle(self) -> None:
        """Close the call if idle too long. Call periodically (e.g. from watch loop)."""
        with self._lock:
            if self.call_id and time.time() - self.last_activity > IDLE_END_SEC:
                self._close_call_locked()

    def close(self) -> None:
        with self._lock:
            if self.call_id:
                self._close_call_locked()

    def _close_call_locked(self) -> None:
        summary = ""
        if self._remote_turns or self._ai_turns:
            first_remote = self._remote_turns[0] if self._remote_turns else ""
            summary = f"对方: {first_remote[:40]} | AI回复 {len(self._ai_turns)} 次"
        self.store.set_summary(self.call_id, summary)
        self.store.end_call(self.call_id)
        print(f"[CALLLOG] 通话结束: {self.call_id}", flush=True)
        self.call_id = None
        if self.on_call_closed:
            try:
                self.on_call_closed()
            except Exception as e:  # noqa: BLE001
                print(f"[CALLLOG] on_call_closed 回调异常: {e}", flush=True)

    # ---------- events (called from bridge recv loop) ----------

    def on_remote_speech(self) -> None:
        """远端开始说话 (speech_started): 视为通话活动心跳."""
        with self._lock:
            self._ensure_call()

    def on_remote_transcript(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            cid = self._ensure_call()
            self._remote_turns.append(text)
        self.store.add_event(cid, "remote", text)

    def on_ai_transcript(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            cid = self._ensure_call()
            self._ai_turns.append(text)
        self.store.add_event(cid, "ai", text)

    def on_ambient(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            cid = self._ensure_call()
        self.store.add_event(cid, "note", f"[环境音] {text}")

    def on_note(self, text: str) -> None:
        """通用备注事件 (如挂断原因/来电接听记录)。"""
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            cid = self._ensure_call()
        self.store.add_event(cid, "note", text)

    def on_tool_call(self, name: str, arguments: str, result_count: int | None = None) -> None:
        with self._lock:
            cid = self._ensure_call()
        extra = {"name": name, "arguments": arguments}
        if result_count is not None:
            extra["result_count"] = result_count
        self.store.add_event(cid, "tool", f"调用工具 {name}", extra)
