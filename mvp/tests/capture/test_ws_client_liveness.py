"""Tests for ws_client.py's startup liveness assertion and verbatim archive.

No `pytest-asyncio`/`anyio` plugin is installed — every test drives its own
event loop via `asyncio.run(...)`, matching `tests/conftest.py`'s
`scripted_server` fixture (which returns the async-context-manager factory,
not an already-entered context).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import orjson
import pytest
import zstandard

from data.capture.ws_client import StartupLivenessError, run_connection
from tests.fixtures.fake_ws_server import DEFAULT_SCRIPT, ScriptedFrame
from tests.fixtures.payloads import SAMPLE_BOOKTICKER_FRAME, SAMPLE_TRADE_FRAME


def _bookticker(u: int) -> dict:
    env = {**SAMPLE_BOOKTICKER_FRAME, "data": {**SAMPLE_BOOKTICKER_FRAME["data"]}}
    env["data"]["u"] = u
    return env


def _trade(t: int) -> dict:
    env = {**SAMPLE_TRADE_FRAME, "data": {**SAMPLE_TRADE_FRAME["data"]}}
    env["data"]["t"] = t
    return env


def _read_archive_lines(archive_dir: Path, conn_id: str) -> list[dict]:
    today = time.strftime("%Y-%m-%d", time.gmtime())
    path = archive_dir / f"date={today}" / f"conn_{conn_id}.ndjson.zst"
    dctx = zstandard.ZstdDecompressor()
    with open(path, "rb") as fh:
        raw = dctx.stream_reader(fh, read_across_frames=True).read()
    lines = [line for line in raw.split(b"\n") if line]
    return [orjson.loads(line) for line in lines]


def test_both_streams_arrive_completes_startup_and_enqueues(
    tmp_path: Path, scripted_server
) -> None:
    async def scenario() -> None:
        async with scripted_server(frames=DEFAULT_SCRIPT) as port:
            queue: asyncio.Queue = asyncio.Queue()
            task = asyncio.create_task(
                run_connection(
                    f"ws://127.0.0.1:{port}",
                    queue,
                    conn_id="A",
                    archive_dir=tmp_path,
                    expected_streams={"bookTicker", "trade"},
                    startup_timeout=2.0,
                )
            )
            items = []
            for _ in range(2):
                items.append(await asyncio.wait_for(queue.get(), timeout=2.0))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            assert len(items) == 2
            for conn_id, rtime_ns, frame in items:
                assert conn_id == "A"
                assert isinstance(rtime_ns, int)
                assert frame["stream"] in ("btcusdt@bookTicker", "btcusdt@trade")

    asyncio.run(scenario())


def test_late_stream_raises_startup_liveness_error_naming_it(
    tmp_path: Path, scripted_server
) -> None:
    frames = [ScriptedFrame(envelope=SAMPLE_BOOKTICKER_FRAME, delay_seconds=0.0)]

    async def scenario() -> None:
        async with scripted_server(frames=frames, withhold={"trade"}) as port:
            queue: asyncio.Queue = asyncio.Queue()
            with pytest.raises(StartupLivenessError) as exc_info:
                await run_connection(
                    f"ws://127.0.0.1:{port}",
                    queue,
                    conn_id="A",
                    archive_dir=tmp_path,
                    expected_streams={"bookTicker", "trade"},
                    startup_timeout=0.5,
                )
            assert "trade" in str(exc_info.value)

    asyncio.run(scenario())


def test_total_silence_raises_startup_liveness_error_within_timeout(
    tmp_path: Path, scripted_server
) -> None:
    async def scenario() -> None:
        async with scripted_server(
            frames=DEFAULT_SCRIPT, withhold={"bookTicker", "trade"}
        ) as port:
            queue: asyncio.Queue = asyncio.Queue()
            start = time.monotonic()
            with pytest.raises(StartupLivenessError):
                await run_connection(
                    f"ws://127.0.0.1:{port}",
                    queue,
                    conn_id="A",
                    archive_dir=tmp_path,
                    expected_streams={"bookTicker", "trade"},
                    startup_timeout=0.5,
                )
            elapsed = time.monotonic() - start
            assert elapsed < 2.0

    asyncio.run(scenario())


def test_liveness_deadline_is_not_rearmed_on_reconnect(
    tmp_path: Path, scripted_server
) -> None:
    """CR-01 (01-REVIEW.md): the startup-liveness deadline exists to catch a
    wrong-class subscription, which can only be a first-connection problem —
    it must NOT be re-armed on a reconnect. First connection delivers both
    streams normally (proving the URL is good) and then drops; the second
    connection withholds `trade` entirely and spreads its `bookTicker`
    frames past `startup_timeout` — under the old (buggy) re-arming
    behavior this raises `StartupLivenessError`; under the fixed behavior
    `run_connection` must keep receiving `bookTicker` frames and never
    raise."""
    startup_timeout = 0.5
    script_1 = [
        ScriptedFrame(envelope=_bookticker(1)),
        ScriptedFrame(envelope=_trade(1)),
    ]
    # No trade frames at all in this script, and the cumulative delay
    # (4 * 0.2s = 0.8s) exceeds startup_timeout -- the old code would
    # raise StartupLivenessError naming "trade" partway through this.
    script_2 = [
        ScriptedFrame(envelope=_bookticker(2), delay_seconds=0.2),
        ScriptedFrame(envelope=_bookticker(3), delay_seconds=0.2),
        ScriptedFrame(envelope=_bookticker(4), delay_seconds=0.2),
        ScriptedFrame(envelope=_bookticker(5), delay_seconds=0.2),
    ]

    async def scenario() -> None:
        async with scripted_server(connection_scripts=[script_1, script_2]) as port:
            queue: asyncio.Queue = asyncio.Queue()
            task = asyncio.create_task(
                run_connection(
                    f"ws://127.0.0.1:{port}",
                    queue,
                    conn_id="A",
                    archive_dir=tmp_path,
                    expected_streams={"bookTicker", "trade"},
                    startup_timeout=startup_timeout,
                )
            )
            # Poll bounded well past script_2's cumulative delay so a hang
            # or a spurious raise both fail fast rather than hanging the
            # suite.
            for _ in range(100):
                if queue.qsize() >= 6:  # 2 from script_1 + 4 from script_2
                    break
                if task.done():
                    break
                await asyncio.sleep(0.05)

            # The discriminating assertion: run_connection must still be
            # running (it must NOT have raised StartupLivenessError on the
            # reconnect's withheld "trade" stream).
            assert not task.done(), (
                f"run_connection finished unexpectedly: "
                f"{task.exception() if task.done() else None}"
            )
            assert queue.qsize() >= 6

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_raw_archive_contains_verbatim_round_trippable_frames(
    tmp_path: Path, scripted_server
) -> None:
    async def scenario() -> None:
        async with scripted_server(frames=DEFAULT_SCRIPT) as port:
            queue: asyncio.Queue = asyncio.Queue()
            task = asyncio.create_task(
                run_connection(
                    f"ws://127.0.0.1:{port}",
                    queue,
                    conn_id="A",
                    archive_dir=tmp_path,
                    expected_streams={"bookTicker", "trade"},
                    startup_timeout=2.0,
                )
            )
            for _ in range(2):
                await asyncio.wait_for(queue.get(), timeout=2.0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            lines = _read_archive_lines(tmp_path, "A")
            assert len(lines) >= 2
            for entry in lines:
                assert set(entry.keys()) >= {"rtime_ns", "conn_id", "raw"}
                round_tripped = orjson.loads(entry["raw"])
                assert round_tripped["stream"] in (
                    SAMPLE_BOOKTICKER_FRAME["stream"],
                    SAMPLE_TRADE_FRAME["stream"],
                )

    asyncio.run(scenario())
