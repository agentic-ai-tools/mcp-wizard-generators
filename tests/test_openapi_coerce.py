"""OpenAPI documents must coerce to IR and honor selected operation ids."""

from __future__ import annotations

import pytest

from mcp_wizard_generators.models import IntermediateRep, IrOperation
from mcp_wizard_generators.openapi import is_openapi_document, openapi_to_ir
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.python_mcp import coerce_spec, generate_python_mcp_result

OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Pets", "version": "1.0.0"},
    "servers": [{"url": "https://pets.example.com/v1"}],
    "paths": {
        "/pets": {
            "get": {
                "operationId": "listPets",
                "summary": "List pets",
                "tags": ["pets"],
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "schema": {"type": "integer"},
                    }
                ],
            },
            "post": {
                "operationId": "createPets",
                "summary": "Create a pet",
                "tags": ["pets"],
            },
        },
        "/pets/{petId}": {
            "get": {
                "operationId": "showPetById",
                "summary": "Show pet",
                "parameters": [
                    {
                        "name": "petId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
            }
        },
    },
}


def test_coerce_openapi_document_to_ir_operations() -> None:
    ir, selected = coerce_spec(OPENAPI, ["listPets", "showPetById"])
    assert ir.title == "Pets"
    assert ir.version == "1.0.0"
    assert ir.servers == ["https://pets.example.com/v1"]
    assert [op.id for op in ir.operations] == ["listPets", "createPets", "showPetById"]
    assert selected == ["listPets", "showPetById"]


def test_generate_from_openapi_emits_only_selected_tools() -> None:
    result = generate_python_mcp_result(
        OPENAPI,
        operations=["listPets", "showPetById"],
        package_name="pets_openapi_mcp",
    )
    assert [t.tool_name for t in result.tool_map.tools] == ["listpets", "showpetbyid"]
    assert result.manifest["operations"] == ["listPets", "showPetById"]
    assert result.manifest["tools"] == ["listpets", "showpetbyid"]
    server = result.tree.get("src/pets_openapi_mcp/server.py")
    assert server is not None
    assert '@mcp.tool(name="listpets"' in server.content
    assert '@mcp.tool(name="showpetbyid"' in server.content
    assert "createpets" not in server.content
    assert "# No operations selected." not in server.content


def test_generate_from_openapi_rejects_unknown_selected_ids() -> None:
    with pytest.raises(ValueError, match="unknown operation"):
        generate_python_mcp_result(
            OPENAPI,
            operations=["listPets", "notARealOp"],
            package_name="pets_openapi_mcp",
        )


def test_openapi_without_operation_id_uses_method_path_id() -> None:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Bare", "version": "0.1.0"},
        "paths": {"/health": {"get": {"summary": "Health"}}},
    }
    ir, selected = coerce_spec(spec, ["get:/health"])
    assert [op.id for op in ir.operations] == ["get:/health"]
    assert selected == ["get:/health"]


def test_is_openapi_document_rejects_non_dict_and_ir_shapes() -> None:
    assert is_openapi_document("not-a-dict") is False  # type: ignore[arg-type]
    assert is_openapi_document({"operations": [], "openapi": "3.0.0", "paths": {}}) is False
    assert is_openapi_document({"openapi": "3.0.0"}) is False
    assert is_openapi_document(OPENAPI) is True
    assert is_openapi_document({"swagger": "2.0", "paths": {}}) is True


def test_swagger2_host_basepath_schemes_become_servers() -> None:
    spec = {
        "swagger": "2.0",
        "info": {"title": "Legacy", "version": "2.0.0"},
        "host": "api.example.com",
        "basePath": "/v2",
        "schemes": ["http"],
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "summary": "List",
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "type": "string",
                            "format": "email",
                            "description": "Filter",
                            "required": True,
                        },
                        {"in": "query"},  # missing name → skipped
                        "not-a-param",  # non-dict → skipped
                    ],
                }
            }
        },
    }
    ir = openapi_to_ir(spec)
    assert ir.spec_version == "2.0"
    assert ir.servers == ["http://api.example.com/v2"]
    assert [op.id for op in ir.operations] == ["listItems"]
    schema = ir.operations[0].input_schema
    assert schema["properties"]["q"]["type"] == "string"
    assert schema["properties"]["q"]["format"] == "email"
    assert schema["properties"]["q"]["description"] == "Filter"
    assert schema["required"] == ["q"]


