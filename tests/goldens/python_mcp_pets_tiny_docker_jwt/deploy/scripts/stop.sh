#!/usr/bin/env sh
# Stop and remove the server container.
# Usage: deploy/scripts/stop.sh
set -eu
cd "$(dirname "$0")/../.."
docker compose down
