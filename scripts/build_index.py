"""Thin CLI over app/ingest.py: build the JSONL retrieval index.

Defaults to the bundled MarkdownSource over data/sample_docs. Any custom
ingestion source can be plugged in with ``--source module:ClassName`` (a
Source class or zero-argument factory; see the Source Protocol in
app/ingest.py).
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.ingest import MarkdownSource, Source, build_records
from app.interfaces import _check


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a JSONL retrieval index from an ingestion source.")
    parser.add_argument(
        "--docs",
        type=Path,
        default=Path("data/sample_docs"),
        help="Markdown directory for the default source.",
    )
    parser.add_argument("--out", type=Path, default=Path("data/index.jsonl"))
    parser.add_argument(
        "--source",
        default="",
        help="Dotted path (module:ClassName) of a Source class or zero-argument factory.",
    )
    return parser.parse_args()


def load_source(spec: str, docs_dir: Path) -> Source:
    """Instantiate the requested Source; default is MarkdownSource over docs_dir."""
    if not spec:
        return MarkdownSource(docs_dir)
    module_name, separator, attribute = spec.partition(":")
    if not separator:
        module_name, _, attribute = spec.rpartition(".")
    factory = getattr(importlib.import_module(module_name), attribute)
    source = factory()
    _check(source, "load")
    return source


def main() -> None:
    args = parse_args()
    settings = Settings(index_path=args.out)
    source = load_source(args.source, args.docs)
    records = build_records(source, settings)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    print(f"Wrote {len(records)} chunks to {args.out}")


if __name__ == "__main__":
    main()
