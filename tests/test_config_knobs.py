"""Documented configuration knobs must actually do what the docs say.

Both cases here were decorative before: MAX_QUESTION_CHARS was declared in
Settings but the limit was hardcoded in the request model, and
RERANK_ENABLED=true booted cleanly and then failed every request.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import build_deps
from app.main import create_app


def test_max_question_chars_is_enforced_from_settings() -> None:
    app = create_app(Settings(max_question_chars=50))

    with TestClient(app) as client:
        assert client.post("/v1/ask", json={"question": "x" * 60}).status_code == 422
        assert client.post("/v1/search", json={"query": "x" * 60}).status_code == 422
        assert client.post("/v1/ask", json={"question": "x" * 10}).status_code == 200


def test_raising_the_limit_admits_longer_questions() -> None:
    """A hardcoded ceiling would reject this regardless of the setting."""
    app = create_app(Settings(max_question_chars=6000))

    with TestClient(app) as client:
        assert client.post("/v1/ask", json={"question": "x" * 5000}).status_code == 200


def test_rerank_enabled_without_an_implementation_fails_at_startup() -> None:
    with pytest.raises(RuntimeError, match="RERANKER_CLASS"):
        build_deps(Settings(rerank_enabled=True))


def test_rerank_enabled_with_a_reranker_class_wires_it() -> None:
    deps = build_deps(
        Settings(
            rerank_enabled=True,
            reranker_class="examples.custom_reranker_crossencoder:CrossEncoderReranker",
        )
    )

    assert type(deps.reranker).__name__ == "CrossEncoderReranker"
