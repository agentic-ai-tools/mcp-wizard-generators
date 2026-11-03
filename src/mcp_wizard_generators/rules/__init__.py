"""Agent Rules enforcement hooks (Cursor + Anthropic stubs)."""

from mcp_wizard_generators.rules.anthropic import AnthropicRulesEnforcer
from mcp_wizard_generators.rules.base import RulesEnforcer, RulesReport, RulesViolation
from mcp_wizard_generators.rules.cursor import CursorRulesEnforcer
from mcp_wizard_generators.rules.hooks import apply_agent_rules

__all__ = [
    "AnthropicRulesEnforcer",
    "CursorRulesEnforcer",
    "RulesEnforcer",
    "RulesReport",
    "RulesViolation",
    "apply_agent_rules",
]
