"""ADR-026 step 6a: servers can load upstream API credentials from AWS Secrets Manager.

AgentCore deployments switch on the ``aws-secrets`` server feature, so credentials never
sit in runtime environment variables, Terraform values or Terraform state.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
import types
from pathlib import Path

import pytest

from mcp_wizard_generators.models import (
    DeploymentConfig,
    FileEntry,
    FileTree,
    GenerateContext,
    IntermediateRep,
    SecurityConfig,
    ServerCapabilities,
)
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.registry import GeneratorRegistry, get_default_registry

ROOT = Path(__file__).parent
IR = json.loads((ROOT / "fixtures" / "pets_tiny.ir.json").read_text(encoding="utf-8"))
PKG = "pets_tiny_mcp"
LOCK_DIR = ROOT.parent / "src" / "mcp_wizard_generators" / "plugins" / "python_mcp" / "lock"
ARN = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:pets-api-AbCdEf"
OAUTH = SecurityConfig(auth_mode="oauth2", oauth_token_url="https://auth.example.com/token")


class _WantsSecrets:
    """A deployment that asks for the aws-secrets feature (the AgentCore targets do)."""

    key = "local"
    display_name = "wants aws secrets"
    requires = frozenset({"stdio"})
    iac_options = frozenset({"none"})
    ci_options = frozenset({"none"})
    supports_scripts = False
    supports_inbound_auth = False
    seen: list[ServerCapabilities] = []

    def validate(self, config: DeploymentConfig) -> None:
        pass

    def server_features(self, config: DeploymentConfig) -> frozenset[str]:
        return frozenset({"aws-secrets"})

    def generate(self, ctx: GenerateContext, caps: ServerCapabilities) -> FileTree:
        self.seen.append(caps)
        return FileTree(entries=[FileEntry("deploy/NOTE.md", "x\n")])


def _registry() -> GeneratorRegistry:
    reg = GeneratorRegistry()
    reg.register_server(get_default_registry().server("python-mcp"))
    reg.register_deployment(_WantsSecrets())
    return reg


def _result(security: SecurityConfig = OAUTH):
    return generate(
        IntermediateRep.from_dict(IR), package_name=PKG, security=security, registry=_registry()
    )


@pytest.fixture(scope="module")
def files() -> dict[str, str]:
    return {e.path: e.content for e in _result().tree.entries}


# ---- what the feature adds ----------------------------------------------------------


def test_feature_adds_the_loader_lock_and_extra(files: dict[str, str]) -> None:
    assert f"src/{PKG}/aws_secrets.py" in files
    lock = files["requirements-aws.lock"]
    assert "boto3==" in lock and "mcp==" in lock and "--hash=sha256:" in lock
    packaged = (LOCK_DIR / "requirements-aws.txt").read_text(encoding="utf-8").replace("\r\n", "\n")

    def body(text: str) -> list[str]:
        lines = text.splitlines()
        while lines and (lines[0].startswith("#") or not lines[0]):
            lines.pop(0)  # the zip swaps uv's header for one written for the project
        return lines

    assert body(lock) == body(packaged)
    assert "pip install --require-hashes -r requirements-aws.lock" in lock
    extras = tomllib.loads(files["pyproject.toml"])["project"]["optional-dependencies"]
    assert any(dep.startswith("boto3") for dep in extras["aws"])
    assert "requirements.lock" in files, "local development still uses the base lock"


def test_aws_lock_is_a_superset_with_identical_shared_pins() -> None:
    def pins(name: str) -> dict[str, str]:
        found: dict[str, str] = {}
        for line in (LOCK_DIR / name).read_text(encoding="utf-8").splitlines():
            if "==" in line and not line.startswith((" ", "#")):
                package, _, rest = line.partition("==")
                found[package] = rest.split()[0]
        return found

    base, aws = pins("requirements.txt"), pins("requirements-aws.txt")
    assert {k: aws[k] for k in base} == base
    assert {"boto3", "botocore"} <= set(aws) - set(base)


def test_capabilities_point_deployments_at_the_aws_lock() -> None:
    plugin = _WantsSecrets()
    _WantsSecrets.seen.clear()
    _result()
    caps = _WantsSecrets.seen[-1]
    assert caps.lock_files[0] == "requirements-aws.lock"
    assert "aws-secrets" in caps.features
    by_name = {v.name: v for v in caps.env_vars}
    assert "API_SECRETS_ARN" in by_name and not by_name["API_SECRETS_ARN"].secret
    assert plugin.key == "local"


def test_without_the_feature_nothing_changes() -> None:
    files = {
        e.path: e.content
        for e in generate(
            IntermediateRep.from_dict(IR), package_name=PKG, security=OAUTH
        ).tree.entries
    }
    assert f"src/{PKG}/aws_secrets.py" not in files
    assert "requirements-aws.lock" not in files
    assert "load_api_secrets" not in files[f"src/{PKG}/server.py"]


def test_server_loads_secrets_before_checking_config(files: dict[str, str]) -> None:
    server = files[f"src/{PKG}/server.py"]
    main = server[server.index("def main()") :]
    assert main.index("load_api_secrets()") < main.index("check_config()")
    assert "SecretsError" in main


def test_env_example_and_readme_document_it(files: dict[str, str]) -> None:
    assert "# API_SECRETS_ARN=" in files[".env.example"]
    assert "API_SECRETS_ARN" in files["README.md"]


# ---- loader behavior (boto3 faked; no AWS calls) ------------------------------------


def _loader(files: dict[str, str], tmp_path: Path) -> types.ModuleType:
    path = tmp_path / "secrets_mod.py"
    path.write_text(files[f"src/{PKG}/aws_secrets.py"], encoding="utf-8")
    name = f"gen_secrets_{abs(hash(str(tmp_path)))}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _ClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code, "Message": "no"}}


def _fake_boto3(monkeypatch: pytest.MonkeyPatch, *, secret: object = None, error: str = "") -> list:
    calls: list = []

    class Client:
        def get_secret_value(self, SecretId: str) -> dict:  # noqa: N803 - boto3 casing
            calls.append(SecretId)
            if error:
                raise _ClientError(error)
            return {"SecretString": secret if isinstance(secret, str) else json.dumps(secret)}

    boto3 = types.ModuleType("boto3")
    boto3.client = lambda service, region_name=None: (
        calls.append((service, region_name)) or Client()
    )
    botocore = types.ModuleType("botocore")
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = _ClientError
    exceptions.BotoCoreError = type("BotoCoreError", (Exception,), {})
    botocore.exceptions = exceptions
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exceptions)
    return calls


def _unset(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Unset, and restore afterwards even if the loader sets them (a plain
    delenv of an unset variable records nothing, so a loaded value would leak)."""
    for name in names:
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)


