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
& $py -m ruff format .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $py -m ruff check --fix .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "format ok"
