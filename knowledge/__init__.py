"""wechatphone knowledge package.

Adapter-based local knowledge base. Backend is selected via
KNOWLEDGE_BACKEND env var:
  - local   (default): SQLite + numpy hybrid retrieval, embedded, zero extra services
  - (future) ragflow / chroma ... implement KnowledgeAdapter to plug in
"""
from __future__ import annotations

import os


def create_knowledge():
    """Factory: return the configured KnowledgeAdapter instance."""
    backend = os.getenv("KNOWLEDGE_BACKEND", "local").strip().lower()
    if backend == "local":
        from knowledge.local_adapter import LocalKnowledgeAdapter
        return LocalKnowledgeAdapter()
    raise ValueError(
        f"unknown KNOWLEDGE_BACKEND={backend!r} (supported: local)"
    )
