"""MCP Wizard generators — IR → MCP tool map → file tree (no HTTP servers)."""

from mcp_wizard_generators.models import (
    FileEntry,
    FileTree,
    GenerateContext,
    IntermediateRep,
    ToolMap,
    ToolMapping,
)
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.registry import GeneratorRegistry, get_default_registry

__all__ = [
    "FileEntry",
    "FileTree",
    "GenerateContext",
    "GeneratorRegistry",
    "IntermediateRep",
    "ToolMap",
    "ToolMapping",
    "generate",
    "get_default_registry",
]

__version__ = "0.1.0"
