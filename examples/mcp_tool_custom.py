"""Custom MCP tools, loaded without touching the repo.

Any module exposing ``register(server, deps_provider, settings)`` can add
tools to the MCP server. Type hints on your function become the tool's input
schema, and the docstring becomes its description.

Run it:

    pip install -r requirements-mcp.txt
    export MCP_EXTENSIONS_MODULE=examples.mcp_tool_custom
    python -m app.mcp_server

Your tools then appear in both the standalone server and the one mounted at
/mcp inside the core service.
"""

from __future__ import annotations

from typing import Any, Callable

from app.config import Settings
from app.pipeline import PipelineDeps


def register(server: Any, deps_provider: Callable[[], PipelineDeps], settings: Settings) -> None:
    """Register this module's tools with the MCP server."""

    @server.tool(description="List every documentation page in the index with its chunk count.")
    def list_documents() -> list[dict[str, object]]:
        """Inventory of indexed documents, useful for corpus spot checks."""
        counts: dict[str, dict[str, object]] = {}
        for chunk in getattr(deps_provider().retriever, "chunks", []):
            entry = counts.setdefault(chunk.doc_id, {"doc_id": chunk.doc_id, "title": chunk.title, "chunks": 0})
            entry["chunks"] = int(entry["chunks"]) + 1
        return sorted(counts.values(), key=lambda item: str(item["doc_id"]))

    @server.tool(description="Find documentation chunks that mention an exact identifier, such as an API path.")
    def find_identifier(identifier: str) -> list[dict[str, object]]:
        """Exact-match lookup that skips ranking entirely."""
        needle = identifier.lower()
        return [
            {"id": chunk.id, "title": chunk.title, "url": chunk.url, "heading_path": chunk.heading_path}
            for chunk in getattr(deps_provider().retriever, "chunks", [])
            if needle in chunk.text.lower()
        ][:20]
