"""Tests for rotation.py's atomic writer/orphan-sweep and seq.py's restart-resume.

Plan 04 Task 1 ("seq-resume scalability" correction) adds the `seq_state.json`
sidecar cases: (a) sidecar present and covers the newest on-disk partition ->
zero partition files opened; (b) sidecar absent -> falls back to a scan
restricted to the newest `date=*` directory only; (c) sidecar present but
stale relative to a newer partition (simulates a crash between a flush and
its sidecar write) -> resume takes max(sidecar, scan) and self-heals the
sidecar. See `data/capture/rotation.py`'s `read_seq_sidecar`/
`write_seq_state_atomic`/`part_ns_of` and `data/capture/seq.py`'s
`resume_seq_assigner`.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from data.capture.parse import parse_bookticker, parse_trade
from data.capture.rotation import (
    part_ns_of,
    partition_dir,
    read_seq_sidecar,
    sweep_orphan_tmp_files,
    write_partition_atomic,
    write_seq_state_atomic,
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
    event_ms = int(
        datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    rows = [_bookticker_row(0, event_ms), _bookticker_row(1, event_ms + 1)]

    written = write_partition_atomic(
        rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker"
    )

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
    assert (
        write_partition_atomic([], BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker")
        == []
    )


def test_sweep_orphan_tmp_files_removes_stray_tmp_leaves_real_file(
    tmp_path: Path,
) -> None:
    event_ms = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [_bookticker_row(0, event_ms)]
    written = write_partition_atomic(
        rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker"
    )
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


def test_resume_seq_assigner_sidecar_present_and_covers_disk_opens_zero_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case (a): sidecar's `part_ns` matches the newest file on disk, so the
    restricted newest-date scan finds nothing newer than the sidecar and
    never opens a partition file at all."""
    event_ms = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [_bookticker_row(seq, event_ms + seq) for seq in range(18)]  # seq 0..17
    written = write_partition_atomic(
        rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker"
    )
    write_seq_state_atomic(
        tmp_path,
        "BTCUSDT",
        {"bookTicker": {"seq": 17, "part_ns": part_ns_of(written[0])}},
    )

    import data.capture.seq as seq_module

    def _boom(*args, **kwargs):
        raise AssertionError(
            "resume_seq_assigner opened a partition file despite a covering sidecar"
        )

    monkeypatch.setattr(seq_module.pl, "scan_parquet", _boom)

    assigner = SeqAssigner()
    resume_seq_assigner(assigner, tmp_path, "BTCUSDT", ["bookTicker", "trade"])

    assert assigner.next("BTCUSDT", "bookTicker") == 18
    assert assigner.next("BTCUSDT", "trade") == 0


