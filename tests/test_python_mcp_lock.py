"""Reproducible, auditable installs for generated python-mcp projects (ADR-023).

Every zip ships ``requirements.lock``: a universal lock with sha256 hashes,
prepared in this repo (the worker has no network) from ``requirements.in``.
Direct dependencies carry upper bounds everywhere they are declared.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from mcp_wizard_generators.python_mcp import generate_python_mcp_result

ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = ROOT / "src" / "mcp_wizard_generators" / "plugins" / "python_mcp" / "lock"
IR = json.loads((ROOT / "tests" / "fixtures" / "pets_tiny.ir.json").read_text(encoding="utf-8"))


def _direct() -> list[Requirement]:
    lines = (LOCK_DIR / "requirements.in").read_text(encoding="utf-8").splitlines()
    return [Requirement(line) for line in lines if line.strip() and not line.startswith("#")]


def _pins() -> dict[str, tuple[str, list[str]]]:
    """name -> (version, hashes) from the packaged lock."""
    text = (LOCK_DIR / "requirements.txt").read_text(encoding="utf-8")
    pins: dict[str, tuple[str, list[str]]] = {}
    for block in re.split(r"\n(?=[a-z0-9])", text):
        match = re.match(r"([A-Za-z0-9_.-]+)==([^\s;]+)", block)
        if match:
            pins[match.group(1).lower()] = (
                match.group(2),
                re.findall(r"--hash=sha256:(\w+)", block),
            )
    return pins


def _files() -> dict[str, str]:
    result = generate_python_mcp_result(IR, None, package_name="pets_tiny_mcp")
    return {e.path: e.content for e in result.tree.entries}


def test_direct_dependencies_have_upper_bounds() -> None:
    reqs = {r.name: r for r in _direct()}
    assert set(reqs) == {"mcp", "httpx", "pydantic"}
    for req in reqs.values():
        assert any(spec.operator == "<" for spec in req.specifier), req
    assert str(reqs["httpx"].specifier) == "<1,>=0.28"


def test_every_pin_has_hashes_and_direct_pins_fit_their_ranges() -> None:
    pins = _pins()
    assert len(pins) > 10  # transitive dependencies are locked too
    for name, (_, hashes) in pins.items():
        assert hashes, f"{name} has no hash"
        assert all(len(h) == 64 for h in hashes)
    for req in _direct():
        version, _ = pins[req.name]
        assert req.specifier.contains(version), (req, version)


def test_lock_is_universal() -> None:
    text = (LOCK_DIR / "requirements.txt").read_text(encoding="utf-8")
    # Windows-only and non-Windows dependencies both appear, behind markers.
    assert "sys_platform == 'win32'" in text
    assert "python scripts/lock_python_mcp.py" in text


def test_generated_requirements_and_pyproject_match_requirements_in() -> None:
    files = _files()
    direct = [str(r) for r in _direct()]
    declared = [line for line in files["requirements.txt"].splitlines() if line]
    assert [str(Requirement(line)) for line in declared] == direct
    project = tomllib.loads(files["pyproject.toml"])["project"]
    assert [str(Requirement(d)) for d in project["dependencies"]] == direct


def test_zip_ships_the_lock() -> None:
    lock = _files()["requirements.lock"]
    header, body = lock.split("\n\n", 1)
    assert "prepared by mcp-wizard-generators" in header
    assert "pip install --require-hashes -r requirements.lock" in header
    packaged = (LOCK_DIR / "requirements.txt").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert body == packaged[packaged.index("\nannotated-types") + 1 :]
    assert "lock_python_mcp" not in lock  # no repo-internal commands in the zip


def test_readme_documents_the_locked_install() -> None:
    readme = _files()["README.md"]
    assert "pip install --require-hashes -r requirements.lock" in readme
    assert "pip install --no-deps -e ." in readme
    assert "requirements.lock" in readme.split("## Layout", 1)[1]


def test_lock_script_builds_the_documented_command() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "lock_script", ROOT / "scripts" / "lock_python_mcp.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cmd = module.compile_command(Path("out.txt"))
    assert cmd[-2:] == ["--output-file", "out.txt"]
    assert {"--universal", "--generate-hashes"} <= set(cmd)
    assert cmd[cmd.index("--python-version") + 1] == "3.11"
    assert module.compile_command(Path("o"), upgrade=True)[-1] == "--upgrade"


@pytest.mark.parametrize("path", ["lock/requirements.in", "lock/requirements.txt"])
def test_lock_files_are_package_data(path: str) -> None:
    from importlib.resources import files

    assert files("mcp_wizard_generators.plugins.python_mcp").joinpath(path).is_file()


def test_ci_checks_lock_drift_and_runs_e2e() -> None:
    quality = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    assert "python scripts/lock_python_mcp.py --check" in quality
    assert "python -m pytest -m e2e" in quality


def test_dependabot_watches_the_lock() -> None:
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert 'directory: "/src/mcp_wizard_generators/plugins/python_mcp/lock"' in config


def test_make_lock_target_and_uv_dev_dependency() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "\nlock:\n\tpython scripts/lock_python_mcp.py\n" in makefile
    dev = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(d.startswith("uv") for d in dev["project"]["optional-dependencies"]["dev"])


def test_e2e_installs_from_the_lock_with_hashes() -> None:
    harness = (ROOT / "tests" / "e2e_harness.py").read_text(encoding="utf-8")
    assert '"--require-hashes"' in harness
    assert '"--no-deps"' in harness
