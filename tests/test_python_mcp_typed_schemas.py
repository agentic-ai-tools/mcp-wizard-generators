"""Strict, typed tool schemas in generated python-mcp servers (ADR-023).

Each tool argument carries a precise annotation (``list[str]``, ``Literal`` enums,
``int`` IDs), a description, and the spec's constraints, so the MCP SDK both
advertises them in ``inputSchema`` and rejects bad calls before any HTTP request.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from mcp_wizard_generators.python_mcp import generate_python_mcp, generate_python_mcp_result

FIXTURES = Path(__file__).parent / "fixtures"
PETSTORE = json.loads((FIXTURES / "petstore3.openapi.json").read_text(encoding="utf-8"))


def _doc(paths: dict) -> dict:
    return {"openapi": "3.1.0", "info": {"title": "T", "version": "1"}, "paths": paths}


def _query_op(op_id: str, *params: dict, method: str = "get", path: str = "/x") -> dict:
    return {path: {method: {"operationId": op_id, "parameters": list(params)}}}


def _q(name: str, schema: dict, **extra: Any) -> dict:
    return {"name": name, "in": "query", "schema": schema, **extra}


CONSTRAINED = _doc(
    {
        **_query_op(
            "search",
            _q("limit", {"type": "integer", "minimum": 1, "maximum": 100}, required=True),
            _q("ratio", {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}),
            _q("step", {"type": "integer", "multipleOf": 5}),
            _q("code", {"type": "string", "minLength": 2, "maxLength": 4}),
            _q("slug", {"type": "string", "pattern": "^(?!admin)[a-z-]+$"}),
            _q("bad_pattern", {"type": "string", "pattern": "[unclosed"}),
            _q(
                "ids", {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 3}
            ),
            _q("kinds", {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}),
            _q("level", {"type": "integer", "enum": [1, 2, 3]}),
            _q("mixed", {"enum": ["a", {"x": 1}]}),
            _q("nullable", {"type": ["string", "null"]}),
            _q("weird", {"type": "integer", "minimum": True, "maximum": float("inf")}),
            _q("filter", {"type": "object"}),
            _q("anything", {"type": "mystery"}),
            _q("Literal", {"type": "string", "description": "Shadows typing.Literal."}),
        ),
        **_query_op(
            "legacy",
            _q("n", {"type": "integer", "minimum": 0, "exclusiveMinimum": True}),
            path="/legacy",
        ),
        "/items/{item_id}": {
            "put": {
                "operationId": "putItem",
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
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


def _server_source(spec: dict, operations: list[str] | None = None) -> str:
    result = generate_python_mcp_result(spec, operations, package_name="typed_mcp")
    return {e.path: e.content for e in result.tree.entries}["src/typed_mcp/server.py"]


@pytest.fixture
def load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Generate a server, import it with ``request`` replaced by a recorder."""
    loaded: list[str] = []

    def _load(spec: dict, package: str) -> tuple[Any, list[dict]]:
        out = tmp_path / package
        generate_python_mcp(spec, out_dir=out, package_name=package)
        monkeypatch.syspath_prepend(str(out / "src"))
        module = importlib.import_module(f"{package}.server")
        loaded.append(package)
        calls: list[dict] = []

        def record(method: str, path: str, **kwargs: Any) -> dict:
            calls.append({"method": method, "path": path, **kwargs})
            return {"ok": True, "items": [1, "two", None]}

        monkeypatch.setattr(module, "request", record)
        return module, calls

    yield _load
    for package in loaded:
        for name in [m for m in sys.modules if m == package or m.startswith(package + ".")]:
            del sys.modules[name]


def _schemas(module: Any) -> dict[str, dict]:
    tools = asyncio.run(module.mcp.list_tools())
    return {t.name: t for t in tools}


def _prop(schema: dict, name: str) -> dict:
    """The non-null branch of an optional property."""
    prop = schema["properties"][name]
    for branch in prop.get("anyOf", []):
        if branch.get("type") != "null":
            return {**{k: v for k, v in prop.items() if k != "anyOf"}, **branch}
    return prop


def _call(module: Any, tool: str, args: dict) -> Any:
    return asyncio.run(module.mcp.call_tool(tool, args))


# --------------------------------------------------------------------- petstore


def test_petstore_annotations_are_precise() -> None:
    src = _server_source(
        PETSTORE, ["findPetsByStatus", "findPetsByTags", "getPetById", "getUserByName"]
    )
    assert 'status: Annotated[Literal["available", "pending", "sold"], Field(' in src
    assert "tags: Annotated[list[str], Field(" in src
    assert "petId: Annotated[int, Field(" in src
    assert "username: Annotated[str, Field(min_length=1, " in src
    assert ") -> JsonValue:" in src
    assert "-> Any:" not in src


