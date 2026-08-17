from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from app.mcp_auth import (
    WELL_KNOWN_PATH,
    OAuthResourceMiddleware,
    TokenVerifier,
    build_protected_resource_metadata,
    build_www_authenticate,
)

ISSUER = "https://idp.example.com/oauth2/default"
AUDIENCE = "api://slack-rag-skeleton"
RESOURCE = "https://rag.example.com"


def test_protected_resource_metadata_shape() -> None:
    metadata = build_protected_resource_metadata(RESOURCE, ISSUER, ["search"])

    assert metadata["resource"] == RESOURCE
    assert metadata["authorization_servers"] == [ISSUER]
    assert metadata["scopes_supported"] == ["search"]
    assert metadata["bearer_methods_supported"] == ["header"]


def test_www_authenticate_challenge() -> None:
    challenge = build_www_authenticate(f"{RESOURCE}{WELL_KNOWN_PATH}")

    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{RESOURCE}{WELL_KNOWN_PATH}"' in challenge


def test_mode_off_accepts_anything() -> None:
    verifier = TokenVerifier(mode="off")

    assert verifier.verify(None)
    assert verifier.verify("garbage")


def test_unknown_mode_rejects() -> None:
    verifier = TokenVerifier(mode="introspection", issuer=ISSUER, audience=AUDIENCE)

    assert not verifier.verify("anything")


def test_jwt_mode_rejects_garbage_token() -> None:
    pytest.importorskip("jwt")
    verifier = TokenVerifier(mode="jwt", issuer=ISSUER, audience=AUDIENCE, jwks={"keys": []})

    assert not verifier.verify("not-a-jwt")
    assert not verifier.verify(None)


def _rsa_jwks_and_signer() -> tuple[dict[str, object], object]:
    jwt = pytest.importorskip("jwt")
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = "test-key"
    return {"keys": [public_jwk]}, private_key


def _sign(private_key: object, claims: dict[str, object]) -> str:
    import jwt

    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


def test_jwt_mode_accepts_valid_token_and_rejects_bad_claims() -> None:
    jwks, private_key = _rsa_jwks_and_signer()
    verifier = TokenVerifier(mode="jwt", issuer=ISSUER, audience=AUDIENCE, jwks=jwks)
    base_claims = {"iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 300}

    assert verifier.verify(_sign(private_key, base_claims))
    assert not verifier.verify(_sign(private_key, {**base_claims, "aud": "api://other"}))
    assert not verifier.verify(_sign(private_key, {**base_claims, "iss": "https://evil.example.com"}))
    assert not verifier.verify(
        _sign(private_key, {**base_claims, "exp": int(time.time()) - 60})
    )


async def _dummy_app(scope: dict, receive, send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


class _RejectAll:
    def verify(self, token: str | None) -> bool:
        return False


def _request(app, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)

    async def _run() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url=RESOURCE) as client:
            return await client.get(path, headers=headers or {})

    return asyncio.run(_run())


def test_middleware_serves_metadata_and_challenges() -> None:
    app = OAuthResourceMiddleware(
        _dummy_app, verifier=_RejectAll(), resource_url=RESOURCE, issuer=ISSUER
    )

    metadata = _request(app, WELL_KNOWN_PATH)
    assert metadata.status_code == 200
    assert metadata.json()["authorization_servers"] == [ISSUER]

    denied = _request(app, "/mcp")
    assert denied.status_code == 401
    assert "resource_metadata" in denied.headers["www-authenticate"]


def test_middleware_passes_through_when_auth_off() -> None:
    app = OAuthResourceMiddleware(
        _dummy_app, verifier=TokenVerifier(mode="off"), resource_url=RESOURCE, issuer=""
    )

    response = _request(app, "/mcp")
    assert response.status_code == 200
    assert response.text == "ok"
