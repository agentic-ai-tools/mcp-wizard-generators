"""Install a generated local Python MCP server, start it, and call a tool."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from e2e_harness import ToolCall, UpstreamMock, install_project, invoke_stdio
from mcp_wizard_generators.python_mcp import generate_python_mcp

ROOT = Path(__file__).parent
FIXTURE = ROOT / "fixtures" / "pets_tiny.ir.json"
PACKAGE = "pets_tiny_mcp"
SCRIPT_NAME = "pets-tiny-mcp.exe" if sys.platform == "win32" else "pets-tiny-mcp"


def _result_text(result: object) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


@pytest.mark.e2e
def test_generated_server_installs_starts_and_serves_a_tool_call(tmp_path: Path) -> None:
    """Fresh venv install, stdio startup, and an MCP client call_tool against a mock API."""
    project = tmp_path / "pets"
    generate_python_mcp(
        json.loads(FIXTURE.read_text(encoding="utf-8")),
        ["listPets", "showPetById"],
        out_dir=project,
        package_name=PACKAGE,
    )

    venv_python = install_project(project, tmp_path / "venv")
    payload = [{"id": "pet-1", "name": "Fido"}]
    upstream = UpstreamMock()
    upstream.add("GET", "/pets", payload)
    with upstream as base_url:
        invoked = invoke_stdio(
            venv_python,
            ["-m", PACKAGE],
            {"API_BASE_URL": base_url},
            [ToolCall("listpets", {"limit": 1})],
        )

    assert "listpets" in invoked.tool_names
    assert "showpetbyid" in invoked.tool_names
    assert len(invoked.calls) == 1
    call = invoked.calls[0]
    assert not call.is_error
    assert "Fido" in _result_text(call)

    script = venv_python.parent / SCRIPT_NAME
    assert script.is_file()
