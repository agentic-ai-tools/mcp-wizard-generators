"""Terraform for the AWS Bedrock AgentCore targets (design ADR-026).

Two root modules, written into ``deploy/terraform``:

- ``bootstrap/``: run once with administrator credentials. The S3 state bucket,
  the ECR repository, the GitHub OIDC provider and a deploy role that only the
  project's GitHub environment can assume (ADR-011 style split).
- the main module: the AgentCore runtime, its least-privilege execution role, the
  Secrets Manager secret shell for upstream credentials (never its value), and an
  optional Cognito user pool. The JWT authorizer takes any OIDC provider.

Templates use ``__PLACEHOLDER__`` markers (HCL's ``${}`` would clash with f-strings);
every value spliced in is validated or JSON-quoted first.
"""

from __future__ import annotations

import json
import re

from mcp_wizard_generators.codegen import one_line
from mcp_wizard_generators.models import FileEntry, GenerateContext, ServerCapabilities
from mcp_wizard_generators.plugins.deploy.constants import AWS_PROVIDER_VERSION, TERRAFORM_VERSION

TF_DIR = "deploy/terraform"
BOOTSTRAP_DIR = f"{TF_DIR}/bootstrap"


def _hcl(value: object) -> str:
    """An HCL literal. JSON strings and lists are valid HCL; ``${`` is escaped."""
    return json.dumps(value).replace("${", "$${").replace("%{", "%%{")


_PLACEHOLDER = re.compile(r"__[A-Z][A-Z_]*__")


def _fill(template: str, **values: str) -> str:
    # Check the template before filling, so a value that happens to contain "__X__"
    # (an API title, say) is never mistaken for a missing placeholder.
    expected = set(_PLACEHOLDER.findall(template))
    assert expected == {f"__{key}__" for key in values}, (expected, sorted(values))
    for key, value in values.items():
        template = template.replace(f"__{key}__", value)
    return template


_VERSIONS = """\
terraform {
  required_version = ">= __TF_MIN__"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "__AWS__"
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
"""

_BACKEND = """\
# State lives in the S3 bucket that bootstrap/ creates. The deploy script and the
# GitHub workflow pass the settings with -backend-config (see backend.hcl.example).
terraform {
  backend "s3" {}
}
"""

_BACKEND_EXAMPLE = """\
# terraform -chdir=deploy/terraform init -backend-config=backend.hcl
# The bucket name is the state_bucket output of deploy/terraform/bootstrap.
bucket       = "<state_bucket output of bootstrap>"
key          = "__NAME__/terraform.tfstate"
region       = "__REGION__"
use_lockfile = true
encrypt      = true
"""

_GITIGNORE = """\
.terraform/
*.tfplan
tfplan
crash.log
"""

_VARIABLES = """\
variable "aws_region" {
  description = "AWS Region for the runtime (AgentCore must be available there)."
  type        = string
}

variable "name" {
  description = "Base name for every resource; must match the bootstrap name."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,46}$", var.name))
    error_message = "name: lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "image_tag" {
  description = "Image tag in the ECR repository to run. The deploy script and workflow pass a new tag on every deploy."
  type        = string
}

variable "api_base_url" {
  description = "Base URL of the upstream API the MCP tools call."
  type        = string

  validation {
    condition     = can(regex("^https?://[^/@?#]+", var.api_base_url))
    error_message = "api_base_url must be an http or https URL."
  }
}

variable "create_cognito" {
  description = "Create an Amazon Cognito user pool and use it as the identity provider."
  type        = bool
  default     = false
}

variable "jwt_discovery_url" {
  description = "OIDC discovery URL of your identity provider (ignored when create_cognito is true)."
  type        = string
  default     = ""

  validation {
    condition     = var.create_cognito || can(regex("^https://[^/?#@]+(/[^?#]*)?/[.]well-known/openid-configuration$", var.jwt_discovery_url))
    error_message = "jwt_discovery_url must be an https URL ending in /.well-known/openid-configuration."
  }
}

variable "jwt_allowed_audiences" {
  description = "Token audiences (aud) the runtime accepts."
  type        = list(string)
  default     = []
}

variable "jwt_allowed_clients" {
  description = "Client ids (client_id) the runtime accepts."
  type        = list(string)
  default     = []
}

variable "jwt_allowed_scopes" {
  description = "Scopes the runtime accepts."
  type        = list(string)
  default     = []
}
"""

