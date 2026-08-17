from __future__ import annotations

import json
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import parse_qs

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.cache import SemanticCache
from app.config import Settings
from app.feedback import FeedbackStore
from app.pipeline import PipelineDeps, answer_question
from app.providers import get_embedder, get_generator
from app.rerank import get_reranker
from app.resilience import CircuitBreaker
from app.retrieval import Retriever
from app.slack_io import render_answer_blocks, verify_slack_signature


class SeenEventSet:
    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, float] = OrderedDict()

    def add(self, event_id: str) -> bool:
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


def build_deps(settings: Settings) -> PipelineDeps:
    retriever = Retriever(settings.index_path, settings)
    return PipelineDeps(
        embedder=get_embedder(settings),
        generator=get_generator(settings),
        retriever=retriever,
        reranker=get_reranker(settings),
        cache=SemanticCache(
            enabled=settings.cache_enabled,
            similarity_threshold=settings.cache_similarity,
            ttl_seconds=settings.cache_ttl_seconds,
        ),
        breaker=CircuitBreaker(),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    app_settings.validate_credentials()
    deps = build_deps(app_settings)
    feedback = FeedbackStore(app_settings.feedback_db_path)
    seen_events = SeenEventSet()

    application = FastAPI(title="slack-rag-skeleton", version=__version__)
    application.state.settings = app_settings
    application.state.deps = deps
    application.state.feedback = feedback

    @application.get("/health")
    def health() -> dict[str, object]:
        retriever = application.state.deps.retriever
        return {"ok": True, "version": __version__, "chunks": retriever.chunk_count}

    @application.post("/slack/events")
    async def slack_events(
        request: Request,
        background_tasks: BackgroundTasks,
        x_slack_signature: str = Header(default=""),
        x_slack_request_timestamp: str = Header(default=""),
        x_slack_retry_num: str | None = Header(default=None),
    ) -> JSONResponse:
        body = await request.body()
        if not verify_slack_signature(
            app_settings.slack_signing_secret,
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
        channel_id = str(event.get("channel", ""))
        if app_settings.allowed_channels and channel_id not in app_settings.allowed_channels:
            return JSONResponse({"ok": True, "ignored": "channel_not_allowed"})
        background_tasks.add_task(process_slack_event, app_settings, deps, event)
        return JSONResponse({"ok": True})

    @application.post("/slack/interactions")
    async def slack_interactions(
        request: Request,
        x_slack_signature: str = Header(default=""),
        x_slack_request_timestamp: str = Header(default=""),
    ) -> JSONResponse:
        body = await request.body()
        if not verify_slack_signature(
            app_settings.slack_signing_secret,
            x_slack_request_timestamp,
            body,
            x_slack_signature,
        ):
            raise HTTPException(status_code=401, detail="invalid Slack signature")
        parsed = parse_qs(body.decode("utf-8"))
        payload_raw = parsed.get("payload", ["{}"])[0]
        payload = json.loads(payload_raw)
        actions = payload.get("actions", [])
        verdict = str(actions[0].get("value", "unknown")) if actions else "unknown"
        user_id = str(payload.get("user", {}).get("id", "unknown"))
        message = payload.get("message", {})
        answer_ts = str(message.get("ts", ""))
        question_hint = str(message.get("text", ""))
        feedback.record(user_id=user_id, question=question_hint, answer_ts=answer_ts, verdict=verdict)
        return JSONResponse({"ok": True})

    return application


async def process_slack_event(settings: Settings, deps: PipelineDeps, event: dict[str, Any]) -> None:
    channel_id = str(event.get("channel", ""))
    thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
    text = str(event.get("text", "")).strip()
    if not text:
        return
    placeholder = await slack_post_message(settings, deps.breaker, channel_id, "Searching the Acme Storefront docs...", thread_ts)
    result = answer_question(text, history=[], settings=settings, deps=deps)
    blocks = render_answer_blocks(result.answer, result.chunks)
    message_ts = str(placeholder.get("ts") or thread_ts)
    await slack_update_message(settings, deps.breaker, channel_id, message_ts, result.answer, blocks)


async def slack_post_message(
    settings: Settings,
    breaker: CircuitBreaker[object],
    channel_id: str,
    text: str,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"channel": channel_id, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return await _slack_api_call(settings, breaker, "chat.postMessage", payload)


async def slack_update_message(
    settings: Settings,
    breaker: CircuitBreaker[object],
    channel_id: str,
    ts: str,
    text: str,
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {"channel": channel_id, "ts": ts, "text": text, "blocks": blocks}
    return await _slack_api_call(settings, breaker, "chat.update", payload)


async def _slack_api_call(
    settings: Settings,
    breaker: CircuitBreaker[object],
    method: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
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


app = create_app()
