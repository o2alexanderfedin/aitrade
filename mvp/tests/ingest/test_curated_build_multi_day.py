"""Tests for data.ingest.curated_build.build_curated_range -- 03-03-PLAN.md
Task 2: idempotent multi-day orchestration over both streams, with
"already_present" and "no_source" as counted skips, never crashes."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from data.ingest.curated_build import build_curated_day, build_curated_range
from data.store import by_date_index_path


def _write_archive_trade_partition(
    lake_root_dir: Path, date: str, trade_ids: list[int]
) -> None:
    archive_dir = (
        lake_root_dir
        / "raw"
        / "symbol=BTCUSDT"
        / "stream=trade"
        / "source=archive"
        / f"date={date}"
    )
    archive_dir.mkdir(parents=True, exist_ok=True)
    n = len(trade_ids)
    df = pl.DataFrame(
        {
            "trade_id": trade_ids,
            "etime": [1_000 * (i + 1) for i in range(n)],
            "event_time": [1_000 * (i + 1) for i in range(n)],
            "price": [100.0 + i for i in range(n)],
            "qty": [1.0] * n,
            "is_buyer_maker": [i % 2 == 0 for i in range(n)],
            "seq": [-1] * n,
            "rtime": [0] * n,
            "source": ["archive"] * n,
            "schema_version": [1] * n,
        }
    )
    df.write_parquet(archive_dir / "part-1.parquet")


def _write_capture_bookticker_partition(
    capture_root_dir: Path, date: str, update_ids: list[int]
) -> None:
    date_dir = (
        capture_root_dir / "symbol=BTCUSDT" / "stream=bookTicker" / f"date={date}"
    )
    date_dir.mkdir(parents=True, exist_ok=True)
    n = len(update_ids)
    df = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"] * n,
            "stream": ["bookTicker"] * n,
            "update_id": update_ids,
            "etime": [1_000 * (i + 1) for i in range(n)],
            "event_time": [1_000 * (i + 1) for i in range(n)],
            "bid_price": [100.0] * n,
            "bid_qty": [1.0] * n,
            "ask_price": [100.1] * n,
            "ask_qty": [1.0] * n,
            "seq": list(range(n)),
            "rtime": [0] * n,
            "source": ["capture"] * n,
            "schema_version": [2] * n,
        }
    )
    df.write_parquet(date_dir / "part-1.parquet")


def test_build_curated_range_trade_writes_each_missing_day_once(tmp_path: Path):
    lake_root_dir = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"

    _write_archive_trade_partition(lake_root_dir, "2026-06-01", [1, 2, 3])
    _write_archive_trade_partition(lake_root_dir, "2026-06-02", [4, 5])
    # 2026-06-03: deliberately no source at all.

    results = build_curated_range(
        "BTCUSDT",
        "trade",
        "2026-06-01",
        "2026-06-03",
        lake_root_dir,
        capture_root,
        registry_root=registry_root,
        code_hash="deadbeef",
    )

    by_date = {r["date"]: r for r in results}
    assert by_date["2026-06-01"]["status"] == "written"
    assert by_date["2026-06-01"]["chosen_source"] == "archive"
    assert by_date["2026-06-01"]["manifest_id"] is not None
    assert by_date["2026-06-02"]["status"] == "written"
    assert by_date["2026-06-02"]["chosen_source"] == "archive"
    assert by_date["2026-06-03"]["status"] == "no_source"
    assert by_date["2026-06-03"]["manifest_id"] is None
    assert by_date["2026-06-03"]["chosen_source"] is None


def test_build_curated_range_rerun_is_a_noop_zero_new_partitions_and_manifests(
    tmp_path: Path,
):
    """The idempotent-range idiom the downloader's own resume test mirrors:
    a second build_curated_range call over the SAME range issues zero new
    manifest JSON files and writes zero new partition files -- every date
    resolves to "already_present" and reuses the first call's manifest_id.
    """
    lake_root_dir = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"

    _write_archive_trade_partition(lake_root_dir, "2026-06-01", [1, 2, 3])
    _write_archive_trade_partition(lake_root_dir, "2026-06-02", [4, 5])

    first = build_curated_range(
        "BTCUSDT",
        "trade",
        "2026-06-01",
        "2026-06-02",
        lake_root_dir,
        capture_root,
        registry_root=registry_root,
        code_hash="deadbeef",
    )
    assert [r["status"] for r in first] == ["written", "written"]

    manifest_files_before = sorted(
        (registry_root / "manifests" / "BTCUSDT.trade").glob("*.json")
    )
    partition_files_before = sorted((lake_root_dir / "curated").rglob("part-*.parquet"))

    second = build_curated_range(
        "BTCUSDT",
        "trade",
        "2026-06-01",
        "2026-06-02",
        lake_root_dir,
        capture_root,
        registry_root=registry_root,
        code_hash="deadbeef",
    )
    assert [r["status"] for r in second] == ["already_present", "already_present"]
    assert [r["manifest_id"] for r in second] == [r["manifest_id"] for r in first]
    assert [r["chosen_source"] for r in second] == ["archive", "archive"]

    manifest_files_after = sorted(
        (registry_root / "manifests" / "BTCUSDT.trade").glob("*.json")
    )
    partition_files_after = sorted((lake_root_dir / "curated").rglob("part-*.parquet"))
    assert manifest_files_after == manifest_files_before
    assert partition_files_after == partition_files_before


def test_build_curated_range_bookticker_skips_no_source_days_capture_only(
    tmp_path: Path,
):
    """bookTicker has no archive source at all -- a date range spanning a
    day with capture data and a day without must produce "written" for the
    former and "no_source" (never a crash) for the latter."""
    lake_root_dir = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"

    _write_capture_bookticker_partition(capture_root, "2026-09-12", [1, 2, 3])
    # 2026-09-11: no capture data (before the real two-regime L1 start).

    results = build_curated_range(
        "BTCUSDT",
        "bookTicker",
        "2026-09-11",
        "2026-09-12",
        lake_root_dir,
        capture_root,
        registry_root=registry_root,
        code_hash="deadbeef",
    )

    by_date = {r["date"]: r for r in results}
    assert by_date["2026-09-11"]["status"] == "no_source"
    assert by_date["2026-09-12"]["status"] == "written"
    assert by_date["2026-09-12"]["chosen_source"] == "capture"

    manifest = by_date_index_path(
        registry_root, "BTCUSDT.bookTicker", "BTCUSDT", "bookTicker", "2026-09-12"
    )
    assert manifest.exists()


def test_build_curated_range_reuses_existing_manifest_built_via_build_curated_day(
    tmp_path: Path,
):
    """A date already built via a direct build_curated_day call (not
    through build_curated_range) is still recognized as already_present
    and its manifest_id reused, not rebuilt."""
    lake_root_dir = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"

    _write_archive_trade_partition(lake_root_dir, "2026-06-01", [1, 2, 3])
    direct_manifest = build_curated_day(
        "BTCUSDT",
        "trade",
        "2026-06-01",
        lake_root_dir,
        capture_root,
        registry_root=registry_root,
        code_hash="deadbeef",
    )

    results = build_curated_range(
        "BTCUSDT",
        "trade",
        "2026-06-01",
        "2026-06-01",
        lake_root_dir,
        capture_root,
        registry_root=registry_root,
        code_hash="deadbeef",
    )
    assert results[0]["status"] == "already_present"
    assert results[0]["manifest_id"] == direct_manifest["manifest_id"]
