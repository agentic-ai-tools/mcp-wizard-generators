"""Hostile OpenAPI input must never become code in a generated project.

Spec values (names, paths, titles, descriptions, server URLs) and agent-rule paths
are attacker-controlled once the wizard is hosted. Every emitted ``.py`` must parse,
no payload may survive outside a string constant, config files must stay well-formed,
and generated tools must URL-encode path arguments.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import io
import os
import subprocess
import sys
import tomllib
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from mcp_wizard_generators.models import (
    AgentRulesBundle,
    FileEntry,
    FileTree,
    IntermediateRep,
)
from mcp_wizard_generators.packaging.ziputil import pack_file_tree_to_zip
from mcp_wizard_generators.pipeline import generate
from mcp_wizard_generators.toolmap import build_tool_map

PKG = "hostile_mcp"
MARK = "PWNED"
# Creates a marker file if it ever executes.
TOUCH = f'__import__("pathlib").Path("{MARK}").touch()'

HOSTILE_IR: dict = {
    "title": f'Evil """\n{TOUCH}\n""" \\',
    "version": '1.0"\n[tool.evil]\nx = 1',
    "servers": [f"https://api.example.com/v1\n{MARK}_ENV=1"],
    "operations": [
        {
            "id": "op-request",
            "operationId": "request",
            "method": "get",
            "path": f'/items/{{item-id}}/x" + {TOUCH} + "',
            "summary": f'Say "hi" \\ """ {MARK}\x00 end"',
            "inputSchema": {
                "type": "object",
                "properties": {
                    f"x={TOUCH}": {"type": "string"},
                    "item-id": {"type": "string"},
                    "class": {"type": "integer"},
                    "path": {"type": "string"},
                    "request": {"type": "string"},
                    "user_id": {"type": "string"},
                    "user-id": {"type": "string"},
                    "9lives": {"type": "string"},
                },
                "required": ["item-id", "class"],
            },
        },
        {
            "id": "op-class",
            "operationId": "class",
            "method": "post",
            "path": "/t/{a.b[0]}",
            "summary": 'trailing quote"',
            "inputSchema": {
                "type": "object",
                "properties": {
                    "a.b[0]": {"type": "string"},
                    "flag": {"type": "boolean", "default": False, "nullable": True},
                },
                "required": ["a.b[0]"],
            },
        },
        {
            "id": "op-main",
            "operationId": "main",
            "method": "delete",
            "path": "/m",
            "summary": None,
            "description": "line one\r\nline two \u2028 sep",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ],
}


def _identifiers(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.FunctionDef):
            yield node.name
        elif isinstance(node, ast.arg):
            yield node.arg
        elif isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, ast.alias):
            yield node.name
            if node.asname:
                yield node.asname
        elif isinstance(node, ast.keyword) and node.arg:
            yield node.arg


def _files(target: str, **kwargs) -> dict[str, str]:
    result = generate(
        IntermediateRep.from_dict(HOSTILE_IR),
        target=target,
        language="python",
        package_name=PKG,
        **kwargs,
    )
    return {e.path: e.content for e in result.tree.entries}


@pytest.fixture(params=["local", "agentcore"])
def files(request: pytest.FixtureRequest) -> dict[str, str]:
    return _files(request.param)


def test_every_python_file_parses_and_payloads_stay_in_strings(files: dict[str, str]) -> None:
    py_files = {p: c for p, c in files.items() if p.endswith(".py")}
    assert py_files
    for path, content in py_files.items():
        tree = ast.parse(content, filename=path)
        # Sanitized identifiers may still contain the marker text (e.g. a parameter named
        # "x___import____pathlib…"); what must never appear is the payload's *code*.
        leaked = [name for name in _identifiers(tree) if name in {"__import__", "touch", MARK}]
        assert not leaked, f"{path}: payload became code: {leaked}"
        assert "\x00" not in content, f"{path}: raw NUL byte"


def test_pyproject_is_valid_toml_with_no_injected_tables(files: dict[str, str]) -> None:
    data = tomllib.loads(files["pyproject.toml"])
    assert set(data) == {"build-system", "project", "tool"}
    assert "hatch" in data["tool"] and set(data["tool"]) <= {"hatch", "pytest"}
    assert "Evil" in data["project"]["description"]


def test_env_example_has_no_injected_lines(files: dict[str, str]) -> None:
    for line in files[".env.example"].splitlines():
        assert not line or line.startswith("#") or line.split("=", 1)[0].isupper(), line
        assert not line.startswith(MARK), line


def test_readme_keeps_spec_text_on_one_line(files: dict[str, str]) -> None:
    lines = files["README.md"].splitlines()
    assert not any(line.startswith("__import__") for line in lines)
    assert not any(line.startswith(f"{MARK}_ENV") for line in lines)


