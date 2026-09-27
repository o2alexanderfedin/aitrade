"""Tests for data.ingest.curated_build.build_curated_range -- 03-03-PLAN.md
Task 2: idempotent multi-day orchestration over both streams, with
"already_present" and "no_source" as counted skips, never crashes."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from data.ingest.curated_build import (
    _curated_build_stats_path,
    build_curated_day,
    build_curated_range,
)
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


def _write_capture_bookticker_rows(
    capture_root_dir: Path, date: str, rows: list[dict]
) -> None:
    """Write an explicit, fully-specified set of bookTicker capture rows --
    used where a test needs per-row control over `seq`/`rtime`/`etime`
    (05-00-PLAN.md's capture-redelivery-duplicate and
    real-anomaly-duplicate cases), which
    `_write_capture_bookticker_partition`'s one-row-per-update_id
    convenience cannot express."""
    date_dir = (
        capture_root_dir / "symbol=BTCUSDT" / "stream=bookTicker" / f"date={date}"
    )
    date_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(date_dir / "part-1.parquet")


def _bookticker_row(
    *, update_id: int, etime: int, seq: int, rtime: int, bid_price: float = 100.0
) -> dict:
    return {
        "symbol": "BTCUSDT",
        "stream": "bookTicker",
        "update_id": update_id,
        "etime": etime,
        "event_time": etime,
        "bid_price": bid_price,
        "bid_qty": 1.0,
        "ask_price": bid_price + 0.1,
        "ask_qty": 1.0,
        "seq": seq,
        "rtime": rtime,
        "source": "capture",
        "schema_version": 2,
    }


def test_capture_redelivery_duplicate_is_dropped_keeping_lowest_seq(tmp_path: Path):
    """The real 2026-09-16 case (05-00-PLAN.md): one `update_id` delivered
    twice by the daemon's redundant connection, content-identical except
    `seq`/`rtime` (STATE.md's BoundedDedup TTL tradeoff -- a duplicate
    delivered later than the dedup TTL is not deduped by the daemon).
    `build_curated_day` must drop the later redelivery and keep the
    earliest arrival, not raise."""
    lake_root_dir = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"
    rows = [
        _bookticker_row(update_id=1, etime=1_000, seq=0, rtime=100),
        _bookticker_row(update_id=2, etime=2_000, seq=1, rtime=200),
        # the redelivered duplicate of update_id=2: identical content,
        # later seq (arrival order) and much later rtime (arrival clock).
        _bookticker_row(update_id=2, etime=2_000, seq=2, rtime=147_300),
        _bookticker_row(update_id=3, etime=3_000, seq=3, rtime=300),
    ]
    _write_capture_bookticker_rows(capture_root, "2026-09-16", rows)

    manifest = build_curated_day(
        "BTCUSDT",
        "bookTicker",
        "2026-09-16",
        lake_root_dir,
        capture_root,
        registry_root=registry_root,
        code_hash="test",
    )
    written = pl.read_parquet(lake_root_dir / manifest["partitions"][0]["path"])
    assert written.height == 3, "one of the two update_id=2 rows must be dropped"
    assert sorted(written["update_id"].to_list()) == [1, 2, 3]
    kept = written.filter(pl.col("update_id") == 2)
    assert kept["rtime"].item() == 200, "must keep the EARLIEST arrival (lowest seq)"

    stats_path = _curated_build_stats_path(
        lake_root_dir, "BTCUSDT", "bookTicker", "2026-09-16"
    )
    stats = json.loads(stats_path.read_text())
    assert stats["capture_redelivery_rows_dropped"] == 1


def test_non_identical_duplicate_update_id_still_raises(tmp_path: Path):
    """Two rows sharing an `update_id` that DISAGREE on a real exchange
    field (not just `seq`/`rtime`) are a genuine anomaly, not a
    redundant-connection redelivery -- the redelivery-dedup must leave
    them untouched and `materialize_seq`'s uniqueness assertion must still
    raise."""
    lake_root_dir = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"
    rows = [
        _bookticker_row(update_id=1, etime=1_000, seq=0, rtime=100),
        _bookticker_row(update_id=2, etime=2_000, seq=1, rtime=200, bid_price=100.0),
        # same update_id, DIFFERENT bid_price -- a real anomaly.
        _bookticker_row(update_id=2, etime=2_000, seq=2, rtime=250, bid_price=101.0),
    ]
    _write_capture_bookticker_rows(capture_root, "2026-09-16", rows)

    with pytest.raises(ValueError, match="duplicate value"):
        build_curated_day(
            "BTCUSDT",
            "bookTicker",
            "2026-09-16",
            lake_root_dir,
            capture_root,
            registry_root=registry_root,
            code_hash="test",
        )


def test_archive_sourced_duplicate_trade_id_still_raises(tmp_path: Path):
    """The capture-redelivery dedup is gated on `chosen_source == "capture"`
    -- an archive-sourced day (no redundant-connection concept) with a
    duplicate `trade_id` must still raise, unaffected by this plan's fix."""
    lake_root_dir = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"
    _write_archive_trade_partition(lake_root_dir, "2026-09-13", [1, 2, 2])

    with pytest.raises(ValueError, match="duplicate value"):
        build_curated_day(
            "BTCUSDT",
            "trade",
            "2026-09-13",
            lake_root_dir,
            capture_root,
            registry_root=registry_root,
            code_hash="test",
        )


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


