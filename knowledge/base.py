"""Adapter interface for pluggable knowledge backends."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Snippet:
    text: str
    score: float
    source: str  # filename or document title
    pinned: bool = False


@dataclass
class DocInfo:
    doc_id: str
    filename: str
    char_count: int
    chunk_count: int
    pinned: bool = False
    created_at: str = ""


class KnowledgeAdapter(ABC):
    """A knowledge backend must implement these four operations."""

    @abstractmethod
    def ingest_file(self, path: str) -> DocInfo:
        """Parse and index a file. Returns document info."""

    @abstractmethod
    def delete_document(self, doc_id: str) -> bool:
        """Remove a document and all its chunks."""

    @abstractmethod
    def query(self, query: str, top_k: int = 5) -> list[Snippet]:
        """Retrieve the most relevant snippets for a question."""

    @abstractmethod
    def list_documents(self) -> list[DocInfo]:
        """List all indexed documents."""

    @abstractmethod
    def get_stats(self) -> dict:
        """Return backend stats (doc count, chunk count, backend name...)."""
