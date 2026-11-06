"""Golden + zip tests for local Python MCP generation."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from mcp_wizard_generators.models import IntermediateRep
from mcp_wizard_generators.packaging import pack_file_tree_to_zip
from mcp_wizard_generators.python_mcp import generate_python_mcp, generate_python_mcp_result

ROOT = Path(__file__).parent
FIXTURE = ROOT / "fixtures" / "pets_tiny.ir.json"
GOLDEN_DIR = ROOT / "goldens" / "python_mcp_pets_tiny"

EXPECTED_ZIP_MEMBERS = [
    "pets-tiny-mcp/.cursor/mcp.json",
    "pets-tiny-mcp/.env.example",
    "pets-tiny-mcp/README.md",
    "pets-tiny-mcp/generation-manifest.json",
    "pets-tiny-mcp/pyproject.toml",
    "pets-tiny-mcp/requirements.txt",
    "pets-tiny-mcp/src/pets_tiny_mcp/__init__.py",
    "pets-tiny-mcp/src/pets_tiny_mcp/__main__.py",
    "pets-tiny-mcp/src/pets_tiny_mcp/client.py",
    "pets-tiny-mcp/src/pets_tiny_mcp/server.py",
]


def _normalize(content: str) -> str:
    return content.replace("\r\n", "\n")


def test_python_mcp_pets_tiny_golden() -> None:
    ir = IntermediateRep.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    result = generate_python_mcp_result(
        ir,
        ["listPets", "showPetById"],
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
    server = result.tree.get("src/pets_tiny_mcp/server.py")
    assert server is not None
    assert "MCPServer" in server.content or "FastMCP" in server.content
    assert '@mcp.tool(name="listpets"' in server.content
    assert '@mcp.tool(name="showpetbyid"' in server.content
    assert "path_template.format(petId=petId)" in server.content

    cursor = result.tree.get(".cursor/mcp.json")
    assert cursor is not None
    cfg = json.loads(cursor.content)
    assert "mcpServers" in cfg
    assert cfg["mcpServers"]["pets_tiny_mcp"]["args"] == ["-m", "pets_tiny_mcp"]


def test_python_mcp_zip_contains_expected_files(tmp_path: Path) -> None:
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    zip_path = tmp_path / "pets_tiny_mcp.zip"
    out = generate_python_mcp(
        ir,
        ["listPets", "showPetById"],
        out_dir=tmp_path / "tree",
        zip_path=zip_path,
        package_name="pets_tiny_mcp",
    )
    assert out == tmp_path / "tree"
    assert zip_path.is_file()
    assert zipfile.is_zipfile(zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(zf.namelist())
        assert names == EXPECTED_ZIP_MEMBERS
        # Valid zip: every member readable
        for name in names:
            data = zf.read(name)
            assert isinstance(data, bytes)


def test_python_mcp_pack_file_tree_zip_roundtrip() -> None:
    result = generate_python_mcp_result(
        json.loads(FIXTURE.read_text(encoding="utf-8")),
        ["listPets", "showPetById"],
        package_name="pets_tiny_mcp",
    )
    data = pack_file_tree_to_zip(result.tree)
    assert zipfile.is_zipfile(io.BytesIO(data))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == EXPECTED_ZIP_MEMBERS


def test_generate_python_mcp_simple_operations(tmp_path: Path) -> None:
    """API can pass simple operation objects instead of a full IR catalog."""
    out = generate_python_mcp(
        {"title": "Simple Pets", "servers": ["https://example.com"]},
        operations=[
            {
                "operationId": "listPets",
                "method": "get",
                "path": "/pets",
                "summary": "List pets",
                "params": [{"name": "limit", "type": "integer"}],
            }
        ],
        out_dir=tmp_path / "simple",
        package_name="simple_pets_mcp",
    )
    assert (out / "src/simple_pets_mcp/server.py").is_file()
    assert (out / ".cursor/mcp.json").is_file()
    text = (out / "src/simple_pets_mcp/server.py").read_text(encoding="utf-8")
    assert "listpets" in text
