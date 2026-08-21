"""Channel-agnostic core service.

Exposes the grounded pipeline as versioned JSON over HTTP:

- ``GET  /health``       liveness plus the resolved provider names
- ``POST /v1/ask``       full grounded pipeline, cited markdown answer
- ``POST /v1/search``    retrieval only, raw corpus chunks
- ``POST /v1/feedback``  verdict recording for any channel

Dependencies are built once in the lifespan context and stored on
``app.state``; routes read them through the thin accessors in app/deps.py.
Optional static bearer auth on ``/v1/*`` is enabled by setting
``API_AUTH_TOKEN``. Platform signature verification (Slack HMAC and
friends) belongs exclusively to channel adapters, never to the core.
"""

from __future__ import annotations

import hmac
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response

from app import __version__
from app.api_models import (
    AskFn,
    AskRequest,
    AskResponse,
    FeedbackRequest,
    GroundingInfo,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SourceRef,
)
from app.cache import GLOBAL_SCOPE
from app.channels.base import FeedbackFn
from app.config import Settings
from app.deps import build_deps, get_deps, get_settings, provider_names
from app.feedback import FeedbackStore
from app.pipeline import PipelineDeps, answer_question
from app.ratelimit import TokenBucketLimiter

logger = logging.getLogger("grounded_rag")

McpRunner = Callable[[], AbstractAsyncContextManager[None]]
"""Factory for the MCP session-manager context, entered by the lifespan."""


def mount_channels(app: FastAPI, ask: AskFn, settings: Settings, feedback: FeedbackFn) -> None:
    """Mount in-process channel adapters (app/channels/*).

    Each adapter is optional and mounts only when its credentials are
    configured, so the core runs standalone by default. Adapters consume the
    core solely through the injected ``ask`` and ``feedback`` callables; add
    your own with one ``include_router`` line here.
    """
    if settings.slack_signing_secret:
        from app.channels import slack

        app.include_router(slack.create_router(ask, settings, feedback))
        logger.info("slack adapter: mounted at /slack/events")
    else:
        logger.info("slack adapter: not mounted, SLACK_SIGNING_SECRET unset")


def mount_mcp(app: FastAPI, settings: Settings, deps_provider: Callable[[], PipelineDeps]) -> McpRunner | None:
    """Mount the MCP transport at /mcp, returning its session-manager runner.

    Returns None when the optional ``mcp`` package is not installed, which is
    the default for the offline stack. The returned runner must be entered by
    the host lifespan or the first MCP request fails.
    """
    from app.mcp_server import mount as mount_mcp_transport

    return mount_mcp_transport(app, settings, deps_provider)


def cache_scope_for(payload: AskRequest) -> str:
    """Partition key for the semantic cache.

    A cache hit skips retrieval, so this is the boundary that decides who can
    be served someone else's answer. With one shared corpus and no per-user
    permissions, every caller sees the same documents and a global scope is
    correct. The moment document-level permissions exist, this must derive
    from the *authenticated* principal rather than from the request body,
    which any caller can set.
    """
    return payload.user_id or GLOBAL_SCOPE


def run_ask(payload: AskRequest, settings: Settings, deps: PipelineDeps) -> AskResponse:
    """Execute the grounded pipeline for one AskRequest."""
    result = answer_question(
        payload.question,
        history=payload.history,
        settings=settings,
        deps=deps,
        cache_scope=cache_scope_for(payload),
    )
    sources = [
        SourceRef(
            id=scored.chunk.id,
            title=scored.chunk.title,
            url=scored.chunk.url,
            heading_path=scored.chunk.heading_path,
            score=scored.score,
        )
        for scored in result.chunks
    ]
    grounding = None
    if result.grounding is not None:
        grounding = GroundingInfo(
            score=result.grounding.score,
            passed=result.grounding.score >= settings.grounding_min_score,
        )
    return AskResponse(
        answer=result.answer,
        citations=result.citations,
        sources=sources,
        grounding=grounding,
        intent=result.route.intent,
        cached=result.cached,
        followups=result.followups,
        timings=result.timings,
        request_id=str(uuid.uuid4()),
    )


def require_api_auth(request: Request) -> None:
    """Optional static bearer check on /v1/*; disabled when API_AUTH_TOKEN is empty."""
    settings: Settings = request.app.state.settings
    if not settings.api_auth_token:
        return
    provided = request.headers.get("authorization", "")
    expected = f"Bearer {settings.api_auth_token}"
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def rate_limit_key(request: Request, settings: Settings) -> str:
    """Caller identity for the rate-limit bucket.

    When a static bearer is configured and the request carries it, the key is
    that principal: a shared token is not per-user auth, so every valid bearer
    shares one budget. Otherwise the key is the client host, which is the only
    stable identity an open deployment has.
    """
    if settings.api_auth_token:
        provided = request.headers.get("authorization", "")
        expected = f"Bearer {settings.api_auth_token}"
        if hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
            return f"token:{settings.api_auth_token}"
    host = request.client.host if request.client is not None else "unknown"
    return f"host:{host}"


