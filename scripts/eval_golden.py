from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.deps import build_deps
from app.pipeline import answer_question


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Offline smoke test against the bundled golden questions. "
            "Not a retrieval benchmark: the sample corpus is tiny and every "
            "question can retrieve chunks from every document. For public "
            "nDCG numbers see scripts/eval_beir.py and docs/EVALUATION.md."
        )
    )
    parser.add_argument("--golden", type=Path, default=Path("data/golden_questions.example.jsonl"))
    parser.add_argument("--threshold", type=float, default=0.85)
    return parser.parse_args()


def read_golden(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main() -> None:
    args = parse_args()
    settings = Settings(embedding_provider="local", generation_provider="local", cache_enabled=False)
    deps = build_deps(settings)
    rows = read_golden(args.golden)
    hit_count = 0
    contains_count = 0
    grounding_scores: list[float] = []
    latencies: list[float] = []

    for row in rows:
        started = time.perf_counter()
        result = answer_question(str(row["question"]), history=[], settings=settings, deps=deps)
        latency = time.perf_counter() - started
        latencies.append(latency)
        expected_doc = str(row["must_cite_doc"])
        retrieved_docs = {chunk.chunk.doc_id for chunk in result.chunks}
        if expected_doc in retrieved_docs:
            hit_count += 1
        required = [str(item).lower() for item in row.get("must_contain", [])]
        answer_lower = result.answer.lower()
        if all(item in answer_lower for item in required):
            contains_count += 1
        if result.grounding is not None:
            grounding_scores.append(result.grounding.score)
        print(f"- {row['question']}")
        print(f"  expected_doc={expected_doc} retrieved={sorted(retrieved_docs)} latency={latency:.4f}s")

    total = len(rows) or 1
    hit_rate = hit_count / total
    contains_rate = contains_count / total
    mean_grounding = statistics.mean(grounding_scores) if grounding_scores else 0.0
    p95_latency = sorted(latencies)[int(0.95 * (len(latencies) - 1))] if latencies else 0.0

    print()
    print(f"retrieval_hit_rate: {hit_rate:.2%}")
    print(f"substring_pass_rate: {contains_rate:.2%}")
    print(f"mean_grounding_score: {mean_grounding:.3f}")
    print(f"latency_mean_seconds: {statistics.mean(latencies) if latencies else 0.0:.4f}")
    print(f"latency_p95_seconds: {p95_latency:.4f}")

    if hit_rate < args.threshold:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
