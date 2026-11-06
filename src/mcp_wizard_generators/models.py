"""Shared generation models: IR, tool map, file tree, context."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from mcp_wizard_generators.paths import normalize_tree_path


@dataclass(frozen=True)
class IrOperation:
    """One OpenAPI operation in the intermediate representation."""

    id: str
    method: str
    path: str
    operation_id: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    input_schema: dict[str, Any] = field(default_factory=dict)
    servers: tuple[str, ...] = ()


@dataclass
class IntermediateRep:
    """Normalized OpenAPI subset consumed by generators."""

    title: str
    version: str
    operations: list[IrOperation]
    servers: list[str] = field(default_factory=list)
    spec_version: str = "3.0"
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntermediateRep:
        ops = [
            IrOperation(
                id=str(op["id"]),
                method=str(op["method"]).lower(),
                path=str(op["path"]),
                operation_id=op.get("operationId") or op.get("operation_id"),
                summary=op.get("summary"),
                description=op.get("description"),
                tags=tuple(op.get("tags") or ()),
                input_schema=dict(op.get("inputSchema") or op.get("input_schema") or {}),
                servers=tuple(op.get("servers") or ()),
            )
            for op in data.get("operations") or []
        ]
        return cls(
            title=str(data.get("title") or "Generated API"),
            version=str(data.get("version") or "0.0.0"),
            operations=ops,
            servers=list(data.get("servers") or []),
            spec_version=str(data.get("specVersion") or data.get("spec_version") or "3.0"),
            extras=dict(data.get("extras") or {}),
        )


@dataclass(frozen=True)
class ToolMapping:
    """Maps one IR operation to an MCP tool name + JSON Schema input."""

    operation_id: str
    tool_name: str
    description: str
    input_schema: dict[str, Any]
    method: str
    path: str


@dataclass
class ToolMap:
    """Ordered MCP tool mappings derived from selected IR operations."""

    tools: list[ToolMapping] = field(default_factory=list)

    def by_name(self) -> dict[str, ToolMapping]:
        return {t.tool_name: t for t in self.tools}


@dataclass(frozen=True)
class FileEntry:
    """One generated file relative to the artifact root."""

    path: str
    content: str
    executable: bool = False


@dataclass
class FileTree:
    """In-memory generated project tree."""

    entries: list[FileEntry] = field(default_factory=list)
    root_name: str = "generated-mcp"

    def paths(self) -> list[str]:
        return sorted(e.path for e in self.entries)

    def get(self, path: str) -> FileEntry | None:
        for entry in self.entries:
            if entry.path == path:
                return entry
        return None

    def write_to(self, dest_dir: str | Path) -> Path:
        root = Path(dest_dir)
        root.mkdir(parents=True, exist_ok=True)
        for entry in self.entries:
            rel = normalize_tree_path(entry.path)
            # Join POSIX segments so Windows hosts never treat "a/b" as one name.
            target = root.joinpath(*PurePosixPath(rel).parts) if rel else root
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry.content, encoding="utf-8")
            if entry.executable and os.name != "nt":
                try:
                    target.chmod(target.stat().st_mode | 0o111)
                except OSError:
                    # Some filesystems (and sandboxes) reject mode bits; content is enough.
                    pass
        return root


@dataclass
class SecurityConfig:
    """Subset of session security options relevant to codegen."""

    auth_mode: str = "none"
    secrets_strategy: str = "dotenv_example_only"
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRulesBundle:
    """User-supplied Cursor / Anthropic rules pack (originals + unified view)."""

    format_family: str = "unknown"  # cursor | anthropic | mixed | unknown
    originals: dict[str, str] = field(default_factory=dict)  # path -> text
    unified_constraints: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GenerateContext:
    """Frozen inputs for one generator plugin invocation."""

    ir: IntermediateRep
    selected_operation_ids: list[str]
    tool_map: ToolMap
    target: str = "agentcore"
    language: str = "python"
    security: SecurityConfig = field(default_factory=SecurityConfig)
    agent_rules: AgentRulesBundle = field(default_factory=AgentRulesBundle)
    package_name: str = "generated_mcp"
    extras: dict[str, Any] = field(default_factory=dict)
