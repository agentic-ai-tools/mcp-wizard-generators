"""JSON Schema -> precise Python annotations for generated MCP tools (ADR-023).

Each argument becomes ``Annotated[<type>, Field(...)]``: the MCP SDK turns that
into the tool's ``inputSchema`` (types, enums, constraints, description) and
validates every call against it before the tool body runs.

Every value taken from the spec is emitted through ``py_str`` or as a checked
number, so a hostile spec cannot inject code.
"""

from __future__ import annotations

import math
import re
from typing import Any

from mcp_wizard_generators.codegen import one_line, py_str

_SCALARS = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}

# Where an argument goes -> how its description names it.
_LABELS = {
    "path": "path parameter `{}`",
    "query": "query parameter `{}`",
    "header": "header `{}`",
    "cookie": "cookie `{}`",
    "body": "request body field `{}`",
}


def _schema_type(schema: dict[str, Any]) -> str | None:
    """The JSON type; for 3.1 ``["string", "null"]`` the first non-null one."""
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), None)
    return kind if isinstance(kind, str) else None


def _literal(values: Any) -> str | None:
    """``Literal[...]`` for an enum of strings / ints / bools, else None."""
    if not isinstance(values, list):
        return None
    out: list[str] = []
    for value in values:
        if value is None:
            continue  # optional arguments already accept None
        if isinstance(value, bool):
            text = repr(value)
        elif isinstance(value, int):
            text = str(value)
        elif isinstance(value, str):
            text = py_str(value)
        else:
            return None
        if text not in out:
            out.append(text)
    return f"Literal[{', '.join(out)}]" if out else None


def _has_enum(schema: dict[str, Any]) -> bool:
    """An enum with a real value; ``[null]`` alone only means "optional"."""
    values = schema.get("enum")
    return isinstance(values, list) and any(v is not None for v in values)


def py_type(schema: Any) -> str:
    """The bare Python type for a JSON schema (no constraints)."""
    if not isinstance(schema, dict):
        return "JsonValue"
    literal = _literal(schema.get("enum"))
    if literal:
        return literal
    if _has_enum(schema):
        return "JsonValue"  # values a Literal can't hold
    kind = _schema_type(schema)
    if kind in _SCALARS:
        return _SCALARS[kind]
    if kind == "array":
        return f"list[{py_type(schema.get('items'))}]"
    if kind == "object":
        return "dict[str, JsonValue]"
    return "JsonValue"


def _number(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return repr(value)


def _pattern(value: Any) -> str | None:
    """A compiled-pattern expression, or None when Python can't compile it.

    Compiled patterns make pydantic use Python ``re``; its default engine
    rejects lookarounds and would stop the server from importing.
    """
    if not isinstance(value, str):
        return None
    try:
        re.compile(value)
    except (re.error, OverflowError, RecursionError):
        return None
    return f"re.compile({py_str(value)})"


def _constraints(schema: dict[str, Any], *, in_path: bool) -> list[str]:
    kind = _schema_type(schema)
    out: list[str] = []
    if _has_enum(schema):
        return out  # the Literal (or JsonValue) says it all
    if kind in ("integer", "number"):
        low, high = schema.get("minimum"), schema.get("maximum")
        ex_low, ex_high = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        # OpenAPI 3.0: boolean flags on minimum/maximum. 3.1: the bound itself.
        if ex_low is True:
            ex_low, low = low, None
        if ex_high is True:
            ex_high, high = high, None
        for key, value in (("ge", low), ("gt", ex_low), ("le", high), ("lt", ex_high)):
            text = _number(value)
            if text is not None:
                out.append(f"{key}={text}")
        step = _number(schema.get("multipleOf"))
        if step is not None and float(step) > 0:
            out.append(f"multiple_of={step}")
    elif kind == "string":
        min_length = schema.get("minLength")
        if in_path:
            # An empty path segment would change the route.
            min_length = max(min_length, 1) if _int(min_length) is not None else 1
        for key, value in (("min_length", min_length), ("max_length", schema.get("maxLength"))):
            if _int(value) is not None:
                out.append(f"{key}={value}")
        pattern = _pattern(schema.get("pattern"))
        if pattern:
            out.append(f"pattern={pattern}")
    elif kind == "array":
        for key, value in (
            ("min_length", schema.get("minItems")),
            ("max_length", schema.get("maxItems")),
        ):
            if _int(value) is not None:
                out.append(f"{key}={value}")
    return out


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def describe(schema: dict[str, Any], wire: str, location: str, *, whole_body: bool) -> str:
    """The spec's description plus where the value goes, e.g. "... (query parameter `x`)"."""
    label = "whole request body" if whole_body else _LABELS[location].format(wire)
    text = one_line(schema.get("description")) if isinstance(schema.get("description"), str) else ""
    return f"{text} ({label})" if text else label[0].upper() + label[1:]


def annotation(schema: Any, wire: str, location: str, *, whole_body: bool = False) -> str:
    """``Annotated[<type>, Field(<constraints>, description=...)]`` for one argument."""
    schema = schema if isinstance(schema, dict) else {}
    args = _constraints(schema, in_path=location == "path")
    args.append(f"description={py_str(describe(schema, wire, location, whole_body=whole_body))}")
    return f"Annotated[{py_type(schema)}, Field({', '.join(args)})]"
