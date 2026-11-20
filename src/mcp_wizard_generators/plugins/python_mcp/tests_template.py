"""The ``tests/`` folder of a generated python-mcp project (ADR-023).

The tests use pytest and ``httpx.MockTransport``, never the network, and cover:

- tool regression: the exposed tools, their arguments, and each tool's endpoint
- authentication failures for the chosen auth mode
- HTTP status handling: error mapping, timeouts, size limits, redirects
- credential redaction, including through the tools the findings check flags

Per-project data (tools, sample arguments) is embedded as JSON literals, so no
spec text is ever spliced into Python source.
"""

from __future__ import annotations

import json
import math
from pprint import pformat
from typing import Any
from urllib.parse import quote

from mcp_wizard_generators.plugins.python_mcp.annotations import (
    _int,
    _pattern,
    _schema_type,
    bound,
)

# A sample that would not pass the argument's validation (e.g. a regex pattern).
SKIP: Any = type("Skip", (), {"__repr__": lambda self: "SKIP"})()
SECRET = "s3cr3t-Value-42"


def _numbers(schema: dict[str, Any]) -> tuple[Any, bool, Any, bool]:
    """(low, low inclusive, high, high inclusive), as the annotation reads them."""
    low, high = bound(schema.get("minimum")), bound(schema.get("maximum"))
    ex_low, ex_high = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
    low_incl = high_incl = True
    if ex_low is True:
        low_incl = False  # OpenAPI 3.0: flag on minimum
    elif bound(ex_low) is not None:
        low, low_incl = bound(ex_low), False
    if ex_high is True:
        high_incl = False
    elif bound(ex_high) is not None:
        high, high_incl = bound(ex_high), False
    return low, low_incl, high, high_incl


def _number_sample(schema: dict[str, Any], integer: bool) -> Any:
    low, low_incl, high, high_incl = _numbers(schema)
    if integer:
        if low is not None:
            value: Any = math.ceil(low) if low_incl else math.floor(low) + 1
        elif high is not None and high < 1:
            value = math.floor(high) if high_incl else math.ceil(high) - 1
        else:
            value = 1
    elif low is not None:
        value = low if low_incl else (low + high) / 2 if high is not None else low + 1
    elif high is not None and (high < 1 or (high == 1 and not high_incl)):
        value = high if high_incl else high - 1
    else:
        value = 1.0
    step = bound(schema.get("multipleOf"))
    if step is not None and step > 0:
        value = math.ceil(value / step) * step
        value = int(value) if integer else float(value)
    if high is not None and (value > high or (value == high and not high_incl)):
        return SKIP
    if low is not None and (value < low or (value == low and not low_incl)):
        return SKIP
    return value


def sample_value(schema: Any) -> Any:
    """A value that passes the argument's generated annotation, or SKIP."""
    if not isinstance(schema, dict):
        return "x"
    enum = schema.get("enum")
    if isinstance(enum, list) and any(v is not None for v in enum):
        return next(v for v in enum if v is not None)
    kind = _schema_type(schema)
    if kind == "string":
        if _pattern(schema.get("pattern")) is not None:
            return SKIP  # can't promise a value that matches an arbitrary regex
        low = max(_int(schema.get("minLength")) or 0, 1)
        high = _int(schema.get("maxLength"))
        return SKIP if high is not None and high < low else "x" * low
    if kind in ("integer", "number"):
        return _number_sample(schema, kind == "integer")
    if kind == "boolean":
        return True
    if kind == "array":
        count = _int(schema.get("minItems")) or 0
        most = _int(schema.get("maxItems"))
        if most is not None and most < count:
            return SKIP
        if count == 0:
            return []
        item = sample_value(schema.get("items"))
        return SKIP if item is SKIP else [item] * count
    if kind == "object":
        return {}
    return "x"


