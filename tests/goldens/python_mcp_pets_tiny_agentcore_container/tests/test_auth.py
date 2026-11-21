"""Authentication: the API key is sent where configured, and failures are clear."""

from __future__ import annotations

import httpx
import pytest
from conftest import SECRET

from pets_tiny_mcp import client


def test_requests_carry_the_api_key(api) -> None:
    client.request("GET", "/probe")
    sent = api.requests[0]
    name = client.API_KEY_NAME_DEFAULT
    if client.API_KEY_LOCATION_DEFAULT == "query":
        assert sent.url.params[name] == SECRET
    else:
        assert sent.headers[name] == SECRET


def test_missing_api_key_fails_before_any_request(api, monkeypatch) -> None:
    monkeypatch.delenv("API_KEY")
    with pytest.raises(client.ConfigError, match="API_KEY is not set"):
        client.request("GET", "/probe")
    with pytest.raises(client.ConfigError, match="API_KEY is not set"):
        client.check_config()
    assert api.requests == []


def test_rejected_api_key_is_explained(api) -> None:
    api.reply = lambda request: httpx.Response(401, json={"message": "bad key"})
    with pytest.raises(client.ApiError, match="authentication failed; check API_KEY"):
        client.request("GET", "/probe")
    assert len(api.requests) == 1  # an API key is not retried
