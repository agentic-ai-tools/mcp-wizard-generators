"""Public-repo hygiene: LICENSE, security docs, Dependabot, CI Semgrep/Codecov/PyPI."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"
LICENSE = ROOT / "LICENSE"
SECURITY = ROOT / "SECURITY.md"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"
CODE_OF_CONDUCT = ROOT / "CODE_OF_CONDUCT.md"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SEMGREP_WORKFLOW = ROOT / ".github" / "workflows" / "semgrep.yml"
PUBLISH_PYPI_WORKFLOW = ROOT / ".github" / "workflows" / "publish-pypi.yml"
CODECOV_YML = ROOT / "codecov.yml"


def _load_pyproject() -> dict:
    import tomllib

    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def test_distribution_and_import_names() -> None:
    data = _load_pyproject()
    assert data["project"]["name"] == "mcp-wizard-generators"
    hatch_wheel = data["tool"]["hatch"]["build"]["targets"]["wheel"]
    packages = hatch_wheel.get("packages") or []
    assert any("mcp_wizard_generators" in str(p) for p in packages)
    assert (ROOT / "src" / "mcp_wizard_generators" / "__init__.py").is_file()
    assert (ROOT / "src" / "mcp_wizard_generators").is_dir()


def test_project_urls_point_at_public_github() -> None:
    urls = _load_pyproject()["project"]["urls"]
    expected = "https://github.com/agentic-ai-tools/mcp-wizard-generators"
    assert urls["Homepage"] == expected
    assert urls["Repository"] == expected
    assert urls["Issues"].startswith(expected)


def test_mit_license_file_present() -> None:
    assert LICENSE.is_file(), "LICENSE required for a public repo"
    text = LICENSE.read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "Copyright (c) 2026 agentic-ai-tools" in text
    assert "Apache" not in text
    license_field = _load_pyproject()["project"]["license"]
    if isinstance(license_field, dict):
        assert license_field.get("text") == "MIT"
    else:
        assert str(license_field) == "MIT"
    classifiers = _load_pyproject()["project"].get("classifiers") or []
    assert "License :: OSI Approved :: MIT License" in classifiers
    assert not any("Apache" in c for c in classifiers)


def test_code_of_conduct_present() -> None:
    assert CODE_OF_CONDUCT.is_file(), "CODE_OF_CONDUCT.md required for a public repo"
    text = CODE_OF_CONDUCT.read_text(encoding="utf-8")
    assert "Contributor Covenant" in text
    assert "SECURITY.md" in text or "security" in text.lower()


def test_security_and_contributing_docs() -> None:
    assert SECURITY.is_file()
    security = SECURITY.read_text(encoding="utf-8")
    assert "Security" in security
    assert "github.com/agentic-ai-tools/mcp-wizard-generators" in security

    assert CONTRIBUTING.is_file()
    contributing = CONTRIBUTING.read_text(encoding="utf-8")
    assert "AGENTS.md" in contributing or "pull request" in contributing.lower()
    assert "test" in contributing.lower()
    assert "CODE_OF_CONDUCT.md" in contributing
    assert "MIT" in contributing
    assert "LICENSE" in contributing


def test_readme_links_community_docs() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "LICENSE" in readme
    assert "CONTRIBUTING.md" in readme
    assert "CODE_OF_CONDUCT.md" in readme
    assert "SECURITY.md" in readme


def test_dependabot_github_actions_and_pip() -> None:
    assert DEPENDABOT.is_file()
    text = DEPENDABOT.read_text(encoding="utf-8")
    assert (
        "package-ecosystem: github-actions" in text or 'package-ecosystem: "github-actions"' in text
    )
    assert "package-ecosystem: pip" in text or 'package-ecosystem: "pip"' in text


def test_ci_runs_lint_test_coverage_and_codecov() -> None:
    assert CI_WORKFLOW.is_file(), f"missing {CI_WORKFLOW}"
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "ruff format --check" in text or "make lint" in text or "ruff check" in text
    assert "pytest" in text
    assert "--cov" in text or "coverage" in text.lower()
    assert "coverage.xml" in text or "lcov" in text.lower()
    assert "codecov/codecov-action" in text
    assert "CODECOV_TOKEN" in text
    # PR CI must not require PyPI tokens / Trusted Publisher secrets.
    assert "PYPI_TOKEN" not in text
    assert "TWINE_PASSWORD" not in text


def test_coverage_floor_configured() -> None:
    data = _load_pyproject()
    tool = data.get("tool") or {}
    coverage = tool.get("coverage") or {}
    report = coverage.get("report") or {}
    pytest_cfg = tool.get("pytest") or {}
    ini = pytest_cfg.get("ini_options") or {}
    addopts = str(ini.get("addopts") or "")
    fail_under = report.get("fail_under")
    assert fail_under is not None or "--cov-fail-under" in addopts, (
        "configure coverage fail_under (tool.coverage.report) or --cov-fail-under"
    )
    floor = (
        int(fail_under)
        if fail_under is not None
        else int(
            re.search(r"--cov-fail-under[= ](\d+)", addopts).group(1)  # type: ignore[union-attr]
        )
    )
    assert floor >= 80, f"coverage floor too low for a library: {floor}"

    optional = data["project"].get("optional-dependencies") or {}
    joined = " ".join(dep for deps in optional.values() for dep in deps)
    assert "pytest-cov" in joined


def test_semgrep_workflow_supports_app_token() -> None:
    assert SEMGREP_WORKFLOW.is_file(), f"missing {SEMGREP_WORKFLOW}"
    text = SEMGREP_WORKFLOW.read_text(encoding="utf-8")
    assert "SEMGREP_APP_TOKEN" in text
    # Current official path: semgrep/semgrep image + `semgrep ci`, or legacy action.
    uses_container = "semgrep/semgrep" in text and "semgrep ci" in text
    uses_action = "semgrep/semgrep-action" in text
    assert uses_container or uses_action, "must use Semgrep container CI or semgrep-action"
    # Fail closed on findings for library CI (App policy or OSS --error).
    assert "semgrep ci" in text or "--error" in text or "semgrep-action" in text


def test_trusted_publisher_stub_oidc_no_pr_secrets() -> None:
    assert PUBLISH_PYPI_WORKFLOW.is_file(), f"missing {PUBLISH_PYPI_WORKFLOW}"
    text = PUBLISH_PYPI_WORKFLOW.read_text(encoding="utf-8")
    assert "id-token: write" in text or "id-token:write" in text
    assert "pypa/gh-action-pypi-publish" in text
    assert "testpypi" in text.lower() or "test.pypi.org" in text
    assert "mcp-wizard-generators" in text or "agentic-ai-tools" in text
    # Must not use long-lived password tokens.
    assert "TWINE_PASSWORD" not in text
    assert "PYPI_API_TOKEN" not in text
    # Not on pull_request (PR CI stays secret-free for publish).
    assert "pull_request" not in text


def test_readme_public_install_and_badges() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "pip install mcp-wizard-generators" in readme
    assert "git+https://github.com/agentic-ai-tools/mcp-wizard-generators.git@" in readme
    assert "codecov" in readme.lower()
    assert "semgrep" in readme.lower()
    assert "agentic-ai-tools" in readme
    # Placeholders / badges for public dashboard links
    assert "badge" in readme.lower() or "codecov.io" in readme or "img.shields.io" in readme


def test_codecov_config_optional_but_sane_if_present() -> None:
    if not CODECOV_YML.is_file():
        return
    text = CODECOV_YML.read_text(encoding="utf-8")
    assert "coverage" in text.lower() or "target" in text.lower()
