"""Generated client hardening (ADR-023): config checks, error mapping, size limits, redaction.

These tests import the *generated* ``client.py``, ``server.py`` and ``verify.py`` and
drive them through ``httpx.MockTransport``, so they check runtime behavior.
"""

from __future__ import annotations

import gzip
import importlib
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_wizard_generators.models import IntermediateRep
from mcp_wizard_generators.python_mcp import generate_python_mcp, generate_python_mcp_result

ROOT = Path(__file__).parent
IR = IntermediateRep.from_dict(
    json.loads((ROOT / "fixtures" / "pets_tiny.ir.json").read_text(encoding="utf-8"))
)
PETSTORE = json.loads((ROOT / "fixtures" / "petstore3.openapi.json").read_text(encoding="utf-8"))
PKG = "hardened_mcp"
API = "https://petstore.example.com/v1"
_REAL_CLIENT = httpx.Client
API_KEY_HEADER = {"authMode": "api_key", "apiKey": {"location": "header", "name": "X-Key"}}
OAUTH = {"authMode": "oauth2", "oauth": {"tokenUrl": "https://auth.example.com/token"}}


def _purge() -> None:
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]


@pytest.fixture
def project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., dict[str, Any]]]:
    """Generate a project and import its modules; HTTP goes to ``handler``."""

    def _load(
        handler: Callable[[httpx.Request], httpx.Response] | None = None,
        *,
        security: dict | None = None,
        spec: Any = IR,
    ) -> dict[str, Any]:
        _purge()
        out = tmp_path / "gen"
        generate_python_mcp(spec, None, out_dir=out, package_name=PKG, security=security)
        monkeypatch.syspath_prepend(str(out / "src"))
        client = importlib.import_module(f"{PKG}.client")
        made: list[int] = []

        def factory(**kwargs: Any) -> httpx.Client:
            made.append(1)
            return _REAL_CLIENT(transport=httpx.MockTransport(handler or _ok), **kwargs)

        monkeypatch.setattr(client.httpx, "Client", factory)
        monkeypatch.setenv("API_BASE_URL", API)
        return {
            "client": client,
            "server": importlib.import_module(f"{PKG}.server"),
            "verify": importlib.import_module(f"{PKG}.verify"),
            "made": made,
            "root": out,
        }

    yield _load
    _purge()


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def _reply(status: int, **kwargs: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, **kwargs)


# --------------------------------------------------------------------- mcp 2 only


def test_server_imports_mcpserver_with_no_fallback() -> None:
    files = {
        e.path: e.content
        for e in generate_python_mcp_result(IR, None, package_name=PKG).tree.entries
    }
    server = files[f"src/{PKG}/server.py"]
    assert "from mcp.server.mcpserver import MCPServer\n" in server
    assert 'mcp = MCPServer("Pets Tiny API")' in server
    assert "fastmcp" not in server.lower()
    assert "ImportError" not in server


# --------------------------------------------------------------------- API_BASE_URL


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "API_BASE_URL is not set"),
        ("ftp://files.example.com", "must use http or https, not 'ftp'"),
        ("file:///etc/passwd", "must use http or https, not 'file'"),
        ("api.example.com/v1", "must use http or https"),
        ("https://", "has no host"),
        ("https://user:pw@api.example.com", "must not contain a user name or password"),
        ("https://api.example.com/v1?key=1", "must not have a query string or fragment"),
        ("https://api.example.com:99999", "is not a valid URL"),
    ],
)
def test_api_base_url_is_validated(project, monkeypatch, value: str, message: str) -> None:
    mods = project()
    monkeypatch.setenv("API_BASE_URL", value)
    with pytest.raises(mods["client"].ConfigError, match=message):
        mods["client"].request("GET", "/pets")
    assert mods["made"] == []


def test_api_base_url_trailing_slash_is_trimmed(project, monkeypatch) -> None:
    seen: list[httpx.Request] = []
    mods = project(lambda r: seen.append(r) or _ok(r))
    monkeypatch.setenv("API_BASE_URL", " http://localhost:8080/api/ ")
    mods["client"].request("GET", "/pets")
    assert str(seen[0].url) == "http://localhost:8080/api/pets"


def test_server_main_fails_fast_on_bad_config(project, monkeypatch, capsys) -> None:
    mods = project()
    ran: list[str] = []
    monkeypatch.setattr(mods["server"].mcp, "run", lambda **kw: ran.append("run"))
    monkeypatch.setenv("API_BASE_URL", "ftp://x")
    with pytest.raises(SystemExit) as exit_info:
        mods["server"].main()
    assert exit_info.value.code == 2
    assert "API_BASE_URL must use http or https" in capsys.readouterr().err
    assert ran == []

    monkeypatch.setenv("API_BASE_URL", API)
    mods["server"].main()
    assert ran == ["run"]


