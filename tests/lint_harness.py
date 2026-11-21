"""External linters for generated deployment assets (design ADR-026, step 8).

actionlint, shellcheck and hadolint run from Docker images pinned by digest, so every
machine and CI runner checks with the same versions; Terraform runs from PATH. On CI the
``REQUIRE_*`` variables turn a missing tool into a failure instead of a silent skip.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ACTIONLINT_IMAGE = (
    "rhysd/actionlint:1.7.12"
    "@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667"
)
SHELLCHECK_IMAGE = (
    "koalaman/shellcheck:v0.11.0"
    "@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d"
)
HADOLINT_IMAGE = (
    "hadolint/hadolint:v2.15.1"
    "@sha256:32dac94127fd60b7b7e3fbfc65e1383b9b5e25c9bfd7b8536de7a539fe68a12d"
)


def docker_linux() -> bool:
    """Docker that runs Linux containers (not Windows containers, as on GitHub's
    Windows runners). Fails instead when REQUIRE_LINUX_DOCKER is set."""
    ok = False
    if shutil.which("docker"):
        info = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True
        )
        ok = info.returncode == 0 and info.stdout.strip() == "linux"
    if not ok and os.environ.get("REQUIRE_LINUX_DOCKER"):
        pytest.fail("REQUIRE_LINUX_DOCKER is set but Docker cannot run Linux containers")
    return ok


def terraform() -> str | None:
    found = shutil.which("terraform")
    if not found and os.environ.get("REQUIRE_TERRAFORM"):
        pytest.fail("terraform is required (REQUIRE_TERRAFORM is set) but not on PATH")
    return found


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, **kwargs)


def _docker(image: str, project: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    # pytest's tmp dirs are private (0700) on POSIX: run as this user so the linter can
    # read the mount. Docker Desktop on Windows does not apply host permissions.
    user = ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
    mount = f"{project.resolve()}:/repo"
    return _run(["docker", "run", "--rm", *user, "-v", mount, "-w", "/repo", image, *args])


def lint_problems(project: Path) -> list[str]:
    """Every linter that applies to the project's files; one entry per failing tool."""
    problems: list[str] = []
    workflows = sorted(
        p.relative_to(project).as_posix() for p in project.glob(".github/workflows/*.yml")
    )
    if workflows:
        proc = _docker(ACTIONLINT_IMAGE, project, workflows)
        if proc.returncode:
            problems.append("actionlint:\n" + proc.stdout + proc.stderr)
    scripts = sorted(p.relative_to(project).as_posix() for p in project.rglob("*.sh"))
    if scripts:
        proc = _docker(SHELLCHECK_IMAGE, project, scripts)
        if proc.returncode:
            problems.append("shellcheck:\n" + proc.stdout + proc.stderr)
    dockerfiles = sorted(p.relative_to(project).as_posix() for p in project.rglob("Dockerfile"))
    if dockerfiles:
        # hadolint's default threshold fails on warnings too (exit 1).
        proc = _docker(HADOLINT_IMAGE, project, ["hadolint", *dockerfiles])
        if proc.returncode:
            problems.append("hadolint:\n" + proc.stdout + proc.stderr)
    return problems


def terraform_problems(project: Path, plugin_cache: Path) -> list[str]:
    """terraform fmt -check, then init -backend=false + validate for every root module."""
    problems: list[str] = []
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    # One provider download per test session instead of one per module.
    env.setdefault("TF_PLUGIN_CACHE_DIR", str(plugin_cache))
    root = project / "deploy" / "terraform"
    proc = _run(["terraform", "fmt", "-check", "-diff", "-recursive", str(root)], env=env)
    if proc.returncode:
        problems.append("terraform fmt:\n" + proc.stdout + proc.stderr)
    for module in sorted({p.parent for p in root.rglob("*.tf")}):
        for cmd in (["init", "-backend=false", "-input=false"], ["validate", "-no-color"]):
            proc = _run(["terraform", f"-chdir={module}", *cmd], env=env)
            if proc.returncode:
                problems.append(f"terraform {cmd[0]} {module.name}:\n{proc.stdout}{proc.stderr}")
                break
    return problems
