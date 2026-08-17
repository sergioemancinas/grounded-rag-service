from __future__ import annotations

import time
from contextlib import ContextDecorator
from typing import Callable, Generic, TypeVar


T = TypeVar("T")
Clock = Callable[[], float]


class CircuitBreakerOpen(RuntimeError):
    pass


class CircuitBreaker(ContextDecorator, Generic[T]):
    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        clock: Clock | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock or time.time
        self.state = "closed"
        self.failure_count = 0
        self.opened_at: float | None = None

    def __enter__(self) -> CircuitBreaker[T]:
        self._before_call()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        del traceback
        if exc_type is None:
            self._record_success()
        else:
            self._record_failure()
        return False

    def call(self, func: Callable[..., T], *args: object, **kwargs: object) -> T:
        self._before_call()
        try:
            result = func(*args, **kwargs)
        except Exception:
            self._record_failure()
            raise
        self._record_success()
        return result

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        def wrapped(*args: object, **kwargs: object) -> T:
            return self.call(func, *args, **kwargs)

        return wrapped

    def _before_call(self) -> None:
        if self.state != "open":
            return
        if self.opened_at is not None and self.clock() - self.opened_at >= self.cooldown_seconds:
            self.state = "half_open"
            return
        raise CircuitBreakerOpen("Circuit breaker is open.")

    def _record_success(self) -> None:
        self.state = "closed"
        self.failure_count = 0
        self.opened_at = None

    def _record_failure(self) -> None:
        if self.state == "half_open":
            self.state = "open"
            self.opened_at = self.clock()
            self.failure_count = self.failure_threshold
            return
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            self.opened_at = self.clock()
