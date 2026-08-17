from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.cache import SemanticCache
from app.config import Settings
from app.grounding import GroundingResult
from app.pipeline import PipelineDeps, answer_question
from app.providers import LocalExtractiveGenerator, LocalHashEmbedder
from app.rerank import PassthroughReranker
from app.resilience import CircuitBreaker
from app.retrieval import Chunk, Retriever, ScoredChunk
from tests.conftest import write_index


class CountingJudge:
    def __init__(self) -> None:
        self.calls = 0

    def judge(self, answer: str, chunks: Sequence[ScoredChunk]) -> GroundingResult:
        self.calls += 1
        return GroundingResult(score=0.9, verdict="supported", reasons=[f"{len(chunks)} chunks checked"])


def test_pipeline_offline_end_to_end(tmp_path: Path) -> None:
    embedder = LocalHashEmbedder()
    texts = [
        "Returns can be created with POST /v1/returns and physical items are returnable within 30 days.",
        "Orders can be created with POST /v1/orders and require fulfillment_type.",
    ]
    embeddings = embedder.embed(texts)
    chunks = [
        Chunk("returns:1", "returns", "Returns", ["Return Eligibility"], "", texts[0], ["/v1/returns"], embeddings[0]),
        Chunk(
            "orders:1",
            "orders",
            "Orders",
            ["Create Order"],
            "",
            texts[1],
            ["/v1/orders", "fulfillment_type"],
            embeddings[1],
        ),
    ]
    index_path = tmp_path / "index.jsonl"
    write_index(index_path, chunks)
    settings = Settings(index_path=index_path, max_context_chunks=2, rerank_pool=5, cache_enabled=False)
    judge = CountingJudge()
    deps = PipelineDeps(
        embedder=embedder,
        generator=LocalExtractiveGenerator(),
        retriever=Retriever(index_path, settings),
        reranker=PassthroughReranker(),
        cache=SemanticCache(enabled=False),
        breaker=CircuitBreaker(),
        grounding_judge=judge,
    )

    result = answer_question("How do returns work?", history=[], settings=settings, deps=deps)

    assert "Offline mode extractive answer" in result.answer
    assert "[1]" in result.answer
    assert result.citations
    assert result.grounding is not None
    assert judge.calls >= 1
