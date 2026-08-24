from __future__ import annotations

from app.cache import SemanticCache


def answer_of(entry) -> str | None:
    """Cache entries carry their evidence; these tests only assert the text."""
    return None if entry is None else entry.answer


def test_cache_hit_above_threshold() -> None:
    cache = SemanticCache(similarity_threshold=0.95, ttl_seconds=100, clock=lambda: 10.0)
    cache.set([1.0, 0.0], "answer")

    assert answer_of(cache.get([0.99, 0.01])) == "answer"


def test_cache_miss_below_threshold() -> None:
    cache = SemanticCache(similarity_threshold=0.95, ttl_seconds=100, clock=lambda: 10.0)
    cache.set([1.0, 0.0], "answer")

    assert cache.get([0.0, 1.0]) is None


def test_cache_ttl_expiry() -> None:
    now = 10.0

    def clock() -> float:
        return now

    cache = SemanticCache(similarity_threshold=0.95, ttl_seconds=5, clock=clock)
    cache.set([1.0, 0.0], "answer")
    now = 20.0

    assert cache.get([1.0, 0.0]) is None
