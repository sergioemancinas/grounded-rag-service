"""Public API request/response models: the adapter compatibility boundary.

Channel adapters (in-process routers and external HTTP clients alike) speak
only these shapes plus the ``AskFn`` callable alias. The core returns
markdown answers with structured citations; rendering to any platform
format happens in the adapter, never here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """One question for the grounded pipeline (POST /v1/ask)."""

    # max_length matches Settings.max_question_chars default: reject before any model call.
    question: str = Field(max_length=4000)
    history: list[str] = Field(default_factory=list, description="Client-held prior turns; keeps the core stateless.")
    conversation_id: str | None = Field(default=None, description="Opaque adapter-defined thread id.")
    user_id: str | None = Field(default=None, description="Hashed before any storage; never persisted raw.")
    channel: str = Field(default="http", description="Name of the calling adapter.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Passed through untouched.")


class SourceRef(BaseModel):
    """One retrieved source chunk backing the answer."""

    id: str
    title: str
    url: str
    heading_path: list[str] = Field(default_factory=list)
    score: float


class GroundingInfo(BaseModel):
    """Outcome of the grounding gate for this answer."""

    score: float
    passed: bool


class AskResponse(BaseModel):
    """Cited answer from the grounded pipeline."""

    answer: str = Field(description="Markdown; never platform-specific formats.")
    citations: list[dict[str, str]] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    grounding: GroundingInfo | None = None
    intent: str = "knowledge"
    cached: bool = False
    followups: list[str] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict, description="Seconds per pipeline stage.")
    request_id: str = Field(description="uuid4 echoed in logs for adapter-side correlation.")


class SearchRequest(BaseModel):
    """Retrieval-only query (POST /v1/search); no generation involved."""

    # Same ceiling as AskRequest.question so search cannot be a bypass for size.
    query: str = Field(max_length=4000)
    top_k: int = Field(default=5, ge=1, le=50)
    filters: dict[str, Any] | None = Field(default=None, description="Reserved for retrievers that support filtering.")


class SearchResult(BaseModel):
    """One raw corpus chunk returned by retrieval."""

    id: str
    text: str
    title: str
    url: str
    heading_path: list[str] = Field(default_factory=list)
    score: float


class SearchResponse(BaseModel):
    """Ranked retrieval results."""

    results: list[SearchResult] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    """User verdict on an answer (POST /v1/feedback)."""

    request_id: str
    verdict: Literal["up", "down"]
    comment: str | None = None
    user_id: str | None = Field(default=None, description="Hashed before storage; never persisted raw.")


AskFn = Callable[[AskRequest], Awaitable[AskResponse]]
"""The single seam between the core and every channel adapter."""
