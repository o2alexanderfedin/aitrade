"""Tests for `harness.holdout_declare` (05-05-PLAN.md): `dry_run`'s
no-write-access guarantee, `declare`'s refusal/ordering, and the red-proof
that neither function ever calls `chmod`.

NOT a sanctioned lockbox test file -- this module drives the public names
(`dry_run`, `declare`) only and never spells a `lockbox/`-rooted path
itself; every date/manifest-id it asserts on came back from a function's
own return value.

Hermetic: every fixture builds its own `tmp_path`-derived `lake_root`/
`registry_root`, exactly `tests/harness/conftest.py`'s own pattern for the
rest of this directory. No MLflow tracking root is needed:
`quarantine_feature_partition`/`declare`/`dry_run` never touch MLflow.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import textwrap
from pathlib import Path

import pytest

import harness.holdout_declare as hd
from data.dates import next_utc_date, prev_utc_date
from data.holdout import quarantined_dates, write_holdout_registry
from tests.fixtures.harness_span import build_span_partition

SYMBOL = "BTCUSDT"
CODE_HASH = "deadbeef"
D_MINUS_1 = "2026-09-13"
D = "2026-09-14"


def _build_two_days(tmp_path: Path) -> tuple[Path, Path]:
    """Two adjacent, real, `FEATURE_ROW_SCHEMA`-shaped features partitions
    (`D_MINUS_1`, `D`) through the actual writer/manifest-issuer path."""
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    build_span_partition(
        lake_root,
        registry_root,
        date=D_MINUS_1,
        start_ns=1_000_000_000,
        step_ns=1_000_000,
        rows=5,
        code_hash=CODE_HASH,
    )
    build_span_partition(
        lake_root,
        registry_root,
        date=D,
        start_ns=2_000_000_000,
        step_ns=1_000_000,
        rows=5,
        code_hash=CODE_HASH,
    )
    return lake_root, registry_root


def _names_and_attrs(func) -> set[str]:
    """Every `Name`/`Attribute` identifier a function's CODE (never its
    docstring) references -- see `tests/lockbox/test_quarantine_feature_
    partition.py`'s twin helper for the rationale."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


# --- prev_utc_date (data/dates.py, a Rule 3 deviation this plan added) ---


def test_prev_utc_date_mirrors_next_utc_date_across_a_year_boundary():
    assert prev_utc_date("2027-01-01") == "2026-12-31"
    assert prev_utc_date(D) == D_MINUS_1
    assert next_utc_date(prev_utc_date(D)) == D
    assert prev_utc_date(next_utc_date(D)) == D


# --- dry_run: no write access needed -------------------------------------


