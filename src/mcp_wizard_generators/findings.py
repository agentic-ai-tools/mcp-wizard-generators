"""Secret-exposure findings for OpenAPI operations (design ADR-023).

One check, shared by the Experience API (operation list: warning labels and
which operations start unticked) and the generators (manifest, SECURITY-NOTES.md,
tool descriptions), so the wizard and the zip always agree.

A parameter is *sensitive* when its name or its schema says it carries a secret.
Where it travels sets the severity: a URL (query or path) ends up in proxy, server
and client logs, browser history and error messages, so that is **medium**.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from mcp_wizard_generators.openapi import IN_KEY, openapi_to_ir

# Tokens that name a secret on their own (after splitting on case and punctuation).
_SECRET_TOKENS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "passphrase",
        "secret",
        "token",
        "credential",
        "credentials",
        "authorization",
        "ssn",
        # Run-together spellings.
        "apikey",
        "accesskey",
        "privatekey",
        "clientsecret",
        "sessionid",
        "accesstoken",
        "refreshtoken",
        "authtoken",
    }
)
# Adjacent token pairs that name a secret ("api key", "session id").
_SECRET_PAIRS = frozenset(
    {
        ("api", "key"),
        ("access", "key"),
        ("private", "key"),
        ("secret", "key"),
        ("session", "id"),
    }
)
# "token" next to one of these is pagination or metadata, not a credential.
_TOKEN_EXEMPT = frozenset(
    {
        "page",
        "next",
        "prev",
        "previous",
        "continuation",
        "cursor",
        "sync",
        "pagination",
        "type",
        "url",
        "uri",
        "endpoint",
        "count",
        "expires",
        "expiry",
        "expiration",
        "id",
    }
)

URL_REMEDIATION = (
    "Leave this operation unticked unless you need it. If you keep it, run the server only "
    "where request URLs are not logged, and ask the API provider for a login that takes "
    "credentials in a header or request body."
)

# location -> (code, severity, message template, remediation, short form for tool descriptions)
_RULES: dict[str, tuple[str, str, str, str, str]] = {
    "query": (
        "secret_in_query",
        "medium",
        "Sends {p} in the URL query string, where proxies, servers and logs can record it.",
        URL_REMEDIATION,
        "sends {p} in the URL query string",
    ),
    "path": (
        "secret_in_path",
        "medium",
        "Puts {p} in the URL path, where proxies, servers and logs can record it.",
        URL_REMEDIATION,
        "puts {p} in the URL path",
    ),
    "cookie": (
        "secret_in_cookie",
        "low",
        "Sends {p} in a cookie. Cookies are rarely logged, but proxies and browsers can keep them.",
        "Keep this operation only if you trust every proxy between the server and the API.",
        "sends {p} in a cookie",
    ),
    "header": (
        "secret_in_header",
        "info",
        "Sends {p} in a request header, the usual place for a credential.",
        "No action needed.",
        "sends {p} in a header",
    ),
    "body": (
        "secret_in_body",
        "info",
        "Sends {p} in the request body, which is not part of the URL.",
        "No action needed.",
        "sends {p} in the request body",
    ),
}

BLOCKING_SEVERITIES = frozenset({"medium", "high"})


@dataclass(frozen=True)
class Finding:
    """One finding, shaped like the Experience API ``OperationFinding``."""

    code: str
    severity: str
    parameter: str | None
    location: str | None
    message: str
    remediation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def short(self) -> str:
        """Lower-case clause for a tool description ("sends password in ...")."""
        rule = _RULES.get(self.location or "")
        return rule[4].format(p=self.parameter) if rule else self.message


def _tokens(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)  # "APIKey" -> "API Key"
    return [t.lower() for t in re.split(r"[^A-Za-z0-9]+", spaced) if t]


def is_sensitive(name: str, schema: dict[str, Any] | None = None) -> bool:
    """True when a parameter named ``name`` with ``schema`` likely carries a secret."""
    schema = schema if isinstance(schema, dict) else {}
    if schema.get("format") == "password" or schema.get("writeOnly") is True:
        return True
    tokens = _tokens(name)
    hits = {t for t in tokens if t in _SECRET_TOKENS}
    if hits == {"token"} and any(t in _TOKEN_EXEMPT for t in tokens):
        hits = set()
    if hits:
        return True
    return any(pair in _SECRET_PAIRS for pair in zip(tokens, tokens[1:], strict=False))


def analyze_operation(input_schema: dict[str, Any]) -> list[Finding]:
    """Findings for one operation's IR input schema, in parameter order (info included)."""
    props = input_schema.get("properties") if isinstance(input_schema, dict) else None
    if not isinstance(props, dict):
        return []
    findings: list[Finding] = []
    for name, prop in props.items():
        if not isinstance(name, str) or not isinstance(prop, dict):
            continue
        rule = _RULES.get(str(prop.get(IN_KEY)))
        if rule is None or not is_sensitive(name, prop):
            continue
        code, severity, message, remediation, _ = rule
        findings.append(
            Finding(code, severity, name, str(prop[IN_KEY]), message.format(p=name), remediation)
        )
    return findings


def is_blocking(findings: list[Finding]) -> bool:
    """True when any finding is medium or higher: the operation starts unticked."""
    return any(f.severity in BLOCKING_SEVERITIES for f in findings)


def visible(findings: list[Finding]) -> list[Finding]:
    """Findings worth showing a person (info-level ones are only for redaction)."""
    return [f for f in findings if f.severity != "info"]


def operation_findings(
    spec: dict[str, Any], *, include_info: bool = False
) -> dict[str, list[dict[str, Any]]]:
    """Findings per operation id (the API catalog ids), for an OpenAPI/Swagger document.

    Operations without findings are omitted. Never raises on a malformed document;
    the API validates documents before it gets here.
    """
    try:
        ir = openapi_to_ir(spec if isinstance(spec, dict) else {})
    except (TypeError, ValueError, AttributeError, KeyError):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for op in ir.operations:
        found = analyze_operation(op.input_schema)
        if not include_info:
            found = visible(found)
        if found:
            out[op.id] = [f.to_dict() for f in found]
    return out
