"""The generated project can report its live tools on any OS."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_wizard_generators.python_mcp import generate_python_mcp

ROOT = Path(__file__).parent
FIXTURE = ROOT / "fixtures" / "pets_tiny.ir.json"


def test_generated_verify_reports_tool_count(tmp_path: Path) -> None:
    """python -m <pkg>.verify prints tool count and names, and exits 0 when they match."""
    pytest.importorskip("mcp")
    pytest.importorskip("httpx")

    project = tmp_path / "pets"
    generate_python_mcp(
        json.loads(FIXTURE.read_text(encoding="utf-8")),
        ["listPets", "showPetById"],
        out_dir=project,
        package_name="pets_tiny_mcp",
    )

    env = {**os.environ, "PYTHONPATH": str(project / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "pets_tiny_mcp.verify"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["toolCount"] == 2
    assert report["expectedToolCount"] == 2
    assert [tool["name"] for tool in report["tools"]] == ["listpets", "showpetbyid"]
    assert report["missing"] == []
    assert report["unexpected"] == []
    assert report["manifestPath"].replace("\\", "/").endswith("generation-manifest.json")
