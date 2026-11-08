# Cross-platform local helpers for mcp-wizard-generators.
# Prefer `make <target>` from macOS, Linux, WSL, Git Bash, or Windows (GNU Make).
# PowerShell-only hosts can call the twin scripts under scripts/*.ps1 directly.
#
# Shell selection:
#   - Git Bash / MSYS (MSYSTEM set) → scripts/*.sh
#   - Windows_NT without MSYSTEM → scripts/*.ps1 via pwsh/powershell
#   - macOS / Linux / WSL → scripts/*.sh
# Override: `make USE_PS1=1 test` or `make USE_SH=1 test`

.PHONY: help bootstrap test e2e tox lint cli-smoke clean format dist

SHELL := /bin/sh

USE_WIN_PS := 0
ifeq ($(OS),Windows_NT)
  ifndef MSYSTEM
    USE_WIN_PS := 1
  endif
endif
ifdef USE_PS1
  USE_WIN_PS := 1
endif
ifdef USE_SH
  USE_WIN_PS := 0
endif

ifeq ($(USE_WIN_PS),1)
  # `where pwsh` prints a "not found" line on stdout, so a non-empty result
  # does not mean pwsh exists. `command -v` is empty when the shell cannot see it.
  ifneq ($(shell command -v pwsh 2>/dev/null),)
    PS := pwsh -NoProfile -File
  else
    PS := powershell -NoProfile -File
  endif
  RUN_BOOTSTRAP := $(PS) scripts/bootstrap.ps1
  RUN_TEST := $(PS) scripts/test.ps1
  RUN_E2E := $(PS) scripts/e2e.ps1
  RUN_TOX := $(PS) scripts/test.ps1 -Tox
  RUN_LINT := $(PS) scripts/lint.ps1
  RUN_SMOKE := $(PS) scripts/cli-smoke.ps1
  RUN_FORMAT := $(PS) scripts/format.ps1
  RUN_CLEAN := $(PS) scripts/clean.ps1
  RUN_DIST := $(PS) scripts/dist.ps1
else
  RUN_BOOTSTRAP := sh scripts/bootstrap.sh
  RUN_TEST := sh scripts/test.sh
  RUN_E2E := sh scripts/e2e.sh
  RUN_TOX := sh scripts/test.sh --tox
  RUN_LINT := sh scripts/lint.sh
  RUN_SMOKE := sh scripts/cli-smoke.sh
  RUN_FORMAT := sh scripts/format.sh
  RUN_CLEAN := sh scripts/clean.sh
  RUN_DIST := sh scripts/dist.sh
endif

help:
	@echo "mcp-wizard-generators local targets"
	@echo "  make bootstrap   create .venv + pip install -e '.[dev]'"
	@echo "  make test        run pytest (skips the e2e marker)"
	@echo "  make e2e         install, start, and invoke a generated MCP server (needs network)"
	@echo "  make tox         run tox (pytest in isolated envs, skips e2e)"
	@echo "  make lint        ruff format --check + ruff check"
	@echo "  make format      ruff format + ruff check --fix"
	@echo "  make dist        build wheel + sdist into dist/ (python -m build)"
	@echo "  make cli-smoke   generate pets fixture to .smoke-out(+.zip)"
	@echo "  make clean       remove caches, venv, smoke artifacts"
	@echo ""
	@echo "Windows PowerShell (no Make): .\\scripts\\bootstrap.ps1 | test.ps1 | e2e.ps1 | dist.ps1 | cli-smoke.ps1"
	@echo "Docs: docs/local-dev.md"

bootstrap:
	$(RUN_BOOTSTRAP)

test:
	$(RUN_TEST)

e2e:
	$(RUN_E2E)

tox:
	$(RUN_TOX)

lint:
	$(RUN_LINT)

format:
	$(RUN_FORMAT)

cli-smoke:
	$(RUN_SMOKE)

dist:
	$(RUN_DIST)

clean:
	$(RUN_CLEAN)
