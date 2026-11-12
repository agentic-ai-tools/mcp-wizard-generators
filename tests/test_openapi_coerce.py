"""OpenAPI documents must coerce to IR and honor selected operation ids."""

from __future__ import annotations

import pytest

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