_MAIN_HEAD = """\
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# Created by bootstrap/, so an image can be pushed before the runtime exists.
data "aws_ecr_repository" "server" {
  name = var.name
}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  partition    = data.aws_partition.current.partition
  runtime_name = replace(var.name, "-", "_")
  log_groups   = "arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes"

  discovery_url   = __DISCOVERY__
  allowed_clients = __CLIENTS__
}
"""

_SECRET = """\
# Created empty by bootstrap/; its value is put with `aws secretsmanager put-secret-value`
# and never appears in Terraform code, plans or state.
data "aws_secretsmanager_secret" "api" {
  name = "${var.name}/api-credentials"
}

"""

_BOOTSTRAP_SECRET = """
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
"""

_BOOTSTRAP_SECRET_OUTPUT = """
output "api_secret_arn" {
  description = "Secret to put the upstream API credentials (JSON) into."
  value       = aws_secretsmanager_secret.api.arn
}
"""

_ROLE = """\
# Execution role the runtime assumes (AgentCore developer guide, "AgentCore Runtime
# execution role"), narrowed to this repository, log groups and secret.
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
    sid       = "ECRImageAccess"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [data.aws_ecr_repository.server.arn]
  }

  statement {
    sid       = "ECRTokenAccess"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
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
__SECRET_STATEMENT__}

resource "aws_iam_role_policy" "runtime" {
  name   = "agentcore-runtime"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json
}

"""

_SECRET_STATEMENT = """
  statement {
    sid       = "ApiCredentials"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [data.aws_secretsmanager_secret.api.arn]
  }
"""

_RUNTIME = """\
resource "aws_bedrockagentcore_agent_runtime" "server" {
  agent_runtime_name = local.runtime_name
  description        = __DESCRIPTION__
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${data.aws_ecr_repository.server.repository_url}:${var.image_tag}"
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "MCP"
  }

  # AgentCore checks the bearer token before a request reaches the container,
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
__ENV__  }

  lifecycle {
    precondition {
      condition     = length(concat(var.jwt_allowed_audiences, local.allowed_clients, var.jwt_allowed_scopes)) > 0
      error_message = "Allow at least one audience, client or scope, so tokens issued for other apps are refused."
    }
  }

  depends_on = [aws_iam_role_policy.runtime]
}
"""

_COGNITO = """\
# Optional identity provider: a Cognito user pool with a machine-to-machine app client
# (OAuth client credentials). Cognito access tokens carry client_id and no aud, so the
# runtime allows this client id.
resource "aws_cognito_user_pool" "this" {
  count = var.create_cognito ? 1 : 0
  name  = "${var.name}-mcp"
}

resource "aws_cognito_resource_server" "this" {
  count        = var.create_cognito ? 1 : 0
  identifier   = "mcp"
  name         = "${var.name} MCP server"
  user_pool_id = aws_cognito_user_pool.this[0].id

  scope {
    scope_name        = "invoke"
    scope_description = "Call the MCP server"
  }
}

resource "aws_cognito_user_pool_client" "this" {
  count                                = var.create_cognito ? 1 : 0
  name                                 = "${var.name}-mcp-client"
  user_pool_id                         = aws_cognito_user_pool.this[0].id
  generate_secret                      = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["mcp/invoke"]
  supported_identity_providers         = ["COGNITO"]

  depends_on = [aws_cognito_resource_server.this]
}

resource "aws_cognito_user_pool_domain" "this" {
  count        = var.create_cognito ? 1 : 0
  domain       = "${var.name}-${local.account_id}"
  user_pool_id = aws_cognito_user_pool.this[0].id
}
"""

_OUTPUTS = """\
output "runtime_arn" {
  description = "ARN of the AgentCore runtime."
  value       = aws_bedrockagentcore_agent_runtime.server.agent_runtime_arn
}

output "mcp_url" {
  description = "URL MCP clients connect to (send Authorization: Bearer <token>)."
  value       = "https://bedrock-agentcore.${var.aws_region}.amazonaws.com/runtimes/${urlencode(aws_bedrockagentcore_agent_runtime.server.agent_runtime_arn)}/invocations?qualifier=DEFAULT"
}
"""

_OUTPUT_COGNITO = """
output "cognito_token_url" {
  description = "OAuth token endpoint of the created Cognito user pool."
  value       = var.create_cognito ? "https://${aws_cognito_user_pool_domain.this[0].domain}.auth.${var.aws_region}.amazoncognito.com/oauth2/token" : null
}

output "cognito_client_id" {
  description = "App client id for the client credentials flow."
  value       = var.create_cognito ? aws_cognito_user_pool_client.this[0].id : null
}

output "cognito_client_secret" {
  description = "App client secret (terraform output -raw cognito_client_secret)."
  value       = var.create_cognito ? aws_cognito_user_pool_client.this[0].client_secret : null
  sensitive   = true
}
"""

