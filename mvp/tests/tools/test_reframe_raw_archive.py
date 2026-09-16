"""Tests for tools.reframe_raw_archive (03-06-PLAN.md Task 2).

Builds a fixture archive file in the OLD (pre-fix) per-message-frame format
-- a fresh `ZstdCompressor().stream_writer` per line, exactly what
`RawArchiveWriter.append()` did before this plan's fix -- then re-frames it
and asserts the decompressed line sequence is byte-identical to the
original.
"""

from __future__ import annotations

import time
from pathlib import Path

import orjson
import pytest
import zstandard

from tools.reframe_raw_archive import (
    ACTIVE_FILE_MIN_AGE_SECONDS,
    is_active_file,
    iter_lines,
    reframe_file,
)


def _write_old_format_fixture(path: Path, lines: list[bytes]) -> None:
    """Write `lines` through the OLD (pre-03-06) per-message-frame shape:
    one fresh `ZstdCompressor().stream_writer` per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        for line in lines:
            cctx = zstandard.ZstdCompressor()
            with cctx.stream_writer(fh, closefd=False) as writer:
                writer.write(line + b"\n")


def _archive_line(n: int, conn_id: str = "A") -> bytes:
    return orjson.dumps(
        {
            "rtime_ns": 1_000_000 + n,
            "conn_id": conn_id,
            "raw": orjson.dumps(
                {
                    "stream": "btcusdt@bookTicker",
                    "data": {"u": n, "s": "BTCUSDT", "b": "77199.90"},
                }
            ).decode(),
        }
    )


def test_iter_lines_reads_old_per_message_frame_format(tmp_path: Path):
    path = tmp_path / "fixture.ndjson.zst"
    lines = [_archive_line(i) for i in range(25)]
    _write_old_format_fixture(path, lines)

    recovered = list(iter_lines(path))
    assert recovered == lines


def test_reframe_produces_byte_identical_decompressed_line_sequence(
    tmp_path: Path,
):
    """`<done>`: a fixture built in the OLD (per-message-frame) format,
    re-framed by the tool, produces a byte-identical decompressed line
    sequence to the original."""
    path = tmp_path / "date=2026-09-01" / "conn_A.ndjson.zst"
    lines = [_archive_line(i) for i in range(500)]
    _write_old_format_fixture(path, lines)

    original_size = path.stat().st_size
    stats = reframe_file(
        path, flush_frame_every_messages=50, flush_frame_every_seconds=999.0
    )

    assert stats["lines"] == 500
    assert stats["original_bytes"] == original_size
    assert path.exists()  # replaced in place, same path

    reframed_lines = list(iter_lines(path))
    assert reframed_lines == lines

    # The whole point of the fix: the OLD per-message-frame format on this
    # repeated-shape fixture compresses far worse than the NEW shared-
    # context format re-framing produces.
    assert stats["reframed_bytes"] < original_size


def test_reframe_raises_and_leaves_original_untouched_on_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """If the identity assertion ever failed, the original must be left
    completely untouched -- simulated by monkeypatching write_reframed to
    drop a line."""
    import tools.reframe_raw_archive as reframe_module

    path = tmp_path / "fixture.ndjson.zst"
    lines = [_archive_line(i) for i in range(10)]
    _write_old_format_fixture(path, lines)
    original_bytes = path.read_bytes()

    real_write_reframed = reframe_module.write_reframed

    def _broken_write_reframed(lines_iter, dest, **kwargs):
        # Drop the last line -- simulates a hypothetical reframe bug.
        real_lines = list(lines_iter)[:-1]
        return real_write_reframed(iter(real_lines), dest, **kwargs)

    monkeypatch.setattr(reframe_module, "write_reframed", _broken_write_reframed)

    with pytest.raises(ValueError, match="line-sequence-identical"):
        reframe_module.reframe_file(path)

    # Original completely untouched.
    assert path.read_bytes() == original_bytes
    # No stray .tmp file left behind.
    assert not (path.parent / (path.name + ".reframe.tmp")).exists()


def test_is_active_file_excludes_today_and_recently_modified(tmp_path: Path):
    today = time.strftime("%Y-%m-%d", time.gmtime())
    active_path = tmp_path / f"date={today}" / "conn_A.ndjson.zst"
    active_path.parent.mkdir(parents=True)
    active_path.write_bytes(b"x")
    assert is_active_file(active_path) is True

    stale_path = tmp_path / "date=2020-01-01" / "conn_A.ndjson.zst"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_bytes(b"x")
    old_time = time.time() - (ACTIVE_FILE_MIN_AGE_SECONDS + 3600)
    import os

    os.utime(stale_path, (old_time, old_time))
    assert is_active_file(stale_path) is False


def test_is_active_file_belt_and_braces_recent_mtime_outside_today_dir(
    tmp_path: Path,
):
    """Belt-and-braces: a file NOT in today's date=... directory, but
    modified moments ago, is still excluded (clock-skew / day-boundary
    safety net)."""
    path = tmp_path / "date=2020-01-01" / "conn_A.ndjson.zst"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")  # mtime = now
    assert is_active_file(path) is True


def test_main_skips_active_and_reframes_the_rest(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    from tools.reframe_raw_archive import main

    today = time.strftime("%Y-%m-%d", time.gmtime())
    active = tmp_path / f"date={today}" / "conn_A.ndjson.zst"
    stale = tmp_path / "date=2020-01-01" / "conn_A.ndjson.zst"
    lines = [_archive_line(i) for i in range(20)]
    _write_old_format_fixture(active, lines)
    _write_old_format_fixture(stale, lines)
    old_time = time.time() - (ACTIVE_FILE_MIN_AGE_SECONDS + 3600)
    import os

    os.utime(stale, (old_time, old_time))
    active_bytes_before = active.read_bytes()

    exit_code = main([str(tmp_path / "date=*" / "conn_A.ndjson.zst")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "SKIP (active)" in out
    assert "REFRAMED" in out
    assert "1 file(s) reframed, 1 skipped" in out
    # Active file byte-for-byte untouched.
    assert active.read_bytes() == active_bytes_before
    # Stale file re-framed but line-sequence-identical.
    assert list(iter_lines(stale)) == lines
