"""Tests for data.dq.report -- the DQ report entrypoint. Hermetic:
tmp_path-rooted fixture lake + registry, never touching the real lake or
the real gap ledger (see tests/dq/test_pause_enforcement.py for the
real-data RP-4 cases)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

from data.dq.checks import load_dq_thresholds
from data.dq.report import (
    build_report_rows_for_date,
    build_stats_path,
    dq_report_markdown_path,
    dq_report_path,
    dq_resync_windows_path,
    main as report_main,
    normalize_row,
    write_report,
)
from data.store import issue_manifest
from tools.check_spec_diff import check_drift
from tools.git_env import scrubbed_git_env  # noqa: F401  (import-sanity; env used indirectly)

GAP_LEDGER_SCHEMA = {
    "stream": pl.Utf8,
    "conn_id": pl.Utf8,
    "gap_start_rtime": pl.Int64,
    "gap_end_rtime": pl.Int64,
    "cause": pl.Utf8,
    "detected_at": pl.Int64,
    "ledger_version": pl.Int32,
}

THRESHOLDS = load_dq_thresholds()
DATE = "2026-09-12"
SYMBOL = "BTCUSDT"


def _write_curated_partition(
    lake_root: Path, symbol: str, stream: str, date: str, df: pl.DataFrame
) -> dict:
    rel_path = f"curated/symbol={symbol}/stream={stream}/date={date}/part-1.parquet"
    final_path = lake_root / rel_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(final_path, compression="zstd")
    st = final_path.stat()
    return {
        "date": date,
        "path": rel_path,
        "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
        "rows": df.height,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": int(df["etime"].min()),
        "etime_max": int(df["etime"].max()),
    }


def _issue_and_write_build_stats(
    lake_root: Path,
    registry_root: Path,
    symbol: str,
    stream: str,
    date: str,
    df: pl.DataFrame,
    build_stats: dict | None,
) -> dict:
    part = _write_curated_partition(lake_root, symbol, stream, date, df)
    manifest = issue_manifest(
        dataset=f"{symbol}.{stream}",
        symbol=symbol,
        stream=stream,
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    if build_stats is not None:
        stats_path = build_stats_path(lake_root, symbol, stream, date)
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text(json.dumps(build_stats))
    return manifest


def _empty_ledger() -> pl.DataFrame:
    return pl.DataFrame(schema=GAP_LEDGER_SCHEMA)


def test_build_report_rows_for_date_covers_all_six_checks(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"

    trade_df = pl.DataFrame(
        {
            "trade_id": [1, 2, 3],
            "etime": [1_000, 2_000, 3_000],
            "price": [1.0, 2.0, 3.0],
        }
    )
    _issue_and_write_build_stats(
        lake_root,
        registry_root,
        SYMBOL,
        "trade",
        DATE,
        trade_df,
        build_stats={
            "reconciliation_missing_from_capture": 1,
            "reconciliation_missing_from_archive": 2,
            "reconciliation_overlap_rows": 1000,
            "na_placeholder_dropped": 0,
            "na_placeholder_rate": 0.0,
        },
    )

    bt_df = pl.DataFrame(
        {
            "etime": [1_000, 2_000, 3_000],
            "bid_price": [100.0, 100.1, 100.2],
            "ask_price": [100.2, 100.0, 100.4],  # row 2 (idx1) crossed
        }
    )
    _issue_and_write_build_stats(
        lake_root, registry_root, SYMBOL, "bookTicker", DATE, bt_df, build_stats=None
    )

    rows = build_report_rows_for_date(
        SYMBOL,
        DATE,
        lake_root=lake_root,
        registry_root=registry_root,
        thresholds=THRESHOLDS,
        ledger_df=_empty_ledger(),
    )
    checks_seen = {(r["stream"], r["check"]) for r in rows}
    assert checks_seen == {
        ("trade", "gap_coverage"),
        ("trade", "etime_plausibility"),
        ("trade", "reconciliation"),
        ("trade", "na_placeholder"),
        ("bookTicker", "gap_coverage"),
        ("bookTicker", "etime_plausibility"),
        ("bookTicker", "crossed_locked_book"),
        ("bookTicker", "l1_sparsity"),
    }
    # All six numbered checks appear at least once across both streams.
    six = {r["check"] for r in rows}
    assert six == {
        "gap_coverage",
        "etime_plausibility",
        "reconciliation",
        "na_placeholder",
        "crossed_locked_book",
        "l1_sparsity",
    }


def test_build_report_rows_skips_stream_with_no_manifest(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    trade_df = pl.DataFrame({"trade_id": [1], "etime": [1_000], "price": [1.0]})
    _issue_and_write_build_stats(
        lake_root,
        registry_root,
        SYMBOL,
        "trade",
        DATE,
        trade_df,
        build_stats={
            "reconciliation_missing_from_capture": None,
            "reconciliation_missing_from_archive": None,
            "reconciliation_overlap_rows": None,
            "na_placeholder_dropped": 0,
            "na_placeholder_rate": 0.0,
        },
    )
    # No bookTicker manifest at all for this date (June-Aug case).
    rows = build_report_rows_for_date(
        SYMBOL,
        DATE,
        lake_root=lake_root,
        registry_root=registry_root,
        thresholds=THRESHOLDS,
        ledger_df=_empty_ledger(),
    )
    streams_seen = {r["stream"] for r in rows}
    assert streams_seen == {"trade"}


def test_normalize_row_maps_heterogeneous_fields_onto_fixed_schema():
    row = {
        "date": DATE,
        "symbol": SYMBOL,
        "stream": "trade",
        "check": "reconciliation",
        "dq_status": "degraded",
        "value_pct": 3.2,
        "missing_from_capture_pct": 1.1,
        "missing_from_archive_pct": 3.2,
    }
    normalized = normalize_row(row)
    assert normalized["value"] == pytest.approx(3.2)
    assert normalized["count"] is None
    assert "missing_from_capture_pct=1.1000" in normalized["detail"]


def test_write_report_writes_parquet_sidecar_and_markdown(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    trade_df = pl.DataFrame({"trade_id": [1], "etime": [1_000], "price": [1.0]})
    _issue_and_write_build_stats(
        lake_root,
        registry_root,
        SYMBOL,
        "trade",
        DATE,
        trade_df,
        build_stats={
            "reconciliation_missing_from_capture": None,
            "reconciliation_missing_from_archive": None,
            "reconciliation_overlap_rows": None,
            "na_placeholder_dropped": 0,
            "na_placeholder_rate": 0.0,
        },
    )

    report_path = write_report(
        SYMBOL,
        DATE,
        lake_root=lake_root,
        registry_root=registry_root,
        thresholds=THRESHOLDS,
        ledger_df=_empty_ledger(),
    )
    assert report_path == dq_report_path(lake_root, DATE)
    assert report_path.exists()
    report_df = pl.read_parquet(report_path)
    assert set(report_df.columns) == {
        "date",
        "symbol",
        "stream",
        "check",
        "dq_status",
        "value",
        "count",
        "detail",
    }
    assert report_df.height > 0

    resync_path = dq_resync_windows_path(lake_root, DATE)
    assert resync_path.exists()
    resync_df = pl.read_parquet(resync_path)
    assert resync_df.height == 0  # empty ledger -- no outages

    md_path = dq_report_markdown_path(lake_root, DATE)
    assert md_path.exists()
    assert DATE in md_path.read_text()


def test_main_range_mode_writes_one_report_per_date(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"
    (capture_root / "gap_ledger").mkdir(parents=True)
    _empty_ledger().write_parquet(capture_root / "gap_ledger" / "ledger.parquet")

    for date in ("2026-09-12", "2026-09-13"):
        trade_df = pl.DataFrame({"trade_id": [1], "etime": [1_000], "price": [1.0]})
        _issue_and_write_build_stats(
            lake_root,
            registry_root,
            SYMBOL,
            "trade",
            date,
            trade_df,
            build_stats={
                "reconciliation_missing_from_capture": None,
                "reconciliation_missing_from_archive": None,
                "reconciliation_overlap_rows": None,
                "na_placeholder_dropped": 0,
                "na_placeholder_rate": 0.0,
            },
        )

    exit_code = report_main(
        [
            "--symbol",
            SYMBOL,
            "--range",
            "2026-09-12",
            "2026-09-13",
            "--lake-root",
            str(lake_root),
            "--registry-root",
            str(registry_root),
            "--capture-data-root",
            str(capture_root),
        ]
    )
    assert exit_code == 0
    assert dq_report_path(lake_root, "2026-09-12").exists()
    assert dq_report_path(lake_root, "2026-09-13").exists()


def test_spec_md_dq_block_round_trips_through_check_spec_diff():
    """Done criterion: spec.md's new marker block round-trips through
    spec.render's existing diff-checking machinery -- check_spec_diff
    still passes against the current, committed tree once the table is
    rendered and committed."""
    from spec.catalogue import load_features, load_labels
    from spec.render import SPEC_MD, load_dq_thresholds_raw

    features = load_features()
    labels = load_labels()
    dq_thresholds = load_dq_thresholds_raw()
    drift = check_drift(SPEC_MD, features, labels, dq_thresholds)
    assert drift is None, drift
