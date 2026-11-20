"""Server-side JWT checks for python-mcp servers over HTTP (design ADR-026).

Emitted only when the deployment asks the server to check tokens itself (the
``docker`` target with inbound JWT auth). AgentCore checks tokens in front of the
server instead (``MCP_AUTH=platform``), so its servers do not get this module.

Any OIDC identity provider works; nothing provider-specific is baked in.
"""

from __future__ import annotations

AUTH_MODULE = '''\
"""Bearer-token (JWT) checks for the HTTP transport, used when MCP_AUTH=jwt.

Works with any OIDC identity provider (Amazon Cognito, Okta, Auth0, Microsoft
Entra ID, Keycloak, ...). Configure it with environment variables:

  MCP_JWT_DISCOVERY_URL  the provider's .../.well-known/openid-configuration URL
  MCP_JWT_AUDIENCES      allowed `aud` values (comma-separated)
  MCP_JWT_CLIENTS        allowed `client_id` / `azp` values (comma-separated)
  MCP_JWT_SCOPES         the token needs at least one of these scopes (comma-separated)

At least one of the last three is required, so tokens the provider issued for
other apps are refused. Tokens must be unexpired, come from the provider's
issuer, and be signed with an asymmetric key from its JWKS.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import anyio.to_thread
import jwt

LOG = logging.getLogger(__name__)
# Asymmetric only: "none" and shared-secret (HS*) tokens are always refused.
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DISCOVERY_SUFFIX = "/.well-known/openid-configuration"
LEEWAY_SECONDS = 60
MAX_DOCUMENT_BYTES = 1_000_000


class AuthConfigError(ValueError):
    """An MCP_JWT_* variable is wrong. The message says how to fix it."""


class TokenRejected(Exception):
    """The bearer token is not acceptable. ``status`` is 401 (who are you) or 403 (not allowed)."""

    def __init__(self, reason: str, status: int = 401) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class JwtSettings:
    discovery_url: str
    audiences: tuple[str, ...] = ()
    clients: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()


def _split(value: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in (value or "").split(",") if item.strip())


def _check_url(url: str, name: str) -> None:
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS:
        return  # a provider on this machine, for tests
    raise AuthConfigError(f"{name} must be an https URL, not {url!r}")


def load_jwt_settings(env: Mapping[str, str] | None = None) -> JwtSettings:
    """Read and check the MCP_JWT_* variables."""
    env = os.environ if env is None else env
    url = (env.get("MCP_JWT_DISCOVERY_URL") or "").strip()
    if not url:
        raise AuthConfigError(
            "MCP_JWT_DISCOVERY_URL is not set: use your identity provider's "
            f"...{DISCOVERY_SUFFIX} URL"
        )
    if not url.endswith(DISCOVERY_SUFFIX):
        raise AuthConfigError(f"MCP_JWT_DISCOVERY_URL must end with {DISCOVERY_SUFFIX}, not {url!r}")
    _check_url(url, "MCP_JWT_DISCOVERY_URL")
    settings = JwtSettings(
        discovery_url=url,
        audiences=_split(env.get("MCP_JWT_AUDIENCES")),
        clients=_split(env.get("MCP_JWT_CLIENTS")),
        scopes=_split(env.get("MCP_JWT_SCOPES")),
    )
    if not (settings.audiences or settings.clients or settings.scopes):
        raise AuthConfigError(
            "set at least one of MCP_JWT_AUDIENCES, MCP_JWT_CLIENTS or MCP_JWT_SCOPES, "
            "so tokens the identity provider issued for other apps are refused"
        )
    return settings


def fetch_json(url: str) -> dict[str, Any]:
    """GET a small JSON document (the provider's discovery document)."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - https checked
        data = response.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"{url} returned more than {MAX_DOCUMENT_BYTES} bytes")
    document = json.loads(data)
    if not isinstance(document, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return document


def _scopes(claims: Mapping[str, Any]) -> set[str]:
    granted: set[str] = set()
    for name in ("scope", "scp"):  # "scope" is a space-separated string; Entra uses "scp"
        value = claims.get(name)
        if isinstance(value, str):
            granted.update(value.split())
        elif isinstance(value, list):
            granted.update(str(item) for item in value)
    return granted


class JwtVerifier:
    """Checks bearer tokens against one identity provider. Thread-safe."""

    def __init__(
        self,
        settings: JwtSettings,
        fetch: Callable[[str], dict[str, Any]] = fetch_json,
    ) -> None:
        self.settings = settings
        self._fetch = fetch
        self._lock = threading.Lock()
        self._issuer: str | None = None
        self._jwks: jwt.PyJWKClient | None = None

    def _provider(self) -> tuple[str, jwt.PyJWKClient]:
        """Discovery runs on first use and is retried until it succeeds."""
        with self._lock:
            if self._issuer is None or self._jwks is None:
                document = self._fetch(self.settings.discovery_url)
                issuer, jwks_uri = document.get("issuer"), document.get("jwks_uri")
                if not isinstance(issuer, str) or not isinstance(jwks_uri, str):
                    raise ValueError("the discovery document has no issuer or jwks_uri")
                _check_url(jwks_uri, "jwks_uri")
                self._jwks = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600, timeout=10)
                self._issuer = issuer
            return self._issuer, self._jwks

    def verify(self, token: str) -> dict[str, Any]:
        """Return the token's claims, or raise ``TokenRejected``."""
        issuer, jwks = self._provider()
        try:
            key = jwks.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError:
            raise  # provider outage: not the caller's fault
        except (jwt.PyJWKClientError, jwt.InvalidTokenError):
            raise TokenRejected("the token is not signed by a key the identity provider publishes") from None
        settings = self.settings
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=list(ALGORITHMS),
                issuer=issuer,
                audience=list(settings.audiences) or None,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iss"], "verify_aud": bool(settings.audiences)},
            )
        except jwt.ExpiredSignatureError:
            raise TokenRejected("the token has expired") from None
        except jwt.InvalidAudienceError:
            raise TokenRejected("the token was issued for a different audience") from None
        except jwt.InvalidIssuerError:
            raise TokenRejected("the token comes from a different issuer") from None
        except jwt.InvalidTokenError:
            raise TokenRejected("the token is not valid") from None
        if settings.clients:
            client = str(claims.get("client_id") or claims.get("azp") or "")
            if client not in settings.clients:
                raise TokenRejected("the token was issued to a client this server does not allow", 403)
        if settings.scopes and not _scopes(claims) & set(settings.scopes):
            raise TokenRejected("the token has none of the scopes this server requires", 403)
        return claims


async def _send_json(send: Any, status: int, body: dict[str, Any], headers: list | None = None) -> None:
    data = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(data)).encode()),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": data})


async def _deny(send: Any, status: int, code: str, description: str) -> None:
    challenge = f'Bearer error="{code}", error_description="{description}"'.encode()
    body = {"error": code, "error_description": description}
    await _send_json(send, status, body, [(b"www-authenticate", challenge)])


class BearerAuthMiddleware:
    """ASGI middleware: every HTTP request needs a valid bearer token."""

    def __init__(self, app: Any, verifier: JwtVerifier) -> None:
        self.app = app
        self.verifier = verifier

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":  # lifespan events
            await self.app(scope, receive, send)
            return
        header = ""
        for name, value in scope.get("headers") or []:
            if name == b"authorization":
                header = value.decode("latin-1")
                break
        scheme, _, token = header.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            await _deny(send, 401, "invalid_request", "send an Authorization: Bearer token")
            return
        try:
            await anyio.to_thread.run_sync(self.verifier.verify, token)
        except TokenRejected as exc:
            LOG.info("Refused a request: %s", exc.reason)
            code = "invalid_token" if exc.status == 401 else "insufficient_scope"
            await _deny(send, exc.status, code, exc.reason)
            return
        except Exception:  # discovery or JWKS unreachable, bad provider config
            LOG.exception("Token check failed")
            await _send_json(send, 503, {"error": "the token check is unavailable; try again later"})
            return
        await self.app(scope, receive, send)


def serve_with_jwt(mcp: Any, transport: Any, settings: JwtSettings, security: Any) -> None:
    """Run streamable HTTP with every request checked by ``BearerAuthMiddleware``."""
    import uvicorn

    app = mcp.streamable_http_app(
        streamable_http_path=transport.path,
        stateless_http=True,
        transport_security=security,
        host=transport.host,
    )
    protected = BearerAuthMiddleware(app, JwtVerifier(settings))
    uvicorn.run(protected, host=transport.host, port=transport.port, log_level="info")
'''

TEST_JWT_AUTH = '''\
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

from __PKG__.auth import (
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
'''

__all__ = ["AUTH_MODULE", "TEST_JWT_AUTH"]
