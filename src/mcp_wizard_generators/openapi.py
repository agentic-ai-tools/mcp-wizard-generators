"""Convert OpenAPI 3 / Swagger 2 documents into IntermediateRep."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from mcp_wizard_generators.models import IntermediateRep, IrOperation

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def is_openapi_document(data: dict[str, Any]) -> bool:
    """True when ``data`` looks like an OpenAPI/Swagger document (not IR JSON)."""
    if not isinstance(data, dict):
        return False
    if "operations" in data and isinstance(data.get("operations"), list):
        # Prefer IR when both shapes somehow appear.
        return False
    return ("openapi" in data or "swagger" in data) and isinstance(data.get("paths"), dict)


_SERVER_VAR = re.compile(r"\{([^{}]+)\}")


def _http_source(source_url: str | None) -> str | None:
    """Return ``source_url`` only when it is an absolute http(s) URL."""
    if not source_url:
        return None
    parts = urlsplit(source_url)
    if parts.scheme.lower() in {"http", "https"} and parts.netloc:
        return source_url
    return None


def _substitute_variables(url: str, variables: Any) -> str:
    """Replace ``{name}`` with the server variable's ``default`` (OpenAPI 3)."""
    if not isinstance(variables, dict):
        return url

    def repl(match: re.Match[str]) -> str:
        var = variables.get(match.group(1))
        if isinstance(var, dict) and var.get("default") is not None:
            return str(var["default"])
        return match.group(0)

    return _SERVER_VAR.sub(repl, url)


def _resolve(url: str, source: str | None) -> str:
    """Resolve a relative server URL against the document's location."""
    if source is None or urlsplit(url).scheme:
        return url
    return urljoin(source, url)


def _server_urls(spec: dict[str, Any], source_url: str | None = None) -> list[str]:
    source = _http_source(source_url)
    servers = spec.get("servers")
    if isinstance(servers, list):
        urls: list[str] = []
        for item in servers:
            if isinstance(item, str) and item:
                urls.append(_resolve(item, source))
            elif isinstance(item, dict):
                url = item.get("url")
                if isinstance(url, str) and url:
                    url = _substitute_variables(url, item.get("variables"))
                    urls.append(_resolve(url, source))
        if urls:
            return urls
    # Swagger 2 host/basePath fallback
    base = spec.get("basePath") or ""
    if not isinstance(base, str):
        base = ""
    host = spec.get("host")
    if isinstance(host, str) and host:
        scheme = "https"
        schemes = spec.get("schemes")
        if isinstance(schemes, list) and schemes:
            scheme = str(schemes[0])
        return [f"{scheme}://{host}{base}"]
    if source is not None:
        if "swagger" in spec:
            # Swagger 2: no host means the host (and scheme) serving the document.
            parts = urlsplit(source)
            return [f"{parts.scheme}://{parts.netloc}{base}"]
        # OpenAPI 3: absent or empty servers means a single server at "/".
        return [urljoin(source, "/")]
    return []


# Where each tool argument goes on the wire (ADR-023). Internal: stripped by
# models.public_schema() before a schema is shown to a model.
IN_KEY = "x-mcp-wizard-in"
WHOLE_BODY_KEY = "x-mcp-wizard-whole-body"

# OpenAPI 3: header parameters with these names SHALL be ignored.
_IGNORED_HEADERS = frozenset({"accept", "content-type", "authorization"})
_MAX_REF_DEPTH = 32
_SWAGGER2_PARAM_KEYS = (
    "type",
    "format",
    "items",
    "enum",
    "default",
    "pattern",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
)


class _Refs:
    """Follow in-document ``$ref`` (``#/...``), cutting cycles and runaway depth."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec

    def _lookup(self, ref: str) -> Any:
        if not ref.startswith("#/"):
            return None  # external refs are rejected upstream (spec_external_ref)
        node: Any = self.spec
        for raw in ref[2:].split("/"):
            key = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and key in node:
                node = node[key]
            elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
                node = node[int(key)]
            else:
                return None
        return node

    def resolve(self, node: Any, stack: tuple[str, ...] = ()) -> Any:
        """A deep copy of ``node`` with every resolvable ``$ref`` inlined."""
        if len(stack) > _MAX_REF_DEPTH:
            return {"type": "object"}
        if isinstance(node, list):
            return [self.resolve(item, stack) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            siblings = {k: v for k, v in node.items() if k != "$ref"}
            if ref in stack:
                # Recursive schema: stop here with a plain object.
                return {"type": "object", **self.resolve(siblings, stack)}
            target = self._lookup(ref)
            resolved = self.resolve(target, (*stack, ref)) if isinstance(target, dict) else {}
            # OpenAPI 3.1 allows siblings next to $ref; they win over the target.
            return {**resolved, **self.resolve(siblings, stack)}
        return {k: self.resolve(v, stack) for k, v in node.items()}


def _param_schema(param: dict[str, Any]) -> dict[str, Any]:
    schema = param.get("schema")
    if isinstance(schema, dict):
        out = dict(schema)
    else:
        # Swagger 2 keeps type information on the parameter itself.
        out = {key: param[key] for key in _SWAGGER2_PARAM_KEYS if key in param}
    if param.get("description") and "description" not in out:
        out["description"] = param["description"]
    if "type" not in out and "enum" not in out and "items" not in out:
        out["type"] = "string"
    return out


def _location(param: dict[str, Any]) -> str:
    where = param.get("in")
    if where in ("query", "path", "header", "cookie"):
        return str(where)
    return "body"  # Swagger 2 "body" / "formData"


def _parameters(
    operation: dict[str, Any], path_item: dict[str, Any], refs: _Refs
) -> list[dict[str, Any]]:
    """Resolved parameters; operation-level ones override path-level (same name + in)."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for source in (path_item.get("parameters"), operation.get("parameters")):
        if not isinstance(source, list):
            continue
        for raw in source:
            param = refs.resolve(raw)
            if isinstance(param, dict) and isinstance(param.get("name"), str) and param["name"]:
                merged[(param["name"], str(param.get("in")))] = param
    return list(merged.values())


