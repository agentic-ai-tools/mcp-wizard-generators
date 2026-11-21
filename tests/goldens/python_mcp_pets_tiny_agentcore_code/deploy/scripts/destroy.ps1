# Delete the AgentCore runtime, its role and secret (the bootstrap is kept).
# Usage: .\deploy\scripts\destroy.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
$region = terraform -chdir=deploy/terraform/bootstrap output -raw aws_region
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$bucket = terraform -chdir=deploy/terraform/bootstrap output -raw state_bucket
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
terraform -chdir=deploy/terraform init -input=false -reconfigure -backend-config="bucket=$bucket" -backend-config="key=pets-tiny-mcp/terraform.tfstate" -backend-config="region=$region" -backend-config="use_lockfile=true" -backend-config="encrypt=true"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
terraform -chdir=deploy/terraform destroy -input=false -var "code_key=code/unused.zip"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'The bootstrap (state bucket, code bucket, deploy role) is kept. To remove it, empty both buckets, then: terraform -chdir=deploy/terraform/bootstrap destroy'
