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

CRASHED SEGMENTS (03-REVIEW-ITER2.md WR-14): since CR-01 every capture run
writes its own segment, so a run killed without `close()` (SIGKILL, OOM,
battery at 0 %) leaves a segment whose last frame is unterminated, possibly
cut mid-block -- a normal post-crash state, not a rare corruption. zstd's
streaming reader reports such a file as a clean EOF. `iter_lines` therefore
walks the zstd frame/block headers itself and fills an `ArchiveReadReport`:
`truncated`, the number of complete lines recovered, and the byte offset
where the last complete frame ends. A half-written final line is dropped,
never passed off as a line. `reframe_file` leaves a truncated segment
untouched unless `accept_truncated=True` (`--accept-truncated`), in which
case the recovered lines are reframed into place and the original is kept
beside it as `<name>.crashed`; `main` reports every truncated file, keeps
going, and exits 1 if any was left untouched. A 0-byte segment (a run that
died between opening its segment and the first append) is benign and
skipped.
"""

from __future__ import annotations

import argparse
import glob
import mmap
import os
import time
from dataclasses import dataclass
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


@dataclass
class ArchiveReadReport:
    """Filled in by `iter_lines` once the generator is exhausted."""

    truncated: bool = False
    complete_lines: int = 0
    #: Offset just past the last COMPLETE zstd frame (== file size when clean).
    complete_frame_bytes: int = 0
    file_bytes: int = 0
    #: Bytes of a half-written final line that was dropped (truncated only).
    dropped_partial_line_bytes: int = 0


_ZSTD_FRAME_MAGIC = 0xFD2FB528
_SKIPPABLE_MAGIC_MIN = 0x184D2A50
_SKIPPABLE_MAGIC_MAX = 0x184D2A5F
_MAX_FRAME_HEADER_BYTES = 18


def complete_frames_end(path: Path) -> int:
    """Offset just past the last complete zstd frame in `path`, found by
    walking frame and block headers (no decompression). Equal to the file
    size exactly when the file ends on a frame boundary; smaller when the
    last frame is unterminated or cut off. Raises `zstandard.ZstdError` on
    bytes that are not a zstd frame at all."""
    size = path.stat().st_size
    if size == 0:
        return 0
    with (
        open(path, "rb") as fh,
        mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm,
    ):
        pos = 0
        while pos < size:
            if size - pos < 4:
                return pos
            magic = int.from_bytes(mm[pos : pos + 4], "little")
            if _SKIPPABLE_MAGIC_MIN <= magic <= _SKIPPABLE_MAGIC_MAX:
                if size - pos < 8:
                    return pos
                end = pos + 8 + int.from_bytes(mm[pos + 4 : pos + 8], "little")
                if end > size:
                    return pos
                pos = end
                continue
            if magic != _ZSTD_FRAME_MAGIC:
                raise zstandard.ZstdError(
                    f"{path}: no zstd frame magic at byte {pos} -- corrupt, not truncated"
                )
            head = bytes(mm[pos : pos + _MAX_FRAME_HEADER_BYTES])
            try:
                header_bytes = zstandard.frame_header_size(head)
                has_checksum = zstandard.get_frame_parameters(head).has_checksum
            except zstandard.ZstdError:
                if size - pos < _MAX_FRAME_HEADER_BYTES:
                    return pos  # header itself cut off
                raise
            q = pos + header_bytes
            while True:
                if size - q < 3:
                    return pos
                block_header = int.from_bytes(mm[q : q + 3], "little")
                block_type = (block_header >> 1) & 3
                if block_type == 3:
                    raise zstandard.ZstdError(
                        f"{path}: reserved zstd block type at byte {q} -- corrupt"
                    )
                q += 3 + (1 if block_type == 1 else block_header >> 3)
                if q > size:
                    return pos
                if block_header & 1:  # last block of the frame
                    break
            if has_checksum:
                q += 4
                if q > size:
                    return pos
            pos = q
        return pos


def iter_lines(
    path: Path,
    *,
    chunk_size: int = READ_CHUNK_BYTES,
    report: ArchiveReadReport | None = None,
):
    """Yield raw NDJSON lines (bytes, no trailing newline) decompressed from
    `path`, streaming in `chunk_size`-byte reads.

    `read_across_frames=True` makes this correct for BOTH the OLD
    (per-message-frame) and NEW (shared-context) archive formats -- a
    zstd stream_reader transparently continues across independent frame
    boundaries either way, so the same generator verifies the reframe
    output against the original without needing format-specific branches.
    Never loads the full decompressed content into memory: a partial
    trailing line is buffered across `read()` calls, not the whole file.

    The stream reader returns a clean EOF on a truncated file, so after the
    last read the frame structure is walked (`complete_frames_end`, WR-14).
    On a truncated file the unterminated final line is dropped. `report`,
    if given, is filled in when the generator is exhausted.
    """
    report = report if report is not None else ArchiveReadReport()
    dctx = zstandard.ZstdDecompressor()
    count = 0
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
                    count += 1
                    yield line
    report.file_bytes = path.stat().st_size
    report.complete_frame_bytes = complete_frames_end(path)
    report.truncated = report.complete_frame_bytes < report.file_bytes
    if buf and report.truncated:
        report.dropped_partial_line_bytes = len(buf)
    elif buf:
        count += 1
        yield buf
    report.complete_lines = count


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
    accept_truncated: bool = False,
) -> dict:
    """Re-frame `path` in place: decompress-then-recompress through the NEW
    framing into a `.tmp`-suffixed sibling, assert the decompressed line
    sequence is unchanged (streamed lockstep comparison, never loaded fully
    into memory), then atomically replace the original. Raises `ValueError`
    (original left completely untouched) if the identity assertion fails.

    A 0-byte file returns `status="empty"` untouched. A truncated/crashed
    segment (WR-14) returns `status="truncated"` untouched, with the number
    of complete lines it holds, unless `accept_truncated=True`: then the
    recovered complete lines are reframed into place and the original bytes
    are kept as `<name>.crashed` (hard-linked BEFORE the swap, so a copy of
    the original exists at every instant), `status="reframed_truncated"`.

    Returns `{"path", "status", "lines", "original_bytes", "reframed_bytes",
    "complete_frame_bytes", "dropped_partial_line_bytes"}`.
    """
    original_size = path.stat().st_size
    result = {
        "path": str(path),
        "status": "reframed",
        "lines": 0,
        "original_bytes": original_size,
        "reframed_bytes": None,
        "complete_frame_bytes": original_size,
        "dropped_partial_line_bytes": 0,
    }
    if original_size == 0:
        return {**result, "status": "empty", "reframed_bytes": 0}

    if complete_frames_end(path) < original_size and not accept_truncated:
        report = ArchiveReadReport()
        for _line in iter_lines(path, report=report):
            pass
        return {
            **result,
            "status": "truncated",
            "lines": report.complete_lines,
            "complete_frame_bytes": report.complete_frame_bytes,
            "dropped_partial_line_bytes": report.dropped_partial_line_bytes,
        }

    tmp_path = path.with_name(path.name + ".reframe.tmp")
    crashed_path = path.with_name(path.name + ".crashed")
    report = ArchiveReadReport()

    # Any failure before the swap (a corrupt source raising mid-decode, the
    # identity assertion, a full disk, Ctrl-C) removes the possibly multi-GB
    # tmp file and leaves the original untouched (03-REVIEW.md WR-09).
    try:
        line_count = write_reframed(
            iter_lines(path, report=report),
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
        if report.truncated:
            if not accept_truncated:  # the file changed since the pre-check
                raise ValueError(f"{path}: became truncated during the reframe")
            os.link(path, crashed_path)  # FileExistsError: never overwrite one
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    # tmp data is already fsynced (write_reframed); rename, then fsync the
    # directory so the rename itself survives a power loss.
    os.replace(tmp_path, path)
    _fsync_dir(path.parent)

    return {
        **result,
        "status": "reframed_truncated" if report.truncated else "reframed",
        "lines": line_count,
        "reframed_bytes": new_size,
        "complete_frame_bytes": report.complete_frame_bytes,
        "dropped_partial_line_bytes": report.dropped_partial_line_bytes,
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
    parser.add_argument(
        "--accept-truncated",
        dest="accept_truncated",
        action="store_true",
        default=False,
        help="reframe a truncated/crashed segment's complete lines into place, "
        "keeping the original as <name>.crashed (default: report it, leave it "
        "untouched, exit 1)",
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
    empty = 0
    truncated_untouched = 0
    corrupt = 0
    for path in candidates:
        if args.skip_active and is_active_file(path):
            skipped += 1
            print(f"SKIP (active): {path} mtime={path.stat().st_mtime}")
            continue
        try:
            stats = reframe_file(path, accept_truncated=args.accept_truncated)
        except zstandard.ZstdError as exc:
            # 03-REVIEW-ITER3.md IN-18: data corruption (not a truncation) in
            # one segment used to abort the batch, so later files were never
            # processed or reported. `reframe_file` leaves it untouched (no
            # swap happens before a full decode); report it and go on.
            corrupt += 1
            print(f"CORRUPT: {path} ({exc}); left untouched")
            continue
        if stats["status"] == "empty":
            empty += 1
            print(f"SKIP (empty segment): {path} is 0 bytes (benign)")
            continue
        tail = (
            f"truncated tail: last complete frame ends at byte "
            f"{stats['complete_frame_bytes']} of {stats['original_bytes']}, "
            f"{stats['lines']} complete lines recovered, "
            f"{stats['dropped_partial_line_bytes']} bytes of a partial final line dropped"
        )
        if stats["status"] == "truncated":
            truncated_untouched += 1
            print(
                f"TRUNCATED: {path} {tail}; left untouched "
                "(re-run with --accept-truncated to reframe the recovered lines "
                "and keep the original as <name>.crashed)"
            )
            continue
        before = stats["original_bytes"]
        after = stats["reframed_bytes"]
        total_before += before
        total_after += after
        ratio = before / after if after else float("inf")
        label = (
            f"REFRAMED ({tail}; original kept as {path.name}.crashed)"
            if stats["status"] == "reframed_truncated"
            else "REFRAMED"
        )
        print(
            f"{label}: {path} lines={stats['lines']} before={before} "
            f"after={after} ratio={ratio:.2f}x"
        )
        processed += 1

    overall_ratio = (total_before / total_after) if total_after else float("inf")
    print(
        f"done: {processed} file(s) reframed, {skipped} skipped (active), "
        f"{empty} empty, {truncated_untouched} truncated and left untouched, "
        f"{corrupt} corrupt and left untouched, "
        f"total_before={total_before} total_after={total_after} "
        f"overall_ratio={overall_ratio:.2f}x"
    )
    return 1 if truncated_untouched or corrupt else 0


if __name__ == "__main__":
    raise SystemExit(main())
