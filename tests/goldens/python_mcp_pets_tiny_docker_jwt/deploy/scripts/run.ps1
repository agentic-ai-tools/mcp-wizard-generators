# Start the server with docker compose and wait until its health check passes.
# Usage: .\deploy\scripts\run.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
if (-not (Test-Path .env)) {
    [Console]::Error.WriteLine('No .env file: copy .env.example to .env and set API_BASE_URL (and credentials).')
    exit 1
}
docker compose up --build --detach
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$container = docker compose ps --quiet pets-tiny-mcp
for ($attempt = 1; $attempt -le 45; $attempt++) {
    $state = docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' $container
    if ($state -eq 'running healthy') {
        # The published host port (MCP_HOST_PORT in .env, default 8000).
        $url = "http://$(docker compose port pets-tiny-mcp 8000)/mcp"
        Write-Host "Running on $url"
        Write-Host "Check it: python -m pets_tiny_mcp.verify --url $url"
        exit 0
    }
    if ($state -match '^(exited|dead|restarting)' -or $state -match 'unhealthy$') {
        [Console]::Error.WriteLine("The server is not healthy ($state). Logs:")
        docker compose logs --tail 50 pets-tiny-mcp
        exit 1
    }
    Start-Sleep -Seconds 2
}
[Console]::Error.WriteLine('Timed out waiting for the health check. Logs: docker compose logs')
exit 1
