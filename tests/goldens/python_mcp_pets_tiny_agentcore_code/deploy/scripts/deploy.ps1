# Package the server as an ARM64 zip, upload it to S3, then apply Terraform.
# Usage: .\deploy\scripts\deploy.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
$region = terraform -chdir=deploy/terraform/bootstrap output -raw aws_region
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$bucket = terraform -chdir=deploy/terraform/bootstrap output -raw state_bucket
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$codeBucket = terraform -chdir=deploy/terraform/bootstrap output -raw code_bucket
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# The key is the zip's SHA-256 (code/<sha256>.zip): unchanged code, same key.
$key = python deploy/agentcore/package.py | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
aws s3 cp deploy/agentcore/build/agentcore-code.zip "s3://$codeBucket/$key" --region $region
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
terraform -chdir=deploy/terraform init -input=false -reconfigure -backend-config="bucket=$bucket" -backend-config="key=pets-tiny-mcp/terraform.tfstate" -backend-config="region=$region" -backend-config="use_lockfile=true" -backend-config="encrypt=true"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
terraform -chdir=deploy/terraform apply -input=false -var "code_key=$key"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "MCP URL: $(terraform -chdir=deploy/terraform output -raw mcp_url)"
