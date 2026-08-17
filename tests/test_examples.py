"""Every example must keep matching the protocol it claims to implement.

Examples are documentation that runs, so they rot silently unless CI reads
them. These tests import each one and duck-check its shape offline: no
network, no heavy optional dependencies, no credentials.
"""

from __future__ import annotations

import importlib

import pytest

EXAMPLE_MODULES = [
    "examples.custom_embedder_fastembed",
    "examples.custom_generator_anthropic",
    "examples.custom_reranker_crossencoder",
    "examples.custom_store_sqlite",
    "examples.custom_source_sitemap",
    "examples.adapter_cli",
    "examples.adapter_discord",
    "examples.mcp_tool_custom",
]

PROTOCOL_METHODS = [
    ("examples.custom_embedder_fastembed", "FastEmbedEmbedder", "embed"),
    ("examples.custom_generator_anthropic", "ClaudeGenerator", "generate"),
    ("examples.custom_reranker_crossencoder", "CrossEncoderReranker", "rerank"),
    ("examples.custom_store_sqlite", "SqliteRetriever", "retrieve"),
    ("examples.custom_source_sitemap", "SitemapSource", "load"),
]


@pytest.mark.parametrize("module_name", EXAMPLE_MODULES)
def test_example_imports_without_optional_dependencies(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None


@pytest.mark.parametrize(("module_name", "class_name", "method"), PROTOCOL_METHODS)
def test_example_class_satisfies_its_protocol(module_name: str, class_name: str, method: str) -> None:
    from app.interfaces import _check

    implementation = getattr(importlib.import_module(module_name), class_name)
    _check(implementation, method)


def test_adapters_expose_the_expected_seam() -> None:
    from app.channels.base import AskFn  # noqa: F401  (documents the seam under test)

    discord = importlib.import_module("examples.adapter_discord")
    cli = importlib.import_module("examples.adapter_cli")

    assert callable(discord.create_router)
    assert callable(cli.local_ask)
    assert callable(cli.render)


def test_mcp_extension_module_exposes_register() -> None:
    module = importlib.import_module("examples.mcp_tool_custom")

    assert callable(module.register)


def test_sqlite_store_round_trips_the_jsonl_index(tmp_path, fake_deps, monkeypatch: pytest.MonkeyPatch) -> None:
    """The one example with no extra dependencies is exercised end to end."""
    import json

    from examples.custom_store_sqlite import SqliteRetriever, convert

    jsonl = tmp_path / "index.jsonl"
    with jsonl.open("w", encoding="utf-8") as handle:
        for chunk in fake_deps.retriever.chunks:
            handle.write(
                json.dumps(
                    {
                        "id": chunk.id,
                        "doc_id": chunk.doc_id,
                        "title": chunk.title,
                        "heading_path": chunk.heading_path,
                        "url": chunk.url,
                        "text": chunk.text,
                        "identifiers": chunk.identifiers,
                        "embedding": chunk.embedding,
                    }
                )
                + "\n"
            )
    sqlite_path = tmp_path / "index.sqlite3"

    assert convert(jsonl, sqlite_path) == len(fake_deps.retriever.chunks)

    monkeypatch.setenv("SQLITE_INDEX_PATH", str(sqlite_path))
    retriever = SqliteRetriever()
    query_embedding = fake_deps.embedder.embed(["how do refunds work"])[0]
    results = retriever.retrieve("how do refunds work", query_embedding, 2)

    assert retriever.chunk_count == len(fake_deps.retriever.chunks)
    assert results and all(result.chunk.id for result in results)