def test_swagger2_host_without_schemes_defaults_https() -> None:
    spec = {
        "swagger": "2.0",
        "info": {"title": "HostOnly", "version": "1"},
        "host": "secure.example.com",
        "basePath": 123,  # non-str → treated as empty
        "paths": {"/ping": {"get": {"summary": "Ping"}}},
    }
    ir = openapi_to_ir(spec)
    assert ir.servers == ["https://secure.example.com"]
    assert ir.operations[0].id == "get:/ping"


def test_servers_accept_string_entries_and_skip_invalid() -> None:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Mixed", "version": "1"},
        "servers": [
            "https://string.example.com",
            {"url": ""},
            {"not_url": True},
            "",
            {"url": "https://dict.example.com"},
        ],
        "paths": {"/ok": {"get": {"operationId": "ok"}}},
    }
    ir = openapi_to_ir(spec)
    assert ir.servers == ["https://string.example.com", "https://dict.example.com"]


def test_invalid_servers_fall_through_to_swagger_host() -> None:
    """servers present but empty after filter → Swagger host/basePath fallback."""
    spec = {
        "swagger": "2.0",
        "info": {"title": "Fallback", "version": "1"},
        "servers": [{"url": ""}, ""],
        "host": "fallback.example.com",
        "basePath": "/api",
        "paths": {"/x": {"get": {"operationId": "x"}}},
    }
    ir = openapi_to_ir(spec)
    assert ir.servers == ["https://fallback.example.com/api"]


def test_paths_non_dict_yields_empty_operations() -> None:
    ir = openapi_to_ir(
        {
            "openapi": "3.0.0",
            "info": {"title": "NoPaths", "version": "1"},
            "paths": ["not", "a", "dict"],
        }
    )
    assert ir.operations == []
    assert ir.title == "NoPaths"


def test_request_body_object_and_non_object_schemas() -> None:
    object_body = {
        "openapi": "3.0.0",
        "info": {"title": "Body", "version": "1"},
        "paths": {
            "/pets": {
                "parameters": [
                    {
                        "name": "trace",
                        "in": "header",
                        "schema": {"type": "string"},
                    }
                ],
                "post": {
                    "operationId": "createPet",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "tag": {"type": "string"},
                                    },
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                },
            }
        },
    }
    ir = openapi_to_ir(object_body)
    props = ir.operations[0].input_schema["properties"]
    assert "trace" in props
    assert props["name"]["type"] == "string"
    assert ir.operations[0].input_schema["required"] == ["name"]

    scalar_body = {
        "openapi": "3.0.0",
        "info": {"title": "Scalar", "version": "1"},
        "paths": {
            "/echo": {
                "post": {
                    "operationId": "echo",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "string"},
                            }
                        },
                    },
                }
            }
        },
    }
    ir2 = openapi_to_ir(scalar_body)
    assert ir2.operations[0].input_schema["properties"]["body"]["type"] == "string"
    assert ir2.operations[0].input_schema["required"] == ["body"]

    plus_json = {
        "openapi": "3.0.0",
        "info": {"title": "Plus", "version": "1"},
        "paths": {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "content": {
                            "application/*+json": {
                                "schema": {
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "weird": "not-a-schema-dict",
                                    },
                                    "required": ["missing", "id"],
                                }
                            }
                        },
                    },
                }
            }
        },
    }
    ir3 = openapi_to_ir(plus_json)
    props = ir3.operations[0].input_schema["properties"]
    assert props["id"]["type"] == "integer"
    assert props["weird"] == {"type": "string"}
    # "missing" not in properties → not added to required
    assert ir3.operations[0].input_schema["required"] == ["id"]


