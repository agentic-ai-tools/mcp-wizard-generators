"""Portable path helpers for local CLI / packaging on macOS, Linux, and Windows."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


def normalize_tree_path(path: str) -> str:
    """Normalize a generated-tree relative path to POSIX separators (zip-safe)."""
    return PurePosixPath(str(path).replace("\\", "/")).as_posix()


def resolve_user_path(path: str | Path) -> Path:
    """Expand ``~`` and resolve to an absolute path (Mac / Windows / Linux)."""
    return Path(path).expanduser().resolve()
