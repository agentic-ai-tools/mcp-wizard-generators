"""Install a generated MCP project and call it over stdio.

Used by end-to-end tests. Not a public library API. The MCP client runs in the
test process; only the generated server runs in the fresh virtualenv.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_PIP_TIMEOUT_SECONDS = 300
_SESSION_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ToolCall:
    """One ``tools/call`` issued after the server has initialized."""

    name: str
    arguments: dict[str, Any] | None = None


@dataclass(frozen=True)
class InvokeResult:
    """Tool names from ``tools/list`` and the ``tools/call`` results, in order."""

    tool_names: list[str]
    calls: list[Any]


def venv_python(venv_dir: Path) -> Path:
    """Interpreter path inside a virtualenv (Windows ``Scripts`` or POSIX ``bin``)."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def install_project(project_dir: Path, venv_dir: Path) -> Path:
    """Create ``venv_dir`` and ``pip install`` the generated project into it.

    Returns the virtualenv interpreter. Needs network access to resolve the
    generated project's dependencies.
    """
    _run([sys.executable, "-m", "venv", str(venv_dir)], timeout=120)
    python = venv_python(venv_dir)
    if not python.is_file():
        raise RuntimeError(f"venv interpreter missing: {python}")
    _run(
        [str(python), "-m", "pip", "install", str(project_dir)],
        timeout=_PIP_TIMEOUT_SECONDS,
    )
    return python


class UpstreamMock:
    """Local JSON API bound to ``127.0.0.1`` on an ephemeral port.

    Register ``(method, path)`` before entering. Query strings are ignored, so
    ``GET /pets?limit=1`` matches a route stored as ``GET /pets``.
    """

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], Any] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def add(self, method: str, path: str, body: Any) -> None:
        self._routes[(method.upper(), path)] = body

    def __enter__(self) -> str:
        handler = _handler_for(self._routes)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def invoke_stdio(
    python: Path,
    args: list[str],
    env: dict[str, str],
    calls: list[ToolCall],
) -> InvokeResult:
    """Start ``python`` + ``args`` over stdio and issue MCP ``initialize``, ``list_tools``, and ``call_tool``.

    ``env`` is merged over ``os.environ`` and passed to the server process.
    ``PYTHONPATH`` is dropped so imports resolve from the virtualenv install.
    The MCP client's default inherited variables (``PATH``, ``SYSTEMROOT``, …)
    still apply underneath that map.
    """
    return asyncio.run(_invoke_stdio(python, args, env, calls))


def _run(cmd: list[str], *, timeout: int) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {cmd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def _handler_for(routes: dict[tuple[str, str], Any]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._respond("GET")

        def do_POST(self) -> None:
            self._respond("POST")

        def do_PUT(self) -> None:
            self._respond("PUT")

        def do_DELETE(self) -> None:
            self._respond("DELETE")

        def _respond(self, method: str) -> None:
            path = urlsplit(self.path).path
            body = routes.get((method, path))
            if body is None:
                payload = b""
                status = 404
                content_type = "text/plain"
            else:
                payload = json.dumps(body).encode("utf-8")
                status = 200
                content_type = "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


async def _invoke_stdio(
    python: Path,
    args: list[str],
    env: dict[str, str],
    calls: list[ToolCall],
) -> InvokeResult:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    merged = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    merged.update(env)
    params = StdioServerParameters(command=str(python), args=list(args), env=merged)
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=_SESSION_TIMEOUT_SECONDS
        ) as session:
            await session.initialize()
            listed = await session.list_tools()
            results = []
            for call in calls:
                results.append(
                    await session.call_tool(
                        call.name,
                        call.arguments,
                        read_timeout_seconds=_SESSION_TIMEOUT_SECONDS,
                    )
                )
            return InvokeResult(tool_names=[tool.name for tool in listed.tools], calls=results)
