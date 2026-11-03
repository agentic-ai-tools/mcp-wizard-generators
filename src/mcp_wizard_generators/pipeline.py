"""Generation pipeline: IR → tool map → rules hooks → plugin → optional zip."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp_wizard_generators.models import (
    AgentRulesBundle,
    FileTree,
    GenerateContext,
    IntermediateRep,
    SecurityConfig,
    ToolMap,
)
from mcp_wizard_generators.registry import GeneratorRegistry, get_default_registry
from mcp_wizard_generators.rules.base import RulesReport
from mcp_wizard_generators.rules.hooks import apply_agent_rules
from mcp_wizard_generators.toolmap import build_tool_map


@dataclass
class GenerateResult:
    """Outcome of one offline generation run."""

    tree: FileTree
    tool_map: ToolMap
    rules_reports: list[RulesReport] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)


class RulesEnforcementError(RuntimeError):
    """Raised when Agent Rules report error-severity violations."""

    def __init__(self, reports: list[RulesReport]) -> None:
        self.reports = reports
        msgs = [
            f"[{r.family}] {v.code}: {v.message}"
            for r in reports
            for v in r.violations
            if v.severity == "error"
        ]
        super().__init__("Agent Rules enforcement failed: " + "; ".join(msgs))


def generate(
    ir: IntermediateRep | dict[str, Any],
    *,
    target: str = "agentcore",
    language: str = "python",
    selected_operation_ids: list[str] | None = None,
    tool_name_overrides: dict[str, str] | None = None,
    security: SecurityConfig | None = None,
    agent_rules: AgentRulesBundle | None = None,
    package_name: str = "generated_mcp",
    registry: GeneratorRegistry | None = None,
    fail_on_rules_errors: bool = True,
    extras: dict[str, Any] | None = None,
) -> GenerateResult:
    """
    Run the offline generation pipeline.

    Intended call site: ``mcp-wizard-api`` Generate Service / worker — not an HTTP server.
    """
    ir_model = ir if isinstance(ir, IntermediateRep) else IntermediateRep.from_dict(ir)
    selected = selected_operation_ids or [op.id for op in ir_model.operations]
    tool_map = build_tool_map(ir_model, selected, tool_name_overrides)
    rules = agent_rules or AgentRulesBundle()

    # Pre-generate rules pass (tool naming constraints, etc.)
    pre_reports = apply_agent_rules(rules, tool_map, tree=None)

    ctx = GenerateContext(
        ir=ir_model,
        selected_operation_ids=list(selected),
        tool_map=tool_map,
        target=target,
        language=language,
        security=security or SecurityConfig(),
        agent_rules=rules,
        package_name=package_name,
        extras=dict(extras or {}),
    )

    reg = registry or get_default_registry()
    plugin = reg.get(target, language)
    tree = plugin.generate(ctx)

    # Post-generate rules pass (path / layout constraints)
    post_reports = apply_agent_rules(rules, tool_map, tree=tree)
    reports = pre_reports + post_reports

    if fail_on_rules_errors and any(r.has_errors for r in reports):
        raise RulesEnforcementError(reports)

    manifest = {
        "generator": {"target": target, "language": language, "plugin": plugin.display_name},
        "package_name": package_name,
        "operations": selected,
        "tools": [t.tool_name for t in tool_map.tools],
        "files": tree.paths(),
        "rules_families": sorted({r.family for r in reports}),
    }
    return GenerateResult(tree=tree, tool_map=tool_map, rules_reports=reports, manifest=manifest)
