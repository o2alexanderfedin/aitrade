"""Tests for gap_ledger.py's GapLedger — persisted outage rows."""

from __future__ import annotations

from pathlib import Path

from data.capture.gap_ledger import GapLedger


def test_record_gap_persists_one_row_readable_by_a_fresh_instance(
    tmp_path: Path,
) -> None:
    ledger = GapLedger(tmp_path)
    ledger.record_gap(
        stream="trade",
        conn_id="merged",
        gap_start_rtime=1000,
        gap_end_rtime=6000,
        cause="no message for 5.0s",
    )

    fresh_ledger = GapLedger(tmp_path)
    df = fresh_ledger.read_all()

    assert df.height == 1
    row = df.row(0, named=True)
    assert row["stream"] == "trade"
    assert row["conn_id"] == "merged"
    assert row["gap_start_rtime"] == 1000
    assert row["gap_end_rtime"] == 6000
    assert row["cause"] == "no message for 5.0s"
    assert isinstance(row["detected_at"], int)
    assert row["detected_at"] > 0


def test_record_gap_twice_appends_two_rows(tmp_path: Path) -> None:
    ledger = GapLedger(tmp_path)
    ledger.record_gap(
        stream="bookTicker",
        conn_id="merged",
        gap_start_rtime=1000,
        gap_end_rtime=6000,
        cause="no message for 5.0s",
    )
    ledger.record_gap(
        stream="trade",
        conn_id="merged",
        gap_start_rtime=2000,
        gap_end_rtime=8000,
        cause="no message for 6.0s",
    )

    df = ledger.read_all()
    assert df.height == 2


def test_read_all_on_empty_ledger_returns_empty_frame_with_schema(
    tmp_path: Path,
) -> None:
    ledger = GapLedger(tmp_path)
    df = ledger.read_all()
    assert df.height == 0
    assert set(df.columns) == {
        "stream",
        "conn_id",
        "gap_start_rtime",
        "gap_end_rtime",
        "cause",
        "detected_at",
    }
