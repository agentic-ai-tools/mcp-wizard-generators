"""MCP stdio server — official Python SDK high-level API.

Prefer MCPServer (mcp 2.x); fall back to FastMCP (mcp 1.x).
"""

from __future__ import annotations

from typing import Any

from pets_tiny_mcp.client import request

try:
    from mcp.server import MCPServer as _MCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _MCP

mcp = _MCP("Pets Tiny API")


@mcp.tool(name="listpets", description="List all pets")
def listpets(limit: int | None = None) -> Any:
    """List all pets"""
    path_template = "/pets"
    path = path_template
    payload = {k: v for k, v in {"limit": limit}.items() if v is not None}
    return request("GET", path, params=payload or None)


@mcp.tool(name="showpetbyid", description="Info for a specific pet")
def showpetbyid(petId: str) -> Any:
    """Info for a specific pet"""
    path_template = "/pets/{petId}"
    path = path_template.format(petId=petId)
    return request("GET", path)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
