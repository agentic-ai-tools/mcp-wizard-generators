"""Generation pipeline (design ADR-026).

IR → tool map → rules hooks → server plugin → deployment plugin → merged tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp_wizard_generators.models import (
    AgentRulesBundle,
    DeploymentConfig,
    FileTree,
    GenerateContext,
    IntermediateRep,
    SecurityConfig,
    ToolMap,
)
from mcp_wizard_generators.package_name import validate_package_name
from mcp_wizard_generators.registry import (
    GeneratorRegistry,
    check_deployment,
    get_default_registry,
)
from mcp_wizard_generators.rules.base import RulesReport
from mcp_wizard_generators.rules.embed import embed_agent_rules
from mcp_wizard_generators.rules.hooks import apply_agent_rules
from mcp_wizard_generators.toolmap import build_tool_map

DEFAULT_ARCHITECTURE = "python-mcp"
# Deployment plugins may append a section to these server files; any other overlap fails.
APPENDABLE_PATHS = frozenset({"README.md"})


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


def resolve_target(
    target: str | None,
    architecture: str | None,
    deployment: DeploymentConfig | None,
) -> tuple[str, DeploymentConfig]:
    """Map the pre-0.6 ``target`` argument onto (architecture, deployment)."""
    if target is None:
        return architecture or DEFAULT_ARCHITECTURE, deployment or DeploymentConfig()
    if architecture is not None or deployment is not None:
        raise ValueError("pass target or architecture/deployment, not both")
    if target == "agentcore":
        # The pre-0.6 AgentCore plugin was a stub with a placeholder server. AgentCore is
        # now a deployment target and needs inbound auth settings, so there is no safe
        # default to map this alias onto.
        raise ValueError(
            "target='agentcore' was removed in 0.6 (design ADR-026). Use "
            "architecture='python-mcp' with deployment=DeploymentConfig("
            "target='agentcore-container', inbound_auth=InboundAuthConfig(...))"
        )
    # "local" (and any architecture key) means that server with the local deployment.
    return target, DeploymentConfig()


def generate(
    ir: IntermediateRep | dict[str, Any],
    *,
    architecture: str | None = None,
    deployment: DeploymentConfig | None = None,
    target: str | None = None,
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

    ``architecture`` picks the server plugin (default ``python-mcp``) and ``deployment``
    the deployment target (default ``local``). ``target`` is the pre-0.6 single key:
    ``local`` still works; it cannot be combined with the new arguments.

    ``selected_operation_ids=None`` means all IR operations. An explicit empty list
    raises ``ValueError`` (does not expand to all). ``package_name`` and the deployment
    options are validated before any plugin writes a tree.
    """
    architecture, deployment = resolve_target(target, architecture, deployment)
    package_name = validate_package_name(package_name)
    reg = registry or get_default_registry()
    server = reg.server(architecture, language)
    deploy = reg.deployment(deployment.target)
    check_deployment(deploy, deployment)

    ir_model = ir if isinstance(ir, IntermediateRep) else IntermediateRep.from_dict(ir)
    if selected_operation_ids is None:
        selected = [op.id for op in ir_model.operations]
    elif len(selected_operation_ids) == 0:
        raise ValueError(
            "selected_operation_ids must not be an empty list; pass None for all "
            "operations, or a non-empty selection"
        )
    else:
        selected = list(selected_operation_ids)
        known = {op.id for op in ir_model.operations}
        unknown = [op_id for op_id in selected if op_id not in known]
        if unknown:
            raise ValueError("unknown operation id(s) in selection: " + ", ".join(unknown))
    tool_map = build_tool_map(ir_model, selected, tool_name_overrides)
    if selected and not tool_map.tools:
        raise ValueError(
            "selected_operation_ids produced no tools; check that ids match IR operation ids"
        )
    rules = agent_rules or AgentRulesBundle()

    # Pre-generate rules pass (tool naming constraints, etc.)
    pre_reports = apply_agent_rules(rules, tool_map, tree=None)

    ctx = GenerateContext(
        ir=ir_model,
        selected_operation_ids=list(selected),
        tool_map=tool_map,
        architecture=server.key,
        language=language,
        deployment=deployment,
        security=security or SecurityConfig(),
        agent_rules=rules,
        package_name=package_name,
        extras=dict(extras or {}),
    )

    tree, caps = server.generate(ctx)
    missing = deploy.requires - caps.transports
    if missing:
        raise ValueError(
            f"deployment {deploy.key!r} needs transport(s) {sorted(missing)}, but the "
            f"{server.key!r} server supports only {sorted(caps.transports)}"
        )
    tree = tree.merge(deploy.generate(ctx, caps), append=APPENDABLE_PATHS)
    tree = tree.merge(FileTree(entries=embed_agent_rules(rules, set(tree.paths()))))

    # Post-generate rules pass (path / layout constraints)
    post_reports = apply_agent_rules(rules, tool_map, tree=tree)
    reports = pre_reports + post_reports

    if fail_on_rules_errors and any(r.has_errors for r in reports):
        raise RulesEnforcementError(reports)

    manifest = {
        "generator": {
            "architecture": server.key,
            "deployment": deploy.key,
            "target": deploy.key,  # pre-0.6 field name
            "language": language,
            "plugin": server.display_name,
            "deploymentPlugin": deploy.display_name,
        },
        "package_name": package_name,
        "operations": selected,
        "tools": [t.tool_name for t in tool_map.tools],
        "files": tree.paths(),
        "rules_families": sorted({r.family for r in reports}),
        "deployment": deployment.manifest(),
    }
    return GenerateResult(tree=tree, tool_map=tool_map, rules_reports=reports, manifest=manifest)
