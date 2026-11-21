"""ADR-026 step 7: the ``agentcore-code`` deployment target (direct code deployment)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest
import yaml

from lint_harness import docker_linux
from mcp_wizard_generators.models import (
    DeploymentConfig,
    InboundAuthConfig,
    IntermediateRep,
    SecurityConfig,
)
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.registry import get_default_registry

ROOT = Path(__file__).parent
IR = json.loads((ROOT / "fixtures" / "pets_tiny.ir.json").read_text(encoding="utf-8"))
PKG = "pets_tiny_mcp"
DISCOVERY = "https://login.example.com/tenant/.well-known/openid-configuration"
JWT = InboundAuthConfig(mode="jwt", discovery_url=DISCOVERY, allowed_audiences=("api://pets",))
COGNITO = InboundAuthConfig(mode="jwt", create_cognito=True)
API_KEY = SecurityConfig(auth_mode="api_key")
TF = "deploy/terraform"
PACKAGE = "deploy/agentcore/package.py"
ENTRY = "deploy/agentcore/main.py"
SCRIPTS = [
    f"deploy/scripts/{n}.{e}" for n in ("bootstrap", "deploy", "destroy") for e in ("sh", "ps1")
]
# ELF e_machine for AArch64.
EM_AARCH64 = 183


def _config(**kwargs) -> DeploymentConfig:
    base = {
        "target": "agentcore-code",
        "iac": "terraform",
        "aws_region": "us-east-1",
        "inbound_auth": JWT,
    }
    return DeploymentConfig(**{**base, **kwargs})


def _tree(deployment: DeploymentConfig, security: SecurityConfig = API_KEY):
    return generate(
        IntermediateRep.from_dict(IR),
        package_name=PKG,
        deployment=deployment,
        security=security,
    ).tree


def _files(deployment: DeploymentConfig, security: SecurityConfig = API_KEY) -> dict[str, str]:
    return {e.path: e.content for e in _tree(deployment, security).entries}


@pytest.fixture(scope="module")
def full() -> dict[str, str]:
    return _files(_config(ci="github-actions", scripts=True))


# ---- registry and options -----------------------------------------------------------


def test_registered_and_needs_http() -> None:
    plugin = get_default_registry().deployment("agentcore-code")
    assert plugin.requires == frozenset({"streamable-http"})
    assert plugin.iac_options == frozenset({"terraform"})


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"iac": "none"}, "iac"),
        ({"aws_region": ""}, "aws_region"),
        ({"inbound_auth": InboundAuthConfig()}, "JWT"),
    ],
)
def test_options_it_cannot_honor_are_refused(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _files(_config(**kwargs))


# ---- package --------------------------------------------------------------------------


def test_no_container_files_but_an_entry_point_and_a_package_script(full: dict[str, str]) -> None:
    assert "Dockerfile" not in full and ".dockerignore" not in full
    assert f"src/{PKG}/aws_secrets.py" in full
    assert "requirements-aws.lock" in full
    entry = full[ENTRY]
    assert f"from {PKG}.server import main" in entry
    for setting in ('"MCP_TRANSPORT", "streamable-http"', '"MCP_HOST", "0.0.0.0"'):
        assert setting in entry, setting
    assert "MCP_AUTH" not in entry.split('"""', 2)[2], "auth is set where it runs (Terraform)"
    assert "deploy/agentcore/.gitignore" in full


def test_package_script_installs_arm64_wheels_from_the_hash_lock(full: dict[str, str]) -> None:
    script = full[PACKAGE]
    for needle in (
        "aarch64-manylinux2014",
        '"3.13"',
        "--require-hashes",
        "--only-binary",
        "requirements-aws.lock",
        "250 * 1024 * 1024",
    ):
        assert needle in script, needle
    compile(script, PACKAGE, "exec")


# ---- Terraform ----------------------------------------------------------------------


