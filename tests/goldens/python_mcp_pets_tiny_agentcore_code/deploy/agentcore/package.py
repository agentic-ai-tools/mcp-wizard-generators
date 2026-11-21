"""Build the AgentCore direct-code zip (DEPLOY.md). Needs Python 3.11+ and uv.

    python deploy/agentcore/package.py

Writes deploy/agentcore/build/agentcore-code.zip and prints its S3 key,
code/<sha256>.zip, on the last line of stdout (everything else goes to stderr). The
same sources and lock give the same bytes, so an unchanged deploy reuses the key.

Layout (AgentCore developer guide, "direct code deployment"): dependencies, the server
package and main.py at the zip root; ARM64 Linux wheels only; 644 files and 755
directories; no __pycache__; at most 250 MB zipped and 750 MB unzipped.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PACKAGE = "pets_tiny_mcp"
LOCK = "requirements-aws.lock"
PYTHON_VERSION = "3.13"
PLATFORM = "aarch64-manylinux2014"
MAX_ZIPPED = 250 * 1024 * 1024
MAX_UNZIPPED = 750 * 1024 * 1024
# Console-script launchers and caches are not needed (or wanted) on the runtime.
SKIP_DIRS = {"__pycache__", "bin"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".exe")
# 1980-01-01, the earliest ZIP timestamp: fixed so the zip is reproducible.
EPOCH = (1980, 1, 1, 0, 0, 0)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = HERE / "build"


def fail(message: str) -> None:
    sys.stderr.write(f"package: {message}\n")
    raise SystemExit(1)


def install_dependencies(target: Path) -> None:
    uv = shutil.which("uv")
    if not uv:
        fail("uv is required (https://docs.astral.sh/uv/): it installs the ARM64 wheels")
    command = [
        uv, "pip", "install",
        "--python-platform", PLATFORM,
        "--python-version", PYTHON_VERSION,
        "--require-hashes", "--no-deps",
        "--only-binary", ":all:",
        "--target", str(target),
        "-r", str(ROOT / LOCK),
    ]  # fmt: skip
    if subprocess.run(command, check=False, stdout=sys.stderr).returncode != 0:
        fail(f"installing {LOCK} for {PLATFORM} failed (every package needs an ARM64 wheel)")


def files(base: Path, prefix: str = ""):
    """(archive name, path) pairs under base, sorted, skipping caches and launchers."""
    for path in sorted(base.iterdir(), key=lambda p: p.name):
        name = f"{prefix}{path.name}"
        if path.is_dir():
            if path.name not in SKIP_DIRS:
                yield name + "/", path
                yield from files(path, name + "/")
        elif not path.name.endswith(SKIP_SUFFIXES):
            yield name, path


def add(archive: zipfile.ZipFile, name: str, path: Path) -> int:
    info = zipfile.ZipInfo(name, date_time=EPOCH)
    info.create_system = 3  # Unix, so the permission bits below are honored
    if name.endswith("/"):
        info.external_attr = (0o40755 << 16) | 0x10
        archive.writestr(info, b"")
        return 0
    data = path.read_bytes()
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)
    return len(data)


def main() -> None:
    shutil.rmtree(BUILD, ignore_errors=True)
    deps = BUILD / "dependencies"
    deps.mkdir(parents=True)
    install_dependencies(deps)

    entries = dict(files(deps))
    entries.update(files(ROOT / "src" / PACKAGE, f"{PACKAGE}/"))
    entries[f"{PACKAGE}/"] = ROOT / "src" / PACKAGE
    entries["main.py"] = HERE / "main.py"
    manifest = ROOT / "generation-manifest.json"
    if manifest.is_file():
        entries["generation-manifest.json"] = manifest

    output = BUILD / "agentcore-code.zip"
    unzipped = 0
    with zipfile.ZipFile(output, "w") as archive:
        for name in sorted(entries):
            unzipped += add(archive, name, entries[name])
    shutil.rmtree(deps)
    size = output.stat().st_size
    if size > MAX_ZIPPED or unzipped > MAX_UNZIPPED:
        fail(f"{output.name} is {size} bytes ({unzipped} unzipped): over AgentCore's limits")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    sys.stderr.write(f"package: {output} ({size // 1024} KiB, sha256 {digest})\n")
    print(f"code/{digest}.zip")


if __name__ == "__main__":
    main()
