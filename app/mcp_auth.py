"""OAuth 2.1 resource-server helpers for the MCP endpoint.

The MCP server is a protected resource, never an authorization server:
operators point it at any standards-compliant identity provider. It
publishes RFC 9728 protected-resource metadata so clients can discover that
provider, and validates incoming bearer JWTs against the provider's JWKS.
Every failure path rejects.

One rule governs this module, and getting it wrong is the classic way to
ship an MCP server that passes local tests and then rejects every real
token: **one canonical resource URL**, including the ``/mcp`` path. That
exact string is the ``resource`` field in the metadata document, the
``resource_metadata`` pointer in the ``WWW-Authenticate`` challenge, and the
``aud`` claim the identity provider must mint into tokens (RFC 8707).

The module deliberately depends on neither the MCP SDK nor PyJWT at import
time, so it stays importable and testable with the base requirements alone.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

WELL_KNOWN_PREFIX = "/.well-known/oauth-protected-resource"
WELL_KNOWN_PATH = WELL_KNOWN_PREFIX
"""Bare metadata path, kept as an alias for readability at call sites."""


def canonical_resource_url(resource_url: str, port: int = 8090, path: str = "/mcp") -> str:
    """Return the canonical resource identifier, always including the MCP path.

    Falls back to a localhost URL so the offline demo publishes something
    coherent without any configuration.
    """
    base = (resource_url or f"http://localhost:{port}").rstrip("/")
    if urlsplit(base).path in ("", "/"):
        return f"{base}{path}"
    return base


def well_known_paths(resource_url: str) -> list[str]:
    """Metadata paths to serve for ``resource_url``.

    RFC 9728 inserts the resource's path into the well-known URL, so a
    resource at ``https://host/mcp`` is described at
    ``https://host/.well-known/oauth-protected-resource/mcp``. Spec-compliant
    clients request that form; the bare path is served too because plenty of
    clients and humans try it first.
    """
    resource_path = urlsplit(resource_url).path.rstrip("/")
    paths = [WELL_KNOWN_PREFIX]
    if resource_path:
        paths.append(f"{WELL_KNOWN_PREFIX}{resource_path}")
    return paths


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


def build_www_authenticate(
    resource_metadata_url: str,
    error: str = "invalid_token",
    description: str = "Missing or invalid bearer token",
) -> str:
    """``WWW-Authenticate`` value pointing clients at the metadata document."""
    return f'Bearer resource_metadata="{resource_metadata_url}", error="{error}", error_description="{description}"'


class BearerVerifier(Protocol):
    def verify_token(self, token: str | None) -> dict | None: ...


@dataclass
class TokenVerifier:
    """Validates bearer tokens for the MCP endpoint.

    ``mode="off"`` accepts everything and exists for the local offline demo.
    ``mode="jwt"`` validates the token's signature against the issuer's JWKS
    plus ``iss``, ``aud``, and ``exp``, where ``audience`` must be the
    canonical resource URL.

    A JWKS dict can be injected via ``jwks`` (tests, or operators pinning
    keys); otherwise it is fetched from ``jwks_url``, or discovered from
    ``<issuer>/.well-known/openid-configuration``, and cached.
    """

    mode: str = "off"
    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    jwks: dict[str, object] | None = None
    jwks_cache_seconds: int = 300
    algorithms: tuple[str, ...] = ("RS256",)
    clock: Callable[[], float] = time.time
    _jwks_fetched_at: float = field(default=0.0, repr=False)

    def verify_token(self, token: str | None) -> dict | None:
        """Return the token's claims when valid, or None to reject it."""
        if self.mode == "off":
            return {}
        if self.mode != "jwt":
            return None
        if not token or not self.issuer or not self.audience or not self.algorithms:
            return None
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
            if header.get("alg") not in self.algorithms:
                return None
            key_data = self._find_key(header.get("kid"))
            if key_data is None:
                return None
            public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
            return dict(claims)
        except Exception:
            return None

    def verify(self, token: str | None) -> bool:
        """Boolean convenience wrapper around :meth:`verify_token`."""
        return self.verify_token(token) is not None

    def _find_key(self, kid: str | None) -> dict[str, object] | None:
        jwks = self._current_jwks()
        keys_raw = jwks.get("keys", []) if isinstance(jwks, dict) else []
        keys = keys_raw if isinstance(keys_raw, list) else []
        for key in keys:
            if not isinstance(key, dict):
                continue
            if kid is None or key.get("kid") == kid:
                return key
        return None

    def _current_jwks(self) -> dict[str, object]:
        now = self.clock()
        if self.jwks is not None and (
            self._jwks_fetched_at == 0.0 or now - self._jwks_fetched_at < self.jwks_cache_seconds
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


def token_scopes(claims: dict) -> set[str]:
    """Extract granted scopes from either the ``scope`` or ``scp`` claim."""
    raw = claims.get("scope", claims.get("scp", ""))
    if isinstance(raw, str):
        return {part for part in raw.split() if part}
    if isinstance(raw, (list, tuple)):
        return {str(part) for part in raw}
    return set()


class OAuthResourceMiddleware:
    """Pure-ASGI bearer enforcement plus metadata publication for an MCP app.

    Serves the RFC 9728 document on every well-known path for the canonical
    resource URL and, outside ``off`` mode, rejects requests that carry no
    valid token (401) or a valid token missing a required scope (403).
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        verifier: BearerVerifier,
        resource_url: str,
        issuer: str,
        required_scopes: set[str] | None = None,
        serve_metadata: bool = True,
    ) -> None:
        self.app = app
        self.verifier = verifier
        self.resource_url = resource_url
        self.issuer = issuer
        self.required_scopes = required_scopes or set()
        # When the transport is mounted under a host application, the
        # well-known documents must live at that host's root, so the host
        # serves them and this middleware only enforces the bearer.
        self.metadata_paths = set(well_known_paths(resource_url)) if serve_metadata else set()

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in self.metadata_paths:
            body = json.dumps(
                build_protected_resource_metadata(self.resource_url, self.issuer, sorted(self.required_scopes))
            ).encode()
            await _send_response(send, 200, body, [(b"content-type", b"application/json")])
            return
        claims = self.verifier.verify_token(_extract_bearer(scope))
        if claims is None:
            await self._challenge(send, 401, "invalid_token", "Missing or invalid bearer token")
            return
        if self.required_scopes and not self.required_scopes.issubset(token_scopes(claims)):
            await self._challenge(
                send,
                403,
                "insufficient_scope",
                f"Token is missing required scopes: {' '.join(sorted(self.required_scopes))}",
            )
            return
        await self.app(scope, receive, send)

    async def _challenge(self, send: Callable, status: int, error: str, description: str) -> None:
        metadata_url = f"{self.resource_url.rstrip('/')}"
        parts = urlsplit(metadata_url)
        challenge_url = f"{parts.scheme}://{parts.netloc}{WELL_KNOWN_PREFIX}{parts.path.rstrip('/')}"
        body = json.dumps({"error": error, "error_description": description}).encode()
        await _send_response(
            send,
            status,
            body,
            [
                (b"content-type", b"application/json"),
                (b"www-authenticate", build_www_authenticate(challenge_url, error, description).encode()),
            ],
        )


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
