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


def _generate(project: Path) -> None:
    generate_python_mcp(
        json.loads(FIXTURE.read_text(encoding="utf-8")),
        ["listPets", "showPetById"],
        out_dir=project,
        package_name="pets_tiny_mcp",
    )


def _run_verify(project: Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(project / "src")}
    return subprocess.run(
        [sys.executable, "-m", "pets_tiny_mcp.verify"],
        cwd=cwd or project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_generated_verify_reports_tool_count(tmp_path: Path) -> None:
    """python -m <pkg>.verify prints tool count and names, and exits 0 when they match."""
    pytest.importorskip("mcp")
    pytest.importorskip("httpx")

    project = tmp_path / "pets"
    _generate(project)
    proc = _run_verify(project)
    assert proc.returncode == 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["toolCount"] == 2
    assert report["expectedToolCount"] == 2
    assert [tool["name"] for tool in report["tools"]] == ["listpets", "showpetbyid"]
    assert report["missing"] == []
    assert report["unexpected"] == []
    assert report["manifestPath"].replace("\\", "/").endswith("generation-manifest.json")


def test_verify_fails_when_a_repeated_expected_name_is_exposed_once(tmp_path: Path) -> None:
    """Set membership would pass; the live server must expose each expected name once per listing."""
    pytest.importorskip("mcp")
    pytest.importorskip("httpx")
    project = tmp_path / "pets"
    _generate(project)
    verify_py = project / "src" / "pets_tiny_mcp" / "verify.py"
    text = verify_py.read_text(encoding="utf-8")
    needle = r"{\"name\": \"listpets\""
    assert needle in text
    verify_py.write_text(text.replace(needle, needle + "}, " + needle, 1), encoding="utf-8")

    proc = _run_verify(project)
    assert proc.returncode == 1, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert report["toolCount"] == 2
    assert report["expectedToolCount"] == 3
    assert report["missing"] == ["listpets"]


def test_verify_fails_when_project_manifest_tools_differ(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    pytest.importorskip("httpx")
    project = tmp_path / "pets"
    _generate(project)
    manifest_path = project / "generation-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tools"][0]["name"] = "missingtool"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    proc = _run_verify(project)
    assert proc.returncode == 1, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    mismatch = report["manifestMismatch"]
    assert "listpets" in mismatch["missingFromManifest"]
    assert "missingtool" in mismatch["extraInManifest"]


def test_unrelated_ancestor_manifest_does_not_fail_verify(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    pytest.importorskip("httpx")
    ancestor = tmp_path / "ancestor"
    project = ancestor / "work" / "pets"
    _generate(project)
    (ancestor / "generation-manifest.json").write_text(
        json.dumps({"tools": [{"name": "not-this-server"}]}),
        encoding="utf-8",
    )
    elsewhere = ancestor / "elsewhere"
    elsewhere.mkdir()

    proc = _run_verify(project, cwd=elsewhere)
    assert proc.returncode == 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["toolCount"] == 2
    found = report["manifestPath"].replace("\\", "/")
    assert found.endswith("work/pets/generation-manifest.json")


def test_colliding_slugs_are_both_exposed(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    pytest.importorskip("httpx")
    project = tmp_path / "pets"
    generate_python_mcp(
        {
            "title": "Pets",
            "version": "1.0.0",
            "servers": ["https://example.invalid"],
            "operations": [
                {
                    "id": "listPets",
                    "operationId": "listPets",
                    "method": "get",
                    "path": "/pets",
                    "summary": "A",
                },
                {
                    "id": "listpets",
                    "operationId": "listpets",
                    "method": "get",
                    "path": "/animals",
                    "summary": "B",
                },
            ],
        },
        ["listPets", "listpets"],
        out_dir=project,
        package_name="pets_tiny_mcp",
    )
    proc = _run_verify(project)
    assert proc.returncode == 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert [tool["name"] for tool in report["tools"]] == ["listpets", "listpets_2"]
    assert report["toolCount"] == 2
