"""Zip packaging helper tests."""

from __future__ import annotations

import io
import zipfile

from mcp_wizard_generators.models import FileEntry, FileTree
from mcp_wizard_generators.packaging import pack_file_tree_to_zip


def test_pack_file_tree_to_zip_roundtrip() -> None:
    tree = FileTree(
        root_name="demo-mcp",
        entries=[
            FileEntry("README.md", "# hi\n"),
            FileEntry("src/pkg/__init__.py", ""),
        ],
    )
    data = pack_file_tree_to_zip(tree)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(zf.namelist())
    assert names == ["demo-mcp/README.md", "demo-mcp/src/pkg/__init__.py"]
