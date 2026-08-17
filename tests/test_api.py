from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cache import SemanticCache
from app.config import Settings
from app.deps import get_deps
from app.interfaces import GroundingResult
from app.main import create_app
from app.pipeline import PipelineDeps
from app.providers import LocalHashEmbedder
from app.rerank import PassthroughReranker
from app.resilience import CircuitBreaker
from app.retrieval import Chunk, Retriever, ScoredChunk
from tests.conftest import write_index


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Offline Settings against a tiny freshly built index in tmp_path."""
    embedder = LocalHashEmbedder()
    texts = [
        "Returns can be created with POST /v1/returns and physical items are returnable within 30 days.",
        "Orders can be created with POST /v1/orders and require fulfillment_type.",
    ]
    embeddings = embedder.embed(texts)
    chunks = [
        Chunk(
            "returns:1",
            "returns",
            "Returns",
            ["Return Eligibility"],
            "https://docs.acme-storefront.example/returns",
            texts[0],
            ["/v1/returns"],
            embeddings[0],
        ),
        Chunk(
            "orders:1",
            "orders",
            "Orders",
            ["Create Order"],
            "https://docs.acme-storefront.example/orders",
            texts[1],
            ["/v1/orders", "fulfillment_type"],
            embeddings[1],
        ),
    ]
    index_path = tmp_path / "index.jsonl"
    write_index(index_path, chunks)
    return Settings(
        index_path=index_path,
        feedback_db_path=tmp_path / "feedback.sqlite3",
        cache_enabled=False,
        max_context_chunks=2,
        rerank_pool=5,
        **overrides,
    )


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_providers(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["chunks"] == 2
    assert body["providers"]["embedder"] == "local"
    assert body["providers"]["generator"] == "local"
    assert body["providers"]["reranker"] == "passthrough"
    assert body["providers"]["retriever"] == "jsonl"


def test_ask_returns_cited_answer(client: TestClient) -> None:
    response = client.post("/v1/ask", json={"question": "How do returns work?"})
    assert response.status_code == 200
    body = response.json()
    assert "Offline mode extractive answer" in body["answer"]
    assert body["citations"]
    assert body["sources"]
    assert body["sources"][0]["id"] in {"returns:1", "orders:1"}
    assert body["grounding"] is not None
    assert body["intent"] == "knowledge"
    assert body["cached"] is False
    assert "total" in body["timings"]
    assert len(body["request_id"]) == 36


def test_search_returns_raw_chunks(client: TestClient) -> None:
    response = client.post("/v1/search", json={"query": "create an order", "top_k": 1})
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["id"] == "orders:1"
    assert "fulfillment_type" in results[0]["text"]


def test_feedback_records_verdict(client: TestClient) -> None:
    ask = client.post("/v1/ask", json={"question": "How do returns work?"})
    request_id = ask.json()["request_id"]
    response = client.post("/v1/feedback", json={"request_id": request_id, "verdict": "up"})
    assert response.status_code == 204


def test_feedback_rejects_unknown_verdict(client: TestClient) -> None:
    response = client.post("/v1/feedback", json={"request_id": "x", "verdict": "sideways"})
    assert response.status_code == 422


def test_api_auth_token_gates_v1_routes(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path, api_auth_token="local-test-only"))
    with TestClient(app) as client:
        denied = client.post("/v1/search", json={"query": "orders"})
        assert denied.status_code == 401
        allowed = client.post(
            "/v1/search",
            json={"query": "orders"},
            headers={"Authorization": "Bearer local-test-only"},
        )
        assert allowed.status_code == 200
        health = client.get("/health")
        assert health.status_code == 200


class CannedGenerator:
    """Test double proving PipelineDeps swaps via dependency_overrides."""

    def generate(self, system: str, user: str, max_tokens: int) -> str:
        del system, user, max_tokens
        return "canned answer for override test [1]"


class ApprovingJudge:
    def judge(self, answer: str, chunks: Sequence[ScoredChunk]) -> GroundingResult:
        del answer, chunks
        return GroundingResult(score=1.0, verdict="supported", reasons=["test double"])


def test_dependency_overrides_swap_pipeline_deps(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    fake_deps = PipelineDeps(
        embedder=LocalHashEmbedder(),
        generator=CannedGenerator(),
        retriever=Retriever(settings.index_path, settings),
        reranker=PassthroughReranker(),
        cache=SemanticCache(enabled=False),
        breaker=CircuitBreaker(),
        grounding_judge=ApprovingJudge(),
    )
    app.dependency_overrides[get_deps] = lambda: fake_deps
    with TestClient(app) as client:
        response = client.post("/v1/ask", json={"question": "How do returns work?"})
    assert response.status_code == 200
    assert response.json()["answer"].startswith("canned answer")
