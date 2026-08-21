"""Slack adapter: the worked example of the two-sided adapter seam.

This module is the reference implementation of the rules documented in
app/channels/base.py, and it is entirely optional. The core mounts it only
when ``SLACK_SIGNING_SECRET`` is set, and deleting this file (plus
slack_render.py and requirements-slack.txt) removes Slack support without
touching a single core module.

Flow: verify the HMAC over the raw body, echo Slack's URL-verification
challenge, drop duplicate retries, filter to the allowed channels,
acknowledge within Slack's 3-second window, then answer in the background by
posting a placeholder and editing it with the rendered Block Kit answer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api_models import FeedbackRequest
from app.channels.base import AskFn, FeedbackFn, InboundMessage
from app.channels.slack_render import render_answer_blocks
from app.config import Settings
from app.resilience import CircuitBreaker

logger = logging.getLogger("grounded_rag.channels.slack")

PLACEHOLDER_TEXT = "Searching the docs..."
"""Posted immediately, then edited with the answer. Change freely."""

SIGNATURE_MAX_AGE_SECONDS = 300
"""Slack's replay window: older signed requests are rejected."""


def verify_slack_signature(signing_secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Validate Slack's ``v0`` HMAC-SHA256 signature over the raw request body.

    Fails closed on every error path, including an unset signing secret: an
    empty secret means the deployment is misconfigured, and accepting
    unsigned requests would be strictly worse than answering nothing.
    """
    if not signing_secret:
        return False
    try:
        timestamp_int = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - timestamp_int) > SIGNATURE_MAX_AGE_SECONDS:
        return False
    base = b"v0:" + timestamp.encode("utf-8") + b":" + body
    digest = hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)


class SeenEventSet:
    """Bounded TTL set of Slack event ids, used to drop duplicate retries."""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, float] = OrderedDict()

    def add(self, event_id: str) -> bool:
        """Record an event id; returns False when it was already present."""
        now = time.time()
        self._evict(now)
        if event_id in self._items:
            return False
        self._items[event_id] = now
        return True

    def _evict(self, now: float) -> None:
        expired = [key for key, seen_at in self._items.items() if now - seen_at > self.ttl_seconds]
        for key in expired:
            self._items.pop(key, None)


def create_router(ask: AskFn, settings: Settings, feedback: FeedbackFn | None = None) -> APIRouter:
    """Build the Slack routes bound to an ``AskFn`` and an optional feedback sink.

    The adapter receives the core only through these two callables; it never
    imports the pipeline, the retriever, or PipelineDeps.
    """
    router = APIRouter(tags=["slack"])
    seen_events = SeenEventSet()
    # Outbound Slack calls get their own breaker so a Slack outage cannot
    # trip the breaker guarding model and embedding providers.
    breaker: CircuitBreaker[object] = CircuitBreaker()

    @router.post("/slack/events")
    async def slack_events(
        request: Request,
        background_tasks: BackgroundTasks,
        x_slack_signature: str = Header(default=""),
        x_slack_request_timestamp: str = Header(default=""),
        x_slack_retry_num: str | None = Header(default=None),
    ) -> JSONResponse:
        """Receive Events API callbacks; ack fast and answer in the background."""
        body = await request.body()
        if not verify_slack_signature(
            settings.slack_signing_secret,
            x_slack_request_timestamp,
            body,
            x_slack_signature,
        ):
            raise HTTPException(status_code=401, detail="invalid Slack signature")
        payload = json.loads(body.decode("utf-8") or "{}")
        if payload.get("type") == "url_verification":
            return JSONResponse({"challenge": payload.get("challenge", "")})

        event_id = str(payload.get("event_id", ""))
        if x_slack_retry_num is not None and event_id and not seen_events.add(event_id):
            return JSONResponse({"ok": True, "deduped": True})
        if event_id:
            seen_events.add(event_id)

        event = payload.get("event", {})
        # Never answer our own messages: without this an assistant posting
        # into a channel it also listens to will answer itself forever.
        if event.get("bot_id") or event.get("subtype"):
            return JSONResponse({"ok": True, "ignored": "bot_or_subtype"})
        channel_id = str(event.get("channel", ""))
        if settings.allowed_channels and channel_id not in settings.allowed_channels:
            return JSONResponse({"ok": True, "ignored": "channel_not_allowed"})
        background_tasks.add_task(process_event, ask, settings, breaker, event)
        return JSONResponse({"ok": True})

    @router.post("/slack/interactions")
    async def slack_interactions(
        request: Request,
        x_slack_signature: str = Header(default=""),
        x_slack_request_timestamp: str = Header(default=""),
    ) -> JSONResponse:
        """Handle feedback button clicks and forward them to the core."""
        body = await request.body()
        if not verify_slack_signature(
            settings.slack_signing_secret,
            x_slack_request_timestamp,
            body,
            x_slack_signature,
        ):
            raise HTTPException(status_code=401, detail="invalid Slack signature")
        parsed = parse_qs(body.decode("utf-8"))
        payload = json.loads(parsed.get("payload", ["{}"])[0])
        actions = payload.get("actions", [])
        verdict = str(actions[0].get("value", "")) if actions else ""
        if verdict not in {"up", "down"} or feedback is None:
            return JSONResponse({"ok": True, "ignored": "no_verdict"})
        message = payload.get("message", {})
        await feedback(
            FeedbackRequest(
                request_id=str(message.get("ts", "")),
                verdict=verdict,  # type: ignore[arg-type]
                user_id=str(payload.get("user", {}).get("id", "")) or None,
            )
        )
        return JSONResponse({"ok": True})

    return router


async def process_event(
    ask: AskFn,
    settings: Settings,
    breaker: CircuitBreaker[object],
    event: dict[str, Any],
) -> None:
    """Answer one Slack message: placeholder, pipeline, then edit in place."""
    message = InboundMessage(
        text=str(event.get("text", "")).strip(),
        conversation_id=f"{event.get('channel', '')}:{event.get('thread_ts') or event.get('ts') or ''}",
        user_id=str(event.get("user", "")),
        channel="slack",
        raw=event,
    )
    if not message.text:
        return
    channel_id = str(event.get("channel", ""))
    thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
    placeholder = await post_message(settings, breaker, channel_id, PLACEHOLDER_TEXT, thread_ts)
    response = await ask(message.to_request())
    blocks = render_answer_blocks(response.answer, response.sources)
    message_ts = str(placeholder.get("ts") or thread_ts)
    await update_message(settings, breaker, channel_id, message_ts, response.answer, blocks)


async def post_message(
    settings: Settings,
    breaker: CircuitBreaker[object],
    channel_id: str,
    text: str,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """Post a message, optionally into a thread (``chat.postMessage``)."""
    payload: dict[str, Any] = {"channel": channel_id, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return await _api_call(settings, breaker, "chat.postMessage", payload)


async def update_message(
    settings: Settings,
    breaker: CircuitBreaker[object],
    channel_id: str,
    ts: str,
    text: str,
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replace a previously posted message with the final answer (``chat.update``)."""
    payload: dict[str, Any] = {"channel": channel_id, "ts": ts, "text": text, "blocks": blocks}
    return await _api_call(settings, breaker, "chat.update", payload)


async def _api_call(
    settings: Settings,
    breaker: CircuitBreaker[object],
    method: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Call one Slack Web API method through the adapter's circuit breaker."""
    with breaker:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"https://slack.com/api/{method}",
                headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok", False):
                raise RuntimeError(f"Slack API error for {method}: {data.get('error', 'unknown')}")
            return dict(data)
