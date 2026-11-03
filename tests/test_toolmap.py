"""Tool mapping unit tests."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_wizard_generators.models import IntermediateRep
from mcp_wizard_generators.toolmap import build_tool_map

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_tool_map_all_ops() -> None:
    ir = IntermediateRep.from_dict(json.loads((FIXTURES / "pets_tiny.ir.json").read_text()))
    tm = build_tool_map(ir)
    assert [t.tool_name for t in tm.tools] == ["listpets", "createpets", "showpetbyid"]
    assert tm.tools[0].method == "get"
    assert tm.tools[0].path == "/pets"


def test_build_tool_map_selection_and_override() -> None:
    ir = IntermediateRep.from_dict(json.loads((FIXTURES / "pets_tiny.ir.json").read_text()))
    tm = build_tool_map(ir, selected_operation_ids=["listPets"], name_overrides={"listPets": "list_all_pets"})
    assert len(tm.tools) == 1
    assert tm.tools[0].tool_name == "list_all_pets"
