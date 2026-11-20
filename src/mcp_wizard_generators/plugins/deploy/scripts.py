"""Deploy scripts for generated projects, always in pairs: POSIX sh + PowerShell (ADR-026).

Each script works from any directory (it changes to the project root first) and
stops at the first failing command.
"""

from __future__ import annotations

from mcp_wizard_generators.models import FileEntry, ServerCapabilities
from mcp_wizard_generators.plugins.deploy.constants import CONTAINER_PORT

SCRIPTS_DIR = "deploy/scripts"
# docker compose waits this long for the container health check (2-second polls).
_HEALTH_ATTEMPTS = 45


def _sh(name: str, purpose: str, usage: str, body: list[str]) -> FileEntry:
    lines = [
        "#!/usr/bin/env sh",
        f"# {purpose}",
        f"# Usage: {SCRIPTS_DIR}/{name}.sh{usage}",
        "set -eu",
        'cd "$(dirname "$0")/../.."',
        *body,
    ]
    return FileEntry(f"{SCRIPTS_DIR}/{name}.sh", "\n".join(lines) + "\n", executable=True)


def _ps1(name: str, purpose: str, usage: str, body: list[str]) -> FileEntry:
    # param() must be the first statement in a PowerShell script.
    params = [line for line in body if line.startswith("param(")]
    body = [line for line in body if not line.startswith("param(")]
    lines = [
        f"# {purpose}",
        f"# Usage: .\\deploy\\scripts\\{name}.ps1{usage}",
        *params,
        "$ErrorActionPreference = 'Stop'",
        "Set-Location (Join-Path $PSScriptRoot '..\\..')",
        *body,
    ]
    return FileEntry(f"{SCRIPTS_DIR}/{name}.ps1", "\n".join(lines) + "\n")


def _ps_check() -> str:
    """Native commands do not throw in Windows PowerShell 5.1; check the exit code."""
    return "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"


def gitattributes() -> FileEntry:
    """Keep shell scripts LF through Git checkouts on Windows (core.autocrlf)."""
    return FileEntry(
        ".gitattributes",
        "# Shell scripts must keep LF line endings to run in Linux, containers and CI.\n"
        "*.sh text eol=lf\n",
    )


def docker_scripts(caps: ServerCapabilities) -> list[FileEntry]:
    """build / run / stop for the docker deployment."""
    service = caps.package_name.replace("_", "-")
    module = caps.entrypoint.split(":", 1)[0].rsplit(".", 1)[0]
    no_env = "No .env file: copy .env.example to .env and set API_BASE_URL (and credentials)."
    build = (
        "Build the container image.",
        " [image-tag]",
    )
    run = (
        "Start the server with docker compose and wait until its health check passes.",
        "",
    )
    stop = ("Stop and remove the server container.", "")
    return [
        _sh(
            "build",
            *build,
            [
                f'tag="${{1:-{service}:local}}"',
                'docker build --tag "$tag" .',
                'echo "Built $tag"',
            ],
        ),
        _ps1(
            "build",
            *build,
            [
                f"param([string]$Tag = '{service}:local')",
                "docker build --tag $Tag .",
                _ps_check(),
                'Write-Host "Built $Tag"',
            ],
        ),
        _sh(
            "run",
            *run,
            [
                "if [ ! -f .env ]; then",
                f'  echo "{no_env}" >&2',
                "  exit 1",
                "fi",
                "docker compose up --build --detach",
                f'container="$(docker compose ps --quiet {service})"',
                "attempt=0",
                "while :; do",
                "  state=\"$(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' "
                '"$container")"',
                '  case "$state" in',
                '    "running healthy") break ;;',
                "    exited*|dead*|restarting*|*unhealthy)",
                '      echo "The server is not healthy ($state). Logs:" >&2',
                f"      docker compose logs --tail 50 {service} >&2",
                "      exit 1 ;;",
                "  esac",
                "  attempt=$((attempt + 1))",
                f'  if [ "$attempt" -ge {_HEALTH_ATTEMPTS} ]; then',
                '    echo "Timed out waiting for the health check. Logs: docker compose logs" >&2',
                "    exit 1",
                "  fi",
                "  sleep 2",
                "done",
                f"# The published host port (MCP_HOST_PORT in .env, default {CONTAINER_PORT}).",
                f'url="http://$(docker compose port {service} {CONTAINER_PORT})/mcp"',
                'echo "Running on $url"',
                f'echo "Check it: python -m {module}.verify --url $url"',
            ],
        ),
        _ps1(
            "run",
            *run,
            [
                "if (-not (Test-Path .env)) {",
                f"    [Console]::Error.WriteLine('{no_env}')",
                "    exit 1",
                "}",
                "docker compose up --build --detach",
                _ps_check(),
                f"$container = docker compose ps --quiet {service}",
                f"for ($attempt = 1; $attempt -le {_HEALTH_ATTEMPTS}; $attempt++) {{",
                "    $state = docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' "
                "$container",
                "    if ($state -eq 'running healthy') {",
                "        # The published host port (MCP_HOST_PORT in .env, default "
                f"{CONTAINER_PORT}).",
                f'        $url = "http://$(docker compose port {service} {CONTAINER_PORT})/mcp"',
                '        Write-Host "Running on $url"',
                f'        Write-Host "Check it: python -m {module}.verify --url $url"',
                "        exit 0",
                "    }",
                "    if ($state -match '^(exited|dead|restarting)' -or $state -match 'unhealthy$') {",
                '        [Console]::Error.WriteLine("The server is not healthy ($state). Logs:")',
                f"        docker compose logs --tail 50 {service}",
                "        exit 1",
                "    }",
                "    Start-Sleep -Seconds 2",
                "}",
                "[Console]::Error.WriteLine('Timed out waiting for the health check. "
                "Logs: docker compose logs')",
                "exit 1",
            ],
        ),
        _sh("stop", *stop, ["docker compose down"]),
        _ps1("stop", *stop, ["docker compose down", _ps_check()]),
    ]
