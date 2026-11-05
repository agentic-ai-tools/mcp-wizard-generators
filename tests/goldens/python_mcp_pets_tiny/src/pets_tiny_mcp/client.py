"""HTTP client for the upstream OpenAPI base URL.

Auth mode: none
Secrets strategy: dotenv_example_only
"""

from __future__ import annotations

import os
from typing import Any

import httpx


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    # authMode=none — no credential headers
    return headers


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
    with httpx.Client(headers=_headers(), timeout=30.0) as client:
        response = client.request(method.upper(), url, params=params, json=json_body)
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return {"status": response.status_code}
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return {"text": response.text, "status": response.status_code}
