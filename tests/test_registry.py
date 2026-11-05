"""Plugin registry smoke tests."""

from __future__ import annotations

import pytest

from mcp_wizard_generators.registry import GeneratorRegistry, get_default_registry
from mcp_wizard_generators.plugins.agentcore import AgentCorePythonPlugin


def test_default_registry_has_local_and_agentcore_python() -> None:
    reg = get_default_registry()
    local = reg.get("local", "python")
    assert local.key == "local"
    assert local.language == "python"
    plugin = reg.get("agentcore", "python")
    assert plugin.key == "agentcore"
    assert plugin.language == "python"
    targets = reg.list_targets()
    assert any(t["key"] == "local" for t in targets)
    assert any(t["key"] == "agentcore" for t in targets)


def test_missing_plugin_raises() -> None:
    reg = GeneratorRegistry()
    reg.register(AgentCorePythonPlugin())
    with pytest.raises(KeyError, match="langgraph"):
        reg.get("langgraph", "python")