def test_petstore_schema_is_described_and_validated(load) -> None:
    module, calls = load(PETSTORE, "petstore_typed_mcp")
    tools = _schemas(module)

    status = _prop(tools["findpetsbystatus"].input_schema, "status")
    assert status["enum"] == ["available", "pending", "sold"]
    assert (
        status["description"]
        == "Status values that need to be considered for filter (query parameter `status`)"
    )

    tags = _prop(tools["findpetsbytags"].input_schema, "tags")
    assert tags["type"] == "array"
    assert tags["items"] == {"type": "string"}

    pet_id = tools["getpetbyid"].input_schema["properties"]["petId"]
    assert pet_id["type"] == "integer"
    assert pet_id["description"] == "ID of pet to return (path parameter `petId`)"

    username = tools["getuserbyname"].input_schema["properties"]["username"]
    assert username["minLength"] == 1
    assert "path parameter `username`" in username["description"]

    with pytest.raises(Exception, match="available"):
        _call(module, "findpetsbystatus", {"status": "lost"})
    with pytest.raises(Exception, match="integer"):
        _call(module, "getpetbyid", {"petId": "abc"})
    with pytest.raises(Exception, match="at least 1 character"):
        _call(module, "getuserbyname", {"username": ""})
    with pytest.raises(Exception, match="list"):
        _call(module, "findpetsbytags", {"tags": "a,b"})
    assert calls == []

    _call(module, "findpetsbytags", {"tags": ["a", "b"]})
    assert calls == [{"method": "GET", "path": "/pet/findByTags", "params": {"tags": ["a", "b"]}}]


def test_json_results_are_structured(load) -> None:
    module, _ = load(PETSTORE, "petstore_result_mcp")
    tool = _schemas(module)["getpetbyid"]
    assert tool.output_schema["properties"]["result"] == {"$ref": "#/$defs/JsonValue"}
    result = _call(module, "getpetbyid", {"petId": 1})
    assert result.structured_content == {"result": {"ok": True, "items": [1, "two", None]}}


# --------------------------------------------------------------------- constraints


def test_constraints_reach_the_input_schema(load) -> None:
    module, _ = load(CONSTRAINED, "constrained_mcp")
    schema = _schemas(module)["search"].input_schema

    assert _prop(schema, "limit") | {} == {
        **_prop(schema, "limit"),
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
    }
    assert {"exclusiveMinimum": 0, "exclusiveMaximum": 1}.items() <= _prop(schema, "ratio").items()
    assert _prop(schema, "step")["multipleOf"] == 5
    assert {"minLength": 2, "maxLength": 4}.items() <= _prop(schema, "code").items()
    assert _prop(schema, "slug")["pattern"] == "^(?!admin)[a-z-]+$"
    assert "pattern" not in _prop(schema, "bad_pattern")
    ids = _prop(schema, "ids")
    assert (ids["items"], ids["minItems"], ids["maxItems"]) == ({"type": "integer"}, 1, 3)
    assert _prop(schema, "kinds")["items"]["enum"] == ["a", "b"]
    assert _prop(schema, "level")["enum"] == [1, 2, 3]
    # Enum with values a Literal can't hold: no enum, any JSON value.
    assert "enum" not in _prop(schema, "mixed")
    assert _prop(schema, "nullable")["type"] == "string"
    weird = _prop(schema, "weird")
    assert weird["type"] == "integer"
    assert "minimum" not in weird and "maximum" not in weird
    assert _prop(schema, "filter")["type"] == "object"
    assert "type" not in _prop(schema, "anything")
    assert _prop(schema, "Literal_")["description"].startswith("Shadows typing.Literal.")


def test_openapi_30_boolean_exclusive_minimum(load) -> None:
    module, _ = load(CONSTRAINED, "legacy_mcp")
    n = _prop(_schemas(module)["legacy"].input_schema, "n")
    assert n["exclusiveMinimum"] == 0
    assert "minimum" not in n


