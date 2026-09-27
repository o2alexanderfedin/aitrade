"""Tests for data.store.load_curated -- the default loader resolves a
manifest_id to verified paths, never a glob, never a `latest` path."""

from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl
import pytest

from data.store import (
    DQPauseError,
    ManifestHashMismatch,
    dq_report_path,
    issue_manifest,
    load_curated,
)


def _write_ok_dq_report(
    lake_root: Path, symbol: str, stream: str, date: str, manifest_id: str
) -> None:
    """Minimal report.parquet fixture giving (symbol, stream, date) an
    "ok" DQ status -- required since Plan 04 wired DQPauseError's
    fail-closed-on-missing-report check into load_curated (a date with no
    report.parquet at all now pauses); see tests/dq/test_pause_enforcement.py
    for the pause behavior itself."""
    path = dq_report_path(lake_root, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "date": [date],
            "symbol": [symbol],
            "stream": [stream],
            "manifest_id": [manifest_id],
            "check": ["gap_coverage"],
            "dq_status": ["ok"],
            "value": [0.0],
            "count": [None],
            "detail": [None],
        },
        schema={
            "date": pl.Utf8,
            "symbol": pl.Utf8,
            "stream": pl.Utf8,
            "manifest_id": pl.Utf8,
            "check": pl.Utf8,
            "dq_status": pl.Utf8,
            "value": pl.Float64,
            "count": pl.Int64,
            "detail": pl.Utf8,
        },
    ).write_parquet(path, compression="zstd")


def _write_partition(lake_root: Path, rel_path: str, df: pl.DataFrame) -> dict:
    final_path = lake_root / rel_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(final_path, compression="zstd")
    st = final_path.stat()
    return {
        "date": "2026-09-12",
        "path": rel_path,
        "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
        "rows": df.height,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": int(df["etime"].min()),
        "etime_max": int(df["etime"].max()),
    }


def test_load_curated_returns_concatenated_verified_rows(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"

    df1 = pl.DataFrame(
        {"trade_id": [1, 2], "etime": [1_000, 2_000], "price": [1.0, 2.0]}
    )
    df2 = pl.DataFrame(
        {"trade_id": [3, 4], "etime": [3_000, 4_000], "price": [3.0, 4.0]}
    )
    part1 = _write_partition(lake_root, "curated/part-1.parquet", df1)
    part2 = _write_partition(lake_root, "curated/part-2.parquet", df2)
    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part1, part2],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    _write_ok_dq_report(
        lake_root, "BTCUSDT", "trade", "2026-09-12", manifest["manifest_id"]
    )

    loaded = load_curated(
        manifest["manifest_id"],
        "BTCUSDT.trade",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert loaded.height == 4
    assert sorted(loaded["trade_id"].to_list()) == [1, 2, 3, 4]


def test_load_curated_raises_on_hash_mismatch_never_returns_wrong_data(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    df = pl.DataFrame({"trade_id": [1], "etime": [1_000], "price": [1.0]})
    part = _write_partition(lake_root, "curated/part-1.parquet", df)
    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
    )

    on_disk_path = lake_root / part["path"]
    with open(on_disk_path, "ab") as f:
        f.write(b"\x00")

    with pytest.raises(ManifestHashMismatch):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )


def _write_two_manifest_dq_report(
    lake_root: Path,
    symbol: str,
    stream: str,
    date: str,
    *,
    ok_manifest_id: str,
    failed_manifest_id: str,
) -> None:
    """IN-01 (06-REVIEW.md): one `report.parquet` carrying TWO manifests'
    row sets for the SAME (date, symbol, stream) -- exactly the shape
    `data.dq.report.build_feature_report_rows_for_date` produces for a
    date covered by more than one manifest (e.g. a rebuild). One row
    scores "ok" for `ok_manifest_id`, the other "failed" for
    `failed_manifest_id` -- if `_dq_verdict_for_date` ever mixed the two
    up, either the ok manifest would wrongly pause or the failed one
    would wrongly load."""
    path = dq_report_path(lake_root, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "date": [date, date],
            "symbol": [symbol, symbol],
            "stream": [stream, stream],
            "manifest_id": [ok_manifest_id, failed_manifest_id],
            "check": ["gap_coverage", "gap_coverage"],
            "dq_status": ["ok", "failed"],
            "value": [0.0, 0.0],
            "count": [None, None],
            "detail": [None, "synthetic IN-01 regression fixture"],
        },
        schema={
            "date": pl.Utf8,
            "symbol": pl.Utf8,
            "stream": pl.Utf8,
            "manifest_id": pl.Utf8,
            "check": pl.Utf8,
            "dq_status": pl.Utf8,
            "value": pl.Float64,
            "count": pl.Int64,
            "detail": pl.Utf8,
        },
    ).write_parquet(path, compression="zstd")


