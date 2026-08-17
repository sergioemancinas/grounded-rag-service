from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.retrieval import cosine_similarity


Clock = Callable[[], float]


@dataclass
class CacheEntry:
    query_embedding: list[float]
    answer: str
    created_at: float


class SemanticCache:
    def __init__(
        self,
        enabled: bool = True,
        similarity_threshold: float = 0.97,
        ttl_seconds: int = 7200,
        path: Path | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.enabled = enabled
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self.path = path
        self.clock = clock or time.time
        self.entries: dict[str, CacheEntry] = {}
        if self.path is not None:
            self._load()

    def get(self, query_embedding: list[float]) -> str | None:
        if not self.enabled:
            return None
        self._evict_expired()
        best_answer: str | None = None
        best_score = -1.0
        for entry in self.entries.values():
            score = cosine_similarity(query_embedding, entry.query_embedding)
            if score >= self.similarity_threshold and score > best_score:
                best_score = score
                best_answer = entry.answer
        return best_answer

    def set(self, query_embedding: list[float], answer: str) -> None:
        if not self.enabled:
            return
        key = str(len(self.entries) + 1)
        self.entries[key] = CacheEntry(query_embedding=list(query_embedding), answer=answer, created_at=self.clock())
        self._persist()

    def _evict_expired(self) -> None:
        now = self.clock()
        expired = [
            key
            for key, entry in self.entries.items()
            if now - entry.created_at > self.ttl_seconds
        ]
        for key in expired:
            del self.entries[key]
        if expired:
            self._persist()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            raw_entries = json.load(handle)
        self.entries = {
            str(index): CacheEntry(
                query_embedding=[float(value) for value in item["query_embedding"]],
                answer=str(item["answer"]),
                created_at=float(item["created_at"]),
            )
            for index, item in enumerate(raw_entries, start=1)
        }

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "query_embedding": entry.query_embedding,
                "answer": entry.answer,
                "created_at": entry.created_at,
            }
            for entry in self.entries.values()
        ]
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
