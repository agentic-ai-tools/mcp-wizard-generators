"""Files that turn the server into an AgentCore direct-code zip (design ADR-026).

``deploy/agentcore/main.py`` becomes the zip's ``main.py`` (the runtime's entry point)
and ``deploy/agentcore/package.py`` builds the zip: hash-locked ARM64 wheels at the zip
root (installed by uv, which evaluates the lock's markers for the target platform, so
Windows-only packages stay out), then the server package and the entry point.
"""

from __future__ import annotations

from mcp_wizard_generators.models import FileEntry, ServerCapabilities
from mcp_wizard_generators.plugins.deploy.constants import (
    AGENTCORE_CODE_PLATFORM,
    AGENTCORE_CODE_PYTHON,
    CONTAINER_PORT,
)

PACKAGE_DIR = "deploy/agentcore"
PACKAGE_SCRIPT = f"{PACKAGE_DIR}/package.py"
ZIP_PATH = f"{PACKAGE_DIR}/build/agentcore-code.zip"

_ENTRY = '''\
"""AgentCore Runtime entry point (direct code deployment; DEPLOY.md).

deploy/agentcore/package.py puts this file at the root of the zip as main.py.
AgentCore's MCP contract: streamable HTTP on 0.0.0.0:__PORT__/mcp. MCP_AUTH is set
where the runtime is defined (deploy/terraform/main.tf); without it the server
refuses to listen on 0.0.0.0, so the package never runs unprotected by accident.
"""

import os

from __PKG__.server import main

if __name__ == "__main__":
    os.environ.setdefault("MCP_TRANSPORT", "streamable-http")
    os.environ.setdefault("MCP_HOST", "0.0.0.0")
    os.environ.setdefault("MCP_PORT", "__PORT__")
    os.environ.setdefault("MCP_PATH", "/mcp")
    main()
'''

_GITIGNORE = "build/\n"

_PACKAGE = '''\
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

PACKAGE = "__PKG__"
LOCK = "__LOCK__"
PYTHON_VERSION = "__PYTHON__"
PLATFORM = "__PLATFORM__"
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
    sys.stderr.write(f"package: {message}\\n")
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
    sys.stderr.write(f"package: {output} ({size // 1024} KiB, sha256 {digest})\\n")
    print(f"code/{digest}.zip")


if __name__ == "__main__":
    main()
'''


def package_files(caps: ServerCapabilities) -> list[FileEntry]:
    """The entry point, the package script and a .gitignore for the build output."""
    entry = _ENTRY.replace("__PORT__", str(CONTAINER_PORT)).replace("__PKG__", caps.package_name)
    script = (
        _PACKAGE.replace("__PKG__", caps.package_name)
        .replace("__LOCK__", caps.lock_files[0])
        .replace("__PYTHON__", AGENTCORE_CODE_PYTHON)
        .replace("__PLATFORM__", AGENTCORE_CODE_PLATFORM)
    )
    return [
        FileEntry(f"{PACKAGE_DIR}/main.py", entry),
        FileEntry(PACKAGE_SCRIPT, script),
        FileEntry(f"{PACKAGE_DIR}/.gitignore", _GITIGNORE),
    ]
