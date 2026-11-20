"""Emit a locally runnable Python MCP server from selected OpenAPI operations.

Uses the official MCP Python SDK high-level server API: MCPServer on mcp 2.x
(`from mcp.server.mcpserver import MCPServer`); generated projects pin mcp>=2,<3.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files
from textwrap import dedent

from mcp_wizard_generators.codegen import (
    docstring_text,
    env_value,
    one_line,
    py_ident,
    py_str,
    toml_str,
)
from mcp_wizard_generators.findings import Finding, analyze_operation, visible
from mcp_wizard_generators.models import FileEntry, FileTree, GenerateContext, ToolMapping
from mcp_wizard_generators.openapi import IN_KEY, WHOLE_BODY_KEY
from mcp_wizard_generators.plugins.python_mcp import client_template, tests_template
from mcp_wizard_generators.plugins.python_mcp.annotations import annotation as _annotation


# Names a generated tool function or parameter must not take: module globals the
# server uses, locals in each tool body, and builtins used in annotations.
def _lock_file(name: str) -> str:
    """A file from the packaged lock directory (see scripts/lock_python_mcp.py)."""
    path = files("mcp_wizard_generators.plugins.python_mcp").joinpath("lock", name)
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _direct_requirements() -> list[str]:
    """Direct dependencies with upper bounds; one source for every declaration."""
    lines = _lock_file("requirements.in").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


_TYPING_NAMES = ("Any", "Annotated", "Literal", "Field", "JsonValue", "re")
_RESERVED_MODULE_NAMES = frozenset(
    {
        "mcp",
        "request",
        "main",
        "quote",
        "annotations",
        "sys",
        "MCPServer",
        "check_config",
        "ConfigError",
        *_TYPING_NAMES,
    }
)
_RESERVED_PARAM_NAMES = frozenset(
    {
        "path",
        "path_template",
        "payload",
        "query",
        "headers",
        "cookies",
        "request",
        "quote",
        "secrets",
        "sys",
        "mcp",
        *_TYPING_NAMES,
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
    }
)


class PythonMcpPlugin:
    """
    Near-term local MCP target: Anthropic / official MCP Python SDK layout.

    No AgentCore deploy stubs and no Agent Rules embedding (out of scope for this slice).
    """

    key = "local"
    language = "python"
    display_name = "Local Python MCP (stdio)"

    def generate(self, ctx: GenerateContext) -> FileTree:
        pkg = ctx.package_name
        tools = ctx.tool_map.tools
        root_name = pkg.replace("_", "-")
        entries: list[FileEntry] = [
            FileEntry("README.md", self._readme(ctx)),
            FileEntry("pyproject.toml", self._pyproject(pkg, ctx)),
            FileEntry("requirements.txt", self._requirements()),
            FileEntry("requirements.lock", self._lock()),
            FileEntry(".env.example", self._env_example(ctx)),
            FileEntry(".cursor/mcp.json", self._cursor_mcp_json(pkg, ctx)),
            FileEntry(f"src/{pkg}/__init__.py", self._init(ctx)),
            FileEntry(f"src/{pkg}/__main__.py", self._main_module(pkg)),
            FileEntry(f"src/{pkg}/server.py", self._server(pkg, ctx, tools)),
            FileEntry(f"src/{pkg}/client.py", self._http_client(ctx)),
            FileEntry(f"src/{pkg}/verify.py", self._verify_module(pkg, ctx, tools)),
            FileEntry("generation-manifest.json", json.dumps(self._manifest(ctx), indent=2) + "\n"),
        ]
        notes = self._security_notes(ctx)
        if notes:
            entries.append(FileEntry("SECURITY-NOTES.md", notes))
        entries += [FileEntry(path, text) for path, text in self._tests(ctx).items()]
        return FileTree(entries=entries, root_name=root_name)

    @staticmethod
    def _schema_parts(tool: ToolMapping) -> tuple[dict, dict, list[str]]:
        schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = [
            n
            for n in dict.fromkeys(schema.get("required") or [])
            if isinstance(n, str) and n in props
        ]
        return schema, props, required

    @staticmethod
    def _param_names(props: dict) -> dict[str, str]:
        """Spec names -> safe Python identifiers; the wire name is kept for the HTTP call."""
        taken: set[str] = set()
        return {wire: py_ident(wire, taken, _RESERVED_PARAM_NAMES) for wire in props}

    def _tests(self, ctx: GenerateContext) -> dict[str, str]:
        """The project's tests/ folder (tool regression, auth, HTTP errors, redaction)."""
        cases: list[dict] = []
        sensitive: list[dict] = []
        for tool in ctx.tool_map.tools:
            schema, props, required = self._schema_parts(tool)
            case, redaction = tests_template.tool_case(
                name=tool.tool_name,
                method=tool.method,
                path=tool.path,
                props=props,
                required=required,
                py_names=self._param_names(props),
                secret_wires=[f.parameter for f in analyze_operation(schema) if f.parameter],
            )
            cases.append(case)
            if redaction:
                sensitive.append(redaction)
        return tests_template.build(ctx.package_name, ctx.security.auth_mode, cases, sensitive)

    @staticmethod
    def _tool_findings(tools: list[ToolMapping]) -> list[tuple[ToolMapping, list[Finding]]]:
        """Findings worth showing, per included tool (ADR-023); clean tools omitted."""
        out = []
        for tool in tools:
            found = visible(analyze_operation(tool.input_schema))
            if found:
                out.append((tool, found))
        return out

    @staticmethod
    def _auth_notes(ctx: GenerateContext) -> list[str]:
        sec = ctx.security
        if sec.auth_mode == "api_key" and sec.api_key_location == "query":
            return [
                f"- **API key in the query string.** Every request sends the API key as "
                f"`{one_line(sec.api_key_name)}` in the URL, where proxies, servers and logs "
                "can record it. Prefer a header if the API accepts one (Security step: API key "
                "location = header), and keep the key out of shared logs."
            ]
        return []

    def _security_notes(self, ctx: GenerateContext) -> str:
        tool_findings = self._tool_findings(ctx.tool_map.tools)
        auth_notes = self._auth_notes(ctx)
        if not tool_findings and not auth_notes:
            return ""
        lines = [
            f"# Security notes: {one_line(ctx.ir.title)} MCP",
            "",
            "The wizard found these risks in the operations and settings you chose.",
            "They come from the upstream API's design: the generated server sends each value",
            "where the API expects it, and keeps secrets out of its own error messages.",
            "",
        ]
        if tool_findings:
            lines += ["## Tools", ""]
            for tool, found in tool_findings:
                lines.append(
                    f"### `{tool.tool_name}` ({tool.method.upper()} {one_line(tool.path)})"
                )
                lines.append("")
                for f in found:
                    lines.append(f"- **{f.severity.capitalize()}**: {f.message}")
                    lines.append(f"  {f.remediation}")
                lines.append("")
            lines += [
                "### Remove a tool",
                "",
                "- **Regenerate (recommended):** in the wizard, untick the operation on the",
                "  Operations step and generate again.",
                "- **Edit this project:** delete the tool's `@mcp.tool` function from",
                f"  `src/{ctx.package_name}/server.py`, remove its entry from",
                "  `generation-manifest.json`, from `EXPECTED` in `verify.py` and from",
                "  `TOOLS` in `tests/test_tools.py`, then run",
                f"  `python -m {ctx.package_name}.verify` and `python -m pytest`.",
                "",
            ]
        if auth_notes:
            lines += ["## Authentication", "", *auth_notes, ""]
        return "\n".join(lines)

    def _manifest(self, ctx: GenerateContext) -> dict:
        return {
            "target": self.key,
            "language": self.language,
            "sdk": "mcp (official Python SDK; MCPServer high-level API, mcp>=2,<3)",
            "title": ctx.ir.title,
            "version": ctx.ir.version,
            "package": ctx.package_name,
            "tools": [
                {
                    "name": t.tool_name,
                    "operationId": t.operation_id,
                    "method": t.method,
                    "path": t.path,
                }
                for t in ctx.tool_map.tools
            ],
            "security": ctx.security.manifest(),
            "findings": [
                {"tool": tool.tool_name, "operationId": tool.operation_id, **f.to_dict()}
                for tool, found in self._tool_findings(ctx.tool_map.tools)
                for f in found
            ],
        }

    def _readme(self, ctx: GenerateContext) -> str:
        pkg = ctx.package_name
        tool_lines = (
            "\n".join(
                f"- `{t.tool_name}` ← `{t.method.upper()} {one_line(t.path)}` — "
                f"{one_line(t.description)}"
                for t in ctx.tool_map.tools
            )
            or "(none)"
        )
        base = ctx.ir.servers[0] if ctx.ir.servers else "https://api.example.com"
        # Build with explicit newlines (avoid textwrap.dedent + f-string indent drift).
        return "\n".join(
            [
                f"# {one_line(ctx.ir.title)} MCP",
                "",
                "Generated by **mcp-wizard-generators** (`local` / `python`).",
                "",
                "## SDK choice",
                "",
                "This project uses the **official MCP Python SDK** (`mcp` on PyPI) **2.x**",
                "high-level **MCPServer** API (`from mcp.server.mcpserver import MCPServer`).",
                "Dependencies pin `mcp>=2.0.0,<3`. (On mcp 1.x the same class was named",
                "**FastMCP** under `mcp.server.fastmcp`.) Tools speak **stdio**, which Cursor",
                "expects for local MCP servers.",
                "",
                "## Tools (1:1 with selected OpenAPI operations)",
                "",
                tool_lines,
                "",
                "## Run locally",
                "",
                "Create a venv, activate it (see matrix below), then install and run.",
                "`requirements.lock` pins every package with sha256 hashes, so installs are",
                "reproducible and pip refuses anything that was tampered with:",
                "",
                "```bash",
                "python3 -m venv .venv",
                "# activate — use the row that matches your OS / shell (table below)",
                "pip install --require-hashes -r requirements.lock",
                "pip install --no-deps -e .",
                "# unpinned alternative (newest allowed versions): pip install -r requirements.txt",
                "cp .env.example .env   # set API_BASE_URL (and auth if needed)",
                f"python -m {pkg}",
                "```",
                "",
                "### Virtualenv activate paths",
                "",
                "| Environment | Activate |",
                "| --- | --- |",
                "| macOS / Linux / WSL / Git Bash on Linux | `source .venv/bin/activate` |",
                "| Windows cmd | `.venv\\Scripts\\activate.bat` |",
                "| Windows PowerShell | `.\\.venv\\Scripts\\Activate.ps1` |",
                "| Git Bash on Windows | `source .venv/Scripts/activate` (typical for a Windows-created venv). "
                "If your venv uses a Unix layout instead, use `source .venv/bin/activate`. |",
                "",
                "**Windows PowerShell (full example):**",
                "",
                "```powershell",
                "py -3 -m venv .venv; .\\.venv\\Scripts\\Activate.ps1",
                "pip install --require-hashes -r requirements.lock",
                "pip install --no-deps -e .",
                "Copy-Item .env.example .env",
                f"python -m {pkg}",
                "```",
                "",
                "Upstream API base URL defaults from the OpenAPI servers list",
                f"(`{one_line(base)}`). Override with `API_BASE_URL`.",
                "",
                *self._readme_auth(ctx),
                *self._readme_security(ctx),
                "## Errors and limits",
                "",
                "- `API_BASE_URL` must be an `http` or `https` URL with a host and no user name,",
                "  password, query string or fragment. The server checks it, and the",
                "  authentication settings, at startup and exits with a clear message.",
                "- Failed calls return a short, actionable error (HTTP status, what to check, and",
                "  the API's own message). API keys, OAuth secrets and tokens, and secret",
                "  arguments such as passwords are replaced with `***`.",
                "- Responses larger than `MAX_RESPONSE_BYTES` (default 1048576, measured after",
                "  decompression) are refused. Redirects are not followed.",
                "",
                "## Verify",
                "",
                "Check that the server exposes the tools selected at generation time.",
                "The command is the same on macOS, Linux, and Windows. It lists tools in-process",
                "and does not call the upstream API, so `API_BASE_URL` is not required.",
                "",
                "```bash",
                f"python -m {pkg}.verify",
                "```",
                "",
                "It prints UTF-8 JSON, including `toolCount`, and exits 0 when the exposed tool",
                "names match. After `pip install`, "
                f"`{pkg.replace('_', '-')}-verify` runs the same check.",
                "",
                "## Tests",
                "",
                "`tests/` checks this server without calling the real API (every HTTP request",
                "goes to an in-memory mock): the tools and their arguments, authentication",
                "failures, HTTP status handling, and redaction of credentials in errors.",
                "",
                "```bash",
                'pip install "pytest>=8,<10"',
                "python -m pytest",
                "```",
                "",
                "Run them after you edit `server.py` or `client.py`, or upgrade dependencies.",
                "",
                "## Configure in Cursor",
                "",
                "1. Copy `.cursor/mcp.json` into your project's `.cursor/mcp.json`, **or**",
                "2. Open **Cursor Settings → MCP** and merge the `mcpServers` entry.",
                "",
                "Point `command`/`args` at the venv Python that has this package installed",
                "(on Windows prefer the full path to `.venv\\Scripts\\python.exe`),",
                "or use an absolute path. Set `API_BASE_URL` (and secrets) under `env`.",
                "",
                "## Layout",
                "",
                "```",
                "pyproject.toml / requirements.txt",
                "requirements.lock  # hash-pinned install (pip --require-hashes)",
                ".env.example",
                ".cursor/mcp.json",
                f"src/{pkg}/",
                f"  __main__.py      # python -m {pkg}",
                "  server.py        # MCP tools + stdio entry",
                "  client.py        # httpx client → API_BASE_URL",
                f"  verify.py        # python -m {pkg}.verify",
                "tests/            # python -m pytest (no network)",
                "generation-manifest.json",
                "```",
                "",
            ]
        )

    def _readme_security(self, ctx: GenerateContext) -> list[str]:
        if not self._tool_findings(ctx.tool_map.tools) and not self._auth_notes(ctx):
            return []
        return [
            "## Security notes",
            "",
            "Some selected operations or settings expose secrets (for example a password in",
            "the URL). Read **SECURITY-NOTES.md** before you run this server; it explains each",
            "risk and how to remove a tool.",
            "",
        ]

    def _readme_auth(self, ctx: GenerateContext) -> list[str]:
        sec = ctx.security
        lines = ["## Authentication", ""]
        if sec.auth_mode == "api_key":
            where = "query parameter" if sec.api_key_location == "query" else "header"
            lines += [
                f"Every upstream request sends an API key as the `{sec.api_key_name}` {where}.",
                "Set the key in `API_KEY` (never commit it). `API_KEY_LOCATION` and",
                "`API_KEY_NAME` default to the values chosen in the wizard.",
                "If `API_KEY` is missing, requests fail instead of going out unauthenticated.",
            ]
        elif sec.auth_mode == "oauth2":
            lines += [
                "Upstream requests use an OAuth2 **client credentials** access token.",
                f"The server requests it from `{sec.oauth_token_url}` (`OAUTH_TOKEN_URL`)",
                "with `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` (HTTP Basic), caches it,",
                "refreshes it shortly before it expires, and retries once on `401`.",
                "`OAUTH_SCOPES` (space-separated) and `OAUTH_AUDIENCE` are optional.",
                "Missing credentials or a non-https token URL fail closed.",
            ]
        else:
            lines += [
                "**No authentication.** Requests go to the upstream API without credentials.",
                "Use this only for development or public APIs.",
            ]
        return [*lines, ""]

    def _pyproject(self, pkg: str, ctx: GenerateContext) -> str:
        dist = pkg.replace("_", "-")
        deps = "".join(f"\n              {json.dumps(r)}," for r in _direct_requirements())
        return dedent(
            f"""\
            [build-system]
            requires = ["hatchling>=1.25,<2"]
            build-backend = "hatchling.build"

            [project]
            name = "{dist}"
            version = "0.1.0"
            description = {toml_str("Generated MCP server for " + ctx.ir.title)}
            requires-python = ">=3.11"
            dependencies = [{deps}
            ]

            [project.optional-dependencies]
            test = ["pytest>=8,<10"]

            [project.scripts]
            {dist} = "{pkg}.server:main"
            {dist}-verify = "{pkg}.verify:main"

            [tool.hatch.build.targets.wheel]
            packages = ["src/{pkg}"]

            [tool.pytest.ini_options]
            testpaths = ["tests"]
            pythonpath = ["src"]
            """
        )

    def _requirements(self) -> str:
        return "\n".join(_direct_requirements()) + "\n"

    def _lock(self) -> str:
        """The packaged hash lock, with a header for this project instead of the repo's."""
        lines = _lock_file("requirements.txt").splitlines()
        while lines and lines[0].startswith("#"):
            lines.pop(0)  # uv's header names a command that only exists in this repo
        header = [
            "# Hash-pinned dependencies, prepared by mcp-wizard-generators:",
            "# every package for every OS and CPython 3.11+, with sha256 hashes. Install with",
            "#   pip install --require-hashes -r requirements.lock",
            "#   pip install --no-deps -e .",
        ]
        return "\n".join(header) + "\n\n" + "\n".join(lines) + "\n"

    def _env_example(self, ctx: GenerateContext) -> str:
        base = ctx.ir.servers[0] if ctx.ir.servers else "https://api.example.com"
        lines = [
            f"API_BASE_URL={env_value(base)}",
            "# Auth mode from wizard security config: " + ctx.security.auth_mode,
        ]
        sec = ctx.security
        if sec.auth_mode == "api_key":
            lines.extend(
                [
                    "API_KEY=replace-me",
                    f"API_KEY_LOCATION={sec.api_key_location}",
                    f"API_KEY_NAME={env_value(sec.api_key_name)}",
                ]
            )
        elif sec.auth_mode == "oauth2":
            lines.extend(
                [
                    f"OAUTH_TOKEN_URL={env_value(sec.oauth_token_url)}",
                    "OAUTH_CLIENT_ID=",
                    "OAUTH_CLIENT_SECRET=",
                    f"OAUTH_SCOPES={env_value(' '.join(sec.oauth_scopes))}",
                    f"OAUTH_AUDIENCE={env_value(sec.oauth_audience)}",
                ]
            )
        else:
            lines.append("# No authentication (development only)")
        lines.append("# Largest response body the server reads (bytes):")
        lines.append("# MAX_RESPONSE_BYTES=1048576")
        lines.append("MCP_TRANSPORT=stdio")
        return "\n".join(lines) + "\n"

    def _cursor_mcp_json(self, pkg: str, ctx: GenerateContext) -> str:
        base = ctx.ir.servers[0] if ctx.ir.servers else "https://api.example.com"
        server_key = re.sub(r"[^a-zA-Z0-9_-]+", "-", pkg).strip("-") or "generated-mcp"
        env: dict[str, str] = {"API_BASE_URL": base}
        sec = ctx.security
        if sec.auth_mode == "api_key":
            env["API_KEY"] = "${API_KEY}"
            env["API_KEY_LOCATION"] = sec.api_key_location
            env["API_KEY_NAME"] = sec.api_key_name
        elif sec.auth_mode == "oauth2":
            env["OAUTH_TOKEN_URL"] = sec.oauth_token_url
            env["OAUTH_CLIENT_ID"] = "${OAUTH_CLIENT_ID}"
            env["OAUTH_CLIENT_SECRET"] = "${OAUTH_CLIENT_SECRET}"
            env["OAUTH_SCOPES"] = " ".join(sec.oauth_scopes)
        config = {
            "mcpServers": {
                server_key: {
                    "command": "python",
                    "args": ["-m", pkg],
                    "env": env,
                }
            }
        }
        return json.dumps(config, indent=2) + "\n"

    def _init(self, ctx: GenerateContext) -> str:
        return (
            f'"""Generated MCP server for {docstring_text(ctx.ir.title)}.\n\n'
            "Built with the official MCP Python SDK (MCPServer, mcp>=2).\n"
            '"""\n\n__version__ = "0.1.0"\n'
        )

    def _verify_module(self, pkg: str, ctx: GenerateContext, tools: list[ToolMapping]) -> str:
        expected = {
            "package": pkg,
            "title": ctx.ir.title,
            "version": ctx.ir.version,
            "transport": "stdio",
            "authMode": ctx.security.auth_mode,
            "tools": [
                {
                    "name": tool.tool_name,
                    "operationId": tool.operation_id,
                    "method": tool.method.upper(),
                    "path": tool.path,
                }
                for tool in tools
            ],
        }
        literal = json.dumps(json.dumps(expected))
        return (
            dedent(
                '''\
                """Report the tools this generated MCP server exposes.

                Run: python -m __PKG__.verify
                Prints UTF-8 JSON to stdout. Exit 0 when live tool names match the
                tools selected at generation time; exit 1 otherwise.
                Does not call the upstream API.
                """

                from __future__ import annotations

                import asyncio
                import json
                import sys
                from collections import Counter
                from pathlib import Path
                from typing import Any

                EXPECTED = json.loads(__EXPECTED__)


                def _find_manifest() -> Path | None:
                    """Find this package's generation-manifest.json, not an unrelated ancestor.

                    Searches the working directory and up to three parents, plus the
                    src-layout project root next to this file (``src/<pkg>/verify.py``).
                    A manifest is used only when its ``package`` matches, or when it has
                    no package field and sits in the working directory or that project root.
                    """
                    expected_pkg = EXPECTED["package"]
                    src_project = Path(__file__).resolve().parents[2]
                    cwd = Path.cwd().resolve()
                    directories: list[Path] = []
                    current = cwd
                    for _ in range(4):
                        directories.append(current)
                        if current.parent == current:
                            break
                        current = current.parent
                    if src_project not in directories:
                        directories.append(src_project)
                    local_dirs = {cwd, src_project}
                    for directory in directories:
                        path = directory / "generation-manifest.json"
                        if not path.is_file():
                            continue
                        try:
                            data = json.loads(path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError, TypeError):
                            if directory in local_dirs:
                                return path
                            continue
                        if not isinstance(data, dict):
                            continue
                        package = data.get("package")
                        if package == expected_pkg or (package is None and directory in local_dirs):
                            return path
                    return None


                def _manifest_tool_names(path: Path) -> list[str]:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    tools = data.get("tools") or []
                    names: list[str] = []
                    for item in tools:
                        if isinstance(item, dict) and item.get("name"):
                            names.append(str(item["name"]))
                    return names


                def build_report() -> dict[str, Any]:
                    from __PKG__.server import mcp

                    listed = asyncio.run(mcp.list_tools())
                    by_name = {item["name"]: item for item in EXPECTED["tools"]}
                    live: list[dict[str, Any]] = []
                    names: list[str] = []
                    for tool in listed:
                        name = str(tool.name)
                        names.append(name)
                        meta = by_name.get(name, {})
                        live.append(
                            {
                                "name": name,
                                "description": getattr(tool, "description", None) or "",
                                "operationId": meta.get("operationId"),
                                "method": meta.get("method"),
                                "path": meta.get("path"),
                            }
                        )
                    expected_names = [item["name"] for item in EXPECTED["tools"]]
                    live_counts = Counter(names)
                    expected_counts = Counter(expected_names)
                    missing: list[str] = []
                    for name, count in expected_counts.items():
                        deficit = count - live_counts.get(name, 0)
                        if deficit > 0:
                            missing.extend([name] * deficit)
                    unexpected: list[str] = []
                    for name, count in live_counts.items():
                        extra = count - expected_counts.get(name, 0)
                        if extra > 0:
                            unexpected.extend([name] * extra)
                    report: dict[str, Any] = {
                        "ok": not missing and not unexpected,
                        "package": EXPECTED["package"],
                        "title": EXPECTED["title"],
                        "version": EXPECTED["version"],
                        "transport": EXPECTED["transport"],
                        "serverType": type(mcp).__name__,
                        "authMode": EXPECTED["authMode"],
                        "toolCount": len(names),
                        "expectedToolCount": len(expected_names),
                        "missing": missing,
                        "unexpected": unexpected,
                        "tools": live,
                    }
                    manifest = _find_manifest()
                    if manifest is not None:
                        report["manifestPath"] = str(manifest)
                        try:
                            manifest_names = _manifest_tool_names(manifest)
                        except (OSError, json.JSONDecodeError, TypeError) as exc:
                            report["ok"] = False
                            report["error"] = f"generation-manifest.json: {exc}"
                            return report
                        report["manifestToolCount"] = len(manifest_names)
                        manifest_missing = [name for name in expected_names if name not in manifest_names]
                        manifest_extra = [name for name in manifest_names if name not in expected_names]
                        if manifest_missing or manifest_extra:
                            report["ok"] = False
                            report["manifestMismatch"] = {
                                "missingFromManifest": manifest_missing,
                                "extraInManifest": manifest_extra,
                            }
                    return report


                def _write_json(report: dict[str, Any]) -> None:
                    text = json.dumps(report, indent=2) + "\\n"
                    try:
                        sys.stdout.reconfigure(encoding="utf-8")
                    except (AttributeError, OSError):
                        pass
                    sys.stdout.write(text)


                _INSTALL = "Install the project first: pip install -r requirements.txt (or pip install -e .)"
                _REGENERATE = "server.py was changed; undo the change or regenerate the project in the wizard"


                def _fail(error: str, hint: str) -> int:
                    _write_json({"ok": False, "error": error, "hint": hint, "toolCount": 0})
                    return 1


                def main() -> int:
                    try:
                        report = build_report()
                    except ModuleNotFoundError as exc:
                        name = exc.name or "a module"
                        if name == "mcp" or name.startswith("mcp."):
                            hint = "This server needs mcp 2.x: pip install 'mcp>=2.0.0,<3'"
                        else:
                            hint = _INSTALL
                        return _fail(f"cannot import {name}", hint)
                    except ImportError as exc:
                        return _fail(f"import failed: {exc}", _INSTALL)
                    except SyntaxError as exc:
                        where = Path(exc.filename or "server.py").name
                        return _fail(f"{where} line {exc.lineno}: {exc.msg}", _REGENERATE)
                    except (TypeError, ValueError) as exc:
                        return _fail(f"a tool definition is invalid: {exc}", _REGENERATE)
                    _write_json(report)
                    return 0 if report["ok"] else 1


                if __name__ == "__main__":
                    raise SystemExit(main())
                '''
            )
            .replace("__PKG__", pkg)
            .replace("__EXPECTED__", literal)
        )

    def _main_module(self, pkg: str) -> str:
        return dedent(
            f"""\
            \"\"\"Allow `python -m {pkg}` to start the MCP stdio server.\"\"\"

            from {pkg}.server import main

            if __name__ == "__main__":
                main()
            """
        )

    def _http_client(self, ctx: GenerateContext) -> str:
        sec = ctx.security
        header = (
            client_template.HEADER.replace("__AUTH_MODE_JSON__", json.dumps(sec.auth_mode))
            .replace("__AUTH_MODE__", docstring_text(sec.auth_mode))
            .replace("__SECRETS_STRATEGY__", docstring_text(sec.secrets_strategy))
        )
        if sec.auth_mode != "oauth2":
            header = header.replace("import time\n", "", 1)  # only the token cache needs it
        return "\n".join([header, self._auth_block(ctx), client_template.CORE])

    def _auth_block(self, ctx: GenerateContext) -> str:
        sec = ctx.security
        if sec.auth_mode == "api_key":
            return dedent(
                f'''\

                API_KEY_LOCATION_DEFAULT = {json.dumps(sec.api_key_location)}
                API_KEY_NAME_DEFAULT = {json.dumps(sec.api_key_name)}
                _AUTH_HINT = "check API_KEY (and API_KEY_LOCATION / API_KEY_NAME)"


                def _api_key_settings() -> tuple[str, str, str]:
                    api_key = os.environ.get("API_KEY")
                    if not api_key:
                        raise ConfigError("API_KEY is not set (authMode=api_key)")
                    location = os.environ.get("API_KEY_LOCATION", API_KEY_LOCATION_DEFAULT)
                    if location not in ("header", "query"):
                        raise ConfigError("API_KEY_LOCATION must be 'header' or 'query'")
                    name = os.environ.get("API_KEY_NAME", API_KEY_NAME_DEFAULT)
                    return api_key, location, name


                def _check_auth() -> None:
                    _api_key_settings()


                def _auth_secrets() -> list[str]:
                    return [os.environ.get("API_KEY", "")]


                def _apply_auth(headers: dict[str, str], params: dict[str, Any]) -> None:
                    """Send API_KEY as a header or query parameter; fail closed when unset."""
                    api_key, location, name = _api_key_settings()
                    if location == "query":
                        params[name] = api_key
                    else:
                        headers[name] = api_key


                def _invalidate_auth() -> bool:
                    return False
                '''
            )
        if sec.auth_mode == "oauth2":
            return dedent(
                f'''\

                OAUTH_TOKEN_URL_DEFAULT = {json.dumps(sec.oauth_token_url)}
                OAUTH_SCOPES_DEFAULT = {json.dumps(" ".join(sec.oauth_scopes))}
                OAUTH_AUDIENCE_DEFAULT = {json.dumps(sec.oauth_audience)}
                _AUTH_HINT = "check OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET and OAUTH_SCOPES"
                # Refresh this many seconds before the token expires.
                _REFRESH_MARGIN_SECONDS = 60.0
                _token: dict[str, Any] = {{"value": None, "expires_at": 0.0}}


                def _required_env(name: str) -> str:
                    value = os.environ.get(name)
                    if not value:
                        raise ConfigError(f"{{name}} is not set (authMode=oauth2)")
                    return value


                def _token_url() -> str:
                    token_url = os.environ.get("OAUTH_TOKEN_URL") or OAUTH_TOKEN_URL_DEFAULT
                    if not token_url.lower().startswith("https://"):
                        raise ConfigError("OAUTH_TOKEN_URL must use https")
                    return token_url


                def _check_auth() -> None:
                    _token_url()
                    _required_env("OAUTH_CLIENT_ID")
                    _required_env("OAUTH_CLIENT_SECRET")


                def _auth_secrets() -> list[str]:
                    return [os.environ.get("OAUTH_CLIENT_SECRET", ""), str(_token["value"] or "")]


                def _fetch_token() -> str:
                    """OAuth2 client credentials grant (RFC 6749 section 4.4), HTTP Basic auth."""
                    token_url = _token_url()
                    client_id = _required_env("OAUTH_CLIENT_ID")
                    client_secret = _required_env("OAUTH_CLIENT_SECRET")
                    data = {{"grant_type": "client_credentials"}}
                    scopes = os.environ.get("OAUTH_SCOPES", OAUTH_SCOPES_DEFAULT).strip()
                    if scopes:
                        data["scope"] = scopes
                    audience = os.environ.get("OAUTH_AUDIENCE", OAUTH_AUDIENCE_DEFAULT).strip()
                    if audience:
                        data["audience"] = audience
                    client = _client()
                    try:
                        response = client.post(
                            token_url,
                            data=data,
                            auth=(client_id, client_secret),
                            headers={{"Accept": "application/json"}},
                        )
                    except httpx.HTTPError as exc:
                        raise ApiError(
                            f"could not reach the OAuth2 token URL ({{type(exc).__name__}}); "
                            "check OAUTH_TOKEN_URL and your network"
                        ) from None
                    finally:
                        client.cookies.clear()
                    if response.status_code >= 400:
                        detail = _detail(response.content)
                        raise ApiError(
                            f"OAuth2 token request failed: HTTP {{response.status_code}}, {{_AUTH_HINT}}."
                            + (f" Token endpoint said: {{detail}}" if detail else ""),
                            response.status_code,
                        )
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = None
                    access_token = payload.get("access_token") if isinstance(payload, dict) else None
                    if not access_token:
                        raise ApiError("OAuth2 token response has no access_token")
                    expires_in = float(payload.get("expires_in") or 300)
                    _token["value"] = access_token
                    _token["expires_at"] = time.monotonic() + expires_in - _REFRESH_MARGIN_SECONDS
                    return str(access_token)


                def _access_token() -> str:
                    if _token["value"] and time.monotonic() < _token["expires_at"]:
                        return str(_token["value"])
                    return _fetch_token()


                def _apply_auth(headers: dict[str, str], params: dict[str, Any]) -> None:
                    headers["Authorization"] = f"Bearer {{_access_token()}}"


                def _invalidate_auth() -> bool:
                    """Drop the cached token so the next request fetches a new one."""
                    _token["value"] = None
                    _token["expires_at"] = 0.0
                    return True
                '''
            )
        return dedent(
            '''\

            _AUTH_HINT = "this API needs credentials; regenerate with an authentication mode"


            def _check_auth() -> None:
                """authMode=none: nothing to check."""


            def _auth_secrets() -> list[str]:
                return []


            def _apply_auth(headers: dict[str, str], params: dict[str, Any]) -> None:
                """authMode=none: no credentials (development or public APIs only)."""


            def _invalidate_auth() -> bool:
                return False
            '''
        )

    def _server(self, pkg: str, ctx: GenerateContext, tools: list[ToolMapping]) -> str:
        taken_functions: set[str] = set()
        tool_fns = "\n\n\n".join(self._tool_function(t, taken_functions) for t in tools) or (
            "# No operations selected."
        )
        title_literal = py_str(ctx.ir.title)
        imports = self._server_imports(tool_fns)
        return (
            dedent(
                f'''\
                """MCP stdio server — official Python SDK MCPServer (mcp 2.x).

                Exits with a clear message when API_BASE_URL or the auth settings are wrong.
                """

                from __future__ import annotations

                {imports}

                from {pkg}.client import ConfigError, check_config, request

                mcp = MCPServer({title_literal})


                '''
            )
            + tool_fns
            + dedent(
                """


                def main() -> None:
                    try:
                        check_config()
                    except ConfigError as exc:
                        sys.stderr.write(f"Cannot start: {exc}\\n")
                        raise SystemExit(2) from None
                    mcp.run(transport="stdio")


                if __name__ == "__main__":
                    main()
                """
            )
        )

    @staticmethod
    def _server_imports(tool_fns: str) -> str:
        """Only the imports the tool functions use, so the project lints clean.

        A spec string that happens to contain one of these markers only adds an
        unused import; a used name is never left unimported.
        """
        stdlib = ["import re"] if "re.compile(" in tool_fns else []
        stdlib.append("import sys")
        typing_names = [n for n in ("Annotated", "Literal") if f"{n}[" in tool_fns]
        if typing_names:
            stdlib.append(f"from typing import {', '.join(typing_names)}")
        if "quote(" in tool_fns:
            stdlib.append("from urllib.parse import quote")
        third_party = ["from mcp.server.mcpserver import MCPServer"]
        pydantic_names = [n for n in ("Field", "JsonValue") if n in tool_fns]
        if pydantic_names:
            third_party.append(f"from pydantic import {', '.join(pydantic_names)}")
        # Joined with the template's 16-space indent so dedent() still lines up.
        return "\n                ".join([*stdlib, "", *third_party])

    def _tool_function(self, tool: ToolMapping, taken_functions: set[str]) -> str:
        """Emit one ``@mcp.tool`` function. Every spec value is escaped or sanitized."""
        schema, props, required_list = self._schema_parts(tool)
        required = set(required_list)
        path_params = set(re.findall(r"\{([^{}]+)\}", tool.path))
        py_names = self._param_names(props)
        func_name = py_ident(tool.tool_name, taken_functions, _RESERVED_MODULE_NAMES)

        doc = docstring_text(tool.description)
        all_found = analyze_operation(schema)
        found = visible(all_found)
        description = tool.description
        if found:
            # The calling model reads this; say it first (ADR-023).
            warning = "; ".join(f.short for f in found)
            description = f"WARNING: {warning} (see SECURITY-NOTES.md). {description}"

        formatted = [p for p in sorted(path_params) if p in props]
        method = tool.method.upper()
        uses_body = method in {"POST", "PUT", "PATCH"}

        body_lines = [f"    path_template = {py_str(self._path_template(tool.path, py_names))}"]
        if formatted:
            # Encode every path argument so a value like "../admin" can't change the route.
            format_map_items = ", ".join(
                f'{py_names[p]}=quote(str({py_names[p]}), safe="")' for p in formatted
            )
            body_lines.append(f"    path = path_template.format({format_map_items})")
        else:
            body_lines.append("    path = path_template")

        leftover = [n for n in props if n not in path_params]
        method_literal = py_str(method)

        def where(wire: str) -> str:
            # IR from OpenAPI says where each argument goes (ADR-023). Older IR JSON
            # without it keeps the previous rule: body for POST/PUT/PATCH, else query.
            prop = props[wire] if isinstance(props[wire], dict) else {}
            loc = prop.get(IN_KEY)
            if loc in ("query", "header", "cookie", "body"):
                return str(loc)
            return "body" if uses_body else "query"

        def annotation(wire: str) -> str:
            prop = props[wire] if isinstance(props[wire], dict) else {}
            location = "path" if wire in path_params else where(wire)
            return _annotation(prop, wire, location, whole_body=prop.get(WHOLE_BODY_KEY) is True)

        # Required first: a parameter without a default can't follow one with a default.
        ordered = [w for w in props if w in required] + [w for w in props if w not in required]
        # One argument per line: each carries its type, constraints and description.
        signature = "".join(
            f"\n    {py_names[w]}: {annotation(w)},"
            if w in required
            else f"\n    {py_names[w]}: {annotation(w)} | None = None,"
            for w in ordered
        )
        if signature:
            signature += "\n"

        groups: dict[str, list[str]] = {"query": [], "header": [], "cookie": [], "body": []}
        for wire in leftover:
            groups[where(wire)].append(wire)
        whole_body = [
            w
            for w in groups["body"]
            if isinstance(props[w], dict) and props[w].get(WHOLE_BODY_KEY) is True
        ]

        def kept(names: list[str], value: str = "v") -> str:
            items = ", ".join(f"{py_str(n)}: {py_names[n]}" for n in names)
            return f"{{k: {value} for k, v in {{{items}}}.items() if v is not None}}"

        # Values of sensitive arguments, kept out of error messages (all severities).
        secret_names = [py_names[f.parameter] for f in all_found if f.parameter in py_names]
        secrets_arg = f", secrets=({', '.join(secret_names)},)" if secret_names else ""

        used = [loc for loc, names in groups.items() if names]
        if not used:
            body_lines.append(f"    return request({method_literal}, path{secrets_arg})")
        elif used in (["query"], ["body"]) and not whole_body:
            # One destination: the compact form the earlier generator emitted.
            body_lines.append(f"    payload = {kept(groups[used[0]])}")
            arg = "params" if used == ["query"] else "json_body"
            body_lines.append(
                f"    return request({method_literal}, path, {arg}=payload or None{secrets_arg})"
            )
        else:
            call_args: list[str] = []
            if groups["query"]:
                body_lines.append(f"    query = {kept(groups['query'])}")
                call_args.append("params=query or None")
            if groups["header"]:
                body_lines.append(f"    headers = {kept(groups['header'], 'str(v)')}")
                call_args.append("headers=headers or None")
            if groups["cookie"]:
                body_lines.append(f"    cookies = {kept(groups['cookie'], 'str(v)')}")
                call_args.append("cookies=cookies or None")
            if whole_body:
                call_args.append(f"json_body={py_names[whole_body[0]]}")
            elif groups["body"]:
                body_lines.append(f"    payload = {kept(groups['body'])}")
                call_args.append("json_body=payload or None")
            body_lines.append(
                f"    return request({method_literal}, path, {', '.join(call_args)}{secrets_arg})"
            )

        body = "\n".join(body_lines)
        return (
            f"@mcp.tool(name={py_str(tool.tool_name)}, description={py_str(description)})\n"
            f"def {func_name}({signature}) -> JsonValue:\n"
            f'    """{doc}"""\n'
            f"{body}"
        )

    @staticmethod
    def _path_template(path: str, py_names: dict[str, str]) -> str:
        """Rewrite ``{wire}`` placeholders to ``{py}`` and escape every other brace.

        Without formatted parameters the raw path is used as-is (no ``.format`` call).
        """
        if not any(p in py_names for p in re.findall(r"\{([^{}]+)\}", path)):
            return path
        out: list[str] = []
        for part in re.split(r"(\{[^{}]+\})", path):
            name = part[1:-1] if part.startswith("{") and part.endswith("}") else None
            if name is not None and name in py_names:
                out.append("{" + py_names[name] + "}")
            else:
                out.append(part.replace("{", "{{").replace("}", "}}"))
        return "".join(out)
