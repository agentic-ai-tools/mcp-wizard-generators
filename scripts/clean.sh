#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
rm -rf .venv .tox .pytest_cache .ruff_cache .mypy_cache \
  .smoke-out .smoke-out.zip dist build \
  mcp_wizard_generators.egg-info src/mcp_wizard_generators.egg-info
find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find . -type d -name '*.egg-info' -prune -exec rm -rf {} + 2>/dev/null || true
echo "clean ok"
