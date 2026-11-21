"""``agentcore-code``: the server on AWS Bedrock AgentCore Runtime as a zip (ADR-026).

Direct code deployment: ``deploy/agentcore/package.py`` builds an ARM64 zip (hash-locked
wheels, the server package and ``main.py``), which is uploaded to a private S3 bucket
and run by AgentCore's managed Python runtime. No container image, registry or Docker.
Inbound auth, Secrets Manager, Terraform, CI and scripts work as for
``agentcore-container``.
"""

from __future__ import annotations

from mcp_wizard_generators.models import (
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
from mcp_wizard_generators.plugins.deploy.agentcore_container import (
    AgentCoreContainerDeployment,
)
from mcp_wizard_generators.plugins.deploy.agentcore_package import package_files


class AgentCoreCodeDeployment(AgentCoreContainerDeployment):
    """Same options and checks as the container target; a zip instead of an image."""

    key = "agentcore-code"
    display_name = "AWS Bedrock AgentCore (direct code)"

    def generate(self, ctx: GenerateContext, caps: ServerCapabilities) -> FileTree:
        deployment = ctx.deployment
        name = aws_terraform.resource_name(caps)
        entries: list[FileEntry] = [
            *package_files(caps),
            *aws_terraform.terraform_files(ctx, caps, code=True),
            FileEntry(
                "DEPLOY.md", deploy_docs.agentcore_deploy_md(ctx, caps, name=name, code=True)
            ),
            FileEntry("README.md", self._readme()),
        ]
        if deployment.ci == "github-actions":
            entries += [
                github_actions.ci_workflow(caps, image=False, package=True),
                github_actions.agentcore_deploy_workflow(caps, name=name, code=True),
                github_actions.dependabot(
                    [
                        "github-actions",
                        ("terraform", f"/{aws_terraform.TF_DIR}"),
                        ("terraform", f"/{aws_terraform.BOOTSTRAP_DIR}"),
                    ]
                ),
            ]
        if deployment.scripts:
            entries += [
                *scripts.agentcore_scripts(caps, name=name, code=True),
                scripts.gitattributes(),
            ]
        return FileTree(entries=entries, root_name=caps.package_name.replace("_", "-"))

    @staticmethod
    def _readme() -> str:
        return "\n".join(
            [
                "## Deploy to AWS Bedrock AgentCore",
                "",
                "This project runs on AWS Bedrock AgentCore Runtime as an ARM64 zip (direct",
                "code deployment, no container): `deploy/agentcore/package.py` builds it.",
                "AgentCore checks a bearer token from your identity provider on every request;",
                "API credentials are read from AWS Secrets Manager. Terraform in",
                "`deploy/terraform` creates everything. Follow **DEPLOY.md** step by step.",
                "",
            ]
        )
