"""Generated-MCP security is honored end to end (design ADR-017).

These tests import the *generated* ``client.py`` and drive it through an
``httpx.MockTransport`` so they check runtime behavior, not just emitted text.
"""

from __future__ import annotations

import base64
import importlib
import json
import sys
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from mcp_wizard_generators.models import IntermediateRep, SecurityConfig
from mcp_wizard_generators.python_mcp import generate_python_mcp, generate_python_mcp_result

ROOT = Path(__file__).parent
FIXTURE = ROOT / "fixtures" / "pets_tiny.ir.json"
PKG = "pets_tiny_mcp"
API = "https://petstore.example.com/v1"
TOKEN_URL = "https://auth.example.com/oauth2/token"


def _ir() -> IntermediateRep:
    return IntermediateRep.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _files(security: SecurityConfig | dict) -> dict[str, str]:
    result = generate_python_mcp_result(
        _ir(), ["listPets", "showPetById"], package_name=PKG, security=security
    )
    return {e.path: e.content for e in result.tree.entries}


@pytest.fixture
def load_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[SecurityConfig | dict, Callable[[httpx.Request], httpx.Response]], object]]:
    """Generate a project, import its client module, and route HTTP to ``handler``."""

    loaded: list[str] = []

    def _load(security, handler):
        out = tmp_path / f"gen{len(loaded)}"
        generate_python_mcp(_ir(), ["listPets"], out_dir=out, package_name=PKG, security=security)
        monkeypatch.syspath_prepend(str(out / "src"))
        for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
            del sys.modules[name]
        module = importlib.import_module(f"{PKG}.client")
        monkeypatch.setattr(
            module.httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(handler))
        )
        monkeypatch.setenv("API_BASE_URL", API)
        loaded.append(PKG)
        return module

    yield _load
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


# --- SecurityConfig: contract mapping + validation -------------------------


