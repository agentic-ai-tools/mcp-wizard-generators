"""Compile the hash lock shipped in every generated python-mcp project (ADR-023).

    python scripts/lock_python_mcp.py            # refresh after editing requirements.in
    python scripts/lock_python_mcp.py --upgrade  # move every pin to the newest allowed
    python scripts/lock_python_mcp.py --check    # CI: fail if the lock is out of date

The worker that builds zips has no network, so the lock is prepared here and
packaged. It is universal (every OS, CPython >= 3.11) and pins sha256 hashes.
Needs `uv` (installed with the dev extras) and network access.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = ROOT / "src" / "mcp_wizard_generators" / "plugins" / "python_mcp" / "lock"
SOURCE = LOCK_DIR / "requirements.in"
LOCK = LOCK_DIR / "requirements.txt"
SELF = "python scripts/lock_python_mcp.py"


def compile_command(output: Path, *, upgrade: bool = False) -> list[str]:
    uv = shutil.which("uv")
    base = [uv] if uv else [sys.executable, "-m", "uv"]
    cmd = [
        *base,
        "pip",
        "compile",
        SOURCE.name,
        "--universal",
        "--generate-hashes",
        "--python-version",
        "3.11",
        "--custom-compile-command",
        SELF,
        "--quiet",
        "--output-file",
        str(output),
    ]
    return [*cmd, "--upgrade"] if upgrade else cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail if the lock would change")
    mode.add_argument("--upgrade", action="store_true", help="move pins to the newest allowed")
    args = parser.parse_args(argv)

    if not args.check:
        subprocess.run(compile_command(LOCK, upgrade=args.upgrade), cwd=LOCK_DIR, check=True)
        print(f"wrote {LOCK.relative_to(ROOT)}")
        return 0

    # Re-resolve with the current pins as preferences; any change means drift.
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "requirements.txt"
        shutil.copyfile(LOCK, candidate)
        subprocess.run(compile_command(candidate), cwd=LOCK_DIR, check=True)
        current = LOCK.read_text(encoding="utf-8").replace("\r\n", "\n")
        fresh = candidate.read_text(encoding="utf-8").replace("\r\n", "\n")
    if current != fresh:
        print(
            f"{LOCK.relative_to(ROOT)} is out of date with {SOURCE.name}; run: {SELF}",
            file=sys.stderr,
        )
        return 1
    print("python-mcp lock is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
