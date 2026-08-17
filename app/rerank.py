from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.config import Settings
from app.retrieval import ScoredChunk


class Reranker(Protocol):
    def rerank(self, query: str, chunks: Sequence[ScoredChunk], top_k: int) -> list[ScoredChunk]: ...


class PassthroughReranker:
    def rerank(self, query: str, chunks: Sequence[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        del query
        return list(chunks)[:top_k]


class CrossEncoderReranker:
    def rerank(self, query: str, chunks: Sequence[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        del query, chunks, top_k
        raise NotImplementedError(
            "Configure a hosted reranking provider here. Keep the contract as "
            "rerank(query, chunks, top_k) -> list[ScoredChunk] so the pipeline "
            "can swap rerankers without changing retrieval or generation."
        )


def get_reranker(settings: Settings) -> Reranker:
    if settings.rerank_enabled:
        return CrossEncoderReranker()
    return PassthroughReranker()
