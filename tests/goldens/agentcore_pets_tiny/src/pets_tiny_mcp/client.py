"""Upstream OpenAPI HTTP client stub.

Auth mode: none
Secrets strategy: dotenv_example_only
"""

from __future__ import annotations

import os
from typing import Any


def request(method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    base = os.environ.get("API_BASE_URL", "").rstrip("/")
    url = f"{base}{path}"
    # Skeleton only — real HTTP + auth adapters land in a later PR.
    return {"_skeleton": True, "method": method, "url": url, "json": json_body}
