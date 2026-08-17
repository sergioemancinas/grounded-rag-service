"""The verified caller identity must reach the tool layer.

Found while validating the threat model against the code: the MCP `ask` tool
called the pipeline with no cache scope, so every authenticated caller shared
one cache partition. Harmless while every caller can read the whole corpus,
and a cross-user disclosure the moment that stops being true, because a cache
hit never reaches the retriever.
"""

from __future__ import annotations

import asyncio

import httpx

from app.mcp_auth import CURRENT_CLAIMS, OAuthResourceMiddleware, current_subject

RESOURCE = "https://rag.example.com/mcp"
ISSUER = "https://idp.example.com/oauth2/default"


class _StaticVerifier:
    def __init__(self, claims: dict | None) -> None:
        self.claims = claims

    def verify_token(self, token: str | None) -> dict | None:
        return self.claims


def test_subject_is_empty_without_a_token() -> None:
    assert current_subject() == ""


def test_claims_are_exposed_to_the_wrapped_app() -> None:
    """The middleware must publish claims for the duration of the request."""
    seen: dict[str, object] = {}

    async def inner(scope: dict, receive, send) -> None:
        seen["subject"] = current_subject()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = OAuthResourceMiddleware(
        inner,
        verifier=_StaticVerifier({"sub": "user-42", "scope": "read"}),
        resource_url=RESOURCE,
        issuer=ISSUER,
    )

    async def _run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://rag.example.com") as client:
            await client.get("/mcp")

    asyncio.run(_run())

    assert seen["subject"] == "user-42"


def test_claims_do_not_leak_between_requests() -> None:
    """A context variable left set would attribute the next call to the last caller."""
    subjects: list[str] = []

    async def inner(scope: dict, receive, send) -> None:
        subjects.append(current_subject())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    def app_for(subject: str) -> OAuthResourceMiddleware:
        return OAuthResourceMiddleware(
            inner,
            verifier=_StaticVerifier({"sub": subject}),
            resource_url=RESOURCE,
            issuer=ISSUER,
        )

    async def _run() -> None:
        for subject in ("alice", "bob"):
            transport = httpx.ASGITransport(app=app_for(subject))
            async with httpx.AsyncClient(transport=transport, base_url="https://rag.example.com") as client:
                await client.get("/mcp")

    asyncio.run(_run())

    assert subjects == ["alice", "bob"]
    assert current_subject() == ""  # reset after the last request


def test_claims_reach_a_synchronous_tool_running_in_a_worker_thread() -> None:
    """MCP tools are sync functions; the SDK may run them off the event loop.

    Context variables are copied into the worker thread by anyio, but that is
    the assumption the scoping depends on, so it is asserted rather than
    trusted.
    """
    import anyio

    async def _run() -> str:
        CURRENT_CLAIMS.set({"sub": "threaded-user"})
        return await anyio.to_thread.run_sync(current_subject)

    assert asyncio.run(_run()) == "threaded-user"