def test_runtime_runs_the_zip_from_the_code_bucket(full: dict[str, str]) -> None:
    main = full[f"{TF}/main.tf"]
    assert "code_configuration {" in main and "container_configuration" not in main
    assert 'entry_point = ["main.py"]' in main
    assert 'runtime     = "PYTHON_3_13"' in main
    assert "prefix = var.code_key" in main
    assert 'data "aws_s3_bucket" "code"' in main
    assert not re.search(r"aws_ecr|ecr:", main)
    assert 'server_protocol = "MCP"' in main and "custom_jwt_authorizer {" in main
    assert "API_SECRETS_ARN" in main
    variables = full[f"{TF}/variables.tf"]
    assert 'variable "code_key"' in variables and "image_tag" not in variables


def test_runtime_role_reads_only_the_code_bucket(full: dict[str, str]) -> None:
    main = full[f"{TF}/main.tf"]
    assert '"s3:GetObject"' in main
    assert '"${data.aws_s3_bucket.code.arn}/*"' in main
    assert '"secretsmanager:GetSecretValue"' in main


def test_bootstrap_creates_a_private_versioned_code_bucket(full: dict[str, str]) -> None:
    boot = full[f"{TF}/bootstrap/main.tf"]
    for snippet in (
        'resource "aws_s3_bucket" "code"',
        'resource "aws_s3_bucket_versioning" "code"',
        'resource "aws_s3_bucket_public_access_block" "code"',
        'resource "aws_s3_bucket_server_side_encryption_configuration" "code"',
        "token.actions.githubusercontent.com",
    ):
        assert snippet in boot, snippet
    assert "aws_ecr_repository" not in boot and "ecr:" not in boot
    # The deploy role uploads packages, and AgentCore checks the caller can read them.
    assert '"s3:PutObject"' in boot and '"s3:GetObject"' in boot
    outputs = full[f"{TF}/bootstrap/outputs.tf"]
    assert 'output "code_bucket"' in outputs and "ecr_repository_url" not in outputs


def test_container_terraform_is_unchanged_by_the_code_target() -> None:
    """The container target keeps its ECR image source (no code bucket leaks in)."""
    files = _files(_config(target="agentcore-container"))
    assert "container_configuration {" in files[f"{TF}/main.tf"]
    assert '"code"' not in files[f"{TF}/bootstrap/main.tf"]


# ---- GitHub Actions -----------------------------------------------------------------


def test_deploy_workflow_packages_uploads_and_applies(full: dict[str, str]) -> None:
    text = full[".github/workflows/deploy.yml"]
    data = yaml.safe_load(text)
    assert data["permissions"] == {"contents": "read", "id-token": "write"}
    job = data["jobs"]["deploy"]
    assert job["environment"] == "production"
    assert "docker" not in text and not re.search(r"amazon-ecr|aws ecr", text)
    assert "astral-sh/setup-uv@" in text
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "python deploy/agentcore/package.py" in runs
    assert "aws s3 cp" in runs and "vars.CODE_BUCKET" in text
    assert "code_key=" in runs and "terraform apply -input=false tfplan" in runs
    assert "${{ github.event" not in runs


def test_ci_builds_the_package_on_every_push(full: dict[str, str]) -> None:
    jobs = yaml.safe_load(full[".github/workflows/ci.yml"])["jobs"]
    assert "package" in jobs and "image" not in jobs
    runs = "\n".join(step.get("run", "") for step in jobs["package"]["steps"])
    assert "python deploy/agentcore/package.py" in runs


def test_dependabot_covers_actions_and_terraform_but_no_docker(full: dict[str, str]) -> None:
    updates = yaml.safe_load(full[".github/dependabot.yml"])["updates"]
    ecosystems = [(u["package-ecosystem"], u["directory"]) for u in updates]
    assert ("terraform", "/deploy/terraform") in ecosystems
    assert ("github-actions", "/") in ecosystems
    assert not any(e == "docker" for e, _ in ecosystems)


# ---- scripts and DEPLOY.md ----------------------------------------------------------


