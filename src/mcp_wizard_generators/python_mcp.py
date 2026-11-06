"""Stable library API for local Python MCP generation (mcp-wizard-api contract)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mcp_wizard_generators.models import IntermediateRep, SecurityConfig
from mcp_wizard_generators.packaging import pack_file_tree_to_zip
from mcp_wizard_generators.pipeline import GenerateResult, generate


def _operation_from_simple(op: dict[str, Any], index: int) -> dict[str, Any]:
    """Normalize a simple operation dict (method/path/operationId/summary/params)."""
    operation_id = op.get("operationId") or op.get("operation_id") or op.get("id")
    method = str(op.get("method") or "get").lower()
    path = str(op.get("path") or "/")
    op_id = str(operation_id or f"{method}_{path}_{index}")
    input_schema = op.get("inputSchema") or op.get("input_schema")
    if input_schema is None and "params" in op:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in op.get("params") or []:
            if isinstance(param, str):
                properties[param] = {"type": "string"}
                required.append(param)
                continue
            name = str(param.get("name") or param.get("id") or "")
            if not name:
                continue
            schema = dict(param.get("schema") or {})
            if "type" not in schema:
                schema["type"] = param.get("type") or "string"
            if param.get("description"):
                schema["description"] = param["description"]
            properties[name] = schema
            if param.get("required", False):
                required.append(name)
        input_schema = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required
    if input_schema is None:
        input_schema = {"type": "object", "properties": {}}
    return {
        "id": str(op.get("id") or op_id),
        "operationId": operation_id or op_id,
        "method": method,
        "path": path,
        "summary": op.get("summary"),
        "description": op.get("description"),
        "tags": list(op.get("tags") or []),
        "inputSchema": input_schema,
        "servers": list(op.get("servers") or []),
    }


def coerce_spec(
    spec: IntermediateRep | dict[str, Any] | None,
    operations: Sequence[str] | Sequence[dict[str, Any]] | None = None,
) -> tuple[IntermediateRep, list[str] | None]:
    """
    Accept IR JSON **or** a simple operations payload.

    Returns ``(ir, selected_operation_ids)``. When ``operations`` is a list of
    operation dicts, they become the IR operation set and selection is all of them
    (unless ``spec`` already carried a fuller catalog and ``operations`` are ids).
    """
    selected_ids: list[str] | None = None
    op_dicts: list[dict[str, Any]] | None = None

    if operations is not None:
        ops_list = list(operations)
        if ops_list and isinstance(ops_list[0], dict):
            op_dicts = [_operation_from_simple(op, i) for i, op in enumerate(ops_list)]  # type: ignore[arg-type]
            selected_ids = [str(op["id"]) for op in op_dicts]
        else:
            selected_ids = [str(x) for x in ops_list]

    if isinstance(spec, IntermediateRep):
        return spec, selected_ids

    data: dict[str, Any] = dict(spec or {})
    if op_dicts is not None:
        # Simple mode: operations argument supplies the catalog (merge over empty spec).
        if not data.get("operations"):
            data["operations"] = op_dicts
        else:
            # Spec has catalog; operations argument was dicts → treat as the selection set.
            data["operations"] = op_dicts
            selected_ids = [str(op["id"]) for op in op_dicts]

    if not data.get("title"):
        data["title"] = "Generated API"
    return IntermediateRep.from_dict(data), selected_ids


def generate_python_mcp(
    spec: IntermediateRep | dict[str, Any] | None,
    operations: Sequence[str] | Sequence[dict[str, Any]] | None = None,
    out_dir: str | Path | None = None,
    *,
    zip_path: str | Path | None = None,
    package_name: str = "generated_mcp",
    tool_name_overrides: dict[str, str] | None = None,
    auth_mode: str = "none",
    security: SecurityConfig | None = None,
) -> Path:
    """
    Generate a locally runnable Python MCP server from OpenAPI-derived operations.

    Stable contract for ``mcp-wizard-api`` workers:

    - ``spec``: IntermediateRep, IR JSON dict, or a minimal ``{title, servers, operations}``
    - ``operations``: selected operation ids **or** a list of simple operation dicts
      (``method``, ``path``, ``operationId``, ``summary``, ``params``)
    - ``out_dir``: write the project tree here (created if needed)
    - ``zip_path``: optional zip archive of the same tree

    Returns ``Path(out_dir)`` when ``out_dir`` is set; otherwise ``Path(zip_path)``.
    At least one of ``out_dir`` / ``zip_path`` is required.
    """
    if out_dir is None and zip_path is None:
        raise ValueError("generate_python_mcp requires out_dir and/or zip_path")

    ir, selected = coerce_spec(spec, operations)
    sec = security or SecurityConfig(auth_mode=auth_mode)
    result = generate(
        ir,
        target="local",
        language="python",
        selected_operation_ids=selected,
        tool_name_overrides=tool_name_overrides,
        security=sec,
        package_name=package_name,
        fail_on_rules_errors=False,  # Agent Rules not in this slice
    )

    out_path: Path | None = None
    if out_dir is not None:
        out_path = Path(out_dir)
        result.tree.write_to(out_path)
    if zip_path is not None:
        zp = Path(zip_path)
        zp.parent.mkdir(parents=True, exist_ok=True)
        pack_file_tree_to_zip(result.tree, zp)
        if out_path is None:
            out_path = zp
    assert out_path is not None
    return out_path


def generate_python_mcp_result(
    spec: IntermediateRep | dict[str, Any] | None,
    operations: Sequence[str] | Sequence[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> GenerateResult:
    """Same inputs as ``generate_python_mcp``, but return the in-memory ``GenerateResult``."""
    ir, selected = coerce_spec(spec, operations)
    auth_mode = kwargs.pop("auth_mode", "none")
    security = kwargs.pop("security", None) or SecurityConfig(auth_mode=auth_mode)
    package_name = kwargs.pop("package_name", "generated_mcp")
    tool_name_overrides = kwargs.pop("tool_name_overrides", None)
    if kwargs:
        raise TypeError(f"Unexpected kwargs: {sorted(kwargs)}")
    return generate(
        ir,
        target="local",
        language="python",
        selected_operation_ids=selected,
        tool_name_overrides=tool_name_overrides,
        security=security,
        package_name=package_name,
        fail_on_rules_errors=False,
    )
