# mcp-wizard-generators

[![CI](https://github.com/agentic-ai-tools/mcp-wizard-generators/actions/workflows/ci.yml/badge.svg)](https://github.com/agentic-ai-tools/mcp-wizard-generators/actions/workflows/ci.yml)
[![Semgrep](https://img.shields.io/badge/Semgrep-protected-blue)](https://semgrep.dev)
[![codecov](https://codecov.io/gh/agentic-ai-tools/mcp-wizard-generators/branch/main/graph/badge.svg)](https://codecov.io/gh/agentic-ai-tools/mcp-wizard-generators)
[![PyPI](https://img.shields.io/pypi/v/mcp-wizard-generators.svg)](https://pypi.org/project/mcp-wizard-generators/)

Pluggable **OpenAPI IR → MCP project** generators for [MCP Wizard](https://github.com/agentic-ai-tools).

**PyPI / dist name:** `mcp-wizard-generators` · **Import:** `mcp_wizard_generators` · **Org:** [`agentic-ai-tools`](https://pypi.org/org/agentic-ai-tools/) (Trusted Publisher when configured)

This package is a **library + CLI**. It does **not** run HTTP servers or workers.  
`mcp-wizard-api` owns jobs, entitlements, zip/GitHub delivery, and invokes this package offline.

Every generation combines two independent choices (design ADR-026):

- **Server architecture:** `python-mcp` — a runnable MCP server on the official Python SDK.
- **Deployment target:** `local` (stdio + Cursor config, the default) or `docker` (pinned,
  hash-locked, non-root image serving streamable HTTP; compose publishes on `127.0.0.1`).
  With `--jwt-discovery-url` plus an audience, client or scope, the docker server checks
  bearer tokens itself (`auth.py`, any OIDC provider). Docker always gets a step-by-step
  `DEPLOY.md`; `--ci github-actions` adds CI (tests + image smoke test), a GHCR release
  workflow and Dependabot (actions pinned by SHA), and `--scripts` adds paired `.sh` / `.ps1`
  build, run and stop scripts. `local` can add CI too.
- **`agentcore-container`** (`--iac terraform --aws-region ...` plus JWT settings or
  `--create-cognito`): an ARM64 image on AWS Bedrock AgentCore Runtime. AgentCore's JWT
  authorizer checks tokens from any OIDC provider; upstream credentials come from Secrets
  Manager (`requirements-aws.lock` adds boto3). Terraform has a one-time `bootstrap/`
  (state bucket, ECR repository, credentials secret, GitHub OIDC deploy role) and the main
  module (runtime, least-privilege execution role, optional Cognito). `--ci github-actions`
  adds an OIDC deploy workflow behind a `production` environment; `--scripts` adds paired
  bootstrap, deploy and destroy scripts.
- **`agentcore-code`** (same options): AgentCore direct code deployment, with no container.
  The generated `deploy/agentcore/package.py` builds a reproducible ARM64 zip
  (hash-locked `aarch64-manylinux2014` wheels installed by uv, the server package and a
  root `main.py`). The zip goes to a private, versioned S3 code bucket from the bootstrap
  as `code/<sha256>.zip`, and runs on AgentCore's managed Python 3.13 runtime. Auth,
  secrets, CI and scripts work as for `agentcore-container`.

## Community

- **License:** [MIT](LICENSE) © 2026 Agentic AI Tools LLC
- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md)
- **Code of Conduct:** [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- **Security:** [SECURITY.md](SECURITY.md)

## SDK choice (generated Python MCP)

Generated servers use the **official MCP Python SDK** (`mcp` on PyPI) **2.x**:

| SDK line | High-level class | Import |
|----------|------------------|--------|
| 2.x (pinned) | **MCPServer** | `from mcp.server.mcpserver import MCPServer` |

Fresh zips pin `mcp>=2.0.0,<3` and import only `MCPServer`; mcp 1.x is not supported. Transport is **stdio** by default (Cursor local MCP); every server can also run **streamable HTTP** (`MCP_TRANSPORT=streamable-http`, stateless, `127.0.0.1:8000/mcp` by default, loopback-only unless `MCP_AUTH=platform`), and `python -m <pkg>.verify --url` checks a running server. Upstream HTTP calls use **httpx** against `API_BASE_URL`. Generated READMEs include a short OS matrix for venv activate (`bin` vs `Scripts`, including Git Bash on Windows).

## Layout

```
src/mcp_wizard_generators/
  models.py              # IntermediateRep, ToolMap, FileTree, GenerateContext
  toolmap.py             # IR ops → MCP tool names / JSON Schema inputs
  pipeline.py            # generate(): IR → tool map → rules → server + deployment → tree
  python_mcp.py          # generate_python_mcp() — stable API for mcp-wizard-api
  registry.py            # Server plugins by (architecture, language); deployments by target
  cli.py                 # mcp-wizard-gen / python -m mcp_wizard_generators
  packaging/             # zip helper for Artifact Service
  plugins/python_mcp/    # python-mcp server architecture
  plugins/deploy/        # Deployment targets layered on a server tree (local, docker, agentcore-*)
  rules/                 # Cursor + Anthropic hooks; originals copied into the artifact
tests/
  fixtures/              # tiny pets OpenAPI + IR
  goldens/               # expected file trees
```

## How `mcp-wizard-api` should invoke this

### Library (preferred in workers) — Python MCP zip

```python
from pathlib import Path
from mcp_wizard_generators import generate_python_mcp

# Prefer this stable contract for the near-term generate slice:
workdir = Path("workdir")  # or tempfile.mkdtemp() — avoid hard-coded /tmp on Windows
out = generate_python_mcp(
    spec,  # IntermediateRep | IR dict | {title, servers, ...}
    operations=["listPets", "showPetById"],  # ids — or list of simple op dicts
    out_dir=workdir / "pets-mcp",
    zip_path=workdir / "pets-mcp.zip",  # optional
    package_name="pets_tiny_mcp",  # validated: ^[a-z][a-z0-9_]{0,62}$
    # Experience API SecurityConfig JSON, or a SecurityConfig instance:
    security={"authMode": "api_key", "apiKey": {"location": "header", "name": "X-API-Key"}},
    # URL the OpenAPI document was fetched from (resolves relative servers):
    source_url="https://petstore3.swagger.io/api/v3/openapi.json",
)
# out → Path(out_dir) if out_dir set, else Path(zip_path)
```

**`source_url` (0.4.1):** the http(s) URL the OpenAPI document was fetched from. Many specs
declare a relative server such as `servers: [{url: "/api/v3"}]`, which OpenAPI defines as
relative to the document's location. With `source_url` set, relative server URLs resolve
against it (`/api/v3` → `https://petstore3.swagger.io/api/v3`), so the generated
`API_BASE_URL` in `.env.example` and `.cursor/mcp.json` is absolute. Server `{variables}`
use their `default` values; a missing `servers` array means the document's origin, and a
Swagger 2 spec without `host` uses the document's host. Without `source_url` (for example an
uploaded file), relative URLs are kept as written and you set `API_BASE_URL` yourself.
Workers should always pass the import URL.

**Untrusted specs (0.4.2):** treat every OpenAPI document as hostile input. Generated code
escapes spec values (names, paths, titles, descriptions, server URLs) and never pastes them
into source as code. Parameter names become safe Python identifiers but keep their original
wire names, and path arguments are URL-encoded at runtime. File paths that would leave the
project (`..`, absolute, drive letters) raise `ValueError`, as do tool-name overrides that
aren't slugs and unknown HTTP methods. `tests/test_generated_code_hardening.py` covers this.

**Hardened python-mcp output (0.5.0, ADR-023):**
- **Secret exposure:** `operation_findings(spec)` flags operations that send a secret where it can be logged, such as a password in the URL. A query or path parameter is medium severity, a cookie is low. The API uses the result to warn and untick those operations, and zips that include them get `SECURITY-NOTES.md`, a warning in the tool description, and a `findings` list in the manifest.
- **Typed tools:** each tool argument is `Annotated[<type>, Field(...)]`, with `Literal` enums, numeric and length limits, patterns, and a description of where the value goes. Tools return `JsonValue`.
- **Client:**
  - `API_BASE_URL` and the auth settings are checked at startup.
  - One shared httpx client; redirects are not followed.
  - Responses over `MAX_RESPONSE_BYTES` (1 MiB by default) are refused.
  - Errors say what to check, and credentials in them are replaced with `***`.
  - mcp 2.x only.
- **Dependencies and tests:** every zip includes a hash-pinned `requirements.lock`, upper bounds on its dependencies, and a `tests/` folder (tool regression, auth failures, HTTP status handling, redaction) that runs without network.

**Deployment targets (0.6.0, ADR-026):** pass `deployment=DeploymentConfig(...)` to choose
where the server runs: `local` (the default), `docker`, `agentcore-container` or
`agentcore-code`. Without `deployment`, the output is the same as in 0.5.0.
- **Transport:** every server can also serve streamable HTTP.
- **Inbound auth:** `InboundAuthConfig(mode="jwt", ...)` takes any OIDC identity provider
  (discovery URL, plus audiences, clients or scopes). AgentCore targets can create a
  Cognito pool instead.
- **Deploy assets:** Terraform (AgentCore), GitHub Actions, paired `.sh` / `.ps1` scripts
  and a step-by-step `DEPLOY.md`.
- **Validation:** CI runs actionlint, shellcheck, hadolint and `terraform validate` on the
  output for every target. It also builds and calls the docker image, and runs the
  `agentcore-code` zip in an ARM64 container.
- **Not yet tested:** a deploy to a real AWS account.
- **Removed:** `target="agentcore"` (the old placeholder plugin).

```python
from mcp_wizard_generators import DeploymentConfig, InboundAuthConfig

deployment = DeploymentConfig(
    target="docker",
    ci="github-actions",
    scripts=True,
    inbound_auth=InboundAuthConfig(
        mode="jwt",
        discovery_url="https://login.example.com/.well-known/openid-configuration",
        allowed_audiences=("api://pets",),
    ),
)
generate_python_mcp(spec, operations, out_dir, package_name="pets_mcp", deployment=deployment)
```

**`security` (ADR-017):** pass the session's saved `SecurityConfig` JSON as-is. Every mode is
honored in the generated `client.py`; invalid or unsupported settings raise `ValueError`
**before** anything is written (never a silent downgrade to no auth).

| `authMode` | Required fields | Generated behavior |
|------------|-----------------|--------------------|
| `none` | — | No credentials (dev / public APIs only) |
| `api_key` | `apiKey.location` (`header` \| `query`), `apiKey.name` | Sends env `API_KEY` as that header or query param; fails if unset |
| `oauth2` | `oauth.tokenUrl` (https); optional `scopes`, `audience` | Client credentials grant with env `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` (HTTP Basic); caches the token, refreshes before expiry, retries once on 401; fails if credentials are unset |

`mcpAuth: true` and `mtls` are rejected: generated servers are stdio-only in MVP-1.
The CLI takes the same JSON via `--security path/to/security.json`.
The legacy `auth_mode="none"` keyword still works for the `none` mode.

**`package_name`:** must match `^[a-z][a-z0-9_]{0,62}$`. Python keywords and a small denylist
(`os`, `sys`, `mcp`, …) are rejected. Invalid names raise **before** any tree/zip is written.
Reuse `validate_package_name` from this package if the API needs the same predicate.

**`operations` semantics:**

| Input | Behavior |
|-------|----------|
| `None` / omit | All IR operations (CLI convenience) |
| Non-empty ids / op dicts | Only those operations |
| Explicit `[]` | **Raises** `ValueError` (does **not** mean all) |

Hosted generate should always pass a non-empty id list.

**Simple operations JSON** (no full IR) is also accepted:

```python
generate_python_mcp(
    {"title": "Pets", "servers": ["https://petstore.example.com/v1"]},
    operations=[
        {
            "operationId": "listPets",
            "method": "get",
            "path": "/pets",
            "summary": "List all pets",
            "params": [{"name": "limit", "type": "integer"}],
        }
    ],
    out_dir="out",
)
```

In-memory result (manifest / tool map / tree) without writing:

```python
from mcp_wizard_generators import generate_python_mcp_result

result = generate_python_mcp_result(spec, operations=["listPets"])
zip_bytes = pack_file_tree_to_zip(result.tree)  # from mcp_wizard_generators.packaging
```

### Generic plugin pipeline

```python
from mcp_wizard_generators import generate, IntermediateRep
from mcp_wizard_generators.packaging import pack_file_tree_to_zip

ir = IntermediateRep.from_dict(job_snapshot["ir"])
result = generate(
    ir,
    target=job_snapshot.get("targetArchitecture", "local"),
    language=job_snapshot.get("language", "python"),
    selected_operation_ids=job_snapshot["selectedOperationIds"],
    tool_name_overrides=job_snapshot.get("toolNameOverrides"),
    package_name="generated_mcp",
)
zip_bytes = pack_file_tree_to_zip(result.tree)
```

Pin a released version of this package from the API deployable / Lambda layer.  
Do **not** vendor template trees inside `mcp-wizard-api` PRs (ADR-005).

### CLI

Prefer relative output paths (portable on macOS **and** Windows). Full shell matrix: [`docs/local-dev.md`](docs/local-dev.md).

```bash
# macOS / Linux / WSL / Git Bash
make bootstrap          # or: sh scripts/bootstrap.sh
python -m mcp_wizard_generators list-targets

python -m mcp_wizard_generators generate-python \
  --ir tests/fixtures/pets_tiny.ir.json \
  --select listPets \
  --select showPetById \
  --package-name pets_tiny_mcp \
  --out-dir .smoke-out \
  --zip .smoke-out.zip

# Equivalent via generic generate (defaults: python-mcp server, local deployment)
python -m mcp_wizard_generators generate \
  --ir tests/fixtures/pets_tiny.ir.json \
  --architecture python-mcp \
  --deployment local \
  --language python \
  --select listPets \
  --select showPetById \
  --out-dir .smoke-out \
  --zip .smoke-out.zip \
  --manifest .smoke-out/manifest.json
```

```powershell
# Windows PowerShell
.\scripts\bootstrap.ps1
python -m mcp_wizard_generators generate-python `
  --ir tests/fixtures/pets_tiny.ir.json `
  --select listPets `
  --select showPetById `
  --package-name pets_tiny_mcp `
  --out-dir .smoke-out `
  --zip .smoke-out.zip
```

Console script (after install): `mcp-wizard-gen …` (Windows: `.venv\Scripts\mcp-wizard-gen.exe`).

Deployment options (both generate commands): `--deployment`, `--iac`, `--ci`, `--scripts`,
`--aws-region`, and MCP endpoint auth from any OIDC provider with `--jwt-discovery-url` plus
`--jwt-audience` / `--jwt-client` / `--jwt-scope` (repeatable), or `--create-cognito`.
`--target local` still works; `--target agentcore` was removed in 0.6 (the old AgentCore
plugin was a placeholder).

## Generated artifact (local Python MCP)

| Path | Purpose |
|------|---------|
| `pyproject.toml` / `requirements.txt` | `mcp`, `httpx` and `pydantic`, each with an upper bound; `test` extra for pytest |
| `requirements.lock` | Hash-pinned lock for every OS and CPython 3.11+ (`pip install --require-hashes -r requirements.lock`) |
| `README.md` | SDK choice, run + Cursor setup, errors and limits, tests |
| `.env.example` | `API_BASE_URL` + auth env vars for the chosen mode (placeholders only, never secrets) |
| `.cursor/mcp.json` | Cursor MCP snippet (`command`/`args`/`env`) |
| `src/<pkg>/server.py` | MCPServer tools (1:1 with ops), typed with `Annotated[..., Field(...)]` |
| `src/<pkg>/client.py` | httpx → upstream API, applying the configured auth; config checks, size limit, redacted errors |
| `src/<pkg>/__main__.py` | `python -m <pkg>` entry (stdio, or HTTP via `MCP_TRANSPORT`) |
| `src/<pkg>/transport.py` | stdio / streamable-HTTP settings from `MCP_*` env vars; refuses a public bind without auth |
| `src/<pkg>/verify.py` | `python -m <pkg>.verify` — tool count and metadata (`<dist>-verify` after install); `--url` checks a running HTTP server |
| `tests/` | Offline pytest suite: tools, auth failures, HTTP status handling, redaction, transport settings + a loopback HTTP round trip |
| `SECURITY-NOTES.md` | Only when selected operations send a secret where it can be logged; how to remove them |
| `generation-manifest.json` | tools, `findings` and generator metadata |

**Out of scope in this slice:** Agent Rules enforcement (originals are copied, not enforced), non-Python languages, GitHub repo write delivery.

## Tests & local scripts

```bash
make bootstrap && make test    # or: sh scripts/test.sh
make lint                      # ruff (required before commit per AGENTS.md)
make dist                      # wheel + sdist → dist/
make cli-smoke                 # portable generate smoke
make e2e                       # install + stdio + tool call (needs network; not part of make test)
make tox                       # optional isolated envs
```

`make e2e` generates the pets server, installs it in a fresh virtualenv, starts it, and calls `listpets` through an MCP client. It needs network access and is not part of `make test`.

Windows PowerShell twins: `.\scripts\bootstrap.ps1`, `.\scripts\test.ps1`, `.\scripts\e2e.ps1`, `.\scripts\dist.ps1`, `.\scripts\cli-smoke.ps1`.  
Details and the **macOS / WSL / Git Bash / PowerShell** matrix: [`docs/local-dev.md`](docs/local-dev.md).

Golden coverage: `tests/fixtures/pets_tiny.ir.json` →  
`tests/goldens/python_mcp_pets_tiny/` (+ zip member assertions) for `listPets` + `showPetById`.

## Publish

Distribution name **`mcp-wizard-generators`** (import **`mcp_wizard_generators`**).  
Publishing target: **PyPI org `agentic-ai-tools`** (OIDC Trusted Publisher stub in CI).

**GitHub Packages does not support a Python/PyPI registry** (roadmap closed as not planned).  
Do **not** use `*.pypi.pkg.github.com` / `upload.pypi.pkg.github.com` — twine uploads fail with 404 or SSL hostname mismatch. There is no GitHub Packages PyPI upload workflow in this repo.

### Install

**Preferred (once the first PyPI release is live):**

```bash
pip install mcp-wizard-generators
```

**Interim** (until that release): install from a git tag or a Release asset:

```bash
pip install git+https://github.com/agentic-ai-tools/mcp-wizard-generators.git@v0.3.0
```

Or download the wheel/sdist from the [GitHub Release](https://github.com/agentic-ai-tools/mcp-wizard-generators/releases) assets for that tag.

**Local / sibling path (development):**

```bash
pip install -e ../mcp-wizard-generators
# or pin a path/Compose mount via MCP_WIZARD_GENERATORS_PATH
```

### Build wheel + sdist

```bash
make bootstrap   # .[dev] includes build + twine + pytest-cov
make dist        # → dist/mcp_wizard_generators-<version>-py3-none-any.whl (+ .tar.gz)
```

```powershell
.\scripts\bootstrap.ps1
.\scripts\dist.ps1
```

### CI quality gates

| Check | Workflow | Notes |
|-------|----------|--------|
| Lint + tests + coverage + Semgrep | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) → reusable [`.github/workflows/quality.yml`](.github/workflows/quality.yml) | Coverage floor **80%** (`tool.coverage.report.fail_under`); uploads XML/LCOV via `codecov/codecov-action`. Set **`CODECOV_TOKEN`** (or install the Codecov GitHub App). Push CI also covers `release/**`. |
| Semgrep (schedule / standalone) | [`.github/workflows/semgrep.yml`](.github/workflows/semgrep.yml) | Install the **Semgrep GitHub App** on the `agentic-ai-tools` org and/or set **`SEMGREP_APP_TOKEN`**. Uses official `semgrep/semgrep` + `semgrep ci` when the token is present; otherwise OSS rules with `--error`. |

PR CI does **not** require PyPI tokens. Publish runs the same `quality.yml` gate before any index upload.

### Tag release (GitHub Release assets)

Push a SemVer git tag matching `pyproject.toml` (for example `v0.3.0`) to run  
[`.github/workflows/publish-github-release.yml`](.github/workflows/publish-github-release.yml):

1. `python -m build`
2. `twine check`
3. Attach `dist/*` as **GitHub Release** assets (`GITHUB_TOKEN`)

`workflow_dispatch` is **build + check only** (no Release upload). Tags are for GitHub Release assets; **PyPI channel publish is branch-based** (below).

### TestPyPI / PyPI (Trusted Publisher)

[`.github/workflows/publish-pypi.yml`](.github/workflows/publish-pypi.yml) publishes with **OIDC** (`pypa/gh-action-pypi-publish`) — no `TWINE_PASSWORD` / API token secrets. Versions are computed by [`scripts/compute_pep440_version.py`](scripts/compute_pep440_version.py) (PEP 440; no `+local` segments).

| Branch | PEP 440 form | Index |
|--------|--------------|-------|
| Feature (anything except `main` / `release/*`) | `{base}.dev{N}` developmental | **TestPyPI only** |
| Default `main` | `{base}rc{N}` release candidate | **TestPyPI only** |
| `release/X.Y.Z` | final `X.Y.Z` only | **TestPyPI then PyPI** |

- `{base}` is the static `[project].version` in `pyproject.toml` (today `0.6.1`) for `.dev` / `rc` builds.
- Feature `N` combines a crc32 band of the normalized branch name with `GITHUB_RUN_NUMBER` so re-publishes get a new developmental version.
- `main` uses `N = GITHUB_RUN_NUMBER` → `{base}rc{N}`.
- Final releases: cut a branch named **`release/X.Y.Z`**, with `pyproject.toml` and `src/mcp_wizard_generators/__init__.__version__` equal to that exact public version; quality must pass, then the workflow uploads to TestPyPI and PyPI.

Triggers: push to any branch (skips `dependabot[bot]`) and `workflow_dispatch`. Quality must succeed before build/publish on every channel.

Trusted Publisher should be configured for project **`mcp-wizard-generators`** → this repo + `publish-pypi.yml`, with GitHub Environments **`testpypi`** and **`pypi`**.

OIDC upload is **opt-in**: set repository variable **`ENABLE_PYPI_PUBLISH=true`** only after Trusted Publisher claims match (workflow + environments). When unset, push still runs version → quality → build (dry-run) but skips upload so feature-branch PR checks stay green. Unset or set **`ENABLE_PYPI_PUBLISH=false`** to kill-switch publishing after it is enabled. No `pull_request` trigger on this workflow.

## Non-goals (this repo)

- Experience API / HTTP / auth / billing  
- Postgres / object storage  
- Long-running MCP or wizard servers  
- LangGraph (register later via the same plugin registry)
