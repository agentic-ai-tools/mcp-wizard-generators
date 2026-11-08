# Build wheel + sdist into dist/ (requires build from .[dev]).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
  $Py = $venvPy
} else {
  $launcher = Get-Command py -ErrorAction SilentlyContinue
  if ($launcher) {
    $Py = $launcher.Source
    $extra = @("-3")
  } else {
    $extra = @()
    foreach ($name in @("python", "python3")) {
      $cmd = Get-Command $name -ErrorAction SilentlyContinue
      if ($cmd) {
        $Py = $cmd.Source
        break
      }
    }
    if (-not $Py) {
      throw "python/py not found on PATH. Run .\scripts\bootstrap.ps1 first."
    }
  }
}

foreach ($p in @("dist", "build")) {
  if (Test-Path $p) {
    Remove-Item -Recurse -Force $p
  }
}

if ($extra) {
  & $Py @extra -m build
} else {
  & $Py -m build
}
Write-Host "dist ok: $((Get-ChildItem dist -ErrorAction SilentlyContinue | ForEach-Object Name) -join ', ')"
