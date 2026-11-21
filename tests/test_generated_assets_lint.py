"""ADR-026 step 8: every deployment target's generated assets pass the standard linters.

One matrix instead of per-target copies: for each target and the option combinations
that change its files, write the project and run actionlint (workflows), shellcheck
(scripts), hadolint (Dockerfiles) and terraform fmt/init/validate (modules).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lint_harness import docker_linux, lint_problems, terraform, terraform_problems
from mcp_wizard_generators.models import (
    DeploymentConfig,
    InboundAuthConfig,
    IntermediateRep,
    SecurityConfig,
)
from mcp_wizard_generators.pipeline import generate

ROOT = Path(__file__).parent
IR = json.loads((ROOT / "fixtures" / "pets_tiny.ir.json").read_text(encoding="utf-8"))
DISCOVERY = "https://login.example.com/tenant/.well-known/openid-configuration"
JWT = InboundAuthConfig(mode="jwt", discovery_url=DISCOVERY, allowed_audiences=("api://pets",))
COGNITO = InboundAuthConfig(mode="jwt", create_cognito=True)
API_KEY = SecurityConfig(auth_mode="api_key")
NO_AUTH = SecurityConfig()
FULL = {"ci": "github-actions", "scripts": True}
AWS = {"iac": "terraform", "aws_region": "us-east-1"}

CASES = {
    "local-ci": (DeploymentConfig(target="local", ci="github-actions"), API_KEY),
    "docker": (DeploymentConfig(target="docker", **FULL), API_KEY),
    "docker-jwt": (DeploymentConfig(target="docker", inbound_auth=JWT, **FULL), API_KEY),
    "agentcore-container-jwt": (
        DeploymentConfig(target="agentcore-container", inbound_auth=JWT, **AWS, **FULL),
        API_KEY,
    ),
    "agentcore-container-cognito": (
        DeploymentConfig(target="agentcore-container", inbound_auth=COGNITO, **AWS, **FULL),
        NO_AUTH,
    ),
    # Upstream credentials and Cognito together (both add deploy-role statements).
    "agentcore-container-cognito-api-key": (
        DeploymentConfig(target="agentcore-container", inbound_auth=COGNITO, **AWS, **FULL),
        API_KEY,
    ),
    "agentcore-code-jwt": (
        DeploymentConfig(target="agentcore-code", inbound_auth=JWT, **AWS, **FULL),
        API_KEY,
    ),
    "agentcore-code-cognito": (
        DeploymentConfig(target="agentcore-code", inbound_auth=COGNITO, **AWS, **FULL),
        NO_AUTH,
    ),
    # Without CI or scripts: Terraform and DEPLOY.md stand alone.
    "agentcore-code-minimal": (
        DeploymentConfig(target="agentcore-code", inbound_auth=JWT, **AWS),
        API_KEY,
    ),
}


def _write(case: str, out: Path) -> Path:
    deployment, security = CASES[case]
    generate(
        IntermediateRep.from_dict(IR),
        package_name="pets_tiny_mcp",
        deployment=deployment,
        security=security,
    ).tree.write_to(out)
    return out


def test_matrix_covers_every_registered_deployment_target() -> None:
    from mcp_wizard_generators.registry import get_default_registry

    registered = {d["key"] for d in get_default_registry().list_deployments()}
    assert registered == {deployment.target for deployment, _ in CASES.values()}


@pytest.mark.e2e
@pytest.mark.parametrize("case", sorted(CASES))
def test_linters_pass(case: str, tmp_path: Path) -> None:
    if not docker_linux():
        pytest.skip("needs Docker with Linux containers")
    problems = lint_problems(_write(case, tmp_path))
    assert not problems, "\n\n".join(problems)


@pytest.mark.e2e
@pytest.mark.parametrize("case", sorted(c for c in CASES if c.startswith("agentcore")))
def test_terraform_validates(
    case: str, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    if not terraform():
        pytest.skip("terraform not installed")
    cache = tmp_path_factory.getbasetemp() / "terraform-plugin-cache"
    cache.mkdir(exist_ok=True)
    problems = terraform_problems(_write(case, tmp_path), cache)
    assert not problems, "\n\n".join(problems)
