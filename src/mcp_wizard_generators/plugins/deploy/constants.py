"""Pinned versions baked into generated deployment files (design ADR-026).

The worker that builds zips has no network (ADR-020), so every pin lives here.
Refresh them with a release; generated repos then keep them current with their
own Dependabot config.
"""

from __future__ import annotations

# Official Python image (Debian slim), pinned by multi-arch index digest so the same
# bytes are used on amd64 and arm64. Refresh:
#   docker buildx imagetools inspect python:3.13-slim
PYTHON_IMAGE = (
    "python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90"
)

# Port the containerized server listens on. AgentCore Runtime's MCP contract uses 8000.
CONTAINER_PORT = 8000
# Numeric non-root user in the runtime image.
CONTAINER_UID = 10001

# GitHub Actions used by generated workflows, pinned to full commit SHAs (a tag can
# be moved; a SHA cannot). The "# vX.Y.Z" comment lets Dependabot propose updates.
# Refresh: gh api repos/<owner>/<repo>/releases/latest, then resolve the tag's commit.
ACTIONS = {
    "actions/checkout": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
    "actions/setup-python": (
        "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0"
    ),
    "docker/setup-qemu-action": (
        "docker/setup-qemu-action@99012661954931238ded8c8b007157a8430204e1 # v4.4.0"
    ),
    "docker/setup-buildx-action": (
        "docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1"
    ),
    "docker/login-action": "docker/login-action@8aa5831fb8d48d8f76594c7ddde61ae58019f550 # v4.8.0",
    "docker/metadata-action": (
        "docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302 # v6.2.0"
    ),
    "docker/build-push-action": (
        "docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc # v7.4.0"
    ),
    "aws-actions/configure-aws-credentials": (
        "aws-actions/configure-aws-credentials@e1253824e5c10ff9df46874f81ed3ec929e19cfd # v6.3.0"
    ),
    "aws-actions/amazon-ecr-login": (
        "aws-actions/amazon-ecr-login@7290e214a610b32bc0eef81748948b6d466a8783 # v2.2.0"
    ),
    "hashicorp/setup-terraform": (
        "hashicorp/setup-terraform@dfe3c3f87815947d99a8997f908cb6525fc44e9e # v4.0.1"
    ),
    "astral-sh/setup-uv": "astral-sh/setup-uv@1db35dd8eee11c5894872e0b383ae6c406cf738d # v10.4.0",
}

# uv installed by generated workflows (the agentcore-code package script needs it:
# unlike pip, it evaluates the lock's markers for the target platform).
UV_VERSION = "0.13.9"

# Terraform used by generated workflows, and the AWS provider range generated modules
# accept. 6.73 is the version the AgentCore runtime resource was validated against.
TERRAFORM_VERSION = "1.17.2"
AWS_PROVIDER_VERSION = "~> 6.73"
# Docker platform AgentCore Runtime requires.
AGENTCORE_PLATFORM = "linux/arm64"
# Python versions the generated CI tests on (oldest and newest supported).
CI_PYTHON_VERSIONS = ("3.11", "3.13")

# Direct code deployment (agentcore-code): the AgentCore managed runtime and the
# matching wheel target. 3.10 and 3.11 are past their AgentCore deprecation dates.
AGENTCORE_CODE_RUNTIME = "PYTHON_3_13"
AGENTCORE_CODE_PYTHON = "3.13"
AGENTCORE_CODE_PLATFORM = "aarch64-manylinux2014"
