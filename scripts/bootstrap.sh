#!/usr/bin/env sh
# Create .venv and install this package editable with dev extras.
# Works on macOS, Linux, WSL, and Git Bash (not PowerShell — use bootstrap.ps1).
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "error: python3/python not found on PATH" >&2
  exit 1
fi

"$PY" -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
echo "bootstrap ok: $($PY -c 'import sys; print(sys.executable)')"
