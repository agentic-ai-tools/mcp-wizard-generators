"""Report the tools this generated MCP server exposes.

Run: python -m pets_tiny_mcp.verify
Prints UTF-8 JSON to stdout. Exit 0 when live tool names match the
tools selected at generation time; exit 1 otherwise.
Does not call the upstream API.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

EXPECTED = json.loads("{\"package\": \"pets_tiny_mcp\", \"title\": \"Pets Tiny API\", \"version\": \"1.0.0\", \"transport\": \"stdio\", \"authMode\": \"none\", \"tools\": [{\"name\": \"listpets\", \"operationId\": \"listPets\", \"method\": \"GET\", \"path\": \"/pets\"}, {\"name\": \"showpetbyid\", \"operationId\": \"showPetById\", \"method\": \"GET\", \"path\": \"/pets/{petId}\"}]}")


def _find_manifest() -> Path | None:
    """Return generation-manifest.json from cwd or this file, walking parents."""
    starts = [Path.cwd().resolve(), Path(__file__).resolve().parent]
    seen: set[Path] = set()
    for start in starts:
        current = start
        while current not in seen:
            seen.add(current)
            candidate = current / "generation-manifest.json"
            if candidate.is_file():
                return candidate
            if current.parent == current:
                break
            current = current.parent
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
    missing = [name for name in expected_names if name not in names]
    unexpected = [name for name in names if name not in expected_names]
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
    text = json.dumps(report, indent=2) + "\n"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    sys.stdout.write(text)


def main() -> int:
    try:
        report = build_report()
    except Exception as exc:
        _write_json({"ok": False, "error": str(exc), "toolCount": 0})
        return 1
    _write_json(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
