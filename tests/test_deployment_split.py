"""ADR-026: the server architecture and the deployment target are independent axes."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from mcp_wizard_generators.models import (
    AgentRulesBundle,
    DeploymentConfig,
    FileEntry,
    FileTree,
    GenerateContext,
    InboundAuthConfig,
    IntermediateRep,
    SecurityConfig,
    ServerCapabilities,
)
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.registry import GeneratorRegistry, get_default_registry

ROOT = Path(__file__).parent
IR = json.loads((ROOT / "fixtures" / "pets_tiny.ir.json").read_text(encoding="utf-8"))
DISCOVERY = "https://login.example.com/tenant/.well-known/openid-configuration"


def _files(**kwargs) -> dict[str, str]:
    result = generate(IntermediateRep.from_dict(IR), package_name="pets_tiny_mcp", **kwargs)
    return {e.path: e.content for e in result.tree.entries}


# ---- DeploymentConfig / InboundAuthConfig -------------------------------------------


def test_default_deployment_is_local_without_extras() -> None:
    cfg = DeploymentConfig()
    assert (cfg.target, cfg.iac, cfg.ci, cfg.scripts) == ("local", "none", "none", False)
    assert cfg.inbound_auth.mode == "none"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"target": "heroku"}, "deployment target"),
        ({"iac": "pulumi"}, "iac"),
        ({"ci": "jenkins"}, "ci"),
        ({"aws_region": "mars-1"}, "region"),
    ],
)
def test_deployment_config_rejects_unknown_values(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        DeploymentConfig(**kwargs)


def test_jwt_auth_accepts_any_oidc_provider() -> None:
    auth = InboundAuthConfig(mode="jwt", discovery_url=DISCOVERY, allowed_audiences=["api://pets"])
    assert auth.allowed_audiences == ("api://pets",)
    assert auth.manifest() == {"mode": "jwt", "identityProvider": "login.example.com"}


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"mode": "basic"}, "inbound auth mode"),
        ({"mode": "none", "discovery_url": DISCOVERY}, "mode is 'none'"),
        ({"mode": "jwt", "allowed_audiences": ["a"]}, "discovery_url"),
        (
            {
                "mode": "jwt",
                "discovery_url": "http://idp.example.com/.well-known/openid-configuration",
                "allowed_audiences": ["a"],
            },
            "discovery_url",
        ),
        (
            {"mode": "jwt", "discovery_url": "https://idp.example.com/jwks"},
            "discovery_url",
        ),
        ({"mode": "jwt", "discovery_url": DISCOVERY}, "audience, client or scope"),
        (
            {"mode": "jwt", "discovery_url": DISCOVERY, "allowed_clients": ['x"; evil']},
            "unsupported characters",
        ),
        (
            {"mode": "jwt", "discovery_url": DISCOVERY, "create_cognito": True},
            "create_cognito",
        ),
    ],
)
def test_inbound_auth_validation(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        InboundAuthConfig(**kwargs)


def test_cognito_is_optional_convenience() -> None:
    auth = InboundAuthConfig(mode="jwt", create_cognito=True)
    assert auth.manifest()["identityProvider"] == "cognito (created)"


# ---- FileTree.merge -----------------------------------------------------------------


def test_merge_refuses_collisions() -> None:
    base = FileTree(entries=[FileEntry("README.md", "a\n")], root_name="proj")
    with pytest.raises(ValueError, match="collision"):
        base.merge(FileTree(entries=[FileEntry("README.md", "b\n")]))


def test_merge_appends_only_when_asked() -> None:
    base = FileTree(entries=[FileEntry("README.md", "a")], root_name="proj")
    merged = base.merge(
        FileTree(entries=[FileEntry("README.md", "b\n"), FileEntry("Dockerfile", "x\n")]),
        append=frozenset({"README.md"}),
    )
    assert merged.root_name == "proj"
    assert merged.get("README.md").content == "a\nb\n"
    assert merged.paths() == ["Dockerfile", "README.md"]
    assert base.paths() == ["README.md"], "merge must not mutate the original tree"


# ---- registry -----------------------------------------------------------------------


def test_registry_has_python_mcp_server_and_local_deployment() -> None:
    reg = get_default_registry()
    server = reg.server("python-mcp", "python")
    assert server.key == "python-mcp"
    assert reg.server("local", "python") is server, "'local' stays an alias"
    assert reg.get("local", "python") is server, "get() stays for older callers"
    assert reg.deployment("local").key == "local"
    assert [a["key"] for a in reg.list_architectures()] == ["python-mcp"]
    assert "local" in [d["key"] for d in reg.list_deployments()]


def test_agentcore_stub_plugin_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("mcp_wizard_generators.plugins.agentcore")


def test_unknown_deployment_lists_registered_ones() -> None:
    with pytest.raises(KeyError, match="local"):
        get_default_registry().deployment("agentcore-container")


# ---- pipeline composition -----------------------------------------------------------


def test_default_and_local_aliases_produce_identical_trees() -> None:
    default = _files()
    assert _files(target="local") == default
    assert _files(architecture="python-mcp", deployment=DeploymentConfig()) == default
    manifest = json.loads(default["generation-manifest.json"])
    assert manifest["target"] == "local"


def test_target_and_architecture_are_exclusive() -> None:
    with pytest.raises(ValueError, match="not both"):
        _files(target="local", architecture="python-mcp")


def test_agentcore_target_alias_explains_the_replacement() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(ValueError, match="agentcore-container"):
            _files(target="agentcore")


def test_local_deployment_rejects_options_it_cannot_honor() -> None:
    with pytest.raises(ValueError, match="iac"):
        _files(deployment=DeploymentConfig(iac="terraform"))
    with pytest.raises(ValueError, match="inbound"):
        _files(
            deployment=DeploymentConfig(
                inbound_auth=InboundAuthConfig(
                    mode="jwt", discovery_url=DISCOVERY, allowed_audiences=["a"]
                )
            )
        )


class _NeedsWebsocket:
    key = "local"
    display_name = "needs websocket"
    requires = frozenset({"websocket"})
    iac_options = frozenset({"none"})
    ci_options = frozenset({"none"})
    supports_scripts = False
    supports_inbound_auth = False

    def validate(self, config: DeploymentConfig) -> None:
        pass

    def generate(self, ctx: GenerateContext, caps: ServerCapabilities) -> FileTree:
        raise AssertionError("must not run when the server lacks a required transport")


class _AddsFile(_NeedsWebsocket):
    requires = frozenset({"stdio"})
    seen: list[ServerCapabilities] = []

    def generate(self, ctx: GenerateContext, caps: ServerCapabilities) -> FileTree:
        self.seen.append(caps)
        return FileTree(entries=[FileEntry("deploy/NOTE.md", f"{caps.entrypoint}\n")])


def _registry_with(deployment) -> GeneratorRegistry:
    reg = GeneratorRegistry()
    reg.register_server(get_default_registry().server("python-mcp"))
    reg.register_deployment(deployment)
    return reg


def test_missing_transport_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="websocket"):
        _files(registry=_registry_with(_NeedsWebsocket()))


def test_deployment_reads_capabilities_and_adds_files() -> None:
    plugin = _AddsFile()
    files = _files(registry=_registry_with(plugin))
    assert files["deploy/NOTE.md"] == "pets_tiny_mcp.server:main\n"
    caps = plugin.seen[-1]
    assert caps.architecture == "python-mcp"
    assert caps.transports == frozenset({"stdio", "streamable-http"})
    assert caps.lock_files == ("requirements.lock",)
    names = {v.name for v in caps.env_vars}
    assert "API_BASE_URL" in names
    assert not caps.secret_env_vars, "no upstream auth means no secret env vars"


def test_capabilities_flag_upstream_secrets() -> None:
    plugin = _AddsFile()
    generate(
        IntermediateRep.from_dict(IR),
        package_name="pets_tiny_mcp",
        security=SecurityConfig(
            auth_mode="oauth2", oauth_token_url="https://idp.example.com/token"
        ),
        registry=_registry_with(plugin),
    )
    assert {v.name for v in plugin.seen[-1].secret_env_vars} == {
        "OAUTH_CLIENT_ID",
        "OAUTH_CLIENT_SECRET",
    }


def test_result_manifest_names_both_axes() -> None:
    result = generate(IntermediateRep.from_dict(IR), package_name="pets_tiny_mcp")
    gen = result.manifest["generator"]
    assert gen["architecture"] == "python-mcp"
    assert gen["deployment"] == "local"
    assert gen["target"] == "local"
    assert result.manifest["deployment"]["target"] == "local"


# ---- Agent Rules are embedded by the pipeline, for every target ----------------------


def test_agent_rules_are_copied_into_the_artifact() -> None:
    rules = AgentRulesBundle(
        format_family="mixed",
        originals={"CLAUDE.md": "# rules", "notes.txt": "hi\n", ".cursor/mcp.json": "{}"},
    )
    files = _files(agent_rules=rules, fail_on_rules_errors=False)
    assert files["CLAUDE.md"] == "# rules\n"
    assert files["agent-rules/notes.txt"] == "hi\n"
    # A rules file never replaces generated output; it moves under agent-rules/.
    assert files["agent-rules/.cursor/mcp.json"] == "{}\n"
    assert json.loads(files[".cursor/mcp.json"])["mcpServers"]


# ---- CLI ----------------------------------------------------------------------------


def test_cli_lists_architectures_and_deployments(capsys: pytest.CaptureFixture[str]) -> None:
    from mcp_wizard_generators.cli import main

    assert main(["list-targets"]) == 0
    out = capsys.readouterr().out
    assert "architectures:" in out and "python-mcp/python" in out
    assert "deployments:" in out and "local\t" in out


def test_cli_generate_with_deployment_flags(tmp_path: Path) -> None:
    from mcp_wizard_generators.cli import main

    ir = ROOT / "fixtures" / "pets_tiny.ir.json"
    args = ["generate", "--ir", str(ir), "--package-name", "pets_tiny_mcp"]
    assert main([*args, "--deployment", "local", "--out-dir", str(tmp_path / "new")]) == 0
    assert main([*args, "--target", "local", "--out-dir", str(tmp_path / "old")]) == 0
    new = sorted(p.relative_to(tmp_path / "new") for p in (tmp_path / "new").rglob("*"))
    old = sorted(p.relative_to(tmp_path / "old") for p in (tmp_path / "old").rglob("*"))
    assert new == old and new


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        (["--target", "agentcore"], "agentcore-container"),
        (["--target", "local", "--deployment", "local"], "not both"),
        (["--jwt-discovery-url", DISCOVERY, "--jwt-audience", "a"], "inbound auth"),
        (["--jwt-audience", "a"], "discovery_url"),
        (["--iac", "terraform"], "iac"),
    ],
)
def test_cli_reports_bad_deployment_options(extra: list[str], match: str) -> None:
    from mcp_wizard_generators.cli import main

    ir = ROOT / "fixtures" / "pets_tiny.ir.json"
    with pytest.raises(SystemExit, match=match):
        main(["generate", "--ir", str(ir), *extra])
