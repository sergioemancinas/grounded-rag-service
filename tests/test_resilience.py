from __future__ import annotations

import pytest

from app.resilience import CircuitBreaker, CircuitBreakerOpen


def test_breaker_opens_after_failures() -> None:
    now = 0.0

    def clock() -> float:
        return now

    breaker: CircuitBreaker[object] = CircuitBreaker(failure_threshold=2, cooldown_seconds=10, clock=clock)

    def fail() -> object:
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        breaker.call(fail)
    with pytest.raises(RuntimeError):
        breaker.call(fail)
    assert breaker.state == "open"
    with pytest.raises(CircuitBreakerOpen):
        breaker.call(lambda: "blocked")


def test_breaker_half_open_probe_and_recovery() -> None:
    now = 0.0

    def clock() -> float:
        return now

    breaker: CircuitBreaker[object] = CircuitBreaker(failure_threshold=1, cooldown_seconds=10, clock=clock)

    with pytest.raises(RuntimeError):
        breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
    assert breaker.state == "open"
    now = 11.0

    assert breaker.call(lambda: "ok") == "ok"
    assert breaker.state == "closed"


def test_breaker_half_open_failure_reopens() -> None:
    now = 0.0

    def clock() -> float:
        return now

    breaker: CircuitBreaker[object] = CircuitBreaker(failure_threshold=1, cooldown_seconds=10, clock=clock)

    with pytest.raises(RuntimeError):
        breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
    now = 11.0
    with pytest.raises(RuntimeError):
        breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("probe failed")))

    assert breaker.state == "open"