def tool_case(
    *,
    name: str,
    method: str,
    path: str,
    props: dict[str, Any],
    required: list[str],
    py_names: dict[str, str],
    secret_wires: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The regression case for one tool, and its redaction case (or None)."""
    args: dict[str, Any] = {}
    skip = ""
    in_path = [w for w in props if f"{{{w}}}" in path and w not in required]
    for wire in [*required, *in_path]:
        value = sample_value(props.get(wire))
        if value is SKIP:
            skip = f"cannot build a sample value for {py_names[wire]}"
            break
        args[py_names[wire]] = value
    expected = path
    for wire in props:
        if f"{{{wire}}}" in expected and py_names[wire] in args:
            expected = expected.replace(f"{{{wire}}}", quote(str(args[py_names[wire]]), safe=""))
    case = {
        "name": name,
        "method": method.upper(),
        "path": expected,
        "properties": sorted(py_names.values()),
        "required": sorted(py_names[w] for w in required),
        "args": args,
        "skip": skip,
    }
    redaction = None
    for wire in secret_wires:
        schema = props.get(wire) if isinstance(props.get(wire), dict) else {}
        accepts_secret = (
            _schema_type(schema) in ("string", None)
            and "enum" not in schema
            and _pattern(schema.get("pattern")) is None
            and (_int(schema.get("maxLength")) or len(SECRET)) >= len(SECRET)
        )
        if accepts_secret and not skip:
            redaction = {"name": name, "args": args, "secret_arg": py_names[wire]}
            break
    return case, redaction


def _literal(data: Any) -> str:
    """A readable Python literal for JSON-shaped data.

    Only dicts, lists, strings, numbers, booleans and None reach here, and their
    repr is a literal, so spec text can never become code.
    """
    return pformat(data, width=96, sort_dicts=False)


_CONFTEST = '''\
"""Shared fixtures: every HTTP call goes to an in-memory mock, never the network."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from __PKG__ import client
from __PKG__.server import mcp

API_BASE_URL = "https://api.test"
TOKEN_URL = "https://auth.test/token"
SECRET = __SECRET__


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
__AUTH_ENV__
    transport = httpx.MockTransport(mock)
    monkeypatch.setattr(client, "_http", httpx.Client(transport=transport, follow_redirects=False))
    client._invalidate_auth()
    yield mock
    client._invalidate_auth()


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Call a tool the way an MCP client does, argument validation included."""
    return asyncio.run(mcp.call_tool(name, arguments))
'''

_AUTH_ENV = {
    "api_key": """\
    monkeypatch.setenv("API_KEY", SECRET)
    monkeypatch.delenv("API_KEY_LOCATION", raising=False)
    monkeypatch.delenv("API_KEY_NAME", raising=False)""",
    "oauth2": """\
    monkeypatch.setenv("OAUTH_TOKEN_URL", TOKEN_URL)
    monkeypatch.setenv("OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", SECRET)""",
    "none": "    # authMode=none: no credentials to set.",
}

_TEST_TOOLS = '''\
"""Tool regression: the server exposes exactly the generated tools, and each one
calls its endpoint. Regenerate the project after changing the selection, or edit
TOOLS when you remove a tool by hand (see SECURITY-NOTES.md, if present)."""

from __future__ import annotations

import asyncio
from urllib.parse import unquote

import pytest
from conftest import call_tool
from mcp.server.mcpserver.exceptions import ToolError

from __PKG__.server import mcp

# One entry per tool: arguments, required arguments, and a sample call.
TOOLS = __TOOLS__
REQUIRED = [tool for tool in TOOLS if tool["required"]]


def _ids(tools: list[dict]) -> list[str]:
    return [tool["name"] for tool in tools]


def test_tools_and_arguments_match_what_was_generated() -> None:
    listed = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert sorted(listed) == sorted(_ids(TOOLS))
    for tool in TOOLS:
        schema = listed[tool["name"]].input_schema
        assert sorted(schema.get("properties", {})) == tool["properties"], tool["name"]
        assert sorted(schema.get("required", [])) == tool["required"], tool["name"]


@pytest.mark.parametrize("tool", TOOLS, ids=_ids(TOOLS))
def test_each_tool_calls_its_endpoint(api, tool: dict) -> None:
    if tool["skip"]:
        pytest.skip(tool["skip"])
    call_tool(tool["name"], tool["args"])
    [sent] = api.requests
    assert sent.method == tool["method"]
    assert sent.url.path == unquote(tool["path"])


@pytest.mark.parametrize("tool", REQUIRED, ids=_ids(REQUIRED))
def test_missing_arguments_are_rejected_before_any_request(api, tool: dict) -> None:
    with pytest.raises(ToolError, match="validation error"):
        call_tool(tool["name"], {})
    assert api.requests == []
'''

_TEST_HTTP_ERRORS = '''\
"""HTTP status handling: every failure is a short error that says what to do."""

from __future__ import annotations

import httpx
import pytest

from __PKG__ import client


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
'''

_TEST_AUTH = {
    "api_key": '''\
"""Authentication: the API key is sent where configured, and failures are clear."""

from __future__ import annotations

import httpx
import pytest
from conftest import SECRET

from __PKG__ import client


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
''',
    "oauth2": '''\
"""Authentication: OAuth2 client credentials are used, refreshed, and failures are clear."""

from __future__ import annotations

import httpx
import pytest

from __PKG__ import client


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
''',
    "none": '''\
"""Authentication: authMode=none sends no credentials; a 401 says so."""

from __future__ import annotations

import httpx
import pytest

from __PKG__ import client


def test_no_credentials_are_sent(api) -> None:
    client.request("GET", "/probe")
    sent = api.requests[0]
    assert "authorization" not in sent.headers
    assert not sent.url.params


def test_401_says_the_api_needs_credentials(api) -> None:
    api.reply = lambda request: httpx.Response(401)
    with pytest.raises(client.ApiError, match="this API needs credentials"):
        client.request("GET", "/probe")
''',
}

_TEST_REDACTION = '''\
"""Credential redaction: errors never contain secrets, even when the API echoes them."""

from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest
from conftest import SECRET, call_tool
from mcp.server.mcpserver.exceptions import ToolError

from __PKG__ import client

# Tools with secret arguments (see SECURITY-NOTES.md, if present).
SENSITIVE_TOOLS = __SENSITIVE__


def _echo(request: httpx.Request) -> httpx.Response:
    return httpx.Response(400, json={"message": f"rejected {SECRET} and {request.url}"})


def test_secret_arguments_are_redacted(api) -> None:
    api.reply = _echo
    with pytest.raises(client.ApiError) as info:
        client.request("GET", f"/items/{quote(SECRET, safe='')}", secrets=(SECRET,))
    message = str(info.value)
    assert SECRET not in message
    assert quote(SECRET, safe="") not in message
    assert "***" in message


def test_errors_do_not_chain_the_original_exception(api) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {request.url}", request=request)

    api.reply = down
    with pytest.raises(client.ApiError) as info:
        client.request("GET", "/probe", secrets=(SECRET,))
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True


@pytest.mark.parametrize("tool", SENSITIVE_TOOLS, ids=[t["name"] for t in SENSITIVE_TOOLS])
def test_flagged_tools_keep_their_secrets_out_of_errors(api, tool: dict) -> None:
    api.reply = _echo
    with pytest.raises(ToolError) as info:
        call_tool(tool["name"], {**tool["args"], tool["secret_arg"]: SECRET})
    assert SECRET not in str(info.value)
    assert "***" in str(info.value)
__AUTH_REDACTION__'''

_AUTH_REDACTION = {
    "api_key": """

def test_api_key_echoed_by_the_api_is_redacted(api) -> None:
    api.reply = lambda request: httpx.Response(400, json={"message": f"bad key {SECRET}"})
    with pytest.raises(client.ApiError) as info:
        client.request("GET", "/probe")
    assert SECRET not in str(info.value)
    assert "bad key ***" in str(info.value)
""",
    "oauth2": """

def test_client_secret_and_token_are_redacted(api) -> None:
    client.request("GET", "/probe")  # caches tok-test
    api.reply = lambda request: httpx.Response(
        400, json={"message": f"secret {SECRET} token tok-test"}
    )
    with pytest.raises(client.ApiError) as info:
        client.request("GET", "/probe")
    assert SECRET not in str(info.value)
    assert "tok-test" not in str(info.value)
""",
    "none": "",
}


def build(
    pkg: str, auth_mode: str, cases: list[dict[str, Any]], sensitive: list[dict[str, Any]]
) -> dict[str, str]:
    """Generated test files, keyed by project path."""
    mode = auth_mode if auth_mode in _AUTH_ENV else "none"

    def fill(text: str) -> str:
        return text.replace("__PKG__", pkg)

    redaction = _TEST_REDACTION.replace("__AUTH_REDACTION__", _AUTH_REDACTION[mode])
    return {
        "tests/conftest.py": fill(
            _CONFTEST.replace("__AUTH_ENV__", _AUTH_ENV[mode]).replace(
                "__SECRET__", json.dumps(SECRET)
            )
        ),
        "tests/test_tools.py": fill(_TEST_TOOLS.replace("__TOOLS__", _literal(cases))),
        "tests/test_http_errors.py": fill(_TEST_HTTP_ERRORS),
        "tests/test_auth.py": fill(_TEST_AUTH[mode]),
        "tests/test_redaction.py": fill(redaction.replace("__SENSITIVE__", _literal(sensitive))),
    }


__all__ = ["SECRET", "SKIP", "build", "sample_value", "tool_case"]
