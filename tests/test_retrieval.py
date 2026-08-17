from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.providers import LocalHashEmbedder
from app.retrieval import Chunk, Retriever, ScoredChunk, mmr_select, reciprocal_rank_fusion
from tests.conftest import write_index


def make_chunk(chunk_id: str, doc_id: str, text: str, embedding: list[float] | None = None) -> Chunk:
    return Chunk(
        id=chunk_id,
        doc_id=doc_id,
        title=f"Title {doc_id}",
        heading_path=[f"Heading {chunk_id}"],
        url="",
        text=text,
        identifiers=[],
        embedding=embedding or [],
    )


def test_bm25_ranks_identifier_match_above_generic_chunk(tmp_path: Path, embedder: LocalHashEmbedder) -> None:
    texts = [
        "Orders can be created and read with ordinary order workflow documentation.",
        "The ACME_ORDER_CONFLICT error means duplicate external_reference with a different body.",
    ]
    embeddings = embedder.embed(texts)
    chunks = [
        make_chunk("generic", "orders", texts[0], embeddings[0]),
        make_chunk("identifier", "orders-errors", texts[1], embeddings[1]),
    ]
    index_path = tmp_path / "index.jsonl"
    write_index(index_path, chunks)
    settings = Settings(index_path=index_path, rerank_pool=10, context_max_per_doc=2)
    retriever = Retriever(index_path, settings)

    results = retriever.retrieve("What does ACME_ORDER_CONFLICT mean?", embedder.embed(["ACME_ORDER_CONFLICT"])[0], k=2)

    assert results[0].chunk.id == "identifier"


def test_rrf_fusion_merges_lanes() -> None:
    one = make_chunk("one", "doc-one", "alpha", [1.0, 0.0])
    two = make_chunk("two", "doc-two", "beta", [0.0, 1.0])

    fused = reciprocal_rank_fusion(
        [
            [ScoredChunk(one, 0.9), ScoredChunk(two, 0.1)],
            [ScoredChunk(two, 0.8)],
        ],
        limit=2,
    )

    assert {item.chunk.id for item in fused} == {"one", "two"}
    assert fused[0].score > 0


def test_identifier_injection_forces_exact_match_into_pool(tmp_path: Path, embedder: LocalHashEmbedder) -> None:
    texts = [
        "Tell me about common return support path and documentation details.",
        "Refund submission endpoint is POST /v1/refunds for approved returns.",
    ]
    embeddings = embedder.embed(texts)
    chunks = [
        make_chunk("generic", "support", texts[0], embeddings[0]),
        Chunk(
            id="exact",
            doc_id="refunds",
            title="Refunds",
            heading_path=["Refund API"],
            url="",
            text=texts[1],
            identifiers=["/v1/refunds"],
            embedding=embeddings[1],
        ),
    ]
    index_path = tmp_path / "index.jsonl"
    write_index(index_path, chunks)
    settings = Settings(index_path=index_path, rerank_pool=1, context_max_per_doc=2, lexical_scorer="overlap")
    retriever = Retriever(index_path, settings)

    results = retriever.retrieve(
        "Tell me about common return support path /v1/refunds",
        embedder.embed(["Tell me about common return support path /v1/refunds"])[0],
        k=1,
    )

    assert results[0].chunk.id == "exact"


def test_mmr_reduces_same_doc_redundancy() -> None:
    first = make_chunk("a1", "doc-a", "alpha first", [1.0, 0.0])
    second = make_chunk("a2", "doc-a", "alpha second", [0.99, 0.01])
    third = make_chunk("b1", "doc-b", "beta first", [0.0, 1.0])
    candidates = [
        ScoredChunk(first, 1.0),
        ScoredChunk(second, 0.99),
        ScoredChunk(third, 0.98),
    ]

    selected = mmr_select(candidates, k=2, lambda_mult=0.5, max_per_doc=2)

    assert {item.chunk.doc_id for item in selected} == {"doc-a", "doc-b"}