def test_server_main_checks_auth_settings(project, monkeypatch, capsys) -> None:
    mods = project(security=API_KEY_HEADER)
    monkeypatch.setattr(mods["server"].mcp, "run", lambda **kw: None)
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(SystemExit):
        mods["server"].main()
    assert "API_KEY is not set" in capsys.readouterr().err


def test_oauth_settings_are_checked_at_startup(project, monkeypatch) -> None:
    mods = project(security=OAUTH)
    monkeypatch.setenv("OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "csecret")
    mods["client"].check_config()
    monkeypatch.setenv("OAUTH_TOKEN_URL", "http://auth.example.com/token")
    with pytest.raises(mods["client"].ConfigError, match="https"):
        mods["client"].check_config()


def test_api_key_location_is_checked_at_startup(project, monkeypatch) -> None:
    mods = project(security=API_KEY_HEADER)
    monkeypatch.setenv("API_KEY", "k-123456")
    monkeypatch.setenv("API_KEY_LOCATION", "cookie")
    with pytest.raises(mods["client"].ConfigError, match="'header' or 'query'"):
        mods["client"].check_config()


# --------------------------------------------------------------------- shared client


def test_one_http_client_is_reused_and_keeps_no_cookies(project) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={}, headers={"set-cookie": "sid=abc; Path=/"})

    mods = project(handler)
    mods["client"].request("GET", "/pets")
    mods["client"].request("GET", "/pets")
    assert len(mods["made"]) == 1
    assert "cookie" not in seen[1].headers


def test_redirects_are_not_followed(project) -> None:
    mods = project(_reply(302, headers={"location": "https://evil.example.com/?token=x"}))
    with pytest.raises(mods["client"].ApiError, match="redirected") as info:
        mods["client"].request("GET", "/pets")
    assert "evil" not in str(info.value)


# --------------------------------------------------------------------- response size


def test_large_responses_are_refused(project, monkeypatch) -> None:
    mods = project(_reply(200, content=b"x" * (1024 * 1024 + 1)))
    with pytest.raises(mods["client"].ApiError, match="larger than 1048576 bytes"):
        mods["client"].request("GET", "/pets")

    monkeypatch.setenv("MAX_RESPONSE_BYTES", "4096")
    mods = project(_reply(200, json={"pad": "y" * 5000}))
    with pytest.raises(mods["client"].ApiError, match="MAX_RESPONSE_BYTES"):
        mods["client"].request("GET", "/pets")


def test_declared_length_is_refused_before_reading(project, monkeypatch) -> None:
    monkeypatch.setenv("MAX_RESPONSE_BYTES", "100")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "5000"}, content=b"x" * 5000)

    mods = project(handler)
    with pytest.raises(mods["client"].ApiError, match="larger than 100 bytes"):
        mods["client"].request("GET", "/pets")


def test_compressed_responses_are_limited_after_decompression(project, monkeypatch) -> None:
    monkeypatch.setenv("MAX_RESPONSE_BYTES", "10000")
    bomb = gzip.compress(b"0" * 200_000)
    assert len(bomb) < 10000
    mods = project(
        _reply(
            200, content=bomb, headers={"content-encoding": "gzip", "content-type": "text/plain"}
        )
    )
    with pytest.raises(mods["client"].ApiError, match="larger than 10000 bytes"):
        mods["client"].request("GET", "/pets")


@pytest.mark.parametrize("value", ["lots", "0", "-5"])
def test_bad_max_response_bytes_is_a_config_error(project, monkeypatch, value: str) -> None:
    mods = project()
    monkeypatch.setenv("MAX_RESPONSE_BYTES", value)
    with pytest.raises(mods["client"].ConfigError, match="MAX_RESPONSE_BYTES"):
        mods["client"].check_config()


# --------------------------------------------------------------------- responses and errors


def test_json_text_and_empty_responses(project) -> None:
    assert project(_reply(200, json=[1, {"a": None}]))["client"].request("GET", "/p") == [
        1,
        {"a": None},
    ]
    vendor = _reply(200, content=b'{"x": 1}', headers={"content-type": "application/vnd.api+json"})
    assert project(vendor)["client"].request("GET", "/p") == {"x": 1}
    text = _reply(200, text="plain words")
    assert project(text)["client"].request("GET", "/p") == {"text": "plain words", "status": 200}
    assert project(_reply(204))["client"].request("DELETE", "/p") == {"status": 204}


def test_json_that_does_not_parse_is_an_error(project) -> None:
    mods = project(_reply(200, content=b"{oops", headers={"content-type": "application/json"}))
    with pytest.raises(mods["client"].ApiError, match="not valid JSON"):
        mods["client"].request("GET", "/pets")


