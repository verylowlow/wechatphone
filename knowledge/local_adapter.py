"""Local knowledge adapter: SQLite storage + in-memory numpy hybrid retrieval.

Design ported from newcallcall (battle-tested in its phone-sales product):
- Hybrid scoring: 0.65 * cosine(vector) + 0.35 * keyword overlap
- Chunking: 600 chars / 80 overlap, paragraph-aware
- Tiered injection (kb_injection idea):
    FULL      (total <= 6000 tokens): inject everything into instructions,
              search tool NOT needed
    RETRIEVAL (larger): inject pinned docs fully + doc index, model uses
              search_knowledge tool for details
- Pinned docs (e.g. price lists) are ALWAYS injected fully — the
  anti-hallucination last line of defense.

Storage: single SQLite file (data/kb.sqlite). Chunks + embeddings are loaded
into memory at startup; fine for personal/team scale (thousands of chunks).
When scale demands, swap in a ChromaAdapter / RAGFlowAdapter — same interface.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time

import numpy as np

from knowledge.base import DocInfo, KnowledgeAdapter, Snippet
from knowledge.embeddings import EmbeddingClient
from knowledge.parsers import parse_file

CHUNK_SIZE = 600
CHUNK_OVERLAP = 80
VECTOR_WEIGHT = 0.65
KEYWORD_WEIGHT = 0.35
# injection budgets (tokens ~= chars for Chinese, conservative)
FULL_BUDGET_TOKENS = 6000

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "kb.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    chunk_idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL,
    FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
"""


def _estimate_tokens(text: str) -> int:
    return len(text)  # conservative: 1 char ~ 1 token


def _chunk_text(text: str) -> list[str]:
    """Paragraph-aware chunking: fill chunks up to CHUNK_SIZE chars."""
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        # oversized paragraph: hard-split
        while len(para) > CHUNK_SIZE:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(para[:CHUNK_SIZE])
            para = para[CHUNK_SIZE - CHUNK_OVERLAP:]
        if len(buf) + len(para) + 1 > CHUNK_SIZE:
            if buf:
                chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def _tokenize(text: str) -> set[str]:
    try:
        import jieba
        return {t for t in jieba.cut(text) if t.strip()}
    except ImportError:
        toks = set(text.lower().split())
        cjk = "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
        for i in range(len(cjk) - 1):
            toks.add(cjk[i:i + 2])
        return toks


