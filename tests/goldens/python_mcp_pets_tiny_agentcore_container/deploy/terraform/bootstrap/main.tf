# One-time setup, run with administrator credentials (DEPLOY.md, step "Bootstrap").
# Creates what the deploy role needs but must not create itself: the Terraform state
# bucket, the ECR repository, the GitHub OIDC provider and the deploy role.
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.name
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  partition    = data.aws_partition.current.partition
  runtime_name = replace(var.name, "-", "_")
  github_oidc  = "token.actions.githubusercontent.com"
  # No repository: deploy with the scripts only, and skip the GitHub OIDC role.
  github      = var.github_repository != ""
  create_oidc = local.github && var.create_github_oidc_provider
  oidc_arn = local.github ? (
    local.create_oidc ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
  ) : null
}

# ---- Terraform state -------------------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = "${var.name}-tfstate-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "state" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state.json
}

# ---- Container registry ----------------------------------------------------------

resource "aws_ecr_repository" "server" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "server" {
  repository = aws_ecr_repository.server.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 30 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = { type = "expire" }
    }]
  })
}

# ---- GitHub Actions deploy role (OIDC: no long-lived keys) ------------------------

resource "aws_iam_openid_connect_provider" "github" {
  count          = local.create_oidc ? 1 : 0
  url            = "https://${local.github_oidc}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = local.github && !local.create_oidc ? 1 : 0
  url   = "https://${local.github_oidc}"
}

data "aws_iam_policy_document" "deploy_trust" {
  count = local.github ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc}:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Only workflow jobs that run in this repository's protected environment.
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc}:sub"
      values   = ["repo:${var.github_repository}:environment:${var.github_environment}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  count              = local.github ? 1 : 0
  name               = "${var.name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_trust[0].json
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "StateBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/*"]
  }

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:ListTagsForResource",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.server.arn]
  }

  statement {
    sid = "RuntimeRole"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRolePolicies",
      "iam:PutRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/${var.name}-agentcore-runtime"]
  }

  statement {
    sid       = "PassRuntimeRole"
    actions   = ["iam:PassRole"]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/${var.name}-agentcore-runtime"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["bedrock-agentcore.amazonaws.com"]
    }
  }

  # Runtime, its endpoints and workload identity. AgentCore names them after the
  # runtime, so the whole service is allowed in this account and Region.
  statement {
    sid       = "AgentCore"
    actions   = ["bedrock-agentcore:*"]
    resources = ["arn:${local.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:*"]
  }

  # Look the secret up by name only: the deploy role never reads or changes its value.
  statement {
    sid       = "SecretLookup"
    actions   = ["secretsmanager:DescribeSecret"]
    resources = [aws_secretsmanager_secret.api.arn]
  }
}

resource "aws_iam_role_policy" "deploy" {
  count  = local.github ? 1 : 0
  name   = "deploy-${var.name}"
  role   = aws_iam_role.deploy[0].id
  policy = data.aws_iam_policy_document.deploy.json
}

# ---- Upstream API credentials ----------------------------------------------------

# JSON such as {"API_KEY": "..."}. Terraform creates only the empty secret: put the
# value with `aws secretsmanager put-secret-value` (DEPLOY.md), so it never appears in
# Terraform code, plans or state. Created here so it can be filled before the first
# deploy, and so the deploy role never needs to manage it.
resource "aws_secretsmanager_secret" "api" {
  name                    = "${var.name}/api-credentials"
  description             = "Upstream API credentials for the ${var.name} MCP server (JSON)."
  recovery_window_in_days = 7
}
