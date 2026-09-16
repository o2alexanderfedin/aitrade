"""Tests for data.ingest.curated_build -- source precedence, the NA-placeholder
filter, and the schema-tolerant capture-partition read."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from data.ingest.curated_build import (
    filter_na_placeholders,
    read_capture_partition,
    select_source_for_day,
)


def test_select_source_for_day_single_source_has_none_reconciliation_fields():
    archive_df = pl.DataFrame({"trade_id": [1, 2, 3]})
    chosen, stats = select_source_for_day(archive_df, None, published=True)
    assert stats["chosen_source"] == "archive"
    assert stats["archive_available"] is True
    assert stats["capture_available"] is False
    assert stats["reconciliation_missing_from_capture"] is None
    assert stats["reconciliation_missing_from_archive"] is None
    assert stats["reconciliation_overlap_rows"] is None
    assert chosen.equals(archive_df)


def test_select_source_for_day_prefers_capture_when_not_published():
    capture_df = pl.DataFrame({"trade_id": [10, 11]})
    archive_df = pl.DataFrame({"trade_id": [10, 11]})
    chosen, stats = select_source_for_day(archive_df, capture_df, published=False)
    assert stats["chosen_source"] == "capture"
    assert chosen.equals(capture_df)


def test_select_source_for_day_both_available_computes_overlap_scoped_reconciliation():
    # archive: ids 1..10; capture: ids 5..12 (starts later, like the real
    # 2026-09-12 day where capture begins mid-day). Overlap range [5, 10].
    archive_df = pl.DataFrame({"trade_id": list(range(1, 11))})
    # capture is missing id 7 within the overlap range, and has 2 extra ids
    # beyond the overlap (11, 12) which must NOT be counted.
    capture_ids = [i for i in range(5, 11) if i != 7] + [11, 12]
    capture_df = pl.DataFrame({"trade_id": capture_ids})

    chosen, stats = select_source_for_day(archive_df, capture_df, published=True)
    assert stats["chosen_source"] == "archive"
    assert stats["reconciliation_overlap_id_min"] == 5
    assert stats["reconciliation_overlap_id_max"] == 10
    # archive ids 5..10 (6 ids) minus capture's overlap-range ids (5 ids,
    # missing 7) => archive id 7 missing from capture => 1
    assert stats["reconciliation_missing_from_capture"] == 1
    # capture has no ids in [5,10] absent from archive
    assert stats["reconciliation_missing_from_archive"] == 0
    assert stats["reconciliation_overlap_rows"] == 5


def _trade_row(schema_version, price, qty, exec_type=None, trade_id=1, etime=1_000):
    row = {
        "trade_id": trade_id,
        "etime": etime,
        "price": price,
        "qty": qty,
        "schema_version": schema_version,
    }
    if exec_type is not None:
        row["exec_type"] = exec_type
    return row


def test_filter_na_placeholders_drops_exact_expected_count_mixed_schema():
    rows = [
        # v1 NA placeholders (price==0, qty==0) -- 2 of them
        _trade_row(1, 0.0, 0.0, trade_id=1),
        _trade_row(1, 0.0, 0.0, trade_id=2),
        # v1 real rows -- 3 of them
        _trade_row(1, 100.0, 1.0, trade_id=3),
        _trade_row(1, 101.0, 2.0, trade_id=4),
        _trade_row(1, 102.0, 3.0, trade_id=5),
        # v2 NA placeholders (exec_type == "NA") -- 1 of them
        _trade_row(2, 100.0, 1.0, exec_type="NA", trade_id=6),
        # v2 real rows -- 2 of them
        _trade_row(2, 100.0, 1.0, exec_type="MARKET", trade_id=7),
        _trade_row(2, 100.0, 1.0, exec_type="MARKET", trade_id=8),
    ]
    df = pl.DataFrame(rows)
    cleaned, drop_count = filter_na_placeholders(df)

    assert drop_count == 3  # 2 v1 NA + 1 v2 NA
    assert cleaned.height == 5
    assert set(cleaned["trade_id"].to_list()) == {3, 4, 5, 7, 8}


def test_filter_na_placeholders_v1_only_no_exec_type_column():
    """Real archive-sourced days have no exec_type column at all -- the v2
    branch must not raise ColumnNotFoundError when it's absent."""
    df = pl.DataFrame(
        [
            _trade_row(1, 0.0, 0.0, trade_id=1),
            _trade_row(1, 100.0, 1.0, trade_id=2),
        ]
    )
    assert "exec_type" not in df.columns
    cleaned, drop_count = filter_na_placeholders(df)
    assert drop_count == 1
    assert cleaned["trade_id"].to_list() == [2]


