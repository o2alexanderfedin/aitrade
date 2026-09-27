"""A reconnect must leave an observable trace on stdout.

Found during Phase 1's live run: `ws_client.run_connection()` auto-reconnects
silently via the `async for ws in websockets.connect(...)` iterator. A clean
reconnect that completes under the gap threshold leaves no log line and no
ledger row — indistinguishable from "never disconnected". Binance force-closes
every connection at ~24h; without a log line, the deferred ">=24h reconnect
check" in STATE.md is unverifiable except by forensic rtime analysis of the
raw archive. This test pins the contract: every established connection and
every close-then-reconnect prints one line naming the conn_id.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from data.capture.ws_client import run_connection
from tests.fixtures.fake_ws_server import ScriptedFrame, scripted_server
from tests.fixtures.payloads import SAMPLE_BOOKTICKER_FRAME, SAMPLE_TRADE_FRAME


def _bookticker(u: int) -> dict:
    env = {**SAMPLE_BOOKTICKER_FRAME, "data": {**SAMPLE_BOOKTICKER_FRAME["data"]}}
    env["data"]["u"] = u
    return env


def _trade(t: int) -> dict:
    env = {**SAMPLE_TRADE_FRAME, "data": {**SAMPLE_TRADE_FRAME["data"]}}
    env["data"]["t"] = t
    return env


def test_drop_and_reconnect_are_logged_with_conn_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Server drops the first connection after two frames; the client
    reconnects and receives two more. Stdout must show: one 'established'
    line for attempt 1, one 'closed ... reconnecting' line, and one
    'established' line for attempt 2 — all naming conn_id 'A'."""
    script_1 = [
        ScriptedFrame(envelope=_bookticker(1)),
        ScriptedFrame(envelope=_trade(1)),
    ]
    script_2 = [
        ScriptedFrame(envelope=_bookticker(2)),
        ScriptedFrame(envelope=_trade(2)),
    ]

    async def scenario() -> None:
        async with scripted_server(connection_scripts=[script_1, script_2]) as port:
            queue: asyncio.Queue = asyncio.Queue()
            task = asyncio.create_task(
                run_connection(
                    f"ws://127.0.0.1:{port}",
                    queue,
                    conn_id="A",
                    archive_dir=tmp_path / "raw_a",
                    expected_streams={"bookTicker", "trade"},
                    startup_timeout=2.0,
                )
            )
            # Wait until all four frames have been enqueued (proves the
            # reconnect actually happened), bounded so a hang fails fast.
            for _ in range(40):
                if queue.qsize() >= 4:
                    break
                await asyncio.sleep(0.05)
            assert queue.qsize() >= 4, "reconnect did not deliver script_2"
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())

    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("connection A:")]
    assert any("established" in ln and "attempt=1" in ln for ln in lines), out
    assert any("closed" in ln and "reconnecting" in ln for ln in lines), out
    assert any("established" in ln and "attempt=2" in ln for ln in lines), out
    # Every closed-line must carry the close code so a 24h server close
    # (1001/1006) is distinguishable from a local network fault.
    assert all("code=" in ln for ln in lines if "closed" in ln), out
