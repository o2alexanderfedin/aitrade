"""Tests for RawArchiveWriter's fixed one-compressor-per-file framing
(03-06-PLAN.md Task 2).

The old (broken) behavior constructed a fresh `ZstdCompressor().stream_writer`
per message -- every ~300-byte line became its own zstd frame (measured
~1.5x, PROBE-RESULTS.md section 4). The fix is ONE long-lived compressor per
open file, `flush(FLUSH_BLOCK)` per message for durability, `flush(FLUSH_FRAME)`
periodically to bound how much of the tail a crash can leave undecoded.
"""

from __future__ import annotations

import time
from pathlib import Path

import orjson
import zstandard

from data.capture.ws_client import (
    DEFAULT_FLUSH_FRAME_EVERY_MESSAGES,
    DEFAULT_FLUSH_FRAME_EVERY_SECONDS,
    RawArchiveWriter,
)


def _today_path(archive_dir: Path, conn_id: str) -> Path:
    today = time.strftime("%Y-%m-%d", time.gmtime())
    return archive_dir / f"date={today}" / f"conn_{conn_id}.ndjson.zst"


def _decode_all_lines(path: Path) -> list[dict]:
    dctx = zstandard.ZstdDecompressor()
    with open(path, "rb") as fh:
        raw = dctx.stream_reader(fh, read_across_frames=True).read()
    lines = [line for line in raw.split(b"\n") if line]
    return [orjson.loads(line) for line in lines]


def _count_frames(data: bytes) -> int:
    """Count independently-openable zstd frames in `data` via
    `decompressobj().unused_data` -- each frame's decompressobj stops
    consuming input at its own frame boundary and reports the rest as
    `unused_data`, letting this walk the file frame-by-frame without
    assuming `read_across_frames` merges them."""
    dctx = zstandard.ZstdDecompressor()
    remaining = data
    frame_count = 0
    while remaining:
        do = dctx.decompressobj()
        do.decompress(remaining)
        assert do.eof, "decompressobj did not reach a frame boundary (eof=False)"
        remaining = do.unused_data
        frame_count += 1
    return frame_count


def test_defaults_are_the_planned_small_safe_cadence():
    assert DEFAULT_FLUSH_FRAME_EVERY_MESSAGES == 500
    assert DEFAULT_FLUSH_FRAME_EVERY_SECONDS == 5.0


def test_round_trip_multi_message_write_then_read_exact_order(tmp_path: Path):
    """`<done>`: a synthetic multi-message write-then-read round-trip
    through the fixed RawArchiveWriter decompresses to the exact original
    messages in order."""
    writer = RawArchiveWriter(tmp_path, conn_id="A")
    messages = [f'{{"n": {i}}}'.encode() for i in range(37)]
    for i, msg in enumerate(messages):
        writer.append(msg, rtime_ns=1_000 + i)
    writer.close()

    path = _today_path(tmp_path, "A")
    assert path.exists()
    entries = _decode_all_lines(path)
    assert len(entries) == len(messages)
    for i, entry in enumerate(entries):
        assert entry["rtime_ns"] == 1_000 + i
        assert entry["conn_id"] == "A"
        assert orjson.loads(entry["raw"]) == {"n": i}


def test_one_compressor_per_file_measured_compression_ratio_beats_per_message(
    tmp_path: Path,
):
    """Regression guard for the measured defect (PROBE-RESULTS.md section
    4): with a shared compression context across ~300-byte NDJSON lines,
    the fixed writer must compress meaningfully better than the old
    per-message-frame ~1.5x. Uses a realistic repeated-field-shape payload
    (not maximally-compressible all-zeros) so the ratio reflects real NDJSON
    structure, not a degenerate best case."""
    writer = RawArchiveWriter(tmp_path, conn_id="A")
    raw_total = 0
    n = 300
    for i in range(n):
        msg = orjson.dumps(
            {
                "stream": "btcusdt@bookTicker",
                "data": {
                    "e": "bookTicker",
                    "u": 11538015451847 + i,
                    "s": "BTCUSDT",
                    "b": "77199.90",
                    "B": "5.832",
                    "a": "77200.00",
                    "A": "12.001",
                    "T": 1789191814815 + i,
                    "E": 1789191814815 + i,
                },
            }
        )
        raw_total += len(msg)
        writer.append(msg, rtime_ns=i)
    writer.close()

    path = _today_path(tmp_path, "A")
    compressed_total = path.stat().st_size
    ratio = raw_total / compressed_total
    # Old per-message-frame behavior measured ~1.5x on real ~300B lines
    # (PROBE-RESULTS.md section 4). The fixed shared-context writer must
    # clear that by a wide margin -- 4x is a conservative floor that still
    # discriminates against the old defect resurfacing, well short of the
    # ~10x PROBE-RESULTS.md measured on real capture data (this fixture's
    # small size and FLUSH_FRAME cadence cost some ratio vs. an unbounded
    # single-frame file).
    assert ratio > 4.0, (
        f"compression ratio {ratio:.2f}x did not clear the 4x floor "
        f"(raw={raw_total}, compressed={compressed_total})"
    )
    entries = _decode_all_lines(path)
    assert len(entries) == n