_TFVARS = """\
# Settings for this deployment. None of these are secrets, so commit this file:
# the deploy script and the GitHub workflow both use it.
aws_region   = __REGION__
name         = __NAME__
api_base_url = __BASE__

# Identity provider for the MCP endpoint (any OIDC provider; DEPLOY.md has examples).
create_cognito        = __COGNITO__
jwt_discovery_url     = __DISCOVERY__
jwt_allowed_audiences = __AUDIENCES__
jwt_allowed_clients   = __CLIENTS__
jwt_allowed_scopes    = __SCOPES__
"""

_BOOTSTRAP = """\
# One-time setup, run with administrator credentials (DEPLOY.md, step "Bootstrap").
# Creates what the deploy role needs but must not create itself: the Terraform state
# bucket, the ECR repository, the GitHub OIDC provider and the deploy role.
terraform {
  required_version = ">= __TF_MIN__"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "__AWS__"
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
__DEPLOY_EXTRA__}

resource "aws_iam_role_policy" "deploy" {
  count  = local.github ? 1 : 0
  name   = "deploy-${var.name}"
  role   = aws_iam_role.deploy[0].id
  policy = data.aws_iam_policy_document.deploy.json
}
"""

_DEPLOY_SECRET = """
  # Look the secret up by name only: the deploy role never reads or changes its value.
  statement {
    sid       = "SecretLookup"
    actions   = ["secretsmanager:DescribeSecret"]
    resources = [aws_secretsmanager_secret.api.arn]
  }
"""

_DEPLOY_COGNITO = """
  # Cognito user pool ids are random, so pool actions cannot be narrowed by name.
  statement {
    sid       = "Cognito"
    actions   = ["cognito-idp:*"]
    resources = ["arn:${local.partition}:cognito-idp:${var.aws_region}:${local.account_id}:userpool/*"]
  }

  statement {
    sid       = "CognitoCreate"
    actions   = ["cognito-idp:CreateUserPool", "cognito-idp:DescribeUserPoolDomain"]
    resources = ["*"]
  }
"""

_BOOTSTRAP_VARIABLES = """\
variable "aws_region" {
  description = "AWS Region (the same as deploy/terraform)."
  type        = string
}

variable "name" {
  description = "Base name; must match deploy/terraform."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,46}$", var.name))
    error_message = "name: lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "github_repository" {
  description = "GitHub repository allowed to deploy, as owner/name. Empty: no GitHub deploy role."
  type        = string
  default     = ""

  validation {
    condition     = var.github_repository == "" || can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/name, or be empty."
  }
}

variable "github_environment" {
  description = "GitHub environment whose jobs may assume the deploy role (add required reviewers to it)."
  type        = string
  default     = "production"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set false if the account already has one."
  type        = bool
  default     = true
}
"""

_BOOTSTRAP_OUTPUTS = """\
output "aws_region" {
  description = "Region (used by the deploy script)."
  value       = var.aws_region
}

output "state_bucket" {
  description = "S3 bucket for the main module's state (GitHub variable TF_STATE_BUCKET)."
  value       = aws_s3_bucket.state.bucket
}

output "ecr_repository_url" {
  description = "Where images are pushed."
  value       = aws_ecr_repository.server.repository_url
}

output "deploy_role_arn" {
  description = "Role the GitHub workflow assumes (GitHub variable AWS_DEPLOY_ROLE_ARN)."
  value       = local.github ? aws_iam_role.deploy[0].arn : null
}
"""

_BOOTSTRAP_TFVARS = """\
# One-time setup. __GITHUB_HINT__
aws_region        = __REGION__
name              = __NAME__
github_repository = __GITHUB__

# Set to false if this AWS account already has the GitHub OIDC provider.
create_github_oidc_provider = true
"""


def resource_name(caps: ServerCapabilities) -> str:
    """Terraform/ECR base name: the distribution name, which is lowercase with hyphens."""
    return caps.package_name.replace("_", "-")[:47]


