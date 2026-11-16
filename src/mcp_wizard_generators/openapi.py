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


def _param_schema(param: dict[str, Any]) -> dict[str, Any]:
    schema = param.get("schema")
    if isinstance(schema, dict):
        out = dict(schema)
    else:
        out = {}
        if param.get("type"):
            out["type"] = param["type"]
        if param.get("format"):
            out["format"] = param["format"]
    if param.get("description") and "description" not in out:
        out["description"] = param["description"]
    if "type" not in out:
        out["type"] = "string"
    return out


def _input_schema_for_operation(
    operation: dict[str, Any], path_item: dict[str, Any]
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []

    params: list[Any] = []
    for source in (path_item.get("parameters"), operation.get("parameters")):
        if isinstance(source, list):
            params.extend(source)

    for param in params:
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        if not isinstance(name, str) or not name:
            continue
        properties[name] = _param_schema(param)
        if param.get("required") or param.get("in") == "path":
            if name not in required:
                required.append(name)

    body = operation.get("requestBody")
    if isinstance(body, dict):
        content = body.get("content")
        if isinstance(content, dict):
            for media in ("application/json", "application/*+json"):
                media_obj = content.get(media)
                if isinstance(media_obj, dict) and isinstance(media_obj.get("schema"), dict):
                    schema = media_obj["schema"]
                    # Prefer object properties when present; otherwise wrap under "body".
                    if schema.get("type") == "object" or "properties" in schema:
                        for key, value in (schema.get("properties") or {}).items():
                            if isinstance(key, str) and key not in properties:
                                properties[key] = (
                                    dict(value) if isinstance(value, dict) else {"type": "string"}
                                )
                        for key in schema.get("required") or []:
                            if isinstance(key, str) and key not in required and key in properties:
                                required.append(key)
                    else:
                        properties.setdefault("body", dict(schema))
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
                        input_schema=_input_schema_for_operation(operation, path_item),
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
