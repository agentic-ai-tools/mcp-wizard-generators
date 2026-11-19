"""Edge cases for parameter locations, $ref resolution and argument routing (ADR-023)."""

from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from mcp_wizard_generators.openapi import IN_KEY, WHOLE_BODY_KEY, openapi_to_ir
from mcp_wizard_generators.python_mcp import generate_python_mcp

WHOLE = WHOLE_BODY_KEY


def _doc(paths: dict, **extra: Any) -> dict:
    return {"openapi": "3.0.3", "info": {"title": "T", "version": "1"}, "paths": paths, **extra}


def _props(spec: dict, op_id: str) -> dict:
    ops = {op.id: op for op in openapi_to_ir(spec).operations}
    return ops[op_id].input_schema["properties"]


# --------------------------------------------------------------------- $ref resolution


def test_external_ref_keeps_sibling_keys_only() -> None:
    spec = _doc(
        {
            "/x": {
                "get": {
                    "operationId": "x",
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "schema": {"$ref": "other.yaml#/Q", "description": "from siblings"},
                        }
                    ],
                }
            }
        }
    )
    assert _props(spec, "x")["q"] == {
        "description": "from siblings",
        "type": "string",
        IN_KEY: "query",
    }


def test_ref_into_a_list_by_index() -> None:
    spec = _doc(
        {
            "/x": {
                "get": {
                    "operationId": "x",
                    "parameters": [
                        {"name": "n", "in": "query", "schema": {"$ref": "#/x-shared/1"}},
                        {"name": "m", "in": "query", "schema": {"$ref": "#/x-shared/9"}},
                    ],
                }
            }
        },
        **{"x-shared": [{"type": "string"}, {"type": "integer", "minimum": 1}]},
    )
    props = _props(spec, "x")
    assert props["n"] == {"type": "integer", "minimum": 1, IN_KEY: "query"}
    # Out of range: unresolvable, falls back to a string.
    assert props["m"]["type"] == "string"


def test_json_pointer_escapes() -> None:
    spec = _doc(
        {
            "/x": {
                "get": {
                    "operationId": "x",
                    "parameters": [
                        {"name": "a", "in": "query", "schema": {"$ref": "#/x-s/a~1b~0c"}}
                    ],
                }
            }
        },
        **{"x-s": {"a/b~c": {"type": "boolean"}}},
    )
    assert _props(spec, "x")["a"]["type"] == "boolean"


def test_deep_ref_chains_are_capped() -> None:
    schemas = {
        f"S{i}": {
            "type": "object",
            "properties": {"next": {"$ref": f"#/components/schemas/S{i + 1}"}},
        }
        for i in range(60)
    }
    schemas["S60"] = {"type": "string"}
    spec = _doc(
        {
            "/x": {
                "post": {
                    "operationId": "x",
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/S0"}}
                        }
                    },
                }
            }
        },
        components={"schemas": schemas},
    )
    node = _props(spec, "x")["next"]
    depth = 1
    while "properties" in node:
        node = node["properties"]["next"]
        depth += 1
    # Cut off well before S60, with a plain object rather than a crash.
    assert node == {"type": "object"}
    assert depth < 60


# --------------------------------------------------------------------- parameters


def test_swagger2_body_and_form_parameters() -> None:
    spec = {
        "swagger": "2.0",
        "info": {"title": "T", "version": "1"},
        "definitions": {"Pet": {"type": "object", "properties": {"name": {"type": "string"}}}},
        "paths": {
            "/pets": {
                "post": {
                    "operationId": "addPet",
                    "parameters": [
                        {
                            "name": "pet",
                            "in": "body",
                            "required": True,
                            "schema": {"$ref": "#/definitions/Pet"},
                        },
                        {"name": "note", "in": "formData", "type": "string", "maxLength": 20},
                    ],
                }
            }
        },
    }
    props = _props(spec, "addPet")
    assert props["pet"][IN_KEY] == "body"
    assert props["pet"][WHOLE] is True
    assert props["pet"]["properties"] == {"name": {"type": "string"}}
    assert props["note"] == {"type": "string", "maxLength": 20, IN_KEY: "body"}


def test_swagger2_body_parameter_without_schema() -> None:
    spec = {
        "swagger": "2.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/raw": {"post": {"operationId": "raw", "parameters": [{"name": "data", "in": "body"}]}}
        },
    }
    assert _props(spec, "raw")["data"] == {"type": "object", WHOLE: True, IN_KEY: "body"}