def test_deploy_script_packages_uploads_and_applies(full: dict[str, str]) -> None:
    for path in SCRIPTS:
        assert path in full, path
    for name in ("deploy.sh", "deploy.ps1"):
        text = full[f"deploy/scripts/{name}"]
        assert "deploy/agentcore/package.py" in text, name
        assert "aws s3 cp" in text, name
        assert "code_key=" in text, name
        assert "docker" not in text, name


def test_shell_scripts_parse(tmp_path: Path, full: dict[str, str]) -> None:
    sh = shutil.which("sh") or shutil.which("bash")
    if not sh:
        pytest.skip("no POSIX shell")
    for path in SCRIPTS:
        if path.endswith(".sh"):
            script = tmp_path / Path(path).name
            script.write_text(full[path], encoding="utf-8", newline="\n")
            proc = subprocess.run([sh, "-n", str(script)], capture_output=True, text=True)
            assert proc.returncode == 0, (path, proc.stderr)


def test_powershell_scripts_parse(tmp_path: Path, full: dict[str, str]) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("no PowerShell")
    for path in SCRIPTS:
        if path.endswith(".ps1"):
            script = tmp_path / Path(path).name
            script.write_text(full[path], encoding="utf-8")
            check = (
                "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                f"'{script}', [ref]$null, [ref]$e); if ($e) {{ $e | Out-String; exit 1 }}"
            )
            proc = subprocess.run(
                [shell, "-NoProfile", "-Command", check], capture_output=True, text=True
            )
            assert proc.returncode == 0, (path, proc.stdout, proc.stderr)


def test_deploy_md_walks_through_every_step(full: dict[str, str]) -> None:
    deploy = full["DEPLOY.md"]
    for needle in (
        "uv",
        "python deploy/agentcore/package.py",
        "CODE_BUCKET",
        "code_key",
        "aws secretsmanager put-secret-value",
        "terraform output -raw mcp_url",
        "```powershell",
        "incompatible with Linux ARM64",
    ):
        assert needle in deploy, needle
    assert "docker" not in deploy.lower()


def test_deploy_md_without_ci_or_scripts_spells_out_the_manual_steps() -> None:
    deploy = _files(_config())["DEPLOY.md"]
    assert "python deploy/agentcore/package.py" in deploy
    assert (
        'aws s3 cp deploy/agentcore/build/agentcore-code.zip "s3://<code_bucket>/<key>"' in deploy
    )
    assert 'apply -var "code_key=<key>"' in deploy
    assert 'destroy -var "code_key=code/unused.zip"' in deploy


def test_readme_points_to_deploy_md(full: dict[str, str]) -> None:
    readme = full["README.md"]
    assert "## Deploy to AWS Bedrock AgentCore" in readme and "zip" in readme


# ---- the real package -------------------------------------------------------------------


def _uv() -> str | None:
    """uv on PATH, else the one from the ``uv`` dev dependency."""
    found = shutil.which("uv")
    if not found:
        try:
            import uv
        except ImportError:
            uv = None
        found = uv.find_uv_bin() if uv else None
    if not found and os.environ.get("REQUIRE_UV"):
        pytest.fail("uv is required (REQUIRE_UV is set) but was not found")
    return found


def _package_env() -> dict[str, str]:
    """The package script looks uv up on PATH: put the found one first."""
    uv_dir = str(Path(_uv() or "uv").parent)
    return {**os.environ, "PATH": uv_dir + os.pathsep + os.environ.get("PATH", "")}


