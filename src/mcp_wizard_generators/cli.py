"""CLI entry for mcp-wizard-api workers and local golden runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mcp_wizard_generators.models import AgentRulesBundle, IntermediateRep, SecurityConfig
from mcp_wizard_generators.packaging import pack_file_tree_to_zip
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.registry import get_default_registry


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcp-wizard-gen",
        description="Generate an MCP project tree from an IR JSON snapshot (no server).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list-targets", help="List registered generator plugins")
    list_p.set_defaults(func=cmd_list_targets)

    gen = sub.add_parser("generate", help="IR JSON → file tree and/or zip")
    gen.add_argument("--ir", required=True, type=Path, help="Path to IntermediateRep JSON")
    gen.add_argument("--target", default="agentcore")
    gen.add_argument("--language", default="python")
    gen.add_argument(
        "--select",
        action="append",
        default=None,
        help="Operation id to include (repeatable; default = all)",
    )
    gen.add_argument("--package-name", default="generated_mcp")
    gen.add_argument("--out-dir", type=Path, help="Write file tree to this directory")
    gen.add_argument("--zip", type=Path, help="Write zip archive to this path")
    gen.add_argument("--manifest", type=Path, help="Write generation manifest JSON")
    gen.add_argument("--auth-mode", default="none")
    gen.add_argument("--rules-dir", type=Path, help="Optional directory of Agent Rules originals")
    gen.add_argument("--rules-family", default="unknown", choices=["cursor", "anthropic", "mixed", "unknown"])
    gen.set_defaults(func=cmd_generate)
    return p


def cmd_list_targets(_: argparse.Namespace) -> int:
    for item in get_default_registry().list_targets():
        print(f"{item['key']}/{item['language']}\t{item['display_name']}")
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


def cmd_generate(args: argparse.Namespace) -> int:
    ir = IntermediateRep.from_dict(_load_json(args.ir))
    result = generate(
        ir,
        target=args.target,
        language=args.language,
        selected_operation_ids=args.select,
        security=SecurityConfig(auth_mode=args.auth_mode),
        agent_rules=_load_rules(args),
        package_name=args.package_name,
    )
    if args.out_dir:
        result.tree.write_to(args.out_dir)
    if args.zip:
        pack_file_tree_to_zip(result.tree, args.zip)
    if args.manifest:
        args.manifest.write_text(json.dumps(result.manifest, indent=2) + "\n", encoding="utf-8")
    if not args.out_dir and not args.zip and not args.manifest:
        print(json.dumps(result.manifest, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