class LocalKnowledgeAdapter(KnowledgeAdapter):
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._embed = EmbeddingClient()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # in-memory indexes
        self._ids: list[int] = []
        self._doc_ids: list[str] = []
        self._texts: list[str] = []
        self._sources: list[str] = []
        self._pinned_flags: list[bool] = []
        self._toks: list[set[str]] = []
        self._mat: np.ndarray | None = None  # (N, D) normalized
        self._idf: dict[str, float] = {}
        self._reload()

    # ---------- storage ----------

    def _reload(self) -> None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.chunk_id, c.doc_id, c.text, c.embedding, d.filename, d.pinned "
                "FROM chunks c JOIN documents d ON d.doc_id = c.doc_id "
                "ORDER BY c.chunk_id"
            ).fetchall()
            self._ids = [r[0] for r in rows]
            self._doc_ids = [r[1] for r in rows]
            self._texts = [r[2] for r in rows]
            self._sources = [r[4] for r in rows]
            self._pinned_flags = [bool(r[5]) for r in rows]
            self._toks = [_tokenize(t) for t in self._texts]
            if rows:
                self._mat = np.stack(
                    [np.frombuffer(r[3], dtype=np.float32) for r in rows]
                )
            else:
                self._mat = None
            # idf
            n = max(len(rows), 1)
            df: dict[str, int] = {}
            for toks in self._toks:
                for t in toks:
                    df[t] = df.get(t, 0) + 1
            self._idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}

    # ---------- KnowledgeAdapter ----------

    def ingest_file(self, path: str) -> DocInfo:
        text = parse_file(path)
        if not text.strip():
            from knowledge.parsers import ParseError
            raise ParseError(f"file parsed to empty text: {path}")
        filename = os.path.basename(path)
        doc_id = hashlib.sha1(f"{filename}:{len(text)}:{time.time_ns()}".encode()).hexdigest()[:16]
        chunks = _chunk_text(text)
        vecs = self._embed.embed(chunks)
        with self._lock:
            self._conn.execute(
                "INSERT INTO documents(doc_id, filename, char_count, pinned, created_at) VALUES(?,?,?,?,?)",
                (doc_id, filename, len(text), 0, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            for i, (chunk, vec) in enumerate(zip(chunks, vecs)):
                self._conn.execute(
                    "INSERT INTO chunks(doc_id, chunk_idx, text, embedding) VALUES(?,?,?,?)",
                    (doc_id, i, chunk, vec.astype(np.float32).tobytes()),
                )
            self._conn.commit()
            self._reload()
        return DocInfo(doc_id=doc_id, filename=filename, char_count=len(text),
                       chunk_count=len(chunks), pinned=False,
                       created_at=time.strftime("%Y-%m-%d %H:%M:%S"))

    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
            self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            self._conn.commit()
            self._reload()
            return cur.rowcount > 0

    def set_pinned(self, doc_id: str, pinned: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE documents SET pinned=? WHERE doc_id=?", (1 if pinned else 0, doc_id)
            )
            self._conn.commit()
            self._reload()
            return cur.rowcount > 0

    def query(self, query: str, top_k: int = 5) -> list[Snippet]:
        query = (query or "").strip()
        if not query or not self._texts:
            return []
        with self._lock:
            qv = self._embed.embed_one(query)
            cos = self._mat @ qv if self._mat is not None else np.zeros(len(self._texts))
            cos = np.clip(cos, 0.0, 1.0)
            qtoks = _tokenize(query)
            kw_scores = np.zeros(len(self._texts))
            if qtoks:
                qw = {t: self._idf.get(t, 1.0) for t in qtoks}
                total_w = sum(qw.values()) or 1.0
                for i, toks in enumerate(self._toks):
                    hit_w = sum(w for t, w in qw.items() if t in toks)
                    kw_scores[i] = hit_w / total_w
            scores = VECTOR_WEIGHT * cos + KEYWORD_WEIGHT * kw_scores
            order = np.argsort(-scores)[:top_k]
            return [
                Snippet(text=self._texts[i], score=float(scores[i]),
                        source=self._sources[i], pinned=self._pinned_flags[i])
                for i in order if scores[i] > 0.05
            ]

    def list_documents(self) -> list[DocInfo]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT d.doc_id, d.filename, d.char_count, d.pinned, d.created_at, COUNT(c.chunk_id) "
                "FROM documents d LEFT JOIN chunks c ON c.doc_id=d.doc_id "
                "GROUP BY d.doc_id ORDER BY d.created_at DESC"
            ).fetchall()
        return [DocInfo(doc_id=r[0], filename=r[1], char_count=r[2],
                        pinned=bool(r[3]), created_at=r[4], chunk_count=r[5]) for r in rows]

    def get_stats(self) -> dict:
        docs = self.list_documents()
        return {
            "backend": "local",
            "embedding": self._embed.backend,
            "documents": len(docs),
            "chunks": len(self._texts),
            "pinned_documents": sum(1 for d in docs if d.pinned),
            "db_path": self.db_path,
        }

    # ---------- injection (ported from newcallcall kb_injection) ----------

    def build_injection(self, budget_tokens: int = FULL_BUDGET_TOKENS) -> dict:
        """Decide what knowledge goes straight into session instructions.

        Returns {tier, context_text, allow_search_tool}.
        - FULL: everything fits -> inject all, no tool needed.
        - RETRIEVAL: inject pinned docs fully + a doc index; the model must
          call search_knowledge for anything else.
        Pinned content is ALWAYS fully injected (price/fact anti-hallucination).
        """
        with self._lock:
            docs = {d.doc_id: d for d in self.list_documents()}
            pinned_text = "\n\n".join(
                t for t, p, did in zip(self._texts, self._pinned_flags, self._doc_ids)
                if p
            )
            # dedupe pinned by doc order (chunks of same doc stay together naturally)
        pinned_tokens = _estimate_tokens(pinned_text)
        if pinned_tokens > budget_tokens:
            print(f"[KB] warning: pinned docs exceed budget ({pinned_tokens} > {budget_tokens}), "
                  "truncating pinned injection", flush=True)
            pinned_text = pinned_text[:budget_tokens]

        total_text = "\n\n".join(self._texts)
        total_tokens = _estimate_tokens(total_text)
        if total_tokens <= budget_tokens:
            return {
                "tier": "FULL",
                "context_text": total_text,
                "allow_search_tool": False,
            }
        # RETRIEVAL tier: pinned + index of available documents
        index_lines = ["可用知识库文档(需要细节时调用 search_knowledge 工具查询):"]
        for d in docs.values():
            pin_mark = " [置顶]" if d.pinned else ""
            index_lines.append(f"- {d.filename}{pin_mark}")
        context = ""
        if pinned_text:
            context += "【置顶必读资料】\n" + pinned_text + "\n\n"
        context += "\n".join(index_lines)
        return {
            "tier": "RETRIEVAL",
            "context_text": context,
            "allow_search_tool": True,
        }
