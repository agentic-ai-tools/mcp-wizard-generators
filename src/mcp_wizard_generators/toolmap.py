"""IR operations → MCP tool name / description / JSON Schema input."""

from __future__ import annotations

import re

from mcp_wizard_generators.models import IntermediateRep, ToolMap, ToolMapping


def _slug_tool_name(operation_id: str | None, method: str, path: str) -> str:
    raw = operation_id or f"{method}_{path}"
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower()
    if not slug:
        slug = f"{method}_op"
    if slug[0].isdigit():
        slug = f"op_{slug}"
    return slug


def build_tool_map(
    ir: IntermediateRep,
    selected_operation_ids: list[str] | None = None,
    name_overrides: dict[str, str] | None = None,
) -> ToolMap:
    """
    Map selected IR ops to MCP tools.

    ``selected_operation_ids=None`` → all operations.
    An explicit empty list → no tools (does **not** mean all). Callers that want
    “all” must pass ``None``; ``generate_python_mcp`` raises on ``operations=[]``.
    """
    overrides = name_overrides or {}
    selected = None if selected_operation_ids is None else set(selected_operation_ids)
    tools: list[ToolMapping] = []

    for op in ir.operations:
        if selected is not None and op.id not in selected:
            continue
        tool_name = overrides.get(op.id) or _slug_tool_name(op.operation_id, op.method, op.path)
        description = op.summary or op.description or f"{op.method.upper()} {op.path}"
        schema = op.input_schema or {"type": "object", "properties": {}}
        tools.append(
            ToolMapping(
                operation_id=op.id,
                tool_name=tool_name,
                description=description,
                input_schema=schema,
                method=op.method,
                path=op.path,
            )
        )
    return ToolMap(tools=tools)
