"""Shared mutable state must survive the threadpool.

FastAPI runs synchronous route handlers in a worker threadpool, so a single
SemanticCache and CircuitBreaker instance are touched by several threads at
once. Both raised or silently corrupted before they were locked; these tests
fail against the unlocked versions.
"""

from __future__ import annotations

import random
import threading

from app.cache import SemanticCache
from app.resilience import CircuitBreaker, CircuitBreakerOpen


def test_cache_survives_concurrent_readers_and_writers() -> None:
    """Unlocked, this raised "OrderedDict mutated during iteration"."""
    cache = SemanticCache(enabled=True, similarity_threshold=0.99, ttl_seconds=1, max_entries=50)
    errors: list[str] = []

    def worker() -> None:
        for index in range(300):
            try:
                vector = [random.random() for _ in range(8)]
                cache.set(vector, f"answer-{index}")
                cache.get(vector)
            except Exception as error:  # noqa: BLE001 - the point is to catch anything
                errors.append(type(error).__name__)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(cache.entries) <= cache.max_entries


def test_cache_respects_its_bound_under_concurrency() -> None:
    cache = SemanticCache(enabled=True, ttl_seconds=3600, max_entries=25)

    def worker() -> None:
        for index in range(200):
            cache.set([float(index), 1.0], f"a{index}")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(cache.entries) == 25


def test_breaker_counts_every_concurrent_failure() -> None:
    """Unlocked, `failure_count += 1` lost increments to a read-modify-write race."""
    breaker: CircuitBreaker[object] = CircuitBreaker(failure_threshold=10_000, cooldown_seconds=60.0)

    def worker() -> None:
        for _ in range(250):
            try:
                breaker.call(_boom)
            except RuntimeError:
                pass

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert breaker.failure_count == 1000


def test_half_open_admits_only_one_probe() -> None:
    """Every waiting caller must not be released into a recovering provider at once."""
    clock = {"t": 1000.0}
    breaker: CircuitBreaker[object] = CircuitBreaker(
        failure_threshold=1, cooldown_seconds=10.0, clock=lambda: clock["t"]
    )
    try:
        breaker.call(_boom)
    except RuntimeError:
        pass
    assert breaker.state == "open"

    clock["t"] = 1100.0  # cooldown elapsed
    admitted, rejected = [], []

    def worker() -> None:
        try:
            breaker._before_call()
            admitted.append(1)
        except CircuitBreakerOpen:
            rejected.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(admitted) == 1, "exactly one probe may enter half-open"
    assert len(rejected) == 7


def _boom() -> None:
    raise RuntimeError("provider down")
