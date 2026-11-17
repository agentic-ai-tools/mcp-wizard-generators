"""Shared generation models: IR, tool map, file tree, context."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from mcp_wizard_generators.paths import safe_tree_path

AUTH_MODES = frozenset({"none", "api_key", "oauth2"})
SECRETS_STRATEGIES = frozenset({"dotenv_example_only", "env", "secret_manager"})
# RFC 9110 token characters: safe as an HTTP header name and as a query key.
_API_KEY_NAME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,128}")


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

    def __post_init__(self) -> None:
        # Paths can come from user input (agent rules); refuse escapes up front.
        safe_tree_path(self.path)


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
            rel = safe_tree_path(entry.path)
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
    """Upstream-API auth the generated MCP server applies (design ADR-017).

    ``from_wizard`` maps the Experience API ``SecurityConfig`` contract. Every
    supported mode is honored in generated code; anything else raises
    ``ValueError`` instead of silently downgrading to no auth. Secrets are
    never stored here — generated code reads them from env at runtime.
    """

    auth_mode: str = "none"
    secrets_strategy: str = "dotenv_example_only"
    api_key_location: str = "header"
    api_key_name: str = "X-API-Key"
    oauth_token_url: str = ""
    oauth_scopes: list[str] = field(default_factory=list)
    oauth_audience: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.auth_mode not in AUTH_MODES:
            raise ValueError(
                f"Unsupported authMode {self.auth_mode!r}; expected one of {sorted(AUTH_MODES)}"
            )
        if self.secrets_strategy not in SECRETS_STRATEGIES:
            raise ValueError(f"Unsupported secretsStrategy {self.secrets_strategy!r}")
        if self.auth_mode == "api_key":
            if self.api_key_location not in ("header", "query"):
                raise ValueError("apiKey.location must be 'header' or 'query'")
            if not _API_KEY_NAME.fullmatch(self.api_key_name or ""):
                raise ValueError(
                    f"apiKey.name {self.api_key_name!r} is not a valid header/query name"
                )
        if self.auth_mode == "oauth2":
            if not self.oauth_token_url:
                raise ValueError("oauth.tokenUrl is required for authMode=oauth2")
            if not self.oauth_token_url.lower().startswith("https://"):
                raise ValueError("oauth.tokenUrl must use https")

    @classmethod
    def from_wizard(cls, config: Mapping[str, Any]) -> SecurityConfig:
        """Build from the Experience API ``SecurityConfig`` JSON (camelCase)."""
        if config.get("mcpAuth"):
            raise ValueError(
                "mcpAuth is not supported: generated MCP servers are stdio-only in MVP-1"
            )
        auth_mode = str(config.get("authMode") or "none")
        api_key = config.get("apiKey") or {}
        oauth = config.get("oauth") or {}
        if auth_mode == "api_key" and not api_key:
            raise ValueError("apiKey (location, name) is required for authMode=api_key")
        if auth_mode == "oauth2" and not oauth.get("tokenUrl"):
            raise ValueError("oauth.tokenUrl is required for authMode=oauth2")
        return cls(
            auth_mode=auth_mode,
            secrets_strategy=str(config.get("secretsStrategy") or "dotenv_example_only"),
            api_key_location=str(api_key.get("location") or "header"),
            api_key_name=str(api_key.get("name") or "X-API-Key"),
            oauth_token_url=str(oauth.get("tokenUrl") or ""),
            oauth_scopes=[str(s) for s in (oauth.get("scopes") or [])],
            oauth_audience=str(oauth.get("audience") or ""),
        )

    def manifest(self) -> dict[str, Any]:
        """Non-secret security summary for generation-manifest.json."""
        data: dict[str, Any] = {
            "authMode": self.auth_mode,
            "secretsStrategy": self.secrets_strategy,
        }
        if self.auth_mode == "api_key":
            data["apiKey"] = {"location": self.api_key_location, "name": self.api_key_name}
        if self.auth_mode == "oauth2":
            data["oauth"] = {
                "tokenUrl": self.oauth_token_url,
                "scopes": list(self.oauth_scopes),
                "audience": self.oauth_audience,
            }
        return data


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
