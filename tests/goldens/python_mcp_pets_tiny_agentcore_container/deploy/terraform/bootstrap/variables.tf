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
