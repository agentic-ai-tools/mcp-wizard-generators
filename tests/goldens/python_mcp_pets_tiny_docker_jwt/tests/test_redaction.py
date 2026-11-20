"""Credential redaction: errors never contain secrets, even when the API echoes them."""

from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest
from conftest import SECRET, call_tool
from mcp.server.mcpserver.exceptions import ToolError

from pets_tiny_mcp import client

# Tools with secret arguments (see SECURITY-NOTES.md, if present).
SENSITIVE_TOOLS = []


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
