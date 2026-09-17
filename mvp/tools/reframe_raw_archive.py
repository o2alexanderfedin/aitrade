"""Offline decompress-then-recompress tool for the existing raw capture
archive (03-06-PLAN.md Task 2).

The pre-fix `RawArchiveWriter` constructed a fresh `ZstdCompressor().stream_
writer` PER MESSAGE, so every ~300-byte NDJSON line became its own zstd
frame (measured ~1.5x instead of the ~10x zstd gives with a shared
compression context, PROBE-RESULTS.md section 4). This tool re-frames
existing archive files through the fixed writer's shape (one long-lived
compressor per file, `flush(FLUSH_BLOCK)` per message, `flush(FLUSH_FRAME)`
periodically), asserting the decompressed line sequence is unchanged before
replacing anything.

Invoked as `python -m tools.reframe_raw_archive <path-glob>` (process
cwd = mvp/).

STREAMING BY DESIGN: a raw archive file can be tens of GB compressed and
~10x that decompressed. Every function here processes lines via a bounded
read-chunk generator (`iter_lines`) rather than `.read()`-ing a whole file --
both the reframe pass and the post-reframe identity check stream both sides
in lockstep, so memory use stays bounded regardless of file size (see
`_first_line_mismatch`).

SAFETY: this module's `reframe_file()` does an in-place atomic
`.tmp`-suffixed-sibling-write + `os.replace()`, the same idiom used
everywhere else in this codebase (`data/capture/rotation.py`'s
`write_parquet_atomic`), made DURABLE (03-REVIEW.md WR-09): the tmp file is
fsynced (`F_FULLFSYNC` on macOS) before the identity check and the swap,
the directory is fsynced after it, and any failure removes the tmp file. Per 03-06-PLAN.md's own safety instructions, this
tool must NEVER be pointed at the live capture daemon's `raw/` tree while
the daemon is running -- the executor that ran this offline run pointed it
at a COPY under a scratch root, never at `/Volumes/ProjectsSSD/aihedgefund/
capture/` directly (see 03-06-SUMMARY.md's Deviations section for why: the
plan's literal text says run the tool "for real against the existing ~21GB
raw archive", but the daemon's raw/ tree is explicitly READ-ONLY to the
executor until a human approves the Task 3 checkpoint restart -- the actual
production swap is a follow-up, human-directed action, not automated here).
`--skip-active` (default on) additionally refuses to touch any file in
TODAY's UTC `date=...` directory, or any file modified in the last
`ACTIVE_FILE_MIN_AGE_SECONDS` -- both a defense against ever racing a live
writer's in-flight appends, and a safety net if this tool is ever run
directly against a real, currently-capturing tree.
"""

from __future__ import annotations

import argparse
import glob
import os
import time
from pathlib import Path

import zstandard

from data.capture.ws_client import (
    DEFAULT_FLUSH_FRAME_EVERY_MESSAGES,
    DEFAULT_FLUSH_FRAME_EVERY_SECONDS,
)

PKG_ROOT = Path(__file__).resolve().parents[1]

#: A file this recently modified is presumed to still be open for writes --
#: either the daemon's own currently-active file (see module docstring) or,
#: in a test/scratch context, still being written by whatever produced it.
#: Generous margin above the ~1s rotation-close latency `RawArchiveWriter`
#: itself exhibits.
ACTIVE_FILE_MIN_AGE_SECONDS = 300.0

#: Bounded read-chunk size for streaming decompression -- large enough to
#: amortize per-call overhead, small enough that memory use never scales
#: with file size.
READ_CHUNK_BYTES = 4 * 1024 * 1024


def is_active_file(path: Path, *, now: float | None = None) -> bool:
    """Return True if `path` should be treated as still-open-for-writes and
    therefore excluded from re-framing: either it lives in TODAY's UTC
    `date=...` directory (matching `RawArchiveWriter`'s own rotation unit),
    or its mtime is within `ACTIVE_FILE_MIN_AGE_SECONDS` of `now` (belt-and-
    braces against clock skew or a stale "today" computed right at a day
    boundary)."""
    now = time.time() if now is None else now
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    if f"date={today}" in path.parts:
        return True
    age = now - path.stat().st_mtime
    return age < ACTIVE_FILE_MIN_AGE_SECONDS


