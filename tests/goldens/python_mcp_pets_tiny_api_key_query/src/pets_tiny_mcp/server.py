"""MCP stdio server — official Python SDK MCPServer (mcp 2.x).

Primary import follows the mcp 2 migration guide:
https://py.sdk.modelcontextprotocol.io/v2/migration/#fastmcp-renamed-to-mcpserver
"""

from __future__ import annotations

import re
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import Field, JsonValue

from pets_tiny_mcp.client import request

try:
    from mcp.server.mcpserver import MCPServer as _MCP
except ImportError:  # mcp 1.x only — fresh installs pin mcp>=2
    from mcp.server.fastmcp import FastMCP as _MCP

mcp = _MCP("Pets Tiny API")


@mcp.tool(name="listpets", description="List all pets")
def listpets(
    limit: Annotated[int, Field(le=100, description="Maximum number of pets to return (query parameter `limit`)")] | None = None,
) -> JsonValue:
    """List all pets"""
    path_template = "/pets"
    path = path_template
    payload = {k: v for k, v in {"limit": limit}.items() if v is not None}
    return request("GET", path, params=payload or None)


@mcp.tool(name="showpetbyid", description="Info for a specific pet")
def showpetbyid(
    petId: Annotated[str, Field(min_length=1, description="Path parameter `petId`")],
) -> JsonValue:
    """Info for a specific pet"""
    path_template = "/pets/{petId}"
    path = path_template.format(petId=quote(str(petId), safe=""))
    return request("GET", path)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
