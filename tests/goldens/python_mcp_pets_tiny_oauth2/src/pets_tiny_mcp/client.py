"""HTTP client for the upstream OpenAPI base URL.

Auth mode: oauth2
Secrets strategy: dotenv_example_only
Secrets are read from the environment at runtime; none are stored here.
"""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import httpx

AUTH_MODE = "oauth2"

OAUTH_TOKEN_URL_DEFAULT = "https://auth.example.com/oauth2/token"
OAUTH_SCOPES_DEFAULT = "pets.read"
OAUTH_AUDIENCE_DEFAULT = "https://petstore.example.com"
# Refresh this many seconds before the token expires.
_REFRESH_MARGIN_SECONDS = 60.0
_token: dict[str, Any] = {"value": None, "expires_at": 0.0}


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set (authMode=oauth2)")
    return value


def _fetch_token() -> str:
    """OAuth2 client credentials grant (RFC 6749 section 4.4), HTTP Basic auth."""
    token_url = os.environ.get("OAUTH_TOKEN_URL") or OAUTH_TOKEN_URL_DEFAULT
    if not token_url.lower().startswith("https://"):
        raise RuntimeError("OAUTH_TOKEN_URL must use https")
    client_id = _required_env("OAUTH_CLIENT_ID")
    client_secret = _required_env("OAUTH_CLIENT_SECRET")
    data = {"grant_type": "client_credentials"}
    scopes = os.environ.get("OAUTH_SCOPES", OAUTH_SCOPES_DEFAULT).strip()
    if scopes:
        data["scope"] = scopes
    audience = os.environ.get("OAUTH_AUDIENCE", OAUTH_AUDIENCE_DEFAULT).strip()
    if audience:
        data["audience"] = audience
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            token_url,
            data=data,
            auth=(client_id, client_secret),
            headers={"Accept": "application/json"},
        )
    response.raise_for_status()
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("OAuth2 token response has no access_token")
    expires_in = float(payload.get("expires_in") or 300)
    _token["value"] = access_token
    _token["expires_at"] = time.monotonic() + expires_in - _REFRESH_MARGIN_SECONDS
    return str(access_token)


def _access_token() -> str:
    if _token["value"] and time.monotonic() < _token["expires_at"]:
        return str(_token["value"])
    return _fetch_token()


def _apply_auth(headers: dict[str, str], params: dict[str, Any]) -> None:
    headers["Authorization"] = f"Bearer {_access_token()}"


def _invalidate_auth() -> bool:
    """Drop the cached token so the next request fetches a new one."""
    _token["value"] = None
    _token["expires_at"] = 0.0
    return True


def _send(
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any],
    json_body: Any,
) -> httpx.Response:
    with httpx.Client(timeout=30.0) as client:
        return client.request(
            method.upper(), url, headers=headers, params=params or None, json=json_body
        )


def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> Any:
    base = os.environ.get("API_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("API_BASE_URL is not set")
    url = f"{base}{path}"

    def build() -> tuple[dict[str, str], dict[str, Any]]:
        # Operation headers and cookies first; auth is applied last and wins.
        out: dict[str, str] = dict(headers or {})
        if cookies:
            out["Cookie"] = "; ".join(
                f"{name}={quote(value, safe='')}" for name, value in cookies.items()
            )
        out["Accept"] = "application/json"
        query: dict[str, Any] = dict(params or {})
        _apply_auth(out, query)
        return out, query

    sent_headers, query = build()
    response = _send(method, url, sent_headers, query, json_body)
    if response.status_code == 401 and _invalidate_auth():
        # Token may have been revoked or expired early: refresh once and retry.
        sent_headers, query = build()
        response = _send(method, url, sent_headers, query, json_body)
    response.raise_for_status()
    if response.status_code == 204 or not response.content:
        return {"status": response.status_code}
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return {"text": response.text, "status": response.status_code}
