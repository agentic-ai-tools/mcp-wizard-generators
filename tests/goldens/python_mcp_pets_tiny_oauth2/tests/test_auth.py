"""Authentication: OAuth2 client credentials are used, refreshed, and failures are clear."""

from __future__ import annotations

import httpx
import pytest

from pets_tiny_mcp import client


def test_requests_carry_a_bearer_token(api) -> None:
    client.request("GET", "/probe")
    assert api.requests[0].headers["Authorization"] == "Bearer tok-test"
    assert b"grant_type=client_credentials" in api.token_requests[0].content


def test_missing_client_secret_fails_before_any_request(api, monkeypatch) -> None:
    monkeypatch.delenv("OAUTH_CLIENT_SECRET")
    with pytest.raises(client.ConfigError, match="OAUTH_CLIENT_SECRET is not set"):
        client.request("GET", "/probe")
    assert api.requests == [] and api.token_requests == []


def test_token_endpoint_rejection_is_explained(api) -> None:
    api.token_reply = lambda request: httpx.Response(401, json={"error": "invalid_client"})
    with pytest.raises(client.ApiError, match="OAuth2 token request failed: HTTP 401"):
        client.request("GET", "/probe")
    assert api.requests == []


def test_401_refreshes_the_token_once(api) -> None:
    tokens = iter(["tok-1", "tok-2", "tok-3"])
    api.token_reply = lambda request: httpx.Response(
        200, json={"access_token": next(tokens), "expires_in": 3600}
    )
    replies = iter([httpx.Response(401), httpx.Response(200, json={"ok": True})])
    api.reply = lambda request: next(replies)
    assert client.request("GET", "/probe") == {"ok": True}
    assert [r.headers["Authorization"] for r in api.requests] == ["Bearer tok-1", "Bearer tok-2"]

    api.reply = lambda request: httpx.Response(401)
    with pytest.raises(client.ApiError, match="authentication failed; check OAUTH_CLIENT_ID"):
        client.request("GET", "/probe")
