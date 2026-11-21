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


def agentcore_deploy_md(
    ctx: GenerateContext, caps: ServerCapabilities, *, name: str, code: bool = False
) -> str:
    """Step-by-step deployment to AWS Bedrock AgentCore Runtime. ``code``: direct code
    deployment (a zip in S3) instead of a container image in ECR."""
    deployment = ctx.deployment
    auth = deployment.inbound_auth
    pkg = caps.package_name
    credentials = [v.name for v in caps.secret_env_vars]
    ci = deployment.ci == "github-actions"
    tf = "deploy/terraform"
    boot = f"{tf}/bootstrap"
    title = one_line(ctx.ir.title)
    sections: list[Section] = []
    unused = '"code_key=code/unused.zip"' if code else '"image_tag=unused"'

    prereqs = [
        "- An AWS account, in a Region where Amazon Bedrock AgentCore is available, and",
        "  administrator credentials for the one-time bootstrap (`aws sts get-caller-identity`).",
        *(
            [
                "- AWS CLI v2, Terraform 1.10 or newer, and [uv](https://docs.astral.sh/uv/).",
                "  `deploy/agentcore/package.py` uses uv to fetch `linux/arm64` wheels (which",
                "  AgentCore requires) from the hash-checked lock on any OS.",
                "- Python 3.11+ for the package script and the `verify` check",
                "  (`pip install -r requirements.txt`, then `pip install --no-deps -e .`).",
            ]
            if code
            else [
                "- AWS CLI v2, Terraform 1.10 or newer, and Docker with `buildx` (Docker Desktop",
                "  includes it). Images are built for `linux/arm64`, which AgentCore requires.",
                "- Python 3.11+ for the `verify` check (`pip install -r requirements.txt`, then",
                "  `pip install --no-deps -e .`).",
            ]
        ),
    ]
    if not auth.create_cognito:
        prereqs.append("- An OIDC identity provider that issues access tokens (step 3).")
    if ci:
        prereqs.append("- A GitHub repository for this project, with Actions enabled.")
    sections.append(Section("Prerequisites", [*prereqs, ""]))

    sections.append(
        Section(
            "Review the settings",
            [
                f"`{tf}/terraform.tfvars` holds this deployment's settings: Region, base name,",
                "upstream API URL and identity provider. None of them are secrets; commit the",
                f"file. `{boot}/terraform.tfvars` holds the bootstrap settings"
                + (": set `github_repository` to `owner/name`." if ci else "."),
                "",
            ],
        )
    )

    if auth.create_cognito:
        sections.append(
            Section(
                "Set up your identity provider",
                [
                    "Terraform creates an Amazon Cognito user pool with a machine-to-machine",
                    "app client (OAuth client credentials) and allows only that client.",
                    "After the first deploy, get a token from it (step 9). To use your own",
                    f"provider instead, set `create_cognito = false` in `{tf}/terraform.tfvars`",
                    "and fill in the `jwt_*` values; these work:",
                    "",
                    "| Provider | Discovery URL | What to allow |",
                    "| --- | --- | --- |",
                    *(f"| {n} | `{u}` | {h} |" for n, u, h in IDENTITY_PROVIDERS),
                    "",
                ],
            )
        )
    else:
        sections.append(
            identity_provider_section(auth, f"`{tf}/terraform.tfvars` (`jwt_*`), then deploy again")
        )

    sections.append(
        Section(
            "Bootstrap AWS (once)",
            [
                "With administrator credentials, create the Terraform state bucket, the "
                + ("code" if code else "ECR"),
                ("bucket" if code else "repository")
                + (", the empty credentials secret" if credentials else "")
                + (" and the GitHub deploy role" if ci else "")
                + ":",
                "",
                *(
                    shell(["deploy/scripts/bootstrap.sh"], ["./deploy/scripts/bootstrap.ps1"])
                    if deployment.scripts
                    else shell(
                        [f"terraform -chdir={boot} init", f"terraform -chdir={boot} apply"],
                        [f"terraform -chdir={boot} init", f"terraform -chdir={boot} apply"],
                    )
                ),
                f"Keep `{boot}/terraform.tfstate` safe (it holds no secrets): Terraform needs",
                "it to change or remove the bootstrap later.",
                "",
            ],
        )
    )

    if credentials:
        example = ", ".join(f'"{n}": "..."' for n in credentials)
        put = (
            f'aws secretsmanager put-secret-value --secret-id "$(terraform -chdir={boot} '
            'output -raw api_secret_arn)" --secret-string file://api-credentials.json'
        )
        sections.append(
            Section(
                "Store the API credentials",
                [
                    "The server reads its upstream credentials from Secrets Manager at startup",
                    "with its own IAM role; they never go into Terraform or environment",
                    "variables. Write them to a local file `api-credentials.json` as",
                    f"`{{{example}}}`, store them, then delete the file:",
                    "",
                    *shell(
                        [put, "rm api-credentials.json"],
                        [
                            put.replace('"$(', "(").replace(')"', ")"),
                            "Remove-Item api-credentials.json",
                        ],
                    ),
                ],
            )
        )

    if ci:
        sections.append(
            Section(
                "Connect GitHub Actions",
                [
                    "In the repository settings:",
                    "",
                    "1. **Environments** → create `production` and add required reviewers, so",
                    "   every deploy waits for approval.",
                    "2. **Secrets and variables → Actions → Variables**, add (from the",
                    f"   `terraform -chdir={boot} output` values):",
                    "   - `AWS_DEPLOY_ROLE_ARN` = `deploy_role_arn`",
                    "   - `AWS_REGION` = `aws_region`",
                    "   - `TF_STATE_BUCKET` = `state_bucket`",
                    *(["   - `CODE_BUCKET` = `code_bucket`"] if code else []),
                    "",
                    "No AWS keys are stored: the workflow gets short-lived credentials through",
                    "GitHub's OIDC token, and only jobs in the `production` environment of this",
                    "repository can assume the role.",
                    "",
                ],
            )
        )

    deploy_body: list[str] = []
    if ci:
        deploy_body += [
            "Push to `main` (or run **Deploy to AgentCore** from the Actions tab). After",
            *(
                [
                    "approval, `.github/workflows/deploy.yml` builds the ARM64 zip, uploads it",
                    "to the code bucket as `code/<sha256>.zip`, and runs `terraform plan` and",
                    "`apply`. The job summary shows the MCP URL. `.github/workflows/ci.yml` runs",
                    "the tests and builds the package on every push and pull request.",
                ]
                if code
                else [
                    "approval, `.github/workflows/deploy.yml` builds the ARM64 image, pushes it to",
                    "ECR tagged with the commit, and runs `terraform plan` and `apply`. The job",
                    "summary shows the MCP URL. `.github/workflows/ci.yml` runs the tests on every",
                    "push and pull request.",
                ]
            ),
            "",
        ]
    if deployment.scripts:
        deploy_body += [
            ("Or deploy" if ci else "Deploy") + " from your machine with AWS credentials:",
            "",
            *shell(["deploy/scripts/deploy.sh"], ["./deploy/scripts/deploy.ps1"]),
        ]
    if not deployment.scripts and not ci and code:
        upload = 'aws s3 cp deploy/agentcore/build/agentcore-code.zip "s3://<code_bucket>/<key>"'
        deploy_body += [
            "Build the zip (the script prints its S3 key, `code/<sha256>.zip`), upload it",
            "to the code bucket from the bootstrap outputs, then apply Terraform:",
            "",
            *shell(
                [
                    "python deploy/agentcore/package.py",
                    upload,
                    f"terraform -chdir={tf} init -backend-config=backend.hcl",
                    f'terraform -chdir={tf} apply -var "code_key=<key>"',
                ],
                [
                    "python deploy/agentcore/package.py",
                    upload,
                    f"terraform -chdir={tf} init -backend-config=backend.hcl",
                    f'terraform -chdir={tf} apply -var "code_key=<key>"',
                ],
            ),
        ]
    elif not deployment.scripts and not ci:
        deploy_body += [
            "Build and push the image, then apply Terraform (bucket and repository from the",
            "bootstrap outputs):",
            "",
            *shell(
                [
                    "docker buildx build --platform linux/arm64 --provenance=false --push "
                    "--tag <ecr_repository_url>:<tag> .",
                    f"terraform -chdir={tf} init -backend-config=backend.hcl",
                    f'terraform -chdir={tf} apply -var "image_tag=<tag>"',
                ],
                [
                    "docker buildx build --platform linux/arm64 --provenance=false --push "
                    "--tag <ecr_repository_url>:<tag> .",
                    f"terraform -chdir={tf} init -backend-config=backend.hcl",
                    f'terraform -chdir={tf} apply -var "image_tag=<tag>"',
                ],
            ),
        ]
    if code and (deployment.scripts or ci):
        deploy_body += [
            "To check the package without deploying, build it locally: it prints the S3",
            "key and writes `deploy/agentcore/build/agentcore-code.zip`.",
            "",
            *shell(["python deploy/agentcore/package.py"], ["python deploy/agentcore/package.py"]),
        ]
    deploy_body += [
        f"The runtime runs your {'code' if code else 'image'} with `MCP_AUTH=platform`: "
        "AgentCore rejects requests",
        f"without a valid token before they reach the {'server' if code else 'container'}.",
        "",
    ]
    sections.append(Section("Deploy", deploy_body))

    url_sh = f'"$(terraform -chdir={tf} output -raw mcp_url)"'
    url_ps = f"(terraform -chdir={tf} output -raw mcp_url)"
    verify_sh = f"python -m {pkg}.verify --url {url_sh}"
    verify_ps = f"python -m {pkg}.verify --url {url_ps}"
    if auth.create_cognito:
        token_sh = [
            f'TOKEN_URL="$(terraform -chdir={tf} output -raw cognito_token_url)"',
            f'CLIENT_ID="$(terraform -chdir={tf} output -raw cognito_client_id)"',
            f'CLIENT_SECRET="$(terraform -chdir={tf} output -raw cognito_client_secret)"',
            'export MCP_BEARER_TOKEN="$(curl -s -u "$CLIENT_ID:$CLIENT_SECRET" -d '
            "'grant_type=client_credentials&scope=mcp/invoke' \"$TOKEN_URL\" | "
            'python -c \'import json,sys; print(json.load(sys.stdin)["access_token"])\')"',
        ]
        token_ps = [
            f"$tokenUrl = terraform -chdir={tf} output -raw cognito_token_url",
            f"$id = terraform -chdir={tf} output -raw cognito_client_id",
            f"$secret = terraform -chdir={tf} output -raw cognito_client_secret",
            '$basic = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${id}:${secret}"))',
            "$env:MCP_BEARER_TOKEN = (Invoke-RestMethod -Method Post -Uri $tokenUrl "
            '-Headers @{Authorization = "Basic $basic"} '
            "-Body 'grant_type=client_credentials&scope=mcp/invoke').access_token",
        ]
        token_intro = "Get a token from the Cognito user pool (outputs `cognito_token_url`,"
        token_intro2 = "`cognito_client_id`, `cognito_client_secret`), then check the server:"
    else:
        token_sh = ["export MCP_BEARER_TOKEN='<access token>'"]
        token_ps = ["$env:MCP_BEARER_TOKEN = '<access token>'"]
        token_intro = "Get an access token from your identity provider for an allowed audience,"
        token_intro2 = "client or scope, then check that the server answers with its tools:"
    sections.append(
        Section(
            "Verify",
            [
                token_intro,
                token_intro2,
                "",
                *shell([*token_sh, verify_sh], [*token_ps, verify_ps]),
                f"`terraform -chdir={tf} output -raw mcp_url` prints the URL MCP clients use.",
                "",
            ],
        )
    )

    sections.append(
        connect_section(
            name,
            "<mcp_url output>",
            token=True,
            note=[
                "Replace `<mcp_url output>` with the full `mcp_url` value. Tokens expire;",
                "get a new one when the client starts getting 401.",
                "",
            ],
        )
    )

    sections.append(
        Section(
            "Update and roll back",
            [
                "- **Code or settings**: commit and push"
                + (" (the workflow deploys)" if ci else "")
                + (", or run the deploy script again" if deployment.scripts else "")
                + (
                    ". Every deploy uploads a zip named after its content, `code/<sha256>.zip`."
                    if code
                    else ". Every deploy pushes a new, immutable image tag."
                ),
                (
                    "- **Roll back** by applying an earlier package (keys are listed with "
                    "`aws s3 ls s3://<code_bucket>/code/`): "
                    f'`terraform -chdir={tf} apply -var "code_key=<earlier key>"`.'
                    if code
                    else f"- **Roll back** by applying an earlier tag: "
                    f'`terraform -chdir={tf} apply -var "image_tag=<earlier tag>"`.'
                ),
                "- **Python packages** are pinned with hashes in `requirements-aws.lock` (the",
                f"  {'package' if code else 'image'}) and `requirements.lock` (local use). "
                "To refresh them:",
                "",
                *shell([LOCK_REFRESH, LOCK_REFRESH_AWS], [LOCK_REFRESH, LOCK_REFRESH_AWS]),
            ],
        )
    )

    sections.append(
        Section(
            "Tear down",
            [
                *(
                    shell(["deploy/scripts/destroy.sh"], ["./deploy/scripts/destroy.ps1"])
                    if deployment.scripts
                    else shell(
                        [f"terraform -chdir={tf} destroy -var {unused}"],
                        [f"terraform -chdir={tf} destroy -var {unused}"],
                    )
                ),
                "This removes the runtime and its role. The bootstrap stays: to remove it,",
                "empty the state bucket and the "
                + ("code bucket" if code else "ECR repository")
                + ", then run",
                f"`terraform -chdir={boot} destroy`.",
                "",
            ],
        )
    )

    trouble = [
        "| Symptom | Fix |",
        "| --- | --- |",
        "| `verify` gets 401 | The token is missing, expired, or not from the configured "
        "provider; check the discovery URL and get a new token. |",
        "| `verify` gets 403 | The token's audience, client or scopes are not in the allowed "
        f"lists in `{tf}/terraform.tfvars`. |",
        "| The runtime does not start | Read its logs in CloudWatch under "
        "`/aws/bedrock-agentcore/runtimes/`. `Cannot start:` lines name the setting to fix. |",
        *(
            _CODE_TROUBLE
            if code
            else [
                "| `exec format error` in the logs | The image is not ARM64: build with "
                "`--platform linux/arm64`. |",
            ]
        ),
        "| `terraform apply` fails right after the role is created | IAM changes take a few "
        "seconds to reach AgentCore; run apply again. |",
        *(
            []
            if code
            else [
                "| `ImageTagAlreadyExistsException` | ECR tags are immutable: deploy with a "
                "new tag. |"
            ]
        ),
    ]
    if credentials:
        trouble.insert(
            4,
            "| Logs say the secret `does not exist or has no value yet` | Store the credentials "
            "(step 5). |",
        )
    sections.append(Section("Troubleshooting", [*trouble, ""]))

    intro = [
        f"How to run **{title}** on AWS Bedrock AgentCore Runtime. Each step shows the",
        "commands for bash (macOS, Linux, WSL, Git Bash) and PowerShell.",
    ]
    return render(f"Deploy {title} to AWS Bedrock AgentCore", intro, sections)


_CODE_TROUBLE = [
    "| `Your artifact contains binary files that are incompatible with Linux ARM64` | "
    "Build the zip with `deploy/agentcore/package.py` (uv, `aarch64-manylinux2014` wheels), "
    "not with a plain `pip install`. |",
    "| `The specified entrypoint could not be found` | `main.py` must be at the zip root; "
    "rebuild with the package script. |",
    "| `package: ... over AgentCore's limits` | Zips are limited to 250 MB (750 MB "
    "unzipped): drop unused dependencies from `requirements.txt` and refresh the locks. |",
    "| `AccessDenied` on `s3:GetObject` during apply | The deploying role must be able to "
    "read the package; the bootstrap deploy role can. |",
]

# requirements.txt plus boto3, read from stdin ("-"): works the same in bash and PowerShell.
LOCK_REFRESH_AWS = (
    'echo "boto3>=1.35,<2" | uv pip compile requirements.txt - --universal --generate-hashes '
    "--python-version 3.11 --output-file requirements-aws.lock"
)
