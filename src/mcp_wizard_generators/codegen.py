"""Safe emission of untrusted spec values into generated source and config files.

OpenAPI documents are user-supplied. Never paste a spec value into generated code
with plain string formatting; route it through one of these helpers instead.
"""

from __future__ import annotations

import json
import keyword
import math
import re
from collections.abc import Iterable
from typing import Any

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})


def py_str(value: str) -> str:
    """A Python string literal for ``value`` (JSON string syntax is valid Python)."""
    return json.dumps(str(value))


def docstring_text(value: str) -> str:
    """Text safe to place between triple double quotes.

    Escapes backslashes and every double quote (so ``\"\"\"`` and a trailing quote
    cannot close the docstring), and escapes non-printable characters such as NUL.
    """
    out: list[str] = []
    for ch in str(value).replace("\r\n", "\n"):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch in "\n\t" or ch.isprintable():
            out.append(ch)
        elif ord(ch) <= 0xFF:
            out.append(f"\\x{ord(ch):02x}")
        elif ord(ch) <= 0xFFFF:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(f"\\U{ord(ch):08x}")
    return "".join(out)


def toml_str(value: str) -> str:
    """A TOML basic string for ``value``."""
    # ensure_ascii=False avoids surrogate-pair escapes, which TOML rejects;
    # JSON already escapes C0 controls, TOML also requires DEL to be escaped.
    return json.dumps(str(value), ensure_ascii=False).replace("\x7f", "\\u007f")


_CONTROL_RUN = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")


def one_line(value: str) -> str:
    """Collapse line breaks and control characters to single spaces (Markdown, .env)."""
    return _CONTROL_RUN.sub(" ", str(value)).strip()


def env_value(value: str) -> str:
    """A value that cannot start a new line in a dotenv file."""
    return one_line(value)


def py_ident(name: str, taken: set[str], reserved: Iterable[str] = ()) -> str:
    """Map ``name`` to a unique, safe Python identifier and record it in ``taken``.

    Non-identifier characters become ``_``; a leading digit gets a ``p_`` prefix;
    keywords and ``reserved`` names get a trailing ``_``; collisions get more ``_``.
    """
    ident = re.sub(r"\W", "_", str(name), flags=re.ASCII) or "param"
    if ident[0].isdigit():
        ident = f"p_{ident}"
    reserved_set = set(reserved)
    if keyword.iskeyword(ident) or ident in reserved_set:
        ident = f"{ident}_"
    while ident in taken or keyword.iskeyword(ident) or ident in reserved_set:
        ident = f"{ident}_"
    taken.add(ident)
    return ident


def py_literal(obj: Any, indent: int = 2, _level: int = 0) -> str:
    """A Python literal for JSON-like data, laid out like ``json.dumps(indent=2)``.

    Unlike JSON text it uses ``True`` / ``False`` / ``None``, so it is valid Python
    (``ast.literal_eval``-safe). Non-finite floats and unknown types become strings.
    """
    pad = " " * (indent * (_level + 1))
    end = " " * (indent * _level)
    if obj is None or isinstance(obj, bool):
        return repr(obj)
    if isinstance(obj, int):
        return repr(int(obj))
    if isinstance(obj, float):
        return repr(obj) if math.isfinite(obj) else py_str(repr(obj))
    if isinstance(obj, str):
        return py_str(obj)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = [
            f"{pad}{py_str(str(k))}: {py_literal(v, indent, _level + 1)}" for k, v in obj.items()
        ]
        return "{\n" + ",\n".join(items) + "\n" + end + "}"
    if isinstance(obj, list | tuple):
        if not obj:
            return "[]"
        items = [f"{pad}{py_literal(v, indent, _level + 1)}" for v in obj]
        return "[\n" + ",\n".join(items) + "\n" + end + "]"
    return py_str(str(obj))
