"""Tests for the merged, deduplicated, gap-detected two-connection pipeline.

Drives `rotation.consume()` (dedup + gap-ledger behavior) directly with
synthetic queue items where determinism matters, and drives real
`ws_client.run_connection()` calls against the extended scripted fake
server for the reconnect/redundancy scenario, matching the three
`<behavior>` cases in 01-03-PLAN.md's Task 2.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
from pathlib import Path

import polars as pl

from data.capture.dedup import dedup_key
from data.capture.gap_ledger import GapLedger
from data.capture.rotation import consume, partition_dir
from data.capture.seq import SeqAssigner
from data.capture.ws_client import run_connection
from tests.fixtures.fake_ws_server import ScriptedFrame
from tests.fixtures.payloads import SAMPLE_BOOKTICKER_FRAME, SAMPLE_TRADE_FRAME


def _bookticker(update_id: int) -> dict:
    frame = copy.deepcopy(SAMPLE_BOOKTICKER_FRAME)
    frame["data"]["u"] = update_id
    return frame


def _trade(trade_id: int) -> dict:
    frame = copy.deepcopy(SAMPLE_TRADE_FRAME)
    frame["data"]["t"] = trade_id
    return frame


def _read_parquet_ids(data_root: Path, symbol: str, stream: str, id_column: str) -> set:
    stream_dir = partition_dir(data_root, symbol, stream)
    files = list(stream_dir.glob("**/*.parquet"))
    if not files:
        return set()
    df = pl.concat([pl.read_parquet(f) for f in files])
    return set(df[id_column].to_list())


def _read_parquet_rows(data_root: Path, symbol: str, stream: str) -> pl.DataFrame:
    stream_dir = partition_dir(data_root, symbol, stream)
    files = list(stream_dir.glob("**/*.parquet"))
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files])


async def _drain_via_consume(
    queue: asyncio.Queue, data_root: Path, symbol: str = "BTCUSDT", **kwargs
) -> None:
    """Push everything already in `queue` through `consume()` and flush,
    by pre-setting `shutdown_event` — `consume()`'s first iteration still
    drains one item via its normal poll path, then the drain loop empties
    the rest via `get_nowait()`, then performs the final unconditional
    flush and returns."""
    assigner = kwargs.pop("assigner", None) or SeqAssigner()
    shutdown_event = asyncio.Event()
    shutdown_event.set()
    await consume(
        queue,
        assigner,
        data_root,
        symbol,
        flush_rows=kwargs.pop("flush_rows", 1000),
        rotation_seconds=kwargs.pop("rotation_seconds", 1000.0),
        shutdown_event=shutdown_event,
        **kwargs,
    )


def test_duplicate_frame_from_two_connections_dedups_to_one_row(tmp_path: Path) -> None:
    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        frame = _bookticker(555)
        await queue.put(("A", 1_000_000_000, frame))
        await queue.put(("B", 1_050_000_000, copy.deepcopy(frame)))

        await _drain_via_consume(queue, tmp_path)

        rows = _read_parquet_rows(tmp_path, "BTCUSDT", "bookTicker")
        # The discriminating assertion: exactly ONE row reaches Parquet,
        # not two — a set-of-ids check alone would pass even with no
        # dedup at all, since both rows share update_id=555.
        assert rows.height == 1
        assert rows["update_id"].to_list() == [555]

    asyncio.run(scenario())


def test_gap_longer_than_threshold_produces_one_ledger_row(tmp_path: Path) -> None:
    """Plan 04 policy correction: a single connection (only "A" is ever
    used here) that goes silent for longer than the threshold is, by
    construction, BOTH connection-silent for A and merged-silent (no other
    connection is around to keep the merged watermark fresh) -- the
    merged-silent signal takes priority as the more informative one, so
    this now records a `__connection__`/merged row rather than the old
    per-stream `trade`/merged row Plan 03 produced. See
    01-04-SUMMARY.md's Deviations for why this test's assertions changed
    rather than the implementation being bent to match the stale plan text."""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        # Three distinct trade ids on the same stream: 0.5s gap (below the
        # 1.0s threshold), then a 2.5s gap (above threshold) -> exactly
        # one gap-ledger row expected.
        await queue.put(("A", 0, _trade(1)))
        await queue.put(("A", 500_000_000, _trade(2)))
        await queue.put(("A", 3_000_000_000, _trade(3)))

        await _drain_via_consume(queue, tmp_path, gap_threshold_seconds=1.0)

        ledger = GapLedger(tmp_path)
        df = ledger.read_all()
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["stream"] == "__connection__"
        assert row["conn_id"] == "merged"
        assert row["cause"].startswith("merged-silent")
        assert row["gap_start_rtime"] == 500_000_000
        assert row["gap_end_rtime"] == 3_000_000_000

    asyncio.run(scenario())


def test_gap_shorter_than_threshold_produces_zero_ledger_rows(tmp_path: Path) -> None:
    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(("A", 0, _trade(10)))
        await queue.put(("A", 500_000_000, _trade(11)))
        await queue.put(("A", 900_000_000, _trade(12)))

        await _drain_via_consume(queue, tmp_path, gap_threshold_seconds=1.0)

        ledger = GapLedger(tmp_path)
        df = ledger.read_all()
        assert df.height == 0

    asyncio.run(scenario())


def test_connection_a_drop_mid_delivery_b_continues_zero_missing_keys(
    tmp_path: Path,
) -> None:
    """Connection A closes abruptly partway through delivery (server drops
    it after its first scripted connection); connection B, independently
    scripted, delivers the full logical event set without interruption.
    The merged, deduplicated Parquet output must contain every key both
    connections were scripted to send — nothing missing.
    """
    from tests.fixtures.fake_ws_server import scripted_server

    script_a1 = [
        ScriptedFrame(envelope=_bookticker(101)),
        ScriptedFrame(envelope=_trade(201)),
        ScriptedFrame(envelope=_bookticker(102)),
    ]
    script_a2 = [
        ScriptedFrame(envelope=_bookticker(103)),
        ScriptedFrame(envelope=_trade(202)),
        ScriptedFrame(envelope=_bookticker(104)),
    ]
    script_b = [
        ScriptedFrame(envelope=_bookticker(101)),
        ScriptedFrame(envelope=_trade(201)),
        ScriptedFrame(envelope=_bookticker(102)),
        ScriptedFrame(envelope=_bookticker(103)),
        ScriptedFrame(envelope=_trade(202)),
        ScriptedFrame(envelope=_bookticker(104)),
    ]

    async def scenario() -> None:
        async with (
            scripted_server(connection_scripts=[script_a1, script_a2]) as port_a,
            scripted_server(connection_scripts=[script_b]) as port_b,
        ):
            queue: asyncio.Queue = asyncio.Queue()
            assigner = SeqAssigner()
            shutdown_event = asyncio.Event()

            task_a = asyncio.create_task(
                run_connection(
                    f"ws://127.0.0.1:{port_a}",
                    queue,
                    conn_id="A",
                    archive_dir=tmp_path / "raw_a",
                    expected_streams={"bookTicker", "trade"},
                    startup_timeout=2.0,
                )
            )
            task_b = asyncio.create_task(
                run_connection(
                    f"ws://127.0.0.1:{port_b}",
                    queue,
                    conn_id="B",
                    archive_dir=tmp_path / "raw_b",
                    expected_streams={"bookTicker", "trade"},
                    startup_timeout=2.0,
                )
            )
            consumer_task = asyncio.create_task(
                consume(
                    queue,
                    assigner,
                    tmp_path,
                    "BTCUSDT",
                    flush_rows=1000,
                    rotation_seconds=1000.0,
                    shutdown_event=shutdown_event,
                )
            )

            # Give A's drop+reconnect and B's uninterrupted delivery time
            # to complete on localhost (no backoff delay applies to a
            # ConnectionClosed caught and `continue`d inside the loop
            # body — see ws_client.py's docstring).
            await asyncio.sleep(1.5)

            for task in (task_a, task_b):
                if not task.done():
                    task.cancel()
            for task in (task_a, task_b):
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            shutdown_event.set()
            await consumer_task

        update_ids = _read_parquet_ids(tmp_path, "BTCUSDT", "bookTicker", "update_id")
        trade_ids = _read_parquet_ids(tmp_path, "BTCUSDT", "trade", "trade_id")
        assert update_ids == {101, 102, 103, 104}
        assert trade_ids == {201, 202}

    asyncio.run(scenario())


def test_dedup_key_helper_is_importable_for_test_assertions() -> None:
    # Sanity check that this module's helper import surface is stable.
    assert dedup_key(_bookticker(1)) == ("bookTicker", 1)
