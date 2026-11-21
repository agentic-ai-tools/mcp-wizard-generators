#!/usr/bin/env sh
# Build the ARM64 image, push it to ECR, then apply Terraform.
# Usage: deploy/scripts/deploy.sh [image-tag]
set -eu
cd "$(dirname "$0")/../.."
region="$(terraform -chdir=deploy/terraform/bootstrap output -raw aws_region)"
bucket="$(terraform -chdir=deploy/terraform/bootstrap output -raw state_bucket)"
repo="$(terraform -chdir=deploy/terraform/bootstrap output -raw ecr_repository_url)"
# ECR tags are immutable, so every deploy gets a new tag.
tag="${1:-$(date -u +%Y%m%d%H%M%S)}"
aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "${repo%%/*}"
docker buildx build --platform linux/arm64 --provenance=false --sbom=false --push --tag "$repo:$tag" .
terraform -chdir=deploy/terraform init -input=false -reconfigure -backend-config="bucket=$bucket" -backend-config="key=pets-tiny-mcp/terraform.tfstate" -backend-config="region=$region" -backend-config="use_lockfile=true" -backend-config="encrypt=true"
terraform -chdir=deploy/terraform apply -input=false -var "image_tag=$tag"
echo "MCP URL: $(terraform -chdir=deploy/terraform output -raw mcp_url)"
