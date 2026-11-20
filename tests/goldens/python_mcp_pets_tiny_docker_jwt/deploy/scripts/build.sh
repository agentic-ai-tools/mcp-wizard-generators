#!/usr/bin/env sh
# Build the container image.
# Usage: deploy/scripts/build.sh [image-tag]
set -eu
cd "$(dirname "$0")/../.."
tag="${1:-pets-tiny-mcp:local}"
docker build --tag "$tag" .
echo "Built $tag"
