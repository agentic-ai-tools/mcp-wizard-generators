"""MCP server entrypoint (stdio skeleton).

Replace register_tool / run_stdio with the pinned MCP Python SDK
once the SDK pinning decision lands (system design open decision #4).
"""

from __future__ import annotations

from pets_tiny_mcp.tools import TOOLS


def register_tool(name: str, handler: dict) -> None:
    """Stub registration hook for the MCP SDK."""
    _ = name, handler


def run_stdio() -> None:
    """Stub transport loop — wire to MCP SDK stdio server."""
    print("mcp-wizard skeleton: stdio server not yet bound to an MCP SDK")


def main() -> None:
    register_tool("listpets", TOOLS["listpets"])
    register_tool("showpetbyid", TOOLS["showpetbyid"])
    run_stdio()


if __name__ == "__main__":
    main()
