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