def test_resume_seq_assigner_sidecar_absent_falls_back_to_newest_date_scan(
    tmp_path: Path,
) -> None:
    """Case (b): no sidecar exists yet -> falls back to scanning the newest
    `date=*` directory (not every historical date) and produces the correct
    max, exactly like Plan 02's original behavior for a fresh store."""
    event_ms = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [_bookticker_row(seq, event_ms + seq) for seq in range(18)]  # seq 0..17
    write_partition_atomic(rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker")

    assert not (tmp_path / "seq_state.json").exists()

    assigner = SeqAssigner()
    resume_seq_assigner(assigner, tmp_path, "BTCUSDT", ["bookTicker", "trade"])

    assert assigner.next("BTCUSDT", "bookTicker") == 18
    assert assigner.next("BTCUSDT", "trade") == 0

    # Self-heal: absence of a sidecar is itself a form of staleness: this
    # resume should now have written one, so the NEXT restart is sidecar-only.
    healed = read_seq_sidecar(tmp_path, "BTCUSDT")
    assert healed["bookTicker"]["seq"] == 17


def test_resume_seq_assigner_empty_newest_date_dir_falls_back_to_older_dir_with_data(
    tmp_path: Path,
) -> None:
    """WR-02 (01-REVIEW.md): `write_partition_atomic()` does
    `part_dir.mkdir(parents=True, exist_ok=True)` before the first
    successful `write_parquet`/`.replace()`. A crash between those two
    steps leaves an empty `date=<today>` directory behind. If that empty
    directory is the NEWEST `date=*` dir and the sidecar is absent, resume
    must still find the real data in an OLDER, non-empty `date=*` dir --
    not silently start at 0, which would violate 'resume is never lower
    than what is actually on disk'."""
    event_ms = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [_bookticker_row(seq, event_ms + seq) for seq in range(18)]  # seq 0..17
    write_partition_atomic(rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker")

    stream_dir = partition_dir(tmp_path, "BTCUSDT", "bookTicker")
    # Simulate the crash-orphaned empty newest date dir: 2026-09-13 is
    # lexicographically (and chronologically) newer than the real data's
    # 2026-09-12, and date_dirs is sorted newest-last.
    (stream_dir / "date=2026-09-13").mkdir(parents=True)

    assert not (tmp_path / "seq_state.json").exists()

    assigner = SeqAssigner()
    resume_seq_assigner(assigner, tmp_path, "BTCUSDT", ["bookTicker", "trade"])

    assert assigner.next("BTCUSDT", "bookTicker") == 18
    assert assigner.next("BTCUSDT", "trade") == 0


def test_resume_seq_assigner_stale_sidecar_takes_max_of_sidecar_and_scan(
    tmp_path: Path,
) -> None:
    """Case (c): sidecar reflects only the first of two flushes (simulating a
    crash between the second flush's Parquet write and its sidecar update).
    Resume must take max(sidecar, scan-of-newer-partitions), never a value
    lower than what is actually on disk, and self-heal the sidecar."""
    event_ms = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)

    rows1 = [_bookticker_row(seq, event_ms + seq) for seq in range(5)]  # seq 0..4
    written1 = write_partition_atomic(
        rows1, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker"
    )
    write_seq_state_atomic(
        tmp_path,
        "BTCUSDT",
        {"bookTicker": {"seq": 4, "part_ns": part_ns_of(written1[0])}},
    )

    time.sleep(0.001)  # ensure a distinct part-<ns> filename for the 2nd flush
    rows2 = [_bookticker_row(seq, event_ms + seq) for seq in range(5, 10)]  # seq 5..9
    write_partition_atomic(rows2, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker")
    # Sidecar deliberately NOT updated for this second flush -> now stale.

    assigner = SeqAssigner()
    resume_seq_assigner(assigner, tmp_path, "BTCUSDT", ["bookTicker", "trade"])

    assert assigner.next("BTCUSDT", "bookTicker") == 10  # max(sidecar=4, scan=9) + 1

    healed = read_seq_sidecar(tmp_path, "BTCUSDT")
    assert healed["bookTicker"]["seq"] == 9


def test_resume_seq_assigner_5000_partitions_with_sidecar_under_10_seconds(
    tmp_path: Path,
) -> None:
    """Scalability acceptance: a store with 5,000 partition files for one
    stream, plus a sidecar covering the newest of them, resumes without the
    O(all-partitions-ever) hazard found live in Plan 02's checkpoint (1,261
    files cost ~14s of daemon downtime) -- because the covering sidecar
    means zero files are opened, regardless of how many partitions exist on
    disk.

    WR-05 (01-REVIEW.md): the bound is a REGRESSION GUARD against that
    O(all-partitions) behavior recurring, not a tight SLA -- 10s leaves
    generous headroom for CI/host load so this does not flake independent
    of any real regression, while still failing hard if the covering-
    sidecar fast path ever regresses back to opening every file."""
    event_ms = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [_bookticker_row(0, event_ms)]
    written = write_partition_atomic(
        rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker"
    )
    template = written[0]
    part_dir = template.parent
    last_ns = part_ns_of(template)

    try:
        for _ in range(1, 5000):
            last_ns += 1
            clone = part_dir / f"part-{last_ns}.parquet"
            os.link(template, clone)  # instant -- no parquet-write cost
    except OSError:
        pytest.skip("filesystem lacks hardlink support")

    write_seq_state_atomic(
        tmp_path, "BTCUSDT", {"bookTicker": {"seq": 0, "part_ns": last_ns}}
    )

    assigner = SeqAssigner()
    start = time.monotonic()
    resume_seq_assigner(assigner, tmp_path, "BTCUSDT", ["bookTicker", "trade"])
    elapsed = time.monotonic() - start

    assert elapsed < 10.0, f"resume took {elapsed:.2f}s with 5,000 partitions on disk"
    assert assigner.next("BTCUSDT", "bookTicker") == 1
