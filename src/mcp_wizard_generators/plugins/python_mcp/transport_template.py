"""``src/<pkg>/transport.py`` of a generated python-mcp project (design ADR-026).

Stdio stays the default. Streamable HTTP is opt-in through ``MCP_*`` environment
variables, and the server refuses to listen beyond loopback unless something
authenticates callers. The module is stdlib-only so it can be tested without a network.
"""

from __future__ import annotations

TRANSPORT_MODULE = '''\
"""How the MCP server is reached: stdio (the default) or streamable HTTP.

Set with environment variables (see .env.example and the README section
"Run over HTTP"). Nothing here needs the network or the MCP SDK.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

TRANSPORTS = ("stdio", "streamable-http")
# none: nothing checks callers, so only loopback is allowed.
# platform: a gateway in front of the server (for example AWS Bedrock AgentCore's
# JWT authorizer) authenticates every request before it arrives.
AUTH_MODES = ("none", "platform")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class TransportConfigError(ValueError):
    """An MCP_* transport variable is wrong. The message says how to fix it."""


@dataclass(frozen=True)
class TransportConfig:
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"
    auth: str = "none"
    allowed_hosts: tuple[str, ...] = ()

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}{self.path}"


def load_transport_config(env: Mapping[str, str] | None = None) -> TransportConfig:
    """Read MCP_TRANSPORT and, for HTTP, MCP_HOST / MCP_PORT / MCP_PATH / MCP_AUTH."""
    env = os.environ if env is None else env

    def setting(name: str, default: str) -> str:
        return (env.get(name) or "").strip() or default

    transport = setting("MCP_TRANSPORT", "stdio")
    if transport not in TRANSPORTS:
        raise TransportConfigError(
            f"MCP_TRANSPORT must be one of {', '.join(TRANSPORTS)}, not {transport!r}"
        )
    if transport == "stdio":
        # HTTP settings left in .env must not break a stdio run.
        return TransportConfig()

    host = setting("MCP_HOST", "127.0.0.1")
    raw_port = setting("MCP_PORT", "8000")
    port = int(raw_port) if raw_port.isdigit() else 0
    if not 1 <= port <= 65535:
        raise TransportConfigError(f"MCP_PORT must be a number from 1 to 65535, not {raw_port!r}")
    path = setting("MCP_PATH", "/mcp")
    if not path.startswith("/") or any(char in path for char in "?# "):
        raise TransportConfigError(
            f"MCP_PATH must start with / and contain no ?, # or spaces, not {path!r}"
        )
    auth = setting("MCP_AUTH", "none")
    if auth == "jwt":
        raise TransportConfigError(
            "MCP_AUTH=jwt is not built into this server. Regenerate it with the docker "
            "deployment, or put it behind a gateway that checks tokens and set MCP_AUTH=platform"
        )
    if auth not in AUTH_MODES:
        raise TransportConfigError(
            f"MCP_AUTH must be one of {', '.join(AUTH_MODES)}, not {auth!r}"
        )
    if host not in LOOPBACK_HOSTS and auth == "none":
        raise TransportConfigError(
            f"refusing to listen on {host} without authentication: anyone who can reach "
            "this port could call the tools. Keep MCP_HOST=127.0.0.1, or set "
            "MCP_AUTH=platform only when a gateway in front of the server (for example "
            "AWS Bedrock AgentCore) checks every caller's token"
        )
    allowed = tuple(
        item.strip() for item in (env.get("MCP_ALLOWED_HOSTS") or "").split(",") if item.strip()
    )
    return TransportConfig(transport, host, port, path, auth, allowed)
'''

_PLATFORM_ONLY = """# none: nothing checks callers, so only loopback is allowed.
# platform: a gateway in front of the server (for example AWS Bedrock AgentCore's
# JWT authorizer) authenticates every request before it arrives.
AUTH_MODES = ("none", "platform")
"""
_WITH_JWT = """# none: nothing checks callers, so only loopback is allowed.
# platform: a gateway in front of the server (for example AWS Bedrock AgentCore's
# JWT authorizer) authenticates every request before it arrives.
# jwt: this server checks a bearer token on every request (auth.py, MCP_JWT_*).
AUTH_MODES = ("none", "platform", "jwt")
"""
_JWT_NOT_BUILT_IN = """    if auth == "jwt":
        raise TransportConfigError(
            "MCP_AUTH=jwt is not built into this server. Regenerate it with the docker "
            "deployment, or put it behind a gateway that checks tokens and set MCP_AUTH=platform"
        )
"""


def transport_module(*, jwt: bool = False) -> str:
    """``transport.py``; with ``jwt`` the server checks tokens itself (``MCP_AUTH=jwt``)."""
    if not jwt:
        return TRANSPORT_MODULE
    text = TRANSPORT_MODULE.replace(_PLATFORM_ONLY, _WITH_JWT).replace(_JWT_NOT_BUILT_IN, "")
    assert text != TRANSPORT_MODULE
    return text


__all__ = ["TRANSPORT_MODULE", "transport_module"]
