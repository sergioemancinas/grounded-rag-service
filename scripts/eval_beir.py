#!/usr/bin/env python3
"""Public BEIR retrieval benchmark for citespine's lexical / dense / hybrid lanes.

Downloads and caches datasets under data/benchmarks/. Stdlib only for I/O;
retrieval uses this repo's tokenize / BM25 / LocalHashEmbedder / cosine /
RRF implementations. Does not exercise generation or the grounding gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.providers import LocalHashEmbedder
from app.retrieval import Retriever, reciprocal_rank_fusion

DATASET_URLS = {
    "scifact": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip",
}
CANDIDATE_POOL = 100
RRF_CONSTANT = 60
METRIC_K = 10
DEFAULT_CACHE_ROOT = Path("data/benchmarks")


def dcg_at_k(gains: Sequence[float], k: int) -> float:
    """Discounted cumulative gain with log2 discount over the first *k* ranks."""
    total = 0.0
    for rank, gain in enumerate(gains[:k], start=1):
        total += gain / math.log2(rank + 1)
    return total


def ndcg_at_k(ranked_doc_ids: Sequence[str], qrels: dict[str, float], k: int) -> float:
    """nDCG@k with graded relevance gains ``2^rel - 1`` and ideal DCG from sorted labels."""
    gains = [math.pow(2.0, qrels.get(doc_id, 0.0)) - 1.0 for doc_id in ranked_doc_ids[:k]]
    dcg = dcg_at_k(gains, k)
    ideal_grades = sorted(qrels.values(), reverse=True)
    ideal_gains = [math.pow(2.0, grade) - 1.0 for grade in ideal_grades]
    idcg = dcg_at_k(ideal_gains, k)
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def recall_at_k(ranked_doc_ids: Sequence[str], qrels: dict[str, float], k: int) -> float:
    """Fraction of relevant documents (rel > 0) recovered in the top *k* ranks."""
    relevant = {doc_id for doc_id, grade in qrels.items() if grade > 0.0}
    if not relevant:
        return 0.0
    hit = sum(1 for doc_id in ranked_doc_ids[:k] if doc_id in relevant)
    return hit / len(relevant)


def mrr_at_k(ranked_doc_ids: Sequence[str], qrels: dict[str, float], k: int) -> float:
    """Mean reciprocal rank of the first relevant hit within the top *k* ranks."""
    for rank, doc_id in enumerate(ranked_doc_ids[:k], start=1):
        if qrels.get(doc_id, 0.0) > 0.0:
            return 1.0 / rank
    return 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate citespine retrieval lanes on a BEIR dataset (default: SciFact)."
    )
    parser.add_argument("--dataset", default="scifact", choices=sorted(DATASET_URLS))
    parser.add_argument(
        "--lanes",
        default="bm25",
        help="Comma-separated lanes: bm25, dense, hybrid (aliases: bm25_only, dense_only, hybrid_rrf).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Evaluate at most N queries.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Exit 1 if any selected lane's nDCG@10 falls below this value.",
    )
    parser.add_argument("--json", type=Path, default=None, help="Write full metrics JSON to PATH.")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_ROOT,
        help="Directory for downloaded BEIR archives and unpacked corpora.",
    )
    return parser.parse_args()


def normalize_lane(name: str) -> str:
    aliases = {
        "bm25": "bm25",
        "bm25_only": "bm25",
        "dense": "dense",
        "dense_only": "dense",
        "hybrid": "hybrid",
        "hybrid_rrf": "hybrid",
    }
    key = name.strip().lower()
    if key not in aliases:
        raise SystemExit(f"Unknown lane {name!r}; expected bm25, dense, or hybrid")
    return aliases[key]


def ensure_dataset(name: str, cache_dir: Path) -> Path:
    """Download and unpack a BEIR dataset zip into cache_dir/<name>/ if needed."""
    url = DATASET_URLS[name]
    dataset_dir = cache_dir / name
    marker = dataset_dir / "corpus.jsonl"
    if marker.exists():
        return dataset_dir

    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / f"{name}.zip"
    if not zip_path.exists():
        print(f"Downloading {url}")
        urllib.request.urlretrieve(url, zip_path)

    print(f"Unpacking {zip_path} -> {cache_dir}")
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(cache_dir)

    if not marker.exists():
        raise SystemExit(f"Expected {marker} after unpacking {zip_path}")
    return dataset_dir


def read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_qrels(path: Path) -> dict[str, dict[str, float]]:
    """Load BEIR/TREC qrels TSV (query-id, corpus-id, score) into nested maps."""
    qrels: dict[str, dict[str, float]] = defaultdict(dict)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = {name.lower(): name for name in (reader.fieldnames or [])}
        query_key = fieldnames.get("query-id") or fieldnames.get("qid")
        doc_key = fieldnames.get("corpus-id") or fieldnames.get("docid")
        score_key = fieldnames.get("score") or fieldnames.get("relevance")
        if not query_key or not doc_key or not score_key:
            raise SystemExit(f"Unrecognized qrels header in {path}: {reader.fieldnames}")
        for row in reader:
            query_id = str(row[query_key])
            doc_id = str(row[doc_key])
            grade = float(row[score_key])
            if grade > 0.0:
                qrels[query_id][doc_id] = grade
    return dict(qrels)


def document_text(title: str, text: str) -> str:
    title = title.strip()
    text = text.strip()
    if title and text:
        return f"{title} {text}"
    return title or text


def build_retriever(corpus: Sequence[dict[str, object]], index_path: Path) -> Retriever:
    """Materialize one Chunk per BEIR document and load a BM25+dense Retriever."""
    embedder = LocalHashEmbedder()
    texts = [document_text(str(row.get("title", "")), str(row.get("text", ""))) for row in corpus]
    embeddings = embedder.embed(texts)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("w", encoding="utf-8") as handle:
        for row, text, embedding in zip(corpus, texts, embeddings, strict=True):
            doc_id = str(row["_id"])
            payload = {
                "id": doc_id,
                "doc_id": doc_id,
                "title": str(row.get("title", "")),
                "heading_path": [],
                "url": "",
                "text": text,
                "identifiers": [],
                "embedding": embedding,
            }
            handle.write(json.dumps(payload) + "\n")

    settings = Settings(
        index_path=index_path,
        lexical_scorer="bm25",
        bm25_k1=1.2,
        bm25_b=0.75,
        rerank_pool=CANDIDATE_POOL,
        context_max_per_doc=10_000,
    )
    return Retriever(index_path, settings)


def rank_lane(retriever: Retriever, embedder: LocalHashEmbedder, query: str, lane: str) -> list[str]:
    """Return ranked document ids for one lane using the repo retrieval primitives."""
    if lane == "bm25":
        ranked = retriever._lexical_rank(query, CANDIDATE_POOL)
    elif lane == "dense":
        query_embedding = embedder.embed([query])[0]
        ranked = retriever._dense_rank(query_embedding, CANDIDATE_POOL)
    elif lane == "hybrid":
        query_embedding = embedder.embed([query])[0]
        dense_ranked = retriever._dense_rank(query_embedding, CANDIDATE_POOL)
        lexical_ranked = retriever._lexical_rank(query, CANDIDATE_POOL)
        ranked = reciprocal_rank_fusion(
            [dense_ranked, lexical_ranked],
            limit=CANDIDATE_POOL,
            constant=RRF_CONSTANT,
        )
    else:
        raise ValueError(lane)
    return [item.chunk.doc_id for item in ranked]


def mean_or_zero(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def evaluate_lanes(
    retriever: Retriever,
    queries: Sequence[dict[str, object]],
    qrels: dict[str, dict[str, float]],
    lanes: Sequence[str],
    limit: int | None,
) -> dict[str, dict[str, float]]:
    embedder = LocalHashEmbedder()
    selected_queries = [row for row in queries if str(row["_id"]) in qrels]
    selected_queries.sort(key=lambda row: str(row["_id"]))
    if limit is not None:
        selected_queries = selected_queries[:limit]

    metrics: dict[str, dict[str, list[float]]] = {
        lane: {"ndcg@10": [], "recall@10": [], "mrr@10": []} for lane in lanes
    }

    for index, row in enumerate(selected_queries, start=1):
        query_id = str(row["_id"])
        query_text = str(row["text"])
        labels = qrels[query_id]
        for lane in lanes:
            ranked_ids = rank_lane(retriever, embedder, query_text, lane)
            metrics[lane]["ndcg@10"].append(ndcg_at_k(ranked_ids, labels, METRIC_K))
            metrics[lane]["recall@10"].append(recall_at_k(ranked_ids, labels, METRIC_K))
            metrics[lane]["mrr@10"].append(mrr_at_k(ranked_ids, labels, METRIC_K))
        if index % 50 == 0 or index == len(selected_queries):
            print(f"  scored {index}/{len(selected_queries)} queries")

    return {
        lane: {
            "nDCG@10": mean_or_zero(values["ndcg@10"]),
            "Recall@10": mean_or_zero(values["recall@10"]),
            "MRR@10": mean_or_zero(values["mrr@10"]),
            "n_queries": float(len(values["ndcg@10"])),
        }
        for lane, values in metrics.items()
    }


def format_lane_label(lane: str) -> str:
    return {"bm25": "bm25_only", "dense": "dense_only", "hybrid": "hybrid_rrf"}[lane]


def main() -> None:
    args = parse_args()
    lanes = [normalize_lane(part) for part in args.lanes.split(",") if part.strip()]
    if not lanes:
        raise SystemExit("At least one lane is required")

    dataset_dir = ensure_dataset(args.dataset, args.cache_dir)
    corpus = read_jsonl(dataset_dir / "corpus.jsonl")
    queries = read_jsonl(dataset_dir / "queries.jsonl")
    qrels = load_qrels(dataset_dir / "qrels" / "test.tsv")

    index_path = args.cache_dir / args.dataset / "citespine_index.jsonl"
    print(f"Building index for {len(corpus)} documents -> {index_path}")
    retriever = build_retriever(corpus, index_path)
    print(f"Evaluating lanes={','.join(lanes)} on {args.dataset}")
    results = evaluate_lanes(retriever, queries, qrels, lanes, args.limit)

    print()
    print(f"{'lane':<12} {'nDCG@10':>10} {'Recall@10':>10} {'MRR@10':>10}")
    for lane in lanes:
        row = results[lane]
        label = format_lane_label(lane)
        print(f"{label:<12} {row['nDCG@10']:10.4f} {row['Recall@10']:10.4f} {row['MRR@10']:10.4f}")

    if args.json is not None:
        payload = {
            "dataset": args.dataset,
            "candidate_pool": CANDIDATE_POOL,
            "rrf_constant": RRF_CONSTANT,
            "lanes": {format_lane_label(lane): results[lane] for lane in lanes},
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.json}")

    if args.threshold is not None:
        below = [format_lane_label(lane) for lane in lanes if results[lane]["nDCG@10"] < args.threshold]
        if below:
            print(f"nDCG@10 below threshold {args.threshold}: {', '.join(below)}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
