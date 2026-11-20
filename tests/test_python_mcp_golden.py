"""Golden + zip tests for local Python MCP generation."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from mcp_wizard_generators.models import IntermediateRep
from mcp_wizard_generators.packaging import pack_file_tree_to_zip
from mcp_wizard_generators.python_mcp import generate_python_mcp, generate_python_mcp_result

ROOT = Path(__file__).parent
FIXTURE = ROOT / "fixtures" / "pets_tiny.ir.json"
GOLDEN_DIR = ROOT / "goldens" / "python_mcp_pets_tiny"

EXPECTED_ZIP_MEMBERS = [
    "pets-tiny-mcp/.cursor/mcp.json",
    "pets-tiny-mcp/.env.example",
    "pets-tiny-mcp/README.md",
    "pets-tiny-mcp/generation-manifest.json",
    "pets-tiny-mcp/pyproject.toml",
    "pets-tiny-mcp/requirements.lock",
    "pets-tiny-mcp/requirements.txt",
    "pets-tiny-mcp/src/pets_tiny_mcp/__init__.py",
    "pets-tiny-mcp/src/pets_tiny_mcp/__main__.py",
    "pets-tiny-mcp/src/pets_tiny_mcp/client.py",
    "pets-tiny-mcp/src/pets_tiny_mcp/server.py",
    "pets-tiny-mcp/src/pets_tiny_mcp/transport.py",
    "pets-tiny-mcp/src/pets_tiny_mcp/verify.py",
    "pets-tiny-mcp/tests/conftest.py",
    "pets-tiny-mcp/tests/test_auth.py",
    "pets-tiny-mcp/tests/test_http_errors.py",
    "pets-tiny-mcp/tests/test_redaction.py",
    "pets-tiny-mcp/tests/test_tools.py",
    "pets-tiny-mcp/tests/test_transport.py",
]


def _normalize(content: str) -> str:
    return content.replace("\r\n", "\n")


def golden_view(path: str, content: str) -> str:
    """What a golden stores. The lock keeps only its header: the pins change with every
    dependency bump and are checked against the packaged lock in test_python_mcp_lock."""
    if path == "requirements.lock":
        return content.split("\n\n", 1)[0] + "\n"
    return content


def test_python_mcp_pets_tiny_golden() -> None:
    ir = IntermediateRep.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    result = generate_python_mcp_result(
        ir,
        ["listPets", "showPetById"],
        package_name="pets_tiny_mcp",
    )

    expected_paths = sorted(
        str(p.relative_to(GOLDEN_DIR)).replace("\\", "/")
        for p in GOLDEN_DIR.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    actual_paths = result.tree.paths()
    assert actual_paths == expected_paths, (
        f"file tree drift.\nactual={actual_paths}\nexpected={expected_paths}"
    )

    for entry in result.tree.entries:
        golden_file = GOLDEN_DIR / entry.path
        assert golden_file.is_file(), f"missing golden: {entry.path}"
        actual = golden_view(entry.path, _normalize(entry.content))
        assert actual == _normalize(golden_file.read_text(encoding="utf-8")), (
            f"content drift: {entry.path}"
        )

    assert [t.tool_name for t in result.tool_map.tools] == ["listpets", "showpetbyid"]
    server = result.tree.get("src/pets_tiny_mcp/server.py")
    assert server is not None
    assert "from mcp.server.mcpserver import MCPServer" in server.content
    assert '@mcp.tool(name="listpets"' in server.content
    assert '@mcp.tool(name="showpetbyid"' in server.content
    # Path arguments are URL-encoded so a value like "../admin" can't change the route.
    assert 'path_template.format(petId=quote(str(petId), safe=""))' in server.content

    cursor = result.tree.get(".cursor/mcp.json")
    assert cursor is not None
    cfg = json.loads(cursor.content)
    assert "mcpServers" in cfg
    assert cfg["mcpServers"]["pets_tiny_mcp"]["args"] == ["-m", "pets_tiny_mcp"]


def test_python_mcp_zip_contains_expected_files(tmp_path: Path) -> None:
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    zip_path = tmp_path / "pets_tiny_mcp.zip"
    out = generate_python_mcp(
        ir,
        ["listPets", "showPetById"],
        out_dir=tmp_path / "tree",
        zip_path=zip_path,
        package_name="pets_tiny_mcp",
    )
    assert out == tmp_path / "tree"
    assert zip_path.is_file()
    assert zipfile.is_zipfile(zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(zf.namelist())
        assert names == EXPECTED_ZIP_MEMBERS
        # Valid zip: every member readable
        for name in names:
            data = zf.read(name)
            assert isinstance(data, bytes)


def test_python_mcp_pack_file_tree_zip_roundtrip() -> None:
    result = generate_python_mcp_result(
        json.loads(FIXTURE.read_text(encoding="utf-8")),
        ["listPets", "showPetById"],
        package_name="pets_tiny_mcp",
    )
    data = pack_file_tree_to_zip(result.tree)
    assert zipfile.is_zipfile(io.BytesIO(data))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == EXPECTED_ZIP_MEMBERS


def test_generate_python_mcp_simple_operations(tmp_path: Path) -> None:
    """API can pass simple operation objects instead of a full IR catalog."""
    out = generate_python_mcp(
        {"title": "Simple Pets", "servers": ["https://example.com"]},
        operations=[
            {
                "operationId": "listPets",
                "method": "get",
                "path": "/pets",
                "summary": "List pets",
                "params": [{"name": "limit", "type": "integer"}],
            }
        ],
        out_dir=tmp_path / "simple",
        package_name="simple_pets_mcp",
    )
    assert (out / "src/simple_pets_mcp/server.py").is_file()
    assert (out / ".cursor/mcp.json").is_file()
    text = (out / "src/simple_pets_mcp/server.py").read_text(encoding="utf-8")
    assert "listpets" in text


SECURITY_GOLDENS = {
    "python_mcp_pets_tiny_api_key_query": {
        "authMode": "api_key",
        "apiKey": {"location": "query", "name": "api_key"},
    },
    "python_mcp_pets_tiny_oauth2": {
        "authMode": "oauth2",
        "oauth": {
            "tokenUrl": "https://auth.example.com/oauth2/token",
            "scopes": ["pets.read"],
            "audience": "https://petstore.example.com",
        },
    },
}


@pytest.mark.parametrize("golden", sorted(SECURITY_GOLDENS))
def test_python_mcp_security_goldens(golden: str) -> None:
    """Lock generated auth code per mode (ADR-017); behavior is in test_python_mcp_security."""
    ir = IntermediateRep.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    result = generate_python_mcp_result(
        ir,
        ["listPets", "showPetById"],
        package_name="pets_tiny_mcp",
        security=SECURITY_GOLDENS[golden],
    )
    golden_dir = ROOT / "goldens" / golden
    expected_paths = sorted(
        str(p.relative_to(golden_dir)).replace("\\", "/")
        for p in golden_dir.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    assert result.tree.paths() == expected_paths
    for entry in result.tree.entries:
        golden_file = golden_dir / entry.path
        actual = golden_view(entry.path, _normalize(entry.content))
        assert actual == _normalize(golden_file.read_text(encoding="utf-8")), (
            f"content drift: {golden}/{entry.path}"
        )


DOCKER_JWT_GOLDEN = "python_mcp_pets_tiny_docker_jwt"


def docker_jwt_result():
    """The docker deployment with JWT checks, GitHub Actions and scripts (ADR-026)."""
    from mcp_wizard_generators.models import DeploymentConfig, InboundAuthConfig

    ir = IntermediateRep.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    return generate_python_mcp_result(
        ir,
        ["listPets", "showPetById"],
        package_name="pets_tiny_mcp",
        deployment=DeploymentConfig(
            target="docker",
            ci="github-actions",
            scripts=True,
            inbound_auth=InboundAuthConfig(
                mode="jwt",
                discovery_url="https://login.example.com/tenant/.well-known/openid-configuration",
                allowed_audiences=("api://pets",),
            ),
        ),
    )


def test_python_mcp_docker_jwt_golden() -> None:
    result = docker_jwt_result()
    golden_dir = ROOT / "goldens" / DOCKER_JWT_GOLDEN
    expected_paths = sorted(
        str(p.relative_to(golden_dir)).replace("\\", "/")
        for p in golden_dir.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    assert result.tree.paths() == expected_paths
    for entry in result.tree.entries:
        actual = golden_view(entry.path, _normalize(entry.content))
        expected = _normalize((golden_dir / entry.path).read_text(encoding="utf-8"))
        assert actual == expected, f"content drift: {DOCKER_JWT_GOLDEN}/{entry.path}"
