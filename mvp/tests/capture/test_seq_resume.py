"""Tests for rotation.py's atomic writer/orphan-sweep and seq.py's restart-resume."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from data.capture.parse import parse_bookticker, parse_trade
from data.capture.rotation import (
    partition_dir,
    sweep_orphan_tmp_files,
    write_partition_atomic,
)
from data.capture.seq import SeqAssigner, resume_seq_assigner
from data.schema import BOOKTICKER_SCHEMA
from tests.fixtures.payloads import SAMPLE_BOOKTICKER_FRAME, SAMPLE_TRADE_FRAME


def _bookticker_row(seq: int, event_ms: int) -> dict:
    data = dict(SAMPLE_BOOKTICKER_FRAME["data"])
    data["T"] = event_ms
    data["E"] = event_ms
    return parse_bookticker(data, seq, rtime_ns=time.time_ns())


def _trade_row(seq: int, event_ms: int) -> dict:
    data = dict(SAMPLE_TRADE_FRAME["data"])
    data["T"] = event_ms
    data["E"] = event_ms
    return parse_trade(data, seq, rtime_ns=time.time_ns())


def test_write_partition_atomic_single_date(tmp_path: Path) -> None:
    event_ms = int(datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [_bookticker_row(0, event_ms), _bookticker_row(1, event_ms + 1)]

    written = write_partition_atomic(rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker")

    assert len(written) == 1
    path = written[0]
    assert path.name.endswith(".parquet")
    assert not path.name.endswith(".tmp")
    assert path.exists()
    df = pl.read_parquet(path)
    assert df.height == 2
    assert set(df["seq"].to_list()) == {0, 1}


def test_write_partition_atomic_midnight_straddle(tmp_path: Path) -> None:
    midnight_ms = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
    before = _bookticker_row(0, midnight_ms - 1)  # 2026-09-11
    after = _bookticker_row(1, midnight_ms)  # 2026-09-12

    written = write_partition_atomic(
        [before, after], BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker"
    )

    assert len(written) == 2
    dates_seen = set()
    for path in written:
        assert not path.name.endswith(".tmp")
        df = pl.read_parquet(path)
        assert df.height == 1
        # Path shape: .../date=<YYYY-MM-DD>/part-<ns>.parquet
        date_part = path.parent.name
        dates_seen.add(date_part)
        if date_part == "date=2026-09-11":
            assert df["seq"][0] == 0
        elif date_part == "date=2026-09-12":
            assert df["seq"][0] == 1
        else:
            pytest.fail(f"unexpected date partition: {date_part}")

    assert dates_seen == {"date=2026-09-11", "date=2026-09-12"}


def test_write_partition_atomic_empty_rows_returns_empty(tmp_path: Path) -> None:
    assert write_partition_atomic([], BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker") == []


def test_sweep_orphan_tmp_files_removes_stray_tmp_leaves_real_file(tmp_path: Path) -> None:
    event_ms = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [_bookticker_row(0, event_ms)]
    written = write_partition_atomic(rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker")
    real_path = written[0]

    stray_tmp = real_path.parent / "part-999999999999999999.parquet.tmp"
    stray_tmp.write_bytes(b"not a real parquet file")
    # Backdate mtime past the min-age guard.
    old_time = time.time() - 60
    import os

    os.utime(stray_tmp, (old_time, old_time))

    deleted = sweep_orphan_tmp_files(tmp_path)

    assert stray_tmp in deleted
    assert not stray_tmp.exists()
    assert real_path.exists()


def test_resume_seq_assigner_seeds_from_max_seq_no_history_starts_fresh(
    tmp_path: Path,
) -> None:
    event_ms = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [_bookticker_row(seq, event_ms + seq) for seq in range(18)]  # seq 0..17
    write_partition_atomic(rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker")

    assigner = SeqAssigner()
    resume_seq_assigner(assigner, tmp_path, "BTCUSDT", ["bookTicker", "trade"])

    assert assigner.next("BTCUSDT", "bookTicker") == 18
    assert assigner.next("BTCUSDT", "trade") == 0


def test_partition_dir_is_the_single_path_shape(tmp_path: Path) -> None:
    expected = tmp_path / "parsed" / "symbol=BTCUSDT" / "stream=trade"
    assert partition_dir(tmp_path, "BTCUSDT", "trade") == expected
