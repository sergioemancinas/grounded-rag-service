from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.deps import build_deps
from app.pipeline import answer_question


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an offline RAG smoke query.")
    parser.add_argument("question")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings(embedding_provider="local", generation_provider="local")
    deps = build_deps(settings)
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
