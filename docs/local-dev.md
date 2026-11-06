# Local development (macOS & Windows)

This package is a **library + CLI**. Use the helpers below for install, test, lint, and a CLI smoke generate. Paths in examples are relative to the repo root so they work without `/tmp` (which is missing on native Windows).

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| Python **3.11+** | macOS/Linux/WSL: `python3`. Windows: [python.org](https://www.python.org/downloads/) installer (enable **py** launcher) or `winget install Python.Python.3.12`. |
| GNU Make (optional) | Routes to the right shell script. Not required if you call `scripts/*.sh` or `scripts/*.ps1` directly. |
| PowerShell 5+ / pwsh | Needed only for the Windows PowerShell helpers. |

## Shell matrix

| Host shell | Install / bootstrap | Test | CLI smoke | Notes |
|------------|---------------------|------|-----------|--------|
| **macOS** (zsh/bash) | `make bootstrap` or `sh scripts/bootstrap.sh` | `make test` | `make cli-smoke` | Uses `.venv/bin/python`. |
| **Linux** | same as macOS | same | same | |
| **WSL** (Ubuntu etc.) | same as macOS | same | same | Prefer WSL paths (`/home/...`), not `/mnt/c/...`, for fewer permission issues. |
| **Windows Git Bash** | `make bootstrap` or `sh scripts/bootstrap.sh` | `make test` | `make cli-smoke` | Make sees `MSYSTEM` and selects `*.sh` (not PowerShell). |
| **Windows PowerShell** | `make bootstrap` *or* `.\scripts\bootstrap.ps1` | `make test` *or* `.\scripts\test.ps1` | `.\scripts\cli-smoke.ps1` | Make without `MSYSTEM` selects `*.ps1` via `pwsh`/`powershell`. |
| **tox** (any of the above) | after bootstrap | `make tox` / `.\scripts\test.ps1 -Tox` / `python -m tox` | — | Skips missing interpreters (`py311`/`py312`). |

Force a helper family: `make USE_SH=1 test` or `make USE_PS1=1 test`.

## Make targets

```text
make help
make bootstrap   # .venv + pip install -e ".[dev]"
make test        # pytest
make tox         # tox env_list (pytest + lint)
make lint        # ruff format --check + ruff check
make format      # ruff format + ruff check --fix
make cli-smoke   # generate pets fixture → .smoke-out / .smoke-out.zip
make clean
```

## Dual helpers

| Task | POSIX (`*.sh`) | PowerShell (`*.ps1`) |
|------|----------------|----------------------|
| Bootstrap | `scripts/bootstrap.sh` | `scripts/bootstrap.ps1` |
| Test | `scripts/test.sh` | `scripts/test.ps1` |
| Tox | `scripts/test.sh --tox` | `scripts/test.ps1 -Tox` |
| Lint | `scripts/lint.sh` | `scripts/lint.ps1` |
| Format | `scripts/format.sh` | `scripts/format.ps1` |
| CLI smoke | `scripts/cli-smoke.sh` | `scripts/cli-smoke.ps1` |
| Clean | `scripts/clean.sh` | `scripts/clean.ps1` |

## Packaging & entrypoints

| Mechanism | How to invoke |
|-----------|----------------|
| Editable install | `pip install -e ".[dev]"` (after venv activate) |
| Module CLI | `python -m mcp_wizard_generators …` |
| Console script | `mcp-wizard-gen …` (on PATH after install; Windows: `.venv\Scripts\mcp-wizard-gen.exe`) |
| Library | `from mcp_wizard_generators import generate_python_mcp` |

CLI path arguments expand `~` on macOS, Linux, and Windows (`USERPROFILE`). Prefer **relative** paths in scripts and docs.

### Example (portable)

```bash
python -m mcp_wizard_generators generate-python \
  --ir tests/fixtures/pets_tiny.ir.json \
  --select listPets \
  --select showPetById \
  --package-name pets_tiny_mcp \
  --out-dir .smoke-out \
  --zip .smoke-out.zip
```

PowerShell (same args; line continuation uses `` ` ``):

```powershell
python -m mcp_wizard_generators generate-python `
  --ir tests/fixtures/pets_tiny.ir.json `
  --select listPets `
  --select showPetById `
  --package-name pets_tiny_mcp `
  --out-dir .smoke-out `
  --zip .smoke-out.zip
```

## Path / shell pitfalls fixed in this repo

- Zip member names always use **forward slashes** (Windows `Path.relative_to` otherwise emits `\`).
- Generated tree writes join **POSIX segments** so `src/pkg/file.py` nests correctly on Windows.
- Executable `chmod` is skipped on Windows (`nt`) and tolerates `OSError` elsewhere.
- Smoke / docs avoid `/tmp`; use repo-relative `.smoke-out`.
- Generated project READMEs document both Unix `source .venv/bin/activate` and PowerShell `.\.venv\Scripts\Activate.ps1`.

## Recommended day-to-day flow

1. Clone the repo.
2. `make bootstrap` (or the matching script for your shell).
3. `make test` before committing; `make lint` / `make format` per `AGENTS.md`.
4. `make cli-smoke` when changing CLI or packaging.
