"""Atomic Parquet rotation, dedup/gap-detection, and orphan-tmp sweep.

Pipeline shape (locked in Plan 02, extended here): ONE shared bounded
`asyncio.Queue` of `(conn_id: str, rtime_ns: int, frame: dict)` tuples.
Producers = two `ws_client.run_connection()` tasks (`conn_id="A"` and
`conn_id="B"`, started staggered by `daemon.py`). Single consumer =
`rotation.consume()`, which does: dequeue -> `dedup_key()` +
`BoundedDedup.is_duplicate()` (drop if True) -> per-stream inter-message
gap check against `GapLedger` -> `seq.next()` -> `parse_combined_frame()`
-> per-stream row buffer -> flush. `seq` is assigned exactly once, by this
single consumer, strictly AFTER the two connections have been
merged/deduped — never inside a per-connection socket callback (locked in
`data/schema.py`'s module docstring).

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

from data.capture.dedup import BoundedDedup, dedup_key
from data.capture.gap_ledger import GapLedger
from data.capture.parse import FrameParseError, parse_combined_frame
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
    gap_threshold_seconds: float = 5.0,
) -> None:
    """Drain `queue`, dedup + gap-detect across both connections, parse
    frames into canonical rows, and flush per-stream buffers to atomic
    Parquet partitions.

    Dequeue -> `dedup_key()` + `BoundedDedup.is_duplicate()` (drop, do not
    forward to `seq.next()` or the buffer, if True) -> per-stream
    inter-message gap check (`GapLedger.record_gap()` if the silence since
    this stream's last non-duplicate message exceeds `gap_threshold_seconds`)
    -> `seq.next()` -> `parse_combined_frame()` -> per-stream row buffer ->
    flush. Flushes a stream's buffer when it reaches `flush_rows`, when
    `rotation_seconds` have elapsed since its last flush, or when
    `shutdown_event` fires (final unconditional flush of both buffers,
    draining any remaining queued items first so no in-flight row is
    silently dropped).

    `gap_threshold_seconds` default of 5.0 is chosen because bookTicker/
    trade both arrive well under 1s apart in normal operation (PROBE-
    RESULTS' ~61-118/s bookTicker, ~5-9/s trade observed rates) — 5s of
    total silence on a stream, summed across BOTH connections, is already
    anomalous.

    The `BoundedDedup` and `GapLedger` instances constructed here are
    local to this call, shared across both connections' items since both
    feed the same queue. Note for Plan 04: `GapLedger` is local to
    `consume()` for now; Plan 04 promotes it to an optional externally-
    supplied parameter (mirroring how `last_seen_rtime` is treated here)
    so `daemon.py` can share one instance between `consume()` and the new
    proactive silence watchdog task — this plan does not pre-empt that
    change.

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
    dedup = BoundedDedup()
    gap_ledger = GapLedger(data_root)
    last_seen_rtime: dict[str, int] = {}
    gap_threshold_ns = int(gap_threshold_seconds * 1_000_000_000)

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
            stream, key = dedup_key(frame)
        except FrameParseError:
            return

        if dedup.is_duplicate(stream, key, rtime_ns):
            return

        prior_rtime = last_seen_rtime.get(stream)
        if prior_rtime is not None and rtime_ns - prior_rtime > gap_threshold_ns:
            gap_seconds = (rtime_ns - prior_rtime) / 1e9
            gap_ledger.record_gap(
                stream=stream,
                conn_id="merged",
                gap_start_rtime=prior_rtime,
                gap_end_rtime=rtime_ns,
                cause=f"no message for {gap_seconds:.1f}s",
            )
        last_seen_rtime[stream] = rtime_ns

        try:
            row = parse_combined_frame(frame, assigner.next(symbol, stream), rtime_ns)
        except FrameParseError:
            return
        buffers[stream].append(row)

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
