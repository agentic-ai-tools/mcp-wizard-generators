#!/usr/bin/env sh
# Format check + lint via ruff (install with: pip install -e ".[dev]").
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi

"$PY" -m ruff format --check .
"$PY" -m ruff check .
echo "lint ok"
