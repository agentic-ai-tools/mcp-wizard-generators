"""HTTP client for the upstream OpenAPI base URL.

Auth mode: none
Secrets strategy: dotenv_example_only
Secrets are read from the environment at runtime; none are stored here.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

AUTH_MODE = "none"

def _apply_auth(headers: dict[str, str], params: dict[str, Any]) -> None:
    """authMode=none: no credentials (development or public APIs only)."""


def _invalidate_auth() -> bool:
    return False


def _send(
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any],
    json_body: dict[str, Any] | None,
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
    json_body: dict[str, Any] | None = None,
) -> Any:
    base = os.environ.get("API_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("API_BASE_URL is not set")
    url = f"{base}{path}"
    headers: dict[str, str] = {"Accept": "application/json"}
    query: dict[str, Any] = dict(params or {})
    _apply_auth(headers, query)
    response = _send(method, url, headers, query, json_body)
    if response.status_code == 401 and _invalidate_auth():
        # Token may have been revoked or expired early: refresh once and retry.
        headers = {"Accept": "application/json"}
        query = dict(params or {})
        _apply_auth(headers, query)
        response = _send(method, url, headers, query, json_body)
    response.raise_for_status()
    if response.status_code == 204 or not response.content:
        return {"status": response.status_code}
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return {"text": response.text, "status": response.status_code}
