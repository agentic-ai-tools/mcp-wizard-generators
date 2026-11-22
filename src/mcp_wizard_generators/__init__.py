"""MCP Wizard generators — IR → MCP tool map → file tree (no HTTP servers)."""

from mcp_wizard_generators.findings import Finding, operation_findings
from mcp_wizard_generators.models import (
    DeploymentConfig,
    FileEntry,
    FileTree,
    GenerateContext,
    InboundAuthConfig,
    IntermediateRep,
    ServerCapabilities,
    ToolMap,
    ToolMapping,
)
from mcp_wizard_generators.package_name import validate_package_name
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.python_mcp import generate_python_mcp, generate_python_mcp_result
from mcp_wizard_generators.registry import GeneratorRegistry, get_default_registry

__all__ = [
    "DeploymentConfig",
    "FileEntry",
    "FileTree",
    "Finding",
    "GenerateContext",
    "GeneratorRegistry",
    "InboundAuthConfig",
    "IntermediateRep",
    "ServerCapabilities",
    "ToolMap",
    "ToolMapping",
    "generate",
    "generate_python_mcp",
    "generate_python_mcp_result",
    "get_default_registry",
    "operation_findings",
    "validate_package_name",
]

__version__ = "0.6.0"
