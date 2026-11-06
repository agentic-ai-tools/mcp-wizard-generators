"""TDD coverage for generated README venv activate matrix and MCP 2.x runtime."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_wizard_generators.models import IntermediateRep
from mcp_wizard_generators.python_mcp import generate_python_mcp_result

ROOT = Path(__file__).parent
FIXTURE = ROOT / "fixtures" / "pets_tiny.ir.json"


def _generate(package_name: str = "pets_tiny_mcp"):
    ir = IntermediateRep.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    return generate_python_mcp_result(
        ir,
        ["listPets", "showPetById"],
        package_name=package_name,
    )


def test_generated_readme_documents_venv_activate_os_matrix() -> None:
    """Fresh zip README must show correct activate paths per OS / shell."""
    result = _generate()
    readme = result.tree.get("README.md")
    assert readme is not None
    text = readme.content

    # Unix-like (including Git Bash on Linux / WSL): bin layout
    assert ".venv/bin/activate" in text
    assert re.search(
        r"(?i)(macOS|Linux|WSL|Unix).{0,80}\.venv/bin/activate"
        r"|\.venv/bin/activate.{0,120}(macOS|Linux|WSL|Unix|Git Bash on Linux)",
        text,
        re.DOTALL,
    )

    # Windows cmd / PowerShell: Scripts layout
    assert "Scripts" in text
    assert re.search(r"(?i)(PowerShell|cmd).{0,120}Scripts", text, re.DOTALL)

    # Git Bash on Windows: document the Windows venv Scripts path that typically works,
    # and do not present a Git Bash section that only hardcodes Unix bin (or only Scripts
    # while implying a Unix layout).
    assert re.search(r"(?i)Git Bash on Windows", text)
    win_git_bash = re.search(
        r"(?i)Git Bash on Windows.{0,400}",
        text,
        re.DOTALL,
    )
    assert win_git_bash is not None
    section = win_git_bash.group(0)
    assert re.search(r"Scripts[/\\]activate", section)
    # Cover bin as well when the environment uses a Unix-layout venv
    assert "bin/activate" in section or ".venv/bin/activate" in text


def test_generated_deps_pin_mcp_2() -> None:
    """Artifact deps must match MCPServer (mcp 2.x), not FastMCP-only 1.x."""
    result = _generate()
    pyproject = result.tree.get("pyproject.toml")
    requirements = result.tree.get("requirements.txt")
    assert pyproject is not None and requirements is not None

    for label, content in (
        ("pyproject.toml", pyproject.content),
        ("requirements.txt", requirements.content),
    ):
        assert "mcp" in content, label
        # Lower bound is mcp 2 (FastMCP removed); keep an upper bound under 3.
        assert re.search(r"mcp\s*>=\s*2(\.0(\.0)?)?", content), f"{label} must pin mcp>=2"
        assert re.search(r"mcp\s*>=\s*2[^\"'\n]*,\s*<\s*3", content), f"{label} must keep mcp>=2,<3"


def test_generated_server_imports_mcpserver_not_fastmcp_only() -> None:
    result = _generate()
    server = result.tree.get("src/pets_tiny_mcp/server.py")
    assert server is not None
    content = server.content
    assert "from mcp.server.mcpserver import MCPServer" in content
    # Must not be FastMCP-only; a 1.x fallback shim is optional.
    assert "mcp.server.fastmcp" not in content or "ImportError" in content or "except" in content


def test_generated_server_imports_against_pinned_mcp(tmp_path: Path) -> None:
    """Generate → materialize → import server module on the installed mcp 2.x SDK."""
    pytest.importorskip("mcp")
    pytest.importorskip("httpx")
    import importlib.metadata as metadata

    mcp_version = metadata.version("mcp")
    major = int(mcp_version.split(".", 1)[0])
    assert major >= 2, f"test env should use mcp 2.x; got {mcp_version}"

    # Canonical import path from the MCP 2 migration guide
    from mcp.server.mcpserver import MCPServer  # noqa: F401

    result = _generate(package_name="pets_tiny_mcp")
    out = tmp_path / "generated"
    for entry in result.tree.entries:
        path = out / entry.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry.content, encoding="utf-8")

    src = out / "src"
    env = {**os.environ, "PYTHONPATH": str(src)}
    # Import the generated package the same way Cursor would load `-m pets_tiny_mcp`
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio\n"
            "from pets_tiny_mcp.server import mcp, main\n"
            "assert mcp is not None\n"
            "assert callable(main)\n"
            "tools = asyncio.run(mcp.list_tools())\n"
            "names = {t.name for t in tools}\n"
            "assert 'listpets' in names and 'showpetbyid' in names\n"
            "print('ok', type(mcp).__name__, sorted(names))\n",
        ],
        cwd=out,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    assert "MCPServer" in proc.stdout
    assert "listpets" in proc.stdout


def test_generated_cursor_mcp_json_still_valid() -> None:
    result = _generate()
    cursor = result.tree.get(".cursor/mcp.json")
    assert cursor is not None
    cfg = json.loads(cursor.content)
    server = cfg["mcpServers"]["pets_tiny_mcp"]
    assert server["command"] == "python"
    assert server["args"] == ["-m", "pets_tiny_mcp"]
    assert "API_BASE_URL" in server["env"]