def terraform_files(ctx: GenerateContext, caps: ServerCapabilities) -> list[FileEntry]:
    deployment = ctx.deployment
    auth = deployment.inbound_auth
    name = resource_name(caps)
    secret = bool(caps.secret_env_vars)
    base = ctx.ir.servers[0] if ctx.ir.servers else "https://api.example.com"
    versions = {"TF_MIN": TERRAFORM_VERSION_MIN, "AWS": AWS_PROVIDER_VERSION}

    if auth.create_cognito:
        discovery = (
            '"https://cognito-idp.${var.aws_region}.amazonaws.com/'
            '${aws_cognito_user_pool.this[0].id}/.well-known/openid-configuration"'
        )
        clients = "concat(var.jwt_allowed_clients, [aws_cognito_user_pool_client.this[0].id])"
    else:
        discovery = "var.jwt_discovery_url"
        clients = "var.jwt_allowed_clients"

    env = ['    MCP_AUTH     = "platform"', "    API_BASE_URL = var.api_base_url"]
    if secret:
        env = [
            '    MCP_AUTH        = "platform"',
            "    API_BASE_URL    = var.api_base_url",
            "    API_SECRETS_ARN = data.aws_secretsmanager_secret.api.arn",
        ]
    main = (
        _fill(_MAIN_HEAD, DISCOVERY=discovery, CLIENTS=clients)
        + "\n"
        + (_SECRET if secret else "")
        + _fill(_ROLE, SECRET_STATEMENT=_SECRET_STATEMENT if secret else "")
        + _fill(
            _RUNTIME,
            DESCRIPTION=_hcl(f"MCP server for {one_line(ctx.ir.title)[:200]}"),
            ENV="\n".join(env) + "\n",
        )
    )
    outputs = _OUTPUTS
    if auth.create_cognito:
        outputs += _OUTPUT_COGNITO
    tfvars = _fill(
        _TFVARS,
        REGION=_hcl(deployment.aws_region),
        NAME=_hcl(name),
        BASE=_hcl(base),
        COGNITO="true" if auth.create_cognito else "false",
        DISCOVERY=_hcl(auth.discovery_url),
        AUDIENCES=_hcl(list(auth.allowed_audiences)),
        CLIENTS=_hcl(list(auth.allowed_clients)),
        SCOPES=_hcl(list(auth.allowed_scopes)),
    )
    extra = (_DEPLOY_SECRET if secret else "") + (_DEPLOY_COGNITO if auth.create_cognito else "")
    files = [
        FileEntry(f"{TF_DIR}/versions.tf", _fill(_VERSIONS, **versions)),
        FileEntry(f"{TF_DIR}/backend.tf", _BACKEND),
        FileEntry(
            f"{TF_DIR}/backend.hcl.example",
            _fill(_BACKEND_EXAMPLE, NAME=name, REGION=deployment.aws_region),
        ),
        FileEntry(f"{TF_DIR}/.gitignore", _GITIGNORE),
        FileEntry(f"{TF_DIR}/variables.tf", _VARIABLES),
        FileEntry(f"{TF_DIR}/main.tf", main),
        FileEntry(f"{TF_DIR}/outputs.tf", outputs),
        FileEntry(f"{TF_DIR}/terraform.tfvars", tfvars),
        FileEntry(
            f"{BOOTSTRAP_DIR}/main.tf",
            _fill(_BOOTSTRAP, DEPLOY_EXTRA=extra, **versions)
            + (_BOOTSTRAP_SECRET if secret else ""),
        ),
        FileEntry(f"{BOOTSTRAP_DIR}/variables.tf", _BOOTSTRAP_VARIABLES),
        FileEntry(
            f"{BOOTSTRAP_DIR}/outputs.tf",
            _BOOTSTRAP_OUTPUTS + (_BOOTSTRAP_SECRET_OUTPUT if secret else ""),
        ),
        FileEntry(
            f"{BOOTSTRAP_DIR}/terraform.tfvars",
            _fill(
                _BOOTSTRAP_TFVARS,
                REGION=_hcl(deployment.aws_region),
                NAME=_hcl(name),
                **(
                    {
                        "GITHUB": '"OWNER/REPOSITORY"',
                        "GITHUB_HINT": "Set github_repository to the repository that deploys.",
                    }
                    if deployment.ci == "github-actions"
                    else {
                        "GITHUB": '""',
                        "GITHUB_HINT": "Set github_repository (owner/name) to add a GitHub "
                        "deploy role.",
                    }
                ),
            ),
        ),
    ]
    if auth.create_cognito:
        files.append(FileEntry(f"{TF_DIR}/cognito.tf", _COGNITO))
    return files


# ``required_version`` floor: S3 native state locking (use_lockfile) needs 1.10.
TERRAFORM_VERSION_MIN = "1.10"
__all__ = ["BOOTSTRAP_DIR", "TF_DIR", "TERRAFORM_VERSION", "resource_name", "terraform_files"]
