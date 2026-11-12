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
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish-github-release.yml"
PUBLISH_PYPI_WORKFLOW = ROOT / ".github" / "workflows" / "publish-pypi.yml"
QUALITY_WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
COMPUTE_VERSION_SCRIPT = ROOT / "scripts" / "compute_pep440_version.py"
LEGACY_PACKAGES_WORKFLOW = ROOT / ".github" / "workflows" / "publish-github-packages.yml"
# Live endpoint usage (install/upload) — warning prose may still name the hosts.
GHP_PYPI_ENDPOINT_RE = re.compile(
    r"(?:--index-url|--repository-url)\s+https?://(?:upload\.)?pypi\.pkg\.github\.com"
    r"|https://(?:upload\.)?pypi\.pkg\.github\.com/[^\s`)]+/",
    re.IGNORECASE,
)
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
        assert license_field.get("text") == "MIT"
    else:
        assert str(license_field) == "MIT"

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
    # build/twine needed for make dist + publish path; packaging for PEP 440 script
    joined = " ".join(dep for deps in optional.values() for dep in deps)
    assert "build" in joined
    assert "twine" in joined
    assert "packaging" in joined
    assert COMPUTE_VERSION_SCRIPT.is_file(), f"missing {COMPUTE_VERSION_SCRIPT}"


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
    assert "make dist" in readme
    # GitHub Packages has no PyPI registry — do not document live install/upload endpoints.
    assert not GHP_PYPI_ENDPOINT_RE.search(readme), (
        "README must not give live *.pypi.pkg.github.com install/upload URLs"
    )
    assert "does not support" in readme.lower() and "python" in readme.lower(), (
        "README Publish must note that GitHub Packages does not support Python/PyPI"
    )
    assert "pip install mcp-wizard-generators" in readme
    assert "git+https://github.com/agentic-ai-tools/mcp-wizard-generators.git@" in readme
    assert "Release" in readme
    assert "agentic-ai-tools" in readme
    # Branch → version → index matrix
    assert ".dev" in readme
    assert "rc" in readme.lower() or "release candidate" in readme.lower()
    assert "TestPyPI" in readme
    assert "release/X.Y.Z" in readme or "release/`X.Y.Z`" in readme or "`release/" in readme


def test_github_release_publish_workflow() -> None:
    """Tag CI builds artifacts and attaches them to a GitHub Release (not GHP PyPI)."""
    assert not LEGACY_PACKAGES_WORKFLOW.is_file(), (
        "legacy publish-github-packages.yml must be removed/renamed "
        "(GitHub Packages has no PyPI registry)"
    )
    assert PUBLISH_WORKFLOW.is_file(), f"missing release workflow: {PUBLISH_WORKFLOW}"
    text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert not GHP_PYPI_ENDPOINT_RE.search(text), (
        "workflow must not twine-upload to *.pypi.pkg.github.com"
    )
    assert "twine upload" not in text
    assert "tags:" in text
    assert "python -m build" in text or "build" in text
    assert "twine check" in text or "twine" in text
    # Release asset upload via GITHUB_TOKEN (works); not packages: write for a fake PyPI.
    assert "contents: write" in text or "contents:write" in text
    assert "gh release" in text or "softprops/action-gh-release" in text or "upload-release" in text
    assert "packages: write" not in text and "packages:write" not in text


def test_reusable_quality_workflow_and_ci_wiring() -> None:
    assert QUALITY_WORKFLOW.is_file(), f"missing {QUALITY_WORKFLOW}"
    quality = QUALITY_WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_call" in quality
    assert "ruff" in quality
    assert "pytest" in quality
    assert "3.11" in quality and "3.12" in quality
    assert "semgrep" in quality.lower()

    assert CI_WORKFLOW.is_file(), f"missing {CI_WORKFLOW}"
    ci = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "quality.yml" in ci
    assert "workflow_call" in quality
    assert "release/**" in ci or "release/*" in ci


def test_publish_pypi_branch_channel_workflow() -> None:
    """Branch-based publish: quality → build → TestPyPI; release also → PyPI."""
    assert PUBLISH_PYPI_WORKFLOW.is_file(), f"missing {PUBLISH_PYPI_WORKFLOW}"
    text = PUBLISH_PYPI_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch" in text
    assert "pypa/gh-action-pypi-publish" in text
    assert "id-token: write" in text or "id-token:write" in text
    assert "environment: testpypi" in text or "environment:\n      name: testpypi" in text
    assert "environment: pypi" in text or re.search(r"environment:\s*\n\s+name:\s*pypi", text)
    assert "TWINE_PASSWORD" not in text
    assert "PYPI_API_TOKEN" not in text
    # Push / dispatch only — never wire PR events (keeps PR CI free of OIDC upload).
    on_block = text.split("jobs:", 1)[0]
    assert "pull_request" not in on_block

    # Push triggers: main, release/**, feature branches; skip dependabot.
    assert "main" in text
    assert "release/**" in text or "release/*" in text
    assert "dependabot" in text.lower()
    # Primary path is branch push, not tag-only.
    assert "compute_pep440_version" in text
    assert "quality.yml" in text or "needs: quality" in text or "needs:\n      - quality" in text

    # Quality before any publish; TestPyPI for all; PyPI for release only.
    assert "test.pypi.org" in text or "testpypi" in text.lower()
    # Opt-in OIDC upload so unset/misconfigured Trusted Publisher does not
    # fail feature-branch push checks associated with open PRs.
    assert "ENABLE_PYPI_PUBLISH == 'true'" in text or 'ENABLE_PYPI_PUBLISH == "true"' in text
    assert "version.txt" in text
    assert "__init__.py" in text or "__version__" in text
    assert "twine check" in text or "twine" in text
    assert "python -m build" in text or "pyproject-build" in text


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
