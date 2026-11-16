"""Relative OpenAPI server URLs resolve against the document's source URL.

OpenAPI 3 (section 4.8.5): server URLs may be relative, meaning relative to the
location where the OpenAPI document is served. Swagger 2: a missing ``host``
means the host serving the document. Without a ``source_url`` relative URLs are
left as-is (never guessed).
"""

from __future__ import annotations

import io
import zipfile

import pytest

from mcp_wizard_generators.openapi import openapi_to_ir
from mcp_wizard_generators.python_mcp import generate_python_mcp, generate_python_mcp_result

SOURCE = "https://petstore3.swagger.io/api/v3/openapi.json"


def _spec(servers: list[dict] | None = None, **extra) -> dict:
    spec: dict = {
        "openapi": "3.0.3",
        "info": {"title": "Pets", "version": "1.0.0"},
        "paths": {"/pet/{petId}": {"get": {"operationId": "getPetById"}}},
        **extra,
    }
    if servers is not None:
        spec["servers"] = servers
    return spec


@pytest.mark.parametrize(
    ("server", "expected"),
    [
        ("/api/v3", "https://petstore3.swagger.io/api/v3"),
        ("v2", "https://petstore3.swagger.io/api/v3/v2"),
        ("../v1", "https://petstore3.swagger.io/api/v1"),
        ("//cdn.example.com/x", "https://cdn.example.com/x"),
        ("https://other.example.com/base", "https://other.example.com/base"),
    ],
)
def test_relative_servers_resolve_against_source_url(server: str, expected: str) -> None:
    ir = openapi_to_ir(_spec([{"url": server}]), source_url=SOURCE)
    assert ir.servers == [expected]
    assert ir.operations[0].servers == (expected,)


def test_relative_server_kept_without_source_url() -> None:
    ir = openapi_to_ir(_spec([{"url": "/api/v3"}]))
    assert ir.servers == ["/api/v3"]


def test_no_servers_defaults_to_document_origin() -> None:
    # OpenAPI 3: an absent/empty servers array means a single server with url "/".
    ir = openapi_to_ir(_spec(), source_url=SOURCE)
    assert ir.servers == ["https://petstore3.swagger.io/"]


def test_no_servers_and_no_source_url_stays_empty() -> None:
    assert openapi_to_ir(_spec()).servers == []


def test_server_variables_use_defaults() -> None:
    servers = [
        {
            "url": "https://{env}.example.com:{port}/{basePath}",
            "variables": {
                "env": {"default": "api", "enum": ["api", "staging"]},
                "port": {"default": "8443"},
                "basePath": {"default": "v2"},
            },
        }
    ]
    assert openapi_to_ir(_spec(servers)).servers == ["https://api.example.com:8443/v2"]


def test_relative_server_with_variables_resolves() -> None:
    servers = [{"url": "/{version}", "variables": {"version": {"default": "v3"}}}]
    ir = openapi_to_ir(_spec(servers), source_url=SOURCE)
    assert ir.servers == ["https://petstore3.swagger.io/v3"]


def test_swagger2_without_host_uses_source_host() -> None:
    spec = {
        "swagger": "2.0",
        "info": {"title": "Pets", "version": "1"},
        "basePath": "/v2",
        "paths": {"/pet": {"get": {"operationId": "listPets"}}},
    }
    ir = openapi_to_ir(spec, source_url="https://petstore.swagger.io/v2/swagger.json")
    assert ir.servers == ["https://petstore.swagger.io/v2"]


def test_non_http_source_url_is_ignored() -> None:
    ir = openapi_to_ir(_spec([{"url": "/api"}]), source_url="file:///tmp/openapi.json")
    assert ir.servers == ["/api"]


def test_generate_python_mcp_writes_absolute_base_url(tmp_path) -> None:
    zip_path = tmp_path / "pets_mcp.zip"
    generate_python_mcp(
        _spec([{"url": "/api/v3"}]),
        operations=["getPetById"],
        zip_path=zip_path,
        package_name="pets_mcp",
        source_url=SOURCE,
    )
    with zipfile.ZipFile(io.BytesIO(zip_path.read_bytes())) as zf:
        env = next(zf.read(n).decode() for n in zf.namelist() if n.endswith(".env.example"))
        mcp_json = next(
            zf.read(n).decode() for n in zf.namelist() if n.endswith(".cursor/mcp.json")
        )
    assert "API_BASE_URL=https://petstore3.swagger.io/api/v3" in env
    assert "https://petstore3.swagger.io/api/v3" in mcp_json


def test_generate_python_mcp_result_accepts_source_url() -> None:
    result = generate_python_mcp_result(
        _spec([{"url": "/api/v3"}]),
        operations=["getPetById"],
        package_name="pets_mcp",
        source_url=SOURCE,
    )
    env = next(e.content for e in result.tree.entries if e.path.endswith(".env.example"))
    assert "API_BASE_URL=https://petstore3.swagger.io/api/v3" in env
