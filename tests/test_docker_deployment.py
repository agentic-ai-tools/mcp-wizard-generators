"""ADR-026 step 4: the ``docker`` deployment target and server-side JWT checks."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from mcp_wizard_generators.models import (
    DeploymentConfig,
    InboundAuthConfig,
    IntermediateRep,
    SecurityConfig,
)
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.plugins.deploy.constants import PYTHON_IMAGE
from mcp_wizard_generators.registry import get_default_registry

ROOT = Path(__file__).parent
IR = json.loads((ROOT / "fixtures" / "pets_tiny.ir.json").read_text(encoding="utf-8"))
PKG = "pets_tiny_mcp"
DISCOVERY = "https://login.example.com/tenant/.well-known/openid-configuration"
JWT = InboundAuthConfig(
    mode="jwt",
    discovery_url=DISCOVERY,
    allowed_audiences=("api://pets",),
    allowed_clients=("cursor-client",),
)


def _files(deployment: DeploymentConfig, **kwargs) -> dict[str, str]:
    result = generate(
        IntermediateRep.from_dict(IR), package_name=PKG, deployment=deployment, **kwargs
    )
    return {e.path: e.content for e in result.tree.entries}


@pytest.fixture(scope="module")
def plain() -> dict[str, str]:
    return _files(DeploymentConfig(target="docker"))


@pytest.fixture(scope="module")
def secured() -> dict[str, str]:
    return _files(DeploymentConfig(target="docker", inbound_auth=JWT))


# ---- registry and options -----------------------------------------------------------


def test_docker_is_registered_and_needs_http() -> None:
    plugin = get_default_registry().deployment("docker")
    assert plugin.requires == frozenset({"streamable-http"})
    assert plugin.supports_inbound_auth


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"iac": "terraform"}, "iac"),
        ({"aws_region": "us-east-1"}, "aws_region"),
        ({"inbound_auth": InboundAuthConfig(mode="jwt", create_cognito=True)}, "Cognito"),
    ],
)
def test_docker_rejects_options_it_cannot_honor(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _files(DeploymentConfig(target="docker", **kwargs))


def test_local_still_rejects_inbound_auth() -> None:
    with pytest.raises(ValueError, match="inbound"):
        _files(DeploymentConfig(target="local", inbound_auth=JWT))


# ---- files: plain docker (no JWT) ---------------------------------------------------


def test_plain_docker_files(plain: dict[str, str]) -> None:
    for path in ("Dockerfile", ".dockerignore", "docker-compose.yml"):
        assert path in plain, path
    assert f"src/{PKG}/auth.py" not in plain


def test_dockerfile_is_pinned_hashed_and_non_root(plain: dict[str, str]) -> None:
    dockerfile = plain["Dockerfile"]
    assert re.fullmatch(r"python:3\.\d+-slim@sha256:[0-9a-f]{64}", PYTHON_IMAGE)
    assert dockerfile.count(f"FROM {PYTHON_IMAGE}") == 2, "build and runtime stages"
    assert "pip install --require-hashes -r requirements.lock" in dockerfile
    assert re.search(r"^USER \d+$", dockerfile, re.M), "numeric non-root user"
    assert "MCP_TRANSPORT=streamable-http" in dockerfile
    assert "MCP_HOST=0.0.0.0" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    # The image never decides auth: without MCP_AUTH the guard refuses 0.0.0.0.
    assert "MCP_AUTH=" not in dockerfile
    for line in dockerfile.splitlines():
        if line.startswith("COPY ") and "--from=" not in line:
            source = line.split()[1].rstrip("/")
            assert any(p == source or p.startswith(source + "/") for p in plain), line


def test_dockerignore_keeps_secrets_out_of_the_image(plain: dict[str, str]) -> None:
    ignored = plain[".dockerignore"].splitlines()
    for entry in (".env", ".venv", ".git"):
        assert entry in ignored


def test_plain_compose_publishes_only_on_loopback(plain: dict[str, str]) -> None:
    compose = yaml.safe_load(plain["docker-compose.yml"])
    service = compose["services"][PKG.replace("_", "-")]
    # Host port is MCP_HOST_PORT from .env (default 8000); always bound to loopback.
    assert service["ports"] == ["127.0.0.1:${MCP_HOST_PORT:-8000}:8000"]
    assert service["environment"]["MCP_AUTH"] == "platform"
    assert "read_only" in service and service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["env_file"] == [{"path": ".env", "required": False}]
    assert "127.0.0.1" in plain["docker-compose.yml"].split("MCP_AUTH")[0]


def test_readme_gets_a_docker_section(plain: dict[str, str]) -> None:
    readme = plain["README.md"]
    assert readme.count("# ") >= 1 and "## Run with Docker" in readme
    assert "docker compose up --build" in readme


# ---- files: docker with JWT ---------------------------------------------------------


def test_jwt_docker_ships_auth_module_and_declares_pyjwt(secured: dict[str, str]) -> None:
    import tomllib

    assert f"src/{PKG}/auth.py" in secured
    # mcp already depends on PyJWT[crypto], so the one shared lock pins it with hashes;
    # if mcp ever drops it, this fails and the lock needs the JWT packages added.
    lock = (
        ROOT.parent
        / "src"
        / "mcp_wizard_generators"
        / "plugins"
        / "python_mcp"
        / "lock"
        / "requirements.txt"
    ).read_text(encoding="utf-8")
    for package in ("pyjwt==", "cryptography==", "uvicorn=="):
        assert package in lock, package
    assert "pip install --require-hashes -r requirements.lock" in secured["Dockerfile"]
    extras = tomllib.loads(secured["pyproject.toml"])["project"]["optional-dependencies"]
    assert any(dep.lower().startswith("pyjwt[crypto]") for dep in extras["jwt"])


def test_jwt_compose_carries_the_idp_settings_not_secrets(secured: dict[str, str]) -> None:
    compose = yaml.safe_load(secured["docker-compose.yml"])
    env = compose["services"][PKG.replace("_", "-")]["environment"]
    assert env["MCP_AUTH"] == "jwt"
    assert env["MCP_JWT_DISCOVERY_URL"] == DISCOVERY
    assert env["MCP_JWT_AUDIENCES"] == "api://pets"
    assert env["MCP_JWT_CLIENTS"] == "cursor-client"
    assert env["MCP_JWT_SCOPES"] == ""


def test_capabilities_and_manifest_for_jwt(secured: dict[str, str]) -> None:
    manifest = json.loads(secured["generation-manifest.json"])
    assert manifest["target"] == "docker"
    env_example = secured[".env.example"]
    assert "# MCP_JWT_DISCOVERY_URL=" in env_example


def test_jwt_settings_are_validated_at_startup(secured: dict[str, str], tmp_path: Path) -> None:
    pytest.importorskip("jwt")
    auth = _load(secured, tmp_path, "auth")
    ok = auth.load_jwt_settings({"MCP_JWT_DISCOVERY_URL": DISCOVERY, "MCP_JWT_AUDIENCES": "a, b"})
    assert ok.audiences == ("a", "b")
    for env, match in [
        ({}, "MCP_JWT_DISCOVERY_URL"),
        ({"MCP_JWT_DISCOVERY_URL": "https://idp.example.com/jwks"}, "openid-configuration"),
        (
            {
                "MCP_JWT_DISCOVERY_URL": "http://idp.example.com/.well-known/openid-configuration",
                "MCP_JWT_AUDIENCES": "a",
            },
            "https",
        ),
        ({"MCP_JWT_DISCOVERY_URL": DISCOVERY}, "at least one"),
    ]:
        with pytest.raises(auth.AuthConfigError, match=match):
            auth.load_jwt_settings(env)


def test_transport_accepts_jwt_only_when_auth_is_built_in(
    plain: dict[str, str], secured: dict[str, str], tmp_path: Path
) -> None:
    public_jwt = {"MCP_TRANSPORT": "streamable-http", "MCP_HOST": "0.0.0.0", "MCP_AUTH": "jwt"}
    with_auth = _load(secured, tmp_path / "s", "transport")
    assert with_auth.load_transport_config(public_jwt).auth == "jwt"
    without = _load(plain, tmp_path / "p", "transport")
    with pytest.raises(without.TransportConfigError, match="not built into this server"):
        without.load_transport_config(public_jwt)


def _load(files: dict[str, str], tmp: Path, module: str) -> ModuleType:
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / f"{module}.py"
    path.write_text(files[f"src/{PKG}/{module}.py"], encoding="utf-8")
    name = f"gen_{module}_{abs(hash(str(tmp)))}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- a running JWT-protected server -------------------------------------------------


class _Idp:
    """Loopback OIDC provider: discovery document, JWKS, and token minting."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.key.public_key().public_numbers()

        def b64(value: int) -> str:
            raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        self.jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "k1",
                    "use": "sig",
                    "alg": "RS256",
                    "n": b64(numbers.n),
                    "e": b64(numbers.e),
                }
            ]
        }
        idp = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path.endswith("/.well-known/openid-configuration"):
                    body = {"issuer": idp.issuer, "jwks_uri": f"{idp.base}/jwks"}
                elif self.path == "/jwks":
                    body = idp.jwks
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.issuer = self.base
        self.discovery = f"{self.base}/.well-known/openid-configuration"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def token(self, **claims: object) -> str:
        import jwt

        now = int(time.time())
        body = {"iss": self.issuer, "aud": "api://pets", "exp": now + 300, "iat": now, **claims}
        return jwt.encode(body, self.key, algorithm="RS256", headers={"kid": "k1"})

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def idp() -> Iterator[_Idp]:
    pytest.importorskip("jwt")
    pytest.importorskip("cryptography")
    provider = _Idp()
    try:
        yield provider
    finally:
        provider.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def jwt_server(tmp_path: Path, idp: _Idp) -> Iterator[tuple[Path, str]]:
    pytest.importorskip("mcp")
    project = tmp_path / "pets"
    result = generate(
        IntermediateRep.from_dict(IR),
        package_name=PKG,
        deployment=DeploymentConfig(target="docker", inbound_auth=JWT),
    )
    result.tree.write_to(project)
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("MCP_")}
    env.update(
        PYTHONPATH=str(project / "src"),
        API_BASE_URL="https://api.example.com",
        MCP_TRANSPORT="streamable-http",
        MCP_PORT=str(port),
        MCP_AUTH="jwt",
        MCP_JWT_DISCOVERY_URL=idp.discovery,
        MCP_JWT_AUDIENCES="api://pets",
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", PKG],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            assert proc.poll() is None, proc.communicate()[1]
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                assert time.monotonic() < deadline, "server did not start"
                time.sleep(0.1)
        yield project, f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()


def _verify(project: Path, url: str, token: str | None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MCP_")}
    env["PYTHONPATH"] = str(project / "src")
    if token is not None:
        env["MCP_BEARER_TOKEN"] = token
    return subprocess.run(
        [sys.executable, "-m", f"{PKG}.verify", "--url", url],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def test_valid_token_is_served(jwt_server: tuple[Path, str], idp: _Idp) -> None:
    project, url = jwt_server
    proc = _verify(project, url, idp.token())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is True and report["toolCount"] == 3


@pytest.mark.parametrize(
    ("claims", "status"),
    [
        (None, 401),  # no token at all
        ({"aud": "api://other"}, 401),
        ({"exp": 1}, 401),
        ({"iss": "https://evil.example.com"}, 401),
    ],
)
def test_bad_tokens_are_refused(
    jwt_server: tuple[Path, str], idp: _Idp, claims: dict | None, status: int
) -> None:
    project, url = jwt_server
    token = None if claims is None else idp.token(**claims)
    proc = _verify(project, url, token)
    assert proc.returncode == 1
    assert str(status) in json.loads(proc.stdout)["error"]


def test_generated_jwt_tests_pass(tmp_path: Path) -> None:
    """The zip's own tests, including tests/test_jwt_auth.py, pass (no network)."""
    pytest.importorskip("jwt")
    pytest.importorskip("mcp")
    project = tmp_path / "pets"
    generate(
        IntermediateRep.from_dict(IR),
        package_name=PKG,
        deployment=DeploymentConfig(target="docker", inbound_auth=JWT),
        security=SecurityConfig(auth_mode="api_key"),
    ).tree.write_to(project)
    assert (project / "tests" / "test_jwt_auth.py").is_file()
    lint = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--select",
            "F",
            str(project / "src"),
            str(project / "tests"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---- the image itself (needs Docker and network; not part of `make test`) -----------


@pytest.mark.e2e
def test_docker_image_builds_and_serves_over_http(tmp_path: Path) -> None:
    if not shutil.which("docker"):
        pytest.skip("docker not installed")
    info = subprocess.run(
        ["docker", "info", "--format", "{{.OSType}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if info.returncode:
        pytest.skip("docker daemon not running")
    if info.stdout.strip() != "linux":
        # GitHub's Windows runners run Windows containers and cannot pull Linux images.
        pytest.skip(f"docker runs {info.stdout.strip() or 'unknown'} containers, not linux")
    project = tmp_path / "pets"
    generate(
        IntermediateRep.from_dict(IR),
        package_name=PKG,
        deployment=DeploymentConfig(target="docker"),
    ).tree.write_to(project)
    tag = "mcp-wizard-e2e-pets:test"
    subprocess.run(["docker", "build", "-t", tag, str(project)], check=True, timeout=900)
    port = _free_port()
    name = f"mcp-wizard-e2e-{port}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--read-only",
            "--cap-drop",
            "ALL",
            "-p",
            f"127.0.0.1:{port}:8000",
            "-e",
            "API_BASE_URL=https://api.example.com",
            "-e",
            "MCP_AUTH=platform",
            tag,
        ],
        check=True,
        timeout=120,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            proc = _verify(project, f"http://127.0.0.1:{port}/mcp", None)
            if proc.returncode == 0 or time.monotonic() > deadline:
                break
            time.sleep(1)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        # Without MCP_AUTH the image refuses to listen on 0.0.0.0.
        refused = subprocess.run(
            ["docker", "run", "--rm", "-e", "API_BASE_URL=https://api.example.com", tag],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert refused.returncode == 2
        assert "without authentication" in refused.stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
