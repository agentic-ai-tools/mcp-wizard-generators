"""Contract tests for cross-platform local DX helpers."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_SH = [
    "scripts/bootstrap.sh",
    "scripts/test.sh",
    "scripts/lint.sh",
    "scripts/format.sh",
    "scripts/cli-smoke.sh",
    "scripts/clean.sh",
]

REQUIRED_PS1 = [
    "scripts/bootstrap.ps1",
    "scripts/test.ps1",
    "scripts/lint.ps1",
    "scripts/format.ps1",
    "scripts/cli-smoke.ps1",
    "scripts/clean.ps1",
]


def test_dual_helpers_exist_in_pairs() -> None:
    for rel in REQUIRED_SH + REQUIRED_PS1:
        path = ROOT / rel
        assert path.is_file(), f"missing helper: {rel}"


def test_makefile_documents_shell_overrides_and_targets() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    for needle in (
        "USE_SH",
        "USE_PS1",
        "MSYSTEM",
        "bootstrap",
        "test",
        "tox",
        "lint",
        "cli-smoke",
        "docs/local-dev.md",
    ):
        assert needle in text, f"Makefile missing {needle!r}"


def test_tox_ini_defines_pytest_and_lint() -> None:
    text = (ROOT / "tox.ini").read_text(encoding="utf-8")
    assert "pytest" in text
    assert "ruff" in text
    assert "py311" in text


def test_local_dev_doc_has_shell_matrix() -> None:
    text = (ROOT / "docs" / "local-dev.md").read_text(encoding="utf-8")
    for needle in (
        "macOS",
        "WSL",
        "Git Bash",
        "PowerShell",
        "Shell matrix",
        "mcp-wizard-gen",
        "forward slashes",
    ):
        assert needle in text, f"docs/local-dev.md missing {needle!r}"


def test_cli_smoke_scripts_avoid_unix_tmp() -> None:
    for rel in ("scripts/cli-smoke.sh", "scripts/cli-smoke.ps1"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "/tmp" not in text
        assert ".smoke-out" in text


def test_pyproject_console_script_and_dev_extras() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'mcp-wizard-gen = "mcp_wizard_generators.cli:main"' in text
    assert "pytest" in text
    assert "tox" in text
    assert "ruff" in text
