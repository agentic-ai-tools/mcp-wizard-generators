# Deploy Pets Tiny API to AWS Bedrock AgentCore

How to run **Pets Tiny API** on AWS Bedrock AgentCore Runtime. Each step shows the
commands for bash (macOS, Linux, WSL, Git Bash) and PowerShell.

## 1. Prerequisites

- An AWS account, in a Region where Amazon Bedrock AgentCore is available, and
  administrator credentials for the one-time bootstrap (`aws sts get-caller-identity`).
- AWS CLI v2, Terraform 1.10 or newer, and Docker with `buildx` (Docker Desktop
  includes it). Images are built for `linux/arm64`, which AgentCore requires.
- Python 3.11+ for the `verify` check (`pip install -r requirements.txt`, then
  `pip install --no-deps -e .`).
- An OIDC identity provider that issues access tokens (step 3).
- A GitHub repository for this project, with Actions enabled.

## 2. Review the settings

`deploy/terraform/terraform.tfvars` holds this deployment's settings: Region, base name,
upstream API URL and identity provider. None of them are secrets; commit the
file. `deploy/terraform/bootstrap/terraform.tfvars` holds the bootstrap settings: set `github_repository` to `owner/name`.

## 3. Set up your identity provider

Every request needs `Authorization: Bearer <access token>`. A token is accepted only
when it is signed by a key your provider publishes, comes from its issuer, has not
expired, and matches at least one allowed value below. Clients or scopes that do not
match get **403**; anything else wrong gets **401**.

Generated with:

- Discovery URL: `https://login.example.com/tenant/.well-known/openid-configuration`
- Audiences (`aud`): `api://pets`

Any OIDC provider works. To switch, change the values in `deploy/terraform/terraform.tfvars` (`jwt_*`), then deploy again:

| Provider | Discovery URL | What to allow |
| --- | --- | --- |
| Amazon Cognito | `https://cognito-idp.<region>.amazonaws.com/<user-pool-id>/.well-known/openid-configuration` | Access tokens carry `client_id` and no `aud`: allow the app **client** id (or scopes). |
| Okta | `https://<org>.okta.com/oauth2/<authorization-server-id>/.well-known/openid-configuration` | Allow the authorization server's **audience** (for example `api://default`). |
| Auth0 | `https://<tenant>.<region>.auth0.com/.well-known/openid-configuration` | Allow the API identifier as the **audience** (use your custom domain if you have one). |
| Microsoft Entra ID | `https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration` | Allow the Application ID URI or client id as the **audience**; scopes arrive as `scp`. Set the app's `accessTokenAcceptedVersion` to 2 so the issuer matches. |
| Keycloak | `https://<host>/realms/<realm>/.well-known/openid-configuration` | Add an audience mapper and allow that **audience**, or allow the **client** (`azp`). |

## 4. Bootstrap AWS (once)

With administrator credentials, create the Terraform state bucket, the ECR
repository, the empty credentials secret and the GitHub deploy role:

```bash
deploy/scripts/bootstrap.sh
```

```powershell
./deploy/scripts/bootstrap.ps1
```

Keep `deploy/terraform/bootstrap/terraform.tfstate` safe (it holds no secrets): Terraform needs
it to change or remove the bootstrap later.

## 5. Store the API credentials

The server reads its upstream credentials from Secrets Manager at startup
with its own IAM role; they never go into Terraform or environment
variables. Write them to a local file `api-credentials.json` as
`{"API_KEY": "..."}`, store them, then delete the file:

```bash
aws secretsmanager put-secret-value --secret-id "$(terraform -chdir=deploy/terraform/bootstrap output -raw api_secret_arn)" --secret-string file://api-credentials.json
rm api-credentials.json
```

```powershell
aws secretsmanager put-secret-value --secret-id (terraform -chdir=deploy/terraform/bootstrap output -raw api_secret_arn) --secret-string file://api-credentials.json
Remove-Item api-credentials.json
```

## 6. Connect GitHub Actions

In the repository settings:

1. **Environments** → create `production` and add required reviewers, so
   every deploy waits for approval.
2. **Secrets and variables → Actions → Variables**, add (from the
   `terraform -chdir=deploy/terraform/bootstrap output` values):
   - `AWS_DEPLOY_ROLE_ARN` = `deploy_role_arn`
   - `AWS_REGION` = `aws_region`
   - `TF_STATE_BUCKET` = `state_bucket`

