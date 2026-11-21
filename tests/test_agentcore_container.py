"""ADR-026 step 6b: the ``agentcore-container`` deployment target."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

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
SCRIPTS = [
    f"deploy/scripts/{n}.{e}" for n in ("bootstrap", "deploy", "destroy") for e in ("sh", "ps1")
]
ACTIONLINT_IMAGE = "rhysd/actionlint:1.7.12"
SHELLCHECK_IMAGE = "koalaman/shellcheck:v0.11.0"


def _config(**kwargs) -> DeploymentConfig:
    base = {
        "target": "agentcore-container",
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
    plugin = get_default_registry().deployment("agentcore-container")
    assert plugin.requires == frozenset({"streamable-http"})


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


# ---- image --------------------------------------------------------------------------


def test_image_is_arm64_installs_the_aws_lock_and_never_sets_auth(full: dict[str, str]) -> None:
    dockerfile = full["Dockerfile"]
    assert dockerfile.count("FROM --platform=linux/arm64 python:") == 2
    assert "pip install --require-hashes -r requirements-aws.lock" in dockerfile
    assert "MCP_AUTH=" not in dockerfile
    assert "MCP_HOST=0.0.0.0" in dockerfile and "MCP_PORT=8000" in dockerfile
    assert f"src/{PKG}/aws_secrets.py" in full
    assert "requirements-aws.lock" in full


# ---- Terraform ----------------------------------------------------------------------


def test_terraform_layout(full: dict[str, str]) -> None:
    for name in (
        "versions.tf",
        "backend.tf",
        "variables.tf",
        "main.tf",
        "outputs.tf",
        "terraform.tfvars",
        "backend.hcl.example",
        ".gitignore",
    ):
        assert f"{TF}/{name}" in full, name
    for name in ("main.tf", "variables.tf", "outputs.tf", "terraform.tfvars"):
        assert f"{TF}/bootstrap/{name}" in full, name
    assert f"{TF}/cognito.tf" not in full


def test_runtime_uses_mcp_public_network_and_the_configured_idp(full: dict[str, str]) -> None:
    main = full[f"{TF}/main.tf"]
    assert 'resource "aws_bedrockagentcore_agent_runtime" "server"' in main
    assert 'server_protocol = "MCP"' in main
    assert 'network_mode = "PUBLIC"' in main
    assert "custom_jwt_authorizer {" in main
    assert "var.image_tag" in main
    assert 'MCP_AUTH        = "platform"' in main or 'MCP_AUTH = "platform"' in main
    assert "API_SECRETS_ARN" in main
    tfvars = full[f"{TF}/terraform.tfvars"]
    assert f'jwt_discovery_url     = "{DISCOVERY}"' in tfvars
    assert '"api://pets"' in tfvars
    assert "create_cognito        = false" in tfvars


def test_runtime_role_follows_the_documented_least_privilege(full: dict[str, str]) -> None:
    main = full[f"{TF}/main.tf"]
    assert '"bedrock-agentcore.amazonaws.com"' in main
    assert '"aws:SourceAccount"' in main and '"aws:SourceArn"' in main
    assert "data.aws_ecr_repository.server.arn" in main, "ECR pull on this repository only"
    assert '"secretsmanager:GetSecretValue"' in main
    assert "aws_secretsmanager_secret.api.arn" in main, "read on this secret only"
    assert "bedrock:InvokeModel" not in main, "the server does not call models"


def test_no_secret_values_anywhere_in_terraform(full: dict[str, str]) -> None:
    for path, text in full.items():
        if path.startswith(TF):
            assert "aws_secretsmanager_secret_version" not in text, path
            assert "secret_string" not in text, path


def test_bootstrap_creates_state_repo_and_a_scoped_github_role(full: dict[str, str]) -> None:
    boot = full[f"{TF}/bootstrap/main.tf"]
    for snippet in (
        'resource "aws_s3_bucket" "state"',
        'resource "aws_ecr_repository" "server"',
        'image_tag_mutability = "IMMUTABLE"',
        "token.actions.githubusercontent.com",
        "sts:AssumeRoleWithWebIdentity",
        "repo:${var.github_repository}:environment:${var.github_environment}",
        '"iam:PassedToService"',
        "aws:SecureTransport",
    ):
        assert snippet in boot, snippet
    assert "secretsmanager:GetSecretValue" not in boot, "the deploy role never reads secret values"


def test_cognito_is_optional() -> None:
    files = _files(_config(inbound_auth=COGNITO))
    cognito = files[f"{TF}/cognito.tf"]
    assert 'resource "aws_cognito_user_pool" "this"' in cognito
    assert '"client_credentials"' in cognito
    assert "create_cognito        = true" in files[f"{TF}/terraform.tfvars"]
    assert "cognito_client_secret" in files[f"{TF}/outputs.tf"]
    assert "sensitive" in files[f"{TF}/outputs.tf"]


def test_no_secret_resource_without_upstream_credentials() -> None:
    files = _files(_config(), security=SecurityConfig())
    assert "aws_secretsmanager_secret" not in files[f"{TF}/main.tf"]
    assert "secretsmanager" not in files[f"{TF}/bootstrap/main.tf"]


def _terraform() -> str | None:
    return shutil.which("terraform")


def test_terraform_files_are_formatted(tmp_path: Path) -> None:
    if not _terraform():
        pytest.skip("terraform not installed")
    for deployment in (_config(ci="github-actions"), _config(inbound_auth=COGNITO)):
        out = tmp_path / ("c" if deployment.inbound_auth.create_cognito else "j")
        _tree(deployment).write_to(out)
        proc = subprocess.run(
            ["terraform", "fmt", "-check", "-diff", "-recursive", str(out / TF)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.e2e
@pytest.mark.parametrize("auth", [JWT, COGNITO], ids=["jwt", "cognito"])
@pytest.mark.parametrize("security", [API_KEY, SecurityConfig()], ids=["api_key", "none"])
def test_terraform_validates(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, auth: InboundAuthConfig, security
) -> None:
    """terraform init -backend=false && validate (downloads the AWS provider)."""
    if not _terraform():
        pytest.skip("terraform not installed")
    _tree(_config(inbound_auth=auth), security).write_to(tmp_path)
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    # One provider download per test session instead of one per module.
    cache = tmp_path_factory.getbasetemp() / "terraform-plugin-cache"
    cache.mkdir(exist_ok=True)
    env.setdefault("TF_PLUGIN_CACHE_DIR", str(cache))
    for folder in (tmp_path / TF, tmp_path / TF / "bootstrap"):
        for cmd in (["init", "-backend=false", "-input=false"], ["validate", "-no-color"]):
            proc = subprocess.run(
                ["terraform", f"-chdir={folder}", *cmd],
                capture_output=True,
                text=True,
                env=env,
                timeout=600,
            )
            assert proc.returncode == 0, (folder.name, cmd, proc.stdout, proc.stderr)


# ---- GitHub Actions -----------------------------------------------------------------


def test_deploy_workflow_uses_oidc_and_an_approval_gate(full: dict[str, str]) -> None:
    text = full[".github/workflows/deploy.yml"]
    data = yaml.safe_load(text)
    assert data["permissions"] == {"contents": "read", "id-token": "write"}
    job = data["jobs"]["deploy"]
    assert job["environment"] == "production"
    for ref in re.findall(r"uses: (\S+)", text):
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
    assert "vars.AWS_DEPLOY_ROLE_ARN" in text and "secrets." not in text
    assert "aws-access-key-id" not in text, "no long-lived keys"
    assert "platforms: linux/arm64" in text
    assert "provenance: false" in text
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "terraform plan" in runs and "-out=tfplan" in runs
    assert "terraform apply -input=false tfplan" in runs
    assert "${{ github.event" not in runs


def test_dependabot_covers_actions_image_and_terraform(full: dict[str, str]) -> None:
    updates = yaml.safe_load(full[".github/dependabot.yml"])["updates"]
    ecosystems = [(u["package-ecosystem"], u["directory"]) for u in updates]
    assert ("terraform", "/deploy/terraform") in ecosystems
    assert ("terraform", "/deploy/terraform/bootstrap") in ecosystems
    assert ("docker", "/") in ecosystems and ("github-actions", "/") in ecosystems


# ---- scripts and DEPLOY.md ----------------------------------------------------------


def test_scripts_come_in_pairs(full: dict[str, str]) -> None:
    for path in SCRIPTS:
        assert path in full, path
    deploy_sh = full["deploy/scripts/deploy.sh"]
    assert "--platform linux/arm64" in deploy_sh and "--provenance=false" in deploy_sh
    assert "aws ecr get-login-password" in deploy_sh
    assert "terraform -chdir=deploy/terraform apply" in deploy_sh


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
    headings = re.findall(r"^## (\d+)\. (.+)$", deploy, re.M)
    assert [int(n) for n, _ in headings] == list(range(1, len(headings) + 1))
    titles = " | ".join(t for _, t in headings).lower()
    for topic in (
        "prerequisites",
        "identity provider",
        "bootstrap",
        "credentials",
        "github",
        "deploy",
        "verify",
        "connect",
        "update",
        "tear down",
        "troubleshoot",
    ):
        assert topic in titles, topic
    for needle in (
        "aws secretsmanager put-secret-value",
        "terraform output -raw mcp_url",
        "AWS_DEPLOY_ROLE_ARN",
        "TF_STATE_BUCKET",
        "MCP_BEARER_TOKEN",
        "Okta",
        "Microsoft Entra ID",
        "```powershell",
        "/aws/bedrock-agentcore/runtimes/",
    ):
        assert needle in deploy, needle


def test_deploy_md_explains_cognito_tokens_when_created() -> None:
    deploy = _files(_config(inbound_auth=COGNITO))["DEPLOY.md"]
    assert "cognito_token_url" in deploy and "grant_type=client_credentials" in deploy


def test_readme_points_to_deploy_md(full: dict[str, str]) -> None:
    assert "## Deploy to AWS Bedrock AgentCore" in full["README.md"]


# ---- external linters ---------------------------------------------------------------


def _docker_linux() -> bool:
    if not shutil.which("docker"):
        return False
    info = subprocess.run(
        ["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True
    )
    return info.returncode == 0 and info.stdout.strip() == "linux"


@pytest.mark.e2e
def test_actionlint_and_shellcheck_pass(tmp_path: Path) -> None:
    if not _docker_linux():
        pytest.skip("needs Docker with Linux containers")
    _tree(_config(ci="github-actions", scripts=True)).write_to(tmp_path)
    mount = f"{tmp_path.resolve()}:/repo"
    user = ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
    workflows = [".github/workflows/ci.yml", ".github/workflows/deploy.yml"]
    lint = subprocess.run(
        ["docker", "run", "--rm", *user, "-v", mount, "-w", "/repo", ACTIONLINT_IMAGE, *workflows],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr
    scripts = [f"/repo/{p}" for p in SCRIPTS if p.endswith(".sh")]
    check = subprocess.run(
        ["docker", "run", "--rm", *user, "-v", mount, SHELLCHECK_IMAGE, *scripts],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert check.returncode == 0, check.stdout + check.stderr
