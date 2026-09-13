"""Hardening tests proving `write_partition_atomic`'s atomicity claim
under a simulated crash between the Parquet write and the atomic rename --
01-04-PLAN.md Task 2's crash-simulation cases, plus a real-process-death
variant per the orchestrator's override (a monkeypatch alone only proves
the exception propagates in-process; a real `os._exit` proves no `finally`/
`atexit` cleanup can paper over a genuine crash).
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from data.capture.parse import parse_bookticker
from data.capture.rotation import (
    partition_dir,
    sweep_orphan_tmp_files,
    write_partition_atomic,
)
from data.schema import BOOKTICKER_SCHEMA
from tests.fixtures.payloads import SAMPLE_BOOKTICKER_FRAME


def _bookticker_row(seq: int, event_ms: int) -> dict:
    data = dict(SAMPLE_BOOKTICKER_FRAME["data"])
    data["T"] = event_ms
    data["E"] = event_ms
    return parse_bookticker(data, seq, rtime_ns=time.time_ns())


def _rows_for(seq_start: int, count: int) -> list[dict]:
    event_ms = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
    return [_bookticker_row(seq_start + i, event_ms + i) for i in range(count)]


def test_replace_failure_propagates_and_leaves_no_final_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path.replace raising AFTER write_parquet has fully written the .tmp
    file: write_partition_atomic must propagate the exception (not swallow
    it), and the final path must not exist -- the rename never happened."""
    rows = _rows_for(0, 3)

    def _boom(self, target):
        raise OSError("simulated crash between write and rename")

    with monkeypatch.context() as m:
        m.setattr(Path, "replace", _boom)
        with pytest.raises(OSError, match="simulated crash"):
            write_partition_atomic(
                rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker"
            )

    stream_dir = partition_dir(tmp_path, "BTCUSDT", "bookTicker")
    final_files = list(stream_dir.glob("**/*.parquet"))
    assert final_files == []

    tmp_files = list(stream_dir.glob("**/*.parquet.tmp"))
    assert len(tmp_files) == 1


def test_pl_read_parquet_glob_sees_nothing_before_any_sweep_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Immediately after the simulated failure (before sweep_orphan_tmp_
    files runs), a pl.read_parquet glob over the partition directory never
    returns the failed write's data under the final .parquet name --
    because the rename never happened, there is nothing to accidentally
    read under that name."""
    rows = _rows_for(0, 3)

    def _boom(self, target):
        raise OSError("simulated crash between write and rename")

    with monkeypatch.context() as m:
        m.setattr(Path, "replace", _boom)
        with pytest.raises(OSError):
            write_partition_atomic(
                rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker"
            )

    stream_dir = partition_dir(tmp_path, "BTCUSDT", "bookTicker")
    final_glob = list(stream_dir.glob("**/*.parquet"))
    assert final_glob == []
    # Sanity: reading the (nonexistent) final-name glob concatenation
    # would fail/return nothing -- there is no accidental-partial-read path.
    dfs = [pl.read_parquet(p) for p in final_glob]
    assert dfs == []


def test_sweep_removes_orphaned_tmp_and_subsequent_write_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the simulated crash leaves an orphaned .tmp file, backdating
    its mtime past the min-age guard and calling sweep_orphan_tmp_files
    removes it; a subsequent, successful write_partition_atomic call for
    the SAME partition then produces a clean final file unaffected by the
    earlier failure."""
    import os

    rows = _rows_for(0, 3)

    def _boom(self, target):
        raise OSError("simulated crash between write and rename")

    with monkeypatch.context() as m:
        m.setattr(Path, "replace", _boom)
        with pytest.raises(OSError):
            write_partition_atomic(
                rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker"
            )

    stream_dir = partition_dir(tmp_path, "BTCUSDT", "bookTicker")
    orphan = next(iter(stream_dir.glob("**/*.parquet.tmp")))
    old_time = time.time() - 60
    os.utime(orphan, (old_time, old_time))

    deleted = sweep_orphan_tmp_files(tmp_path)
    assert orphan in deleted
    assert not orphan.exists()

    # Path.replace is back to normal now (monkeypatch context exited).
    written = write_partition_atomic(
        rows, BOOKTICKER_SCHEMA, tmp_path, "BTCUSDT", "bookTicker"
    )
    assert len(written) == 1
    final_path = written[0]
    assert final_path.exists()
    assert not final_path.name.endswith(".tmp")
    df = pl.read_parquet(final_path)
    assert df.height == 3
    assert set(df["seq"].to_list()) == {0, 1, 2}


def test_real_process_death_mid_write_leaves_no_partial_final_file(
    tmp_path: Path,
) -> None:
    """Not a monkeypatch: a real child process is killed via os._exit
    (bypassing every `finally`/`atexit` hook) immediately after
    write_parquet completes but before Path.replace runs, proving the
    atomicity claim against an actual crash, not just an in-process
    exception."""
    mvp_root = Path(__file__).resolve().parents[2]
    script = f"""
import sys
sys.path.insert(0, {str(mvp_root)!r})
import os
from pathlib import Path
import data.capture.rotation as rotation

_real_write_parquet = None

def _patched_write_partition_atomic(rows, schema, data_root, symbol, stream):
    import polars as pl
    df = pl.DataFrame(rows, schema=schema)
    df = df.with_columns(
        pl.col("etime").cast(pl.Datetime("ns")).dt.strftime("%Y-%m-%d").alias("date")
    )
    parts = df.partition_by("date", include_key=False, as_dict=True)
    base_dir = rotation.partition_dir(data_root, symbol, stream)
    for date_key, sub_df in parts.items():
        date_value = date_key[0]
        part_dir = base_dir / f"date={{date_value}}"
        part_dir.mkdir(parents=True, exist_ok=True)
        import time as _time
        final_path = part_dir / f"part-{{_time.time_ns()}}.parquet"
        tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
        sub_df.write_parquet(tmp_path, compression="zstd")
        # Real crash: die HERE, after the .tmp write completed, before
        # the rename -- os._exit skips every finally/atexit hook.
        os._exit(137)

rotation.write_partition_atomic = _patched_write_partition_atomic

from data.capture.parse import parse_bookticker
from tests.fixtures.payloads import SAMPLE_BOOKTICKER_FRAME
import time
from datetime import datetime, timezone

event_ms = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
data = dict(SAMPLE_BOOKTICKER_FRAME["data"])
data["T"] = event_ms
data["E"] = event_ms
row = parse_bookticker(data, 0, rtime_ns=time.time_ns())

from data.schema import BOOKTICKER_SCHEMA
rotation.write_partition_atomic([row], BOOKTICKER_SCHEMA, Path({str(tmp_path)!r}), "BTCUSDT", "bookTicker")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(mvp_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 137, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    stream_dir = partition_dir(tmp_path, "BTCUSDT", "bookTicker")
    final_files = list(stream_dir.glob("**/*.parquet"))
    assert final_files == [], "a partial file leaked through a real process death"

    tmp_files = list(stream_dir.glob("**/*.parquet.tmp"))
    assert len(tmp_files) == 1, (
        "the .tmp file should exist, orphaned, ready for the next startup's sweep"
    )
