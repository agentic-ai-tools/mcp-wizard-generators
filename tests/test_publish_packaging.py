"""Publish-ready packaging: metadata, stable APIs, and wheel/sdist contents."""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"
MAKEFILE = ROOT / "Makefile"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish-github-packages.yml"
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# First package-ready release marker (consumers can pin this SemVer).
MIN_PACKAGE_READY = (0, 3, 0)

# Subpackages that must ship in the wheel (no silent missing package data).
REQUIRED_WHEEL_MODULES = (
    "mcp_wizard_generators/__init__.py",
    "mcp_wizard_generators/python_mcp.py",
    "mcp_wizard_generators/package_name.py",
    "mcp_wizard_generators/packaging/ziputil.py",
    "mcp_wizard_generators/plugins/python_mcp/plugin.py",
    "mcp_wizard_generators/plugins/agentcore/plugin.py",
    "mcp_wizard_generators/rules/cursor.py",
    "mcp_wizard_generators/rules/anthropic.py",
)


def _load_pyproject() -> dict:
    import tomllib

    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def _parse_semver_core(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.match(version)
    assert match, f"not SemVer: {version}"
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def test_pyproject_publish_metadata() -> None:
    data = _load_pyproject()
    project = data["project"]

    assert project["name"] == "mcp-wizard-generators"
    version = str(project["version"])
    assert SEMVER_RE.match(version), version
    assert _parse_semver_core(version) >= MIN_PACKAGE_READY, (
        f"package-ready marker requires >= {'.'.join(map(str, MIN_PACKAGE_READY))}, got {version}"
    )
    assert project["requires-python"].startswith(">=")
    assert "3.11" in project["requires-python"]

    license_field = project["license"]
    if isinstance(license_field, dict):
        assert (
            "Apache" in license_field.get("text", "") or license_field.get("text") == "Apache-2.0"
        )
    else:
        assert str(license_field) in {"Apache-2.0", "Apache 2.0"}

    urls = project.get("urls") or {}
    assert "Repository" in urls or "Homepage" in urls
    assert "agentic-ai-tools/mcp-wizard-generators" in " ".join(urls.values())

    hatch_wheel = (
        data.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {})
    )
    assert hatch_wheel, "wheel build target must be configured"
    # Explicit package / package-data knobs so plugins+rules ship in the wheel.
    assert (
        hatch_wheel.get("packages")
        or hatch_wheel.get("only-include")
        or hatch_wheel.get("force-include")
    )

    optional = project.get("optional-dependencies") or {}
    # build/twine needed for make dist + publish path
    joined = " ".join(dep for deps in optional.values() for dep in deps)
    assert "build" in joined
    assert "twine" in joined


def test_version_matches_package_dunder() -> None:
    data = _load_pyproject()
    project_version = str(data["project"]["version"])
    pkg = importlib.import_module("mcp_wizard_generators")
    assert pkg.__version__ == project_version


def test_stable_public_apis_exportable() -> None:
    pkg = importlib.import_module("mcp_wizard_generators")
    assert callable(pkg.generate_python_mcp)
    assert callable(pkg.validate_package_name)
    assert "generate_python_mcp" in pkg.__all__
    assert "validate_package_name" in pkg.__all__


def test_make_dist_target_documented() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert re.search(r"(?m)^dist:", makefile), "Makefile must define a dist target"
    assert "python -m build" in makefile or "pyproject-build" in makefile
    help_block = makefile
    assert "make dist" in help_block or "dist" in help_block

    readme = README.read_text(encoding="utf-8")
    assert re.search(r"(?m)^## Publish\b", readme), "README needs a ## Publish section"
    assert "pypi.pkg.github.com/agentic-ai-tools" in readme
    assert "make dist" in readme


def test_github_packages_publish_workflow_stub() -> None:
    assert PUBLISH_WORKFLOW.is_file(), f"missing workflow stub: {PUBLISH_WORKFLOW}"
    text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert "pypi.pkg.github.com/agentic-ai-tools" in text
    assert "tags:" in text or "tags'" in text or "tags:" in text
    assert "packages: write" in text or "packages:write" in text
    assert "twine" in text
    assert "build" in text


def test_make_dist_builds_wheel_and_sdist(tmp_path: Path) -> None:
    """Build artifacts via python -m build (same as make dist)."""
    dist_dir = tmp_path / "dist"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--outdir",
            str(dist_dir),
            str(ROOT),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            "python -m build failed (install build via pip install build / make bootstrap).\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    version = str(_load_pyproject()["project"]["version"])
    wheel = dist_dir / f"mcp_wizard_generators-{version}-py3-none-any.whl"
    sdist = dist_dir / f"mcp_wizard_generators-{version}.tar.gz"
    assert wheel.is_file(), f"missing wheel: {wheel}; got {[p.name for p in dist_dir.iterdir()]}"
    assert sdist.is_file(), f"missing sdist: {sdist}"

    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
    for member in REQUIRED_WHEEL_MODULES:
        assert member in names, f"wheel missing package data member: {member}"