@pytest.mark.parametrize(
    ("security", "status", "expected"),
    [
        (API_KEY_HEADER, 401, ["GET /pets failed: HTTP 401", "authentication failed", "API_KEY"]),
        (None, 401, ["HTTP 401", "this API needs credentials"]),
        (None, 403, ["HTTP 403", "not allowed"]),
        (None, 404, ["HTTP 404", "not found", "check the IDs"]),
        (None, 409, ["HTTP 409", "the API rejected the request"]),
        (None, 500, ["HTTP 500", "the API failed", "try again later"]),
        (None, 503, ["HTTP 503", "try again later"]),
    ],
)
def test_http_statuses_map_to_actionable_errors(
    project, monkeypatch, security, status: int, expected: list[str]
) -> None:
    monkeypatch.setenv("API_KEY", "k-123456")
    mods = project(_reply(status, json={}), security=security)
    with pytest.raises(mods["client"].ApiError) as info:
        mods["client"].request("GET", "/pets")
    for text in expected:
        assert text in str(info.value)
    assert info.value.status == status


def test_oauth_401_error_names_the_oauth_settings(project, monkeypatch) -> None:
    monkeypatch.setenv("OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "csecret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.example.com":
            return httpx.Response(200, json={"access_token": "tok-abc", "expires_in": 3600})
        return httpx.Response(401, json={})

    mods = project(handler, security=OAUTH)
    with pytest.raises(mods["client"].ApiError, match="OAUTH_CLIENT_ID") as info:
        mods["client"].request("GET", "/pets")
    assert "HTTP 401" in str(info.value)


def test_rate_limit_reports_retry_after(project) -> None:
    mods = project(_reply(429, headers={"retry-after": "7"}, json={}))
    with pytest.raises(mods["client"].ApiError, match="rate limited; retry after 7 seconds"):
        mods["client"].request("GET", "/pets")


def test_upstream_detail_is_included_and_trimmed(project) -> None:
    mods = project(_reply(400, json={"message": "status must be one of available, sold"}))
    with pytest.raises(mods["client"].ApiError, match="status must be one of available, sold"):
        mods["client"].request("GET", "/pets")
    long = "z" * 5000
    mods = project(_reply(422, text=long))
    with pytest.raises(mods["client"].ApiError) as info:
        mods["client"].request("GET", "/pets")
    assert len(str(info.value)) < 600


def test_timeouts_and_unreachable_hosts(project) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mods = project(timeout)
    with pytest.raises(mods["client"].ApiError, match="did not respond within 30 seconds"):
        mods["client"].request("GET", "/pets")
    mods = project(refused)
    with pytest.raises(mods["client"].ApiError, match="could not reach the API") as info:
        mods["client"].request("GET", "/pets")
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True


def test_oauth_token_failures_are_actionable_and_secret_free(project, monkeypatch) -> None:
    monkeypatch.setenv("OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "csecret-value")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client: csecret-value rejected"})

    mods = project(handler, security=OAUTH)
    with pytest.raises(mods["client"].ApiError) as info:
        mods["client"].request("GET", "/pets")
    message = str(info.value)
    assert "OAuth2 token request failed: HTTP 401" in message
    assert "OAUTH_CLIENT_ID" in message
    assert "csecret-value" not in message

    mods = project(_reply(200, json={"token_type": "bearer"}), security=OAUTH)
    with pytest.raises(mods["client"].ApiError, match="no access_token"):
        mods["client"].request("GET", "/pets")

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mods = project(refused, security=OAUTH)
    with pytest.raises(mods["client"].ApiError, match="could not reach the OAuth2 token URL"):
        mods["client"].request("GET", "/pets")


# --------------------------------------------------------------------- redaction


def test_api_key_echoed_by_the_api_is_redacted(project, monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "k-live-998877")
    mods = project(_reply(400, json={"message": "bad key k-live-998877"}), security=API_KEY_HEADER)
    with pytest.raises(mods["client"].ApiError) as info:
        mods["client"].request("GET", "/pets")
    assert "k-live-998877" not in str(info.value)
    assert "bad key ***" in str(info.value)


def test_secret_arguments_are_redacted_everywhere(project) -> None:
    mods = project(_reply(404, json={"message": "no user hunter 2!"}))
    with pytest.raises(mods["client"].ApiError) as info:
        mods["client"].request("GET", "/users/hunter%202%21", secrets=("hunter 2!", None, ""))
    message = str(info.value)
    assert "hunter" not in message
    assert "GET /users/*** failed" in message