def test_from_wizard_maps_the_experience_api_contract() -> None:
    sec = SecurityConfig.from_wizard(
        {
            "authMode": "oauth2",
            "apiKey": None,
            "oauth": {
                "tokenUrl": TOKEN_URL,
                "scopes": ["pets.read", "pets.write"],
                "audience": "https://petstore.example.com",
            },
            "secretsStrategy": "dotenv_example_only",
            "mcpAuth": False,
        }
    )
    assert sec.auth_mode == "oauth2"
    assert sec.oauth_token_url == TOKEN_URL
    assert sec.oauth_scopes == ["pets.read", "pets.write"]
    assert sec.oauth_audience == "https://petstore.example.com"

    key = SecurityConfig.from_wizard(
        {"authMode": "api_key", "apiKey": {"location": "query", "name": "api_key"}}
    )
    assert (key.api_key_location, key.api_key_name) == ("query", "api_key")


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"authMode": "mtls"}, "authMode"),
        ({"authMode": "api_key"}, "apiKey"),
        ({"authMode": "api_key", "apiKey": {"location": "cookie", "name": "k"}}, "location"),
        ({"authMode": "api_key", "apiKey": {"location": "header", "name": "bad name"}}, "name"),
        ({"authMode": "oauth2"}, "tokenUrl"),
        ({"authMode": "oauth2", "oauth": {"tokenUrl": "http://auth.example.com/t"}}, "https"),
        ({"authMode": "none", "mcpAuth": True}, "mcpAuth"),
    ],
)
def test_invalid_security_is_rejected(config: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SecurityConfig.from_wizard(config)


def test_invalid_security_fails_before_writing(tmp_path: Path) -> None:
    out = tmp_path / "never"
    with pytest.raises(ValueError):
        generate_python_mcp(
            _ir(), ["listPets"], out_dir=out, package_name=PKG, security={"authMode": "mtls"}
        )
    assert not out.exists()


def test_unknown_auth_mode_is_never_silently_downgraded() -> None:
    with pytest.raises(ValueError, match="authMode"):
        generate_python_mcp_result(_ir(), ["listPets"], package_name=PKG, auth_mode="mtls")


# --- api_key ---------------------------------------------------------------


def test_api_key_header_uses_configured_name(load_client, monkeypatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    client = load_client(
        {"authMode": "api_key", "apiKey": {"location": "header", "name": "X-Pet-Key"}}, handler
    )
    monkeypatch.setenv("API_KEY", "s3cret")
    client.request("get", "/pets")
    assert seen[0].headers["X-Pet-Key"] == "s3cret"
    assert "api_key" not in seen[0].url.params


def test_api_key_query_param(load_client, monkeypatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    client = load_client(
        {"authMode": "api_key", "apiKey": {"location": "query", "name": "api_key"}}, handler
    )
    monkeypatch.setenv("API_KEY", "s3cret")
    client.request("get", "/pets", params={"limit": 5})
    assert seen[0].url.params["api_key"] == "s3cret"
    assert seen[0].url.params["limit"] == "5"
    assert "X-API-Key" not in seen[0].headers


def test_api_key_missing_fails_closed(load_client, monkeypatch) -> None:
    client = load_client(
        {"authMode": "api_key", "apiKey": {"location": "header", "name": "X-API-Key"}}, _ok
    )
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="API_KEY"):
        client.request("get", "/pets")


# --- oauth2 client credentials --------------------------------------------


def _oauth_handler(calls: dict, *, expires_in: int = 3600, fail_first_api_call: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            calls["token"] += 1
            calls["token_requests"].append(request)
            return httpx.Response(
                200,
                json={
                    "access_token": f"tok-{calls['token']}",
                    "token_type": "Bearer",
                    "expires_in": expires_in,
                },
            )
        calls["api"].append(request)
        if fail_first_api_call and len(calls["api"]) == 1:
            return httpx.Response(401, json={"error": "expired"})
        return _ok(request)

    return handler


OAUTH = {
    "authMode": "oauth2",
    "oauth": {"tokenUrl": TOKEN_URL, "scopes": ["pets.read"], "audience": "https://petstore"},
}


@pytest.fixture
def oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "csecret")


def test_oauth2_client_credentials_fetches_and_uses_token(load_client, oauth_env) -> None:
    calls: dict = {"token": 0, "token_requests": [], "api": []}
    client = load_client(OAUTH, _oauth_handler(calls))
    client.request("get", "/pets")

    token_req = calls["token_requests"][0]
    assert token_req.method == "POST"
    form = parse_qs(token_req.content.decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["scope"] == ["pets.read"]
    assert form["audience"] == ["https://petstore"]
    expected_basic = base64.b64encode(b"cid:csecret").decode()
    assert token_req.headers["Authorization"] == f"Basic {expected_basic}"
    assert calls["api"][0].headers["Authorization"] == "Bearer tok-1"


def test_oauth2_token_is_cached(load_client, oauth_env) -> None:
    calls: dict = {"token": 0, "token_requests": [], "api": []}
    client = load_client(OAUTH, _oauth_handler(calls))
    client.request("get", "/pets")
    client.request("get", "/pets")
    assert calls["token"] == 1


def test_oauth2_expired_token_is_refreshed(load_client, oauth_env) -> None:
    calls: dict = {"token": 0, "token_requests": [], "api": []}
    # expires_in below the refresh margin → every call needs a fresh token
    client = load_client(OAUTH, _oauth_handler(calls, expires_in=1))
    client.request("get", "/pets")
    client.request("get", "/pets")
    assert calls["token"] == 2
    assert calls["api"][1].headers["Authorization"] == "Bearer tok-2"


def test_oauth2_401_refreshes_once_and_retries(load_client, oauth_env) -> None:
    calls: dict = {"token": 0, "token_requests": [], "api": []}
    client = load_client(OAUTH, _oauth_handler(calls, fail_first_api_call=True))
    assert client.request("get", "/pets") == {"ok": True}
    assert calls["token"] == 2
    assert [r.headers["Authorization"] for r in calls["api"]] == ["Bearer tok-1", "Bearer tok-2"]


def test_oauth2_missing_client_credentials_fails_closed(load_client, monkeypatch) -> None:
    calls: dict = {"token": 0, "token_requests": [], "api": []}
    client = load_client(OAUTH, _oauth_handler(calls))
    monkeypatch.delenv("OAUTH_CLIENT_ID", raising=False)
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "csecret")
    with pytest.raises(RuntimeError, match="OAUTH_CLIENT_ID"):
        client.request("get", "/pets")
    assert calls["api"] == []


def test_oauth2_rejects_non_https_token_url_override(load_client, oauth_env, monkeypatch) -> None:
    calls: dict = {"token": 0, "token_requests": [], "api": []}
    client = load_client(OAUTH, _oauth_handler(calls))
    monkeypatch.setenv("OAUTH_TOKEN_URL", "http://auth.example.com/oauth2/token")
    with pytest.raises(RuntimeError, match="https"):
        client.request("get", "/pets")


# --- none ------------------------------------------------------------------


def test_none_sends_no_credentials(load_client) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    client = load_client({"authMode": "none"}, handler)
    client.request("get", "/pets")
    assert "Authorization" not in seen[0].headers
    assert not seen[0].url.params


# --- emitted config / docs -------------------------------------------------


def test_env_example_and_cursor_config_match_security() -> None:
    files = _files({"authMode": "api_key", "apiKey": {"location": "query", "name": "api_key"}})
    env = files[".env.example"]
    assert "API_KEY=replace-me" in env
    assert "API_KEY_LOCATION=query" in env
    assert "API_KEY_NAME=api_key" in env
    cursor_env = json.loads(files[".cursor/mcp.json"])["mcpServers"]["pets_tiny_mcp"]["env"]
    assert cursor_env["API_KEY"] == "${API_KEY}"

    files = _files(OAUTH)
    env = files[".env.example"]
    assert f"OAUTH_TOKEN_URL={TOKEN_URL}" in env
    assert "OAUTH_CLIENT_ID=" in env and "OAUTH_CLIENT_SECRET=" in env
    assert "OAUTH_SCOPES=pets.read" in env
    assert "API_TOKEN" not in env
    cursor_env = json.loads(files[".cursor/mcp.json"])["mcpServers"]["pets_tiny_mcp"]["env"]
    assert cursor_env["OAUTH_CLIENT_SECRET"] == "${OAUTH_CLIENT_SECRET}"


def test_manifest_records_non_secret_security_details() -> None:
    manifest = json.loads(_files(OAUTH)["generation-manifest.json"])
    assert manifest["security"] == {
        "authMode": "oauth2",
        "secretsStrategy": "dotenv_example_only",
        "oauth": {"tokenUrl": TOKEN_URL, "scopes": ["pets.read"], "audience": "https://petstore"},
    }


def test_readme_documents_the_auth_mode() -> None:
    readme = _files(OAUTH)["README.md"]
    assert "## Authentication" in readme
    assert "client credentials" in readme
    readme = _files({"authMode": "none"})["README.md"]
    assert "no authentication" in readme.lower()


def test_no_secret_values_in_generated_tree() -> None:
    for config in (
        OAUTH,
        {"authMode": "api_key", "apiKey": {"location": "header", "name": "X-API-Key"}},
    ):
        blob = "\n".join(_files(config).values())
        assert "csecret" not in blob and "s3cret" not in blob


# --- CLI -------------------------------------------------------------------


def test_cli_generate_python_accepts_security_json(tmp_path: Path) -> None:
    from mcp_wizard_generators.cli import main

    sec_file = tmp_path / "security.json"
    sec_file.write_text(json.dumps(OAUTH), encoding="utf-8")
    out = tmp_path / "out"
    rc = main(
        [
            "generate-python",
            "--ir",
            str(FIXTURE),
            "--select",
            "listPets",
            "--package-name",
            PKG,
            "--out-dir",
            str(out),
            "--security",
            str(sec_file),
        ]
    )
    assert rc == 0
    assert f"OAUTH_TOKEN_URL={TOKEN_URL}" in (out / ".env.example").read_text(encoding="utf-8")


def test_cli_rejects_unsupported_auth_mode(tmp_path: Path) -> None:
    from mcp_wizard_generators.cli import main

    with pytest.raises(SystemExit):
        main(
            [
                "generate-python",
                "--ir",
                str(FIXTURE),
                "--out-dir",
                str(tmp_path / "o"),
                "--auth-mode",
                "mtls",
            ]
        )
