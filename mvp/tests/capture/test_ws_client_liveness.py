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