def test_flagged_tools_pass_their_secret_arguments(project) -> None:
    files = {
        e.path: e.content
        for e in generate_python_mcp_result(
            PETSTORE, ["loginUser", "deletePet", "getPetById"], package_name=PKG
        ).tree.entries
    }
    server = files[f"src/{PKG}/server.py"]
    assert "params=payload or None, secrets=(password,))" in server
    assert "secrets=(api_key,)" in server
    get_pet = server.split("def getpetbyid(")[1].split("@mcp.tool")[0]
    assert "secrets" not in get_pet


def test_tool_errors_reach_the_model_redacted(project, monkeypatch) -> None:
    import asyncio

    mods = project(_reply(400, json={"message": "wrong password s3cr3t-pw"}), spec=PETSTORE)
    monkeypatch.setenv("API_BASE_URL", API)
    with pytest.raises(Exception) as info:
        asyncio.run(
            mods["server"].mcp.call_tool("loginuser", {"username": "bob", "password": "s3cr3t-pw"})
        )
    assert "s3cr3t-pw" not in str(info.value)
    assert "wrong password ***" in str(info.value)


def test_security_notes_say_errors_are_redacted() -> None:
    files = {
        e.path: e.content
        for e in generate_python_mcp_result(PETSTORE, ["loginUser"], package_name=PKG).tree.entries
    }
    assert "keeps secrets out of its own error messages" in files["SECURITY-NOTES.md"]


# --------------------------------------------------------------------- verify.main()


def _verify_main(mods: dict, monkeypatch, exc: BaseException, capsys) -> tuple[int, dict]:
    def boom() -> dict:
        raise exc

    monkeypatch.setattr(mods["verify"], "build_report", boom)
    code = mods["verify"].main()
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(
    ("exc", "error", "hint"),
    [
        (
            ModuleNotFoundError("No module named 'httpx'", name="httpx"),
            "cannot import httpx",
            "pip install -r requirements.txt",
        ),
        (
            ModuleNotFoundError(
                "No module named 'mcp.server.mcpserver'", name="mcp.server.mcpserver"
            ),
            "cannot import mcp.server.mcpserver",
            "mcp>=2.0.0,<3",
        ),
        (
            ImportError("cannot import name 'MCPServer'"),
            "cannot import name 'MCPServer'",
            "pip install -r requirements.txt",
        ),
        (
            SyntaxError("invalid syntax", ("server.py", 12, 1, "def (", 12, 2)),
            "server.py line 12: invalid syntax",
            "regenerate",
        ),
        (
            TypeError("bad annotation"),
            "a tool definition is invalid: bad annotation",
            "regenerate",
        ),
    ],
)
def test_verify_main_gives_actionable_diagnostics(
    project, monkeypatch, capsys, exc, error: str, hint: str
) -> None:
    code, report = _verify_main(project(), monkeypatch, exc, capsys)
    assert code == 1
    assert report["ok"] is False
    assert error in report["error"]
    assert hint in report["hint"]
    assert report["toolCount"] == 0


def test_verify_main_does_not_hide_unexpected_errors(project, monkeypatch) -> None:
    mods = project()

    def boom() -> dict:
        raise LookupError("a bug")

    monkeypatch.setattr(mods["verify"], "build_report", boom)
    with pytest.raises(LookupError):
        mods["verify"].main()


def test_verify_main_still_passes(project, monkeypatch, capsys) -> None:
    mods = project()
    monkeypatch.chdir(mods["root"])
    assert mods["verify"].main() == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_env_example_and_readme_document_the_limits() -> None:
    files = {
        e.path: e.content
        for e in generate_python_mcp_result(IR, None, package_name=PKG).tree.entries
    }
    assert "# MAX_RESPONSE_BYTES=1048576\n" in files[".env.example"]
    readme = files["README.md"]
    assert "## Errors and limits" in readme
    assert "MAX_RESPONSE_BYTES" in readme


@pytest.mark.parametrize(
    ("spec", "operations"),
    [(IR, None), (PETSTORE, None), (PETSTORE, ["logoutUser"]), (PETSTORE, ["findPetsByTags"])],
)
def test_generated_python_has_no_unused_imports_or_undefined_names(
    tmp_path: Path, spec: Any, operations: list[str] | None
) -> None:
    import subprocess

    generate_python_mcp(spec, operations, out_dir=tmp_path, package_name=PKG)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--select",
            "F",
            str(tmp_path / "src"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_server_imports_with_no_tools() -> None:
    from mcp_wizard_generators.plugins.python_mcp.plugin import PythonMcpPlugin

    imports = PythonMcpPlugin._server_imports("# No operations selected.")
    assert imports.split("\n") == [
        "import sys",
        "                ",
        "                from mcp.server.mcpserver import MCPServer",
    ]
