"""Per-caller token-bucket rate limit for the HTTP core.

Without a ceiling, every ``/v1/ask`` can trigger several provider calls and an
unauthenticated deployment is a denial-of-wallet (OWASP LLM10). This module
is the cheapest meaningful fix: a fixed refill schedule keyed by caller
identity, with a hard cap on tracked keys so the limiter cannot itself become
a memory leak under a flood of distinct hosts.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

Clock = Callable[[], float]

DEFAULT_MAX_KEYS = 10_000


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketLimiter:
    """Thread-safe token bucket, one bucket per caller key.

    FastAPI runs sync route handlers in a threadpool, so every mutation goes
    through ``_lock``. The clock is injectable so tests can advance time
    without sleeping.
    """

    def __init__(
        self,
        requests: int,
        window_seconds: float,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Clock | None = None,
    ) -> None:
        if requests < 1:
            raise ValueError("requests must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.capacity = float(requests)
        self.window_seconds = float(window_seconds)
        self.refill_per_second = self.capacity / self.window_seconds
        self.max_keys = max_keys
        self.clock: Clock = clock or time.time
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def allow(self, key: str) -> tuple[bool, int]:
        """Consume one token for ``key``.

        Returns ``(True, 0)`` when the call may proceed, or
        ``(False, retry_after_seconds)`` when the bucket is empty. The retry
        value is the whole seconds until one token will be available again,
        suitable for an HTTP ``Retry-After`` header.
        """
        with self._lock:
            now = self.clock()
            bucket = self._buckets.get(key)
            if bucket is None:
                self._evict_oldest_if_full()
                bucket = _Bucket(tokens=self.capacity, updated_at=now)
                self._buckets[key] = bucket
            else:
                self._buckets.move_to_end(key)
                elapsed = max(0.0, now - bucket.updated_at)
                bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_second)
                bucket.updated_at = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0

            deficit = 1.0 - bucket.tokens
            retry_after = max(1, int(math.ceil(deficit / self.refill_per_second)))
            return False, retry_after

    @property
    def tracked_keys(self) -> int:
        """Number of caller keys currently held (for tests and diagnostics)."""
        with self._lock:
            return len(self._buckets)

    def _evict_oldest_if_full(self) -> None:
        """Drop the least-recently-used key when the map would exceed ``max_keys``."""
        while len(self._buckets) >= self.max_keys:
            self._buckets.popitem(last=False)
