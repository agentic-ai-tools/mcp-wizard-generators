"""HTTP client for the upstream API (API_BASE_URL).

Auth mode: api_key
Secrets strategy: dotenv_example_only
Secrets are read from the environment at runtime; none are stored here.
Errors raised from here never contain credentials (see redact()).
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import JsonValue

AUTH_MODE = "api_key"
TIMEOUT_SECONDS = 30.0
# Largest response body read into memory, after decompression.
MAX_RESPONSE_BYTES_DEFAULT = 1_048_576
_REDACTED = "***"
# Shorter values are not redacted: replacing them would garble messages.
_MIN_SECRET_LENGTH = 4


# Both subclass the SDK's ToolError: only then does the model see the message
# (any other exception becomes a bare "Error executing tool <name>").
class ConfigError(ToolError, RuntimeError):
    """The environment is misconfigured. Fix .env or the MCP client's env block."""


class ApiError(ToolError, RuntimeError):
    """An upstream call failed. The message is safe to show: secrets are redacted."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def base_url() -> str:
    """API_BASE_URL, checked; host-controlled, never taken from tool arguments."""
    raw = os.environ.get("API_BASE_URL", "").strip()
    if not raw:
        raise ConfigError(
            "API_BASE_URL is not set. Set it to the API's base URL, "
            "for example https://api.example.com/v1"
        )
    try:
        parts = urlsplit(raw)
        parts.port  # noqa: B018 - raises ValueError for an out-of-range port
    except ValueError:
        raise ConfigError("API_BASE_URL is not a valid URL") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        got = repr(scheme) if scheme else "no scheme"
        raise ConfigError(
            f"API_BASE_URL must use http or https, not {got}, "
            "for example https://api.example.com/v1"
        )
    if not parts.hostname:
        raise ConfigError("API_BASE_URL has no host, for example https://api.example.com/v1")
    if parts.username is not None or parts.password is not None:
        raise ConfigError(
            "API_BASE_URL must not contain a user name or password; "
            "use the authentication settings instead"
        )
    if parts.query or parts.fragment:
        raise ConfigError("API_BASE_URL must not have a query string or fragment")
    return raw.rstrip("/")


def max_response_bytes() -> int:
    raw = os.environ.get("MAX_RESPONSE_BYTES", "").strip()
    if not raw:
        return MAX_RESPONSE_BYTES_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        raise ConfigError("MAX_RESPONSE_BYTES must be a whole number of bytes above 0")
    return value


API_KEY_LOCATION_DEFAULT = "header"
API_KEY_NAME_DEFAULT = "X-API-Key"
_AUTH_HINT = "check API_KEY (and API_KEY_LOCATION / API_KEY_NAME)"


def _api_key_settings() -> tuple[str, str, str]:
    api_key = os.environ.get("API_KEY")
    if not api_key:
        raise ConfigError("API_KEY is not set (authMode=api_key)")
    location = os.environ.get("API_KEY_LOCATION", API_KEY_LOCATION_DEFAULT)
    if location not in ("header", "query"):
        raise ConfigError("API_KEY_LOCATION must be 'header' or 'query'")
    name = os.environ.get("API_KEY_NAME", API_KEY_NAME_DEFAULT)
    return api_key, location, name


def _check_auth() -> None:
    _api_key_settings()


def _auth_secrets() -> list[str]:
    return [os.environ.get("API_KEY", "")]


def _apply_auth(headers: dict[str, str], params: dict[str, Any]) -> None:
    """Send API_KEY as a header or query parameter; fail closed when unset."""
    api_key, location, name = _api_key_settings()
    if location == "query":
        params[name] = api_key
    else:
        headers[name] = api_key


def _invalidate_auth() -> bool:
    return False



def check_config() -> None:
    """Fail fast at startup on settings every request needs."""
    base_url()
    max_response_bytes()
    _check_auth()


def redact(text: str, secrets: tuple[Any, ...] = ()) -> str:
    """Replace credentials and secret argument values (plain or URL-encoded) with ***."""
    values = {str(v) for v in (*secrets, *_auth_secrets()) if v is not None}
    forms = {
        form
        for value in values
        if len(value) >= _MIN_SECRET_LENGTH
        for form in (value, quote(value, safe=""), quote_plus(value))
    }
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, _REDACTED)
    return text


_http: httpx.Client | None = None


def _client() -> httpx.Client:
    """One shared client: connection reuse, no redirects."""
    global _http
    if _http is None:
        _http = httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)
    return _http


def _read(where: str, http_request: httpx.Request) -> tuple[httpx.Response, bytes]:
    """Send and read the body, refusing more than MAX_RESPONSE_BYTES."""
    limit = max_response_bytes()
    too_large = (
        f"{where} failed: the response is larger than {limit} bytes "
        "(raise MAX_RESPONSE_BYTES, or ask for less, for example with a page size)"
    )
    client = _client()
    try:
        response = client.send(http_request, stream=True)
        try:
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise ApiError(too_large, response.status_code)
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > limit:
                    raise ApiError(too_large, response.status_code)
        finally:
            response.close()
            client.cookies.clear()  # never carry cookies from one call to the next
    except httpx.TimeoutException:
        raise ApiError(
            f"{where} failed: the API did not respond within {TIMEOUT_SECONDS:g} seconds"
        ) from None
    except httpx.HTTPError as exc:
        raise ApiError(
            f"{where} failed: could not reach the API ({type(exc).__name__}); "
            "check API_BASE_URL and your network"
        ) from None
    return response, bytes(body)


def _detail(body: bytes) -> str:
    """A short, single-line excerpt of an error body (its message field when JSON)."""
    text = body[:4000].decode("utf-8", errors="replace")
    try:
        data = json.loads(body) if len(body) <= 65536 else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        for key in ("message", "error_description", "error", "detail", "title"):
            if isinstance(data.get(key), str):
                text = data[key]
                break
    text = " ".join(text.split())
    return text[:300] + ("..." if len(text) > 300 else "")


def _status_error(where: str, response: httpx.Response, body: bytes) -> ApiError:
    status = response.status_code
    if status == 401:
        hint = f"authentication failed; {_AUTH_HINT}"
    elif status == 403:
        hint = "the credentials are not allowed to do this"
    elif status == 404:
        hint = "not found; check the IDs and values you passed"
    elif status == 429:
        retry = response.headers.get("retry-after", "").strip()
        hint = "rate limited; " + (
            f"retry after {retry} seconds" if retry.isdigit() else "slow down and retry later"
        )
    elif status >= 500:
        hint = "the API failed; try again later"
    else:
        hint = "the API rejected the request; check the arguments"
    detail = _detail(body)
    message = f"{where} failed: HTTP {status}, {hint}."
    return ApiError(f"{message} API said: {detail}" if detail else message, status)


def _decode(where: str, response: httpx.Response, body: bytes) -> JsonValue:
    if response.status_code == 204 or not body:
        return {"status": response.status_code}
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "application/json" or content_type.endswith("+json"):
        try:
            return json.loads(body)
        except ValueError:
            raise ApiError(f"{where} failed: the API sent a response that is not valid JSON") from None
    text = body.decode(response.encoding or "utf-8", errors="replace")
    return {"text": text, "status": response.status_code}


def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    secrets: tuple[Any, ...] = (),
) -> JsonValue:
    """Call the API. ``secrets`` are argument values to keep out of error messages."""
    method = method.upper()
    url = f"{base_url()}{path}"
    where = f"{method} {path}"

    def build() -> httpx.Request:
        # Operation headers and cookies first; auth is applied last and wins.
        out: dict[str, str] = dict(headers or {})
        if cookies:
            out["Cookie"] = "; ".join(
                f"{name}={quote(value, safe='')}" for name, value in cookies.items()
            )
        out["Accept"] = "application/json"
        query: dict[str, Any] = dict(params or {})
        _apply_auth(out, query)
        return _client().build_request(
            method, url, headers=out, params=query or None, json=json_body
        )

    try:
        response, body = _read(where, build())
        if response.status_code == 401 and _invalidate_auth():
            # Token may have been revoked or expired early: refresh once and retry.
            response, body = _read(where, build())
        if 300 <= response.status_code < 400:
            raise ApiError(
                f"{where} failed: the API redirected (HTTP {response.status_code}); "
                "set API_BASE_URL to the final address",
                response.status_code,
            )
        if response.status_code >= 400:
            raise _status_error(where, response, body)
        return _decode(where, response, body)
    except ApiError as exc:
        raise ApiError(redact(str(exc), secrets), exc.status) from None
