#!/usr/bin/env sh
# Run pytest (and optional tox) with the project venv when present.
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

if [ "${1:-}" = "--tox" ]; then
  shift
  exec "$PY" -m tox "$@"
fi

exec "$PY" -m pytest -m "not e2e" "$@"
