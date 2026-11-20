"""``local``: run the server on the developer's machine over stdio (the default)."""

from __future__ import annotations

from mcp_wizard_generators.models import (
    DeploymentConfig,
    FileEntry,
    FileTree,
    GenerateContext,
    ServerCapabilities,
)
from mcp_wizard_generators.plugins.deploy import github_actions


class LocalDeployment:
    """Adds nothing: the server tree already runs locally (README, .cursor/mcp.json)."""

    key = "local"
    display_name = "Local (stdio)"
    requires = frozenset({"stdio"})
    iac_options = frozenset({"none"})
    ci_options = frozenset({"none", "github-actions"})
    supports_scripts = False
    # stdio has no network endpoint, so there is nothing to authenticate.
    supports_inbound_auth = False

    def validate(self, config: DeploymentConfig) -> None:
        if config.aws_region:
            raise ValueError("deployment 'local' does not use aws_region")

    def server_features(self, config: DeploymentConfig) -> frozenset[str]:
        return frozenset()

    def generate(self, ctx: GenerateContext, caps: ServerCapabilities) -> FileTree:
        entries: list[FileEntry] = []
        if ctx.deployment.ci == "github-actions":
            entries += [
                github_actions.ci_workflow(caps, image=False),
                github_actions.dependabot(["github-actions"]),
            ]
        return FileTree(entries=entries, root_name=caps.package_name.replace("_", "-"))
