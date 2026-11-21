"""AgentCore Runtime entry point (direct code deployment; DEPLOY.md).

deploy/agentcore/package.py puts this file at the root of the zip as main.py.
AgentCore's MCP contract: streamable HTTP on 0.0.0.0:8000/mcp. MCP_AUTH is set
where the runtime is defined (deploy/terraform/main.tf); without it the server
refuses to listen on 0.0.0.0, so the package never runs unprotected by accident.
"""

import os

from pets_tiny_mcp.server import main

if __name__ == "__main__":
    os.environ.setdefault("MCP_TRANSPORT", "streamable-http")
    os.environ.setdefault("MCP_HOST", "0.0.0.0")
    os.environ.setdefault("MCP_PORT", "8000")
    os.environ.setdefault("MCP_PATH", "/mcp")
    main()