def _write_parquet_no_exec_type(path: Path, trade_ids: list[int]) -> None:
    df = pl.DataFrame(
        {
            "trade_id": trade_ids,
            "etime": [1_000 * i for i in trade_ids],
            "price": [100.0] * len(trade_ids),
            "qty": [1.0] * len(trade_ids),
            "schema_version": [1] * len(trade_ids),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


def _write_parquet_with_exec_type(path: Path, trade_ids: list[int]) -> None:
    df = pl.DataFrame(
        {
            "trade_id": trade_ids,
            "etime": [1_000 * i for i in trade_ids],
            "price": [100.0] * len(trade_ids),
            "qty": [1.0] * len(trade_ids),
            "schema_version": [2] * len(trade_ids),
            "exec_type": ["MARKET"] * len(trade_ids),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


def test_read_capture_partition_schema_tolerant_mixed_v1_v2(tmp_path: Path):
    """A date=... directory holding both a pre-restart (schema_version=1, no
    exec_type) and a post-restart (schema_version=2, exec_type present) file
    reads successfully via diagonal_relaxed concat, never a hard crash."""
    date_dir = tmp_path / "date=2026-09-12"
    _write_parquet_no_exec_type(date_dir / "part-1.parquet", [1, 2])
    _write_parquet_with_exec_type(date_dir / "part-2.parquet", [3, 4])

    result = read_capture_partition(date_dir)

    assert result is not None
    assert result.height == 4
    assert "exec_type" in result.columns
    # v1 rows' exec_type is null (schema-tolerant union), never a crash.
    v1_rows = result.filter(pl.col("schema_version") == 1)
    assert v1_rows["exec_type"].is_null().all()

    # filter_na_placeholders still classifies each row correctly by its own
    # schema_version, even with the unioned null exec_type column present.
    cleaned, drop_count = filter_na_placeholders(result)
    assert drop_count == 0
    assert cleaned.height == 4


def test_read_capture_partition_returns_none_when_absent(tmp_path: Path):
    assert read_capture_partition(tmp_path / "date=2026-01-01") is None


def test_build_curated_day_refuses_second_write_same_day(tmp_path: Path):
    """Write-once, matching Plan 01's write_raw_partition: a second
    build_curated_day call for an already-written day raises FileExistsError
    naming the existing part file, never a silent overwrite/duplicate."""
    from data.ingest.curated_build import build_curated_day

    lake_root_dir = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    capture_root = tmp_path / "capture"

    archive_dir = (
        lake_root_dir
        / "raw"
        / "symbol=BTCUSDT"
        / "stream=trade"
        / "source=archive"
        / "date=2026-09-12"
    )
    archive_dir.mkdir(parents=True)
    archive_df = pl.DataFrame(
        {
            "trade_id": [1, 2, 3],
            "etime": [1_000, 2_000, 3_000],
            "event_time": [1_000, 2_000, 3_000],
            "price": [100.0, 101.0, 102.0],
            "qty": [1.0, 1.0, 1.0],
            "is_buyer_maker": [True, False, True],
            "seq": [-1, -1, -1],
            "rtime": [0, 0, 0],
            "source": ["archive", "archive", "archive"],
            "schema_version": [1, 1, 1],
        }
    )
    archive_df.write_parquet(archive_dir / "part-1.parquet")

    build_curated_day(
        "BTCUSDT",
        "trade",
        "2026-09-12",
        lake_root_dir,
        capture_root,
        registry_root=registry_root,
        code_hash="deadbeef",
    )

    with pytest.raises(FileExistsError, match="already has a written part file"):
        build_curated_day(
            "BTCUSDT",
            "trade",
            "2026-09-12",
            lake_root_dir,
            capture_root,
            registry_root=registry_root,
            code_hash="deadbeef",
        )
