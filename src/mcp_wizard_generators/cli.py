"""CLI entry for mcp-wizard-api workers and local golden runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mcp_wizard_generators.models import (
    AUTH_MODES,
    CI_OPTIONS,
    DEPLOYMENT_TARGETS,
    IAC_OPTIONS,
    AgentRulesBundle,
    DeploymentConfig,
    InboundAuthConfig,
    IntermediateRep,
    SecurityConfig,
)
from mcp_wizard_generators.packaging import pack_file_tree_to_zip
from mcp_wizard_generators.paths import resolve_user_path
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.python_mcp import generate_python_mcp
from mcp_wizard_generators.registry import get_default_registry


def _path_type(value: str) -> Path:
    """Argparse type: expand ``~`` on macOS / Windows / Linux."""
    return resolve_user_path(value)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _add_deployment_args(parser: argparse.ArgumentParser) -> None:
    """Deployment target options (design ADR-026); the same on every generate command."""
    group = parser.add_argument_group("deployment (ADR-026)")
    group.add_argument(
        "--deployment",
        choices=DEPLOYMENT_TARGETS,
        default=None,
        help="Where the server runs (default: local)",
    )
    group.add_argument("--iac", choices=sorted(IAC_OPTIONS), default="none")
    group.add_argument("--ci", choices=sorted(CI_OPTIONS), default="none")
    group.add_argument("--scripts", action="store_true", help="Generate .sh + .ps1 scripts")
    group.add_argument("--aws-region", default="")
    group.add_argument(
        "--jwt-discovery-url",
        default="",
        help="OIDC discovery URL of the identity provider for MCP endpoint auth",
    )
    for flag, what in (("audience", "audience"), ("client", "client id"), ("scope", "scope")):
        group.add_argument(
            f"--jwt-{flag}",
            dest=f"jwt_{flag}s",
            action="append",
            default=[],
            help=f"Allowed token {what} (repeatable)",
        )
    group.add_argument(
        "--create-cognito",
        action="store_true",
        help="Create a Cognito user pool as the identity provider",
    )


def _deployment(args: argparse.Namespace) -> DeploymentConfig | None:
    """``None`` when no deployment option was given (keeps ``--target`` usable)."""
    jwt = bool(
        args.jwt_discovery_url
        or args.jwt_audiences
        or args.jwt_clients
        or args.jwt_scopes
        or args.create_cognito
    )
    given = (
        args.deployment is not None
        or args.iac != "none"
        or args.ci != "none"
        or args.scripts
        or bool(args.aws_region)
        or jwt
    )
    if not given:
        return None
    try:
        return DeploymentConfig(
            target=args.deployment or "local",
            iac=args.iac,
            ci=args.ci,
            scripts=args.scripts,
            aws_region=args.aws_region,
            inbound_auth=InboundAuthConfig(
                mode="jwt" if jwt else "none",
                discovery_url=args.jwt_discovery_url,
                allowed_audiences=tuple(args.jwt_audiences),
                allowed_clients=tuple(args.jwt_clients),
                allowed_scopes=tuple(args.jwt_scopes),
                create_cognito=args.create_cognito,
            ),
        )
    except ValueError as exc:
        raise SystemExit(f"invalid deployment options: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcp-wizard-gen",
        description="Generate an MCP project tree from an IR JSON snapshot (no server).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list-targets", help="List server architectures and deployment targets")
    list_p.set_defaults(func=cmd_list_targets)

    gen = sub.add_parser("generate", help="IR JSON → file tree and/or zip")
    gen.add_argument("--ir", required=True, type=_path_type, help="Path to IntermediateRep JSON")
    gen.add_argument(
        "--architecture",
        default=None,
        help="Server architecture (default: python-mcp)",
    )
    gen.add_argument(
        "--target",
        default=None,
        help="Deprecated single key ('local'); use --architecture and --deployment",
    )
    gen.add_argument("--language", default="python")
    gen.add_argument(
        "--select",
        action="append",
        default=None,
        help="Operation id to include (repeatable; default = all)",
    )
    gen.add_argument("--package-name", default="generated_mcp")
    gen.add_argument("--out-dir", type=_path_type, help="Write file tree to this directory")
    gen.add_argument("--zip", type=_path_type, help="Write zip archive to this path")
    gen.add_argument("--manifest", type=_path_type, help="Write generation manifest JSON")
    gen.add_argument("--auth-mode", default="none", choices=sorted(AUTH_MODES))
    gen.add_argument(
        "--security",
        type=_path_type,
        help="Experience API SecurityConfig JSON (authMode, apiKey, oauth); overrides --auth-mode",
    )
    gen.add_argument(
        "--rules-dir", type=_path_type, help="Optional directory of Agent Rules originals"
    )
    gen.add_argument(
        "--rules-family",
        default="unknown",
        choices=["cursor", "anthropic", "mixed", "unknown"],
    )
    _add_deployment_args(gen)
    gen.set_defaults(func=cmd_generate)

    py = sub.add_parser(
        "generate-python",
        help="Convenience: local Python MCP (mcp 2.x MCPServer) → out-dir and/or zip",
    )
    py.add_argument(
        "--ir",
        type=_path_type,
        help="Path to IntermediateRep JSON (or simple {title,servers,operations} JSON)",
    )
    py.add_argument(
        "--operations",
        type=_path_type,
        help="Optional JSON file: list of operation ids or operation objects",
    )
    py.add_argument(
        "--select",
        action="append",
        default=None,
        help="Operation id to include (repeatable; alternative to --operations)",
    )
    py.add_argument("--package-name", default="generated_mcp")
    py.add_argument("--out-dir", type=_path_type, help="Write file tree to this directory")
    py.add_argument("--zip", type=_path_type, help="Write zip archive to this path")
    py.add_argument("--auth-mode", default="none", choices=sorted(AUTH_MODES))
    py.add_argument(
        "--security",
        type=_path_type,
        help="Experience API SecurityConfig JSON (authMode, apiKey, oauth); overrides --auth-mode",
    )
    _add_deployment_args(py)
    py.set_defaults(func=cmd_generate_python)
    return p


def cmd_list_targets(_: argparse.Namespace) -> int:
    registry = get_default_registry()
    print("architectures:")
    for item in registry.list_architectures():
        print(f"  {item['key']}/{item['language']}\t{item['display_name']}")
    print("deployments:")
    for item in registry.list_deployments():
        print(f"  {item['key']}\t{item['display_name']}")
    return 0


def _load_rules(args: argparse.Namespace) -> AgentRulesBundle:
    if not args.rules_dir:
        return AgentRulesBundle()
    root = Path(args.rules_dir)
    originals: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file():
            originals[str(path.relative_to(root))] = path.read_text(encoding="utf-8")
    return AgentRulesBundle(format_family=args.rules_family, originals=originals)


def _security(args: argparse.Namespace) -> SecurityConfig:
    try:
        if args.security:
            return SecurityConfig.from_wizard(_load_json(args.security))
        return SecurityConfig(auth_mode=args.auth_mode)
    except ValueError as exc:
        raise SystemExit(f"invalid security config: {exc}") from exc


def cmd_generate(args: argparse.Namespace) -> int:
    ir = IntermediateRep.from_dict(_load_json(args.ir))
    deployment = _deployment(args)
    target = deployment.target if deployment else "local"
    try:
        result = generate(
            ir,
            target=args.target,
            architecture=args.architecture,
            deployment=deployment,
            language=args.language,
            selected_operation_ids=args.select,
            security=_security(args),
            agent_rules=_load_rules(args),
            package_name=args.package_name,
            # Agent Rules errors block only deployable output; local runs just report.
            fail_on_rules_errors=(args.target or target) != "local",
        )
    except (KeyError, ValueError) as exc:
        raise SystemExit(f"cannot generate: {exc}") from exc
    if args.out_dir:
        result.tree.write_to(args.out_dir)
    if args.zip:
        pack_file_tree_to_zip(result.tree, args.zip)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(result.manifest, indent=2) + "\n", encoding="utf-8")
    if not args.out_dir and not args.zip and not args.manifest:
        print(json.dumps(result.manifest, indent=2))
    return 0


def cmd_generate_python(args: argparse.Namespace) -> int:
    if not args.ir and not args.operations:
        raise SystemExit("generate-python requires --ir and/or --operations")
    if not args.out_dir and not args.zip:
        raise SystemExit("generate-python requires --out-dir and/or --zip")

    spec = _load_json(args.ir) if args.ir else {}
    operations: list | None = args.select
    if args.operations:
        loaded = json.loads(Path(args.operations).read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and "operations" in loaded and not args.ir:
            spec = loaded
            operations = args.select
        else:
            operations = loaded

    path = generate_python_mcp(
        spec,
        operations,
        out_dir=args.out_dir,
        zip_path=args.zip,
        package_name=args.package_name,
        security=_security(args),
        deployment=_deployment(args),
    )
    # Pathlib str() is OS-native; fine for humans and shell copy-paste.
    print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
