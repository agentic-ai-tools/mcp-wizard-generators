"""Shared ``package_name`` validation (same predicate intended for API + generators)."""

from __future__ import annotations

import keyword
import re

# Snake-case Python identifier: letter start, then letter/digit/underscore, ≤63 chars.
PACKAGE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Small denylist of reserved / confusing module names (beyond language keywords).
PACKAGE_NAME_DENYLIST = frozenset(
    {
        "os",
        "sys",
        "mcp",
        "json",
        "re",
        "typing",
        "pathlib",
        "asyncio",
        "builtins",
        "types",
        "copy",
        "string",
        "http",
        "httpx",
    }
)


def validate_package_name(package_name: str) -> str:
    """
    Validate ``package_name`` used as the Python package / import path.

    Accepts only ``^[a-z][a-z0-9_]{0,62}$``. Rejects Python keywords, a small
    denylist (``os``, ``sys``, ``mcp``, …), and values with path separators,
    dots, hyphens, spaces, or ``..`` (those also fail the pattern).

    Returns the validated name unchanged.
    Raises ``ValueError`` with a message containing ``package_name`` on failure.
    """
    if not isinstance(package_name, str):
        raise ValueError(f"package_name must be a string, got {type(package_name).__name__}")

    # Explicit path / traversal checks (defense in depth; pattern also rejects these).
    if (
        ".." in package_name
        or "/" in package_name
        or "\\" in package_name
        or "." in package_name
        or "-" in package_name
        or " " in package_name
    ):
        raise ValueError(
            "package_name must be a snake_case identifier "
            f"(got {package_name!r}); path separators, dots, hyphens, spaces, "
            "and '..' are not allowed"
        )

    if not PACKAGE_NAME_PATTERN.fullmatch(package_name):
        raise ValueError(f"package_name must match ^[a-z][a-z0-9_]{{0,62}}$ (got {package_name!r})")

    if keyword.iskeyword(package_name):
        raise ValueError(f"package_name must not be a Python keyword (got {package_name!r})")

    if package_name in PACKAGE_NAME_DENYLIST:
        raise ValueError(f"package_name is reserved and not allowed (got {package_name!r})")

    return package_name
