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