def enforce_rate_limit(request: Request) -> None:
    """Reject /v1 callers that have exhausted their token bucket (HTTP 429)."""
    settings: Settings = request.app.state.settings
    if not settings.rate_limit_enabled:
        return
    limiter: TokenBucketLimiter = request.app.state.rate_limiter
    allowed, retry_after = limiter.allow(rate_limit_key(request, settings))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


api = APIRouter(prefix="/v1", dependencies=[Depends(require_api_auth), Depends(enforce_rate_limit)])


@api.post("/ask", response_model=AskResponse)
def ask_route(
    payload: AskRequest,
    settings: Settings = Depends(get_settings),
    deps: PipelineDeps = Depends(get_deps),
) -> AskResponse:
    """Run the full grounded pipeline and return a cited markdown answer."""
    return run_ask(payload, settings, deps)


@api.post("/search", response_model=SearchResponse)
def search_route(
    payload: SearchRequest,
    deps: PipelineDeps = Depends(get_deps),
) -> SearchResponse:
    """Hybrid retrieval over the corpus index; no generation, raw chunks."""
    embedding = deps.embedder.embed([payload.query])[0]
    results = deps.retriever.retrieve(payload.query, embedding, payload.top_k)
    return SearchResponse(
        results=[
            SearchResult(
                id=scored.chunk.id,
                text=scored.chunk.text,
                title=scored.chunk.title,
                url=scored.chunk.url,
                heading_path=scored.chunk.heading_path,
                score=scored.score,
            )
            for scored in results[: payload.top_k]
        ]
    )


@api.post("/feedback", status_code=204)
def feedback_route(payload: FeedbackRequest, request: Request) -> Response:
    """Record an up/down verdict against a previously returned request_id."""
    record_feedback(request.app, payload)
    return Response(status_code=204)


def record_feedback(application: FastAPI, payload: FeedbackRequest) -> None:
    """Persist one verdict; shared by the HTTP route and in-process adapters."""
    store: FeedbackStore = application.state.feedback
    store.record(
        user_id=payload.user_id or "api",
        question=payload.comment or "",
        answer_ts=payload.request_id,
        verdict=payload.verdict,
    )


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Build dependencies at startup (the index loads here, not at import)."""
    settings: Settings = application.state.settings
    application.state.deps = build_deps(settings)
    application.state.feedback = FeedbackStore(settings.feedback_db_path, settings.feedback_hmac_key)
    runner: McpRunner | None = getattr(application.state, "mcp_runner", None)
    if runner is None:
        yield
        return
    # The MCP transport requires its session manager to be running for the
    # lifetime of the host application; without this the first request fails.
    async with runner():
        yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application around the given (or env-derived) settings."""
    app_settings = settings or Settings()
    application = FastAPI(title="grounded-rag-service", version=__version__, lifespan=lifespan)
    application.state.settings = app_settings
    application.state.rate_limiter = TokenBucketLimiter(
        requests=app_settings.rate_limit_requests,
        window_seconds=app_settings.rate_limit_window_seconds,
    )
    application.include_router(api)

    # Adapters and the MCP transport mount at build time, but resolve their
    # dependencies lazily from app.state so the index still loads in the
    # lifespan rather than at import.
    async def ask(payload: AskRequest) -> AskResponse:
        return run_ask(payload, application.state.settings, application.state.deps)

    async def feedback(payload: FeedbackRequest) -> None:
        record_feedback(application, payload)

    application.state.ask = ask
    mount_channels(application, ask, app_settings, feedback)
    application.state.mcp_runner = mount_mcp(application, app_settings, lambda: application.state.deps)

    @application.get("/health")
    def health(request: Request) -> dict[str, object]:
        """Liveness endpoint echoing the resolved provider names."""
        deps: PipelineDeps = request.app.state.deps
        return {
            "status": "ok",
            "version": __version__,
            "chunks": getattr(deps.retriever, "chunk_count", None),
            "providers": provider_names(request.app.state.settings),
        }

    return application


def serve() -> None:
    """Console entry point for ``grounded-rag-serve`` (uvicorn app.main:app)."""
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)


app = create_app()
