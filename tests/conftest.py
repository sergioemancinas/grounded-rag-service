from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.providers import LocalHashEmbedder
from app.retrieval import Chunk


@pytest.fixture()
def embedder() -> LocalHashEmbedder:
    return LocalHashEmbedder()


@pytest.fixture()
def fake_deps(tmp_path: Path):
    """A fully local PipelineDeps over a two-chunk index, for offline tests."""
    from app.config import Settings
    from app.deps import build_deps

    embed = LocalHashEmbedder()
    chunks = [
        Chunk(
            id="c1",
            doc_id="orders-api",
            title="Orders API",
            heading_path=["Orders API", "Create Order"],
            url="https://docs.acme-storefront.example/orders-api",
            text="Use POST /v1/orders to create an order with fulfillment_type and line_items.",
            identifiers=["POST /v1/orders", "fulfillment_type"],
            embedding=embed.embed(["Use POST /v1/orders to create an order."])[0],
        ),
        Chunk(
            id="c2",
            doc_id="returns-and-refunds",
            title="Returns And Refunds",
            heading_path=["Returns And Refunds", "Refund API"],
            url="https://docs.acme-storefront.example/returns-and-refunds",
            text="Use POST /v1/refunds to submit a refund after return inspection.",
            identifiers=["POST /v1/refunds"],
            embedding=embed.embed(["Use POST /v1/refunds to submit a refund."])[0],
        ),
    ]
    index_path = tmp_path / "index.jsonl"
    write_index(index_path, chunks)
    return build_deps(Settings(index_path=index_path, feedback_db_path=tmp_path / "feedback.sqlite3"))


def write_index(path: Path, chunks: list[Chunk]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(
                json.dumps(
                    {
                        "id": chunk.id,
                        "doc_id": chunk.doc_id,
                        "title": chunk.title,
                        "heading_path": chunk.heading_path,
                        "url": chunk.url,
                        "text": chunk.text,
                        "identifiers": chunk.identifiers,
                        "embedding": chunk.embedding,
                    }
                )
                + "\n"
            )
