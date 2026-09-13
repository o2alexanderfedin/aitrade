"""Binance USD-M futures websocket URL construction and scheme guard.

Live-stream scope is the routed `/public` combined stream only. `aggTrade`
and the `/market` class are intentionally excluded — out of Phase 1 scope
per CLAUDE.md (aggTrades is a Phase 3 cross-check only, never the primary
tape).
"""

from __future__ import annotations

PUBLIC_BASE = "wss://fstream.binance.com/public"


def combined_public_stream_url(symbol: str) -> str:
    """Build the combined Public-class stream URL for `symbol`.

    Carries both bookTicker and trade on one connection (verified live,
    see evidence/PROBE-RESULTS.md).
    """
    sym = symbol.lower()
    return f"{PUBLIC_BASE}/stream?streams={sym}@bookTicker/{sym}@trade"


def assert_secure_url(url: str) -> None:
    """Permit only `wss://`, plus loopback `ws://` for test fixtures.

    Loopback (`ws://127.0.0.1` / `ws://localhost`) is a test-only exception
    for Plan 02's fake-websocket-server tests. Every other scheme/host
    combination raises ValueError naming the rejected URL. Certificate
    verification is never disabled anywhere in this codebase.
    """
    if url.startswith("wss://"):
        return
    if url.startswith("ws://127.0.0.1") or url.startswith("ws://localhost"):
        return
    raise ValueError(f"refusing insecure/unrecognized websocket URL: {url!r}")
