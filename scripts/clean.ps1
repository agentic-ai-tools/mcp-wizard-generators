$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$paths = @(
  ".venv", ".tox", ".pytest_cache", ".ruff_cache", ".mypy_cache",
  ".smoke-out", ".smoke-out.zip", "dist", "build",
  "mcp_wizard_generators.egg-info", "src\mcp_wizard_generators.egg-info"
)
foreach ($p in $paths) {
  if (Test-Path $p) {
    Remove-Item -Recurse -Force $p
  }
}
Get-ChildItem -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Directory -Filter "*.egg-info" -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "clean ok"