def test_openapi_skips_non_http_methods_and_invalid_path_items() -> None:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Odd", "version": "1"},
        "paths": {
            "/ok": {
                "parameters": "ignore-me",
                "x-vendor": {"ignored": True},
                "get": "not-an-operation",
                "post": {"operationId": "createOk", "summary": "Create"},
            },
            "/bad": "not-a-path-item",
        },
    }
    ir = openapi_to_ir(spec)
    assert [op.id for op in ir.operations] == ["createOk"]


def test_param_schema_defaults_type_string_when_empty() -> None:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Param", "version": "1"},
        "paths": {
            "/x/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "schema": {"type": "string"}},
                ],
                "get": {
                    "operationId": "getX",
                    "parameters": [
                        # Duplicate path param name → required already present
                        {"name": "id", "in": "path", "required": True},
                        {
                            "name": "flag",
                            "in": "query",
                            "required": True,
                            "schema": {},
                        },
                    ],
                },
            }
        },
    }
    ir = openapi_to_ir(spec)
    schema = ir.operations[0].input_schema
    assert schema["properties"]["id"]["type"] == "string"
    assert schema["properties"]["flag"]["type"] == "string"
    assert schema["required"] == ["id", "flag"]


def test_coerce_already_ir_model_passes_through() -> None:
    ir = IntermediateRep(
        title="IR",
        version="1",
        operations=[
            IrOperation(id="a", method="get", path="/a", operation_id="a"),
        ],
    )
    out, selected = coerce_spec(ir, ["a"])
    assert out is ir
    assert selected == ["a"]


def test_coerce_ir_dict_without_openapi_keys() -> None:
    ir_dict = {
        "title": "Pets Tiny API",
        "version": "1.0.0",
        "operations": [
            {"id": "listPets", "method": "get", "path": "/pets", "operationId": "listPets"},
        ],
    }
    ir, selected = coerce_spec(ir_dict, ["listPets"])
    assert not is_openapi_document(ir_dict)
    assert [op.id for op in ir.operations] == ["listPets"]
    assert selected == ["listPets"]


def test_coerce_empty_spec_gets_default_title() -> None:
    ir, selected = coerce_spec({}, None)
    assert ir.title == "Generated API"
    assert ir.operations == []
    assert selected is None


def test_pipeline_rejects_explicit_empty_selection() -> None:
    ir = openapi_to_ir(OPENAPI)
    with pytest.raises(ValueError, match="selected_operation_ids must not be an empty list"):
        generate(ir, target="local", language="python", selected_operation_ids=[])


def test_pipeline_rejects_unknown_ids_when_catalog_empty() -> None:
    """Non-empty selection against an empty IR catalog fails closed (unknown ids)."""
    empty = IntermediateRep(title="Empty", version="1", operations=[])
    with pytest.raises(ValueError, match="unknown operation"):
        generate(
            empty,
            target="local",
            language="python",
            selected_operation_ids=["ghost"],
        )


def test_openapi_empty_paths_plus_selection_fails_closed() -> None:
    """OpenAPI with no operations + selected ids → unknown (empty after coerce/filter)."""
    bare = {
        "openapi": "3.0.0",
        "info": {"title": "Empty", "version": "1"},
        "paths": {},
    }
    with pytest.raises(ValueError, match="unknown operation"):
        generate_python_mcp_result(bare, operations=["listPets"], package_name="empty_mcp")


def test_generate_selects_method_path_id_from_openapi() -> None:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Bare", "version": "0.1.0"},
        "servers": [{"url": "https://example.com"}],
        "paths": {
            "/health": {"get": {"summary": "Health"}},
            "/ready": {"get": {"operationId": "readyCheck", "summary": "Ready"}},
        },
    }
    result = generate_python_mcp_result(
        spec,
        operations=["get:/health"],
        package_name="bare_mcp",
    )
    assert result.manifest["operations"] == ["get:/health"]
    # method:path → slug replaces non-alnum with underscores (get:/health → get__health)
    assert [t.tool_name for t in result.tool_map.tools] == ["get__health"]
    assert "readycheck" not in result.manifest["tools"]
