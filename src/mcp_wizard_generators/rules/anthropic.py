"""Anthropic Agent Rules enforcement stub.

Future: parse `CLAUDE.md` / `.claude/` instruction files and apply project
instruction constraints during generation (ADR-007).
"""

from __future__ import annotations

from mcp_wizard_generators.models import AgentRulesBundle, FileTree, ToolMap
from mcp_wizard_generators.rules.base import RulesReport, RulesViolation


class AnthropicRulesEnforcer:
    """Stub enforcer — detects Anthropic-shaped originals; no hard fails yet."""

    family = "anthropic"

    def enforce(
        self,
        rules: AgentRulesBundle,
        tool_map: ToolMap,
        tree: FileTree | None = None,
    ) -> RulesReport:
        _ = tool_map, tree
        violations: list[RulesViolation] = []
        anthropic_files = [
            p
            for p in rules.originals
            if p.endswith("CLAUDE.md")
            or "/.claude/" in p
            or p.startswith(".claude/")
        ]
        if rules.format_family in {"anthropic", "mixed"} and not anthropic_files and rules.originals:
            violations.append(
                RulesViolation(
                    severity="warn",
                    code="anthropic.no_recognized_files",
                    message="format_family hints Anthropic but no CLAUDE.md/.claude paths found",
                )
            )
        return RulesReport(family=self.family, violations=violations)
