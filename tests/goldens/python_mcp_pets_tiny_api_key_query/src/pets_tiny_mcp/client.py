"""HTTP client for the upstream OpenAPI base URL.

Auth mode: api_key
Secrets strategy: dotenv_example_only
Secrets are read from the environment at runtime; none are stored here.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

AUTH_MODE = "api_key"

API_KEY_LOCATION_DEFAULT = "query"
API_KEY_NAME_DEFAULT = "api_key"


def _apply_auth(headers: dict[str, str], params: dict[str, Any]) -> None:
    """Send API_KEY as a header or query parameter; fail closed when unset."""
    api_key = os.environ.get("API_KEY")
    if not api_key:
        raise RuntimeError("API_KEY is not set (authMode=api_key)")
    location = os.environ.get("API_KEY_LOCATION", API_KEY_LOCATION_DEFAULT)
    name = os.environ.get("API_KEY_NAME", API_KEY_NAME_DEFAULT)
    if location == "query":
        params[name] = api_key
    elif location == "header":
        headers[name] = api_key
    else:
        raise RuntimeError("API_KEY_LOCATION must be 'header' or 'query'")


def _invalidate_auth() -> bool:
    return False


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
