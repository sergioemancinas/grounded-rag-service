from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from app.mcp_auth import (
    WELL_KNOWN_PREFIX,
    OAuthResourceMiddleware,
    TokenVerifier,
    build_protected_resource_metadata,
    build_www_authenticate,
    canonical_resource_url,
    token_scopes,
    well_known_paths,
)

ISSUER = "https://idp.example.com/oauth2/default"
HOST = "https://rag.example.com"
RESOURCE = f"{HOST}/mcp"


def test_protected_resource_metadata_shape() -> None:
    metadata = build_protected_resource_metadata(RESOURCE, ISSUER, ["search"])

    assert metadata["resource"] == RESOURCE
    assert metadata["authorization_servers"] == [ISSUER]
    assert metadata["scopes_supported"] == ["search"]
    assert metadata["bearer_methods_supported"] == ["header"]


def test_canonical_resource_url_always_includes_mcp_path() -> None:
    assert canonical_resource_url(HOST) == RESOURCE
    assert canonical_resource_url(f"{HOST}/") == RESOURCE
    assert canonical_resource_url(RESOURCE) == RESOURCE
    assert canonical_resource_url("", port=9000) == "http://localhost:9000/mcp"


def test_well_known_paths_include_path_inserted_variant() -> None:
    paths = well_known_paths(RESOURCE)

    assert WELL_KNOWN_PREFIX in paths
    assert f"{WELL_KNOWN_PREFIX}/mcp" in paths


def test_www_authenticate_challenge() -> None:
    challenge = build_www_authenticate(f"{HOST}{WELL_KNOWN_PREFIX}/mcp")

    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{HOST}{WELL_KNOWN_PREFIX}/mcp"' in challenge


def test_mode_off_accepts_anything() -> None:
    verifier = TokenVerifier(mode="off")

    assert verifier.verify_token(None) == {}
    assert verifier.verify("garbage")


def test_unknown_mode_rejects() -> None:
    verifier = TokenVerifier(mode="introspection", issuer=ISSUER, audience=RESOURCE)

    assert verifier.verify_token("anything") is None


def test_jwt_mode_rejects_garbage_token() -> None:
    pytest.importorskip("jwt")
    verifier = TokenVerifier(mode="jwt", issuer=ISSUER, audience=RESOURCE, jwks={"keys": []})

    assert verifier.verify_token("not-a-jwt") is None
    assert verifier.verify_token(None) is None


def test_token_scopes_reads_both_claim_shapes() -> None:
    assert token_scopes({"scope": "read search"}) == {"read", "search"}
    assert token_scopes({"scp": ["read", "search"]}) == {"read", "search"}
    assert token_scopes({}) == set()


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
    verifier = TokenVerifier(mode="jwt", issuer=ISSUER, audience=RESOURCE, jwks=jwks)
    base_claims = {"iss": ISSUER, "aud": RESOURCE, "exp": int(time.time()) + 300}

    claims = verifier.verify_token(_sign(private_key, base_claims))
    assert claims is not None and claims["aud"] == RESOURCE
    assert verifier.verify_token(_sign(private_key, {**base_claims, "aud": HOST})) is None
    assert verifier.verify_token(_sign(private_key, {**base_claims, "iss": "https://evil.example.com"})) is None
    assert verifier.verify_token(_sign(private_key, {**base_claims, "exp": int(time.time()) - 60})) is None


def test_jwt_mode_rejects_disallowed_algorithm() -> None:
    jwks, private_key = _rsa_jwks_and_signer()
    verifier = TokenVerifier(
        mode="jwt", issuer=ISSUER, audience=RESOURCE, jwks=jwks, algorithms=("PS256",)
    )
    claims = {"iss": ISSUER, "aud": RESOURCE, "exp": int(time.time()) + 300}

    assert verifier.verify_token(_sign(private_key, claims)) is None


async def _dummy_app(scope: dict, receive, send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


class _StaticVerifier:
    def __init__(self, claims: dict | None) -> None:
        self.claims = claims

    def verify_token(self, token: str | None) -> dict | None:
        return self.claims


def _request(app, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)

    async def _run() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url=HOST) as client:
            return await client.get(path, headers=headers or {})

    return asyncio.run(_run())


def test_middleware_serves_metadata_on_both_well_known_paths() -> None:
    app = OAuthResourceMiddleware(
        _dummy_app, verifier=_StaticVerifier(None), resource_url=RESOURCE, issuer=ISSUER
    )

    for path in (WELL_KNOWN_PREFIX, f"{WELL_KNOWN_PREFIX}/mcp"):
        response = _request(app, path)
        assert response.status_code == 200
        assert response.json()["resource"] == RESOURCE
        assert response.json()["authorization_servers"] == [ISSUER]


def test_canonical_url_is_identical_across_metadata_challenge_and_audience() -> None:
    """The three values an identity provider must agree on, checked together.

    A mismatch here is the classic failure where local tests pass and every
    real token is rejected, so it is asserted explicitly.
    """
    app = OAuthResourceMiddleware(
        _dummy_app, verifier=_StaticVerifier(None), resource_url=RESOURCE, issuer=ISSUER
    )
    metadata_resource = _request(app, f"{WELL_KNOWN_PREFIX}/mcp").json()["resource"]
    challenge = _request(app, "/mcp").headers["www-authenticate"]
    verifier_audience = TokenVerifier(mode="jwt", issuer=ISSUER, audience=RESOURCE).audience

    assert metadata_resource == verifier_audience == RESOURCE
    assert f'resource_metadata="{HOST}{WELL_KNOWN_PREFIX}/mcp"' in challenge


def test_missing_token_gets_401_with_challenge() -> None:
    app = OAuthResourceMiddleware(
        _dummy_app, verifier=_StaticVerifier(None), resource_url=RESOURCE, issuer=ISSUER
    )

    denied = _request(app, "/mcp")

    assert denied.status_code == 401
    assert "resource_metadata" in denied.headers["www-authenticate"]


def test_insufficient_scope_gets_403() -> None:
    app = OAuthResourceMiddleware(
        _dummy_app,
        verifier=_StaticVerifier({"scope": "read"}),
        resource_url=RESOURCE,
        issuer=ISSUER,
        required_scopes={"search"},
    )

    denied = _request(app, "/mcp")

    assert denied.status_code == 403
    assert "insufficient_scope" in denied.headers["www-authenticate"]


def test_sufficient_scope_passes_through() -> None:
    app = OAuthResourceMiddleware(
        _dummy_app,
        verifier=_StaticVerifier({"scope": "search read"}),
        resource_url=RESOURCE,
        issuer=ISSUER,
        required_scopes={"search"},
    )

    assert _request(app, "/mcp").text == "ok"


def test_middleware_passes_through_when_auth_off() -> None:
    app = OAuthResourceMiddleware(
        _dummy_app, verifier=TokenVerifier(mode="off"), resource_url=RESOURCE, issuer=""
    )

    response = _request(app, "/mcp")
    assert response.status_code == 200
    assert response.text == "ok"


def test_metadata_not_served_when_host_owns_it() -> None:
    app = OAuthResourceMiddleware(
        _dummy_app,
        verifier=TokenVerifier(mode="off"),
        resource_url=RESOURCE,
        issuer=ISSUER,
        serve_metadata=False,
    )

    assert _request(app, f"{WELL_KNOWN_PREFIX}/mcp").text == "ok"
