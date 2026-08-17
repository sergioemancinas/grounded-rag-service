"""Custom Reranker: a cross-encoder that rescores retrieved chunks.

Implements the ``Reranker`` protocol from app/interfaces.py. Note the
copy-don't-mutate contract: new ScoredChunk objects are built rather than
editing the ones retrieval returned.

Run it:

    pip install sentence-transformers
    export RERANKER_CLASS=examples.custom_reranker_crossencoder:CrossEncoderReranker
    export RERANK_ENABLED=true
    python scripts/smoke_query.py "How do refunds work?"

A cross-encoder reads the query and each candidate together, which is far
more accurate than vector similarity and far slower, so it runs only over
the top RERANK_POOL candidates.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from app.config import Settings
from app.retrieval import ScoredChunk

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderReranker:
    """Reranker backed by a sentence-transformers cross-encoder."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self.model_name = MODEL_NAME
        self._model = None

    def _load(self):
        """Load the cross-encoder once, on first use."""
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(self, query: str, chunks: Sequence[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        """Return up to ``top_k`` chunks reordered by cross-encoder score."""
        if not chunks:
            return []
        model = self._load()
        scores = model.predict([(query, scored.chunk.text) for scored in chunks])
        rescored = [
            replace(scored, score=float(score), scores={**scored.scores, "cross_encoder": float(score)})
            for scored, score in zip(chunks, scores, strict=True)
        ]
        rescored.sort(key=lambda item: item.score, reverse=True)
        return rescored[:top_k]