def test_agentcore_tools_table_is_a_python_literal() -> None:
    tools_py = _files("agentcore")[f"src/{PKG}/tools.py"]
    tree = ast.parse(tools_py)
    assign = next(
        n
        for n in tree.body
        if isinstance(n, ast.Assign | ast.AnnAssign)
        and getattr(n.targets[0] if isinstance(n, ast.Assign) else n.target, "id", "") == "TOOLS"
    )
    tools = ast.literal_eval(assign.value)
    assert tools["class"]["inputSchema"]["properties"]["flag"]["default"] is False
    assert tools["class"]["inputSchema"]["properties"]["flag"]["nullable"] is True


# ---- generated local server behaves correctly ----------------------------------------


@pytest.fixture
def hostile_project(tmp_path: Path) -> Path:
    result = generate(
        IntermediateRep.from_dict(HOSTILE_IR),
        target="local",
        language="python",
        package_name=PKG,
    )
    out = tmp_path / "gen"
    result.tree.write_to(out)
    return out


def test_generated_server_imports_without_running_payloads(
    hostile_project: Path, tmp_path: Path
) -> None:
    env = {**os.environ, "PYTHONPATH": str(hostile_project / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", f"{PKG}.verify"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not list(tmp_path.rglob(MARK)), "payload executed"


@pytest.fixture
def server_module(hostile_project: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(hostile_project / "src"))
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]
    module = importlib.import_module(f"{PKG}.server")
    calls: list[tuple] = []

    def fake_request(method, path, *, params=None, json_body=None):
        calls.append((method, path, params, json_body))
        return {"ok": True}

    monkeypatch.setattr(module, "request", fake_request)
    module.calls = calls
    yield module
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]


def test_tool_functions_avoid_keywords_and_module_names(server_module) -> None:
    fn = server_module.request_
    params = list(inspect.signature(fn).parameters)
    # Required parameters come first so the signature is valid Python.
    assert params[:2] == ["item_id", "class_"]
    assert {"path_", "request_", "user_id", "user_id_", "p_9lives"} <= set(params)
    assert callable(server_module.class_)
    assert callable(server_module.main_)
    assert callable(server_module.main)  # the real entrypoint is not shadowed


def test_wire_names_and_encoded_path_arguments(server_module) -> None:
    server_module.request_(item_id="../admin", class_=1, user_id_="u", path_="p")
    method, path, params, _ = server_module.calls[-1]
    assert method == "GET"
    assert path.startswith("/items/..%2Fadmin/x")
    assert params == {"class": 1, "user-id": "u", "path": "p"}

    server_module.class_(**{"a_b_0_": "a/b"}, flag=True)
    method, path, _, body = server_module.calls[-1]
    assert (method, path, body) == ("POST", "/t/a%2Fb", {"flag": True})


# ---- tool names, methods, and file paths ---------------------------------------------


def test_tool_name_overrides_must_be_slugs() -> None:
    ir = IntermediateRep.from_dict(HOSTILE_IR)
    with pytest.raises(ValueError, match="tool name"):
        build_tool_map(ir, ["op-main"], {"op-main": "x(); import os"})


def test_unknown_http_methods_are_rejected() -> None:
    data = {**HOSTILE_IR, "operations": [{**HOSTILE_IR["operations"][2], "method": 'get"); x('}]}
    with pytest.raises(ValueError, match="HTTP method"):
        build_tool_map(IntermediateRep.from_dict(data), None)


@pytest.mark.parametrize(
    "path",
    ["../evil.py", "/etc/passwd", "C:/evil.py", "a/../../b", "a\\..\\..\\b", "", "a/\x00b"],
)
def test_file_entries_refuse_paths_outside_the_project(path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        FileEntry(path, "x")


def test_agent_rule_paths_cannot_escape_the_project() -> None:
    rules = AgentRulesBundle(format_family="cursor", originals={"../../evil.md": "x"})
    with pytest.raises(ValueError, match="path"):
        generate(
            IntermediateRep.from_dict(HOSTILE_IR),
            target="agentcore",
            language="python",
            package_name=PKG,
            agent_rules=rules,
        )


def test_zip_and_disk_writes_stay_inside_the_root(tmp_path: Path) -> None:
    tree = FileTree(entries=[FileEntry("src/ok.py", "x = 1\n")], root_name="proj")
    names = zipfile.ZipFile(io.BytesIO(pack_file_tree_to_zip(tree))).namelist()
    assert names == ["proj/src/ok.py"]
    tree.write_to(tmp_path / "out")
    assert (tmp_path / "out" / "src" / "ok.py").read_text(encoding="utf-8") == "x = 1\n"
