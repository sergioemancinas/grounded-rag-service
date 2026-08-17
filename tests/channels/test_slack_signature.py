from __future__ import annotations

import hashlib
import hmac
import time

from app.channels.slack import verify_slack_signature


def sign(secret: str, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode("utf-8") + b":" + body
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


def test_valid_signature_accepted() -> None:
    secret = "test-secret"
    timestamp = str(int(time.time()))
    body = b'{"type":"event_callback"}'

    assert verify_slack_signature(secret, timestamp, body, sign(secret, timestamp, body))


def test_tampered_body_rejected() -> None:
    secret = "test-secret"
    timestamp = str(int(time.time()))
    body = b'{"type":"event_callback"}'

    assert not verify_slack_signature(secret, timestamp, b'{"type":"changed"}', sign(secret, timestamp, body))


def test_stale_timestamp_rejected() -> None:
    secret = "test-secret"
    timestamp = str(int(time.time()) - 301)
    body = b"{}"

    assert not verify_slack_signature(secret, timestamp, body, sign(secret, timestamp, body))


def test_empty_secret_rejected() -> None:
    timestamp = str(int(time.time()))
    body = b"{}"

    assert not verify_slack_signature("", timestamp, body, "v0=anything")
