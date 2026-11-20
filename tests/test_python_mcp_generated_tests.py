"""Generated projects ship their own tests (ADR-023) and those tests pass.

Each project gets ``tests/`` (pytest + ``httpx.MockTransport``, no network):
tool regression, authentication failures, HTTP status handling and credential
redaction. Here they run in a subprocess against freshly generated projects.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from mcp_wizard_generators.models import IntermediateRep
from mcp_wizard_generators.plugins.python_mcp.tests_template import SKIP, sample_value
from mcp_wizard_generators.python_mcp import generate_python_mcp, generate_python_mcp_result

ROOT = Path(__file__).parent
IR = IntermediateRep.from_dict(
    json.loads((ROOT / "fixtures" / "pets_tiny.ir.json").read_text(encoding="utf-8"))
)
PETSTORE = json.loads((ROOT / "fixtures" / "petstore3.openapi.json").read_text(encoding="utf-8"))
TEST_FILES = [
    "tests/conftest.py",
    "tests/test_auth.py",
    "tests/test_http_errors.py",
    "tests/test_redaction.py",
    "tests/test_tools.py",
    "tests/test_transport.py",
]

CONSTRAINED = {
    "openapi": "3.1.0",
    "info": {"title": "Constrained", "version": "1"},
    "paths": {
        "/codes/{code}": {
            "get": {
                "operationId": "byCode",
                "parameters": [
                    {
                        "name": "code",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "pattern": "^[A-Z]{3}$"},
                    }
                ],
            }
        },
        "/ranges": {
            "get": {
                "operationId": "ranges",
                "parameters": [
                    {
                        "name": "n",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "integer", "minimum": 10, "multipleOf": 4},
                    },
                    {
                        "name": "ratio",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "number", "exclusiveMinimum": 0, "maximum": 0.5},
                    },
                    {
                        "name": "ids",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "array", "items": {"type": "integer"}, "minItems": 2},
                    },
                    {
                        "name": "kind",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string", "enum": ["b", "a"]},
                    },
                    {
                        "name": "X-Token",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string", "minLength": 3},
                    },
                ],
            }
        },
    },
}


def _run_generated_tests(
    tmp_path: Path, spec: Any, security: dict | None, package: str
) -> subprocess.CompletedProcess[str]:
    project = tmp_path / package
    generate_python_mcp(spec, None, out_dir=project, package_name=package, security=security)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("API_", "OAUTH_", "MAX_"))}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


@pytest.mark.parametrize(
    ("spec", "security", "package"),
    [
        (IR, None, "none_mcp"),
        (IR, {"authMode": "api_key", "apiKey": {"location": "header", "name": "X-Key"}}, "hdr_mcp"),
        (IR, {"authMode": "api_key", "apiKey": {"location": "query", "name": "key"}}, "qry_mcp"),
        (IR, {"authMode": "oauth2", "oauth": {"tokenUrl": "https://auth.example.com/t"}}, "oa_mcp"),
        (PETSTORE, None, "petstore_tests_mcp"),
        (CONSTRAINED, None, "constrained_tests_mcp"),
    ],
)
def test_generated_tests_pass(
    tmp_path: Path, spec: Any, security: dict | None, package: str
) -> None:
    proc = _run_generated_tests(tmp_path, spec, security, package)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert " passed" in proc.stdout
    assert "failed" not in proc.stdout


def test_petstore_tests_cover_every_tool_and_the_flagged_one(tmp_path: Path) -> None:
    proc = _run_generated_tests(tmp_path, PETSTORE, None, "petstore_cover_mcp")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    assert "SKIPPED" not in out.split("short test summary info")[-1] or "pattern" in out
    src = {
        e.path: e.content
        for e in generate_python_mcp_result(PETSTORE, None, package_name="p_mcp").tree.entries
    }
    tools = src["tests/test_tools.py"]
    assert "'name': 'loginuser'" in tools
    assert "'name': 'findpetsbystatus'" in tools
    redaction = src["tests/test_redaction.py"]
    assert "'secret_arg': 'password'" in redaction


def test_unbuildable_samples_are_skipped_with_a_reason(tmp_path: Path) -> None:
    proc = _run_generated_tests(tmp_path, CONSTRAINED, None, "skip_reason_mcp")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cannot build a sample value for code" in proc.stdout


def test_zip_layout_readme_and_pyproject() -> None:
    files = {
        e.path: e.content
        for e in generate_python_mcp_result(IR, None, package_name="pets_tiny_mcp").tree.entries
    }
    for path in TEST_FILES:
        assert path in files, path
    project = tomllib.loads(files["pyproject.toml"])
    assert project["project"]["optional-dependencies"]["test"] == ["pytest>=8,<10"]
    assert project["tool"]["pytest"]["ini_options"] == {
        "testpaths": ["tests"],
        "pythonpath": ["src"],
    }
    readme = files["README.md"]
    assert "## Tests" in readme
    assert "python -m pytest" in readme
    assert "tests/" in readme.split("## Layout", 1)[1]


def test_security_notes_mention_the_tool_test_list() -> None:
    files = {
        e.path: e.content
        for e in generate_python_mcp_result(
            PETSTORE, ["loginUser"], package_name="p_mcp"
        ).tree.entries
    }
    assert "`TOOLS` in `tests/test_tools.py`" in files["SECURITY-NOTES.md"]


def test_generated_tests_lint_clean(tmp_path: Path) -> None:
    generate_python_mcp(PETSTORE, None, out_dir=tmp_path, package_name="lint_mcp")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--select",
            "F",
            str(tmp_path / "tests"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------- sample values


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string"}, "x"),
        ({"type": "string", "minLength": 3}, "xxx"),
        ({"type": "string", "minLength": 3, "maxLength": 2}, SKIP),
        ({"type": "string", "pattern": "^a$"}, SKIP),
        ({"type": "string", "pattern": "[bad"}, "x"),  # dropped by the annotation too
        ({"enum": [None, "on", "off"]}, "on"),
        ({"enum": [{"a": 1}]}, {"a": 1}),
        ({"type": "integer"}, 1),
        ({"type": "integer", "minimum": 10, "multipleOf": 4}, 12),
        ({"type": "integer", "minimum": 0, "exclusiveMinimum": True}, 1),
        ({"type": "integer", "exclusiveMinimum": 5}, 6),
        ({"type": "integer", "maximum": -3}, -3),
        ({"type": "integer", "exclusiveMaximum": 0}, -1),
        ({"type": "integer", "minimum": 5, "maximum": 4}, SKIP),
        ({"type": "integer", "minimum": True, "maximum": float("inf")}, 1),
        ({"type": "number", "exclusiveMinimum": 0, "maximum": 0.5}, 0.25),
        ({"type": "number", "minimum": 2.5}, 2.5),
        ({"type": "number", "multipleOf": 0.5, "minimum": 0.7}, 1.0),
        ({"type": "integer", "multipleOf": 0}, 1),
        ({"type": "integer", "minimum": 5, "maximum": 5, "exclusiveMaximum": True}, SKIP),
        ({"type": "number", "maximum": 0.5}, 0.5),
        ({"type": "number", "exclusiveMaximum": 1}, 0),
        ({"type": "number", "maximum": 5}, 1.0),
        ({"type": "number", "exclusiveMinimum": 0, "maximum": 0}, SKIP),
        ({"type": "boolean"}, True),
        ({"type": "array", "items": {"type": "integer"}, "minItems": 2}, [1, 1]),
        ({"type": "array"}, []),
        ({"type": "array", "minItems": 3, "maxItems": 1}, SKIP),
        ({"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "a"}}, SKIP),
        ({"type": "object"}, {}),
        ({"type": ["integer", "null"]}, 1),
        ({}, "x"),
        ("junk", "x"),
    ],
)
def test_sample_values_satisfy_the_annotation(schema: Any, expected: Any) -> None:
    assert sample_value(schema) == expected


def test_redaction_case_uses_the_first_argument_that_can_hold_a_secret() -> None:
    from mcp_wizard_generators.plugins.python_mcp.tests_template import tool_case

    props = {
        "pin": {"type": "string", "enum": ["1234"]},
        "short": {"type": "string", "maxLength": 4},
        "password": {"type": "string"},
    }
    names = {w: w for w in props}
    case, redaction = tool_case(
        name="t",
        method="post",
        path="/t",
        props=props,
        required=[],
        py_names=names,
        secret_wires=["pin", "short", "password"],
    )
    assert redaction == {"name": "t", "args": {}, "secret_arg": "password"}
    assert case["method"] == "POST"

    props["code"] = {"type": "string", "pattern": "^a$"}
    names["code"] = "code"
    case, redaction = tool_case(
        name="t",
        method="get",
        path="/t",
        props=props,
        required=["code"],
        py_names=names,
        secret_wires=["password"],
    )
    assert case["skip"] == "cannot build a sample value for code"
    assert redaction is None
