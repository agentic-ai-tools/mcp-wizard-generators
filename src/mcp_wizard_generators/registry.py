"""Pluggable generator registry keyed by (targetArchitecture, language)."""

from __future__ import annotations

from typing import Protocol

from mcp_wizard_generators.models import FileTree, GenerateContext


class GeneratorPlugin(Protocol):
    """Target + language plugin that emits a file tree."""

    key: str
    language: str
    display_name: str

    def generate(self, ctx: GenerateContext) -> FileTree: ...


class GeneratorRegistry:
    """In-process plugin registry (no network; no long-running servers)."""

    def __init__(self) -> None:
        self._plugins: dict[tuple[str, str], GeneratorPlugin] = {}

    def register(self, plugin: GeneratorPlugin) -> None:
        self._plugins[(plugin.key, plugin.language)] = plugin

    def get(self, target: str, language: str = "python") -> GeneratorPlugin:
        key = (target, language)
        if key not in self._plugins:
            available = ", ".join(f"{t}/{lang}" for t, lang in sorted(self._plugins)) or "(none)"
            raise KeyError(
                f"No generator for target={target!r} language={language!r}. "
                f"Registered: {available}"
            )
        return self._plugins[key]

    def list_targets(self) -> list[dict[str, str]]:
        return [
            {
                "key": plugin.key,
                "language": plugin.language,
                "display_name": plugin.display_name,
            }
            for plugin in self._plugins.values()
        ]


_default: GeneratorRegistry | None = None


def get_default_registry() -> GeneratorRegistry:
    """Lazy-built registry with MVP plugins (local Python MCP + AgentCore scaffold)."""
    global _default
    if _default is None:
        from mcp_wizard_generators.plugins.agentcore import AgentCorePythonPlugin
        from mcp_wizard_generators.plugins.python_mcp import PythonMcpPlugin

        registry = GeneratorRegistry()
        registry.register(PythonMcpPlugin())
        registry.register(AgentCorePythonPlugin())
        _default = registry
    return _default
