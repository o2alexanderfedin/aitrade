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


# --- WR-09 (03-REVIEW.md): durable swap, no orphaned multi-GB tmp files ----


def test_failed_reframe_leaves_no_tmp_file_and_original_untouched(tmp_path: Path):
    """A source that fails to decode mid-stream (e.g. a CR-01-corrupted file)
    must not leave `<name>.reframe.tmp` behind -- on a real 7 GB day that is
    several GB of orphaned disk."""
    path = tmp_path / "date=2020-01-01" / "conn_A.ndjson.zst"
    _write_old_format_fixture(path, [_archive_line(i) for i in range(50)])
    with open(path, "ab") as fh:
        fh.write(b"\x28\xb5\x2f\xfd" + b"\xff" * 64)  # a corrupt trailing frame
    original = path.read_bytes()

    with pytest.raises(zstandard.ZstdError):
        reframe_file(path)

    assert not path.with_name(path.name + ".reframe.tmp").exists()
    assert path.read_bytes() == original


def test_reframed_file_is_fsynced_before_replace_and_directory_after(
    tmp_path: Path, monkeypatch
):
    import os

    import tools.reframe_raw_archive as tool

    events: list[str] = []
    real_replace = os.replace
    monkeypatch.setattr(tool, "_fsync_fd", lambda fd: events.append("fsync_file"))
    monkeypatch.setattr(tool, "_fsync_dir", lambda d: events.append("fsync_dir"))

    def spy_replace(src, dst):
        events.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)

    path = tmp_path / "date=2020-01-01" / "conn_A.ndjson.zst"
    lines = [_archive_line(i) for i in range(30)]
    _write_old_format_fixture(path, lines)
    reframe_file(path)

    assert events == ["fsync_file", "replace", "fsync_dir"]
    assert list(iter_lines(path)) == lines


# --- WR-14 (03-REVIEW-ITER2.md): a crashed segment is not a clean EOF -------


