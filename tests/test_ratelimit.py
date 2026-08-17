"""Tests for the per-caller token-bucket rate limit (LLM10)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers import LocalHashEmbedder
from app.ratelimit import TokenBucketLimiter
from app.retrieval import Chunk
from tests.conftest import write_index


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    embedder = LocalHashEmbedder()
    text = "Returns are accepted within 30 days of delivery."
    chunk = Chunk(
        "returns:1",
        "returns",
        "Returns",
        ["Eligibility"],
        "https://docs.acme-storefront.example/returns",
        text,
        [],
        embedder.embed([text])[0],
    )
    index_path = tmp_path / "index.jsonl"
    write_index(index_path, [chunk])
    values: dict[str, object] = {
        "index_path": index_path,
        "feedback_db_path": tmp_path / "feedback.sqlite3",
        "cache_enabled": False,
        "rate_limit_enabled": True,
        "rate_limit_requests": 3,
        "rate_limit_window_seconds": 60,
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def test_bucket_refills_over_time() -> None:
    now = 100.0

    def clock() -> float:
        return now

    limiter = TokenBucketLimiter(requests=2, window_seconds=10, clock=clock)
    assert limiter.allow("a")[0] is True
    assert limiter.allow("a")[0] is True
    assert limiter.allow("a")[0] is False

    # Half the window restores one token at capacity/window per second.
    now = 105.0
    allowed, _ = limiter.allow("a")
    assert allowed is True
    assert limiter.allow("a")[0] is False


def test_limit_triggers_429_with_retry_after(client: TestClient) -> None:
    for _ in range(3):
        response = client.post("/v1/search", json={"query": "returns"})
        assert response.status_code == 200

    blocked = client.post("/v1/search", json={"query": "returns"})
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After") is not None
    assert int(blocked.headers["Retry-After"]) >= 1


def test_distinct_callers_get_distinct_buckets(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, rate_limit_requests=1))
    limiter: TokenBucketLimiter = app.state.rate_limiter

    assert limiter.allow("host:10.0.0.1")[0] is True
    assert limiter.allow("host:10.0.0.1")[0] is False
    assert limiter.allow("host:10.0.0.2")[0] is True


def test_key_eviction_is_bounded() -> None:
    limiter = TokenBucketLimiter(requests=1, window_seconds=60, max_keys=3)
    for index in range(5):
        assert limiter.allow(f"host:{index}")[0] is True
    assert limiter.tracked_keys == 3


def test_oversized_question_rejected_with_422(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, rate_limit_enabled=False))
    with TestClient(app) as client:
        response = client.post("/v1/ask", json={"question": "x" * 4001})
    assert response.status_code == 422