@pytest.fixture(scope="module")
def built_zip(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    """Generate the project, then run its package script (downloads ARM64 wheels)."""
    if not _uv():
        pytest.skip("uv not installed")
    out = tmp_path_factory.mktemp("agentcore-code")
    _tree(_config()).write_to(out)
    proc = subprocess.run(
        [sys.executable, str(out / PACKAGE)],
        capture_output=True,
        text=True,
        timeout=900,
        env=_package_env(),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    key = proc.stdout.strip().splitlines()[-1]
    return out / "deploy" / "agentcore" / "build" / "agentcore-code.zip", key


@pytest.mark.e2e
def test_package_is_an_arm64_zip_agentcore_accepts(built_zip: tuple[Path, str]) -> None:
    path, key = built_zip
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert key == f"code/{digest}.zip"
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        assert "main.py" in names, "entry point at the zip root"
        assert f"{PKG}/server.py" in names and f"{PKG}/aws_secrets.py" in names
        assert "mcp/__init__.py" in names and "boto3/__init__.py" in names
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
        assert not any(n.startswith("bin/") or n.endswith(".exe") for n in names)
        assert not any(n.startswith("pywin32") or n.startswith("win32") for n in names)
        sos = [i for i in infos if i.filename.endswith(".so")]
        assert sos, "compiled dependencies (pydantic-core, cryptography) are included"
        for info in sos:
            head = archive.read(info)[:20]
            assert head[:4] == b"\x7fELF", info.filename
            assert int.from_bytes(head[18:20], "little") == EM_AARCH64, info.filename
        for info in infos:
            mode = info.external_attr >> 16
            if info.is_dir():
                assert stat.S_IMODE(mode) == 0o755, info.filename
            else:
                assert stat.S_IMODE(mode) == 0o644, info.filename
        assert path.stat().st_size < 250 * 1024 * 1024


@pytest.mark.e2e
def test_package_is_reproducible(built_zip: tuple[Path, str], tmp_path: Path) -> None:
    path, key = built_zip
    project = path.parents[3]
    proc = subprocess.run(
        [sys.executable, str(project / PACKAGE)],
        capture_output=True,
        text=True,
        timeout=900,
        env=_package_env(),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == key, "same inputs, same S3 key"


def _docker_arm64() -> bool:
    if not docker_linux():
        return False
    probe = subprocess.run(
        ["docker", "run", "--rm", "--platform", "linux/arm64", "busybox:1.37", "true"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    return probe.returncode == 0


@pytest.mark.e2e
def test_package_serves_mcp_on_arm64(built_zip: tuple[Path, str], tmp_path: Path) -> None:
    """Unzip into /var/task of an ARM64 Python 3.13 container, run main.py, list tools."""
    if not _docker_arm64():
        if os.environ.get("REQUIRE_ARM64_DOCKER"):
            pytest.fail("REQUIRE_ARM64_DOCKER is set but Docker cannot run linux/arm64")
        pytest.skip("needs Docker with Linux containers that can run linux/arm64")
    from mcp_wizard_generators.plugins.deploy.constants import PYTHON_IMAGE

    path, _ = built_zip
    task = tmp_path / "task"
    with zipfile.ZipFile(path) as archive:
        archive.extractall(task)
    name = f"agentcore-code-e2e-{os.getpid()}"
    run = subprocess.run(
        [
            "docker", "run", "--detach", "--name", name, "--platform", "linux/arm64",
            "-p", "127.0.0.1::8000", "-v", f"{task.resolve()}:/var/task:ro", "-w", "/var/task",
            "-e", "MCP_AUTH=platform", "-e", "API_BASE_URL=https://api.example.com",
            "-e", "API_KEY=e2e-test-key", "-e", "PYTHONPATH=/var/task",
            PYTHON_IMAGE, "python", "main.py",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr
    try:
        port = (
            subprocess.run(
                ["docker", "port", name, "8000"], capture_output=True, text=True, check=True
            )
            .stdout.split(":")[-1]
            .strip()
        )
        project = path.parents[3]
        env = {**os.environ, "PYTHONPATH": str(project / "src")}
        verify = None
        # Imports compile on every start (read-only code, no bytecode in the zip), which
        # is slow under QEMU emulation: allow a few minutes.
        for _ in range(150):
            verify = subprocess.run(
                [sys.executable, "-m", f"{PKG}.verify", "--url", f"http://127.0.0.1:{port}/mcp"],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )
            if verify.returncode == 0:
                break
            time.sleep(2)
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        assert verify is not None and verify.returncode == 0, (
            verify.stdout + verify.stderr + logs.stdout + logs.stderr
        )
        assert "listpets" in verify.stdout
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
