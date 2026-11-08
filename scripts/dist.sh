#!/usr/bin/env sh
# Build wheel + sdist into dist/ (requires build from .[dev]).
# Works on macOS, Linux, WSL, and Git Bash (not PowerShell — use dist.ps1).
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
  echo "error: python3/python not found on PATH" >&2
  exit 1
fi

rm -rf dist build
"$PY" -m build
echo "dist ok: $(ls -1 dist)"