def test_flush_frame_cadence_by_message_count(tmp_path: Path):
    """Every `flush_frame_every_messages` messages, a FLUSH_FRAME closes a
    real frame boundary -- verified by counting independently-openable zstd
    frames in the output, not just decoding across all of them."""
    writer = RawArchiveWriter(
        tmp_path,
        conn_id="A",
        flush_frame_every_messages=4,
        flush_frame_every_seconds=999.0,  # never trips on time in this test
    )
    for i in range(12):
        writer.append(f'{{"n": {i}}}'.encode(), rtime_ns=i)
    writer.close()

    path = _today_path(tmp_path, "A")
    with open(path, "rb") as fh:
        data = fh.read()
    frame_count = _count_frames(data)

    # 12 messages, FLUSH_FRAME every 4 -> at least 3 frame boundaries from
    # the cadence itself, plus the final close()'s forced FLUSH_FRAME (which
    # is a no-op frame-boundary-wise if the last cadence flush already
    # landed exactly on message 12, or one more small frame otherwise).
    assert frame_count >= 3, f"expected >=3 frames, found {frame_count}"

    entries = _decode_all_lines(path)
    assert len(entries) == 12


def test_crash_mid_write_truncates_at_most_tail_of_one_frame(tmp_path: Path):
    """`<verification_that_actually_matters>`: proof that a crash mid-write
    truncates at most the tail of one frame rather than corrupting the
    file -- simulated by cutting the file at a byte offset strictly inside
    the LAST (unflushed-to-FLUSH_FRAME) frame's compressed bytes, then
    showing every message up through the last FLUSH_FRAME boundary still
    decodes cleanly."""
    writer = RawArchiveWriter(
        tmp_path,
        conn_id="A",
        flush_frame_every_messages=5,
        flush_frame_every_seconds=999.0,
    )
    for i in range(5):
        writer.append(f'{{"n": {i}}}'.encode(), rtime_ns=i)
    # At this point message 4 (0-indexed) triggered a FLUSH_FRAME -- 5
    # messages, cadence=5. Record the clean-frame-boundary size.
    path = _today_path(tmp_path, "A")
    size_after_first_frame_boundary = path.stat().st_size

    for i in range(5, 8):
        writer.append(f'{{"n": {i}}}'.encode(), rtime_ns=i)
    # writer object still open (not closed) -- messages 5,6,7 are only
    # FLUSH_BLOCK'd, not yet FLUSH_FRAME'd (cadence=5, only 3 more written).
    size_before_crash = path.stat().st_size
    assert size_before_crash > size_after_first_frame_boundary

    # Simulate a crash: truncate the file partway through the still-open
    # final frame's compressed bytes (strictly between the last clean frame
    # boundary and the current end-of-file), WITHOUT calling writer.close()
    # (a real crash never runs cleanup).
    cut_at = (size_after_first_frame_boundary + size_before_crash) // 2
    assert size_after_first_frame_boundary < cut_at < size_before_crash
    with open(path, "r+b") as fh:
        fh.truncate(cut_at)

    dctx = zstandard.ZstdDecompressor()
    with open(path, "rb") as fh:
        recovered_raw = dctx.stream_reader(fh, read_across_frames=True).read()
    recovered_lines = [line for line in recovered_raw.split(b"\n") if line]
    # The cut lands mid-line inside the still-open final frame -- the last
    # split "line" may be an incomplete JSON fragment (truncated exactly
    # where the crash happened). Decode every line that parses cleanly;
    # a trailing undecodable fragment is the EXPECTED shape of "at most the
    # tail of one frame is lost", not a test bug.
    recovered = []
    for line in recovered_lines:
        try:
            recovered.append(orjson.loads(line))
        except orjson.JSONDecodeError:
            break

    # Every message up through the last FLUSH_FRAME boundary (0..4) must
    # still be present -- the crash only cost messages after that boundary
    # (5, 6, 7 -- only FLUSH_BLOCK'd, no frame boundary yet), never
    # corrupting or losing the already-frame-closed prefix.
    recovered_ns = [orjson.loads(e["raw"])["n"] for e in recovered]
    assert recovered_ns[:5] == [0, 1, 2, 3, 4]
    # And the crash must not have fabricated data beyond what was actually
    # written before the cut -- never more than the 8 messages appended.
    assert len(recovered_ns) <= 8

    # Deliberately never call writer.close(): a real crash never runs
    # cleanup, and the underlying file was truncated out from under the
    # writer's own buffered C-extension state -- attempting to flush/close
    # it now would not represent anything a real process does. tmp_path's
    # own teardown removes the file regardless of open handles.
