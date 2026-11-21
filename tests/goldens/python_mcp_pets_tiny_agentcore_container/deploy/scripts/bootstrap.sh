#!/usr/bin/env sh
# One-time setup with administrator credentials: state bucket, ECR repository, deploy role.
# Usage: deploy/scripts/bootstrap.sh
set -eu
cd "$(dirname "$0")/../.."
if grep -q 'OWNER/REPOSITORY' deploy/terraform/bootstrap/terraform.tfvars; then
  echo "Set github_repository in deploy/terraform/bootstrap/terraform.tfvars first." >&2
  exit 1
fi
terraform -chdir=deploy/terraform/bootstrap init -input=false
terraform -chdir=deploy/terraform/bootstrap apply -input=false
terraform -chdir=deploy/terraform/bootstrap output
echo "Next: DEPLOY.md, steps after Bootstrap."
