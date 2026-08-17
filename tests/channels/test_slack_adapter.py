from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api_models import AskRequest, AskResponse, SourceRef
from app.channels import slack
from app.config import Settings
from app.main import create_app

SECRET = "test-signing-secret"


def sign(timestamp: str, body: bytes, secret: str = SECRET) -> str:
    base = b"v0:" + timestamp.encode("utf-8") + b":" + body
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


def signed_headers(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": sign(timestamp, body),
        "Content-Type": "application/json",
    }


async def fake_ask(payload: AskRequest) -> AskResponse:
    return AskResponse(
        answer=f"Answer to: {payload.question}",
        citations=[],
        sources=[SourceRef(id="c1", title="Doc", url="https://docs.example/doc", score=1.0)],
        request_id="req-1",
    )


def adapter_app(secret: str = SECRET) -> FastAPI:
    app = FastAPI()
    app.include_router(slack.create_router(fake_ask, Settings(slack_signing_secret=secret)))
    return app


def test_adapter_not_mounted_without_secret() -> None:
    app = create_app(Settings(slack_signing_secret=""))

    assert "/slack/events" not in app.openapi()["paths"]


def test_adapter_mounted_with_secret() -> None:
    app = create_app(Settings(slack_signing_secret=SECRET))

    assert "/slack/events" in app.openapi()["paths"]


def test_unsigned_request_rejected() -> None:
    client = TestClient(adapter_app())

    response = client.post("/slack/events", json={"type": "event_callback"})

    assert response.status_code == 401


def test_url_verification_challenge_echoed() -> None:
    client = TestClient(adapter_app())
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()

    response = client.post("/slack/events", content=body, headers=signed_headers(body))

    assert response.status_code == 200
    assert response.json()["challenge"] == "abc123"


def test_retry_of_seen_event_is_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    async def recorder(ask, settings, breaker, event):
        seen.append(event)

    monkeypatch.setattr(slack, "process_event", recorder)
    client = TestClient(adapter_app())
    body = json.dumps(
        {"type": "event_callback", "event_id": "Ev1", "event": {"channel": "C1", "text": "hi", "ts": "1.0"}}
    ).encode()

    first = client.post("/slack/events", content=body, headers=signed_headers(body))
    retry_headers = signed_headers(body) | {"X-Slack-Retry-Num": "1"}
    second = client.post("/slack/events", content=body, headers=retry_headers)

    assert first.json() == {"ok": True}
    assert second.json()["deduped"] is True
    assert len(seen) == 1


def test_bot_messages_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack, "process_event", lambda *args: pytest.fail("bot message was answered"))
    client = TestClient(adapter_app())
    body = json.dumps({"type": "event_callback", "event": {"channel": "C1", "text": "hi", "bot_id": "B1"}}).encode()

    response = client.post("/slack/events", content=body, headers=signed_headers(body))

    assert response.json()["ignored"] == "bot_or_subtype"


def test_channel_allowlist_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack, "process_event", lambda *args: pytest.fail("disallowed channel was answered"))
    app = FastAPI()
    settings = Settings(slack_signing_secret=SECRET, allowed_channel_ids="C_ALLOWED")
    app.include_router(slack.create_router(fake_ask, settings))
    client = TestClient(app)
    body = json.dumps({"type": "event_callback", "event": {"channel": "C_OTHER", "text": "hi"}}).encode()

    response = client.post("/slack/events", content=body, headers=signed_headers(body))

    assert response.json()["ignored"] == "channel_not_allowed"


def test_process_event_posts_placeholder_then_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    async def fake_post(settings, breaker, channel_id, text, thread_ts=None):
        calls["placeholder"] = text
        return {"ts": "111.222"}

    async def fake_update(settings, breaker, channel_id, ts, text, blocks):
        calls["update_ts"] = ts
        calls["blocks"] = blocks
        calls["text"] = text

    monkeypatch.setattr(slack, "post_message", fake_post)
    monkeypatch.setattr(slack, "update_message", fake_update)
    event = {"channel": "C1", "text": "How do refunds work?", "ts": "1.0", "user": "U1"}

    import asyncio

    asyncio.run(slack.process_event(fake_ask, Settings(), slack.CircuitBreaker(), event))

    assert calls["placeholder"] == slack.PLACEHOLDER_TEXT
    assert calls["update_ts"] == "111.222"
    assert "Answer to: How do refunds work?" in str(calls["text"])
    assert any(block.get("block_id") == "feedback" for block in calls["blocks"])  # type: ignore[union-attr]
