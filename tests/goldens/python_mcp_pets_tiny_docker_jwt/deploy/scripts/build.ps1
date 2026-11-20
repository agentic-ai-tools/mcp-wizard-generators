# Build the container image.
# Usage: .\deploy\scripts\build.ps1 [image-tag]
param([string]$Tag = 'pets-tiny-mcp:local')
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
docker build --tag $Tag .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Built $Tag"
