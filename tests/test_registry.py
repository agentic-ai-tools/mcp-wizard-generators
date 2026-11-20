"""Plugin registry smoke tests."""

from __future__ import annotations

import pytest

from mcp_wizard_generators.plugins.python_mcp import PythonMcpPlugin
from mcp_wizard_generators.registry import GeneratorRegistry, get_default_registry


def test_default_registry_has_python_mcp() -> None:
    reg = get_default_registry()
    plugin = reg.server("python-mcp", "python")
    assert plugin.key == "python-mcp"
    assert plugin.language == "python"
    assert "stdio" in plugin.transports
    targets = reg.list_targets()
    assert any(t["key"] == "python-mcp" for t in targets)


def test_missing_plugin_raises() -> None:
    reg = GeneratorRegistry()
    reg.register(PythonMcpPlugin())
    with pytest.raises(KeyError, match="langgraph"):
        reg.get("langgraph", "python")
    with pytest.raises(KeyError, match="docker"):
        reg.deployment("docker")
