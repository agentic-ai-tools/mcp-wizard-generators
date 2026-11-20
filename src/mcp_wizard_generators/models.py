"""Shared generation models: IR, tool map, file tree, context."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from mcp_wizard_generators.paths import safe_tree_path

AUTH_MODES = frozenset({"none", "api_key", "oauth2"})
SECRETS_STRATEGIES = frozenset({"dotenv_example_only", "env", "secret_manager"})
# RFC 9110 token characters: safe as an HTTP header name and as a query key.
_API_KEY_NAME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,128}")

# Deployment axis (design ADR-026). The server architecture is chosen separately.
DEPLOYMENT_TARGETS = ("local", "docker", "agentcore-container", "agentcore-code")
IAC_OPTIONS = frozenset({"none", "terraform"})
CI_OPTIONS = frozenset({"none", "github-actions"})
INBOUND_AUTH_MODES = frozenset({"none", "jwt"})
# AgentCore's JWT authorizer requires this suffix; every OIDC provider publishes it.
_DISCOVERY_URL = re.compile(r"https://[^\s/?#@]+(/[^\s?#]*)?/\.well-known/openid-configuration")
_AWS_REGION = re.compile(r"[a-z]{2}(-[a-z]+)+-\d")
# Audiences, client ids and scopes end up in Terraform strings and env files.
_AUTH_VALUE = re.compile(r"[A-Za-z0-9:/._~@+=,-]{1,256}")


INTERNAL_SCHEMA_PREFIX = "x-mcp-wizard-"


def public_schema(schema: Any) -> Any:
    """``schema`` without the generator's internal ``x-mcp-wizard-*`` markers."""
    if isinstance(schema, dict):
        return {
            k: public_schema(v)
            for k, v in schema.items()
            if not (isinstance(k, str) and k.startswith(INTERNAL_SCHEMA_PREFIX))
        }
    if isinstance(schema, list):
        return [public_schema(v) for v in schema]
    return schema


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

    def merge(self, other: FileTree, *, append: frozenset[str] = frozenset()) -> FileTree:
        """A new tree: this tree plus ``other``, keeping this tree's root name.

        A path present in both trees is an error, so a deployment can never silently
        overwrite server code. Paths listed in ``append`` are the exception: the other
        tree's content is appended (for example a README section).
        """
        entries = list(self.entries)
        index = {e.path: i for i, e in enumerate(entries)}
        for entry in other.entries:
            if entry.path not in index:
                index[entry.path] = len(entries)
                entries.append(entry)
                continue
            if entry.path not in append:
                raise ValueError(f"generated path collision: {entry.path!r} is emitted twice")
            mine = entries[index[entry.path]]
            joined = mine.content if mine.content.endswith("\n") else mine.content + "\n"
            entries[index[entry.path]] = FileEntry(
                entry.path, joined + entry.content, mine.executable or entry.executable
            )
        return FileTree(entries=entries, root_name=self.root_name)

    def write_to(self, dest_dir: str | Path) -> Path:
        root = Path(dest_dir)
        root.mkdir(parents=True, exist_ok=True)
        for entry in self.entries:
            rel = safe_tree_path(entry.path)
            # Join POSIX segments so Windows hosts never treat "a/b" as one name.
            target = root.joinpath(*PurePosixPath(rel).parts) if rel else root
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="": write the text as generated (LF) on every OS; Windows would
            # otherwise turn each newline into CRLF and break shell scripts.
            target.write_text(entry.content, encoding="utf-8", newline="")
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
                "mcpAuth is not supported here: MCP endpoint auth is configured on the "
                "deployment (design ADR-026), not in SecurityConfig"
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


