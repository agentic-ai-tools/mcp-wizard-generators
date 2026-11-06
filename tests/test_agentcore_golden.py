"""Fixture-based golden test for AgentCore + tiny pets OpenAPI IR subset."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_wizard_generators.models import IntermediateRep
from mcp_wizard_generators.pipeline import generate

ROOT = Path(__file__).parent
FIXTURE = ROOT / "fixtures" / "pets_tiny.ir.json"
GOLDEN_DIR = ROOT / "goldens" / "agentcore_pets_tiny"


def _normalize(content: str) -> str:
    return content.replace("\r\n", "\n")


def test_agentcore_pets_tiny_golden() -> None:
    ir = IntermediateRep.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    # Tiny subset: list + show only (createPets excluded on purpose).
    result = generate(
        ir,
        target="agentcore",
        language="python",
        selected_operation_ids=["listPets", "showPetById"],
        package_name="pets_tiny_mcp",
    )

    expected_paths = sorted(
        str(p.relative_to(GOLDEN_DIR)).replace("\\", "/")
        for p in GOLDEN_DIR.rglob("*")
        if p.is_file()
    )
    actual_paths = result.tree.paths()
    assert actual_paths == expected_paths, (
        f"file tree drift.\nactual={actual_paths}\nexpected={expected_paths}"
    )

    for entry in result.tree.entries:
        golden_file = GOLDEN_DIR / entry.path
        assert golden_file.is_file(), f"missing golden: {entry.path}"
        assert _normalize(entry.content) == _normalize(golden_file.read_text(encoding="utf-8")), (
            f"content drift: {entry.path}"
        )

    assert [t.tool_name for t in result.tool_map.tools] == ["listpets", "showpetbyid"]
