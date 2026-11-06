# Smoke the module CLI with relative paths (no Unix temp-dir assumptions).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
  $py = $venvPy
} else {
  $cmd = Get-Command py -ErrorAction SilentlyContinue
  if (-not $cmd) { $cmd = Get-Command python -ErrorAction SilentlyContinue }
  if (-not $cmd) { throw "python not found; run .\scripts\bootstrap.ps1 first" }
  $py = $cmd.Source
}

$OutDir = if ($env:MCP_WIZARD_SMOKE_OUT) { $env:MCP_WIZARD_SMOKE_OUT } else { ".smoke-out" }
$ZipPath = if ($env:MCP_WIZARD_SMOKE_ZIP) { $env:MCP_WIZARD_SMOKE_ZIP } else { ".smoke-out.zip" }

if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }

& $py -m mcp_wizard_generators list-targets
& $py -m mcp_wizard_generators generate-python `
  --ir tests/fixtures/pets_tiny.ir.json `
  --select listPets `
  --select showPetById `
  --package-name pets_tiny_mcp `
  --out-dir $OutDir `
  --zip $ZipPath

$server = Join-Path $OutDir "src\pets_tiny_mcp\server.py"
if (-not (Test-Path $server)) { throw "missing $server" }
if (-not (Test-Path $ZipPath)) { throw "missing $ZipPath" }
Write-Host "cli-smoke ok: $OutDir + $ZipPath"