def test_dry_run_lists_both_dates_and_needs_no_write_access(tmp_path: Path):
    lake_root, registry_root = _build_two_days(tmp_path)

    before = _snapshot(lake_root) | _snapshot(registry_root)

    mode = registry_root.stat().st_mode
    os.chmod(registry_root, 0o500)  # read-only: r-x, no write/create
    try:
        report = hd.dry_run(
            [D], symbol=SYMBOL, registry_root=registry_root, lake_root=lake_root
        )
    finally:
        os.chmod(registry_root, mode)

    reported_dates = {entry["date"] for entry in report["would_move"]}
    assert reported_dates == {D, D_MINUS_1}
    assert len(report["would_stop_resolving"]) == 2
    assert report["already_declared"] is False

    after = _snapshot(lake_root) | _snapshot(registry_root)
    assert before == after


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    return {
        str(p): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_dry_run_reports_not_built_for_a_date_with_nothing(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    report = hd.dry_run(
        ["2099-01-01"], symbol=SYMBOL, registry_root=registry_root, lake_root=lake_root
    )
    assert report["would_move"] == []
    assert report["would_stop_resolving"] == []
    assert report["already_declared"] is False


# --- declare: refusal, ordering, real move --------------------------------


def test_declare_refuses_if_already_declared(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    write_holdout_registry(
        ["2020-01-01"], reason="prior", symbol=SYMBOL, registry_root=registry_root
    )
    with pytest.raises(ValueError, match="already declared"):
        hd.declare(
            [D],
            symbol=SYMBOL,
            registry_root=registry_root,
            lake_root=lake_root,
            code_hash=CODE_HASH,
            reason="test",
        )


def test_declare_moves_both_dates_and_writes_holdout_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    lake_root, registry_root = _build_two_days(tmp_path)

    call_order: list[tuple[str, object]] = []
    real_quarantine = hd.quarantine_feature_partition
    real_write = hd.write_holdout_registry

    def _quarantine_spy(date, **kwargs):
        call_order.append(("quarantine", date))
        return real_quarantine(date, **kwargs)

    def _write_spy(dates, **kwargs):
        call_order.append(("write_holdout_registry", tuple(dates)))
        return real_write(dates, **kwargs)

    monkeypatch.setattr(hd, "quarantine_feature_partition", _quarantine_spy)
    monkeypatch.setattr(hd, "write_holdout_registry", _write_spy)

    result = hd.declare(
        [D],
        symbol=SYMBOL,
        registry_root=registry_root,
        lake_root=lake_root,
        code_hash=CODE_HASH,
        reason="v0 gate",
    )

    # -- both dates moved --
    assert len(result["moved"]) == 2
    moved_paths = {m["partitions"][0]["date"] for m in result["moved"]}
    assert moved_paths == {D, D_MINUS_1}
    d_dir = lake_root / "features" / f"symbol={SYMBOL}" / f"date={D}"
    d_minus_1_dir = lake_root / "features" / f"symbol={SYMBOL}" / f"date={D_MINUS_1}"
    assert list(d_dir.glob("part-*.parquet")) == []
    assert list(d_minus_1_dir.glob("part-*.parquet")) == []

    # -- holdout.json declares exactly the caller's own dates (D, not D-1) --
    on_disk = json.loads(result["holdout_registry_path"].read_text())
    assert on_disk["dates"] == [D]
    read_back = quarantined_dates(registry_root=registry_root, symbol=SYMBOL)
    assert read_back.declared is True
    assert set(read_back) == {D}

    # -- ORDER: both quarantine calls happen strictly before the registry
    # write (a crash between them leaves an undeclared-but-partially
    # -quarantined state, never declared-but-still-readable) --
    quarantine_indices = [i for i, c in enumerate(call_order) if c[0] == "quarantine"]
    write_indices = [
        i for i, c in enumerate(call_order) if c[0] == "write_holdout_registry"
    ]
    assert len(quarantine_indices) == 2
    assert len(write_indices) == 1
    assert max(quarantine_indices) < write_indices[0]


def test_declare_with_nothing_built_still_writes_holdout_json(tmp_path: Path):
    """D-05-16's ordinary Phase 8 case: `D_lock` is a synthetic future date
    with nothing built yet (and never has been) -- `declare` must still
    write `holdout.json`, moving nothing, rather than erroring on an
    all-empty candidate set."""
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    result = hd.declare(
        ["2099-01-01"],
        symbol=SYMBOL,
        registry_root=registry_root,
        lake_root=lake_root,
        code_hash=CODE_HASH,
        reason="forward declaration, nothing built",
    )
    assert result["moved"] == []
    on_disk = json.loads(result["holdout_registry_path"].read_text())
    assert on_disk["dates"] == ["2099-01-01"]


def test_neither_function_ever_calls_chmod():
    identifiers = (
        _names_and_attrs(hd.dry_run)
        | _names_and_attrs(hd.declare)
        | _names_and_attrs(hd._partition_status)
        | _names_and_attrs(hd._candidate_dates)
        | _names_and_attrs(hd._d_minus_1)
    )
    assert "chmod" not in identifiers
