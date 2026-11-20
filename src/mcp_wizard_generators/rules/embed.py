"""Copy the user's Agent Rules originals into the generated project (design ADR-007)."""

from __future__ import annotations

from collections.abc import Collection

from mcp_wizard_generators.models import AgentRulesBundle, FileEntry

# Layouts that tools read from fixed places, so they stay where the user put them.
_KEEP_PREFIXES = (".cursor/", ".claude/")
_KEEP_SUFFIXES = ("CLAUDE.md", "AGENTS.md", ".cursorrules")
RULES_DIR = "agent-rules"


def embed_agent_rules(rules: AgentRulesBundle, taken: Collection[str]) -> list[FileEntry]:
    """Rules files as tree entries. Anything else, or anything that would replace a
    generated file (``taken``), goes under ``agent-rules/``.

    A leading "/" is dropped (rules are project-relative); ``FileEntry`` still refuses
    ".." segments and drive letters, so rules can't escape the project (zip slip).
    """
    entries: list[FileEntry] = []
    for rel, text in sorted(rules.originals.items()):
        path = rel.replace("\\", "/").lstrip("/")
        keep = path.startswith(_KEEP_PREFIXES) or path.endswith(_KEEP_SUFFIXES)
        if not keep or path in taken:
            path = f"{RULES_DIR}/{path}"
        entries.append(FileEntry(path, text if text.endswith("\n") else text + "\n"))
    return entries
