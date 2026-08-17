"""Custom Embedder: local ONNX embeddings via fastembed.

Implements the ``Embedder`` protocol from app/interfaces.py, which is one
method. The heavy import lives inside the class so this file stays importable
(and CI-checkable) without fastembed installed.

Run it:

    pip install fastembed
    export EMBEDDER_CLASS=examples.custom_embedder_fastembed:FastEmbedEmbedder
    python scripts/build_index.py --docs data/sample_docs --out data/index.jsonl
    python scripts/smoke_query.py "How do refunds work?"

Rebuild the index whenever you change embedders: query vectors and stored
vectors must come from the same model.
"""

from __future__ import annotations

from app.config import Settings

MODEL_NAME = "BAAI/bge-small-en-v1.5"


class FastEmbedEmbedder:
    """Embedder backed by a local fastembed model (no API key, no network at query time)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self.model_name = MODEL_NAME
        self._model = None

    def _load(self):
        """Load the model once, on first use."""
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in input order."""
        if not texts:
            return []
        model = self._load()
        return [list(map(float, vector)) for vector in model.embed(texts)]
