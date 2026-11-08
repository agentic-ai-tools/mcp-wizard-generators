"""Operations selection + empty-list semantics for generate_python_mcp."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from mcp_wizard_generators.python_mcp import generate_python_mcp, generate_python_mcp_result

FIXTURE = Path(__file__).parent / "fixtures" / "pets_tiny.ir.json"


def test_explicit_empty_operations_raises() -> None:
    """Explicit operations=[] must not expand to all ops — raise instead."""
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="operations"):
        generate_python_mcp_result(ir, operations=[])


def test_explicit_empty_operations_raises_before_write(tmp_path: Path) -> None:
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    out_dir = tmp_path / "tree"
    with pytest.raises(ValueError, match="operations"):
        generate_python_mcp(ir, operations=[], out_dir=out_dir, package_name="pets_tiny_mcp")
    assert not out_dir.exists()


def test_omit_operations_means_all() -> None:
    """None / omit may mean all operations (CLI convenience)."""
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = generate_python_mcp_result(ir, operations=None, package_name="pets_tiny_mcp")
    names = [t.tool_name for t in result.tool_map.tools]
    assert names == ["listpets", "createpets", "showpetbyid"]
    server = result.tree.get("src/pets_tiny_mcp/server.py")
    assert server is not None
    assert "createpets" in server.content


def test_subset_excludes_unselected_createpets_from_server() -> None:
    """Selected listPets+showPetById must not emit createPets/createpets in server.py."""
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = generate_python_mcp_result(
        ir,
        ["listPets", "showPetById"],
        package_name="pets_tiny_mcp",
    )
    server = result.tree.get("src/pets_tiny_mcp/server.py")
    assert server is not None
    assert "createPets" not in server.content
    assert "createpets" not in server.content
    assert '@mcp.tool(name="listpets"' in server.content
    assert '@mcp.tool(name="showpetbyid"' in server.content
    assert [t.tool_name for t in result.tool_map.tools] == ["listpets", "showpetbyid"]
    assert "createPets" not in result.manifest["operations"]
    assert "createpets" not in result.manifest["tools"]


def test_zip_members_under_sanitized_root(tmp_path: Path) -> None:
    """Zip archive members live under package_name with underscores → hyphens."""
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    zip_path = tmp_path / "my_custom_mcp.zip"
    generate_python_mcp(
        ir,
        ["listPets", "showPetById"],
        zip_path=zip_path,
        package_name="my_custom_mcp",
    )
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert names, "zip must contain members"
    for name in names:
        assert name.startswith("my-custom-mcp/"), f"member not under sanitized root: {name}"
        assert ".." not in name
        assert "\\" not in name
    assert any(n.endswith("src/my_custom_mcp/server.py") for n in names)
