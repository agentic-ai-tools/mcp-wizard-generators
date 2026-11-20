"""Deployment target plugins (design ADR-026): files layered on top of a server tree."""

from mcp_wizard_generators.plugins.deploy.docker import DockerDeployment
from mcp_wizard_generators.plugins.deploy.local import LocalDeployment

__all__ = ["DockerDeployment", "LocalDeployment"]
