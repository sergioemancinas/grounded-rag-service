"""MCP server exposing the pipeline to Model Context Protocol clients.

Three tools cover the useful shapes: ``search`` returns ranked chunks,
``fetch`` returns one chunk in full by id (the pair that makes this a valid
connector for clients that expect search-then-fetch), and ``ask`` runs the
whole grounded pipeline and returns a cited answer.

The server runs either standalone (``python -m app.mcp_server``) or mounted
at ``/mcp`` inside the core service, sharing one set of dependencies. Adding
your own tool takes one module and one env var; see MCP_EXTENSIONS_MODULE
below and docs/mcp.md.

The ``mcp`` SDK is an optional dependency (requirements-mcp.txt) and is
imported lazily, so the core service and the offline test suite never need
it installed.
"""

from __future__ import annotations

import importlib
import logging
from contextlib import AbstractAsyncContextManager
from typing import Any, Callable

from app.config import Settings
from app.mcp_auth import (
    OAuthResourceMiddleware,
    TokenVerifier,
    build_protected_resource_metadata,
    canonical_resource_url,
    well_known_paths,
)
from app.pipeline import PipelineDeps, answer_question

logger = logging.getLogger("citespine.mcp")

DEFAULT_SEARCH_DESCRIPTION = "Search the documentation corpus and return ranked matching chunks."
DEFAULT_FETCH_DESCRIPTION = "Fetch one documentation chunk in full by its id, as returned by search."
DEFAULT_ASK_DESCRIPTION = "Answer a question from the documentation corpus with inline citations."


def resource_url(settings: Settings) -> str:
    """The canonical resource identifier for this MCP server."""
    return canonical_resource_url(settings.mcp_resource_url, settings.mcp_port)


def build_verifier(settings: Settings) -> TokenVerifier:
    """Token verifier configured for the canonical resource URL."""
    return TokenVerifier(
        mode=settings.mcp_auth_mode,
        issuer=settings.mcp_auth_issuer,
        audience=settings.mcp_auth_audience or resource_url(settings),
        jwks_url=settings.mcp_auth_jwks_url,
        algorithms=tuple(part.strip() for part in settings.mcp_auth_algorithms.split(",") if part.strip()),
    )


def build_default_deps(settings: Settings) -> PipelineDeps:
    """Build a standalone dependency set (used by the standalone server)."""
    from app.deps import build_deps

    return build_deps(settings)


def _import_mcp_server_class() -> Any:
    """Import the SDK's server class, supporting both mcp 1.x and 2.x."""
    try:
        from mcp.server.mcpserver import MCPServer  # mcp >= 2.0

        return MCPServer
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x

        return FastMCP
    except ImportError as error:
        raise RuntimeError(
            "The MCP server requires the 'mcp' package. "
            "Install it with: pip install -r requirements-mcp.txt"
        ) from error


def build_mcp_server(settings: Settings, deps_provider: Callable[[], PipelineDeps]) -> Any:
    """Create the MCP server and register the built-in and custom tools.

    ``deps_provider`` is called per request rather than captured, so a
    mounted server picks up the dependencies built in the host's lifespan.
    """
    server_class = _import_mcp_server_class()
    server = server_class("citespine")

    @server.tool(description=settings.mcp_tool_search_description or DEFAULT_SEARCH_DESCRIPTION)
    def search(query: str, k: int = 5) -> list[dict[str, object]]:
        """Hybrid retrieval over the corpus; no generation."""
        deps = deps_provider()
        top_k = max(k, 1)
        embedding = deps.embedder.embed([query])[0]
        results = deps.retriever.retrieve(query, embedding, top_k)
        return [
            {
                "id": scored.chunk.id,
                "title": scored.chunk.title,
                "heading_path": scored.chunk.heading_path,
                "url": scored.chunk.url,
                "snippet": scored.chunk.text[: settings.max_context_chars_per_chunk],
                "score": scored.score,
            }
            for scored in results[:top_k]
        ]

    @server.tool(
        name="search_docs",
        description=settings.mcp_tool_search_description or DEFAULT_SEARCH_DESCRIPTION,
    )
    def search_docs(query: str, k: int = 5) -> list[dict[str, object]]:
        """Deprecated alias for ``search``, kept for existing clients."""
        return search(query, k)

    @server.tool(description=settings.mcp_tool_fetch_description or DEFAULT_FETCH_DESCRIPTION)
    def fetch(id: str) -> dict[str, object]:
        """Return one chunk in full, by the id that ``search`` returned."""
        deps = deps_provider()
        for chunk in getattr(deps.retriever, "chunks", []):
            if chunk.id == id:
                return {
                    "id": chunk.id,
                    "title": chunk.title,
                    "heading_path": chunk.heading_path,
                    "url": chunk.url,
                    "text": chunk.text,
                }
        raise ValueError(f"No chunk with id {id!r}")

    @server.tool(description=settings.mcp_tool_ask_description or DEFAULT_ASK_DESCRIPTION)
    def ask(question: str) -> dict[str, object]:
        """Run the full grounded pipeline and return a cited answer."""
        deps = deps_provider()
        result = answer_question(question, history=[], settings=settings, deps=deps)
        return {
            "answer": result.answer,
            "citations": result.citations,
            "grounding_score": result.grounding.score if result.grounding else None,
        }

    _register_extensions(server, settings, deps_provider)
    return server