def test_load_curated_scores_two_manifests_sharing_one_date_independently(
    tmp_path: Path,
):
    """IN-01 (06-REVIEW.md): `report.parquet`'s one-date-can-have-many-
    manifest-rows shape must never let one manifest's verdict leak into
    another's. Two manifests cover the SAME date with DIFFERENT partition
    content (so they get distinct manifest_ids); one is scored "ok", the
    other "failed", in the SAME report.parquet. Both directions are
    checked: the ok manifest must load cleanly (not poisoned by the
    other's failed row) and return its OWN rows (not the other's), and
    the failed manifest must pause (not masked by the other's ok row)."""
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"

    df_ok = pl.DataFrame({"trade_id": [1], "etime": [1_000], "price": [1.0]})
    df_failed = pl.DataFrame({"trade_id": [2], "etime": [2_000], "price": [2.0]})
    part_ok = _write_partition(lake_root, "curated/part-ok.parquet", df_ok)
    part_failed = _write_partition(lake_root, "curated/part-failed.parquet", df_failed)

    manifest_ok = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part_ok],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    manifest_failed = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part_failed],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    # Anti-vacuity: if these collided, the report fixture below could not
    # even express two distinct rows for "the same date".
    assert manifest_ok["manifest_id"] != manifest_failed["manifest_id"]

    _write_two_manifest_dq_report(
        lake_root,
        "BTCUSDT",
        "trade",
        "2026-09-12",
        ok_manifest_id=manifest_ok["manifest_id"],
        failed_manifest_id=manifest_failed["manifest_id"],
    )

    loaded = load_curated(
        manifest_ok["manifest_id"],
        "BTCUSDT.trade",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert loaded["trade_id"].to_list() == [1]  # its OWN partition, not the other's

    with pytest.raises(DQPauseError, match="failed"):
        load_curated(
            manifest_failed["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )


# --- 03-REVIEW-FOLLOWUPS.md WR-08: the loader must not need mlflow ---------


def _blockade_mlflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate an environment where `mlflow` is not installed, for code that
    imports it lazily. A `None` entry in `sys.modules` is exactly what the
    import machinery leaves when a module is absent, and it makes any later
    `import mlflow` raise `ImportError`."""
    import sys

    monkeypatch.delitem(sys.modules, "tracking.mlflow_utils", raising=False)
    monkeypatch.delitem(sys.modules, "mlflow", raising=False)
    monkeypatch.setitem(sys.modules, "mlflow", None)


def test_load_curated_works_without_mlflow_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`load_curated` gained a hard runtime dependency it did not have on
    `develop`: the lazy `from tracking.mlflow_utils import
    log_data_provenance` fires before anything has asked whether a run is
    even active, so reading data in an environment without mlflow raised
    `ModuleNotFoundError`."""
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    df = pl.DataFrame(
        {"trade_id": [1, 2], "etime": [1_000, 2_000], "price": [1.0, 2.0]}
    )
    part = _write_partition(lake_root, "curated/part-1.parquet", df)
    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    _write_ok_dq_report(
        lake_root, "BTCUSDT", "trade", "2026-09-12", manifest["manifest_id"]
    )

    _blockade_mlflow(monkeypatch)
    loaded = load_curated(
        manifest["manifest_id"],
        "BTCUSDT.trade",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert loaded.height == 2


def test_a_broken_tracking_module_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The guard must be narrow. Swallowing every `ImportError` would turn a
    real bug inside `tracking.mlflow_utils` -- a mistyped or missing import of
    some OTHER module -- into a silent skip of the provenance record.

    Near-miss worth keeping: the first version of this test put a fake module
    in `sys.modules` and had its `log_data_provenance` raise. The import then
    succeeded and the exception came from the CALL, outside the guard, so the
    test passed against a mutant that swallowed every `ImportError`. It now
    blocks a module `tracking.mlflow_utils` imports at module level, so the
    failure happens where the guard is."""
    import sys

    monkeypatch.delitem(sys.modules, "tracking.mlflow_utils", raising=False)
    monkeypatch.delitem(sys.modules, "tools.git_env", raising=False)
    monkeypatch.setitem(sys.modules, "tools.git_env", None)

    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    df = pl.DataFrame({"trade_id": [1], "etime": [1_000], "price": [1.0]})
    part = _write_partition(lake_root, "curated/part-1.parquet", df)
    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    _write_ok_dq_report(
        lake_root, "BTCUSDT", "trade", "2026-09-12", manifest["manifest_id"]
    )

    with pytest.raises(ImportError, match="git_env"):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )
