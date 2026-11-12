# mcp-wizard-generators

[![CI](https://github.com/agentic-ai-tools/mcp-wizard-generators/actions/workflows/ci.yml/badge.svg)](https://github.com/agentic-ai-tools/mcp-wizard-generators/actions/workflows/ci.yml)
[![Semgrep](https://img.shields.io/badge/Semgrep-protected-blue)](https://semgrep.dev)
[![codecov](https://codecov.io/gh/agentic-ai-tools/mcp-wizard-generators/branch/main/graph/badge.svg)](https://codecov.io/gh/agentic-ai-tools/mcp-wizard-generators)
[![PyPI](https://img.shields.io/pypi/v/mcp-wizard-generators.svg)](https://pypi.org/project/mcp-wizard-generators/)

Pluggable **OpenAPI IR → MCP project** generators for [MCP Wizard](https://github.com/agentic-ai-tools).

**PyPI / dist name:** `mcp-wizard-generators` · **Import:** `mcp_wizard_generators` · **Org:** [`agentic-ai-tools`](https://pypi.org/org/agentic-ai-tools/) (Trusted Publisher when configured)

This package is a **library + CLI**. It does **not** run HTTP servers or workers.  
`mcp-wizard-api` owns jobs, entitlements, zip/GitHub delivery, and invokes this package offline.

**Near-term slice:** `local` + `python` — runnable Anthropic-style MCP server zip + Cursor config.  
**Also registered:** `agentcore` + `python` scaffold (AgentCore deploy stubs; expand later).

## Community

- **License:** [MIT](LICENSE)
- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md)
- **Code of Conduct:** [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- **Security:** [SECURITY.md](SECURITY.md)

## SDK choice (generated Python MCP)

Generated servers use the **official MCP Python SDK** (`mcp` on PyPI) **2.x**:

| SDK line | High-level class | Import |
|----------|------------------|--------|
| 2.x (pinned) | **MCPServer** | `from mcp.server.mcpserver import MCPServer` |
| 1.x (legacy) | **FastMCP** | `from mcp.server.fastmcp import FastMCP` |

Fresh zips pin `mcp>=2.0.0,<3` and import `MCPServer` (with an optional FastMCP fallback). Transport is **stdio** for Cursor local MCP. Upstream HTTP calls use **httpx** against `API_BASE_URL`. Generated READMEs include a short OS matrix for venv activate (`bin` vs `Scripts`, including Git Bash on Windows).

## Layout

```
src/mcp_wizard_generators/
  models.py              # IntermediateRep, ToolMap, FileTree, GenerateContext
  toolmap.py             # IR ops → MCP tool names / JSON Schema inputs
  pipeline.py            # generate(): IR → tool map → rules hooks → plugin → tree
  python_mcp.py          # generate_python_mcp() — stable API for mcp-wizard-api
  registry.py            # Plugin registry keyed by (target, language)
  cli.py                 # mcp-wizard-gen / python -m mcp_wizard_generators
  packaging/             # zip helper for Artifact Service
  plugins/python_mcp/   # Local Python MCP (stdio) emitter
  plugins/agentcore/     # AWS Bedrock AgentCore scaffold (deploy stubs)
  rules/                 # Cursor + Anthropic hooks (not enforced in local slice)
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
    auth_mode="none",  # none | api_key | oauth2
)
# out → Path(out_dir) if out_dir set, else Path(zip_path)
```

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

# Equivalent via generic generate (default target=local)
python -m mcp_wizard_generators generate \
  --ir tests/fixtures/pets_tiny.ir.json \
  --target local \
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

## Generated artifact (local Python MCP)

| Path | Purpose |
|------|---------|
| `pyproject.toml` / `requirements.txt` | `mcp` + `httpx` deps |
| `README.md` | SDK choice, run + Cursor setup |
| `.env.example` | `API_BASE_URL` (+ auth placeholders) |
| `.cursor/mcp.json` | Cursor MCP snippet (`command`/`args`/`env`) |
| `src/<pkg>/server.py` | FastMCP/MCPServer tools (1:1 with ops) |
| `src/<pkg>/client.py` | httpx → upstream API |
| `src/<pkg>/__main__.py` | `python -m <pkg>` stdio entry |
| `src/<pkg>/verify.py` | `python -m <pkg>.verify` — tool count and metadata (`<dist>-verify` after install) |
| `generation-manifest.json` | tools + generator metadata |

**Out of scope in this slice:** AgentCore cloud deploy scaffolding, Agent Rules enforcement/embedding, non-Python languages, GitHub repo write delivery.

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
AgentCore scaffold golden remains under `tests/goldens/agentcore_pets_tiny/`.

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

- `{base}` is the static `[project].version` in `pyproject.toml` (today `0.3.0`) for `.dev` / `rc` builds.
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
