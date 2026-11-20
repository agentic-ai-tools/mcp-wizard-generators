"""Shared fixtures: every HTTP call goes to an in-memory mock, never the network."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from pets_tiny_mcp import client
from pets_tiny_mcp.server import mcp

API_BASE_URL = "https://api.test"
TOKEN_URL = "https://auth.test/token"
SECRET = "s3cr3t-Value-42"


class MockApi:
    """Records requests and answers with ``reply`` (200 {"ok": true} by default)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_requests: list[httpx.Request] = []
        self.reply: Callable[[httpx.Request], httpx.Response] = lambda r: httpx.Response(
            200, json={"ok": True}
        )
        self.token_reply: Callable[[httpx.Request], httpx.Response] = lambda r: httpx.Response(
            200, json={"access_token": "tok-test", "expires_in": 3600}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            self.token_requests.append(request)
            return self.token_reply(request)
        self.requests.append(request)
        return self.reply(request)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[MockApi]:
    mock = MockApi()
    monkeypatch.setenv("API_BASE_URL", API_BASE_URL)
    monkeypatch.delenv("MAX_RESPONSE_BYTES", raising=False)
    monkeypatch.setenv("OAUTH_TOKEN_URL", TOKEN_URL)
    monkeypatch.setenv("OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", SECRET)
    transport = httpx.MockTransport(mock)
    monkeypatch.setattr(client, "_http", httpx.Client(transport=transport, follow_redirects=False))
    client._invalidate_auth()
    yield mock
    client._invalidate_auth()


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Call a tool the way an MCP client does, argument validation included."""
    return asyncio.run(mcp.call_tool(name, arguments))
