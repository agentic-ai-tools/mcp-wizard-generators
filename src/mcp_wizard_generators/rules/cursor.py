"""Cursor Agent Rules enforcement stub.

Future: parse `.cursor/rules/*.mdc`, `.cursorrules`, `AGENTS.md` and apply
naming/path/pattern constraints during generation (ADR-007).
"""

from __future__ import annotations

from mcp_wizard_generators.models import AgentRulesBundle, FileTree, ToolMap
from mcp_wizard_generators.rules.base import RulesReport, RulesViolation


class CursorRulesEnforcer:
    """Stub enforcer — detects Cursor-shaped originals; no hard fails yet."""

    family = "cursor"

    def enforce(
        self,
        rules: AgentRulesBundle,
        tool_map: ToolMap,
        tree: FileTree | None = None,
    ) -> RulesReport:
        _ = tool_map, tree
        violations: list[RulesViolation] = []
        cursor_files = [
            p
            for p in rules.originals
            if p.endswith(".mdc")
            or p.endswith(".cursorrules")
            or p.endswith("AGENTS.md")
            or "/.cursor/" in p
            or p.startswith(".cursor/")
        ]
        if rules.format_family in {"cursor", "mixed"} and not cursor_files and rules.originals:
            violations.append(
                RulesViolation(
                    severity="warn",
                    code="cursor.no_recognized_files",
                    message="format_family hints Cursor but no .mdc/.cursorrules/AGENTS.md paths found",
                )
            )
        # Scaffold placeholder: real constraint evaluation lands in a later PR.
        return RulesReport(family=self.family, violations=violations)
