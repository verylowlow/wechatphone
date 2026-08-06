"""SQLite storage for call records.

Schema:
  calls  - one row per detected call session
  events - conversation timeline entries (user said / AI said / tool call / note)

DB lives at data/calls.sqlite (gitignored). All ops are thread-safe (single lock).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "calls.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_sec INTEGER DEFAULT 0,
    summary TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,        -- remote | ai | tool | note
    text TEXT NOT NULL,
    extra TEXT DEFAULT '',     -- optional JSON (e.g. tool name/args)
    FOREIGN KEY(call_id) REFERENCES calls(call_id)
);
CREATE INDEX IF NOT EXISTS idx_events_call ON events(call_id);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class CallStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------- write ----------

    def create_call(self, call_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO calls(call_id, started_at) VALUES(?, ?)",
                (call_id, _now()),
            )
            self._conn.commit()

    def end_call(self, call_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE calls SET ended_at=? WHERE call_id=? AND ended_at IS NULL",
                (_now(), call_id),
            )
            self._conn.execute(
                "UPDATE calls SET duration_sec="
                " CAST((julianday(ended_at)-julianday(started_at))*86400 AS INTEGER)"
                " WHERE call_id=? AND ended_at IS NOT NULL",
                (call_id,),
            )
            self._conn.commit()

    def add_event(self, call_id: str, kind: str, text: str, extra: dict | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(call_id, ts, kind, text, extra) VALUES(?,?,?,?,?)",
                (call_id, _now(), kind, text,
                 json.dumps(extra, ensure_ascii=False) if extra else ""),
            )
            self._conn.commit()

    def set_summary(self, call_id: str, summary: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE calls SET summary=? WHERE call_id=?", (summary, call_id)
            )
            self._conn.commit()

    # ---------- read ----------

    def list_calls(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.*, COUNT(e.event_id) AS event_count "
                "FROM calls c LEFT JOIN events e ON e.call_id=c.call_id "
                "GROUP BY c.call_id ORDER BY c.started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_call(self, call_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM calls WHERE call_id=?", (call_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_events(self, call_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, kind, text, extra FROM events WHERE call_id=? "
                "ORDER BY event_id ASC",
                (call_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["extra"] = json.loads(d["extra"]) if d["extra"] else {}
            except Exception:
                d["extra"] = {}
            out.append(d)
        return out
