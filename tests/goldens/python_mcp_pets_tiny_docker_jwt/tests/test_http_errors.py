"""HTTP status handling: every failure is a short error that says what to do."""

from __future__ import annotations

import httpx
import pytest

from pets_tiny_mcp import client


@pytest.mark.parametrize(
    ("status", "hint"),
    [
        (400, "the API rejected the request"),
        (403, "not allowed"),
        (404, "not found"),
        (429, "rate limited"),
        (500, "try again later"),
        (503, "try again later"),
    ],
)
def test_error_statuses_are_explained(api, status: int, hint: str) -> None:
    api.reply = lambda request: httpx.Response(status, json={"message": "upstream says no"})
    with pytest.raises(client.ApiError) as info:
        client.request("GET", "/probe")
    message = str(info.value)
    assert f"HTTP {status}" in message
    assert hint in message
    assert "upstream says no" in message
    assert info.value.status == status


def test_retry_after_is_reported(api) -> None:
    api.reply = lambda request: httpx.Response(429, headers={"retry-after": "9"})
    with pytest.raises(client.ApiError, match="retry after 9 seconds"):
        client.request("GET", "/probe")


def test_timeouts_and_unreachable_hosts(api) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    api.reply = slow
    with pytest.raises(client.ApiError, match="did not respond"):
        client.request("GET", "/probe")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    api.reply = down
    with pytest.raises(client.ApiError, match="could not reach the API"):
        client.request("GET", "/probe")


def test_large_responses_are_refused(api, monkeypatch) -> None:
    monkeypatch.setenv("MAX_RESPONSE_BYTES", "64")
    api.reply = lambda request: httpx.Response(200, content=b"x" * 65)
    with pytest.raises(client.ApiError, match="larger than 64 bytes"):
        client.request("GET", "/probe")


def test_redirects_are_not_followed(api) -> None:
    api.reply = lambda request: httpx.Response(302, headers={"location": "https://elsewhere.test/"})
    with pytest.raises(client.ApiError, match="redirected"):
        client.request("GET", "/probe")
    assert len(api.requests) == 1


def test_json_text_and_empty_responses(api) -> None:
    api.reply = lambda request: httpx.Response(200, json={"items": [1, 2]})
    assert client.request("GET", "/probe") == {"items": [1, 2]}
    api.reply = lambda request: httpx.Response(200, text="plain")
    assert client.request("GET", "/probe") == {"text": "plain", "status": 200}
    api.reply = lambda request: httpx.Response(204)
    assert client.request("GET", "/probe") == {"status": 204}


@pytest.mark.parametrize("value", ["ftp://api.test", "api.test", "https://u:p@api.test"])
def test_api_base_url_must_be_a_plain_http_url(api, monkeypatch, value: str) -> None:
    monkeypatch.setenv("API_BASE_URL", value)
    with pytest.raises(client.ConfigError, match="API_BASE_URL"):
        client.request("GET", "/probe")
    assert api.requests == []