def test_same_name_in_two_locations_keeps_the_first() -> None:
    spec = _doc(
        {
            "/x": {
                "get": {
                    "operationId": "x",
                    "parameters": [
                        {"name": "id", "in": "query", "schema": {"type": "string"}},
                        {"name": "id", "in": "header", "schema": {"type": "string"}},
                    ],
                }
            }
        }
    )
    assert _props(spec, "x")["id"][IN_KEY] == "query"


def test_operation_parameter_overrides_path_parameter() -> None:
    spec = _doc(
        {
            "/x": {
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "string"}}],
                "get": {
                    "operationId": "x",
                    "parameters": [
                        {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                        "not a parameter",
                        {"in": "query"},
                    ],
                },
            }
        }
    )
    assert _props(spec, "x") == {"limit": {"type": "integer", IN_KEY: "query"}}


def test_request_body_without_a_content_map() -> None:
    spec = _doc({"/x": {"post": {"operationId": "x", "requestBody": {"content": "nope"}}}})
    assert _props(spec, "x") == {}


def test_whole_body_does_not_collide_with_a_body_parameter() -> None:
    spec = _doc(
        {
            "/x": {
                "put": {
                    "operationId": "x",
                    "parameters": [{"name": "body", "in": "query", "schema": {"type": "string"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "array", "items": {"type": "integer"}}
                            }
                        },
                    },
                }
            }
        }
    )
    ops = {op.id: op for op in openapi_to_ir(spec).operations}
    schema = ops["x"].input_schema
    assert schema["properties"]["body"][IN_KEY] == "query"
    assert schema["properties"]["request_body"][WHOLE] is True
    assert schema["properties"]["request_body"]["items"] == {"type": "integer"}
    assert "request_body" in schema["required"]


# --------------------------------------------------------------------- routing at runtime

ROUTED = _doc(
    {
        "/things/{id}": {
            "post": {
                "operationId": "makeThing",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "dry_run", "in": "query", "schema": {"type": "boolean"}},
                    {"name": "X-Trace", "in": "header", "schema": {"type": "string"}},
                    {"name": "sess", "in": "cookie", "schema": {"type": "string"}},
                ],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {"name": {"type": "string"}},
                            }
                        }
                    }
                },
            }
        },
        "/tags": {
            "put": {
                "operationId": "putTags",
                "parameters": [{"name": "dry_run", "in": "query", "schema": {"type": "boolean"}}],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"type": "array", "items": {"type": "string"}}
                        }
                    }
                },
            }
        },
    }
)
PKG = "routed_mcp"


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, list[dict]]]:
    """Import the generated server with ``request`` replaced by a recorder."""
    generate_python_mcp(ROUTED, out_dir=tmp_path, package_name=PKG)
    monkeypatch.syspath_prepend(str(tmp_path / "src"))
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]
    module = importlib.import_module(f"{PKG}.server")
    calls: list[dict] = []

    def record(method: str, path: str, **kwargs: Any) -> dict:
        calls.append({"method": method, "path": path, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(module, "request", record)
    yield module, calls
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]


def _call(fn: Callable[..., Any], **by_wire: Any) -> Any:
    """Call a generated tool using wire names; Python names may be sanitized."""
    params = list(inspect.signature(fn).parameters)
    lookup = {p.lower().replace("_", ""): p for p in params}
    kwargs = {lookup[w.lower().replace("-", "").replace("_", "")]: v for w, v in by_wire.items()}
    return fn(**kwargs)


def test_every_location_reaches_its_slot(server) -> None:
    module, calls = server
    _call(
        module.makething,
        id="a/b",
        dry_run=True,
        **{"X-Trace": 42},
        sess="s1",
        name="rex",
    )
    assert calls == [
        {
            "method": "POST",
            "path": "/things/a%2Fb",
            "params": {"dry_run": True},
            "headers": {"X-Trace": "42"},
            "cookies": {"sess": "s1"},
            "json_body": {"name": "rex"},
        }
    ]


def test_unset_optional_arguments_are_left_out(server) -> None:
    module, calls = server
    _call(module.makething, id="1", name="rex")
    assert calls[0]["params"] is None
    assert calls[0]["headers"] is None
    assert calls[0]["cookies"] is None
    assert calls[0]["json_body"] == {"name": "rex"}


def test_whole_body_goes_as_is_next_to_query(server) -> None:
    module, calls = server
    _call(module.puttags, body=["a", "b"], dry_run=False)
    assert calls == [
        {"method": "PUT", "path": "/tags", "params": {"dry_run": False}, "json_body": ["a", "b"]}
    ]
