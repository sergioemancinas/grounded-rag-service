"""Dependency composition for the core service.

``build_deps`` is the one place where registry names, dotted-path escape
hatches, and settings meet to produce a wired PipelineDeps. Tests either
swap single routes via FastAPI ``dependency_overrides`` on ``get_deps`` /
``get_settings``, or construct PipelineDeps directly.
"""

from __future__ import annotations

from fastapi import Request

from app.cache import SemanticCache
from app.config import Settings
from app.grounding import get_grounding_judge
from app.interfaces import _check
from app.pipeline import PipelineDeps
from app.providers import get_embedder, get_generator, get_reranker, get_retriever
from app.resilience import CircuitBreaker


def build_deps(settings: Settings) -> PipelineDeps:
    """Compose PipelineDeps from the registries and escape hatches.

    Every resolved component is duck-checked against its stage Protocol so a
    miswired dotted path fails at startup, not on the first request.
    """
    return PipelineDeps(
        embedder=_check(get_embedder(settings), "embed"),
        generator=_check(get_generator(settings), "generate"),
        retriever=_check(get_retriever(settings), "retrieve"),
        reranker=_check(get_reranker(settings), "rerank"),
        cache=SemanticCache(
            enabled=settings.cache_enabled,
            similarity_threshold=settings.cache_similarity,
            ttl_seconds=settings.cache_ttl_seconds,
        ),
        breaker=CircuitBreaker(),
        grounding_judge=_check(get_grounding_judge(settings), "judge"),
    )


def provider_names(settings: Settings) -> dict[str, str]:
    """Resolved implementation name per stage, echoed by GET /health."""

    def name(hatch: object, fallback: str) -> str:
        if hatch is None:
            return fallback
        module = getattr(hatch, "__module__", type(hatch).__module__)
        qualname = getattr(hatch, "__qualname__", type(hatch).__qualname__)
        return f"{module}:{qualname}"

    return {
        "embedder": name(settings.embedder_class, settings.embedding_provider),
        "generator": name(settings.generator_class, settings.generation_provider),
        "reranker": name(settings.reranker_class, "cross_encoder" if settings.rerank_enabled else "passthrough"),
        "retriever": name(settings.retriever_class, "jsonl"),
    }


def get_settings(request: Request) -> Settings:
    """FastAPI accessor for the Settings built at startup (app.state)."""
    return request.app.state.settings


def get_deps(request: Request) -> PipelineDeps:
    """FastAPI accessor for the PipelineDeps built at startup (app.state)."""
    return request.app.state.deps
