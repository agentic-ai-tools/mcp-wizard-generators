"""Step-by-step ``DEPLOY.md`` for generated projects (design ADR-026).

Sections are built from small helpers that the AgentCore targets reuse. Every
command is shown for bash and PowerShell, and the document mentions only files
that were actually generated (scripts, workflows, token checks).
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp_wizard_generators.codegen import one_line
from mcp_wizard_generators.models import GenerateContext, InboundAuthConfig, ServerCapabilities
from mcp_wizard_generators.plugins.deploy.constants import CONTAINER_PORT

# (provider, discovery URL pattern, which allowed values to set)
IDENTITY_PROVIDERS = (
    (
        "Amazon Cognito",
        "https://cognito-idp.<region>.amazonaws.com/<user-pool-id>/.well-known/openid-configuration",
        "Access tokens carry `client_id` and no `aud`: allow the app **client** id (or scopes).",
    ),
    (
        "Okta",
        "https://<org>.okta.com/oauth2/<authorization-server-id>/.well-known/openid-configuration",
        "Allow the authorization server's **audience** (for example `api://default`).",
    ),
    (
        "Auth0",
        "https://<tenant>.<region>.auth0.com/.well-known/openid-configuration",
        "Allow the API identifier as the **audience** (use your custom domain if you have one).",
    ),
    (
        "Microsoft Entra ID",
        "https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration",
        "Allow the Application ID URI or client id as the **audience**; scopes arrive as `scp`. "
        "Set the app's `accessTokenAcceptedVersion` to 2 so the issuer matches.",
    ),
    (
        "Keycloak",
        "https://<host>/realms/<realm>/.well-known/openid-configuration",
        "Add an audience mapper and allow that **audience**, or allow the **client** (`azp`).",
    ),
)

LOCK_REFRESH = (
    "uv pip compile requirements.txt --universal --generate-hashes "
    "--python-version 3.11 --output-file requirements.lock"
)


@dataclass(frozen=True)
class Section:
    title: str
    body: list[str]


def shell(bash: list[str], powershell: list[str]) -> list[str]:
    """The same commands for bash (macOS, Linux, WSL, Git Bash) and PowerShell."""
    return ["```bash", *bash, "```", "", "```powershell", *powershell, "```", ""]


def render(title: str, intro: list[str], sections: list[Section]) -> str:
    lines = [f"# {title}", "", *intro, ""]
    for number, section in enumerate(sections, 1):
        lines += [f"## {number}. {section.title}", "", *section.body]
        if lines[-1] != "":
            lines.append("")
    return "\n".join(lines)


def _allowed(auth: InboundAuthConfig) -> list[str]:
    rows = [
        ("Audiences (`aud`)", auth.allowed_audiences),
        ("Clients (`client_id` / `azp`)", auth.allowed_clients),
        ("Scopes (`scope` / `scp`, any one)", auth.allowed_scopes),
    ]
    return [f"- {label}: {', '.join(f'`{v}`' for v in values)}" for label, values in rows if values]


def identity_provider_section(auth: InboundAuthConfig, where: str) -> Section:
    """Configured provider, what is checked, and how to point at another provider."""
    table = [
        "| Provider | Discovery URL | What to allow |",
        "| --- | --- | --- |",
        *(f"| {name} | `{url}` | {hint} |" for name, url, hint in IDENTITY_PROVIDERS),
    ]
    configured = (
        ["- A Cognito user pool created for this deployment"]
        if auth.create_cognito
        else [f"- Discovery URL: `{auth.discovery_url}`"]
    )
    return Section(
        "Set up your identity provider",
        [
            "Every request needs `Authorization: Bearer <access token>`. A token is accepted only",
            "when it is signed by a key your provider publishes, comes from its issuer, has not",
            "expired, and matches at least one allowed value below. Clients or scopes that do not",
            "match get **403**; anything else wrong gets **401**.",
            "",
            "Generated with:",
            "",
            *configured,
            *_allowed(auth),
            "",
            f"Any OIDC provider works. To switch, change the values in {where}:",
            "",
            *table,
            "",
        ],
    )


def connect_section(name: str, url: str, *, token: bool, note: list[str]) -> Section:
    """Cursor and Claude Code settings for a server reached by URL."""
    if token:
        cursor = [
            "{",
            '  "mcpServers": {',
            f'    "{name}": {{',
            f'      "url": "{url}",',
            '      "headers": { "Authorization": "Bearer ${env:MCP_BEARER_TOKEN}" }',
            "    }",
            "  }",
            "}",
        ]
        claude_bash = [
            f"claude mcp add --transport http {name} {url} "
            '--header "Authorization: Bearer $MCP_BEARER_TOKEN"'
        ]
        claude_ps = [
            f"claude mcp add --transport http {name} {url} "
            '--header "Authorization: Bearer $env:MCP_BEARER_TOKEN"'
        ]
    else:
        cursor = [
            "{",
            '  "mcpServers": {',
            f'    "{name}": {{ "url": "{url}" }}',
            "  }",
            "}",
        ]
        claude_bash = claude_ps = [f"claude mcp add --transport http {name} {url}"]
    return Section(
        "Connect an MCP client",
        [
            "**Cursor**: add this to `.cursor/mcp.json` (the generated file there starts the",
            "server locally over stdio instead):",
            "",
            "```json",
            *cursor,
            "```",
            "",
            "**Claude Code**:",
            "",
            *shell(claude_bash, claude_ps),
            *note,
        ],
    )


def docker_deploy_md(ctx: GenerateContext, caps: ServerCapabilities, *, jwt: bool) -> str:
    deployment = ctx.deployment
    pkg = caps.package_name
    service = pkg.replace("_", "-")
    url = f"http://127.0.0.1:{CONTAINER_PORT}/mcp"
    verify = f"python -m {pkg}.verify --url {url}"
    sections: list[Section] = []

    prereqs = [
        "- Docker Engine 24+ or Docker Desktop, with Docker Compose 2.24+",
        "  (`docker compose version`). Docker Desktop must use **Linux containers**.",
        "- Python 3.11+ for the `verify` check (`pip install -r requirements.txt`, then",
        "  `pip install --no-deps -e .`).",
    ]
    if jwt:
        prereqs.append("- An OIDC identity provider that can issue access tokens (step 3).")
    if deployment.ci == "github-actions":
        prereqs.append("- A GitHub repository for this project (for CI and image releases).")
    sections.append(Section("Prerequisites", [*prereqs, ""]))

    sections.append(
        Section(
            "Configure the server",
            [
                "Put the upstream API settings in `.env`. Compose passes them to the container;",
                "the image never contains them (`.dockerignore` excludes `.env`).",
                "",
                *shell(["cp .env.example .env"], ["Copy-Item .env.example .env"]),
                "Edit `.env`: set `API_BASE_URL` and the credentials your API needs. Leave the",
                "`MCP_*` lines commented out: `docker-compose.yml` sets them.",
                "",
            ],
        )
    )

    if jwt:
        sections.append(
            identity_provider_section(
                deployment.inbound_auth,
                "`docker-compose.yml` (`MCP_JWT_DISCOVERY_URL`, `MCP_JWT_AUDIENCES`, "
                "`MCP_JWT_CLIENTS`, `MCP_JWT_SCOPES`), then restart",
            )
        )

    if deployment.scripts:
        run_steps = shell(
            ["deploy/scripts/run.sh"],
            ["./deploy/scripts/run.ps1"],
        ) + [
            "The script builds the image, starts it with `docker compose`, and waits for the",
            "container health check. `deploy/scripts/build.sh` (`.ps1`) only builds the image.",
            "",
        ]
    else:
        run_steps = shell(
            ["docker compose up --build --detach"], ["docker compose up --build --detach"]
        )
    sections.append(
        Section(
            "Build and run",
            [
                *run_steps,
                f"The server listens on `{url}`, published on `127.0.0.1` only. It runs as a",
                "non-root user with a read-only filesystem and no Linux capabilities.",
                "",
            ],
        )
    )

    if jwt:
        verify_body = [
            "Get an access token from your provider for an allowed audience, client or scope,",
            "then check that the server answers and exposes the expected tools:",
            "",
            *shell(
                ["export MCP_BEARER_TOKEN='<access token>'", verify],
                ["$env:MCP_BEARER_TOKEN = '<access token>'", verify],
            ),
            "`verify` reads the token only from `MCP_BEARER_TOKEN`, so it stays out of your shell",
            "history, and never sends it over plain `http` to another machine.",
            "",
        ]
    else:
        verify_body = [*shell([verify], [verify])]
    sections.append(Section("Verify", verify_body))

    if jwt:
        note = [
            "Serve the container over HTTPS (a reverse proxy or load balancer in front of it)",
            "before you publish the port beyond this machine, and use that HTTPS URL here.",
            "",
        ]
    else:
        note = [
            "This server **does not check tokens**, so it is reachable from this machine only",
            "(`127.0.0.1`). To expose it, regenerate it with JWT auth.",
            "",
        ]
    sections.append(connect_section(service, url, token=jwt, note=note))

    if deployment.ci == "github-actions":
        sections.append(
            Section(
                "Publish the image (GitHub Actions)",
                [
                    "Push the project to GitHub. `.github/workflows/ci.yml` runs the tests and",
                    "builds the image, then calls it over HTTP, on every push and pull request.",
                    "",
                    "To publish, tag a release. `.github/workflows/release.yml` builds for",
                    "`linux/amd64` and `linux/arm64` and pushes to",
                    "`ghcr.io/<owner>/<repo>` (with an SBOM and provenance):",
                    "",
                    *shell(
                        ["git tag v1.0.0", "git push origin v1.0.0"],
                        ["git tag v1.0.0", "git push origin v1.0.0"],
                    ),
                    "The workflow uses the repository's `GITHUB_TOKEN`; no secrets to set up.",
                    "New packages are private: change that under the package's settings on GitHub.",
                    "Run a published image anywhere with the same environment as",
                    "`docker-compose.yml` (`MCP_AUTH` and, with token checks, `MCP_JWT_*`).",
                    "`.github/dependabot.yml` opens weekly PRs for the actions and the base image.",
                    "",
                ],
            )
        )

    update = [
        "- **Server code**: regenerate the project in the wizard, or edit it, then build and",
        "  run again (step 4).",
        "- **Python packages** are pinned with hashes in `requirements.lock`. To move to newer",
        "  versions inside the ranges in `requirements.txt`:",
        "",
        *shell([LOCK_REFRESH], [LOCK_REFRESH]),
    ]
    if deployment.ci == "github-actions":
        update += [
            "- **Roll back** by running the previous image tag from `ghcr.io`, or by re-tagging",
            "  an earlier commit.",
            "",
        ]
    sections.append(Section("Update and roll back", update))

    if deployment.scripts:
        down = shell(["deploy/scripts/stop.sh"], ["./deploy/scripts/stop.ps1"])
    else:
        down = shell(["docker compose down"], ["docker compose down"])
    sections.append(
        Section(
            "Tear down",
            [
                *down,
                f"Remove the image too with `docker image rm {service}:local`.",
                "",
            ],
        )
    )

    trouble = [
        "| Symptom | Fix |",
        "| --- | --- |",
        "| Container exits with `Cannot start: API_BASE_URL is not set` | Set it in `.env` "
        "(step 2). |",
        "| `refusing to listen on 0.0.0.0 without authentication` | `MCP_AUTH` is missing: run "
        "with `docker-compose.yml`, or pass the same environment. |",
        "| `no matching manifest for windows/amd64` | Switch Docker Desktop to Linux containers. |",
        "| `port is already allocated` | Another program uses port "
        f"{CONTAINER_PORT}. Set `MCP_HOST_PORT=8001` (any free port) in `.env`, start again, "
        "and use that port in the URLs above. |",
        "| `verify` says it cannot connect | Check `docker compose ps` and "
        "`docker compose logs`, and that the URL uses the published port. |",
    ]
    if jwt:
        trouble += [
            "| 401 `the token was issued for a different audience` | Request the token for an "
            "allowed audience, or add it to `MCP_JWT_AUDIENCES`. |",
            "| 401 `the token comes from a different issuer` | The discovery URL must be the "
            "token issuer's; for Entra ID use v2 tokens. |",
            "| 403 | The token is valid but its client or scopes are not allowed "
            "(`MCP_JWT_CLIENTS`, `MCP_JWT_SCOPES`). |",
            "| 503 `the token check is unavailable` | The server cannot reach the provider's "
            "discovery or JWKS URL; check the container's outbound network. |",
        ]
    sections.append(Section("Troubleshooting", [*trouble, ""]))

    title = one_line(ctx.ir.title)
    intro = [
        f"How to run **{title}** as a container with Docker. Each step shows the",
        "commands for bash (macOS, Linux, WSL, Git Bash) and PowerShell.",
    ]
    return render(f"Deploy {title} with Docker", intro, sections)
