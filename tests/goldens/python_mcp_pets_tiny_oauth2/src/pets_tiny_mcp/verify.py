"""Report the tools this generated MCP server exposes.

Run: python -m pets_tiny_mcp.verify             (imports the server in-process)
     python -m pets_tiny_mcp.verify --url URL   (asks a running server over HTTP;
                                          bearer token from MCP_BEARER_TOKEN)
Prints UTF-8 JSON to stdout. Exit 0 when live tool names match the
tools selected at generation time; exit 1 otherwise.
Does not call the upstream API.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

EXPECTED = json.loads("{\"package\": \"pets_tiny_mcp\", \"title\": \"Pets Tiny API\", \"version\": \"1.0.0\", \"transport\": \"stdio\", \"authMode\": \"oauth2\", \"tools\": [{\"name\": \"listpets\", \"operationId\": \"listPets\", \"method\": \"GET\", \"path\": \"/pets\"}, {\"name\": \"showpetbyid\", \"operationId\": \"showPetById\", \"method\": \"GET\", \"path\": \"/pets/{petId}\"}]}")


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
    from pets_tiny_mcp.server import mcp

    listed = asyncio.run(mcp.list_tools())
    return _report(listed, EXPECTED["transport"], type(mcp).__name__)


def _report(listed: list[Any], transport: str, server_type: str) -> dict[str, Any]:
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
        "transport": transport,
        "serverType": server_type,
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
    text = json.dumps(report, indent=2) + "\n"
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


_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_STATUS_HINTS = {
    401: (
        "The server wants a valid bearer token: set MCP_BEARER_TOKEN to an access "
        "token from the identity provider the deployment trusts."
    ),
    403: (
        "The token was read but not accepted: check that its audience, client id and "
        "scopes match the values the deployment allows."
    ),
    404: "No MCP endpoint at this URL: check the path (the default is /mcp).",
}


def _check_url(url: str, token: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "--url must be an http or https URL with a host"
    if parts.username or parts.password:
        return "--url must not contain a user name or password; put a token in MCP_BEARER_TOKEN"
    if token and parts.scheme == "http" and parts.hostname not in _LOOPBACK:
        return f"refusing to send MCP_BEARER_TOKEN over plain http to {parts.hostname}; use https"
    return None


async def _remote_tools(url: str, token: str, statuses: list[int]) -> list[Any]:
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def record(response: Any) -> None:
        statuses.append(response.status_code)

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(
        headers=headers, timeout=httpx2.Timeout(30.0), event_hooks={"response": [record]}
    ) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return list((await session.list_tools()).tools)


def _leaves(exc: BaseException) -> list[BaseException]:
    nested = getattr(exc, "exceptions", None)
    if nested:
        return [leaf for sub in nested for leaf in _leaves(sub)]
    return [exc]


def _explain_remote(exc: BaseException, statuses: list[int]) -> tuple[str, str]:
    for status in reversed(statuses):
        if status in _STATUS_HINTS:
            return f"the server answered HTTP {status}", _STATUS_HINTS[status]
    leaves = _leaves(exc)
    names = {type(leaf).__name__ for leaf in leaves}
    if isinstance(exc, TimeoutError) or any("Timeout" in name for name in names):
        return "timed out", "The server did not answer in time: check the URL and that it has started."
    if "ConnectError" in names:
        leaf = next(leaf for leaf in leaves if type(leaf).__name__ == "ConnectError")
        return (
            f"cannot connect: {leaf}",
            "Check that the server is running and that the URL (host, port, path) and "
            "its TLS certificate are right.",
        )
    leaf = leaves[0]
    return (
        f"remote check failed: {type(leaf).__name__}: {leaf}",
        "Check that the URL is this server's MCP streamable-HTTP endpoint.",
    )


def _main_remote(url: str) -> int:
    token = os.environ.get("MCP_BEARER_TOKEN", "").strip()
    problem = _check_url(url, token)
    if problem:
        return _fail(problem, "Example: python -m pets_tiny_mcp.verify --url https://mcp.example.com/mcp")
    statuses: list[int] = []
    try:
        listed = asyncio.run(asyncio.wait_for(_remote_tools(url, token, statuses), 120))
    except ModuleNotFoundError as exc:
        return _fail(f"cannot import {exc.name or 'a module'}", _INSTALL)
    except Exception as exc:  # the MCP client wraps failures in exception groups
        error, hint = _explain_remote(exc, statuses)
        return _fail(error, hint)
    report = _report(listed, "streamable-http", "remote")
    report["url"] = url
    _write_json(report)
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pets_tiny_mcp.verify",
        description="Report the tools this MCP server exposes.",
    )
    parser.add_argument(
        "--url",
        help="check a running server over streamable HTTP (token from MCP_BEARER_TOKEN)",
    )
    args = parser.parse_args(argv)
    if args.url:
        return _main_remote(args.url)
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
