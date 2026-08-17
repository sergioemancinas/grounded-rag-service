"""Custom Retriever: serve chunks from SQLite instead of the JSONL index.

Implements the ``Retriever`` protocol from app/interfaces.py. Swapping the
store changes only where chunks come from: RRF fusion across query
phrasings, MMR diversity, and the grounding gate all stay in the pipeline.

This example uses SQLite with brute-force cosine because it needs no server
and no extra dependency. A pgvector or Qdrant retriever has the same shape:
run the query, return ``ScoredChunk`` objects carrying stable ids.

Run it:

    python examples/custom_store_sqlite.py data/index.jsonl data/index.sqlite3
    export RETRIEVER_CLASS=examples.custom_store_sqlite:SqliteRetriever
    export SQLITE_INDEX_PATH=data/index.sqlite3
    python scripts/smoke_query.py "How do refunds work?"
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

from app.config import Settings
from app.retrieval import Chunk, ScoredChunk, cosine_similarity


class SqliteRetriever:
    """Retriever over a SQLite table of chunks with stored embeddings."""

    def __init__(self, settings: Settings | None = None) -> None:
        path = os.environ.get("SQLITE_INDEX_PATH", "data/index.sqlite3")
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.chunks = [
            Chunk(
                id=row[0],
                doc_id=row[1],
                title=row[2],
                heading_path=json.loads(row[3]),
                url=row[4],
                text=row[5],
                identifiers=json.loads(row[6]),
                embedding=json.loads(row[7]),
            )
            for row in self.connection.execute(
                "SELECT id, doc_id, title, heading_path, url, text, identifiers, embedding FROM chunks"
            )
        ]

    @property
    def chunk_count(self) -> int:
        """Exposed on /health and used by the MCP fetch tool."""
        return len(self.chunks)

    def retrieve(self, query: str, query_embedding: Sequence[float], k: int) -> list[ScoredChunk]:
        """Return the ``k`` chunks closest to the query embedding."""
        scored = [
            ScoredChunk(chunk=chunk, score=cosine_similarity(query_embedding, chunk.embedding))
            for chunk in self.chunks
        ]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:k]


def convert(jsonl_path: Path, sqlite_path: Path) -> int:
    """Load a JSONL index into a SQLite table; returns the row count."""
    connection = sqlite3.connect(sqlite_path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS chunks "
        "(id TEXT PRIMARY KEY, doc_id TEXT, title TEXT, heading_path TEXT, "
        "url TEXT, text TEXT, identifiers TEXT, embedding TEXT)"
    )
    rows = 0
    with jsonl_path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            connection.execute(
                "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record["id"],
                    record["doc_id"],
                    record["title"],
                    json.dumps(record.get("heading_path", [])),
                    record.get("url", ""),
                    record["text"],
                    json.dumps(record.get("identifiers", [])),
                    json.dumps(record.get("embedding", [])),
                ),
            )
            rows += 1
    connection.commit()
    return rows


if __name__ == "__main__":
    count = convert(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"wrote {count} chunks to {sys.argv[2]}")
