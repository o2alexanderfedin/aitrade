"""Tests for gap_ledger.py's GapLedger — persisted outage rows — and, per
01-04-PLAN.md Task 1's gap-ledger policy correction, the three-signal
detection logic (`connection-silent`/`merged-silent`/`trade-id-skip`) that
`rotation.consume()` now records into it, replacing Plan 03's defective
per-stream reactive rule (17 minutes of live running logged two false
"outages" on `trade` alone while bookTicker flowed and `trade_id` stayed
contiguous -- a market lull, not a capture outage)."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path

from data.capture.gap_ledger import GapLedger
from data.capture.rotation import consume
from data.capture.seq import SeqAssigner
from tests.fixtures.payloads import SAMPLE_BOOKTICKER_FRAME, SAMPLE_TRADE_FRAME


def _bookticker(update_id: int) -> dict:
    frame = copy.deepcopy(SAMPLE_BOOKTICKER_FRAME)
    frame["data"]["u"] = update_id
    return frame


def _trade(trade_id: int) -> dict:
    frame = copy.deepcopy(SAMPLE_TRADE_FRAME)
    frame["data"]["t"] = trade_id
    return frame


async def _drain_via_consume(queue: asyncio.Queue, data_root: Path, **kwargs) -> None:
    """Push everything already queued through `consume()` and flush, by
    pre-setting `shutdown_event` (matches test_reconnect.py's helper)."""
    assigner = kwargs.pop("assigner", None) or SeqAssigner()
    shutdown_event = asyncio.Event()
    shutdown_event.set()
    await consume(
        queue,
        assigner,
        data_root,
        kwargs.pop("symbol", "BTCUSDT"),
        flush_rows=kwargs.pop("flush_rows", 1000),
        rotation_seconds=kwargs.pop("rotation_seconds", 1000.0),
        shutdown_event=shutdown_event,
        **kwargs,
    )


def test_record_gap_persists_one_row_readable_by_a_fresh_instance(
    tmp_path: Path,
) -> None:
    ledger = GapLedger(tmp_path)
    ledger.record_gap(
        stream="trade",
        conn_id="merged",
        gap_start_rtime=1000,
        gap_end_rtime=6000,
        cause="no message for 5.0s",
    )

    fresh_ledger = GapLedger(tmp_path)
    df = fresh_ledger.read_all()

    assert df.height == 1
    row = df.row(0, named=True)
    assert row["stream"] == "trade"
    assert row["conn_id"] == "merged"
    assert row["gap_start_rtime"] == 1000
    assert row["gap_end_rtime"] == 6000
    assert row["cause"] == "no message for 5.0s"
    assert isinstance(row["detected_at"], int)
    assert row["detected_at"] > 0


def test_record_gap_twice_appends_two_rows(tmp_path: Path) -> None:
    ledger = GapLedger(tmp_path)
    ledger.record_gap(
        stream="bookTicker",
        conn_id="merged",
        gap_start_rtime=1000,
        gap_end_rtime=6000,
        cause="no message for 5.0s",
    )
    ledger.record_gap(
        stream="trade",
        conn_id="merged",
        gap_start_rtime=2000,
        gap_end_rtime=8000,
        cause="no message for 6.0s",
    )

    df = ledger.read_all()
    assert df.height == 2


def test_read_all_on_empty_ledger_returns_empty_frame_with_schema(
    tmp_path: Path,
) -> None:
    ledger = GapLedger(tmp_path)
    df = ledger.read_all()
    assert df.height == 0
    assert set(df.columns) == {
        "stream",
        "conn_id",
        "gap_start_rtime",
        "gap_end_rtime",
        "cause",
        "detected_at",
        "ledger_version",
    }


def test_record_gap_writes_current_ledger_version(tmp_path: Path) -> None:
    ledger = GapLedger(tmp_path)
    ledger.record_gap(
        stream="trade",
        conn_id="merged",
        gap_start_rtime=1000,
        gap_end_rtime=6000,
        cause="no message for 5.0s",
    )
    row = ledger.read_all().row(0, named=True)
    assert row["ledger_version"] == 2


def test_read_all_backfills_ledger_version_1_on_a_legacy_file_missing_the_column(
    tmp_path: Path,
) -> None:
    """A Plan 03 ledger.parquet predates the ledger_version column entirely.
    read_all() must backfill it as 1 (not crash, not silently omit it) so
    Phase 3 DQ reports can filter `ledger_version >= 2` without needing a
    separate migration pass over every historical file."""
    import polars as pl

    from data.capture.gap_ledger import GAP_LEDGER_SCHEMA

    legacy_schema = {k: v for k, v in GAP_LEDGER_SCHEMA.items() if k != "ledger_version"}
    legacy_df = pl.DataFrame(
        {
            "stream": ["trade"],
            "conn_id": ["merged"],
            "gap_start_rtime": [1000],
            "gap_end_rtime": [6000],
            "cause": ["no message for 5.0s"],
            "detected_at": [123],
        },
        schema=legacy_schema,
    )
    ledger_path = tmp_path / "gap_ledger" / "ledger.parquet"
    ledger_path.parent.mkdir(parents=True)
    legacy_df.write_parquet(ledger_path)

    df = GapLedger(tmp_path).read_all()
    assert df.height == 1
    assert df.row(0, named=True)["ledger_version"] == 1


def test_record_gap_appends_cleanly_onto_a_legacy_file_missing_ledger_version(
    tmp_path: Path,
) -> None:
    """record_gap() must not raise a schema-mismatch error when appending a
    v2 row onto a pre-existing v1 (column-less) ledger file."""
    import polars as pl

    from data.capture.gap_ledger import GAP_LEDGER_SCHEMA

    legacy_schema = {k: v for k, v in GAP_LEDGER_SCHEMA.items() if k != "ledger_version"}
    legacy_df = pl.DataFrame(
        {
            "stream": ["trade"],
            "conn_id": ["merged"],
            "gap_start_rtime": [1000],
            "gap_end_rtime": [6000],
            "cause": ["no message for 5.0s"],
            "detected_at": [123],
        },
        schema=legacy_schema,
    )
    ledger_path = tmp_path / "gap_ledger" / "ledger.parquet"
    ledger_path.parent.mkdir(parents=True)
    legacy_df.write_parquet(ledger_path)

    ledger = GapLedger(tmp_path)
    ledger.record_gap(
        stream="trade",
        conn_id="merged",
        gap_start_rtime=2000,
        gap_end_rtime=8000,
        cause="no message for 6.0s",
    )

    df = ledger.read_all()
    assert df.height == 2
    versions = sorted(df["ledger_version"].to_list())
    assert versions == [1, 2]


def test_trade_silent_while_bookticker_flows_is_not_recorded(tmp_path: Path) -> None:
    """(a) trade silent 8s while bookTicker flows on the SAME connection ->
    ledger unchanged. bookTicker frames keep conn_id="A" alive, so neither
    connection-silent nor merged-silent fires; there is no more per-stream
    rule left to fire on trade's silence alone."""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(("A", 0, _trade(1)))
        # bookTicker keeps flowing on conn A every 2s while trade is silent.
        for i, t in enumerate((2_000_000_000, 4_000_000_000, 6_000_000_000, 8_000_000_000)):
            await queue.put(("A", t, _bookticker(100 + i)))

        await _drain_via_consume(queue, tmp_path, gap_threshold_seconds=5.0)

        assert GapLedger(tmp_path).read_all().height == 0

    asyncio.run(scenario())


def test_both_streams_silent_on_one_connection_is_connection_silent_not_merged(
    tmp_path: Path,
) -> None:
    """(b) both streams silent 6s on connection A while B flows -> one
    connection-silent row for A, nothing merged-silent (B keeps the merged
    watermark fresh)."""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(("A", 0, _bookticker(1)))
        await queue.put(("B", 1_000_000_000, _bookticker(2)))
        await queue.put(("B", 3_000_000_000, _bookticker(3)))
        await queue.put(("B", 5_000_000_000, _bookticker(4)))
        # A resumes after 6s of its own silence; B kept the merged watermark
        # fresh throughout, so only connection-silent for A should fire.
        await queue.put(("A", 6_000_000_000, _bookticker(5)))

        await _drain_via_consume(queue, tmp_path, gap_threshold_seconds=5.0)

        df = GapLedger(tmp_path).read_all()
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["stream"] == "__connection__"
        assert row["conn_id"] == "A"
        assert row["cause"].startswith("connection-silent")

    asyncio.run(scenario())


def test_both_connections_silent_is_merged_silent(tmp_path: Path) -> None:
    """(c) both connections silent 6s -> one merged-silent row."""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(("A", 0, _bookticker(1)))
        await queue.put(("A", 6_000_000_000, _bookticker(2)))

        await _drain_via_consume(queue, tmp_path, gap_threshold_seconds=5.0)

        df = GapLedger(tmp_path).read_all()
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["stream"] == "__connection__"
        assert row["conn_id"] == "merged"
        assert row["cause"].startswith("merged-silent")

    asyncio.run(scenario())


def test_trade_id_skip_records_missing_id_count(tmp_path: Path) -> None:
    """(d) merged trade rows with ids 100, 101, 105 -> one trade-id-skip row
    naming 3 missing ids (102, 103, 104)."""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(("A", 0, _trade(100)))
        await queue.put(("A", 100_000_000, _trade(101)))
        await queue.put(("A", 200_000_000, _trade(105)))

        await _drain_via_consume(queue, tmp_path, gap_threshold_seconds=5.0)

        df = GapLedger(tmp_path).read_all()
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["stream"] == "trade"
        assert row["conn_id"] == "merged"
        assert row["cause"] == "trade-id-skip: missing 3 ids (102..104)"

    asyncio.run(scenario())


def test_trade_id_skip_ignores_negative_diff_from_late_redundant_delivery(
    tmp_path: Path,
) -> None:
    """A late-but-lower trade_id arriving from the redundant connection
    after a numerically-higher id was already processed (the exact
    late-delivery scenario BoundedDedup's anti-high-water-mark design
    exists to preserve) must not be misreported as a skip, and must not
    rewind the trade-id watermark."""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(("A", 0, _trade(100)))
        await queue.put(("A", 100_000_000, _trade(101)))
        await queue.put(("A", 200_000_000, _trade(103)))  # jump: missing 102
        await queue.put(("B", 250_000_000, _trade(102)))  # late-arriving fill

        await _drain_via_consume(queue, tmp_path, gap_threshold_seconds=5.0)

        df = GapLedger(tmp_path).read_all()
        assert df.height == 1  # only the genuine 101->103 skip
        row = df.row(0, named=True)
        assert row["cause"] == "trade-id-skip: missing 1 id (102..102)"

    asyncio.run(scenario())


def test_last_seen_state_updates_before_dedup_drop_slow_connection_never_flagged(
    tmp_path: Path,
) -> None:
    """A connection whose every frame loses the dedup race (arrives after
    its twin already delivered the same key) must still be recognized as
    alive: last_seen_state is updated on every raw frame BEFORE dedup is
    consulted, not only for frames that survive it."""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        last_seen_state: dict[str, int] = {}
        # B's frames are always duplicates of A's (same key), arriving a
        # few ms later, for well beyond the 5s threshold -- 8 seconds of
        # B "losing the race" every time.
        for i in range(9):
            t = i * 1_000_000_000
            frame = _bookticker(1000 + i)
            await queue.put(("A", t, frame))
            await queue.put(("B", t + 10_000_000, copy.deepcopy(frame)))

        await _drain_via_consume(
            queue, tmp_path, gap_threshold_seconds=5.0, last_seen_state=last_seen_state
        )

        assert GapLedger(tmp_path).read_all().height == 0
        assert "B" in last_seen_state

    asyncio.run(scenario())