def _crashed_segment(tmp_path: Path, n: int, truncate_bytes: int) -> Path:
    """Run the REAL `RawArchiveWriter` in a child process: `n` appends, then
    `os._exit(0)` (no close, so no final FLUSH_FRAME), then cut
    `truncate_bytes` off the end -- the post-crash state CR-01 makes normal.
    Aged past the active-file window."""
    import os
    import subprocess
    import sys

    archive = tmp_path / "archive"
    code = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "from data.capture.ws_client import RawArchiveWriter\n"
        "w = RawArchiveWriter(Path(sys.argv[1]), 'A', flush_frame_every_messages=100,"
        " flush_frame_every_seconds=1e9)\n"
        "for i in range(int(sys.argv[2])):\n"
        '    w.append(\'{"u": %d, "pad": "%s"}\' % (i, \'x\' * 40), 1_000 + i)\n'
        "os._exit(0)\n"
    )
    subprocess.run(
        [sys.executable, "-c", code, str(archive), str(n)],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    (segment,) = list(archive.glob("date=*/conn_A.*.ndjson.zst"))
    size = segment.stat().st_size
    with open(segment, "r+b") as fh:
        fh.truncate(size - truncate_bytes)
    old = time.time() - (ACTIVE_FILE_MIN_AGE_SECONDS + 3600)
    os.utime(segment, (old, old))
    # Move out of today's date= directory so is_active_file does not skip it.
    aged = tmp_path / "date=2020-01-01" / segment.name
    aged.parent.mkdir(parents=True, exist_ok=True)
    segment.rename(aged)
    os.utime(aged, (old, old))
    return aged


def _zstd_cli_says_truncated(path: Path) -> bool | None:
    import shutil
    import subprocess

    if shutil.which("zstd") is None:
        return None
    return subprocess.run(["zstd", "-t", "-q", str(path)]).returncode != 0


def test_iter_lines_reports_a_truncated_segment_and_drops_the_partial_line(
    tmp_path: Path,
):
    from tools.reframe_raw_archive import ArchiveReadReport

    segment = _crashed_segment(tmp_path, n=1050, truncate_bytes=7)
    assert _zstd_cli_says_truncated(segment) in (True, None)

    report = ArchiveReadReport()
    lines = list(iter_lines(segment, report=report))

    assert report.truncated is True
    assert report.complete_lines == len(lines)
    assert 1000 <= len(lines) < 1050
    for line in lines:  # nothing half-written was passed off as a line
        orjson.loads(line)
    assert report.complete_frame_bytes < segment.stat().st_size


def test_iter_lines_reports_a_clean_file_as_not_truncated(tmp_path: Path):
    from tools.reframe_raw_archive import ArchiveReadReport

    path = tmp_path / "fixture.ndjson.zst"
    lines = [_archive_line(i) for i in range(40)]
    _write_old_format_fixture(path, lines)
    report = ArchiveReadReport()
    assert list(iter_lines(path, report=report)) == lines
    assert report.truncated is False
    assert report.complete_lines == 40


def test_unterminated_segment_without_truncation_is_still_reported(tmp_path: Path):
    """A crash with no byte lost still leaves the last frame unterminated."""
    from tools.reframe_raw_archive import ArchiveReadReport

    segment = _crashed_segment(tmp_path, n=250, truncate_bytes=0)
    report = ArchiveReadReport()
    lines = list(iter_lines(segment, report=report))
    assert report.truncated is True
    assert len(lines) == 250


def test_reframe_file_leaves_a_truncated_segment_untouched_by_default(tmp_path: Path):
    segment = _crashed_segment(tmp_path, n=1050, truncate_bytes=7)
    before = segment.read_bytes()

    stats = reframe_file(segment)

    assert stats["status"] == "truncated"
    assert 1000 <= stats["lines"] < 1050
    assert segment.read_bytes() == before
    assert list(segment.parent.iterdir()) == [segment]  # no tmp, no .crashed


def test_main_surfaces_truncation_keeps_going_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    import os

    from tools.reframe_raw_archive import main

    segment = _crashed_segment(tmp_path, n=1050, truncate_bytes=7)
    clean = segment.parent / "conn_B.ndjson.zst"
    _write_old_format_fixture(clean, [_archive_line(i) for i in range(20)])
    old = time.time() - (ACTIVE_FILE_MIN_AGE_SECONDS + 3600)
    os.utime(clean, (old, old))
    empty = segment.parent / "conn_C.1.ndjson.zst"
    empty.write_bytes(b"")
    os.utime(empty, (old, old))
    before = segment.read_bytes()

    exit_code = main([str(segment.parent / "conn_*.ndjson.zst")])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "TRUNCATED" in out and "complete lines recovered" in out
    assert "REFRAMED" in out  # the clean file was still processed
    assert "SKIP (empty segment)" in out
    assert segment.read_bytes() == before


def test_accept_truncated_reframes_recovered_lines_and_keeps_the_original(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    from tools.reframe_raw_archive import main

    segment = _crashed_segment(tmp_path, n=1050, truncate_bytes=7)
    before = segment.read_bytes()
    recovered = list(iter_lines(segment))

    exit_code = main([str(segment), "--accept-truncated"])

    assert exit_code == 0
    crashed = segment.with_name(segment.name + ".crashed")
    assert crashed.read_bytes() == before
    assert list(iter_lines(segment)) == recovered
    assert _zstd_cli_says_truncated(segment) in (False, None)
    assert "truncated tail" in capsys.readouterr().out


# --- IN-18 (03-REVIEW-ITER3.md): one corrupt segment must not end the batch --


def test_main_reports_a_corrupt_segment_keeps_going_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    import os

    from tools.reframe_raw_archive import main

    old = time.time() - (ACTIVE_FILE_MIN_AGE_SECONDS + 3600)
    day = tmp_path / "date=2020-01-01"
    corrupt = day / "a_corrupt.ndjson.zst"
    _write_old_format_fixture(corrupt, [_archive_line(i) for i in range(20)])
    with open(corrupt, "ab") as fh:
        fh.write(b"\x01\x02\x03\x04\x05\x06\x07\x08")  # data corruption, not a cut
    clean = day / "c_clean.ndjson.zst"
    _write_old_format_fixture(clean, [_archive_line(i) for i in range(20)])
    for path in (corrupt, clean):
        os.utime(path, (old, old))
    corrupt_before = corrupt.read_bytes()

    exit_code = main([str(day / "*.ndjson.zst")])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert f"CORRUPT: {corrupt}" in out
    assert f"REFRAMED: {clean}" in out  # sorted after the corrupt one: still done
    assert "1 corrupt" in out  # the summary line counts it
    assert corrupt.read_bytes() == corrupt_before
    assert sorted(p.name for p in day.iterdir()) == [corrupt.name, clean.name]
