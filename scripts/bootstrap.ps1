# Create .venv and install this package editable with dev extras (Windows PowerShell).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Python {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
  $launcher = Get-Command py -ErrorAction SilentlyContinue
  if ($launcher) {
    & $launcher.Source -3 @PyArgs
    return
  }
  foreach ($name in @("python", "python3")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) {
      & $cmd.Source @PyArgs
      return
    }
  }
  throw "python/py not found on PATH. Install Python 3.11+ from https://python.org"
}

Invoke-Python -m venv .venv
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  throw "venv python missing at $venvPy"
}
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -e ".[dev]"
Write-Host "bootstrap ok: $venvPy"