def iter_lines(path: Path, *, chunk_size: int = READ_CHUNK_BYTES):
    """Yield raw NDJSON lines (bytes, no trailing newline) decompressed from
    `path`, streaming in `chunk_size`-byte reads.

    `read_across_frames=True` makes this correct for BOTH the OLD
    (per-message-frame) and NEW (shared-context) archive formats -- a
    zstd stream_reader transparently continues across independent frame
    boundaries either way, so the same generator verifies the reframe
    output against the original without needing format-specific branches.
    Never loads the full decompressed content into memory: a partial
    trailing line is buffered across `read()` calls, not the whole file.
    """
    dctx = zstandard.ZstdDecompressor()
    with open(path, "rb") as fh:
        reader = dctx.stream_reader(fh, read_across_frames=True)
        buf = b""
        while True:
            chunk = reader.read(chunk_size)
            if not chunk:
                break
            buf += chunk
            parts = buf.split(b"\n")
            buf = parts.pop()  # last element may be an incomplete line
            for line in parts:
                if line:
                    yield line
        if buf:
            yield buf


def _fsync_fd(fd: int) -> None:
    """Force `fd`'s data to stable storage. On macOS `os.fsync` only reaches
    the drive's cache; `F_FULLFSYNC` asks the drive to flush it (APFS on this
    battery-powered host), falling back to `fsync` where unsupported."""
    try:
        import fcntl

        full_fsync = getattr(fcntl, "F_FULLFSYNC", None)
    except ImportError:  # non-POSIX
        full_fsync = None
    if full_fsync is not None:
        try:
            fcntl.fcntl(fd, full_fsync)
            return
        except OSError:
            pass
    os.fsync(fd)


def _fsync_dir(directory: Path) -> None:
    """Make a rename in `directory` durable (fsync the directory entry)."""
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        _fsync_fd(dir_fd)
    finally:
        os.close(dir_fd)


def write_reframed(
    lines,
    dest: Path,
    *,
    flush_frame_every_messages: int = DEFAULT_FLUSH_FRAME_EVERY_MESSAGES,
    flush_frame_every_seconds: float = DEFAULT_FLUSH_FRAME_EVERY_SECONDS,
) -> int:
    """Write `lines` (an iterable of bytes, no trailing newline) to `dest`
    through the NEW one-compressor-per-file framing -- the same
    FLUSH_BLOCK-per-message / FLUSH_FRAME-periodic cadence as the fixed
    `RawArchiveWriter.append()`. Returns the number of lines written."""
    cctx = zstandard.ZstdCompressor()
    line_count = 0
    with open(dest, "wb") as fh:
        writer = cctx.stream_writer(fh, closefd=False)
        since_frame_flush = 0
        last_frame_flush = time.monotonic()
        for line in lines:
            writer.write(line + b"\n")
            writer.flush(zstandard.FLUSH_BLOCK)
            since_frame_flush += 1
            line_count += 1
            now = time.monotonic()
            if (
                since_frame_flush >= flush_frame_every_messages
                or now - last_frame_flush >= flush_frame_every_seconds
            ):
                writer.flush(zstandard.FLUSH_FRAME)
                since_frame_flush = 0
                last_frame_flush = now
        writer.flush(zstandard.FLUSH_FRAME)
        writer.close()
        # Durable BEFORE the caller's identity check and swap (03-REVIEW.md
        # WR-09): otherwise the check reads the page cache and proves nothing
        # about what reached the disk.
        fh.flush()
        _fsync_fd(fh.fileno())
    return line_count


