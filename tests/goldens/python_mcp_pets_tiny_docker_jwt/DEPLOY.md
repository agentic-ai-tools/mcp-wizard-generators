# Deploy Pets Tiny API with Docker

How to run **Pets Tiny API** as a container with Docker. Each step shows the
commands for bash (macOS, Linux, WSL, Git Bash) and PowerShell.

## 1. Prerequisites

- Docker Engine 24+ or Docker Desktop, with Docker Compose 2.24+
  (`docker compose version`). Docker Desktop must use **Linux containers**.
- Python 3.11+ for the `verify` check (`pip install -r requirements.txt`, then
  `pip install --no-deps -e .`).
- An OIDC identity provider that can issue access tokens (step 3).
- A GitHub repository for this project (for CI and image releases).

## 2. Configure the server

Put the upstream API settings in `.env`. Compose passes them to the container;
the image never contains them (`.dockerignore` excludes `.env`).

```bash
cp .env.example .env
```

```powershell
Copy-Item .env.example .env
```

Edit `.env`: set `API_BASE_URL` and the credentials your API needs. Leave the
`MCP_*` lines commented out: `docker-compose.yml` sets them.

## 3. Set up your identity provider

Every request needs `Authorization: Bearer <access token>`. A token is accepted only
when it is signed by a key your provider publishes, comes from its issuer, has not
expired, and matches at least one allowed value below. Clients or scopes that do not
match get **403**; anything else wrong gets **401**.

Generated with:

- Discovery URL: `https://login.example.com/tenant/.well-known/openid-configuration`
- Audiences (`aud`): `api://pets`

Any OIDC provider works. To switch, change the values in `docker-compose.yml` (`MCP_JWT_DISCOVERY_URL`, `MCP_JWT_AUDIENCES`, `MCP_JWT_CLIENTS`, `MCP_JWT_SCOPES`), then restart:

| Provider | Discovery URL | What to allow |
| --- | --- | --- |
| Amazon Cognito | `https://cognito-idp.<region>.amazonaws.com/<user-pool-id>/.well-known/openid-configuration` | Access tokens carry `client_id` and no `aud`: allow the app **client** id (or scopes). |
| Okta | `https://<org>.okta.com/oauth2/<authorization-server-id>/.well-known/openid-configuration` | Allow the authorization server's **audience** (for example `api://default`). |
| Auth0 | `https://<tenant>.<region>.auth0.com/.well-known/openid-configuration` | Allow the API identifier as the **audience** (use your custom domain if you have one). |
| Microsoft Entra ID | `https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration` | Allow the Application ID URI or client id as the **audience**; scopes arrive as `scp`. Set the app's `accessTokenAcceptedVersion` to 2 so the issuer matches. |
| Keycloak | `https://<host>/realms/<realm>/.well-known/openid-configuration` | Add an audience mapper and allow that **audience**, or allow the **client** (`azp`). |

## 4. Build and run

```bash
deploy/scripts/run.sh
```

```powershell
./deploy/scripts/run.ps1
```

The script builds the image, starts it with `docker compose`, and waits for the
container health check. `deploy/scripts/build.sh` (`.ps1`) only builds the image.

The server listens on `http://127.0.0.1:8000/mcp`, published on `127.0.0.1` only. It runs as a
non-root user with a read-only filesystem and no Linux capabilities.

## 5. Verify

Get an access token from your provider for an allowed audience, client or scope,
then check that the server answers and exposes the expected tools:

```bash
export MCP_BEARER_TOKEN='<access token>'
python -m pets_tiny_mcp.verify --url http://127.0.0.1:8000/mcp
```

```powershell
$env:MCP_BEARER_TOKEN = '<access token>'
python -m pets_tiny_mcp.verify --url http://127.0.0.1:8000/mcp
```

`verify` reads the token only from `MCP_BEARER_TOKEN`, so it stays out of your shell
history, and never sends it over plain `http` to another machine.

## 6. Connect an MCP client

**Cursor**: add this to `.cursor/mcp.json` (the generated file there starts the
server locally over stdio instead):

```json
{
  "mcpServers": {
    "pets-tiny-mcp": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer ${env:MCP_BEARER_TOKEN}" }
    }
  }
}
```

**Claude Code**:

```bash
claude mcp add --transport http pets-tiny-mcp http://127.0.0.1:8000/mcp --header "Authorization: Bearer $MCP_BEARER_TOKEN"
```

```powershell
claude mcp add --transport http pets-tiny-mcp http://127.0.0.1:8000/mcp --header "Authorization: Bearer $env:MCP_BEARER_TOKEN"
```

Serve the container over HTTPS (a reverse proxy or load balancer in front of it)
before you publish the port beyond this machine, and use that HTTPS URL here.

## 7. Publish the image (GitHub Actions)

Push the project to GitHub. `.github/workflows/ci.yml` runs the tests and
builds the image, then calls it over HTTP, on every push and pull request.

To publish, tag a release. `.github/workflows/release.yml` builds for
`linux/amd64` and `linux/arm64` and pushes to
`ghcr.io/<owner>/<repo>` (with an SBOM and provenance):

```bash
git tag v1.0.0
git push origin v1.0.0
```

```powershell
git tag v1.0.0
git push origin v1.0.0
```

The workflow uses the repository's `GITHUB_TOKEN`; no secrets to set up.
New packages are private: change that under the package's settings on GitHub.
Run a published image anywhere with the same environment as
`docker-compose.yml` (`MCP_AUTH` and, with token checks, `MCP_JWT_*`).
`.github/dependabot.yml` opens weekly PRs for the actions and the base image.

## 8. Update and roll back

- **Server code**: regenerate the project in the wizard, or edit it, then build and
  run again (step 4).
- **Python packages** are pinned with hashes in `requirements.lock`. To move to newer
  versions inside the ranges in `requirements.txt`:

```bash
uv pip compile requirements.txt --universal --generate-hashes --python-version 3.11 --output-file requirements.lock
```

```powershell
uv pip compile requirements.txt --universal --generate-hashes --python-version 3.11 --output-file requirements.lock
```

- **Roll back** by running the previous image tag from `ghcr.io`, or by re-tagging
  an earlier commit.

## 9. Tear down

```bash
deploy/scripts/stop.sh
```

```powershell
./deploy/scripts/stop.ps1
```

Remove the image too with `docker image rm pets-tiny-mcp:local`.

## 10. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Container exits with `Cannot start: API_BASE_URL is not set` | Set it in `.env` (step 2). |
| `refusing to listen on 0.0.0.0 without authentication` | `MCP_AUTH` is missing: run with `docker-compose.yml`, or pass the same environment. |
| `no matching manifest for windows/amd64` | Switch Docker Desktop to Linux containers. |
| `port is already allocated` | Another program uses port 8000. Set `MCP_HOST_PORT=8001` (any free port) in `.env`, start again, and use that port in the URLs above. |
| `verify` says it cannot connect | Check `docker compose ps` and `docker compose logs`, and that the URL uses the published port. |
| 401 `the token was issued for a different audience` | Request the token for an allowed audience, or add it to `MCP_JWT_AUDIENCES`. |
| 401 `the token comes from a different issuer` | The discovery URL must be the token issuer's; for Entra ID use v2 tokens. |
| 403 | The token is valid but its client or scopes are not allowed (`MCP_JWT_CLIENTS`, `MCP_JWT_SCOPES`). |
| 503 `the token check is unavailable` | The server cannot reach the provider's discovery or JWKS URL; check the container's outbound network. |
