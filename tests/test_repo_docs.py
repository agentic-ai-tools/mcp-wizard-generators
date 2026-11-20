"""The repo README describes what generators actually emit today."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _artifact_table() -> str:
    match = re.search(r"## Generated artifact \(local Python MCP\)\n(.*?)\n## ", README, re.S)
    assert match, "README must keep the generated-artifact section"
    return match.group(1)


def test_no_fastmcp_fallback_claims() -> None:
    # Generated servers are mcp 2.x MCPServer only; the 1.x FastMCP class is never used.
    assert "fastmcp" not in README.lower()
    for path in (ROOT / "src").rglob("*.py"):
        assert "fastmcp" not in path.read_text(encoding="utf-8").lower(), path


def test_artifact_table_lists_every_hardened_file() -> None:
    table = _artifact_table()
    for needle in (
        "requirements.lock",
        "SECURITY-NOTES.md",
        "tests/",
        "pydantic",
        "MCPServer",
    ):
        assert needle in table, needle
