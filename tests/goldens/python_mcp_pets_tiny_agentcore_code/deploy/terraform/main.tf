data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# Created by bootstrap/, so a package can be uploaded before the runtime exists.
data "aws_s3_bucket" "code" {
  bucket = "${var.name}-code-${data.aws_caller_identity.current.account_id}"
}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  partition    = data.aws_partition.current.partition
  runtime_name = replace(var.name, "-", "_")
  log_groups   = "arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes"

  discovery_url   = var.jwt_discovery_url
  allowed_clients = var.jwt_allowed_clients
}

# Created empty by bootstrap/; its value is put with `aws secretsmanager put-secret-value`
# and never appears in Terraform code, plans or state.
data "aws_secretsmanager_secret" "api" {
  name = "${var.name}/api-credentials"
}

# Execution role the runtime assumes (AgentCore developer guide, "AgentCore Runtime
# execution role"), narrowed to this code bucket, log groups and secret.
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:*"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name               = "${var.name}-agentcore-runtime"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid       = "CodePackage"
    actions   = ["s3:GetObject"]
    resources = ["${data.aws_s3_bucket.code.arn}/*"]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:DescribeLogStreams", "logs:CreateLogGroup"]
    resources = ["${local.log_groups}/*"]
  }

  statement {
    sid       = "LogsResourcePolicy"
    actions   = ["logs:PutResourcePolicy"]
    resources = ["${local.log_groups}/${local.runtime_name}-*"]
  }

  statement {
    sid       = "LogGroups"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:*"]
  }

  statement {
    sid       = "LogEvents"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.log_groups}/*:log-stream:*"]
  }

  statement {
    sid = "Tracing"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }

  statement {
    sid = "WorkloadAccessToken"
    actions = [
      "bedrock-agentcore:GetWorkloadAccessToken",
      "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
      "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
    ]
    resources = [
      "arn:${local.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:workload-identity-directory/default",
      "arn:${local.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:workload-identity-directory/default/workload-identity/${local.runtime_name}-*",
    ]
  }

  statement {
    sid       = "ApiCredentials"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [data.aws_secretsmanager_secret.api.arn]
  }
}

resource "aws_iam_role_policy" "runtime" {
  name   = "agentcore-runtime"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json
}

resource "aws_bedrockagentcore_agent_runtime" "server" {
  agent_runtime_name = local.runtime_name
  description        = "MCP server for Pets Tiny API"
  role_arn           = aws_iam_role.runtime.arn

  # Direct code deployment: the zip from deploy/agentcore/package.py, run with the
  # AgentCore managed Python runtime on ARM64.
  agent_runtime_artifact {
    code_configuration {
      entry_point = ["main.py"]
      runtime     = "PYTHON_3_13"

      code {
        s3 {
          bucket = data.aws_s3_bucket.code.bucket
          prefix = var.code_key
        }
      }
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "MCP"
  }

  # AgentCore checks the bearer token before a request reaches the code,
  # so the server runs with MCP_AUTH=platform. Any OIDC provider works.
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url    = local.discovery_url
      allowed_audience = length(var.jwt_allowed_audiences) > 0 ? var.jwt_allowed_audiences : null
      allowed_clients  = length(local.allowed_clients) > 0 ? local.allowed_clients : null
      allowed_scopes   = length(var.jwt_allowed_scopes) > 0 ? var.jwt_allowed_scopes : null
    }
  }

  environment_variables = {
    MCP_AUTH        = "platform"
    API_BASE_URL    = var.api_base_url
    API_SECRETS_ARN = data.aws_secretsmanager_secret.api.arn
  }

  lifecycle {
    precondition {
      condition     = length(concat(var.jwt_allowed_audiences, local.allowed_clients, var.jwt_allowed_scopes)) > 0
      error_message = "Allow at least one audience, client or scope, so tokens issued for other apps are refused."
    }
  }

  depends_on = [aws_iam_role_policy.runtime]
}
