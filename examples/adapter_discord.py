"""Channel adapter: Discord interactions webhook.

The same three rules as every adapter (see app/channels/base.py), with
Discord's specifics: signatures are Ed25519 rather than HMAC, the ping type
must be echoed, and the deferred-response type buys time for the pipeline.

Wire it up by adding one line to ``mount_channels`` in app/main.py:

    from examples import adapter_discord
    app.include_router(adapter_discord.create_router(ask, settings))

Then:

    pip install PyNaCl
    export DISCORD_PUBLIC_KEY=...        # from the Discord developer portal
    export DISCORD_APPLICATION_ID=...
    uvicorn app.main:app --port 8000

Deliberately not wired in by default: it is an example, and the core ships
with no adapters mounted.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api_models import AskRequest
from app.channels.base import AskFn
from app.config import Settings

PING = 1
APPLICATION_COMMAND = 2
DEFERRED_CHANNEL_MESSAGE = 5


def verify_signature(public_key: str, signature: str, timestamp: str, body: bytes) -> bool:
    """Verify Discord's Ed25519 request signature over timestamp + raw body."""
    if not public_key or not signature or not timestamp:
        return False
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey

        VerifyKey(bytes.fromhex(public_key)).verify(
            timestamp.encode() + body, bytes.fromhex(signature)
        )
        return True
    except (BadSignatureError, ValueError):
        return False


def create_router(ask: AskFn, settings: Settings | None = None) -> APIRouter:
    """Build the Discord interactions route bound to an AskFn."""
    router = APIRouter(tags=["discord"])
    public_key = os.environ.get("DISCORD_PUBLIC_KEY", "")

    @router.post("/discord/interactions")
    async def discord_interactions(
        request: Request,
        x_signature_ed25519: str = Header(default=""),
        x_signature_timestamp: str = Header(default=""),
    ) -> JSONResponse:
        """Handle a slash command; verify first, then answer."""
        body = await request.body()
        if not verify_signature(public_key, x_signature_ed25519, x_signature_timestamp, body):
            raise HTTPException(status_code=401, detail="invalid Discord signature")
        payload = json.loads(body.decode("utf-8") or "{}")
        if payload.get("type") == PING:
            return JSONResponse({"type": PING})
        if payload.get("type") != APPLICATION_COMMAND:
            return JSONResponse({"type": DEFERRED_CHANNEL_MESSAGE})

        options = payload.get("data", {}).get("options", [])
        question = next((str(option.get("value", "")) for option in options if option.get("name") == "question"), "")
        if not question:
            return JSONResponse({"type": DEFERRED_CHANNEL_MESSAGE})
        response = await ask(AskRequest(question=question, channel="discord"))
        # Real deployments defer here and PATCH the followup webhook, because
        # Discord allows only 3 seconds for the initial response.
        return JSONResponse({"type": 4, "data": {"content": response.answer[:2000]}})

    return router
