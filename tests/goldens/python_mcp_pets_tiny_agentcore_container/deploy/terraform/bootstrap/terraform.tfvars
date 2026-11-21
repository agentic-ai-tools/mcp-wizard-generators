# One-time setup. Set github_repository to the repository that deploys.
aws_region        = "us-east-1"
name              = "pets-tiny-mcp"
github_repository = "OWNER/REPOSITORY"

# Set to false if this AWS account already has the GitHub OIDC provider.
create_github_oidc_provider = true
