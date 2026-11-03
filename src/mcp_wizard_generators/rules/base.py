"""Shared Agent Rules enforcement types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from mcp_wizard_generators.models import AgentRulesBundle, FileTree, ToolMap


@dataclass(frozen=True)
class RulesViolation:
    severity: str  # error | warn | info
    code: str
    message: str
    path: str | None = None


@dataclass
class RulesReport:
    family: str
    violations: list[RulesViolation] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(v.severity == "error" for v in self.violations)


class RulesEnforcer(Protocol):
    family: str

    def enforce(
        self,
        rules: AgentRulesBundle,
        tool_map: ToolMap,
        tree: FileTree | None = None,
    ) -> RulesReport: ...