def test_constraints_are_enforced_before_any_request(load) -> None:
    module, calls = load(CONSTRAINED, "enforced_mcp")
    bad = [
        {"limit": 0},
        {"limit": 101},
        {"limit": 5, "ratio": 1},
        {"limit": 5, "step": 7},
        {"limit": 5, "code": "x"},
        {"limit": 5, "slug": "admin-panel"},
        {"limit": 5, "ids": []},
        {"limit": 5, "ids": [1, 2, 3, 4]},
        {"limit": 5, "kinds": ["c"]},
        {"limit": 5, "level": 4},
    ]
    for args in bad:
        with pytest.raises(Exception, match="validation error"):
            _call(module, "search", args)
    assert calls == []

    _call(
        module,
        "search",
        {"limit": 5, "slug": "pets", "kinds": ["a"], "level": 2, "mixed": {"x": 1}},
    )
    assert calls[0]["params"] == {
        "limit": 5,
        "slug": "pets",
        "kinds": ["a"],
        "level": 2,
        "mixed": {"x": 1},
    }


def test_path_strings_must_not_be_empty_and_whole_body_is_typed(load) -> None:
    module, calls = load(CONSTRAINED, "path_mcp")
    tool = _schemas(module)["putitem"]
    assert tool.input_schema["properties"]["item_id"]["minLength"] == 1
    assert _prop(tool.input_schema, "body")["items"] == {"type": "string"}
    assert "Whole request body" == _prop(tool.input_schema, "body")["description"]
    with pytest.raises(Exception, match="validation error"):
        _call(module, "putitem", {"item_id": "", "body": ["a"]})
    _call(module, "putitem", {"item_id": "a b", "body": ["x"]})
    assert calls == [{"method": "PUT", "path": "/items/a%20b", "json_body": ["x"]}]


def test_descriptions_name_where_each_argument_goes() -> None:
    spec = _doc(
        {
            "/t/{id}": {
                "post": {
                    "operationId": "t",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        },
                        {"name": "X-Trace", "in": "header", "schema": {"type": "string"}},
                        {"name": "sess", "in": "cookie", "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        }
                    },
                }
            }
        }
    )
    src = _server_source(spec)
    assert 'description="Path parameter `id`"' in src
    assert 'description="Header `X-Trace`"' in src
    assert 'description="Cookie `sess`"' in src
    assert 'description="Request body field `name`"' in src


def test_ir_without_locations_still_gets_descriptions() -> None:
    from mcp_wizard_generators.models import IntermediateRep, IrOperation

    ir = IntermediateRep(
        title="Old",
        version="1",
        servers=[],
        operations=[
            IrOperation(
                id="old",
                method="post",
                path="/old/{id}",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
                    "required": ["id"],
                },
            )
        ],
    )
    result = generate_python_mcp_result(ir, None, package_name="old_mcp")
    src = {e.path: e.content for e in result.tree.entries}["src/old_mcp/server.py"]
    assert 'id: Annotated[str, Field(min_length=1, description="Path parameter `id`")]' in src
    assert 'description="Request body field `note`"' in src


def test_one_parameter_per_line() -> None:
    src = _server_source(PETSTORE, ["getPetById", "logoutUser"])
    assert (
        "def getpetbyid(\n"
        '    petId: Annotated[int, Field(description="ID of pet to return (path parameter `petId`)")],\n'
        ") -> JsonValue:\n"
    ) in src
    assert "def logoutuser() -> JsonValue:\n" in src


def test_pydantic_is_a_declared_dependency() -> None:
    result = generate_python_mcp_result(PETSTORE, ["getPetById"], package_name="typed_mcp")
    files = {e.path: e.content for e in result.tree.entries}
    assert '"pydantic>=2.7,<3",' in files["pyproject.toml"]
    assert "pydantic>=2.7,<3\n" in files["requirements.txt"]


# --------------------------------------------------------------------- unit: annotations


def test_literal_handles_none_bools_and_duplicates() -> None:
    from mcp_wizard_generators.plugins.python_mcp.annotations import py_type

    assert py_type({"enum": [None, True, False, True]}) == "Literal[True, False]"
    assert py_type({"enum": ["a", "a", 2]}) == 'Literal["a", 2]'
    assert py_type({"type": "string", "enum": [None]}) == "str"
    assert py_type({"type": "array"}) == "list[JsonValue]"
    assert py_type("junk") == "JsonValue"


def test_openapi_30_boolean_exclusive_maximum_and_bad_steps() -> None:
    from mcp_wizard_generators.plugins.python_mcp.annotations import annotation

    text = annotation(
        {"type": "number", "maximum": 9, "exclusiveMaximum": True, "multipleOf": 0},
        "x",
        "query",
    )
    assert text == 'Annotated[float, Field(lt=9, description="Query parameter `x`")]'
    text = annotation({"type": "string", "minLength": 3, "maxLength": -1}, "s", "path")
    assert text == 'Annotated[str, Field(min_length=3, description="Path parameter `s`")]'
    assert annotation(None, "b", "body", whole_body=True) == (
        'Annotated[JsonValue, Field(description="Whole request body")]'
    )
