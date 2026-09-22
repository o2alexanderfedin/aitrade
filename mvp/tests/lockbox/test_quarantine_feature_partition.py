"""Tests for `data.lockbox.quarantine_feature_partition` (05-05-PLAN.md,
D-05-18/Q6) -- the second, and only other, operation in the whole codebase
permitted to join a path under `lockbox/`.

SANCTIONED (`tools/check_lockbox_containment.py:SANCTIONED_TEST_FILES`):
this file builds a READABLE (no `chmod 0000`) synthetic features partition
and asserts on the literal `lake/lockbox/...` shape the moved bytes land
under -- exactly the same posture `tests/lockbox/test_token_one_look.py`
takes for the token protocol, applied here to the move.

Hermetic: every fixture builds its own `tmp_path`-derived `lake_root`/
`registry_root` -- never the real, git-committed `mvp/data/lake_registry/`
or the real `/Volumes/ProjectsSSD/aihedgefund/lake/`. No MLflow tracking
root is needed here: unlike `open_lockbox`, `quarantine_feature_partition`
never touches MLflow at all.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import textwrap
from pathlib import Path

import pytest

from data.holdout import quarantined_dates, write_holdout_registry
from data.lockbox import QuarantineError, quarantine_feature_partition
from data.store import ManifestHashMismatch, resolve_manifest
from tests.fixtures.harness_span import build_span_partition

SYMBOL = "BTCUSDT"
CODE_HASH = "deadbeef"


def _build_fixture_date(
    tmp_path: Path, *, date: str = "2026-09-14", rows: int = 5
) -> tuple[Path, Path, dict]:
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    built = build_span_partition(
        lake_root,
        registry_root,
        date=date,
        start_ns=1_000_000_000,
        step_ns=1_000_000,
        rows=rows,
        code_hash=CODE_HASH,
    )
    return lake_root, registry_root, built


# --- the real move -----------------------------------------------------


def test_quarantine_feature_partition_moves_bytes_and_issues_a_lockbox_manifest(
    tmp_path: Path,
):
    lake_root, registry_root, built = _build_fixture_date(tmp_path)
    dataset = built["dataset"]

    original_manifest = resolve_manifest(
        built["manifest_id"],
        dataset,
        registry_root=registry_root,
        lake_root=lake_root,
        expected_tier="features",
    )
    original_entry = original_manifest["partitions"][0]
    original_path = lake_root / original_entry["path"]
    assert original_path.exists()  # fixture actually wrote a real file

    new_manifest = quarantine_feature_partition(
        "2026-09-14",
        symbol=SYMBOL,
        lake_root=lake_root,
        registry_root=registry_root,
        code_hash=CODE_HASH,
        reason="test",
    )

    # -- the new manifest names a lockbox-tier partition --
    assert new_manifest["dataset"] == dataset
    assert new_manifest["tier"] == "lockbox"
    assert new_manifest["stream"] == "features"
    new_entry = new_manifest["partitions"][0]
    assert new_entry["path"].startswith("lockbox/symbol=BTCUSDT/date=2026-09-14/")
    new_path = lake_root / new_entry["path"]
    assert new_path.exists()

    # -- independent, from-scratch proof the bytes are byte-identical (not
    # merely the same recorded sha256 field carried over by construction) --
    independent_sha256 = hashlib.sha256(new_path.read_bytes()).hexdigest()
    assert independent_sha256 == original_entry["sha256"]
    assert new_entry["sha256"] == original_entry["sha256"]
    assert new_entry["rows"] == original_entry["rows"]
    assert new_entry["etime_min"] == original_entry["etime_min"]
    assert new_entry["etime_max"] == original_entry["etime_max"]

    # -- a GENUINE move: the old path is gone, not merely superseded --
    assert not original_path.exists()

    # -- and the OLD manifest no longer resolves (the named residual) --
    with pytest.raises(ManifestHashMismatch):
        resolve_manifest(
            built["manifest_id"],
            dataset,
            registry_root=registry_root,
            lake_root=lake_root,
            expected_tier="features",
        )

    # -- provenance carried over, not severed --
    assert new_manifest["inputs"] == original_manifest["inputs"]


def _names_and_attrs(func) -> set[str]:
    """Every `Name`/`Attribute` identifier a function's CODE (never its
    docstring, which is a plain string Constant an AST Name/Attribute walk
    does not see) references -- a red-proof for "this function never calls
    `chmod`/`os.chmod`" that a plain substring search on `inspect.getsource`
    would false-positive on this module's own prose."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def test_quarantine_feature_partition_never_calls_chmod():
    import data.lockbox as lockbox_module

    identifiers = _names_and_attrs(
        lockbox_module.quarantine_feature_partition
    ) | _names_and_attrs(lockbox_module._lockbox_feature_partition_path)
    assert "chmod" not in identifiers


def test_quarantine_refuses_a_date_with_no_features_partition(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    with pytest.raises(QuarantineError, match="no features partition on record"):
        quarantine_feature_partition(
            "2099-01-01",
            symbol=SYMBOL,
            lake_root=lake_root,
            registry_root=registry_root,
            code_hash=CODE_HASH,
            reason="test",
        )


# --- the holdout.json writer --------------------------------------------


def test_holdout_writer_produces_the_schema_the_reader_parses(tmp_path: Path):
    registry_root = tmp_path / "registry"
    path = write_holdout_registry(
        ["2026-01-01"], reason="test", symbol=SYMBOL, registry_root=registry_root
    )
    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert on_disk["version"] == 1
    assert on_disk["symbol"] == SYMBOL
    assert on_disk["dates"] == ["2026-01-01"]
    assert isinstance(on_disk["locked_at"], int)
    assert on_disk["reason"] == "test"

    read_back = quarantined_dates(registry_root=registry_root, symbol=SYMBOL)
    assert read_back.declared is True
    assert set(read_back) == {"2026-01-01"}


def test_holdout_writer_refuses_to_overwrite_an_existing_registry(tmp_path: Path):
    registry_root = tmp_path / "registry"
    write_holdout_registry(
        ["2026-01-01"], reason="first", symbol=SYMBOL, registry_root=registry_root
    )
    with pytest.raises(ValueError, match="already exists"):
        write_holdout_registry(
            ["2026-01-02"], reason="second", symbol=SYMBOL, registry_root=registry_root
        )


def test_holdout_writer_refuses_a_malformed_date(tmp_path: Path):
    registry_root = tmp_path / "registry"
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        write_holdout_registry(
            ["not-a-date"], reason="test", symbol=SYMBOL, registry_root=registry_root
        )


def test_holdout_writer_never_calls_chmod():
    import data.holdout as holdout_module

    identifiers = _names_and_attrs(holdout_module.write_holdout_registry)
    assert "chmod" not in identifiers
