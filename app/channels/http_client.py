"""An ``AskFn`` backed by a remote core over HTTP.

Proof that in-process and out-of-process adapters are the same architecture:
an adapter written against ``AskFn`` works unchanged whether the pipeline
runs in this process or behind ``POST /v1/ask`` on another host. Adapters in
other languages do exactly what this file does, minus the Python.
"""

from __future__ import annotations

import httpx

from app.api_models import AskFn, AskRequest, AskResponse


def remote_ask(base_url: str, token: str = "", timeout: float = 60.0) -> AskFn:
    """Build an ``AskFn`` that calls a citespine service at ``base_url``.

    ``token`` is the optional static bearer configured with ``API_AUTH_TOKEN``
    on the target service.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def ask(payload: AskRequest) -> AskResponse:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout, headers=headers) as client:
            response = await client.post("/v1/ask", json=payload.model_dump())
            response.raise_for_status()
            return AskResponse.model_validate(response.json())

    return ask
