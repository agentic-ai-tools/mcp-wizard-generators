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


_BOOT = "deploy/terraform/bootstrap"
_MAIN = "deploy/terraform"


def _backend_args(name: str) -> str:
    """-backend-config flags for the main module's S3 state. The same text works in sh
    and PowerShell: both expand $bucket and $region inside double quotes."""
    parts = [
        "bucket=$bucket",
        f"key={name}/terraform.tfstate",
        "region=$region",
        "use_lockfile=true",
        "encrypt=true",
    ]
    return " ".join(f'-backend-config="{part}"' for part in parts)


def agentcore_scripts(caps: ServerCapabilities, *, name: str) -> list[FileEntry]:
    """bootstrap / deploy / destroy for the AgentCore container target."""
    boot = (
        "One-time setup with administrator credentials: state bucket, ECR repository, deploy role.",
        "",
    )
    deploy = (
        "Build the ARM64 image, push it to ECR, then apply Terraform.",
        " [image-tag]",
    )
    destroy = (
        "Delete the AgentCore runtime, its role and secret (the bootstrap is kept).",
        "",
    )
    placeholder = "Set github_repository in deploy/terraform/bootstrap/terraform.tfvars first."
    keep = (
        "The bootstrap (state bucket, ECR repository, deploy role) is kept. To remove it, "
        f"empty the bucket and repository, then: terraform -chdir={_BOOT} destroy"
    )
    sh_outputs = [
        f'region="$(terraform -chdir={_BOOT} output -raw aws_region)"',
        f'bucket="$(terraform -chdir={_BOOT} output -raw state_bucket)"',
    ]
    ps_outputs = [
        f"$region = terraform -chdir={_BOOT} output -raw aws_region",
        _ps_check(),
        f"$bucket = terraform -chdir={_BOOT} output -raw state_bucket",
        _ps_check(),
    ]
    sh_init = f"terraform -chdir={_MAIN} init -input=false -reconfigure {_backend_args(name)}"
    ps_init = f"terraform -chdir={_MAIN} init -input=false -reconfigure {_backend_args(name)}"
    return [
        _sh(
            "bootstrap",
            *boot,
            [
                f"if grep -q 'OWNER/REPOSITORY' {_BOOT}/terraform.tfvars; then",
                f'  echo "{placeholder}" >&2',
                "  exit 1",
                "fi",
                f"terraform -chdir={_BOOT} init -input=false",
                f"terraform -chdir={_BOOT} apply -input=false",
                f"terraform -chdir={_BOOT} output",
                'echo "Next: DEPLOY.md, steps after Bootstrap."',
            ],
        ),
        _ps1(
            "bootstrap",
            *boot,
            [
                f"if (Select-String -Path {_BOOT}/terraform.tfvars -Pattern 'OWNER/REPOSITORY' "
                "-Quiet) {",
                f"    [Console]::Error.WriteLine('{placeholder}')",
                "    exit 1",
                "}",
                f"terraform -chdir={_BOOT} init -input=false",
                _ps_check(),
                f"terraform -chdir={_BOOT} apply -input=false",
                _ps_check(),
                f"terraform -chdir={_BOOT} output",
                "Write-Host 'Next: DEPLOY.md, steps after Bootstrap.'",
            ],
        ),
        _sh(
            "deploy",
            *deploy,
            [
                *sh_outputs,
                f'repo="$(terraform -chdir={_BOOT} output -raw ecr_repository_url)"',
                "# ECR tags are immutable, so every deploy gets a new tag.",
                'tag="${1:-$(date -u +%Y%m%d%H%M%S)}"',
                'aws ecr get-login-password --region "$region" | '
                'docker login --username AWS --password-stdin "${repo%%/*}"',
                "docker buildx build --platform linux/arm64 --provenance=false --sbom=false "
                '--push --tag "$repo:$tag" .',
                sh_init,
                f'terraform -chdir={_MAIN} apply -input=false -var "image_tag=$tag"',
                f'echo "MCP URL: $(terraform -chdir={_MAIN} output -raw mcp_url)"',
            ],
        ),
        _ps1(
            "deploy",
            *deploy,
            [
                "param([string]$Tag = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))",
                *ps_outputs,
                f"$repo = terraform -chdir={_BOOT} output -raw ecr_repository_url",
                _ps_check(),
                "# ECR tags are immutable, so every deploy gets a new tag.",
                "aws ecr get-login-password --region $region | "
                "docker login --username AWS --password-stdin $repo.Split('/')[0]",
                _ps_check(),
                "docker buildx build --platform linux/arm64 --provenance=false --sbom=false "
                '--push --tag "${repo}:$Tag" .',
                _ps_check(),
                ps_init,
                _ps_check(),
                f'terraform -chdir={_MAIN} apply -input=false -var "image_tag=$Tag"',
                _ps_check(),
                f'Write-Host "MCP URL: $(terraform -chdir={_MAIN} output -raw mcp_url)"',
            ],
        ),
        _sh(
            "destroy",
            *destroy,
            [
                *sh_outputs,
                sh_init,
                f'terraform -chdir={_MAIN} destroy -input=false -var "image_tag=unused"',
                f'echo "{keep}"',
            ],
        ),
        _ps1(
            "destroy",
            *destroy,
            [
                *ps_outputs,
                ps_init,
                _ps_check(),
                f'terraform -chdir={_MAIN} destroy -input=false -var "image_tag=unused"',
                _ps_check(),
                f"Write-Host '{keep}'",
            ],
        ),
    ]
