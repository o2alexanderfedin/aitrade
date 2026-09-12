"""A scripted local websocket server for capture-daemon tests.

No real network egress — a local `websockets.serve()` on an OS-assigned
loopback port, scripted to send a fixed sequence of frames (optionally
withholding some stream kinds entirely) so reconnect/liveness paths can be
proven without depending on the real exchange.
"""

from __future__ import annotations

import asyncio
import itertools
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
    connection_scripts: list[list[ScriptedFrame]] | None = None,
    withhold: set[str] | None = None,
) -> AsyncIterator[int]:
    """Start a scripted server; yield the bound loopback port.

    Accepts multiple sequential client connections over its lifetime
    (simulating drop + reconnect), serving `connection_scripts[0]` to the
    first accepted connection, `connection_scripts[1]` to the second, and
    so on. Every script except the last is followed by the server closing
    the connection (simulating a drop); the last script's connection is
    held open (`await ws.wait_closed()`) so the client's auto-reconnect
    iterator does not immediately re-receive the same frames in a tight
    loop. A connection beyond the end of `connection_scripts` receives no
    frames and is held open.

    `frames: list[ScriptedFrame]` (Plan 02's original single-script
    argument) is backward-compatible shorthand for
    `connection_scripts=[frames]` — the existing Plan 02 call sites
    (`test_ws_client_liveness.py`) only ever exercise the first accepted
    connection, so this preserves their behavior unchanged.
    """
    if connection_scripts is not None:
        scripts = connection_scripts
    else:
        scripts = [frames if frames is not None else DEFAULT_SCRIPT]
    withheld = withhold or set()
    connection_counter = itertools.count()

    async def handler(ws: websockets.ServerConnection) -> None:
        idx = next(connection_counter)
        if idx < len(scripts):
            for frame in scripts[idx]:
                if stream_kind_of(frame.envelope) in withheld:
                    continue
                if frame.delay_seconds:
                    await asyncio.sleep(frame.delay_seconds)
                await ws.send(orjson.dumps(frame.envelope).decode())
            if idx < len(scripts) - 1:
                # Not the last scripted connection — close now, simulating
                # a drop, so the client's auto-reconnect iterator advances
                # to the next connection's script.
                return
        await ws.wait_closed()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield port
