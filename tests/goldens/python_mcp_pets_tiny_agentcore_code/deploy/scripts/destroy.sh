#!/usr/bin/env sh
# Delete the AgentCore runtime, its role and secret (the bootstrap is kept).
# Usage: deploy/scripts/destroy.sh
set -eu
cd "$(dirname "$0")/../.."
region="$(terraform -chdir=deploy/terraform/bootstrap output -raw aws_region)"
bucket="$(terraform -chdir=deploy/terraform/bootstrap output -raw state_bucket)"
terraform -chdir=deploy/terraform init -input=false -reconfigure -backend-config="bucket=$bucket" -backend-config="key=pets-tiny-mcp/terraform.tfstate" -backend-config="region=$region" -backend-config="use_lockfile=true" -backend-config="encrypt=true"
terraform -chdir=deploy/terraform destroy -input=false -var "code_key=code/unused.zip"
echo "The bootstrap (state bucket, code bucket, deploy role) is kept. To remove it, empty both buckets, then: terraform -chdir=deploy/terraform/bootstrap destroy"
