"""Tests for the MCP tool audit sink (MCP08)."""

from __future__ import annotations

import json
import logging

import pytest

from app.audit import audit_tool_call, summarize_arguments
from app.mcp_auth import CURRENT_CLAIMS


def test_summarize_arguments_never_includes_raw_question() -> None:
    key = b"test-key"
    summary = summarize_arguments({"question": "How do refunds work?", "k": 3}, key)
    dumped = json.dumps(summary)
    assert "How do refunds work?" not in dumped
    assert summary["question"]["length"] == len("How do refunds work?")
    assert "digest" in summary["question"]
    assert summary["k"] == 3


def test_event_emitted_per_call(caplog: pytest.LogCaptureFixture) -> None:
    @audit_tool_call("search", hmac_key="audit-test-key")
    def search(query: str, k: int = 5) -> list[str]:
        return [query[:1]] * k

    with caplog.at_level(logging.INFO, logger="citespine.audit"):
        search("orders", k=2)

    records = [record for record in caplog.records if record.name == "citespine.audit"]
    assert len(records) == 1
    event = json.loads(records[0].message)
    assert event["event"] == "mcp.tool_call"
    assert event["tool"] == "search"
    assert event["outcome"] == "ok"
    assert event["subject"] == "anonymous"
    assert "duration_ms" in event
    assert event["arguments"]["query"]["length"] == len("orders")


def test_subject_recorded(caplog: pytest.LogCaptureFixture) -> None:
    @audit_tool_call("ask", hmac_key="audit-test-key")
    def ask(question: str) -> str:
        return "ok"

    token = CURRENT_CLAIMS.set({"sub": "user-42"})
    try:
        with caplog.at_level(logging.INFO, logger="citespine.audit"):
            ask("secret question text that must not leak")
    finally:
        CURRENT_CLAIMS.reset(token)

    event = json.loads(next(r.message for r in caplog.records if r.name == "citespine.audit"))
    assert event["subject"] == "user-42"
    assert "secret question text that must not leak" not in event["arguments"]["question"].get("digest", "")
    assert "secret question text that must not leak" not in json.dumps(event)


def test_failures_recorded_with_error_class(caplog: pytest.LogCaptureFixture) -> None:
    @audit_tool_call("fetch", hmac_key="audit-test-key")
    def fetch(id: str) -> dict[str, str]:
        raise ValueError(f"No chunk with id {id!r}")

    with caplog.at_level(logging.INFO, logger="citespine.audit"), pytest.raises(ValueError):
        fetch("missing")

    event = json.loads(next(r.message for r in caplog.records if r.name == "citespine.audit"))
    assert event["outcome"] == "error"
    assert event["error_class"] == "ValueError"


def test_decorator_preserves_signature_annotations() -> None:
    @audit_tool_call("search", hmac_key="audit-test-key")
    def search(query: str, k: int = 5) -> list[dict[str, object]]:
        return []

    assert search.__name__ == "search"
    assert set(search.__annotations__) == {"query", "k", "return"}
    # The MCP SDK builds the input schema from these annotations; a silent
    # drop would register a tool with an empty parameter list.
    assert "query" in search.__annotations__
    assert "k" in search.__annotations__


def test_raw_question_never_in_emitted_record(caplog: pytest.LogCaptureFixture) -> None:
    # A distinctive phrase, not a credential: the test asserts this exact
    # string never reaches a log record, so it has to be easy to search for.
    canary = "how many refunds may one customer request in a single year"

    @audit_tool_call("ask", hmac_key="audit-test-key")
    def ask(question: str) -> str:
        return "answer"

    with caplog.at_level(logging.INFO, logger="citespine.audit"):
        ask(canary)

    for record in caplog.records:
        assert canary not in record.getMessage()
        assert canary not in getattr(record, "message", "")
