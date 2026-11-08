# Security Policy

## Supported versions

Security fixes are applied to the latest release of **`mcp-wizard-generators`** on the `main` branch. Older tags may not receive backports.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security reports.

Prefer one of:

1. [GitHub private vulnerability reporting](https://github.com/agentic-ai-tools/mcp-wizard-generators/security/advisories/new) for this repository (when enabled), or
2. Email the maintainers via the contact listed on the [`agentic-ai-tools`](https://github.com/agentic-ai-tools) organization profile.

Include:

- Affected package version / commit
- Impact and reproduction steps (PoC if available)
- Any known workarounds

We aim to acknowledge reports within a few business days and will coordinate a fix and disclosure timeline with you.

## Scope notes

This repository is a **library + CLI** that generates MCP project trees. It does not run hosted HTTP services. Generated templates intentionally keep secrets in environment variables / `.env.example` placeholders — never commit live credentials to generated output or to this repo.
