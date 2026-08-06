"""Embedding client: OpenAI-compatible /embeddings API with hash-vector fallback.

Ported from newcallcall's approach:
- Batch size 10 per request (DashScope compatible-mode limit).
- When API is not configured or fails, fall back to deterministic
  256-dim hash vectors so the system keeps working offline.
"""
from __future__ import annotations

import hashlib
import os

import numpy as np

BATCH_SIZE = 10
HASH_DIM = 256


def _hash_vector(text: str) -> np.ndarray:
    """Deterministic pseudo-embedding: each token hash activates dimensions.

    Not semantic, but lets keyword-ish retrieval keep functioning and
    guarantees the pipeline never hard-fails.
    """
    vec = np.zeros(HASH_DIM, dtype=np.float32)
    tokens = _tokenize(text)
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "big") % HASH_DIM
        weight = (int.from_bytes(h[4:8], "big") % 1000) / 1000.0 + 0.5
        vec[idx] += weight
    n = np.linalg.norm(vec)
    if n > 0:
        vec /= n
    return vec


def _tokenize(text: str) -> list[str]:
    """Jieba if available, else whitespace + char bigrams."""
    try:
        import jieba
        return [t for t in jieba.cut(text) if t.strip()]
    except ImportError:
        words = [w for w in text.split() if w]
        # add char bigrams for CJK
        out = list(words)
        cjk = "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
        for i in range(len(cjk) - 1):
            out.append(cjk[i:i + 2])
        return out


class EmbeddingClient:
    def __init__(self):
        self.api_url = os.getenv("KNOWLEDGE_EMBEDDING_API_URL", "").strip()
        self.api_key = os.getenv("KNOWLEDGE_EMBEDDING_API_KEY", "").strip()
        self.model = os.getenv("KNOWLEDGE_EMBEDDING_MODEL", "text-embedding-v4").strip()
        self._failed = False  # sticky: after a failure stay on hash fallback

    @property
    def backend(self) -> str:
        if self.api_url and self.api_key and not self._failed:
            return f"api:{self.model}"
        return "hash-fallback"

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed a list of texts -> (N, D) float32, L2-normalized."""
        if not texts:
            return np.zeros((0, HASH_DIM), dtype=np.float32)
        if self.api_url and self.api_key and not self._failed:
            try:
                return self._embed_api(texts)
            except Exception as e:  # noqa: BLE001
                print(f"[KB] embedding API failed ({e}); falling back to hash vectors", flush=True)
                self._failed = True
        return np.stack([_hash_vector(t) for t in texts]).astype(np.float32)

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]

    def _embed_api(self, texts: list[str]) -> np.ndarray:
        import requests
        url = self.api_url.rstrip("/")
        if not url.endswith("/embeddings"):
            url += "/embeddings"
        vecs: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "input": batch, "encoding_format": "float"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            data.sort(key=lambda d: d["index"])
            vecs.extend([d["embedding"] for d in data])
        arr = np.asarray(vecs, dtype=np.float32)
        n = np.linalg.norm(arr, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return arr / n
