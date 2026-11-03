"""Orchestrate Cursor + Anthropic enforcement hooks in the generation pipeline."""

from __future__ import annotations

from mcp_wizard_generators.models import AgentRulesBundle, FileTree, ToolMap
from mcp_wizard_generators.rules.anthropic import AnthropicRulesEnforcer
from mcp_wizard_generators.rules.base import RulesReport
from mcp_wizard_generators.rules.cursor import CursorRulesEnforcer


def apply_agent_rules(
    rules: AgentRulesBundle,
    tool_map: ToolMap,
    tree: FileTree | None = None,
) -> list[RulesReport]:
    """Run registered family stubs; empty originals → no-op reports."""
    if not rules.originals and not rules.unified_constraints:
        return []

    reports: list[RulesReport] = []
    family = rules.format_family
    if family in {"cursor", "mixed", "unknown"}:
        reports.append(CursorRulesEnforcer().enforce(rules, tool_map, tree))
    if family in {"anthropic", "mixed", "unknown"}:
        reports.append(AnthropicRulesEnforcer().enforce(rules, tool_map, tree))
    return reports