def _register_extensions(server: Any, settings: Settings, deps_provider: Callable[[], PipelineDeps]) -> None:
    """Load custom tools from ``MCP_EXTENSIONS_MODULE``, if configured.

    The named module must expose ``register(server, deps_provider, settings)``
    and register its tools with the SDK's ``@server.tool()`` decorator. This
    is deliberately one explicit import rather than a plugin scan: what runs
    is exactly what the operator named.
    """
    module_name = settings.mcp_extensions_module.strip()
    if not module_name:
        return
    module = importlib.import_module(module_name)
    register = getattr(module, "register", None)
    if register is None:
        raise RuntimeError(f"MCP_EXTENSIONS_MODULE={module_name!r} has no register(server, deps_provider, settings)")
    register(server, deps_provider, settings)
    logger.info("mcp extensions: loaded %s", module_name)


def build_http_app(settings: Settings | None = None, deps: PipelineDeps | None = None) -> Any:
    """ASGI app for the standalone server: MCP transport plus auth wrapper."""
    settings = settings or Settings()
    resolved = deps or build_default_deps(settings)
    server = build_mcp_server(settings, lambda: resolved)
    transport = server.streamable_http_app()
    return OAuthResourceMiddleware(
        transport,
        verifier=build_verifier(settings),
        resource_url=resource_url(settings),
        issuer=settings.mcp_auth_issuer,
        required_scopes=settings.mcp_scopes,
    )


class _ExactMountPath:
    """Serve the mounted transport at ``/mcp`` as well as ``/mcp/``.

    Starlette hands a mounted app an empty remaining path for a request to
    the mount point itself, which the transport's own router answers with a
    307 to the trailing-slash form. MCP clients configured with a bare
    ``/mcp`` URL that do not follow redirects would fail, so the empty path
    is normalized here instead.
    """

    def __init__(self, app: Callable[..., Any]) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") == "http" and scope.get("path", "") == "":
            scope = {**scope, "path": "/", "raw_path": b"/"}
        await self.app(scope, receive, send)


def mount(
    application: Any,
    settings: Settings,
    deps_provider: Callable[[], PipelineDeps],
) -> Callable[[], AbstractAsyncContextManager[None]] | None:
    """Mount the MCP transport at ``/mcp`` on a host FastAPI application.

    Returns a factory for the SDK session-manager context, which the host
    lifespan must enter, or None when the optional ``mcp`` package is not
    installed. The RFC 9728 metadata documents are registered on the host
    itself, because they must live at the host's root, not under ``/mcp``.
    """
    try:
        _import_mcp_server_class()
    except RuntimeError:
        logger.info("mcp server: not mounted, the optional 'mcp' package is not installed")
        return None

    from starlette.responses import JSONResponse

    canonical = resource_url(settings)
    server = build_mcp_server(settings, deps_provider)
    transport = server.streamable_http_app(streamable_http_path="/")
    guarded = OAuthResourceMiddleware(
        transport,
        verifier=build_verifier(settings),
        resource_url=canonical,
        issuer=settings.mcp_auth_issuer,
        required_scopes=settings.mcp_scopes,
        serve_metadata=False,
    )
    application.mount("/mcp", _ExactMountPath(guarded))

    if settings.mcp_auth_mode == "jwt":
        metadata = build_protected_resource_metadata(
            canonical, settings.mcp_auth_issuer, sorted(settings.mcp_scopes)
        )
        for path in well_known_paths(canonical):
            application.add_route(
                path,
                lambda request, payload=metadata: JSONResponse(payload),
                methods=["GET"],
            )
    logger.info("mcp server: mounted at /mcp (resource %s)", canonical)
    return server.session_manager.run


def main() -> None:
    """Run the standalone MCP server."""
    import uvicorn

    settings = Settings()
    uvicorn.run(build_http_app(settings), host="0.0.0.0", port=settings.mcp_port)


if __name__ == "__main__":
    main()
