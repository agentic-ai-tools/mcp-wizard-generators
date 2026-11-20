"""Transport settings, and a real streamable-HTTP round trip on this machine only."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pets_tiny_mcp.transport import TransportConfigError, load_transport_config

PROJECT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT / "generation-manifest.json"
HTTP = {"MCP_TRANSPORT": "streamable-http"}


def test_stdio_is_the_default() -> None:
    assert load_transport_config({}).transport == "stdio"
    assert load_transport_config({"MCP_PORT": "not-a-port"}).transport == "stdio"


def test_http_defaults() -> None:
    cfg = load_transport_config(HTTP)
    assert (cfg.host, cfg.port, cfg.path, cfg.auth) == ("127.0.0.1", 8000, "/mcp", "none")


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({"MCP_TRANSPORT": "sse"}, "MCP_TRANSPORT"),
        ({"MCP_PORT": "0"}, "MCP_PORT"),
        ({"MCP_PATH": "mcp"}, "MCP_PATH"),
        ({"MCP_HOST": "0.0.0.0"}, "without authentication"),
    ],
)
def test_bad_settings_are_refused(env: dict[str, str], match: str) -> None:
    with pytest.raises(TransportConfigError, match=match):
        load_transport_config({**HTTP, **env})


def test_platform_auth_allows_a_public_bind() -> None:
    cfg = load_transport_config({**HTTP, "MCP_HOST": "0.0.0.0", "MCP_AUTH": "platform"})
    assert cfg.host == "0.0.0.0"


def _server_env(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MCP_")}
    # Placeholders so the startup check passes; listing tools never calls the API.
    env.setdefault("API_BASE_URL", "https://api.test")
    env.setdefault("API_KEY", "placeholder")
    env.setdefault("OAUTH_TOKEN_URL", "https://auth.test/token")
    env.setdefault("OAUTH_CLIENT_ID", "placeholder")
    env.setdefault("OAUTH_CLIENT_SECRET", "placeholder")
    # The server runs as a child process, so pytest's pythonpath setting does not reach it.
    paths = [str(PROJECT / "src"), env.get("PYTHONPATH", "")]
    env["PYTHONPATH"] = os.pathsep.join(path for path in paths if path)
    env.update(extra)
    return env


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _list_tools(url: str) -> list[str]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            return [tool.name for tool in (await session.list_tools()).tools]


def test_http_round_trip_lists_the_generated_tools() -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "pets_tiny_mcp"],
        env=_server_env(MCP_TRANSPORT="streamable-http", MCP_PORT=str(port)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            assert server.poll() is None, server.communicate()[1]
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                assert time.monotonic() < deadline, "server did not start listening"
                time.sleep(0.1)
        names = asyncio.run(_list_tools(f"http://127.0.0.1:{port}/mcp"))
    finally:
        server.terminate()
        try:
            server.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate()
    expected = [tool["name"] for tool in json.loads(MANIFEST.read_text(encoding="utf-8"))["tools"]]
    assert sorted(names) == sorted(expected)


def test_public_bind_without_auth_does_not_start() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pets_tiny_mcp"],
        env=_server_env(MCP_TRANSPORT="streamable-http", MCP_HOST="0.0.0.0"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 2
    assert "without authentication" in proc.stderr
