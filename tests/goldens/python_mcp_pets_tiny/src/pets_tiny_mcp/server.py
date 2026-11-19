"""MCP stdio server — official Python SDK MCPServer (mcp 2.x).

Exits with a clear message when API_BASE_URL or the auth settings are wrong.
"""

from __future__ import annotations

import sys
from typing import Annotated
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from pydantic import Field, JsonValue

from pets_tiny_mcp.client import ConfigError, check_config, request

mcp = MCPServer("Pets Tiny API")


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
    try:
        check_config()
    except ConfigError as exc:
        sys.stderr.write(f"Cannot start: {exc}\n")
        raise SystemExit(2) from None
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
