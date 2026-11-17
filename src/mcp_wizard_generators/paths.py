"""Portable path helpers for local CLI / packaging on macOS, Linux, and Windows."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

_DRIVE = re.compile(r"^[A-Za-z]:")


def normalize_tree_path(path: str) -> str:
    """Normalize a generated-tree relative path to POSIX separators (zip-safe)."""
    return PurePosixPath(str(path).replace("\\", "/")).as_posix()


def safe_tree_path(path: str) -> str:
    """Normalize ``path`` and refuse anything that could land outside the project root.

    Rejects empty paths, absolute paths, drive letters, ``..`` segments, and NUL bytes,
    so a generated tree can't escape its root on disk or in a zip (zip slip).
    """
    raw = str(path)
    normalized = normalize_tree_path(raw)
    if (
        not raw.strip()
        or "\x00" in raw
        or normalized.startswith("/")
        or _DRIVE.match(normalized)
        or ".." in PurePosixPath(normalized).parts
        or normalized in (".", "")
    ):
        raise ValueError(f"unsafe generated file path {raw!r}: must stay inside the project")
    return normalized


def resolve_user_path(path: str | Path) -> Path:
    """Expand ``~`` and resolve to an absolute path (Mac / Windows / Linux)."""
    return Path(path).expanduser().resolve()
