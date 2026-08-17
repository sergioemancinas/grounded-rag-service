"""Thin CLI over app/ingest.py: build the JSONL retrieval index.

Defaults to the bundled MarkdownSource over data/sample_docs. Any custom
ingestion source can be plugged in with ``--source module:ClassName`` (a
Source class or zero-argument factory; see the Source Protocol in
app/ingest.py).

After writing the index, a sidecar manifest records its sha256 and build
provenance so loaders can detect on-disk tampering (see app/retrieval.py).
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.deps import provider_names
from app.ingest import MarkdownSource, Source, build_records, source_identifier
from app.interfaces import _check
from app.retrieval import MANIFEST_SCHEMA_VERSION, file_sha256, index_manifest_path


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
    parser.add_argument(
        "--built-at",
        default="",
        help="ISO-8601 UTC build timestamp for the manifest; defaults to now.",
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


def write_manifest(
    index_path: Path,
    *,
    source_id: str,
    chunk_count: int,
    embedder: str,
    built_at: str,
) -> Path:
    """Write the integrity sidecar next to the index; returns the manifest path."""
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "built_at": built_at,
        "source": source_id,
        "chunk_count": chunk_count,
        "embedder": embedder,
        "index_sha256": file_sha256(index_path),
    }
    path = index_manifest_path(index_path)
    path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    args = parse_args()
    settings = Settings(index_path=args.out)
    source = load_source(args.source, args.docs)
    source_id = source_identifier(source)
    records = build_records(source, settings, source_id=source_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    built_at = args.built_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_path = write_manifest(
        args.out,
        source_id=source_id,
        chunk_count=len(records),
        embedder=provider_names(settings)["embedder"],
        built_at=built_at,
    )
    print(f"Wrote {len(records)} chunks to {args.out}")
    print(f"Wrote manifest to {manifest_path}")


if __name__ == "__main__":
    main()
