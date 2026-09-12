"""A scripted local websocket server for capture-daemon tests.

No real network egress — a local `websockets.serve()` on an OS-assigned
loopback port, scripted to send a fixed sequence of frames (optionally
withholding some stream kinds entirely) so reconnect/liveness paths can be
proven without depending on the real exchange.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import orjson
import websockets

from data.capture.parse import stream_kind_of
from tests.fixtures.payloads import SAMPLE_BOOKTICKER_FRAME, SAMPLE_TRADE_FRAME


@dataclass
class ScriptedFrame:
    envelope: dict
    delay_seconds: float = 0.0


DEFAULT_SCRIPT: list[ScriptedFrame] = [
    ScriptedFrame(envelope=SAMPLE_BOOKTICKER_FRAME, delay_seconds=0.0),
    ScriptedFrame(envelope=SAMPLE_TRADE_FRAME, delay_seconds=0.0),
]


@asynccontextmanager
async def scripted_server(
    frames: list[ScriptedFrame] | None = None,
    withhold: set[str] | None = None,
) -> AsyncIterator[int]:
    """Start a scripted server; yield the bound loopback port.

    Each connection sends every `frame.envelope` in `frames` (default:
    one bookTicker + one trade), after its `delay_seconds`, skipping any
    frame whose `stream_kind_of(frame.envelope)` is in `withhold`
    (simulating the PROBE-RESULTS "silent no-data" failure mode). The
    handler holds the connection open (`await ws.wait_closed()`) after
    sending its script so the client's auto-reconnect iterator does not
    immediately re-receive the same frames in a tight loop.
    """
    script = frames if frames is not None else DEFAULT_SCRIPT
    withheld = withhold or set()

    async def handler(ws: websockets.ServerConnection) -> None:
        for frame in script:
            if stream_kind_of(frame.envelope) in withheld:
                continue
            if frame.delay_seconds:
                await asyncio.sleep(frame.delay_seconds)
            await ws.send(orjson.dumps(frame.envelope).decode())
        await ws.wait_closed()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield port
