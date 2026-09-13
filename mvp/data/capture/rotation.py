"""Atomic Parquet rotation, dedup/gap-detection, seq-resume sidecar, and
orphan-tmp sweep.

Pipeline shape (locked in Plan 02, extended in Plan 03/04): ONE shared
bounded `asyncio.Queue` of `(conn_id: str, rtime_ns: int, frame: dict)`
tuples. Producers = two `ws_client.run_connection()` tasks (`conn_id="A"`
and `conn_id="B"`, started staggered by `daemon.py`). Single consumer =
`rotation.consume()`, which does: dequeue -> connection-liveness
bookkeeping + reactive `connection-silent`/`merged-silent` gap detection
(keyed by `conn_id`, Plan 04 policy correction -- see `consume()`'s
docstring) -> `dedup_key()` + `BoundedDedup.is_duplicate()` (drop if True)
-> `trade-id-skip` contiguity check -> `seq.next()` ->
`parse_combined_frame()` -> per-stream row buffer -> flush (which also
persists the `seq_state.json` sidecar via `write_seq_state_atomic()`).
`seq` is assigned exactly once, by this single consumer, strictly AFTER the
two connections have been merged/deduped — never inside a per-connection
socket callback (locked in `data/schema.py`'s module docstring).

Never use the unstable multi-file writer — polars documents it as unstable.
This module always builds an in-memory `pl.DataFrame`, splits it with the
stable `df.partition_by(...)` API, and writes each part via a `.tmp`-suffixed
sibling path followed by an atomic `Path.replace()`.

Plan 04 also adds `seq_state.json` (`read_seq_sidecar`/
`write_seq_state_atomic`/`part_ns_of`, all defined here since `seq.py`
already imports `partition_dir` from this module at runtime -- adding the
reverse import would create a cycle): a tiny per-`(symbol, stream)` sidecar
of `{"seq": <last_written_seq>, "part_ns": <its part file's ns>}`, written
atomically after every successful flush, so `seq.py`'s
`resume_seq_assigner()` can seed in O(1) instead of globbing every
partition ever written (found live in Plan 02's checkpoint: 1,261 files
cost ~14s of daemon downtime; ~2,600 partitions/day would reach minutes
within weeks).
"""

from __future__ import annotations

import asyncio
import json
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


SEQ_STATE_FILENAME = "seq_state.json"


def seq_state_path(data_root: Path) -> Path:
    """Single source of truth for the seq-resume sidecar's on-disk path."""
    return Path(data_root) / SEQ_STATE_FILENAME


def part_ns_of(path: Path) -> int:
    """Extract the `<ns>` integer from a `part-<ns>.parquet` filename.

    Shared by `write_partition_atomic`'s caller (`consume()`, to record the
    just-written file's identity in the sidecar) and `seq.py`'s
    `resume_seq_assigner` (to decide which files on disk are newer than
    what the sidecar already knows about).
    """
    return int(path.stem.split("-", 1)[1])


def read_seq_sidecar(data_root: Path, symbol: str) -> dict[str, dict[str, int]]:
    """Read `<data_root>/seq_state.json`'s entry for `symbol`.

    Returns `{stream: {"seq": <last_written_seq>, "part_ns": <its filename's
    ns>}}` for whatever streams are present. Returns `{}` if the file is
    absent, unreadable, or does not (yet) mention `symbol` -- never raises,
    since a missing/corrupt sidecar simply means `seq.py`'s newest-date
    fallback scan does the work instead (found during Plan 02's live
    checkpoint: the pre-sidecar full-history glob took ~14s at 1,261 files).
    """
    path = seq_state_path(data_root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}

    symbol_state = raw.get(symbol) if isinstance(raw, dict) else None
    if not isinstance(symbol_state, dict):
        return {}

    result: dict[str, dict[str, int]] = {}
    for stream, entry in symbol_state.items():
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("seq"), int)
            and isinstance(entry.get("part_ns"), int)
        ):
            result[stream] = {"seq": entry["seq"], "part_ns": entry["part_ns"]}
    return result


