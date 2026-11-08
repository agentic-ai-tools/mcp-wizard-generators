"""Tests for package_name validation (shared predicate for generators + API)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_wizard_generators.package_name import validate_package_name
from mcp_wizard_generators.python_mcp import generate_python_mcp, generate_python_mcp_result

FIXTURE = Path(__file__).parent / "fixtures" / "pets_tiny.ir.json"


@pytest.mark.parametrize(
    "name",
    [
        "pets_tiny_mcp",
        "generated_mcp",
        "a",
        "a" + "b" * 62,  # 63 chars
        "my_server_1",
    ],
)
def test_validate_package_name_accepts_valid(name: str) -> None:
    assert validate_package_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Pets",
        "my-package",
        "my.package",
        "my package",
        "1pets",
        "pets/tiny",
        "pets\\tiny",
        "..",
        "../os",
        "a" + "b" * 63,  # 64 chars
        "os",
        "sys",
        "mcp",
        "class",
        "import",
        "def",
        "My Package!",
    ],
)
def test_validate_package_name_rejects_invalid(name: str) -> None:
    with pytest.raises(ValueError, match="package_name"):
        validate_package_name(name)


@pytest.mark.parametrize(
    "name",
    ["My Package!", "os", "my-package", "pets.tiny", "sys", "mcp"],
)
def test_generate_python_mcp_rejects_bad_package_name_before_write(
    tmp_path: Path, name: str
) -> None:
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    out_dir = tmp_path / "tree"
    zip_path = tmp_path / "out.zip"
    with pytest.raises(ValueError, match="package_name"):
        generate_python_mcp(
            ir,
            ["listPets"],
            out_dir=out_dir,
            zip_path=zip_path,
            package_name=name,
        )
    assert not out_dir.exists()
    assert not zip_path.exists()


def test_generate_python_mcp_result_rejects_bad_package_name() -> None:
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="package_name"):
        generate_python_mcp_result(ir, ["listPets"], package_name="os")
