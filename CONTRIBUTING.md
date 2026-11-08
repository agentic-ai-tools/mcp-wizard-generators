# Contributing

Thanks for helping improve **`mcp-wizard-generators`** (import package **`mcp_wizard_generators`**).

## Development

Follow [`AGENTS.md`](AGENTS.md):

- Branch from latest `main`; never commit to `main`; never force-push.
- **TDD:** add or update a failing test, implement until green, then refactor.
- Run formatting and linting before commit; fix every reported issue.

```bash
make bootstrap
make test      # pytest, skips e2e
make lint      # ruff format --check + ruff check
make format    # ruff format + ruff check --fix
```

Optional: `make e2e` (network), `make dist` (wheel + sdist), `make cli-smoke`.

Details and the macOS / Windows matrix: [`docs/local-dev.md`](docs/local-dev.md).

## Pull requests

1. Open a PR into `main` with a clear description of behavior and tests.
2. Keep CI green: lint, tests with coverage floor, Semgrep.
3. Do not add live secrets, private org-only docs, or GitHub Packages PyPI upload URLs (that registry does not support Python).

## Release / publish notes

Distribution name: **`mcp-wizard-generators`**. Install (once published): `pip install mcp-wizard-generators`.

Tag releases (`v*`) build artifacts for GitHub Release assets. Optional OIDC Trusted Publisher publish to TestPyPI / PyPI is stubbed in `.github/workflows/publish-pypi.yml` and requires org setup (see README).
