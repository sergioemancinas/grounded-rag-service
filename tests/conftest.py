from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.providers import LocalHashEmbedder
from app.retrieval import Chunk


@pytest.fixture()
def embedder() -> LocalHashEmbedder:
    return LocalHashEmbedder()


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
