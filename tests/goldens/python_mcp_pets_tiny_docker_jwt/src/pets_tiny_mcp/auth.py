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
