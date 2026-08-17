"""MCP server exposing the RAG pipeline to Model Context Protocol clients.

Runs as a standalone streamable-HTTP server (``python -m app.mcp_server``)
next to the Slack app, sharing the same retrieval and generation wiring. Two
tools are exposed: ``search_docs`` (retrieval only) and ``ask`` (the full
grounded pipeline).

Authorization is optional and standards-based: with ``MCP_AUTH_MODE=jwt`` the
server acts as an OAuth 2.1 protected resource for any compliant identity
provider (see app/mcp_auth.py and the README). The default ``off`` mode is for
the local offline demo only.

The ``mcp`` SDK is an optional dependency (requirements-mcp.txt); it is
imported lazily so the rest of the project works without it.
"""

from __future__ import annotations

from app.cache import SemanticCache
from app.config import Settings
from app.mcp_auth import OAuthResourceMiddleware, TokenVerifier
from app.pipeline import PipelineDeps, answer_question
from app.providers import get_embedder, get_generator
from app.rerank import get_reranker
from app.resilience import CircuitBreaker
from app.retrieval import Retriever


def build_default_deps(settings: Settings) -> PipelineDeps:
    """Same wiring as scripts/smoke_query.py, reused for MCP tool calls."""
    return PipelineDeps(
        embedder=get_embedder(settings),
        generator=get_generator(settings),
        retriever=Retriever(settings.index_path, settings),
        reranker=get_reranker(settings),
        cache=SemanticCache(
            enabled=settings.cache_enabled,
            similarity_threshold=settings.cache_similarity,
            ttl_seconds=settings.cache_ttl_seconds,
        ),
        breaker=CircuitBreaker(),
    )


def build_verifier(settings: Settings) -> TokenVerifier:
    return TokenVerifier(
        mode=settings.mcp_auth_mode,
        issuer=settings.mcp_auth_issuer,
        audience=settings.mcp_auth_audience,
        jwks_url=settings.mcp_auth_jwks_url,
    )


def build_http_app(settings: Settings | None = None, deps: PipelineDeps | None = None):
    """ASGI app: FastMCP streamable HTTP wrapped in the OAuth middleware."""
    try:
        from mcp.server.mcpserver import MCPServer  # mcp >= 2.0
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as MCPServer  # mcp 1.x
        except ImportError as error:
            raise RuntimeError(
                "The MCP server requires the 'mcp' package. "
                "Install it with: pip install -r requirements-mcp.txt"
            ) from error

    settings = settings or Settings()
    deps = deps or build_default_deps(settings)
    server = MCPServer("slack-rag-skeleton")

    @server.tool()
    def search_docs(query: str, k: int = 5) -> list[dict[str, object]]:
        """Hybrid retrieval over the documentation index, no generation."""
        embedding = deps.embedder.embed([query])[0]
        results = deps.retriever.retrieve(query, embedding, max(k, 1))
        return [
            {
                "title": scored.chunk.title,
                "heading_path": scored.chunk.heading_path,
                "url": scored.chunk.url,
                "snippet": scored.chunk.text[: settings.max_context_chars_per_chunk],
                "score": scored.score,
            }
            for scored in results[: max(k, 1)]
        ]

    @server.tool()
    def ask(question: str) -> dict[str, object]:
        """Run the full grounded pipeline and return a cited answer."""
        result = answer_question(question, history=[], settings=settings, deps=deps)
        return {
            "answer": result.answer,
            "citations": result.citations,
            "grounding_score": result.grounding.score if result.grounding else None,
        }

    app = server.streamable_http_app()
    resource_url = settings.mcp_resource_url or f"http://localhost:{settings.mcp_port}"
    return OAuthResourceMiddleware(
        app,
        verifier=build_verifier(settings),
        resource_url=resource_url,
        issuer=settings.mcp_auth_issuer,
    )


def main() -> None:
    import uvicorn

    settings = Settings()
    uvicorn.run(build_http_app(settings), host="0.0.0.0", port=settings.mcp_port)


if __name__ == "__main__":
    main()
