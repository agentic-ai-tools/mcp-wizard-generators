# Build the ARM64 image, push it to ECR, then apply Terraform.
# Usage: .\deploy\scripts\deploy.ps1 [image-tag]
param([string]$Tag = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
$region = terraform -chdir=deploy/terraform/bootstrap output -raw aws_region
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$bucket = terraform -chdir=deploy/terraform/bootstrap output -raw state_bucket
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$repo = terraform -chdir=deploy/terraform/bootstrap output -raw ecr_repository_url
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# ECR tags are immutable, so every deploy gets a new tag.
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $repo.Split('/')[0]
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker buildx build --platform linux/arm64 --provenance=false --sbom=false --push --tag "${repo}:$Tag" .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
terraform -chdir=deploy/terraform init -input=false -reconfigure -backend-config="bucket=$bucket" -backend-config="key=pets-tiny-mcp/terraform.tfstate" -backend-config="region=$region" -backend-config="use_lockfile=true" -backend-config="encrypt=true"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
terraform -chdir=deploy/terraform apply -input=false -var "image_tag=$Tag"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "MCP URL: $(terraform -chdir=deploy/terraform output -raw mcp_url)"
