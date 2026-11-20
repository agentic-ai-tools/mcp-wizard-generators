"""Tool regression: the server exposes exactly the generated tools, and each one
calls its endpoint. Regenerate the project after changing the selection, or edit
TOOLS when you remove a tool by hand (see SECURITY-NOTES.md, if present)."""

from __future__ import annotations

import asyncio
from urllib.parse import unquote

import pytest
from conftest import call_tool
from mcp.server.mcpserver.exceptions import ToolError

from pets_tiny_mcp.server import mcp

# One entry per tool: arguments, required arguments, and a sample call.
TOOLS = [{'name': 'listpets',
  'method': 'GET',
  'path': '/pets',
  'properties': ['limit'],
  'required': [],
  'args': {},
  'skip': ''},
 {'name': 'showpetbyid',
  'method': 'GET',
  'path': '/pets/x',
  'properties': ['petId'],
  'required': ['petId'],
  'args': {'petId': 'x'},
  'skip': ''}]
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
