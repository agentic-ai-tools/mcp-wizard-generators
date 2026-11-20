"""ADR-026 step 5: GitHub Actions, deploy scripts, Dependabot and DEPLOY.md."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from mcp_wizard_generators.models import DeploymentConfig, InboundAuthConfig, IntermediateRep
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.plugins.deploy import constants

ROOT = Path(__file__).parent
IR = json.loads((ROOT / "fixtures" / "pets_tiny.ir.json").read_text(encoding="utf-8"))
PKG = "pets_tiny_mcp"
DISCOVERY = "https://login.example.com/tenant/.well-known/openid-configuration"
JWT = InboundAuthConfig(mode="jwt", discovery_url=DISCOVERY, allowed_audiences=("api://pets",))
SCRIPTS = [
    f"deploy/scripts/{name}.{ext}" for name in ("build", "run", "stop") for ext in ("sh", "ps1")
]
# Linters for the generated files, run through their official images.
ACTIONLINT_IMAGE = "rhysd/actionlint:1.7.12"
SHELLCHECK_IMAGE = "koalaman/shellcheck:v0.11.0"


def _tree(deployment: DeploymentConfig):
    return generate(IntermediateRep.from_dict(IR), package_name=PKG, deployment=deployment).tree


def _files(deployment: DeploymentConfig) -> dict[str, str]:
    return {e.path: e.content for e in _tree(deployment).entries}


@pytest.fixture(scope="module")
def full() -> dict[str, str]:
    """docker + JWT + GitHub Actions + scripts."""
    return _files(
        DeploymentConfig(target="docker", ci="github-actions", scripts=True, inbound_auth=JWT)
    )


# ---- pinned actions -----------------------------------------------------------------


def test_actions_are_pinned_to_full_commit_shas() -> None:
    assert constants.ACTIONS, "action pins live in constants"
    for name, pin in constants.ACTIONS.items():
        repo, _, rest = pin.partition("@")
        sha, _, comment = rest.partition(" # ")
        assert repo == name and re.fullmatch(r"[0-9a-f]{40}", sha), pin
        assert re.fullmatch(r"v\d+(\.\d+)*", comment), pin


def _uses(workflow: str) -> list[str]:
    return re.findall(r"uses: (\S+@\S+)(?: # (\S+))?", workflow)


def _check_workflow_hygiene(text: str) -> dict:
    data = yaml.safe_load(text)
    for ref, version in _uses(text):
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
        assert version, f"{ref} needs a version comment for Dependabot"
    # No untrusted event data interpolated into shell commands (script injection).
    for job in data["jobs"].values():
        for step in job["steps"]:
            assert "${{ github.event" not in step.get("run", ""), step
            assert "${{ github.head_ref" not in step.get("run", ""), step
        for step in job["steps"]:
            if "actions/checkout@" in step.get("uses", ""):
                assert step["with"]["persist-credentials"] is False
    return data


# ---- local + GitHub Actions ---------------------------------------------------------


def test_local_can_add_ci_only() -> None:
    files = _files(DeploymentConfig(ci="github-actions"))
    assert ".github/workflows/ci.yml" in files
    assert ".github/dependabot.yml" in files
    assert ".github/workflows/release.yml" not in files
    assert "DEPLOY.md" not in files
    ci = _check_workflow_hygiene(files[".github/workflows/ci.yml"])
    assert set(ci["jobs"]) == {"test"}
    assert ci["permissions"] == {"contents": "read"}
    dependabot = yaml.safe_load(files[".github/dependabot.yml"])
    assert [u["package-ecosystem"] for u in dependabot["updates"]] == ["github-actions"]


def test_local_still_has_no_scripts() -> None:
    with pytest.raises(ValueError, match="scripts"):
        _files(DeploymentConfig(scripts=True))


# ---- docker + GitHub Actions --------------------------------------------------------


def test_ci_tests_the_server_and_smoke_tests_the_image(full: dict[str, str]) -> None:
    ci = _check_workflow_hygiene(full[".github/workflows/ci.yml"])
    assert set(ci["jobs"]) == {"test", "image"}
    test_steps = "\n".join(s.get("run", "") for s in ci["jobs"]["test"]["steps"])
    assert "pip install --require-hashes -r requirements.lock" in test_steps
    assert "python -m pytest" in test_steps
    image_steps = "\n".join(s.get("run", "") for s in ci["jobs"]["image"]["steps"])
    assert "docker build" in image_steps
    assert "127.0.0.1:8000:8000" in image_steps
    assert f"python -m {PKG}.verify --url http://127.0.0.1:8000/mcp" in image_steps
    assert ci["jobs"]["image"]["needs"] == "test"


def test_release_pushes_a_multi_arch_image_to_ghcr(full: dict[str, str]) -> None:
    release = _check_workflow_hygiene(full[".github/workflows/release.yml"])
    assert release["permissions"] == {"contents": "read", "packages": "write"}
    triggers = release[True] if True in release else release["on"]  # YAML 1.1 "on"
    assert triggers["push"]["tags"] == ["v*"] and "workflow_dispatch" in triggers
    text = full[".github/workflows/release.yml"]
    assert "registry: ghcr.io" in text
    assert "secrets.GITHUB_TOKEN" in text
    assert "linux/amd64,linux/arm64" in text
    assert "push: true" in text


def test_dependabot_watches_actions_and_the_base_image(full: dict[str, str]) -> None:
    dependabot = yaml.safe_load(full[".github/dependabot.yml"])
    assert dependabot["version"] == 2
    assert [u["package-ecosystem"] for u in dependabot["updates"]] == ["github-actions", "docker"]


def test_docker_without_ci_has_no_github_folder() -> None:
    files = _files(DeploymentConfig(target="docker"))
    assert not any(p.startswith(".github/") for p in files)
    assert "DEPLOY.md" in files, "docker always gets step-by-step instructions"


# ---- scripts ------------------------------------------------------------------------


def test_scripts_come_in_bash_and_powershell_pairs() -> None:
    tree = _tree(DeploymentConfig(target="docker", scripts=True))
    by_path = {e.path: e for e in tree.entries}
    for path in SCRIPTS:
        assert path in by_path, path
    for path in SCRIPTS:
        entry = by_path[path]
        if path.endswith(".sh"):
            assert entry.executable
            assert entry.content.startswith("#!/usr/bin/env sh\n")
            assert "set -eu" in entry.content
            assert 'cd "$(dirname "$0")/../.."' in entry.content
        else:
            assert "$ErrorActionPreference = 'Stop'" in entry.content
            assert "$PSScriptRoot" in entry.content
            assert "$LASTEXITCODE" in entry.content


def test_run_script_waits_for_the_container_health_check() -> None:
    files = _files(DeploymentConfig(target="docker", scripts=True))
    for path in ("deploy/scripts/run.sh", "deploy/scripts/run.ps1"):
        text = files[path]
        assert "docker compose up --build --detach" in text
        assert ".State.Health.Status" in text
        assert ".env" in text
        assert f"docker compose port {PKG.replace('_', '-')} 8000" in text, "real host port"


def test_shell_scripts_parse(tmp_path: Path) -> None:
    sh = shutil.which("sh") or shutil.which("bash")
    if not sh:
        pytest.skip("no POSIX shell")
    files = _files(DeploymentConfig(target="docker", scripts=True))
    for path in SCRIPTS:
        if path.endswith(".sh"):
            script = tmp_path / Path(path).name
            script.write_text(files[path], encoding="utf-8", newline="\n")
            proc = subprocess.run([sh, "-n", str(script)], capture_output=True, text=True)
            assert proc.returncode == 0, (path, proc.stderr)


def test_powershell_scripts_parse(tmp_path: Path) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("no PowerShell")
    files = _files(DeploymentConfig(target="docker", scripts=True))
    for path in SCRIPTS:
        if path.endswith(".ps1"):
            script = tmp_path / Path(path).name
            script.write_text(files[path], encoding="utf-8")
            check = (
                "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                f"'{script}', [ref]$null, [ref]$e); if ($e) {{ $e | Out-String; exit 1 }}"
            )
            proc = subprocess.run(
                [shell, "-NoProfile", "-Command", check], capture_output=True, text=True
            )
            assert proc.returncode == 0, (path, proc.stdout, proc.stderr)


# ---- DEPLOY.md ----------------------------------------------------------------------


def test_deploy_md_is_a_numbered_walkthrough(full: dict[str, str]) -> None:
    deploy = full["DEPLOY.md"]
    headings = re.findall(r"^## (\d+)\. (.+)$", deploy, re.M)
    assert [int(n) for n, _ in headings] == list(range(1, len(headings) + 1))
    titles = " | ".join(t for _, t in headings).lower()
    for topic in (
        "prerequisites",
        "identity provider",
        "build",
        "verify",
        "connect",
        "update",
        "tear down",
        "troubleshoot",
    ):
        assert topic in titles, topic


def test_deploy_md_covers_identity_providers_without_hardcoding_one(full: dict[str, str]) -> None:
    deploy = full["DEPLOY.md"]
    for provider in ("Amazon Cognito", "Okta", "Auth0", "Microsoft Entra ID", "Keycloak"):
        assert provider in deploy, provider
    assert DISCOVERY in deploy, "the configured provider is shown"
    assert "MCP_JWT_DISCOVERY_URL" in deploy


def test_deploy_md_shows_both_shells_and_the_chosen_extras(full: dict[str, str]) -> None:
    deploy = full["DEPLOY.md"]
    assert "```bash" in deploy and "```powershell" in deploy
    assert "deploy/scripts/run.sh" in deploy and "deploy/scripts/run.ps1" in deploy
    assert "release.yml" in deploy and "ghcr.io" in deploy
    assert "MCP_BEARER_TOKEN" in deploy
    assert '"Authorization": "Bearer ${env:MCP_BEARER_TOKEN}"' in deploy
    assert "claude mcp add --transport http" in deploy


def test_deploy_md_only_mentions_what_was_generated() -> None:
    deploy = _files(DeploymentConfig(target="docker"))["DEPLOY.md"]
    assert "deploy/scripts" not in deploy
    assert "release.yml" not in deploy
    # Without JWT: no identity provider section, and a clear loopback-only warning.
    assert "Okta" not in deploy


def test_deploy_md_explains_changing_the_host_port() -> None:
    deploy = _files(DeploymentConfig(target="docker"))["DEPLOY.md"]
    assert "MCP_HOST_PORT" in deploy and "port is already allocated" in deploy
    assert "127.0.0.1" in deploy and "does not check tokens" in deploy


# ---- external linters (Docker images; not part of `make test`) ----------------------


def _docker_linux() -> bool:
    if not shutil.which("docker"):
        return False
    info = subprocess.run(
        ["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True
    )
    return info.returncode == 0 and info.stdout.strip() == "linux"


@pytest.mark.e2e
def test_actionlint_and_shellcheck_pass(tmp_path: Path, full: dict[str, str]) -> None:
    if not _docker_linux():
        pytest.skip("needs Docker with Linux containers")
    tree = _tree(
        DeploymentConfig(target="docker", ci="github-actions", scripts=True, inbound_auth=JWT)
    )
    tree.write_to(tmp_path)
    mount = f"{tmp_path.resolve()}:/repo"
    actionlint = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            mount,
            "-w",
            "/repo",
            ACTIONLINT_IMAGE,
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert actionlint.returncode == 0, actionlint.stdout + actionlint.stderr
    scripts = [f"/repo/{p}" for p in SCRIPTS if p.endswith(".sh")]
    shellcheck = subprocess.run(
        ["docker", "run", "--rm", "-v", mount, SHELLCHECK_IMAGE, *scripts],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert shellcheck.returncode == 0, shellcheck.stdout + shellcheck.stderr


def test_written_files_keep_lf_line_endings_on_every_os(tmp_path: Path) -> None:
    """write_to must not translate newlines (Windows would turn \n into \r\n), or shell
    scripts fail inside Linux containers and CI."""
    _tree(DeploymentConfig(target="docker", scripts=True)).write_to(tmp_path)
    for name in ("deploy/scripts/run.sh", "Dockerfile", "README.md"):
        assert b"\r\n" not in (tmp_path / name).read_bytes(), name


def test_gitattributes_keeps_shell_scripts_lf_through_git_checkouts() -> None:
    files = _files(DeploymentConfig(target="docker", scripts=True))
    assert "*.sh text eol=lf" in files[".gitattributes"].splitlines()
    assert ".gitattributes" not in _files(DeploymentConfig(target="docker"))
