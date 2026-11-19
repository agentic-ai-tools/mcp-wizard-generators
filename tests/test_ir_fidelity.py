"""IR keeps where each parameter goes and follows in-document $ref (ADR-023)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mcp_wizard_generators.models import public_schema
from mcp_wizard_generators.openapi import openapi_to_ir
from mcp_wizard_generators.python_mcp import generate_python_mcp_result

FIXTURES = Path(__file__).parent / "fixtures"
PETSTORE = json.loads((FIXTURES / "petstore3.openapi.json").read_text(encoding="utf-8"))
IN = "x-mcp-wizard-in"


def _ops(spec: dict) -> dict:
    return {op.id: op for op in openapi_to_ir(spec).operations}


def _props(op) -> dict:
    return op.input_schema["properties"]


@pytest.fixture(scope="module")
def petstore() -> dict:
    return _ops(PETSTORE)


# --------------------------------------------------------------------- locations


def test_query_parameters_keep_their_location(petstore) -> None:
    props = _props(petstore["loginUser"])
    assert props["username"][IN] == "query"
    assert props["password"][IN] == "query"


def test_query_parameters_on_post_stay_in_the_query(petstore) -> None:
    props = _props(petstore["updatePetWithForm"])
    assert props["petId"][IN] == "path"
    assert props["name"][IN] == "query"
    assert props["status"][IN] == "query"


def test_header_parameters_are_headers(petstore) -> None:
    assert _props(petstore["deletePet"])["api_key"][IN] == "header"


def test_body_properties_are_marked_body(petstore) -> None:
    props = _props(petstore["addPet"])
    assert props["name"][IN] == "body"
    assert props["photoUrls"][IN] == "body"


def test_accept_content_type_and_authorization_headers_are_ignored() -> None:
    # OpenAPI 3: these header parameter definitions SHALL be ignored.
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/x": {
                "get": {
                    "operationId": "x",
                    "parameters": [
                        {"name": n, "in": "header", "schema": {"type": "string"}}
                        for n in ("Accept", "content-type", "Authorization", "X-Trace")
                    ],
                }
            }
        },
    }
    assert set(_props(_ops(spec)["x"])) == {"X-Trace"}


def test_non_object_body_is_one_whole_body_argument() -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/tags": {
                "put": {
                    "operationId": "putTags",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "array", "items": {"type": "string"}}
                            }
                        },
                    },
                }
            }
        },
    }
    body = _props(_ops(spec)["putTags"])["body"]
    assert body[IN] == "body"
    assert body["x-mcp-wizard-whole-body"] is True
    assert body["items"] == {"type": "string"}


# --------------------------------------------------------------------- $ref


def test_request_body_ref_is_followed(petstore) -> None:
    op = petstore["addPet"]
    props = _props(op)
    assert "body" not in props
    assert {"id", "name", "category", "photoUrls", "tags", "status"} <= set(props)
    assert props["status"]["enum"] == ["available", "pending", "sold"]
    assert props["photoUrls"]["items"]["type"] == "string"
    assert props["category"]["properties"]["name"]["type"] == "string"
    assert set(op.input_schema["required"]) >= {"name", "photoUrls"}


def test_parameter_schemas_keep_enum_items_and_format(petstore) -> None:
    status = _props(petstore["findPetsByStatus"])["status"]
    assert status["enum"] == ["available", "pending", "sold"]
    tags = _props(petstore["findPetsByTags"])["tags"]
    assert tags["type"] == "array"
    assert tags["items"] == {"type": "string"}
    pet_id = _props(petstore["getPetById"])["petId"]
    assert pet_id["type"] == "integer"
    assert pet_id["format"] == "int64"
    assert pet_id["description"]


def test_parameter_and_request_body_component_refs() -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "components": {
            "parameters": {
                "Limit": {
                    "name": "limit",
                    "in": "query",
                    "schema": {"$ref": "#/components/schemas/Lim"},
                }
            },
            "schemas": {"Lim": {"type": "integer", "maximum": 50}},
            "requestBodies": {
                "Note": {
                    "content": {
                        "application/json": {
                            "schema": {"type": "object", "properties": {"text": {"type": "string"}}}
                        }
                    }
                }
            },
        },
        "paths": {
            "/notes": {
                "parameters": [{"$ref": "#/components/parameters/Limit"}],
                "post": {
                    "operationId": "addNote",
                    "requestBody": {"$ref": "#/components/requestBodies/Note"},
                },
            }
        },
    }
    props = _props(_ops(spec)["addNote"])
    assert props["limit"] == {"type": "integer", "maximum": 50, IN: "query"}
    assert props["text"] == {"type": "string", IN: "body"}


def test_recursive_refs_terminate() -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Node"},
                        },
                    },
                }
            }
        },
        "paths": {
            "/nodes": {
                "post": {
                    "operationId": "addNode",
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Node"}}
                        }
                    },
                }
            }
        },
    }
    children = _props(_ops(spec)["addNode"])["children"]
    assert children["items"]["type"] == "object"
    # The cycle is cut, not expanded forever.
    assert "$ref" not in json.dumps(children)


def test_unresolvable_ref_does_not_crash() -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/x": {
                "get": {
                    "operationId": "x",
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "schema": {"$ref": "#/components/schemas/Missing"},
                        }
                    ],
                }
            }
        },
    }
    assert _props(_ops(spec)["x"])["q"][IN] == "query"


# --------------------------------------------------------------------- public schema


def test_public_schema_strips_internal_markers(petstore) -> None:
    clean = public_schema(petstore["addPet"].input_schema)
    assert "x-mcp-wizard" not in json.dumps(clean)
    assert clean["properties"]["status"]["enum"] == ["available", "pending", "sold"]


# --------------------------------------------------------------------- generated code


@pytest.fixture(scope="module")
def petstore_server() -> str:
    result = generate_python_mcp_result(PETSTORE, package_name="petstore_mcp")
    entry = result.tree.get("src/petstore_mcp/server.py")
    assert entry is not None
    return entry.content if isinstance(entry.content, str) else entry.content.decode()


def _tool_body(server: str, name: str) -> str:
    match = re.search(rf"def {name}\(.*?(?=\n\n\n|\Z)", server, re.S)
    assert match, name
    return match.group(0)


def test_generated_post_sends_query_parameters_in_the_query(petstore_server) -> None:
    body = _tool_body(petstore_server, "updatepetwithform")
    assert "params=" in body
    assert "json_body" not in body


def test_generated_header_parameter_goes_in_headers(petstore_server) -> None:
    body = _tool_body(petstore_server, "deletepet")
    assert "headers=" in body
    assert '"api_key"' in body
    assert "params=" not in body


def test_generated_body_from_ref_has_real_fields(petstore_server) -> None:
    body = _tool_body(petstore_server, "addpet")
    assert "body: str" not in body
    assert "photoUrls" in body
    assert "json_body=" in body


def test_generated_server_compiles(petstore_server) -> None:
    compile(petstore_server, "server.py", "exec")
