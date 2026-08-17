"""Stage Protocols for the answer pipeline.

Every pluggable stage is described here as a small synchronous Protocol.
Implementers only need this file: each docstring carries the full contract,
so nobody has to read app/pipeline.py to write a custom stage.

Shared contract for every stage: copy, don't mutate. Stages receive shared
objects (chunk lists, ScoredChunk instances) and must return new lists or
new objects instead of mutating their inputs.

Wiring happens by name through app.registry (flat dicts populated by
decorators in app/providers.py and app/grounding.py) or by dotted path
through the ``*_CLASS`` settings in app/config.py. Conformance is
duck-checked at wiring time with ``_check``; ``@runtime_checkable``
isinstance checks are deliberately avoided.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, TypeVar

from app.retrieval import ScoredChunk


T = TypeVar("T")


@dataclass(frozen=True)
class GroundingResult:
    """Verdict of a grounding check over a generated answer.

    ``score`` is 0..1 faithfulness, ``verdict`` a short label such as
    "supported" or "weak", and ``reasons`` human-readable explanations.
    """

    score: float
    verdict: str
    reasons: list[str]


class Embedder(Protocol):
    """Turns texts into fixed-width vectors for dense retrieval."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each text; return one vector per input, in input order.

        Vectors must have a consistent dimensionality per embedder instance.
        The same embedder must be used at index build time and query time.
        """
        ...


class Generator(Protocol):
    """Produces answer text from a system prompt and a user message."""

    def generate(self, system: str, user: str, max_tokens: int) -> str:
        """Return the model output as plain markdown text.

        Implementations should treat ``user`` content as untrusted data and
        may truncate at ``max_tokens``. Lazy-import provider SDKs inside the
        method body so they stay optional dependencies.
        """
        ...


class Reranker(Protocol):
    """Reorders retrieved chunks by relevance to the query."""

    def rerank(self, query: str, chunks: Sequence[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        """Return up to ``top_k`` chunks, best first, as a new list.

        Must not mutate ``chunks`` or the ScoredChunk instances inside it;
        build new ScoredChunk objects when scores change.
        """
        ...


class Retriever(Protocol):
    """Searches the corpus index for chunks relevant to a query."""

    def retrieve(self, query: str, query_embedding: Sequence[float], k: int) -> list[ScoredChunk]:
        """Return up to ``k`` ScoredChunk results, best first.

        Chunks must carry stable ids so fusion, feedback, and MCP fetch can
        reference them across calls. Hybrid (dense + lexical) scoring is the
        retriever's concern; RRF fusion across query phrasings stays in the
        pipeline.
        """
        ...


class GroundingJudge(Protocol):
    """Scores how faithful an answer is to its source chunks."""

    def judge(self, answer: str, chunks: Sequence[ScoredChunk]) -> GroundingResult:
        """Return a GroundingResult; never raise on judge failure.

        Prefer returning a low score with a "judge_error" verdict so the
        pipeline's regenerate-then-caveat flow stays deterministic.
        """
        ...


def _check(obj: T, *methods: str) -> T:
    """Duck-validate that ``obj`` exposes every method in ``methods``.

    Used at wiring time (app/deps.py) instead of runtime isinstance checks;
    raises TypeError naming the object and the missing methods.
    """
    missing = [name for name in methods if not callable(getattr(obj, name, None))]
    if missing:
        raise TypeError(
            f"{type(obj).__name__} does not satisfy the stage protocol: "
            f"missing callable method(s) {', '.join(missing)}"
        )
    return obj
