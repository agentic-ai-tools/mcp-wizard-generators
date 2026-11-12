"""Unit tests for scripts/compute_pep440_version.py (PEP 440 by branch kind)."""

from __future__ import annotations

import importlib.util
import zlib
from pathlib import Path
from typing import Any

import pytest
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compute_pep440_version.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("compute_pep440_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def compute() -> Any:
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    return _load_module()


def test_normalize_branch_lowercases_and_replaces_non_alnum(compute: Any) -> None:
    assert compute.normalize_branch("Feature/Foo_Bar.Baz") == "feature-foo-bar-baz"
    assert compute.normalize_branch("cursor/pypi-branch-versioning-6bd1") == (
        "cursor-pypi-branch-versioning-6bd1"
    )


def test_feature_dev_n_uses_crc32_band_and_run_number(compute: Any) -> None:
    branch = "feature/my-cool-thing"
    run = 42
    version = compute.compute_pep440_version(
        branch=branch,
        base_version="0.3.0",
        run_number=run,
        pyproject_version="0.3.0",
        init_version="0.3.0",
    )
    assert "+" not in version
    parsed = Version(version)
    assert parsed.is_devrelease
    assert version.startswith("0.3.0.dev")

    normalized = compute.normalize_branch(branch)
    band = (zlib.crc32(normalized.encode("utf-8")) % 90000) + 10000
    expected_n = band * 100000 + (run % 100000)
    assert version == f"0.3.0.dev{expected_n}"


def test_feature_republish_changes_n_with_run_number(compute: Any) -> None:
    kwargs = dict(
        branch="feat/x",
        base_version="0.3.0",
        pyproject_version="0.3.0",
        init_version="0.3.0",
    )
    v1 = compute.compute_pep440_version(run_number=1, **kwargs)
    v2 = compute.compute_pep440_version(run_number=2, **kwargs)
    assert v1 != v2
    assert Version(v1).is_devrelease and Version(v2).is_devrelease


def test_main_emits_rc_with_run_number(compute: Any) -> None:
    version = compute.compute_pep440_version(
        branch="main",
        base_version="0.3.0",
        run_number=17,
        pyproject_version="0.3.0",
        init_version="0.3.0",
    )
    assert version == "0.3.0rc17"
    assert "+" not in version
    parsed = Version(version)
    assert parsed.is_prerelease
    assert not parsed.is_devrelease


def test_release_branch_emits_final_matching_sources(compute: Any) -> None:
    version = compute.compute_pep440_version(
        branch="release/0.3.0",
        base_version="0.3.0",
        run_number=99,
        pyproject_version="0.3.0",
        init_version="0.3.0",
    )
    assert version == "0.3.0"
    parsed = Version(version)
    assert not parsed.is_prerelease
    assert not parsed.is_devrelease
    assert not parsed.is_postrelease


def test_release_rejects_mismatched_pyproject(compute: Any) -> None:
    with pytest.raises(SystemExit):
        compute.compute_pep440_version(
            branch="release/0.3.0",
            base_version="0.3.0",
            run_number=1,
            pyproject_version="0.2.0",
            init_version="0.3.0",
        )


def test_release_rejects_mismatched_init(compute: Any) -> None:
    with pytest.raises(SystemExit):
        compute.compute_pep440_version(
            branch="release/0.3.0",
            base_version="0.3.0",
            run_number=1,
            pyproject_version="0.3.0",
            init_version="0.2.9",
        )


def test_release_rejects_pre_release_pattern(compute: Any) -> None:
    with pytest.raises(SystemExit):
        compute.compute_pep440_version(
            branch="release/1.2.3.dev1",
            base_version="1.2.3",
            run_number=1,
            pyproject_version="1.2.3.dev1",
            init_version="1.2.3.dev1",
        )


def test_release_rejects_rc_in_branch_name(compute: Any) -> None:
    with pytest.raises(SystemExit):
        compute.compute_pep440_version(
            branch="release/1.0.0rc1",
            base_version="1.0.0",
            run_number=1,
            pyproject_version="1.0.0rc1",
            init_version="1.0.0rc1",
        )


def test_no_local_segment_in_any_kind(compute: Any) -> None:
    cases = [
        ("feature/foo", 3),
        ("main", 5),
        ("release/0.3.0", 1),
    ]
    for branch, run in cases:
        version = compute.compute_pep440_version(
            branch=branch,
            base_version="0.3.0",
            run_number=run,
            pyproject_version="0.3.0",
            init_version="0.3.0",
        )
        assert "+" not in version
        # packaging.Version rejects local when we never emit it; assert public form.
        assert Version(version).local is None


def test_cli_prints_single_line_version(
    compute: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "9")
    rc = compute.main(["--repo-root", str(ROOT)])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "0.3.0rc9"
    assert "\n" not in out
