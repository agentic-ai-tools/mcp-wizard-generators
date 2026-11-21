"""Compile the hash locks shipped in generated python-mcp projects (ADR-023, ADR-026).

    python scripts/lock_python_mcp.py            # refresh after editing a requirements*.in
    python scripts/lock_python_mcp.py --upgrade  # move every pin to the newest allowed
    python scripts/lock_python_mcp.py --check    # CI: fail if a lock is out of date

The worker that builds zips has no network, so the locks are prepared here and
packaged. They are universal (every OS, CPython >= 3.11) and pin sha256 hashes.

- requirements.txt      every project (requirements.in)
- requirements-aws.txt  servers that read credentials from AWS Secrets Manager
                        (AgentCore deployments): requirements.in plus boto3,
                        constrained to the base lock so shared pins stay identical

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
AWS_SOURCE = LOCK_DIR / "requirements-aws.in"
AWS_LOCK = LOCK_DIR / "requirements-aws.txt"
# Order matters: the AWS lock is constrained by the base lock.
LOCKS = ((SOURCE, LOCK), (AWS_SOURCE, AWS_LOCK))
SELF = "python scripts/lock_python_mcp.py"


def compile_command(output: Path, *, upgrade: bool = False, source: Path = SOURCE) -> list[str]:
    uv = shutil.which("uv")
    base = [uv] if uv else [sys.executable, "-m", "uv"]
    cmd = [
        *base,
        "pip",
        "compile",
        source.name,
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


def _up_to_date(source: Path, lock: Path) -> bool:
    # Re-resolve with the current pins as preferences; any change means drift.
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / lock.name
        shutil.copyfile(lock, candidate)
        subprocess.run(compile_command(candidate, source=source), cwd=LOCK_DIR, check=True)
        current = lock.read_text(encoding="utf-8").replace("\r\n", "\n")
        fresh = candidate.read_text(encoding="utf-8").replace("\r\n", "\n")
    if current != fresh:
        print(
            f"{lock.relative_to(ROOT)} is out of date with {source.name}; run: {SELF}",
            file=sys.stderr,
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail if a lock would change")
    mode.add_argument("--upgrade", action="store_true", help="move pins to the newest allowed")
    args = parser.parse_args(argv)

    if not args.check:
        for source, lock in LOCKS:
            cmd = compile_command(lock, upgrade=args.upgrade, source=source)
            subprocess.run(cmd, cwd=LOCK_DIR, check=True)
            print(f"wrote {lock.relative_to(ROOT)}")
        return 0

    if not all([_up_to_date(source, lock) for source, lock in LOCKS]):
        return 1
    print("python-mcp locks are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
