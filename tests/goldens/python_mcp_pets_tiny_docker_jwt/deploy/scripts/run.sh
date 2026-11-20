#!/usr/bin/env sh
# Start the server with docker compose and wait until its health check passes.
# Usage: deploy/scripts/run.sh
set -eu
cd "$(dirname "$0")/../.."
if [ ! -f .env ]; then
  echo "No .env file: copy .env.example to .env and set API_BASE_URL (and credentials)." >&2
  exit 1
fi
docker compose up --build --detach
container="$(docker compose ps --quiet pets-tiny-mcp)"
attempt=0
while :; do
  state="$(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' "$container")"
  case "$state" in
    "running healthy") break ;;
    exited*|dead*|restarting*|*unhealthy)
      echo "The server is not healthy ($state). Logs:" >&2
      docker compose logs --tail 50 pets-tiny-mcp >&2
      exit 1 ;;
  esac
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 45 ]; then
    echo "Timed out waiting for the health check. Logs: docker compose logs" >&2
    exit 1
  fi
  sleep 2
done
# The published host port (MCP_HOST_PORT in .env, default 8000).
url="http://$(docker compose port pets-tiny-mcp 8000)/mcp"
echo "Running on $url"
echo "Check it: python -m pets_tiny_mcp.verify --url $url"
