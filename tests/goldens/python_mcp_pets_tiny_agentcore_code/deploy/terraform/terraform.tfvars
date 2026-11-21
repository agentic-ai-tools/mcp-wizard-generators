# Settings for this deployment. None of these are secrets, so commit this file:
# the deploy script and the GitHub workflow both use it.
aws_region   = "us-east-1"
name         = "pets-tiny-mcp"
api_base_url = "https://petstore.example.com/v1"

# Identity provider for the MCP endpoint (any OIDC provider; DEPLOY.md has examples).
create_cognito        = false
jwt_discovery_url     = "https://login.example.com/tenant/.well-known/openid-configuration"
jwt_allowed_audiences = ["api://pets"]
jwt_allowed_clients   = []
jwt_allowed_scopes    = []
