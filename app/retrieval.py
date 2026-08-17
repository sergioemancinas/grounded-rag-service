from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings

logger = logging.getLogger("citespine.retrieval")

TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]+")
IDENTIFIER_RE = re.compile(
    r"\b[A-Z][A-Z0-9_]{2,}\b|/[A-Za-z0-9_./-]+|"
    r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b|"
    r"\b[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+\b"
)

MANIFEST_SCHEMA_VERSION = 1


def index_manifest_path(index_path: Path) -> Path:
    """Sidecar path for ``index.jsonl`` → ``index.manifest.json``."""
    return index_path.with_name(f"{index_path.stem}.manifest.json")


def file_sha256(path: Path) -> str:
    """Content digest used to detect on-disk tampering of the index."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(65536)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify_index_integrity(index_path: Path, settings: Settings) -> None:
    """Check the index against its sidecar manifest when one is present.

    Write access to ``data/`` is otherwise equivalent to control over every
    answer (LLM04 / STRIDE tampering). A missing manifest is tolerated so
    older indexes still load; ``index_verify`` chooses how mismatches fail.
    """
    mode = settings.index_verify.lower().strip()
    if mode == "off":
        return
    manifest_path = index_manifest_path(index_path)
    if not manifest_path.exists():
        logger.warning(
            "index integrity: no manifest at %s; corpus integrity is unverified",
            manifest_path,
        )
        return
    if not index_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        message = f"index integrity: unreadable manifest {manifest_path}: {error}"
        if mode == "strict":
            raise RuntimeError(message) from error
        logger.warning("%s", message)
        return
    expected = str(manifest.get("index_sha256", ""))
    actual = file_sha256(index_path)
    if not expected or not _digests_match(expected, actual):
        message = f"index integrity: digest mismatch for {index_path} (manifest={expected!r}, actual={actual!r})"
        if mode == "strict":
            raise RuntimeError(message)
        logger.warning("%s", message)


def _digests_match(left: str, right: str) -> bool:
    """Constant-time hex digest compare; length mismatch is a miss, not an error."""
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))


@dataclass(frozen=True)
class Chunk:
    id: str
    doc_id: str
    title: str
    heading_path: list[str]
    url: str
    text: str
    identifiers: list[str] = field(default_factory=list)
    embedding: list[float] = field(default_factory=list)
    # Provenance: which ingestion source produced this chunk, and the URL of
    # the originating document. Both travel with the answer so a poisoned
    # corpus entry can be traced after the fact.
    source: str = ""
    source_url: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Chunk:
        heading_path = data.get("heading_path", [])
        identifiers = data.get("identifiers", [])
        embedding = data.get("embedding", [])
        source_url = str(data.get("source_url", "") or data.get("url", ""))
        return cls(
            id=str(data["id"]),
            doc_id=str(data["doc_id"]),
            title=str(data["title"]),
            heading_path=[str(item) for item in heading_path] if isinstance(heading_path, list) else [],
            url=str(data.get("url", "")),
            text=str(data["text"]),
            identifiers=[str(item) for item in identifiers] if isinstance(identifiers, list) else [],
            embedding=[float(item) for item in embedding] if isinstance(embedding, list) else [],
            source=str(data.get("source", "")),
            source_url=source_url,
        )


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk
    score: float
    scores: dict[str, float] = field(default_factory=dict)


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def extract_identifiers(text: str) -> list[str]:
    seen: set[str] = set()
    identifiers: list[str] = []
    for match in IDENTIFIER_RE.finditer(text):
        value = match.group(0)
        key = value.lower()
        if key not in seen:
            identifiers.append(value)
            seen.add(key)
    return identifiers


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        return 0.0
    limit = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(limit))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[ScoredChunk]],
    limit: int,
    constant: int = 60,
) -> list[ScoredChunk]:
    by_id: dict[str, ScoredChunk] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    lane_scores: defaultdict[str, dict[str, float]] = defaultdict(dict)
    for lane_index, ranked in enumerate(ranked_lists):
        for rank, scored in enumerate(ranked, start=1):
            chunk_id = scored.chunk.id
            by_id[chunk_id] = scored
            scores[chunk_id] += 1.0 / (constant + rank)
            lane_scores[chunk_id][f"rrf_lane_{lane_index}"] = scored.score
    fused = [
        ScoredChunk(chunk=by_id[chunk_id].chunk, score=score, scores=lane_scores[chunk_id])
        for chunk_id, score in scores.items()
    ]
    fused.sort(key=lambda item: item.score, reverse=True)
    return fused[:limit]


def mmr_select(
    candidates: Sequence[ScoredChunk],
    k: int,
    lambda_mult: float,
    max_per_doc: int | None = None,
) -> list[ScoredChunk]:
    if k <= 0:
        return []
    remaining = list(candidates)
    selected: list[ScoredChunk] = []
    doc_counts: Counter[str] = Counter()
    max_score = max((abs(item.score) for item in remaining), default=1.0) or 1.0

    while remaining and len(selected) < k:
        best_index = 0
        best_value = -float("inf")
        for index, candidate in enumerate(remaining):
            if max_per_doc is not None and doc_counts[candidate.chunk.doc_id] >= max_per_doc:
                continue
            relevance = candidate.score / max_score
            diversity_penalty = 0.0
            if selected:
                diversity_penalty = max(_chunk_similarity(candidate.chunk, item.chunk) for item in selected)
            value = lambda_mult * relevance - (1.0 - lambda_mult) * diversity_penalty
            if value > best_value:
                best_value = value
                best_index = index
        candidate = remaining.pop(best_index)
        if max_per_doc is not None and doc_counts[candidate.chunk.doc_id] >= max_per_doc:
            continue
        selected.append(candidate)
        doc_counts[candidate.chunk.doc_id] += 1
    return selected


def _chunk_similarity(left: Chunk, right: Chunk) -> float:
    if left.id == right.id:
        return 1.0
    similarity = cosine_similarity(left.embedding, right.embedding)
    if left.doc_id == right.doc_id:
        return max(similarity, 0.85)
    return similarity


class Retriever:
    def __init__(self, index_path: Path, settings: Settings | None = None) -> None:
        self.index_path = index_path
        self.settings = settings or Settings()
        verify_index_integrity(index_path, self.settings)
        self.chunks: list[Chunk] = self._load_index(index_path)
        self._tokens_by_id: dict[str, list[str]] = {chunk.id: tokenize(chunk.text) for chunk in self.chunks}
        self._term_freqs: dict[str, Counter[str]] = {
            chunk.id: Counter(self._tokens_by_id[chunk.id]) for chunk in self.chunks
        }
        self._doc_freqs: Counter[str] = Counter()
        for tokens in self._tokens_by_id.values():
            self._doc_freqs.update(set(tokens))
        self._avgdl = (
            sum(len(tokens) for tokens in self._tokens_by_id.values()) / len(self._tokens_by_id)
            if self._tokens_by_id
            else 0.0
        )

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    def retrieve(self, query: str, query_embedding: Sequence[float], k: int) -> list[ScoredChunk]:
        if not self.chunks:
            return []
        pool_size = max(k, self.settings.rerank_pool)
        dense_ranked = self._dense_rank(query_embedding, pool_size)
        lexical_ranked = self._lexical_rank(query, pool_size)
        fused = reciprocal_rank_fusion([dense_ranked, lexical_ranked], pool_size)
        fused = self._inject_identifier_matches(query, fused)
        fused.sort(key=lambda item: item.score, reverse=True)
        deduped = self._dedup(fused, pool_size)
        return mmr_select(
            deduped,
            k=k,
            lambda_mult=self.settings.mmr_lambda,
            max_per_doc=self.settings.context_max_per_doc,
        )

    def _load_index(self, index_path: Path) -> list[Chunk]:
        if not index_path.exists():
            return []
        chunks: list[Chunk] = []
        with index_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    chunks.append(Chunk.from_dict(json.loads(line)))
        return chunks

    def _dense_rank(self, query_embedding: Sequence[float], limit: int) -> list[ScoredChunk]:
        scored = [
            ScoredChunk(
                chunk=chunk,
                score=cosine_similarity(query_embedding, chunk.embedding),
                scores={"dense": cosine_similarity(query_embedding, chunk.embedding)},
            )
            for chunk in self.chunks
        ]
        scored = [item for item in scored if item.score > 0.0]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    def _lexical_rank(self, query: str, limit: int) -> list[ScoredChunk]:
        query_tokens = tokenize(query)
        if self.settings.lexical_scorer == "overlap":
            scored = [
                ScoredChunk(
                    chunk=chunk,
                    score=self._overlap_score(query_tokens, chunk),
                    scores={"overlap": self._overlap_score(query_tokens, chunk)},
                )
                for chunk in self.chunks
            ]
        else:
            scored = [
                ScoredChunk(
                    chunk=chunk,
                    score=self._bm25_score(query_tokens, chunk),
                    scores={"bm25": self._bm25_score(query_tokens, chunk)},
                )
                for chunk in self.chunks
            ]
        scored = [item for item in scored if item.score > 0.0]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    def _bm25_score(self, query_tokens: list[str], chunk: Chunk) -> float:
        if not query_tokens or self._avgdl == 0.0:
            return 0.0
        term_freq = self._term_freqs[chunk.id]
        doc_len = len(self._tokens_by_id[chunk.id])
        score = 0.0
        total_docs = len(self.chunks)
        for token in query_tokens:
            frequency = term_freq[token]
            if frequency == 0:
                continue
            doc_frequency = self._doc_freqs[token]
            idf = math.log(1.0 + (total_docs - doc_frequency + 0.5) / (doc_frequency + 0.5))
            denominator = frequency + self.settings.bm25_k1 * (
                1.0 - self.settings.bm25_b + self.settings.bm25_b * doc_len / self._avgdl
            )
            score += idf * (frequency * (self.settings.bm25_k1 + 1.0)) / denominator
        return score

    def _overlap_score(self, query_tokens: list[str], chunk: Chunk) -> float:
        if not query_tokens:
            return 0.0
        doc_tokens = set(self._tokens_by_id[chunk.id])
        overlap = len(set(query_tokens) & doc_tokens)
        return overlap / math.sqrt(max(len(doc_tokens), 1))

    def _inject_identifier_matches(self, query: str, candidates: list[ScoredChunk]) -> list[ScoredChunk]:
        identifiers = {identifier.lower() for identifier in extract_identifiers(query)}
        if not identifiers:
            return candidates
        by_id = {item.chunk.id: item for item in candidates}
        for chunk in self.chunks:
            chunk_identifiers = {identifier.lower() for identifier in chunk.identifiers}
            text_lower = chunk.text.lower()
            if not any(identifier in chunk_identifiers or identifier in text_lower for identifier in identifiers):
                continue
            existing = by_id.get(chunk.id)
            if existing is None:
                by_id[chunk.id] = ScoredChunk(chunk=chunk, score=1.0, scores={"identifier": 1.0})
            else:
                merged_scores = dict(existing.scores)
                merged_scores["identifier"] = 1.0
                by_id[chunk.id] = ScoredChunk(chunk=chunk, score=existing.score + 1.0, scores=merged_scores)
        return list(by_id.values())

    def _dedup(self, candidates: Iterable[ScoredChunk], limit: int) -> list[ScoredChunk]:
        selected: list[ScoredChunk] = []
        seen_ids: set[str] = set()
        title_owner: dict[str, str] = {}
        doc_counts: Counter[str] = Counter()
        for item in candidates:
            chunk = item.chunk
            if chunk.id in seen_ids:
                continue
            title_key = chunk.title.strip().lower()
            if title_key in title_owner and title_owner[title_key] != chunk.doc_id:
                continue
            if doc_counts[chunk.doc_id] >= self.settings.context_max_per_doc:
                continue
            title_owner.setdefault(title_key, chunk.doc_id)
            seen_ids.add(chunk.id)
            doc_counts[chunk.doc_id] += 1
            selected.append(item)
            if len(selected) >= limit:
                break
        return selected