def write_seq_state_atomic(
    data_root: Path, symbol: str, updates: dict[str, dict[str, int]]
) -> None:
    """Atomically merge `updates` (`{stream: {"seq": int, "part_ns": int}}`)
    into `<data_root>/seq_state.json`'s entry for `symbol`.

    Read-modify-write against whatever is currently on disk, so flushing
    only one stream (e.g. bookTicker, because trade's buffer happened to be
    empty this cycle) never clobbers the other stream's or another symbol's
    already-recorded entry. Called by `consume()` immediately after every
    successful Parquet flush, and by `seq.py`'s `resume_seq_assigner` to
    self-heal a stale or missing sidecar the moment it detects one (so the
    *next* restart is sidecar-only again).
    """
    path = seq_state_path(data_root)
    state: dict = {}
    if path.exists():
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            state = {}
    if not isinstance(state, dict):
        state = {}

    symbol_state = state.get(symbol)
    if not isinstance(symbol_state, dict):
        symbol_state = {}
    symbol_state.update(updates)
    state[symbol] = symbol_state

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state))
    tmp_path.replace(path)


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
    last_seen_state: dict[str, int] | None = None,
    gap_ledger: GapLedger | None = None,
) -> None:
    """Drain `queue`, dedup + gap-detect across both connections, parse
    frames into canonical rows, and flush per-stream buffers to atomic
    Parquet partitions.

    Dequeue -> connection-liveness bookkeeping + reactive gap detection
    (see below) -> `dedup_key()` + `BoundedDedup.is_duplicate()` (drop, do
    not forward to `seq.next()` or the buffer, if True) -> trade-id
    contiguity check -> `seq.next()` -> `parse_combined_frame()` ->
    per-stream row buffer -> flush (which also persists the `seq_state.json`
    sidecar so a restart never has to glob every partition ever written).
    Flushes a stream's buffer when it reaches `flush_rows`, when
    `rotation_seconds` have elapsed since its last flush, or when
    `shutdown_event` fires (final unconditional flush of both buffers,
    draining any remaining queued items first so no in-flight row is
    silently dropped).

    Gap-ledger policy (Plan 04 correction, replacing Plan 03's per-stream
    rule): Plan 03's live checkpoint found a 5s per-stream silence rule logs
    ~170 false "outages"/day on `trade` alone (p999 inter-arrival 3.2s, max
    5.67s) while `bookTicker` flows uninterrupted -- a market lull, not a
    capture outage. An outage is a property of the CONNECTION, not of a
    bursty stream, so this restructures detection into three signals, each
    with a distinct `cause` prefix:

    1. `connection-silent` -- no frame of ANY stream from a given `conn_id`
       for > `gap_threshold_seconds` (keyed by `conn_id`, not `stream`,
       because bookTicker at ~118/s is that connection's own heartbeat).
    2. `merged-silent` -- no frame of ANY stream from EITHER connection for
       > threshold (both sockets dead -- the case redundancy exists to
       survive). Takes priority over `connection-silent` when both are true
       simultaneously, since it is the more informative signal.
    3. `trade-id-skip` -- consecutive MERGED (post-dedup) `trade` rows whose
       `trade_id` differ by more than 1. The exchange's own sequence proves
       loss regardless of timing. `bookTicker`'s `u` is deliberately NOT
       checked for contiguity -- it is the order-book update id and
       legitimately skips between BBO changes.

    `last_seen_state` (keys: each `conn_id` seen so far, plus `"merged"`) is
    updated on every raw frame BEFORE dedup is even consulted -- a
    connection that is consistently a few ms slower than its twin would
    otherwise see ~100% of its frames dropped as duplicates and look
    permanently silent to a check that only updated liveness on non-dup
    frames. If `None`, a fresh dict is used (Plan 03's callers, and any
    test that does not care about sharing, keep working unchanged); if
    provided, `daemon.py` shares the SAME dict with `watchdog.py`'s
    proactive per-connection stall check, which catches the case this
    reactive check structurally cannot: a connection that goes silent and
    NEVER sends another frame to trigger this check at all.

    `gap_ledger`, similarly, defaults to a fresh `GapLedger(data_root)` if
    `None`, or uses the caller-supplied instance so `daemon.py` can hand the
    same instance to `watchdog.py` -- closing the gap Plan 03 left (its
    `GapLedger` was `consume()`-local with no way for `daemon.py` to obtain
    it). `consume()` and `Watchdog.run()` share one asyncio event loop and
    `record_gap()` is synchronous, so sharing one instance is a pure
    shape-of-wiring change, not a concurrent-writer hazard.

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
    if gap_ledger is None:
        gap_ledger = GapLedger(data_root)
    if last_seen_state is None:
        last_seen_state = {}
    last_trade: dict[str, int | None] = {"id": None, "rtime": None}
    gap_threshold_ns = int(gap_threshold_seconds * 1_000_000_000)

    def flush_stream(stream: str) -> None:
        rows = buffers[stream]
        if not rows:
            last_flush[stream] = time.monotonic()
            return
        written = write_partition_atomic(
            rows, schemas[stream], data_root, symbol, stream
        )
        buffers[stream] = []
        last_flush[stream] = time.monotonic()
        if written:
            last_part_ns = max(part_ns_of(p) for p in written)
            last_seq = assigner.peek(symbol, stream) - 1
            write_seq_state_atomic(
                data_root, symbol, {stream: {"seq": last_seq, "part_ns": last_part_ns}}
            )

    def ingest(conn_id: str, rtime_ns: int, frame: dict) -> None:
        # Connection-liveness bookkeeping happens BEFORE dedup, on every raw
        # frame: a connection that is consistently the "loser" of the dedup
        # race would otherwise see 100% of its frames dropped as duplicates
        # and look permanently silent.
        prior_merged = last_seen_state.get("merged")
        prior_conn = last_seen_state.get(conn_id)
        if prior_merged is not None and rtime_ns - prior_merged > gap_threshold_ns:
            gap_seconds = (rtime_ns - prior_merged) / 1e9
            gap_ledger.record_gap(
                stream="__connection__",
                conn_id="merged",
                gap_start_rtime=prior_merged,
                gap_end_rtime=rtime_ns,
                cause=(
                    f"merged-silent: no message from either connection for "
                    f"{gap_seconds:.1f}s"
                ),
            )
        elif prior_conn is not None and rtime_ns - prior_conn > gap_threshold_ns:
            gap_seconds = (rtime_ns - prior_conn) / 1e9
            gap_ledger.record_gap(
                stream="__connection__",
                conn_id=conn_id,
                gap_start_rtime=prior_conn,
                gap_end_rtime=rtime_ns,
                cause=(
                    f"connection-silent: no message from conn_id={conn_id} "
                    f"for {gap_seconds:.1f}s"
                ),
            )
        last_seen_state[conn_id] = rtime_ns
        last_seen_state["merged"] = rtime_ns

        try:
            stream, key = dedup_key(frame)
        except FrameParseError:
            return

        if dedup.is_duplicate(stream, key, rtime_ns):
            return

        if stream == "trade":
            try:
                trade_id = frame["data"]["t"]
            except KeyError:
                trade_id = None
            if trade_id is not None:
                prior_id = last_trade["id"]
                if prior_id is not None:
                    diff = trade_id - prior_id
                    if diff > 1:
                        missing = diff - 1
                        gap_ledger.record_gap(
                            stream="trade",
                            conn_id="merged",
                            gap_start_rtime=last_trade["rtime"],
                            gap_end_rtime=rtime_ns,
                            cause=(
                                f"trade-id-skip: missing {missing} id"
                                f"{'s' if missing != 1 else ''} "
                                f"({prior_id + 1}..{trade_id - 1})"
                            ),
                        )
                    # A negative/zero diff is a legitimately-late delivery
                    # from the redundant connection arriving after a
                    # numerically-higher id was already processed -- not a
                    # skip, and must not retroactively rewind the watermark.
                    if diff > 0:
                        last_trade["id"] = trade_id
                        last_trade["rtime"] = rtime_ns
                else:
                    last_trade["id"] = trade_id
                    last_trade["rtime"] = rtime_ns

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
