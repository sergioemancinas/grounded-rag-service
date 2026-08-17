"""Tests for index manifest integrity (LLM04 / STRIDE tampering)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.config import Settings
from app.ingest import MarkdownSource, build_records, source_identifier
from app.retrieval import (
    MANIFEST_SCHEMA_VERSION,
    Retriever,
    file_sha256,
    index_manifest_path,
    verify_index_integrity,
)
from scripts.build_index import write_manifest


def _write_tiny_index(tmp_path: Path) -> tuple[Path, Settings, str]:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "returns.md").write_text("# Returns\n\nRefunds within 30 days.\n", encoding="utf-8")
    index_path = tmp_path / "index.jsonl"
    settings = Settings(index_path=index_path, index_verify="off")
    source = MarkdownSource(docs)
    source_id = source_identifier(source)
    records = build_records(source, settings, source_id=source_id)
    with index_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return index_path, settings, source_id


def test_manifest_written_with_correct_digest(tmp_path: Path) -> None:
    index_path, _settings, source_id = _write_tiny_index(tmp_path)
    manifest_path = write_manifest(
        index_path,
        source_id=source_id,
        chunk_count=1,
        embedder="local",
        built_at="2026-08-17T12:00:00Z",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["built_at"] == "2026-08-17T12:00:00Z"
    assert manifest["source"] == source_id
    assert manifest["chunk_count"] == 1
    assert manifest["embedder"] == "local"
    assert manifest["index_sha256"] == file_sha256(index_path)
    assert manifest_path == index_manifest_path(index_path)

    # Provenance lands on each chunk record.
    record = json.loads(index_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["source"] == source_id
    assert record["source_url"]


def test_tampering_with_index_is_detected(tmp_path: Path) -> None:
    index_path, settings, source_id = _write_tiny_index(tmp_path)
    write_manifest(
        index_path,
        source_id=source_id,
        chunk_count=1,
        embedder="local",
        built_at="2026-08-17T12:00:00Z",
    )
    index_path.write_text(index_path.read_text(encoding="utf-8") + '{"id":"evil"}\n', encoding="utf-8")
    settings = settings.model_copy(update={"index_verify": "strict"})
    with pytest.raises(RuntimeError, match="digest mismatch"):
        verify_index_integrity(index_path, settings)


def test_strict_mode_raises(tmp_path: Path) -> None:
    index_path, settings, source_id = _write_tiny_index(tmp_path)
    write_manifest(
        index_path,
        source_id=source_id,
        chunk_count=1,
        embedder="local",
        built_at="2026-08-17T12:00:00Z",
    )
    index_path.write_bytes(index_path.read_bytes() + b"\n")
    with pytest.raises(RuntimeError, match="digest mismatch"):
        Retriever(index_path, settings.model_copy(update={"index_verify": "strict"}))


def test_warn_mode_logs_and_continues(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    index_path, settings, source_id = _write_tiny_index(tmp_path)
    write_manifest(
        index_path,
        source_id=source_id,
        chunk_count=1,
        embedder="local",
        built_at="2026-08-17T12:00:00Z",
    )
    index_path.write_bytes(index_path.read_bytes() + b"\n")
    with caplog.at_level(logging.WARNING, logger="citespine.retrieval"):
        retriever = Retriever(index_path, settings.model_copy(update={"index_verify": "warn"}))
    assert any("digest mismatch" in record.getMessage() for record in caplog.records)
    assert retriever.chunk_count >= 1


def test_absent_manifest_is_tolerated(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    index_path, settings, _source_id = _write_tiny_index(tmp_path)
    with caplog.at_level(logging.WARNING, logger="citespine.retrieval"):
        retriever = Retriever(index_path, settings.model_copy(update={"index_verify": "warn"}))
    assert retriever.chunk_count >= 1
    assert any("unverified" in record.getMessage() for record in caplog.records)
