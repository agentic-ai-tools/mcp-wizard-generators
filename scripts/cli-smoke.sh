#!/usr/bin/env sh
# Smoke the console entry / module CLI with portable relative paths (no Unix temp dir).
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

OUT_DIR=${MCP_WIZARD_SMOKE_OUT:-.smoke-out}
ZIP_PATH=${MCP_WIZARD_SMOKE_ZIP:-.smoke-out.zip}
rm -rf "$OUT_DIR" "$ZIP_PATH"

"$PY" -m mcp_wizard_generators list-targets
"$PY" -m mcp_wizard_generators generate-python \
  --ir tests/fixtures/pets_tiny.ir.json \
  --select listPets \
  --select showPetById \
  --package-name pets_tiny_mcp \
  --out-dir "$OUT_DIR" \
  --zip "$ZIP_PATH"

test -f "$OUT_DIR/src/pets_tiny_mcp/server.py"
test -f "$ZIP_PATH"
echo "cli-smoke ok: $OUT_DIR + $ZIP_PATH"
