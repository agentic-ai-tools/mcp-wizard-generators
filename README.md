# mcp-wizard-generators

Pluggable **OpenAPI IR → MCP project** generators for [MCP Wizard](https://github.com/agentic-ai-tools).

This package is a **library + CLI**. It does **not** run HTTP servers or workers.  
`mcp-wizard-api` owns jobs, entitlements, zip/GitHub delivery, and invokes this package offline.

**MVP primary plugin:** `agentcore` + `python` (AWS Bedrock AgentCore) — ADR-001 / ADR-005.

## Layout

```
src/mcp_wizard_generators/
  models.py          # IntermediateRep, ToolMap, FileTree, GenerateContext
  toolmap.py         # IR ops → MCP tool names / JSON Schema inputs
  pipeline.py        # generate(): IR → tool map → rules hooks → plugin → tree
  registry.py        # Plugin registry keyed by (target, language)
  cli.py             # mcp-wizard-gen / python -m mcp_wizard_generators
  rules/             # Cursor + Anthropic enforcement stubs (ADR-007)
  packaging/         # zip helper for Artifact Service
  plugins/agentcore/ # AWS Bedrock AgentCore + Python emitter
tests/
  fixtures/          # tiny pets OpenAPI + IR
  goldens/           # expected AgentCore file tree
```

## Pipeline

1. **IR normalize** (caller / Import Service) → `IntermediateRep` JSON  
2. **Tool mapping** → `ToolMap`  
3. **Agent Rules hooks** (Cursor + Anthropic stubs)  
4. **Plugin generate** → `FileTree`  
5. **Package** → zip bytes (optional) or write to disk  

## How `mcp-wizard-api` should invoke this

### Library (preferred in workers)

```python
from mcp_wizard_generators import generate, IntermediateRep
from mcp_wizard_generators.packaging import pack_file_tree_to_zip

# snapshot comes from GenerationJob.snapshot (IR subset + selections)
ir = IntermediateRep.from_dict(job_snapshot["ir"])
result = generate(
    ir,
    target=job_snapshot.get("targetArchitecture", "agentcore"),
    language=job_snapshot.get("language", "python"),
    selected_operation_ids=job_snapshot["selectedOperationIds"],
    tool_name_overrides=job_snapshot.get("toolNameOverrides"),
    package_name="generated_mcp",
)

# Experience API Artifact Service stores the zip (MinIO/S3) or writes to GitHub.
zip_bytes = pack_file_tree_to_zip(result.tree)
# result.manifest → Artifact.manifest
# result.tree.write_to(workdir)  # optional before git commit
```

Pin a released version of this package from the API deployable / Lambda layer.  
Do **not** vendor template trees inside `mcp-wizard-api` PRs (ADR-005).

### CLI (local / scripts / one-shot jobs)

```bash
pip install -e ".[dev]"

# List plugins
python -m mcp_wizard_generators list-targets

# Generate tree + zip from an IR fixture
python -m mcp_wizard_generators generate \
  --ir tests/fixtures/pets_tiny.ir.json \
  --target agentcore \
  --language python \
  --select listPets \
  --select showPetById \
  --out-dir /tmp/out \
  --zip /tmp/out.zip \
  --manifest /tmp/manifest.json
```

Console script (after install): `mcp-wizard-gen …`

## Agent Rules stubs

`rules/cursor.py` and `rules/anthropic.py` are **hooks only** in this scaffold:

- Detect Cursor (`.mdc`, `.cursorrules`, `AGENTS.md`) vs Anthropic (`CLAUDE.md`, `.claude/`) originals  
- Emit warn-level reports when `formatFamily` does not match files  
- Real naming/path enforcement lands in follow-up work  

Wire session rules into `AgentRulesBundle` before calling `generate()`.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Golden coverage: `tests/fixtures/pets_tiny.ir.json` (tiny OpenAPI subset) →  
`tests/goldens/agentcore_pets_tiny/` for `listPets` + `showPetById`.

## Non-goals (this repo)

- Experience API / HTTP / auth / billing  
- Postgres / object storage  
- Long-running MCP or wizard servers  
- LangGraph (register later via the same plugin registry)
