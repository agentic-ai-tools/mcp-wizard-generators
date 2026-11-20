"""Authentication: authMode=none sends no credentials; a 401 says so."""

from __future__ import annotations

import httpx
import pytest

from pets_tiny_mcp import client


def test_no_credentials_are_sent(api) -> None:
    client.request("GET", "/probe")
    sent = api.requests[0]
    assert "authorization" not in sent.headers
    assert not sent.url.params


def test_401_says_the_api_needs_credentials(api) -> None:
    api.reply = lambda request: httpx.Response(401)
    with pytest.raises(client.ApiError, match="this API needs credentials"):
        client.request("GET", "/probe")
