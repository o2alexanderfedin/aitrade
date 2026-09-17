"""Connection producer: connect, archive verbatim, startup liveness, enqueue.

Verbatim-first invariant (01-CONTEXT.md): every raw wire message is archived
to zstd-compressed NDJSON BEFORE any parsing happens, so the wire bytes
remain the source of truth and any parse decision is replayable.

Startup liveness invariant (PROBE-RESULTS.md finding 3): a wrong-class
subscription is accepted by the server and then silently delivers nothing,
forever, with no error. `run_connection` asserts every stream in
`expected_streams` delivers at least one frame within `startup_timeout`
seconds of the FIRST connection — including the case where ZERO frames ever
arrive. The deadline is on the wait itself (`asyncio.wait_for`), not on a
loop iteration that presupposes a prior message.

The liveness assertion is deliberately NOT re-armed on a reconnect
(`attempt >= 2`): it exists to catch a wrong-class subscription, which is a
configuration bug that can only manifest on the very first connection — the
URL is already proven live once the first connection has passed liveness.
A slow frame after a reconnect is ordinary market-lull jitter (measured
p999 inter-arrival 3.2s, max 5.67s in evidence/PROBE-RESULTS.md), which a
fixed startup deadline would eventually and spuriously trip on some
reconnect over a multi-day run. That case is the watchdog's job
(`connection-silent`, a logged ledger row, not a fatal exception).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import orjson
import websockets
import zstandard

from data.capture.parse import FrameParseError, decode_frame, stream_kind_of
from data.capture.streams import assert_secure_url


class StartupLivenessError(RuntimeError):
    """Raised when one or more expected streams deliver no frame within the
    startup timeout — the loud failure mode for a wrong-class or silent-
    no-data subscription (see PROBE-RESULTS.md finding 3)."""


#: 03-06-PLAN.md fix defaults: how often the long-lived per-file zstd
#: compressor closes a decodable FRAME boundary (as opposed to the
#: per-message FLUSH_BLOCK, which is durable but not a frame boundary every
#: reader can rely on). Small and safe -- bounds how much of the tail a
#: crash between two FLUSH_FRAME calls can leave undecoded to at most a few
#: hundred messages / a few seconds, while still getting the ~10x
#: shared-context compression ratio a fresh-compressor-per-message defeated
#: (measured 1.5x, PROBE-RESULTS.md section 4).
DEFAULT_FLUSH_FRAME_EVERY_MESSAGES = 500
DEFAULT_FLUSH_FRAME_EVERY_SECONDS = 5.0


def archive_segment_paths(day_dir: Path, conn_id: str) -> list[Path]:
    """Every raw-archive file for `conn_id` in one `date=...` directory, in
    write order: the legacy single-file name `conn_<id>.ndjson.zst` first
    (written by pre-CR-01 runs, which appended across restarts), then the
    per-open segments `conn_<id>.<open_ns>.ndjson.zst` in `open_ns` order.

    Each file is an independent zstd stream: decode them one after another,
    never by concatenating bytes (a crashed segment may end mid-frame)."""
    day_dir = Path(day_dir)
    legacy = day_dir / f"conn_{conn_id}.ndjson.zst"
    segments = sorted(
        p for p in day_dir.glob(f"conn_{conn_id}.*.ndjson.zst") if p != legacy
    )
    return ([legacy] if legacy.exists() else []) + segments


class RawArchiveWriter:
    """Appends verbatim wire messages to a zstd-compressed NDJSON archive.

    Re-evaluates the UTC date on every `append()` call and transparently
    rotates to a new `date=...` file the moment the date changes, since a
    multi-day-running connection must not keep writing into day 1's file
    forever.

    ONE long-lived `ZstdCompressor().stream_writer` per open file (not one
    per message, per 03-06-PLAN.md -- the fresh-compressor-per-message
    defect measured ~1.5x instead of the ~10x zstd gives NDJSON with a
    shared compression context, PROBE-RESULTS.md section 4): every
    `append()` writes the line, then `flush(FLUSH_BLOCK)` (durable -- the
    file is decodable up to this point even if the process dies before the
    next flush; NOT a frame boundary every reader can rely on). Every
    `flush_frame_every_messages` messages OR `flush_frame_every_seconds`
    seconds, whichever comes first, `flush(FLUSH_FRAME)` closes a real
    frame boundary, bounding how much of the tail a crash can leave
    undecoded by *some* readers to at most one partial frame. Rotation
    (date change) and `close()` both force a final `FLUSH_FRAME` before the
    underlying file handle closes.

    ONE FILE PER PROCESS RUN, NEVER APPEND (03-REVIEW.md CR-01): between two
    `FLUSH_FRAME`s the file ends in an UNTERMINATED frame. If the process
    dies without `close()` (SIGKILL, OOM, battery at 0 %) and a restart on
    the same UTC day reopened that file in append mode, the new frame's
    magic number would land where the decoder expects the next block header
    of the unfinished frame, and every standard reader (python-zstandard,
    `zstd -d`, `tools/reframe_raw_archive.py`) raises `Data corruption
    detected` from that byte on -- the rest of the day becomes unreadable.
    So every open creates a NEW segment, `conn_<id>.<open_ns>.ndjson.zst`,
    with exclusive create (`"xb"`): a crashed run's truncated tail stays
    exactly as recoverable as a truncated file on its own, and a later run
    can never be glued onto it. The legacy pre-fix name
    `conn_<id>.ndjson.zst` is never opened for writing again. Readers
    enumerate a day's files with `archive_segment_paths`.
    """

    def __init__(
        self,
        archive_dir: Path,
        conn_id: str,
        flush_frame_every_messages: int = DEFAULT_FLUSH_FRAME_EVERY_MESSAGES,
        flush_frame_every_seconds: float = DEFAULT_FLUSH_FRAME_EVERY_SECONDS,
    ) -> None:
        self._archive_dir = archive_dir
        self._conn_id = conn_id
        self._flush_frame_every_messages = flush_frame_every_messages
        self._flush_frame_every_seconds = flush_frame_every_seconds
        self._current_date: str | None = None
        self._fh = None
        self._writer = None
        self.current_path: Path | None = None
        self._messages_since_frame_flush = 0
        self._last_frame_flush_monotonic = 0.0
        self._open_for_today()

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def _open_for_today(self) -> None:
        date_str = self._today()
        if date_str == self._current_date and self._fh is not None:
            return
        self._close_writer()
        day_dir = self._archive_dir / f"date={date_str}"
        day_dir.mkdir(parents=True, exist_ok=True)
        # Exclusive create of a fresh per-open segment -- never "ab" (CR-01,
        # see class docstring). `time.time_ns()` is 19 digits until 2286, so
        # lexicographic order of the names is write order.
        path = day_dir / f"conn_{self._conn_id}.{time.time_ns()}.ndjson.zst"
        self._fh = open(path, "xb")
        self.current_path = path
        cctx = zstandard.ZstdCompressor()
        self._writer = cctx.stream_writer(self._fh, closefd=False)
        self._current_date = date_str
        self._messages_since_frame_flush = 0
        self._last_frame_flush_monotonic = time.monotonic()

    def _close_writer(self) -> None:
        if self._writer is not None:
            # Final FLUSH_FRAME before the file handle closes -- rotation
            # (date change) and shutdown must never leave the last frame
            # unclosed.
            self._writer.flush(zstandard.FLUSH_FRAME)
            self._writer.close()
            self._writer = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def append(self, raw: bytes | str, rtime_ns: int) -> None:
        self._open_for_today()
        raw_str = raw if isinstance(raw, str) else raw.decode()
        line = (
            orjson.dumps(
                {"rtime_ns": rtime_ns, "conn_id": self._conn_id, "raw": raw_str}
            )
            + b"\n"
        )
        self._writer.write(line)
        self._writer.flush(zstandard.FLUSH_BLOCK)
        self._messages_since_frame_flush += 1
        now = time.monotonic()
        due_by_count = (
            self._messages_since_frame_flush >= self._flush_frame_every_messages
        )
        due_by_time = (
            now - self._last_frame_flush_monotonic >= self._flush_frame_every_seconds
        )
        if due_by_count or due_by_time:
            self._writer.flush(zstandard.FLUSH_FRAME)
            self._messages_since_frame_flush = 0
            self._last_frame_flush_monotonic = now

    def close(self) -> None:
        self._close_writer()


async def run_connection(
    url: str,
    queue: asyncio.Queue,
    conn_id: str,
    archive_dir: Path,
    expected_streams: set[str],
    startup_timeout: float = 10.0,
) -> None:
    """Connect to `url`, archive every frame verbatim, assert startup
    liveness for `expected_streams`, and enqueue `(conn_id, rtime_ns, frame)`
    tuples onto `queue`.

    Never returns on success (auto-reconnects forever via the websockets
    async-iterator pattern) except by raising `StartupLivenessError` on a
    silent or late subscription on the FIRST connection attempt, or by
    task cancellation.
    """
    assert_secure_url(url)
    archive_writer = RawArchiveWriter(archive_dir, conn_id)
    attempt = 0
    try:
        async for ws in websockets.connect(url, ping_interval=20, ping_timeout=20):
            attempt += 1
            established_at = time.monotonic()
            # Observability contract (test_ws_client_reconnect_logging.py):
            # every established connection and every close-then-reconnect is
            # one stdout line naming conn_id. Without this, a clean reconnect
            # under the gap threshold is indistinguishable from "never
            # disconnected" — which makes Binance's ~24h forced close, and
            # the deferred >=24h reconnect check, unverifiable.
            print(f"connection {conn_id}: established attempt={attempt}", flush=True)
            # Only the FIRST connection arms the startup-liveness deadline
            # (see module docstring) — a reconnect (attempt >= 2) leaves
            # `pending` empty so the branch below goes straight to a plain
            # `ws.recv()`, never re-arming `asyncio.wait_for`.
            pending = set(expected_streams) if attempt == 1 else set()
            deadline = time.monotonic() + startup_timeout
            try:
                while True:
                    if pending:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise StartupLivenessError(
                                "no frame arrived for stream(s) "
                                f"{sorted(pending)!r} within {startup_timeout}s "
                                "of connecting (silent wrong-class subscription?)"
                            )
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                        except (asyncio.TimeoutError, TimeoutError) as exc:
                            raise StartupLivenessError(
                                "no frame arrived for stream(s) "
                                f"{sorted(pending)!r} within {startup_timeout}s "
                                "of connecting (silent wrong-class subscription?)"
                            ) from exc
                    else:
                        raw = await ws.recv()

                    rtime_ns = time.time_ns()
                    archive_writer.append(raw, rtime_ns)
                    try:
                        frame = decode_frame(raw)
                        kind = stream_kind_of(frame)
                    except FrameParseError:
                        continue
                    pending.discard(kind)
                    await queue.put((conn_id, rtime_ns, frame))
            except websockets.exceptions.ConnectionClosed as exc:
                uptime = time.monotonic() - established_at
                # `rcvd` is the peer's close frame; it is None when the TCP
                # connection dropped without one (network fault, RST). A
                # Binance ~24h forced close arrives as a proper frame
                # (typically 1001), so the two cases stay distinguishable.
                code = exc.rcvd.code if exc.rcvd is not None else None
                reason = exc.rcvd.reason if exc.rcvd is not None else ""
                print(
                    f"connection {conn_id}: closed code={code} "
                    f"reason={reason!r} after {uptime:.1f}s — reconnecting",
                    flush=True,
                )
                continue
    finally:
        archive_writer.close()
