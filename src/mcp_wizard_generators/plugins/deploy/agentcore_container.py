"""``agentcore-container``: the server on AWS Bedrock AgentCore Runtime (design ADR-026).

An ARM64 image in ECR runs on AgentCore Runtime over streamable HTTP. AgentCore's
custom JWT authorizer checks every caller's token (any OIDC provider), so the server
runs with ``MCP_AUTH=platform``. Upstream credentials come from Secrets Manager.
Terraform (with a one-time bootstrap) creates everything; GitHub Actions and scripts
are optional.
"""

from __future__ import annotations

from mcp_wizard_generators.models import (
    DeploymentConfig,
    FileEntry,
    FileTree,
    GenerateContext,
    ServerCapabilities,
)
from mcp_wizard_generators.plugins.deploy import (
    aws_terraform,
    deploy_docs,
    github_actions,
    scripts,
)
from mcp_wizard_generators.plugins.deploy.constants import AGENTCORE_PLATFORM
from mcp_wizard_generators.plugins.deploy.docker import dockerfile, dockerignore

AWS_SECRETS_FEATURE = "aws-secrets"


class AgentCoreContainerDeployment:
    key = "agentcore-container"
    display_name = "AWS Bedrock AgentCore (container)"
    requires = frozenset({"streamable-http"})
    iac_options = frozenset({"terraform"})
    ci_options = frozenset({"none", "github-actions"})
    supports_scripts = True
    supports_inbound_auth = True

    def validate(self, config: DeploymentConfig) -> None:
        if not config.aws_region:
            raise ValueError(f"deployment '{self.key}' needs aws_region")
        if config.inbound_auth.mode != "jwt":
            raise ValueError(
                f"deployment '{self.key}' needs inbound JWT auth: AgentCore checks "
                "every caller's token. Set a discovery URL plus an audience, client or "
                "scope, or create_cognito"
            )

    def server_features(self, config: DeploymentConfig) -> frozenset[str]:
        return frozenset({AWS_SECRETS_FEATURE})

    def generate(self, ctx: GenerateContext, caps: ServerCapabilities) -> FileTree:
        deployment = ctx.deployment
        name = aws_terraform.resource_name(caps)
        entries: list[FileEntry] = [
            FileEntry(
                "Dockerfile",
                dockerfile(
                    caps,
                    deployment=self.key,
                    guide="DEPLOY.md",
                    auth_where="deploy/terraform/main.tf",
                    platform=AGENTCORE_PLATFORM,
                ),
            ),
            FileEntry(".dockerignore", dockerignore()),
            *aws_terraform.terraform_files(ctx, caps),
            FileEntry("DEPLOY.md", deploy_docs.agentcore_deploy_md(ctx, caps, name=name)),
            FileEntry("README.md", self._readme()),
        ]
        if deployment.ci == "github-actions":
            entries += [
                github_actions.ci_workflow(caps, image=False),
                github_actions.agentcore_deploy_workflow(caps, name=name),
                github_actions.dependabot(
                    [
                        "github-actions",
                        "docker",
                        ("terraform", f"/{aws_terraform.TF_DIR}"),
                        ("terraform", f"/{aws_terraform.BOOTSTRAP_DIR}"),
                    ]
                ),
            ]
        if deployment.scripts:
            entries += [*scripts.agentcore_scripts(caps, name=name), scripts.gitattributes()]
        return FileTree(entries=entries, root_name=caps.package_name.replace("_", "-"))

    @staticmethod
    def _readme() -> str:
        return "\n".join(
            [
                "## Deploy to AWS Bedrock AgentCore",
                "",
                "This project runs on AWS Bedrock AgentCore Runtime as an ARM64 container.",
                "AgentCore checks a bearer token from your identity provider on every request;",
                "API credentials are read from AWS Secrets Manager. Terraform in",
                "`deploy/terraform` creates everything. Follow **DEPLOY.md** step by step.",
                "",
            ]
        )
