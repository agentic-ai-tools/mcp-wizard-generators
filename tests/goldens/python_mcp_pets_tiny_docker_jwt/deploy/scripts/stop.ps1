# Stop and remove the server container.
# Usage: .\deploy\scripts\stop.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
docker compose down
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