@dataclass(frozen=True)
class InboundAuthConfig:
    """Auth on the generated MCP endpoint itself (design ADR-026).

    ``jwt`` means a bearer token from a configurable OIDC identity provider; no
    provider is hardcoded. ``create_cognito`` asks the deployment to create a
    Cognito user pool and use it as the provider. None of these values are secrets.
    """

    mode: str = "none"  # none | jwt
    discovery_url: str = ""
    allowed_audiences: tuple[str, ...] = ()
    allowed_clients: tuple[str, ...] = ()
    allowed_scopes: tuple[str, ...] = ()
    create_cognito: bool = False

    def __post_init__(self) -> None:
        for name in ("allowed_audiences", "allowed_clients", "allowed_scopes"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        self.validate()

    @property
    def configured(self) -> bool:
        return bool(
            self.discovery_url
            or self.allowed_audiences
            or self.allowed_clients
            or self.allowed_scopes
            or self.create_cognito
        )

    def validate(self) -> None:
        if self.mode not in INBOUND_AUTH_MODES:
            raise ValueError(
                f"Unsupported inbound auth mode {self.mode!r}; "
                f"expected one of {sorted(INBOUND_AUTH_MODES)}"
            )
        if self.mode == "none":
            if self.configured:
                raise ValueError(
                    "inbound auth settings were given but mode is 'none'; "
                    "set mode='jwt' or drop the settings"
                )
            return
        for name in ("allowed_audiences", "allowed_clients", "allowed_scopes"):
            for value in getattr(self, name):
                if not _AUTH_VALUE.fullmatch(value):
                    raise ValueError(f"{name} value {value!r} has unsupported characters")
        if self.create_cognito:
            if self.discovery_url:
                raise ValueError(
                    "create_cognito derives the discovery URL; do not also set discovery_url"
                )
            return
        if not _DISCOVERY_URL.fullmatch(self.discovery_url):
            raise ValueError(
                "inbound JWT discovery_url must be an https URL ending in "
                "/.well-known/openid-configuration"
            )
        if not (self.allowed_audiences or self.allowed_clients or self.allowed_scopes):
            raise ValueError(
                "inbound JWT auth needs at least one allowed audience, client or scope"
            )

    def manifest(self) -> dict[str, Any]:
        """Non-secret summary: the provider host only, not the full token settings."""
        data: dict[str, Any] = {"mode": self.mode}
        if self.mode == "jwt":
            data["identityProvider"] = (
                "cognito (created)"
                if self.create_cognito
                else urlsplit(self.discovery_url).hostname or ""
            )
        return data


@dataclass(frozen=True)
class DeploymentConfig:
    """Where and how the generated server is deployed (design ADR-026).

    Target-specific rules (for example which IaC a target supports) live on the
    deployment plugin; this class rejects only values that are invalid everywhere.
    """

    target: str = "local"
    iac: str = "none"
    ci: str = "none"
    scripts: bool = False
    aws_region: str = ""
    inbound_auth: InboundAuthConfig = field(default_factory=InboundAuthConfig)
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.target not in DEPLOYMENT_TARGETS:
            raise ValueError(
                f"Unsupported deployment target {self.target!r}; "
                f"expected one of {list(DEPLOYMENT_TARGETS)}"
            )
        if self.iac not in IAC_OPTIONS:
            raise ValueError(f"Unsupported iac {self.iac!r}; expected one of {sorted(IAC_OPTIONS)}")
        if self.ci not in CI_OPTIONS:
            raise ValueError(f"Unsupported ci {self.ci!r}; expected one of {sorted(CI_OPTIONS)}")
        if self.aws_region and not _AWS_REGION.fullmatch(self.aws_region):
            raise ValueError(f"aws_region {self.aws_region!r} is not an AWS region name")
        if not isinstance(self.inbound_auth, InboundAuthConfig):
            raise TypeError("inbound_auth must be an InboundAuthConfig")

    def manifest(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "target": self.target,
            "iac": self.iac,
            "ci": self.ci,
            "scripts": self.scripts,
            "inboundAuth": self.inbound_auth.manifest(),
        }
        if self.aws_region:
            data["awsRegion"] = self.aws_region
        return data


@dataclass(frozen=True)
class EnvVar:
    """One environment variable the generated server reads."""

    name: str
    description: str
    secret: bool = False


@dataclass(frozen=True)
class ServerCapabilities:
    """What a server plugin produced; deployment plugins read only this (ADR-026)."""

    architecture: str
    language: str
    package_name: str
    entrypoint: str  # "pkg.server:main"
    transports: frozenset[str]
    lock_files: tuple[str, ...] = ()
    env_vars: tuple[EnvVar, ...] = ()
    python_requires: str = ">=3.11"
    # Optional server features that were switched on (for example "jwt-auth").
    features: frozenset[str] = frozenset()

    @property
    def secret_env_vars(self) -> tuple[EnvVar, ...]:
        return tuple(v for v in self.env_vars if v.secret)


@dataclass
class GenerateContext:
    """Inputs for one server + deployment plugin invocation."""

    ir: IntermediateRep
    selected_operation_ids: list[str]
    tool_map: ToolMap
    architecture: str = "python-mcp"
    language: str = "python"
    deployment: DeploymentConfig = field(default_factory=DeploymentConfig)
    # Optional server features the deployment needs (for example "jwt-auth").
    server_features: frozenset[str] = frozenset()
    security: SecurityConfig = field(default_factory=SecurityConfig)
    agent_rules: AgentRulesBundle = field(default_factory=AgentRulesBundle)
    package_name: str = "generated_mcp"
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def target(self) -> str:
        """Deprecated: the deployment target key (kept for older plugins)."""
        return self.deployment.target