def _first_line_mismatch(path_a: Path, path_b: Path) -> tuple[int, bytes, bytes] | None:
    """Stream both files' decompressed line sequences in lockstep (never
    materializing either fully in memory), returning `(index, line_a,
    line_b)` for the first mismatching position -- including a length
    mismatch, reported at the shorter sequence's end (with the missing
    side's line as `b"<missing>"`) -- or `None` if every line matches and
    both sequences are the same length."""
    gen_a = iter_lines(path_a)
    gen_b = iter_lines(path_b)
    sentinel = object()
    idx = 0
    while True:
        a = next(gen_a, sentinel)
        b = next(gen_b, sentinel)
        if a is sentinel and b is sentinel:
            return None
        if a != b:
            return (
                idx,
                a if a is not sentinel else b"<missing>",
                b if b is not sentinel else b"<missing>",
            )
        idx += 1


def reframe_file(
    path: Path,
    *,
    flush_frame_every_messages: int = DEFAULT_FLUSH_FRAME_EVERY_MESSAGES,
    flush_frame_every_seconds: float = DEFAULT_FLUSH_FRAME_EVERY_SECONDS,
) -> dict:
    """Re-frame `path` in place: decompress-then-recompress through the NEW
    framing into a `.tmp`-suffixed sibling, assert the decompressed line
    sequence is unchanged (streamed lockstep comparison, never loaded fully
    into memory), then atomically replace the original. Raises `ValueError`
    (original left completely untouched) if the identity assertion fails.

    Returns `{"path", "lines", "original_bytes", "reframed_bytes"}`.
    """
    original_size = path.stat().st_size
    tmp_path = path.with_name(path.name + ".reframe.tmp")

    # Any failure before the swap (a corrupt source raising mid-decode, the
    # identity assertion, a full disk, Ctrl-C) removes the possibly multi-GB
    # tmp file and leaves the original untouched (03-REVIEW.md WR-09).
    try:
        line_count = write_reframed(
            iter_lines(path),
            tmp_path,
            flush_frame_every_messages=flush_frame_every_messages,
            flush_frame_every_seconds=flush_frame_every_seconds,
        )

        mismatch = _first_line_mismatch(path, tmp_path)
        if mismatch is not None:
            idx, line_a, line_b = mismatch
            raise ValueError(
                f"{path}: line-sequence-identical assertion FAILED at line "
                f"{idx}: original={line_a!r} reframed={line_b!r}. Original "
                "file left completely untouched."
            )

        new_size = tmp_path.stat().st_size
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    # tmp data is already fsynced (write_reframed); rename, then fsync the
    # directory so the rename itself survives a power loss.
    os.replace(tmp_path, path)
    _fsync_dir(path.parent)

    return {
        "path": str(path),
        "lines": line_count,
        "original_bytes": original_size,
        "reframed_bytes": new_size,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path_glob", help="glob of raw archive file(s) to re-frame in place"
    )
    parser.add_argument(
        "--skip-active",
        dest="skip_active",
        action="store_true",
        default=True,
        help="exclude today's date=... directory and recently-modified "
        "files (default: on)",
    )
    parser.add_argument(
        "--no-skip-active",
        dest="skip_active",
        action="store_false",
        help="do NOT exclude any file by recency -- dangerous, testing "
        "only; never use against a live capture tree",
    )
    args = parser.parse_args(argv)

    candidates = sorted(Path(p) for p in glob.glob(args.path_glob))
    if not candidates:
        print(f"no files matched glob: {args.path_glob}")
        return 0

    total_before = 0
    total_after = 0
    processed = 0
    skipped = 0
    for path in candidates:
        if args.skip_active and is_active_file(path):
            skipped += 1
            print(f"SKIP (active): {path} mtime={path.stat().st_mtime}")
            continue
        stats = reframe_file(path)
        before = stats["original_bytes"]
        after = stats["reframed_bytes"]
        total_before += before
        total_after += after
        ratio = before / after if after else float("inf")
        print(
            f"REFRAMED: {path} lines={stats['lines']} before={before} "
            f"after={after} ratio={ratio:.2f}x"
        )
        processed += 1

    overall_ratio = (total_before / total_after) if total_after else float("inf")
    print(
        f"done: {processed} file(s) reframed, {skipped} skipped (active), "
        f"total_before={total_before} total_after={total_after} "
        f"overall_ratio={overall_ratio:.2f}x"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
