"""Cross-platform path / packaging / CLI reliability."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from mcp_wizard_generators.cli import main
from mcp_wizard_generators.models import FileEntry, FileTree
from mcp_wizard_generators.packaging import pack_directory_to_zip, pack_file_tree_to_zip
from mcp_wizard_generators.paths import normalize_tree_path, resolve_user_path


def test_normalize_tree_path_uses_forward_slashes() -> None:
    assert normalize_tree_path(r"src\pkg\server.py") == "src/pkg/server.py"
    assert normalize_tree_path("src/pkg/server.py") == "src/pkg/server.py"
    assert normalize_tree_path(r".cursor\mcp.json") == ".cursor/mcp.json"


def test_file_tree_write_to_joins_posix_entry_paths(tmp_path: Path) -> None:
    tree = FileTree(
        root_name="demo",
        entries=[FileEntry(r"src\pkg\__init__.py", "# ok\n", executable=True)],
    )
    out = tree.write_to(tmp_path / "out")
    written = out / "src" / "pkg" / "__init__.py"
    assert written.is_file()
    assert written.read_text(encoding="utf-8") == "# ok\n"


def test_file_tree_write_to_tolerates_chmod_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = FileTree(
        root_name="demo",
        entries=[FileEntry("bin/tool", "#!/bin/sh\n", executable=True)],
    )

    def boom(self: Path, mode: int) -> None:  # noqa: ARG001
        raise OSError("chmod unsupported")

    monkeypatch.setattr(Path, "chmod", boom)
    out = tree.write_to(tmp_path / "out")
    assert (out / "bin" / "tool").is_file()


def test_pack_directory_to_zip_arcnames_are_posix(tmp_path: Path) -> None:
    root = tmp_path / "demo-mcp"
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    (nested / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "README.md").write_text("# hi\n", encoding="utf-8")

    data = pack_directory_to_zip(root)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(zf.namelist())
    assert names == ["demo-mcp/README.md", "demo-mcp/src/pkg/a.py"]
    assert all("\\" not in name for name in names)
    assert all(PurePosixPath(name).as_posix() == name for name in names)


def test_pack_file_tree_to_zip_normalizes_entry_separators() -> None:
    tree = FileTree(
        root_name="demo-mcp",
        entries=[FileEntry(r"src\pkg\a.py", "x = 1\n")],
    )
    data = pack_file_tree_to_zip(tree)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    assert names == ["demo-mcp/src/pkg/a.py"]


def test_resolve_user_path_expands_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    resolved = resolve_user_path("~/artifact.zip")
    assert resolved == (tmp_path / "artifact.zip").resolve()


def test_cli_generate_python_accepts_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "pets_tiny.ir.json"
    ir = tmp_path / "ir.json"
    ir.write_bytes(fixture.read_bytes())
    out_dir = tmp_path / "generated"
    zip_path = tmp_path / "out.zip"

    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "generate-python",
            "--ir",
            "ir.json",
            "--select",
            "listPets",
            "--package-name",
            "pets_tiny_mcp",
            "--out-dir",
            "generated",
            "--zip",
            "out.zip",
        ]
    )
    assert code == 0
    assert out_dir.is_dir()
    assert zip_path.is_file()
    assert (out_dir / "src" / "pets_tiny_mcp" / "server.py").is_file()


def test_cli_list_targets_exit_zero() -> None:
    assert main(["list-targets"]) == 0
