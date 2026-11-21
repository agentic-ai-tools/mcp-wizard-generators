output "aws_region" {
  description = "Region (used by the deploy script)."
  value       = var.aws_region
}

output "state_bucket" {
  description = "S3 bucket for the main module's state (GitHub variable TF_STATE_BUCKET)."
  value       = aws_s3_bucket.state.bucket
}

output "code_bucket" {
  description = "S3 bucket for code packages (GitHub variable CODE_BUCKET)."
  value       = aws_s3_bucket.code.bucket
}

output "deploy_role_arn" {
  description = "Role the GitHub workflow assumes (GitHub variable AWS_DEPLOY_ROLE_ARN)."
  value       = local.github ? aws_iam_role.deploy[0].arn : null
}

output "api_secret_arn" {
  description = "Secret to put the upstream API credentials (JSON) into."
  value       = aws_secretsmanager_secret.api.arn
}
