"""MCP server — official Python SDK MCPServer (mcp 2.x).

Speaks stdio by default, or streamable HTTP (see transport.py). Exits with a
clear message when API_BASE_URL, the auth or the transport settings are wrong.
"""

from __future__ import annotations

import sys
from typing import Annotated
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field, JsonValue

from pets_tiny_mcp.client import ConfigError, check_config, request
from pets_tiny_mcp.transport import TransportConfigError, load_transport_config

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
        transport = load_transport_config()
    except (ConfigError, TransportConfigError) as exc:
        sys.stderr.write(f"Cannot start: {exc}\n")
        raise SystemExit(2) from None
    if transport.transport == "stdio":
        mcp.run(transport="stdio")
        return
    security = None
    if transport.allowed_hosts:
        # Answer only these Host headers (DNS-rebinding protection).
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(transport.allowed_hosts),
        )
    sys.stderr.write(
        f"MCP server on {transport.url} (streamable HTTP, auth: {transport.auth})\n"
    )
    mcp.run(
        "streamable-http",
        host=transport.host,
        port=transport.port,
        streamable_http_path=transport.path,
        stateless_http=True,
        transport_security=security,
    )


if __name__ == "__main__":
    main()
