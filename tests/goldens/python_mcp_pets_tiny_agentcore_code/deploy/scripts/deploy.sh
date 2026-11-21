#!/usr/bin/env sh
# Package the server as an ARM64 zip, upload it to S3, then apply Terraform.
# Usage: deploy/scripts/deploy.sh
set -eu
cd "$(dirname "$0")/../.."
region="$(terraform -chdir=deploy/terraform/bootstrap output -raw aws_region)"
bucket="$(terraform -chdir=deploy/terraform/bootstrap output -raw state_bucket)"
code_bucket="$(terraform -chdir=deploy/terraform/bootstrap output -raw code_bucket)"
# The key is the zip's SHA-256 (code/<sha256>.zip): unchanged code, same key.
key="$("${PYTHON:-python3}" deploy/agentcore/package.py)"
aws s3 cp deploy/agentcore/build/agentcore-code.zip "s3://$code_bucket/$key" --region "$region"
terraform -chdir=deploy/terraform init -input=false -reconfigure -backend-config="bucket=$bucket" -backend-config="key=pets-tiny-mcp/terraform.tfstate" -backend-config="region=$region" -backend-config="use_lockfile=true" -backend-config="encrypt=true"
terraform -chdir=deploy/terraform apply -input=false -var "code_key=$key"
echo "MCP URL: $(terraform -chdir=deploy/terraform output -raw mcp_url)"