def test_no_arn_means_nothing_to_load(files, tmp_path, monkeypatch) -> None:
    secrets = _loader(files, tmp_path)
    monkeypatch.delenv("API_SECRETS_ARN", raising=False)
    assert secrets.load_api_secrets() == []


def test_loads_only_known_credentials_and_keeps_explicit_env(files, tmp_path, monkeypatch) -> None:
    secrets = _loader(files, tmp_path)
    calls = _fake_boto3(
        monkeypatch,
        secret={
            "OAUTH_CLIENT_ID": "id-from-secret",
            "OAUTH_CLIENT_SECRET": "s3cret-from-secret",
            "UNRELATED": "ignored",
        },
    )
    monkeypatch.setenv("API_SECRETS_ARN", ARN)
    monkeypatch.setenv("OAUTH_CLIENT_ID", "id-from-env")
    _unset(monkeypatch, "OAUTH_CLIENT_SECRET", "UNRELATED")
    loaded = secrets.load_api_secrets()
    assert loaded == ["OAUTH_CLIENT_SECRET"]
    import os

    assert os.environ["OAUTH_CLIENT_SECRET"] == "s3cret-from-secret"
    assert os.environ["OAUTH_CLIENT_ID"] == "id-from-env", "explicit env wins"
    assert "UNRELATED" not in os.environ
    assert ("secretsmanager", "eu-west-2") in calls, "region comes from the ARN"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"error": "AccessDeniedException"}, "not allowed to read"),
        ({"error": "ResourceNotFoundException"}, "does not exist"),
        ({"secret": "not json"}, "JSON object"),
        ({"secret": ["a", "b"]}, "JSON object"),
        ({"secret": {"OAUTH_CLIENT_SECRET": 5}}, "must be a string"),
    ],
)
def test_failures_are_explained_without_leaking_values(
    files, tmp_path, monkeypatch, kwargs: dict, match: str
) -> None:
    secrets = _loader(files, tmp_path)
    _fake_boto3(monkeypatch, **kwargs)
    monkeypatch.setenv("API_SECRETS_ARN", ARN)
    _unset(monkeypatch, "OAUTH_CLIENT_SECRET")
    with pytest.raises(secrets.SecretsError, match=match) as info:
        secrets.load_api_secrets()
    assert "s3cret" not in str(info.value)


def test_a_malformed_arn_is_refused(files, tmp_path, monkeypatch) -> None:
    secrets = _loader(files, tmp_path)
    monkeypatch.setenv("API_SECRETS_ARN", "pets-api")
    with pytest.raises(secrets.SecretsError, match="API_SECRETS_ARN"):
        secrets.load_api_secrets()


def test_generated_tests_pass_without_boto3_installed(tmp_path: Path) -> None:
    """The zip's own tests (tests/test_aws_secrets.py included) fake boto3."""
    project = tmp_path / "pets"
    _result(SecurityConfig(auth_mode="api_key")).tree.write_to(project)
    assert (project / "tests" / "test_aws_secrets.py").is_file()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
