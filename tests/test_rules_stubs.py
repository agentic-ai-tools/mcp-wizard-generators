"""Cursor / Anthropic enforcement hook stubs."""

from __future__ import annotations

from mcp_wizard_generators.models import AgentRulesBundle, ToolMap
from mcp_wizard_generators.rules import apply_agent_rules


def test_empty_rules_noop() -> None:
    assert apply_agent_rules(AgentRulesBundle(), ToolMap()) == []


def test_cursor_stub_warns_without_recognized_files() -> None:
    rules = AgentRulesBundle(format_family="cursor", originals={"notes.txt": "hi"})
    reports = apply_agent_rules(rules, ToolMap())
    assert any(r.family == "cursor" for r in reports)
    assert any(v.code == "cursor.no_recognized_files" for r in reports for v in r.violations)


def test_anthropic_stub_accepts_claude_md() -> None:
    rules = AgentRulesBundle(
        format_family="anthropic",
        originals={"CLAUDE.md": "# Project instructions\n"},
    )
    reports = apply_agent_rules(rules, ToolMap())
    anthropic = next(r for r in reports if r.family == "anthropic")
    assert anthropic.violations == []
