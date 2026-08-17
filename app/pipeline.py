from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

from app.cache import GLOBAL_SCOPE, SemanticCache
from app.config import Settings
from app.grounding import get_grounding_judge
from app.interfaces import Embedder, Generator, GroundingJudge, GroundingResult, Reranker, Retriever
from app.llm import Answer, expand_query, generate_answer
from app.resilience import CircuitBreaker
from app.retrieval import ScoredChunk, mmr_select, reciprocal_rank_fusion
from app.router import RouterResult, route_intent


@dataclass
class PipelineDeps:
    """Everything answer_question() needs, one Protocol-typed slot per stage.

    Stages must treat their inputs as read-only (copy, don't mutate); see
    app/interfaces.py for each contract. Construct directly in tests, or via
    app.deps.build_deps for the registry-wired composition.
    """

    embedder: Embedder
    generator: Generator
    retriever: Retriever
    reranker: Reranker
    cache: SemanticCache
    breaker: CircuitBreaker[object]
    grounding_judge: GroundingJudge | None = None


@dataclass(frozen=True)
class PipelineResult:
    answer: str
    citations: list[dict[str, str]]
    chunks: list[ScoredChunk]
    timings: dict[str, float]
    grounding: GroundingResult | None
    route: RouterResult
    cached: bool = False
    followups: list[str] = field(default_factory=list)


class _BreakerGenerator:
    def __init__(self, generator: Generator, breaker: CircuitBreaker[object]) -> None:
        self.generator = generator
        self.breaker = breaker

    def generate(self, system: str, user: str, max_tokens: int) -> str:
        return cast(str, self.breaker.call(self.generator.generate, system, user, max_tokens))


def answer_question(
    question: str,
    history: Sequence[str],
    settings: Settings,
    deps: PipelineDeps,
    cache_scope: str = GLOBAL_SCOPE,
) -> PipelineResult:
    """Run the grounded pipeline for one question.

    ``cache_scope`` partitions the semantic cache. Any caller serving more
    than one audience must pass the identity of the asker: a cache hit
    bypasses retrieval entirely, so the cache, not the retriever, is where a
    shared answer would cross an authorization boundary.
    """
    timings: dict[str, float] = {}
    total_start = time.perf_counter()
    guarded_generator = _BreakerGenerator(deps.generator, deps.breaker)

    cache_start = time.perf_counter()
    question_embedding = cast(list[list[float]], deps.breaker.call(deps.embedder.embed, [question]))[0]
    cached_answer = deps.cache.get(question_embedding, scope=cache_scope)
    timings["cache"] = time.perf_counter() - cache_start
    if cached_answer is not None:
        timings["total"] = time.perf_counter() - total_start
        return PipelineResult(
            answer=cached_answer,
            citations=[],
            chunks=[],
            timings=timings,
            grounding=None,
            route=RouterResult(intent="knowledge"),
            cached=True,
        )

    route_start = time.perf_counter()
    route = route_intent(question, settings, guarded_generator)
    timings["router"] = time.perf_counter() - route_start
    if route.intent != "knowledge":
        timings["total"] = time.perf_counter() - total_start
        return PipelineResult(
            answer=route.reply or "I can only answer grounded documentation questions.",
            citations=[],
            chunks=[],
            timings=timings,
            grounding=None,
            route=route,
        )

    expand_start = time.perf_counter()
    phrasings = expand_query(question, history, settings, guarded_generator)
    timings["expand"] = time.perf_counter() - expand_start

    retrieve_start = time.perf_counter()
    phrase_embeddings = cast(list[list[float]], deps.breaker.call(deps.embedder.embed, phrasings))
    retrieval_lists: list[list[ScoredChunk]] = []
    for phrasing, embedding in zip(phrasings, phrase_embeddings, strict=True):
        retrieval_lists.append(deps.retriever.retrieve(phrasing, embedding, settings.rerank_pool))
    merged = reciprocal_rank_fusion(retrieval_lists, settings.rerank_pool)
    timings["retrieve"] = time.perf_counter() - retrieve_start

    rerank_start = time.perf_counter()
    reranked = deps.reranker.rerank(question, merged, settings.rerank_pool)
    selected = mmr_select(
        reranked,
        k=settings.max_context_chunks,
        lambda_mult=settings.mmr_lambda,
        max_per_doc=settings.context_max_per_doc,
    )
    timings["rerank_mmr"] = time.perf_counter() - rerank_start

    generate_start = time.perf_counter()
    answer = generate_answer(question, selected, history, settings, guarded_generator)
    timings["generate"] = time.perf_counter() - generate_start

    grounding: GroundingResult | None = None
    if settings.grounding_check_enabled:
        grounding_start = time.perf_counter()
        judge = deps.grounding_judge or get_grounding_judge(settings)
        grounding = judge.judge(answer.text, selected)
        if grounding.score < settings.grounding_min_score:
            answer = generate_answer(question, selected, history, settings, guarded_generator, strict=True)
            grounding = judge.judge(answer.text, selected)
            if grounding.score < settings.grounding_min_score:
                answer = Answer(
                    text="Low confidence: I could not fully verify this against the retrieved sources.\n\n"
                    + answer.text,
                    citations=answer.citations,
                )
        timings["grounding"] = time.perf_counter() - grounding_start

    deps.cache.set(question_embedding, answer.text, scope=cache_scope)
    timings["total"] = time.perf_counter() - total_start
    return PipelineResult(
        answer=answer.text,
        citations=answer.citations,
        chunks=selected,
        timings=timings,
        grounding=grounding,
        route=route,
    )
