# Contributing

Thanks for helping improve **`mcp-wizard-generators`** (import package **`mcp_wizard_generators`**).

## Code of Conduct

Participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By contributing, you agree to uphold it. Report concerns privately via the paths in [`SECURITY.md`](SECURITY.md) — do not open a public issue for CoC reports.

## License

Contributions are accepted under the [MIT License](LICENSE).

## How to propose changes

1. Fork (or branch from latest `main` in a clone). Never commit to `main`; never force-push.
2. Follow [`AGENTS.md`](AGENTS.md) **TDD:** add or update a failing test, implement until green, then refactor.
3. Run formatting and linting before commit; fix every reported issue.
4. Open a PR into `main` with a clear description of behavior and tests.

```bash
make bootstrap
make test      # pytest, skips e2e
make lint      # ruff format --check + ruff check
make format    # ruff format + ruff check --fix
```

Optional: `make e2e` (network), `make dist` (wheel + sdist), `make cli-smoke`.

Details and the macOS / Windows matrix: [`docs/local-dev.md`](docs/local-dev.md).

## Install

**Local editable (development):**

```bash
make bootstrap
# or: pip install -e ".[dev]"
```

**From PyPI (once published):**

```bash
pip install mcp-wizard-generators
```

## Pull requests

1. Keep CI green: lint, tests with coverage floor, Semgrep.
2. Do not add live secrets, private org-only docs, or GitHub Packages PyPI upload URLs (that registry does not support Python).

## Release / publish notes

Distribution name: **`mcp-wizard-generators`**. Install (once published): `pip install mcp-wizard-generators`.

Tag releases (`v*`) build artifacts for GitHub Release assets. Optional OIDC Trusted Publisher publish to TestPyPI / PyPI is stubbed in `.github/workflows/publish-pypi.yml` and requires org setup (see README).
