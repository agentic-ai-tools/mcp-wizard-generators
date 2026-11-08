# mcp-wizard-generators

Pluggable **OpenAPI IR → MCP project** generators for [MCP Wizard](https://github.com/agentic-ai-tools).

This package is a **library + CLI**. It does **not** run HTTP servers or workers.  
`mcp-wizard-api` owns jobs, entitlements, zip/GitHub delivery, and invokes this package offline.

**Near-term slice:** `local` + `python` — runnable Anthropic-style MCP server zip + Cursor config.  
**Also registered:** `agentcore` + `python` scaffold (AgentCore deploy stubs; expand later).

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
make cli-smoke                 # portable generate smoke
make e2e                       # install + stdio + tool call (needs network; not part of make test)
make tox                       # optional isolated envs
```

`make e2e` generates the pets server, installs it in a fresh virtualenv, starts it, and calls `listpets` through an MCP client. It needs network access and is not part of `make test`.

Windows PowerShell twins: `.\scripts\bootstrap.ps1`, `.\scripts\test.ps1`, `.\scripts\e2e.ps1`, `.\scripts\cli-smoke.ps1`.  
Details and the **macOS / WSL / Git Bash / PowerShell** matrix: [`docs/local-dev.md`](docs/local-dev.md).

Golden coverage: `tests/fixtures/pets_tiny.ir.json` →  
`tests/goldens/python_mcp_pets_tiny/` (+ zip member assertions) for `listPets` + `showPetById`.  
AgentCore scaffold golden remains under `tests/goldens/agentcore_pets_tiny/`.

## Non-goals (this repo)

- Experience API / HTTP / auth / billing  
- Postgres / object storage  
- Long-running MCP or wizard servers  
- LangGraph (register later via the same plugin registry)
