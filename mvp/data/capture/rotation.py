"""Atomic Parquet rotation and orphan-tmp sweep.

Pipeline shape (locked here for Plan 03 to extend without restructuring):
ONE shared bounded `asyncio.Queue` of `(conn_id: str, rtime_ns: int, frame: dict)`
tuples. Producer(s) = `ws_client.run_connection()`. Single consumer =
`rotation.consume()`, which does: dequeue -> *(Plan 03 inserts a bounded dedup
stage exactly here)* -> `seq.next()` -> `parse_combined_frame()` -> per-stream
row buffer -> flush. Plan 03 adds a second producer task feeding the SAME
queue and one dedup stage inside `consume()`; nothing else in this shape moves.

Never use the unstable multi-file writer — polars documents it as unstable.
This module always builds an in-memory `pl.DataFrame`, splits it with the
stable `df.partition_by(...)` API, and writes each part via a `.tmp`-suffixed
sibling path followed by an atomic `Path.replace()`.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from data.capture.parse import FrameParseError, parse_combined_frame, stream_kind_of
from data.schema import BOOKTICKER_SCHEMA, TRADE_SCHEMA

if TYPE_CHECKING:
    from data.capture.seq import SeqAssigner

ORPHAN_TMP_MIN_AGE_SECONDS = 10.0
QUEUE_POLL_TIMEOUT_SECONDS = 1.0


def partition_dir(data_root: Path, symbol: str, stream: str) -> Path:
    """Single source of truth for the on-disk partition path shape.

    Imported by both `write_partition_atomic` and `seq.py`'s resume logic —
    no second inline copy of this path pattern anywhere.
    """
    return data_root / "parsed" / f"symbol={symbol}" / f"stream={stream}"


def write_partition_atomic(
    rows: list[dict], schema: dict, data_root: Path, symbol: str, stream: str
) -> list[Path]:
    """Write `rows` as one or more atomically-renamed Parquet files, one per
    distinct UTC calendar date present in `rows`'s `etime` column.

    Returns the list of final paths written. Returns `[]` for an empty `rows`.
    """
    if not rows:
        return []

    df = pl.DataFrame(rows, schema=schema)
    df = df.with_columns(
        pl.col("etime").cast(pl.Datetime("ns")).dt.strftime("%Y-%m-%d").alias("date")
    )
    parts = df.partition_by("date", include_key=False, as_dict=True)

    written: list[Path] = []
    base_dir = partition_dir(data_root, symbol, stream)
    for date_key, sub_df in parts.items():
        date_value = date_key[0]
        part_dir = base_dir / f"date={date_value}"
        part_dir.mkdir(parents=True, exist_ok=True)
        final_path = part_dir / f"part-{time.time_ns()}.parquet"
        tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
        sub_df.write_parquet(tmp_path, compression="zstd")
        tmp_path.replace(final_path)
        written.append(final_path)

    return written


def sweep_orphan_tmp_files(data_root: Path) -> list[Path]:
    """Delete stray `*.parquet.tmp` files left over from a crash.

    Skips any `.tmp` file whose `st_mtime` is younger than
    `ORPHAN_TMP_MIN_AGE_SECONDS` (guards against the vanishingly unlikely
    race of sweeping a `.tmp` file an in-flight write is still populating,
    even though atomic writes make this theoretically unreachable at
    startup). Call this once at daemon startup, before any connection
    opens — a `.tmp` file left over from a crash was never referenced by a
    `.replace()` and is safe to discard.
    """
    parsed_dir = data_root / "parsed"
    if not parsed_dir.exists():
        return []

    deleted: list[Path] = []
    now = time.time()
    for tmp_file in parsed_dir.glob("**/*.parquet.tmp"):
        age = now - tmp_file.stat().st_mtime
        if age < ORPHAN_TMP_MIN_AGE_SECONDS:
            continue
        tmp_file.unlink()
        deleted.append(tmp_file)

    return deleted


async def consume(
    queue: asyncio.Queue,
    assigner: "SeqAssigner",
    data_root: Path,
    symbol: str,
    flush_rows: int,
    rotation_seconds: float,
    shutdown_event: asyncio.Event,
) -> None:
    """Drain `queue`, parse frames into canonical rows, and flush per-stream
    buffers to atomic Parquet partitions.

    Dequeue -> *(Plan 03 inserts a bounded dedup stage exactly here)* ->
    `seq.next()` -> `parse_combined_frame()` -> per-stream row buffer ->
    flush. Flushes a stream's buffer when it reaches `flush_rows`, when
    `rotation_seconds` have elapsed since its last flush, or when
    `shutdown_event` fires (final unconditional flush of both buffers,
    draining any remaining queued items first so no in-flight row is
    silently dropped).

    Polls the queue with a bounded timeout rather than blocking forever on
    `await queue.get()`, so a quiet stream still rotates on
    `rotation_seconds` and SIGTERM/SIGINT is still noticed promptly.
    """
    schemas = {"bookTicker": BOOKTICKER_SCHEMA, "trade": TRADE_SCHEMA}
    buffers: dict[str, list[dict]] = {"bookTicker": [], "trade": []}
    last_flush: dict[str, float] = {
        "bookTicker": time.monotonic(),
        "trade": time.monotonic(),
    }

    def flush_stream(stream: str) -> None:
        rows = buffers[stream]
        if not rows:
            last_flush[stream] = time.monotonic()
            return
        write_partition_atomic(rows, schemas[stream], data_root, symbol, stream)
        buffers[stream] = []
        last_flush[stream] = time.monotonic()

    def ingest(conn_id: str, rtime_ns: int, frame: dict) -> None:
        try:
            kind = stream_kind_of(frame)
            row = parse_combined_frame(frame, assigner.next(symbol, kind), rtime_ns)
        except FrameParseError:
            return
        buffers[kind].append(row)

    while True:
        try:
            conn_id, rtime_ns, frame = await asyncio.wait_for(
                queue.get(), timeout=QUEUE_POLL_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            pass
        else:
            ingest(conn_id, rtime_ns, frame)
            for stream in ("bookTicker", "trade"):
                if len(buffers[stream]) >= flush_rows:
                    flush_stream(stream)

        if shutdown_event.is_set():
            while True:
                try:
                    conn_id, rtime_ns, frame = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                ingest(conn_id, rtime_ns, frame)
            flush_stream("bookTicker")
            flush_stream("trade")
            return

        now = time.monotonic()
        for stream in ("bookTicker", "trade"):
            if now - last_flush[stream] >= rotation_seconds:
                flush_stream(stream)
