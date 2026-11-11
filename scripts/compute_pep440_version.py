#!/usr/bin/env python3
"""Compute a PEP 440 public version string from GitHub branch kind.

Branch policy (no local ``+…`` segments — PyPI rejects them):

* feature (anything except ``main`` / ``release/*``) → ``{base}.dev{N}``
  where ``N = (crc32(normalized_branch) % 90000 + 10000) * 100000
  + (GITHUB_RUN_NUMBER % 100000)``
* ``main`` → ``{base}rc{N}`` with ``N = GITHUB_RUN_NUMBER``
* ``release/X.Y.Z`` → final ``X.Y.Z`` (must match pyproject + ``__version__``)

Emits one line on stdout; exits non-zero on illegal branch/version.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
import zlib
from pathlib import Path

from packaging.version import InvalidVersion, Version

RELEASE_PREFIX = "release/"
INIT_VERSION_RE = re.compile(
    r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$',
    re.MULTILINE,
)


def normalize_branch(branch: str) -> str:
    """Lowercase; replace each run of non-alphanumeric chars with a single '-'."""
    lowered = branch.lower()
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


def _die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _read_pyproject_version(repo_root: Path) -> str:
    path = repo_root / "pyproject.toml"
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return str(data["project"]["version"])


def _read_init_version(repo_root: Path) -> str:
    path = repo_root / "src" / "mcp_wizard_generators" / "__init__.py"
    text = path.read_text(encoding="utf-8")
    match = INIT_VERSION_RE.search(text)
    if not match:
        _die(f"could not parse __version__ from {path}")
    return match.group(1)


def _parse_public(version: str, *, label: str) -> Version:
    try:
        parsed = Version(version)
    except InvalidVersion as exc:
        _die(f"invalid {label} version {version!r}: {exc}")
    if parsed.local is not None:
        _die(f"{label} version must not include a local segment: {version!r}")
    return parsed


def _assert_final(version: str, *, label: str) -> Version:
    parsed = _parse_public(version, label=label)
    if parsed.is_devrelease or parsed.is_prerelease or parsed.is_postrelease:
        _die(f"{label} must be a final PEP 440 version (no .dev / a / b / rc / .post): {version!r}")
    return parsed


def compute_pep440_version(
    *,
    branch: str,
    base_version: str,
    run_number: int,
    pyproject_version: str,
    init_version: str,
) -> str:
    """Return the public PEP 440 version for *branch*; raise SystemExit on error."""
    if not branch:
        _die("branch name is empty")
    if run_number < 0:
        _die(f"run_number must be >= 0, got {run_number}")

    base = _parse_public(base_version, label="base")
    # Strip any pre/dev from the static base for .dev / rc composition.
    base_public = f"{base.major}.{base.minor}.{base.micro}"

    if branch == "main":
        candidate = f"{base_public}rc{run_number}"
        parsed = _parse_public(candidate, label="main")
        if not parsed.is_prerelease or parsed.is_devrelease:
            _die(f"internal error: expected rc pre-release, got {candidate!r}")
        return candidate

    if branch.startswith(RELEASE_PREFIX):
        release_ver = branch[len(RELEASE_PREFIX) :]
        if not release_ver or "/" in release_ver:
            _die(f"illegal release branch pattern: {branch!r}")
        _assert_final(release_ver, label="release branch")
        if pyproject_version != release_ver:
            _die(
                f"pyproject version {pyproject_version!r} must equal "
                f"release branch version {release_ver!r}"
            )
        if init_version != release_ver:
            _die(f"__version__ {init_version!r} must equal release branch version {release_ver!r}")
        if pyproject_version != init_version:
            _die(f"pyproject ({pyproject_version!r}) and __version__ ({init_version!r}) disagree")
        return release_ver

    # Feature / anything else → developmental .dev{N}
    normalized = normalize_branch(branch)
    if not normalized:
        _die(f"branch normalizes to empty string: {branch!r}")
    band = (zlib.crc32(normalized.encode("utf-8")) % 90000) + 10000
    n = band * 100000 + (run_number % 100000)
    candidate = f"{base_public}.dev{n}"
    parsed = _parse_public(candidate, label="feature")
    if not parsed.is_devrelease:
        _die(f"internal error: expected .dev release, got {candidate!r}")
    if parsed.local is not None or "+" in candidate:
        _die(f"local version segments are forbidden: {candidate!r}")
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--branch",
        default=None,
        help="Git branch name (default: GITHUB_REF_NAME)",
    )
    parser.add_argument(
        "--run-number",
        type=int,
        default=None,
        help="CI run number (default: GITHUB_RUN_NUMBER)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root containing pyproject.toml (default: cwd)",
    )
    parser.add_argument(
        "--base-version",
        default=None,
        help="Override base version (default: [project].version from pyproject)",
    )
    args = parser.parse_args(argv)

    branch = args.branch if args.branch is not None else os.environ.get("GITHUB_REF_NAME", "")
    if not branch:
        _die("branch required via --branch or GITHUB_REF_NAME")

    run_raw = (
        str(args.run_number)
        if args.run_number is not None
        else os.environ.get("GITHUB_RUN_NUMBER", "")
    )
    if run_raw == "":
        _die("run number required via --run-number or GITHUB_RUN_NUMBER")
    try:
        run_number = int(run_raw)
    except ValueError:
        _die(f"invalid run number: {run_raw!r}")

    repo_root = (args.repo_root or Path.cwd()).resolve()
    pyproject_version = _read_pyproject_version(repo_root)
    init_version = _read_init_version(repo_root)
    base_version = args.base_version or pyproject_version

    version = compute_pep440_version(
        branch=branch,
        base_version=base_version,
        run_number=run_number,
        pyproject_version=pyproject_version,
        init_version=init_version,
    )
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