def _input_schema_for_operation(
    operation: dict[str, Any], path_item: dict[str, Any], refs: _Refs | None = None
) -> dict[str, Any]:
    refs = refs or _Refs({})
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in _parameters(operation, path_item, refs):
        name = param["name"]
        where = _location(param)
        if where == "header" and name.lower() in _IGNORED_HEADERS:
            continue
        if name in properties:
            continue
        if param.get("in") == "body":
            # Swagger 2 body parameter: the whole request body.
            prop = dict(param.get("schema") or {"type": "object"})
            prop[WHOLE_BODY_KEY] = True
        else:
            prop = _param_schema(param)
        prop[IN_KEY] = where
        properties[name] = prop
        if (param.get("required") or where == "path") and name not in required:
            required.append(name)

    body = refs.resolve(operation.get("requestBody"))
    if isinstance(body, dict):
        content = body.get("content")
        if isinstance(content, dict):
            for media in ("application/json", "application/*+json"):
                media_obj = content.get(media)
                if isinstance(media_obj, dict) and isinstance(media_obj.get("schema"), dict):
                    schema = media_obj["schema"]
                    # Prefer object properties when present; otherwise one whole-body argument.
                    if schema.get("type") == "object" or "properties" in schema:
                        for key, value in (schema.get("properties") or {}).items():
                            if isinstance(key, str) and key not in properties:
                                prop = (
                                    dict(value) if isinstance(value, dict) else {"type": "string"}
                                )
                                prop[IN_KEY] = "body"
                                properties[key] = prop
                        for key in schema.get("required") or []:
                            if isinstance(key, str) and key not in required and key in properties:
                                required.append(key)
                    elif "body" not in properties:
                        properties["body"] = {**schema, IN_KEY: "body", WHOLE_BODY_KEY: True}
                        if body.get("required") and "body" not in required:
                            required.append("body")
                    break

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def openapi_to_ir(spec: dict[str, Any], *, source_url: str | None = None) -> IntermediateRep:
    """
    Normalize an OpenAPI/Swagger document into IntermediateRep.

    Operation ``id`` matches mcp-wizard-api catalog ids: ``operationId`` when
    present, otherwise ``{method}:{path}``.

    ``source_url`` is the http(s) URL the document was fetched from. Relative
    server URLs (and a missing Swagger 2 ``host``) resolve against it, as the
    OpenAPI spec defines. Without it, relative URLs are kept as written.
    """
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    title = str(info.get("title") or "Generated API")
    version = str(info.get("version") or "0.0.0")
    spec_version = str(spec.get("openapi") or spec.get("swagger") or "3.0")
    servers = _server_urls(spec, source_url)
    refs = _Refs(spec)

    ops: list[IrOperation] = []
    paths = spec.get("paths") or {}
    if isinstance(paths, dict):
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method not in _HTTP_METHODS or not isinstance(operation, dict):
                    continue
                operation_id = operation.get("operationId")
                stable_id = str(operation_id) if operation_id else f"{method}:{path}"
                tags = operation.get("tags") or []
                ops.append(
                    IrOperation(
                        id=stable_id,
                        method=method,
                        path=str(path),
                        operation_id=str(operation_id) if operation_id else None,
                        summary=operation.get("summary"),
                        description=operation.get("description"),
                        tags=tuple(str(tag) for tag in tags),
                        input_schema=_input_schema_for_operation(operation, path_item, refs),
                        servers=tuple(servers),
                    )
                )

    return IntermediateRep(
        title=title,
        version=version,
        operations=ops,
        servers=servers,
        spec_version=spec_version,
    )
