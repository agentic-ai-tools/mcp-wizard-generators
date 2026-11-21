"""Plugin registry: server architectures and deployment targets (design ADR-026).

A generation always uses one **server** plugin, keyed by ``(architecture, language)``,
plus one **deployment** plugin, keyed by target. Deployment plugins add files on top
of the server tree and read only the server's ``ServerCapabilities``.
"""

from __future__ import annotations

from typing import Protocol

from mcp_wizard_generators.models import (
    DeploymentConfig,
    FileTree,
    GenerateContext,
    ServerCapabilities,
)

# Older names for an architecture. "local" was the python-mcp plugin's key before 0.6.
ARCHITECTURE_ALIASES = {"local": "python-mcp"}


class ServerPlugin(Protocol):
    """Emits the MCP server itself and reports what it supports."""

    key: str
    language: str
    display_name: str
    transports: frozenset[str]
    # Optional features a deployment may switch on, for example "jwt-auth".
    features: frozenset[str]

    def generate(self, ctx: GenerateContext) -> tuple[FileTree, ServerCapabilities]: ...


# Pre-0.6 name for a server plugin.
GeneratorPlugin = ServerPlugin


class DeploymentPlugin(Protocol):
    """Adds deployment files on top of a server tree; never changes server code."""

    key: str
    display_name: str
    requires: frozenset[str]  # transports the server must support
    iac_options: frozenset[str]
    ci_options: frozenset[str]
    supports_scripts: bool
    supports_inbound_auth: bool

    def validate(self, config: DeploymentConfig) -> None: ...

    def server_features(self, config: DeploymentConfig) -> frozenset[str]:
        """Server features this deployment needs for ``config``."""
        ...

    def generate(self, ctx: GenerateContext, caps: ServerCapabilities) -> FileTree: ...


def check_deployment(plugin: DeploymentPlugin, config: DeploymentConfig) -> None:
    """Reject options the deployment plugin cannot honor, before anything is generated."""
    if config.iac not in plugin.iac_options:
        raise ValueError(
            f"deployment {plugin.key!r} does not support iac={config.iac!r}; "
            f"supported: {sorted(plugin.iac_options)}"
        )
    if config.ci not in plugin.ci_options:
        raise ValueError(
            f"deployment {plugin.key!r} does not support ci={config.ci!r}; "
            f"supported: {sorted(plugin.ci_options)}"
        )
    if config.scripts and not plugin.supports_scripts:
        raise ValueError(f"deployment {plugin.key!r} does not generate deploy scripts")
    if config.inbound_auth.mode != "none" and not plugin.supports_inbound_auth:
        raise ValueError(
            f"deployment {plugin.key!r} has no network endpoint, so inbound auth "
            "does not apply; use mode='none'"
        )
    plugin.validate(config)


class GeneratorRegistry:
    """In-process plugin registry (no network; no long-running servers)."""

    def __init__(self) -> None:
        self._servers: dict[tuple[str, str], ServerPlugin] = {}
        self._deployments: dict[str, DeploymentPlugin] = {}

    def register_server(self, plugin: ServerPlugin) -> None:
        self._servers[(plugin.key, plugin.language)] = plugin

    # Pre-0.6 name.
    register = register_server

    def register_deployment(self, plugin: DeploymentPlugin) -> None:
        self._deployments[plugin.key] = plugin

    def server(self, architecture: str, language: str = "python") -> ServerPlugin:
        key = (ARCHITECTURE_ALIASES.get(architecture, architecture), language)
        if key not in self._servers:
            available = ", ".join(f"{a}/{lang}" for a, lang in sorted(self._servers)) or "(none)"
            raise KeyError(
                f"No server generator for architecture={architecture!r} "
                f"language={language!r}. Registered: {available}"
            )
        return self._servers[key]

    # Pre-0.6 name.
    get = server

    def deployment(self, target: str) -> DeploymentPlugin:
        if target not in self._deployments:
            available = ", ".join(sorted(self._deployments)) or "(none)"
            raise KeyError(f"No deployment target {target!r}. Registered: {available}")
        return self._deployments[target]

    def list_architectures(self) -> list[dict[str, str]]:
        return [
            {"key": p.key, "language": p.language, "display_name": p.display_name}
            for p in self._servers.values()
        ]

    # Pre-0.6 name.
    list_targets = list_architectures

    def list_deployments(self) -> list[dict[str, str]]:
        return [{"key": p.key, "display_name": p.display_name} for p in self._deployments.values()]


_default: GeneratorRegistry | None = None


def get_default_registry() -> GeneratorRegistry:
    """Lazy-built registry with the shipped server and deployment plugins."""
    global _default
    if _default is None:
        from mcp_wizard_generators.plugins.deploy import (
            AgentCoreCodeDeployment,
            AgentCoreContainerDeployment,
            DockerDeployment,
            LocalDeployment,
        )
        from mcp_wizard_generators.plugins.python_mcp import PythonMcpPlugin

        registry = GeneratorRegistry()
        registry.register_server(PythonMcpPlugin())
        registry.register_deployment(LocalDeployment())
        registry.register_deployment(DockerDeployment())
        registry.register_deployment(AgentCoreContainerDeployment())
        registry.register_deployment(AgentCoreCodeDeployment())
        _default = registry
    return _default
