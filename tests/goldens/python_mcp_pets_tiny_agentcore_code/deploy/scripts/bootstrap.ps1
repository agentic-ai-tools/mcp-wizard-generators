# One-time setup with administrator credentials: state bucket, code bucket, deploy role.
# Usage: .\deploy\scripts\bootstrap.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
if (Select-String -Path deploy/terraform/bootstrap/terraform.tfvars -Pattern 'OWNER/REPOSITORY' -Quiet) {
    [Console]::Error.WriteLine('Set github_repository in deploy/terraform/bootstrap/terraform.tfvars first.')
    exit 1
}
terraform -chdir=deploy/terraform/bootstrap init -input=false
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
terraform -chdir=deploy/terraform/bootstrap apply -input=false
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
terraform -chdir=deploy/terraform/bootstrap output
Write-Host 'Next: DEPLOY.md, steps after Bootstrap.'
