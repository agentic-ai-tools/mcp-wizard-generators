"""ADR-026 step 3: every python-mcp server can also speak streamable HTTP (stdio stays default)."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

from mcp_wizard_generators.models import IntermediateRep
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.python_mcp import generate_python_mcp, generate_python_mcp_result
from mcp_wizard_generators.registry import get_default_registry

ROOT = Path(__file__).parent
IR = json.loads((ROOT / "fixtures" / "pets_tiny.ir.json").read_text(encoding="utf-8"))
PKG = "pets_tiny_mcp"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("http") / "pets"
    generate_python_mcp(IR, ["listPets", "showPetById"], out_dir=out, package_name=PKG)
    return out


@pytest.fixture(scope="module")
def transport(project: Path) -> ModuleType:
    """The generated transport module is stdlib-only, so it loads on its own."""
    path = project / "src" / PKG / "transport.py"
    spec = importlib.util.spec_from_file_location("generated_transport", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


# ---- transport configuration --------------------------------------------------------


def test_stdio_is_the_default_and_ignores_http_settings(transport: ModuleType) -> None:
    cfg = transport.load_transport_config({})
    assert cfg.transport == "stdio"
    # An HTTP setting left in .env must not break a stdio run.
    assert transport.load_transport_config({"MCP_PORT": "nope"}).transport == "stdio"


def test_http_defaults_match_agentcore_path_and_port(transport: ModuleType) -> None:
    cfg = transport.load_transport_config({"MCP_TRANSPORT": "streamable-http"})
    assert (cfg.host, cfg.port, cfg.path, cfg.auth) == ("127.0.0.1", 8000, "/mcp", "none")
    assert cfg.url == "http://127.0.0.1:8000/mcp"
    assert cfg.allowed_hosts == ()


def test_platform_auth_allows_a_public_bind(transport: ModuleType) -> None:
    cfg = transport.load_transport_config(
        {
            "MCP_TRANSPORT": "streamable-http",
            "MCP_HOST": "0.0.0.0",
            "MCP_AUTH": "platform",
            "MCP_ALLOWED_HOSTS": "mcp.example.com, api.example.com:443",
        }
    )
    assert cfg.host == "0.0.0.0" and cfg.auth == "platform"
    assert cfg.allowed_hosts == ("mcp.example.com", "api.example.com:443")


def test_ipv6_loopback_url_is_bracketed(transport: ModuleType) -> None:
    cfg = transport.load_transport_config({"MCP_TRANSPORT": "streamable-http", "MCP_HOST": "::1"})
    assert cfg.url == "http://[::1]:8000/mcp"


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({"MCP_TRANSPORT": "sse"}, "MCP_TRANSPORT"),
        ({"MCP_PORT": "0"}, "MCP_PORT"),
        ({"MCP_PORT": "70000"}, "MCP_PORT"),
        ({"MCP_PORT": "http"}, "MCP_PORT"),
        ({"MCP_PATH": "mcp"}, "MCP_PATH"),
        ({"MCP_PATH": "/mcp?x=1"}, "MCP_PATH"),
        ({"MCP_AUTH": "basic"}, "MCP_AUTH"),
        ({"MCP_AUTH": "jwt"}, "not built into this server"),
        ({"MCP_HOST": "0.0.0.0"}, "without authentication"),
        ({"MCP_HOST": "192.168.1.5", "MCP_AUTH": "none"}, "without authentication"),
    ],
)
def test_bad_transport_settings_are_refused(
    transport: ModuleType, env: dict[str, str], match: str
) -> None:
    with pytest.raises(transport.TransportConfigError, match=match):
        transport.load_transport_config({"MCP_TRANSPORT": "streamable-http", **env})


# ---- server wiring ------------------------------------------------------------------


def test_server_runs_stateless_http_when_asked(project: Path) -> None:
    server = (project / "src" / PKG / "server.py").read_text(encoding="utf-8")
    ast.parse(server)
    assert "load_transport_config()" in server
    assert '"streamable-http"' in server
    assert "stateless_http=True" in server
    assert 'mcp.run(transport="stdio")' in server


def test_capabilities_report_both_transports_and_their_env_vars() -> None:
    plugin = get_default_registry().server("python-mcp")
    assert plugin.transports == frozenset({"stdio", "streamable-http"})
    result = generate_python_mcp_result(IR, ["listPets"], package_name=PKG)
    assert f"src/{PKG}/transport.py" in result.tree.paths()
    from mcp_wizard_generators.models import GenerateContext
    from mcp_wizard_generators.toolmap import build_tool_map

    ir = IntermediateRep.from_dict(IR)
    ctx = GenerateContext(
        ir=ir, selected_operation_ids=["listPets"], tool_map=build_tool_map(ir, ["listPets"], None)
    )
    _, caps = plugin.generate(ctx)
    names = {v.name for v in caps.env_vars}
    assert {"MCP_HOST", "MCP_PORT", "MCP_PATH", "MCP_AUTH", "MCP_ALLOWED_HOSTS"} <= names
    assert not caps.secret_env_vars


def test_env_example_documents_http_settings_as_comments(project: Path) -> None:
    lines = (project / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "MCP_TRANSPORT=stdio" in lines
    for name in ("MCP_HOST", "MCP_PORT", "MCP_PATH", "MCP_AUTH", "MCP_ALLOWED_HOSTS"):
        assert any(line.startswith(f"# {name}=") for line in lines), name


def test_a_tool_cannot_shadow_the_transport_loader() -> None:
    ir = {
        "title": "Shadow",
        "version": "1",
        "servers": ["https://api.example.com"],
        "operations": [
            {
                "id": "load_transport_config",
                "operationId": "load_transport_config",
                "method": "get",
                "path": "/x",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    }
    result = generate(IntermediateRep.from_dict(ir), package_name="shadow_mcp")
    server = result.tree.get("src/shadow_mcp/server.py").content
    defined = [n.name for n in ast.parse(server).body if isinstance(n, ast.FunctionDef)]
    assert "load_transport_config" not in defined


# ---- running server + remote verify -------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _env(project: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MCP_")}
    env.update(PYTHONPATH=str(project / "src"), API_BASE_URL="https://api.example.com", **extra)
    return env


def _wait_for_port(port: int, proc: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"server exited early: {proc.communicate()}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("server did not start listening")


def _verify(project: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", f"{PKG}.verify", *args],
        cwd=project,
        env=_env(project, **env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


@pytest.fixture
def http_server(project: Path) -> Iterator[str]:
    pytest.importorskip("mcp")
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", PKG],
        env=_env(project, MCP_TRANSPORT="streamable-http", MCP_PORT=str(port)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_port(port, proc)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()


def test_remote_verify_lists_tools_over_http(project: Path, http_server: str) -> None:
    proc = _verify(project, "--url", http_server)
    assert proc.returncode == 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["transport"] == "streamable-http"
    assert report["url"] == http_server
    assert [t["name"] for t in report["tools"]] == ["listpets", "showpetbyid"]


def test_public_bind_without_auth_refuses_to_start(project: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", PKG],
        env=_env(project, MCP_TRANSPORT="streamable-http", MCP_HOST="0.0.0.0"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 2
    assert "without authentication" in proc.stderr


class _Status(BaseHTTPRequestHandler):
    status = 401

    def _reply(self) -> None:
        self.send_response(self.status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = do_DELETE = _reply

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def status_server() -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Status)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(("status", "hint"), [(401, "MCP_BEARER_TOKEN"), (403, "audience")])
def test_remote_verify_explains_auth_failures(
    project: Path, status_server: ThreadingHTTPServer, status: int, hint: str
) -> None:
    pytest.importorskip("mcp")
    _Status.status = status
    url = f"http://127.0.0.1:{status_server.server_address[1]}/mcp"
    proc = _verify(project, "--url", url, MCP_BEARER_TOKEN="tok-SECRET-123")
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert str(status) in report["error"]
    assert hint in report["hint"]
    assert "tok-SECRET-123" not in proc.stdout + proc.stderr


def test_remote_verify_explains_connection_refused(project: Path) -> None:
    pytest.importorskip("mcp")
    proc = _verify(project, "--url", f"http://127.0.0.1:{_free_port()}/mcp")
    assert proc.returncode == 1
    assert "running" in json.loads(proc.stdout)["hint"]


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("ftp://example.com/mcp", "http or https"),
        ("https://user@example.com/mcp", "user name"),
        ("http://mcp.example.com/mcp", "plain http"),
    ],
)
def test_remote_verify_rejects_unsafe_urls_before_connecting(
    project: Path, url: str, match: str
) -> None:
    proc = _verify(project, "--url", url, MCP_BEARER_TOKEN="tok")
    assert proc.returncode == 1
    assert match in json.loads(proc.stdout)["error"]
