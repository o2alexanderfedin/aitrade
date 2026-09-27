"""Tests for data.ingest.normalize -- archive CSV -> TRADE_SCHEMA rows, and
the write-once raw-tier partition writer."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from data.ingest.normalize import normalize_archive_trades, write_raw_partition
from data.schema import TRADE_SCHEMA
from data.unit_registry import get_unit_entry
from tests.fixtures.archive_csv import write_sample_archive_csv, SAMPLE_ARCHIVE_CSV_ROWS


def test_normalize_archive_trades_shapes_rows_like_trade_schema(tmp_path: Path):
    csv_path = write_sample_archive_csv(tmp_path / "sample.csv")
    unit_entry = get_unit_entry("futures-um", "trades")
    rtime_ns = 1_700_000_000_000_000_000

    df = normalize_archive_trades(csv_path, unit_entry, "BTCUSDT", rtime_ns)

    assert dict(df.schema) == TRADE_SCHEMA
    assert df.height == len(SAMPLE_ARCHIVE_CSV_ROWS)

    first_row = df.row(0, named=True)
    first_csv_row = SAMPLE_ARCHIVE_CSV_ROWS[0].split(",")
    expected_time_ms = int(first_csv_row[4])
    assert first_row["etime"] == expected_time_ms * 1_000_000
    assert first_row["event_time"] == first_row["etime"]
    assert first_row["trade_id"] == int(first_csv_row[0])
    assert first_row["price"] == pytest.approx(float(first_csv_row[1]))
    assert first_row["qty"] == pytest.approx(float(first_csv_row[2]))
    assert first_row["is_buyer_maker"] == (first_csv_row[5] == "true")
    assert first_row["seq"] == -1
    assert first_row["rtime"] == rtime_ns
    assert first_row["source"] == "archive"
    assert first_row["schema_version"] == 1
    assert first_row["symbol"] == "BTCUSDT"
    assert first_row["stream"] == "trade"


def test_normalize_archive_trades_rejects_non_ms_time_unit(tmp_path: Path):
    csv_path = write_sample_archive_csv(tmp_path / "sample.csv")
    spot_entry = get_unit_entry("spot", "trades")
    with pytest.raises(ValueError, match="time_unit='ms'"):
        normalize_archive_trades(csv_path, spot_entry, "BTCUSDT", 0)


def test_write_raw_partition_writes_and_is_readable(tmp_path: Path):
    df = pl.DataFrame({k: pl.Series([], dtype=v) for k, v in TRADE_SCHEMA.items()})
    csv_path = write_sample_archive_csv(tmp_path / "sample.csv")
    unit_entry = get_unit_entry("futures-um", "trades")
    df = normalize_archive_trades(csv_path, unit_entry, "BTCUSDT", 1)

    lake_root = tmp_path / "lake"
    written = write_raw_partition(
        df, "BTCUSDT", "trade", "archive", "2026-09-12", lake_root
    )

    assert written.exists()
    read_back = pl.read_parquet(written)
    assert read_back.height == df.height
    assert (
        written.parent
        == lake_root
        / "raw"
        / "symbol=BTCUSDT"
        / "stream=trade"
        / "source=archive"
        / "date=2026-09-12"
    )


def test_write_raw_partition_refuses_second_write_same_day(tmp_path: Path):
    csv_path = write_sample_archive_csv(tmp_path / "sample.csv")
    unit_entry = get_unit_entry("futures-um", "trades")
    df = normalize_archive_trades(csv_path, unit_entry, "BTCUSDT", 1)
    lake_root = tmp_path / "lake"

    write_raw_partition(df, "BTCUSDT", "trade", "archive", "2026-09-12", lake_root)

    with pytest.raises(FileExistsError, match="already has a written part file"):
        write_raw_partition(df, "BTCUSDT", "trade", "archive", "2026-09-12", lake_root)
