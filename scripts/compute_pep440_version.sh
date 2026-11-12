#!/usr/bin/env sh
# Thin wrapper for scripts/compute_pep440_version.py (Makefile / CI).
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

exec "$PY" scripts/compute_pep440_version.py "$@"
