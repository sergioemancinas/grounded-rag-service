"""Semantic answer cache.

A near-identical question should not pay for retrieval and generation twice.
"Near-identical" is cosine similarity above a deliberately high threshold:
a false hit returns a confidently wrong answer, which costs far more than a
miss costs latency.

Two properties matter more than the caching itself and are easy to get
wrong:

**Scope.** Entries are partitioned by a caller-supplied scope key. The core
passes the identity of whoever is asking, so a cached answer can only ever
be served back to the same audience that was allowed to produce it. Without
this, the cache silently becomes a cross-user disclosure channel the moment
authentication or per-document permissions exist: B asks a similar question
and receives an answer assembled from documents B may not read. Nothing in
the retrieval path can defend against that, because the retrieval path is
never reached on a cache hit.

**Bounded growth.** Entries are capped and evicted oldest-first, so a busy
deployment cannot exhaust memory through the cache.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from pathlib import Path

from app.retrieval import cosine_similarity

Clock = Callable[[], float]

GLOBAL_SCOPE = "*"
"""Scope for deployments with a single shared corpus and no per-user access."""

DEFAULT_MAX_ENTRIES = 1000


@dataclass
class CacheEntry:
    """One cached answer, with the query vector that produced it."""

    query_embedding: list[float]
    answer: str
    created_at: float
    scope: str = GLOBAL_SCOPE


class SemanticCache:
    """Cosine-similarity cache of answers, partitioned by scope."""

    def __init__(
        self,
        enabled: bool = True,
        similarity_threshold: float = 0.97,
        ttl_seconds: int = 7200,
        path: Path | None = None,
        clock: Clock | None = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.enabled = enabled
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self.path = path
        self.clock = clock or time.time
        self.max_entries = max_entries
        # Insertion-ordered so eviction is oldest-first, and keyed by a
        # monotonic counter: deriving keys from len() collides with a live
        # key after any eviction and silently destroys that entry.
        self.entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._sequence = count(1)
        # Sync handlers run in a threadpool, so one cache instance is shared
        # across workers. Iterating entries while another worker inserts or
        # evicts raises "OrderedDict mutated during iteration"; the counter is
        # not atomic either, so both reads and writes are guarded.
        self._lock = threading.Lock()
        if self.path is not None:
            self._load()

    def get(self, query_embedding: list[float], scope: str = GLOBAL_SCOPE) -> str | None:
        """Return a cached answer for this scope, or None.

        Only entries stored under the same ``scope`` are considered, so a
        hit can never cross an authorization boundary.
        """
        if not self.enabled:
            return None
        self._evict_expired()
        best_answer: str | None = None
        best_score = -1.0
        with self._lock:
            candidates = [e for e in self.entries.values() if e.scope == scope]
        for entry in candidates:
            score = cosine_similarity(query_embedding, entry.query_embedding)
            if score >= self.similarity_threshold and score > best_score:
                best_score = score
                best_answer = entry.answer
        return best_answer

    def set(self, query_embedding: list[float], answer: str, scope: str = GLOBAL_SCOPE) -> None:
        """Cache an answer under ``scope``, evicting the oldest entry if full."""
        if not self.enabled:
            return
        with self._lock:
            key = f"e{next(self._sequence)}"
            self.entries[key] = CacheEntry(
                query_embedding=list(query_embedding),
                answer=answer,
                created_at=self.clock(),
                scope=scope,
            )
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)
        self._persist()

    def _evict_expired(self) -> None:
        now = self.clock()
        with self._lock:
            expired = [key for key, entry in self.entries.items() if now - entry.created_at > self.ttl_seconds]
            for key in expired:
                self.entries.pop(key, None)
        if expired:
            self._persist()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            raw_entries = json.load(handle)
        self.entries = OrderedDict()
        for item in raw_entries:
            key = f"e{next(self._sequence)}"
            self.entries[key] = CacheEntry(
                query_embedding=[float(value) for value in item["query_embedding"]],
                answer=str(item["answer"]),
                created_at=float(item["created_at"]),
                scope=str(item.get("scope", GLOBAL_SCOPE)),
            )

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            snapshot = list(self.entries.values())
        payload = [
            {
                "query_embedding": entry.query_embedding,
                "answer": entry.answer,
                "created_at": entry.created_at,
                "scope": entry.scope,
            }
            for entry in snapshot
        ]
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
