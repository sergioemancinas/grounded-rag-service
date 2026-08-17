from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cache import SemanticCache
from app.config import Settings
from app.pipeline import PipelineDeps, answer_question
from app.providers import get_embedder, get_generator
from app.rerank import get_reranker
from app.resilience import CircuitBreaker
from app.retrieval import Retriever


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an offline RAG smoke query.")
    parser.add_argument("question")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings(embedding_provider="local", generation_provider="local")
    retriever = Retriever(settings.index_path, settings)
    deps = PipelineDeps(
        embedder=get_embedder(settings),
        generator=get_generator(settings),
        retriever=retriever,
        reranker=get_reranker(settings),
        cache=SemanticCache(
            enabled=settings.cache_enabled,
            similarity_threshold=settings.cache_similarity,
            ttl_seconds=settings.cache_ttl_seconds,
        ),
        breaker=CircuitBreaker(),
    )
    result = answer_question(args.question, history=[], settings=settings, deps=deps)
    print("Answer")
    print("------")
    print(result.answer)
    print()
    print("Citations")
    print("---------")
    for citation in result.citations:
        print(f"[{citation['number']}] {citation['title']} ({citation['doc_id']}) {citation['url']}")
    print()
    print("Timings")
    print("-------")
    for stage, seconds in result.timings.items():
        print(f"{stage}: {seconds:.4f}s")


if __name__ == "__main__":
    main()
