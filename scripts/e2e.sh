#!/usr/bin/env sh
# Install a generated MCP server, start it, and call a tool (needs network).
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "error: python not found; run scripts/bootstrap.sh first" >&2
  exit 1
fi

exec "$PY" -m pytest -m e2e "$@"
