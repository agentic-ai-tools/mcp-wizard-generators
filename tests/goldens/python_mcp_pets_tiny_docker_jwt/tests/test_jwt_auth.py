"""JWT checks against an identity provider running on this machine (no network)."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from pets_tiny_mcp.auth import (
    AuthConfigError,
    BearerAuthMiddleware,
    JwtSettings,
    JwtVerifier,
    TokenRejected,
    load_jwt_settings,
)


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Provider:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.key.public_key().public_numbers()
        jwks = {"keys": [{"kty": "RSA", "kid": "k1", "alg": "RS256", "use": "sig",
                          "n": _b64(numbers.n), "e": _b64(numbers.e)}]}
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = jwks if self.path == "/jwks" else {
                    "issuer": provider.issuer, "jwks_uri": provider.issuer + "/jwks"}
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.issuer = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.discovery = self.issuer + "/.well-known/openid-configuration"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def token(self, key: object = None, algorithm: str = "RS256", **claims: object) -> str:
        now = int(time.time())
        body = {"iss": self.issuer, "aud": "api://test", "exp": now + 300, **claims}
        return jwt.encode(body, key or self.key, algorithm=algorithm, headers={"kid": "k1"})


@pytest.fixture(scope="module")
def provider() -> Iterator[Provider]:
    idp = Provider()
    yield idp
    idp.server.shutdown()
    idp.server.server_close()


def verifier(provider: Provider, **settings: tuple[str, ...]) -> JwtVerifier:
    settings.setdefault("audiences", ("api://test",))
    return JwtVerifier(JwtSettings(provider.discovery, **settings))


def test_a_valid_token_passes(provider: Provider) -> None:
    assert verifier(provider).verify(provider.token())["iss"] == provider.issuer


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"exp": 1}, "expired"),
        ({"aud": "api://other"}, "different audience"),
        ({"iss": "https://evil.example.com"}, "different issuer"),
    ],
)
def test_bad_claims_are_refused(provider: Provider, claims: dict, reason: str) -> None:
    with pytest.raises(TokenRejected, match=reason) as info:
        verifier(provider).verify(provider.token(**claims))
    assert info.value.status == 401


def test_tokens_signed_with_another_key_or_a_shared_secret_are_refused(provider: Provider) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(TokenRejected):
        verifier(provider).verify(provider.token(key=other))
    with pytest.raises(TokenRejected):
        verifier(provider).verify(provider.token(key="shared-secret-0123456789abcdef", algorithm="HS256"))
    with pytest.raises(TokenRejected):
        verifier(provider).verify("not-a-jwt")


def test_clients_and_scopes(provider: Provider) -> None:
    by_client = verifier(provider, audiences=(), clients=("app-1",))
    assert by_client.verify(provider.token(client_id="app-1"))
    assert by_client.verify(provider.token(azp="app-1"))
    with pytest.raises(TokenRejected) as info:
        by_client.verify(provider.token(client_id="app-2"))
    assert info.value.status == 403

    by_scope = verifier(provider, audiences=(), scopes=("pets.read", "pets.write"))
    assert by_scope.verify(provider.token(scope="openid pets.read"))
    assert by_scope.verify(provider.token(scp=["pets.write"]))
    with pytest.raises(TokenRejected) as info:
        by_scope.verify(provider.token(scope="openid"))
    assert info.value.status == 403


def test_settings_are_checked() -> None:
    url = "https://idp.example.com/.well-known/openid-configuration"
    assert load_jwt_settings({"MCP_JWT_DISCOVERY_URL": url, "MCP_JWT_SCOPES": "a,b"}).scopes == ("a", "b")
    for env in ({}, {"MCP_JWT_DISCOVERY_URL": url}, {"MCP_JWT_DISCOVERY_URL": "https://idp.example.com/"}):
        with pytest.raises(AuthConfigError):
            load_jwt_settings(env)


def _call(middleware: BearerAuthMiddleware, headers: list[tuple[bytes, bytes]]) -> tuple[int, bool]:
    reached: list[bool] = []
    sent: list[dict] = []

    async def app(scope: dict, receive: object, send: object) -> None:
        reached.append(True)

    async def send(message: dict) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    middleware.app = app
    asyncio.run(middleware({"type": "http", "headers": headers}, receive, send))
    status = sent[0]["status"] if sent else 200
    return status, bool(reached)


def test_middleware_requires_a_valid_bearer_token(provider: Provider) -> None:
    middleware = BearerAuthMiddleware(None, verifier(provider))
    assert _call(middleware, []) == (401, False)
    assert _call(middleware, [(b"authorization", b"Basic abc")]) == (401, False)
    bad = provider.token(aud="api://other").encode()
    assert _call(middleware, [(b"authorization", b"Bearer " + bad)]) == (401, False)
    good = provider.token().encode()
    assert _call(middleware, [(b"authorization", b"Bearer " + good)]) == (200, True)
