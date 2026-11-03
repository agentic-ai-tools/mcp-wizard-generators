"""Zip packaging for generated file trees."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from mcp_wizard_generators.models import FileTree


def pack_file_tree_to_zip(tree: FileTree, dest: str | Path | None = None) -> bytes:
    """
    Pack an in-memory FileTree into a zip archive.

    If ``dest`` is set, also write the bytes to that path.
    Returns the zip bytes (API Artifact Service can stream/store them).
    """
    buf = io.BytesIO()
    prefix = tree.root_name.rstrip("/") + "/"
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in sorted(tree.entries, key=lambda e: e.path):
            arcname = prefix + entry.path
            info = zipfile.ZipInfo(arcname)
            if entry.executable:
                info.external_attr = (0o755 & 0xFFFF) << 16
            else:
                info.external_attr = (0o644 & 0xFFFF) << 16
            zf.writestr(info, entry.content.encode("utf-8"))
    data = buf.getvalue()
    if dest is not None:
        Path(dest).write_bytes(data)
    return data


def pack_directory_to_zip(src_dir: str | Path, dest: str | Path | None = None) -> bytes:
    """Zip an on-disk directory (helper for post-write packaging)."""
    root = Path(src_dir)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(root.parent)))
    data = buf.getvalue()
    if dest is not None:
        Path(dest).write_bytes(data)
    return data