No AWS keys are stored: the workflow gets short-lived credentials through
GitHub's OIDC token, and only jobs in the `production` environment of this
repository can assume the role.

## 7. Deploy

Push to `main` (or run **Deploy to AgentCore** from the Actions tab). After
approval, `.github/workflows/deploy.yml` builds the ARM64 image, pushes it to
ECR tagged with the commit, and runs `terraform plan` and `apply`. The job
summary shows the MCP URL. `.github/workflows/ci.yml` runs the tests on every
push and pull request.

Or deploy from your machine with AWS credentials:

```bash
deploy/scripts/deploy.sh
```

```powershell
./deploy/scripts/deploy.ps1
```

The runtime runs your image with `MCP_AUTH=platform`: AgentCore rejects requests
without a valid token before they reach the container.

## 8. Verify

Get an access token from your identity provider for an allowed audience,
client or scope, then check that the server answers with its tools:

```bash
export MCP_BEARER_TOKEN='<access token>'
python -m pets_tiny_mcp.verify --url "$(terraform -chdir=deploy/terraform output -raw mcp_url)"
```

```powershell
$env:MCP_BEARER_TOKEN = '<access token>'
python -m pets_tiny_mcp.verify --url (terraform -chdir=deploy/terraform output -raw mcp_url)
```

`terraform -chdir=deploy/terraform output -raw mcp_url` prints the URL MCP clients use.

## 9. Connect an MCP client

**Cursor**: add this to `.cursor/mcp.json` (the generated file there starts the
server locally over stdio instead):

```json
{
  "mcpServers": {
    "pets-tiny-mcp": {
      "url": "<mcp_url output>",
      "headers": { "Authorization": "Bearer ${env:MCP_BEARER_TOKEN}" }
    }
  }
}
```

**Claude Code**:

```bash
claude mcp add --transport http pets-tiny-mcp <mcp_url output> --header "Authorization: Bearer $MCP_BEARER_TOKEN"
```

```powershell
claude mcp add --transport http pets-tiny-mcp <mcp_url output> --header "Authorization: Bearer $env:MCP_BEARER_TOKEN"
```

Replace `<mcp_url output>` with the full `mcp_url` value. Tokens expire;
get a new one when the client starts getting 401.

## 10. Update and roll back

- **Code or settings**: commit and push (the workflow deploys), or run the deploy script again. Every deploy pushes a new, immutable image tag.
- **Roll back** by applying an earlier tag: `terraform -chdir=deploy/terraform apply -var "image_tag=<earlier tag>"`.
- **Python packages** are pinned with hashes in `requirements-aws.lock` (the
  image) and `requirements.lock` (local use). To refresh them:

```bash
uv pip compile requirements.txt --universal --generate-hashes --python-version 3.11 --output-file requirements.lock
echo "boto3>=1.35,<2" | uv pip compile requirements.txt - --universal --generate-hashes --python-version 3.11 --output-file requirements-aws.lock
```

```powershell
uv pip compile requirements.txt --universal --generate-hashes --python-version 3.11 --output-file requirements.lock
echo "boto3>=1.35,<2" | uv pip compile requirements.txt - --universal --generate-hashes --python-version 3.11 --output-file requirements-aws.lock
```

## 11. Tear down

```bash
deploy/scripts/destroy.sh
```

```powershell
./deploy/scripts/destroy.ps1
```

This removes the runtime and its role. The bootstrap stays: to remove it,
empty the state bucket and the ECR repository, then run
`terraform -chdir=deploy/terraform/bootstrap destroy`.

## 12. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `verify` gets 401 | The token is missing, expired, or not from the configured provider; check the discovery URL and get a new token. |
| `verify` gets 403 | The token's audience, client or scopes are not in the allowed lists in `deploy/terraform/terraform.tfvars`. |
| Logs say the secret `does not exist or has no value yet` | Store the credentials (step 5). |
| The runtime does not start | Read its logs in CloudWatch under `/aws/bedrock-agentcore/runtimes/`. `Cannot start:` lines name the setting to fix. |
| `exec format error` in the logs | The image is not ARM64: build with `--platform linux/arm64`. |
| `terraform apply` fails right after the role is created | IAM changes take a few seconds to reach AgentCore; run apply again. |
| `ImageTagAlreadyExistsException` | ECR tags are immutable: deploy with a new tag. |
