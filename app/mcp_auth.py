"""OAuth 2.1 resource-server helpers for the MCP endpoint.

The MCP server acts as a protected resource, not an authorization server.
Operators point it at any standards-compliant identity provider: the server
publishes RFC 9728 protected-resource metadata so clients can discover the
authorization server, and validates incoming bearer JWTs against the issuer's
JWKS. Everything here fails closed: any parse, fetch, or verification error
rejects the request.

This module has no dependency on the MCP SDK so it stays importable and
testable with the base requirements only. JWT validation lazy-imports PyJWT.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

WELL_KNOWN_PATH = "/.well-known/oauth-protected-resource"


def build_protected_resource_metadata(
    resource_url: str,
    issuer: str,
    scopes: list[str] | None = None,
) -> dict[str, object]:
    """RFC 9728 protected-resource metadata document."""
    return {
        "resource": resource_url,
        "authorization_servers": [issuer],
        "scopes_supported": scopes or [],
        "bearer_methods_supported": ["header"],
    }


def build_www_authenticate(resource_metadata_url: str) -> str:
    """``WWW-Authenticate`` value for 401 responses, pointing clients at the
    metadata document per the MCP authorization spec."""
    return (
        f'Bearer resource_metadata="{resource_metadata_url}", '
        'error="invalid_token", error_description="Missing or invalid bearer token"'
    )


class BearerVerifier(Protocol):
    def verify(self, token: str | None) -> bool: ...


@dataclass
class TokenVerifier:
    """Validates bearer tokens for the MCP endpoint.

    ``mode="off"`` accepts everything and is meant for the local offline demo
    only. ``mode="jwt"`` validates an RS256 JWT: signature against the
    issuer's JWKS, ``iss``, ``aud``, and ``exp``.

    A JWKS dict can be injected via ``jwks`` (used by tests and by operators
    who pin keys); otherwise it is fetched from ``jwks_url``, or discovered
    from ``<issuer>/.well-known/openid-configuration``, and cached for
    ``jwks_cache_seconds``.
    """

    mode: str = "off"
    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    jwks: dict[str, object] | None = None
    jwks_cache_seconds: int = 300
    clock: Callable[[], float] = time.time
    _jwks_fetched_at: float = field(default=0.0, repr=False)

    def verify(self, token: str | None) -> bool:
        if self.mode == "off":
            return True
        if self.mode != "jwt":
            return False
        if not token or not self.issuer or not self.audience:
            return False
        try:
            import jwt
            from jwt.algorithms import RSAAlgorithm
        except ImportError as error:  # pragma: no cover - depends on env
            raise RuntimeError(
                "MCP_AUTH_MODE=jwt requires PyJWT with crypto support. "
                "Install it with: pip install -r requirements-mcp.txt"
            ) from error
        try:
            header = jwt.get_unverified_header(token)
            key_data = self._find_key(header.get("kid"))
            if key_data is None:
                return False
            public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
            jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
            return True
        except Exception:
            return False

    def _find_key(self, kid: str | None) -> dict[str, object] | None:
        jwks = self._current_jwks()
        keys = jwks.get("keys", []) if isinstance(jwks, dict) else []
        for key in keys:
            if not isinstance(key, dict):
                continue
            if kid is None or key.get("kid") == kid:
                return key
        return None

    def _current_jwks(self) -> dict[str, object]:
        now = self.clock()
        if self.jwks is not None and (
            self._jwks_fetched_at == 0.0
            or now - self._jwks_fetched_at < self.jwks_cache_seconds
        ):
            return self.jwks
        self.jwks = self._fetch_jwks()
        self._jwks_fetched_at = now
        return self.jwks

    def _fetch_jwks(self) -> dict[str, object]:
        import httpx

        url = self.jwks_url
        if not url:
            discovery = f"{self.issuer.rstrip('/')}/.well-known/openid-configuration"
            response = httpx.get(discovery, timeout=10.0)
            response.raise_for_status()
            url = str(response.json().get("jwks_uri", ""))
            if not url:
                raise RuntimeError(f"No jwks_uri in discovery document at {discovery}")
        response = httpx.get(url, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"JWKS at {url} is not a JSON object")
        return data


class OAuthResourceMiddleware:
    """Pure-ASGI middleware enforcing bearer auth on an MCP app.

    Serves the RFC 9728 metadata document at ``/.well-known/oauth-protected-resource``
    and, when the verifier is not in ``off`` mode, rejects requests without a
    valid bearer token using a 401 plus a ``WWW-Authenticate`` challenge.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        verifier: BearerVerifier,
        resource_url: str,
        issuer: str,
    ) -> None:
        self.app = app
        self.verifier = verifier
        self.resource_url = resource_url
        self.issuer = issuer

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path") == WELL_KNOWN_PATH:
            body = json.dumps(
                build_protected_resource_metadata(self.resource_url, self.issuer)
            ).encode()
            await _send_response(send, 200, body, [(b"content-type", b"application/json")])
            return
        token = _extract_bearer(scope)
        if not self.verifier.verify(token):
            metadata_url = f"{self.resource_url.rstrip('/')}{WELL_KNOWN_PATH}"
            challenge = build_www_authenticate(metadata_url)
            body = json.dumps({"error": "invalid_token"}).encode()
            await _send_response(
                send,
                401,
                body,
                [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", challenge.encode()),
                ],
            )
            return
        await self.app(scope, receive, send)


def _extract_bearer(scope: dict) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            text = value.decode("latin-1")
            scheme, _, credentials = text.partition(" ")
            if scheme.lower() == "bearer" and credentials.strip():
                return credentials.strip()
    return None


async def _send_response(
    send: Callable,
    status: int,
    body: bytes,
    headers: list[tuple[bytes, bytes]],
) -> None:
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
