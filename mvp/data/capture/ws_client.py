"""Connection producer: connect, archive verbatim, startup liveness, enqueue.

Verbatim-first invariant (01-CONTEXT.md): every raw wire message is archived
to zstd-compressed NDJSON BEFORE any parsing happens, so the wire bytes
remain the source of truth and any parse decision is replayable.

Startup liveness invariant (PROBE-RESULTS.md finding 3): a wrong-class
subscription is accepted by the server and then silently delivers nothing,
forever, with no error. `run_connection` asserts every stream in
`expected_streams` delivers at least one frame within `startup_timeout`
seconds of connecting — including the case where ZERO frames ever arrive.
The deadline is on the wait itself (`asyncio.wait_for`), not on a loop
iteration that presupposes a prior message.
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


class RawArchiveWriter:
    """Appends verbatim wire messages to a zstd-compressed NDJSON archive.

    Re-evaluates the UTC date on every `append()` call and transparently
    rotates to a new `date=...` file the moment the date changes, since a
    multi-day-running connection must not keep writing into day 1's file
    forever.
    """

    def __init__(self, archive_dir: Path, conn_id: str) -> None:
        self._archive_dir = archive_dir
        self._conn_id = conn_id
        self._current_date: str | None = None
        self._fh = None
        self._open_for_today()

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def _open_for_today(self) -> None:
        date_str = self._today()
        if date_str == self._current_date and self._fh is not None:
            return
        if self._fh is not None:
            self._fh.close()
        day_dir = self._archive_dir / f"date={date_str}"
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"conn_{self._conn_id}.ndjson.zst"
        self._fh = open(path, "ab")
        self._current_date = date_str

    def append(self, raw: bytes | str, rtime_ns: int) -> None:
        self._open_for_today()
        raw_str = raw if isinstance(raw, str) else raw.decode()
        line = orjson.dumps(
            {"rtime_ns": rtime_ns, "conn_id": self._conn_id, "raw": raw_str}
        ) + b"\n"
        cctx = zstandard.ZstdCompressor()
        with cctx.stream_writer(self._fh, closefd=False) as writer:
            writer.write(line)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


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
    silent or late subscription, or by task cancellation.
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
            pending = set(expected_streams)
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