# --- WR-03 (03-REVIEW.md): partial/capture days supersede; orphans surface --


def _write_capture_trade_partition(
    capture_root_dir: Path, date: str, trade_ids: list[int]
) -> None:
    date_dir = capture_root_dir / "symbol=BTCUSDT" / "stream=trade" / f"date={date}"
    date_dir.mkdir(parents=True, exist_ok=True)
    n = len(trade_ids)
    pl.DataFrame(
        {
            "symbol": ["BTCUSDT"] * n,
            "stream": ["trade"] * n,
            "trade_id": trade_ids,
            "etime": [1_000 * (i + 1) for i in range(n)],
            "event_time": [1_000 * (i + 1) for i in range(n)],
            "price": [100.0 + i for i in range(n)],
            "qty": [1.0] * n,
            "is_buyer_maker": [i % 2 == 0 for i in range(n)],
            "exec_type": ["TRADE"] * n,
            "seq": list(range(n)),
            "rtime": [0] * n,
            "source": ["capture"] * n,
            "schema_version": [2] * n,
        }
    ).write_parquet(date_dir / "part-1.parquet")


def _range(lake_root_dir, capture_root, registry_root, start, end, today="2099-01-01"):
    return build_curated_range(
        "BTCUSDT",
        "trade",
        start,
        end,
        lake_root_dir,
        capture_root,
        registry_root=registry_root,
        code_hash="deadbeef",
        today=today,
    )


def test_capture_sourced_day_is_superseded_once_the_archive_publishes(tmp_path: Path):
    """03-REVIEW.md WR-03 reproduction: a day built from (partial) capture
    before the archive published stayed capture-sourced forever. A rebuild
    must supersede it -- NEW partition + NEW manifest + moved by-date
    pointer -- while the old manifest and its partition stay resolvable."""
    import json

    from data.store import resolve_manifest

    lake_root_dir = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"

    _write_capture_trade_partition(capture_root, "2026-09-12", [16, 17, 18, 19, 20])
    first = _range(
        lake_root_dir, capture_root, registry_root, "2026-09-12", "2026-09-12"
    )
    assert first[0]["status"] == "written" and first[0]["chosen_source"] == "capture"
    old_id = first[0]["manifest_id"]

    _write_archive_trade_partition(lake_root_dir, "2026-09-12", list(range(1, 21)))
    second = _range(
        lake_root_dir, capture_root, registry_root, "2026-09-12", "2026-09-12"
    )
    assert second[0]["status"] == "superseded", second
    assert second[0]["chosen_source"] == "archive"
    assert second[0]["superseded_manifest_id"] == old_id
    new_id = second[0]["manifest_id"]
    assert new_id != old_id

    idx = by_date_index_path(
        registry_root, "BTCUSDT.trade", "BTCUSDT", "trade", "2026-09-12"
    )
    assert json.loads(idx.read_text())["manifest_id"] == new_id
    for mid, rows in ((old_id, 5), (new_id, 20)):
        m = resolve_manifest(
            mid,
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root_dir,
            expected_tier="curated",
        )
        assert m["row_count"] == rows

    third = _range(
        lake_root_dir, capture_root, registry_root, "2026-09-12", "2026-09-12"
    )
    assert third[0]["status"] == "already_present"
    assert third[0]["manifest_id"] == new_id


def test_orphan_partition_without_a_manifest_is_reported_not_already_present(
    tmp_path: Path,
):
    """Crash between write_parquet_atomic and issue_manifest: the part file
    exists, no manifest names it. Used to report `already_present` with
    manifest_id=None forever."""
    lake_root_dir = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"
    _write_archive_trade_partition(lake_root_dir, "2026-09-13", [1, 2, 3])
    orphan_dir = lake_root_dir / "curated/symbol=BTCUSDT/stream=trade/date=2026-09-13"
    orphan_dir.mkdir(parents=True)
    pl.DataFrame({"trade_id": [1]}).write_parquet(orphan_dir / "part-123.parquet")

    results = _range(
        lake_root_dir, capture_root, registry_root, "2026-09-13", "2026-09-13"
    )
    assert results[0]["status"] == "orphan", results
    assert results[0]["orphan_paths"] == [
        "curated/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-123.parquet"
    ]


def test_today_and_future_dates_are_never_built(tmp_path: Path):
    lake_root_dir = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"
    _write_capture_trade_partition(capture_root, "2026-09-16", [1, 2, 3])
    _write_capture_trade_partition(capture_root, "2026-09-17", [4, 5, 6])

    results = _range(
        lake_root_dir,
        capture_root,
        registry_root,
        "2026-09-16",
        "2026-09-17",
        today="2026-09-16",
    )
    assert [r["status"] for r in results] == ["not_final", "not_final"]
    assert not (lake_root_dir / "curated").exists()
