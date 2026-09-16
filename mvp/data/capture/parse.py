"""orjson decode + canonical row construction for bookTicker/trade frames.

The millisecond-to-nanosecond conversion happens in exactly ONE place in
this whole codebase: the public `ms_to_ns` helper below. Every later
plan and test relies on this being the single conversion site (see
01-CONTEXT.md: Binance millisecond fields are multiplied by one million
in exactly one place, driven by a per-stream field map). `ms_to_ns` is
polymorphic over Python `int`, `pl.Series`, and `pl.Expr` via ordinary
operator overloading -- Phase 3's `data/ingest/normalize.py` imports and
calls it directly on a polars expression rather than adding a second
`* 1_000_000` site (enforced by `tools/check_ms_to_ns_site.py`).
"""

from __future__ import annotations

import orjson

from data.schema import SCHEMA_VERSION


class FrameParseError(ValueError):
    """Raised when a raw or decoded frame cannot be turned into a canonical row."""


def decode_frame(raw: bytes | str) -> dict:
    """Decode a raw websocket message into a dict, raising FrameParseError on bad JSON."""
    try:
        return orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise FrameParseError(f"invalid JSON frame: {exc}") from exc


def ms_to_ns(ms):
    """Convert a millisecond timestamp (int, pl.Series, or pl.Expr) to
    nanoseconds. The one conversion site -- see tools/check_ms_to_ns_site.py."""
    return ms * 1_000_000


def parse_bookticker(
    data: dict, seq: int, rtime_ns: int, source: str = "capture"
) -> dict:
    """Map a decoded bookTicker `data` payload to a canonical row dict."""
    try:
        return {
            "symbol": data["s"],
            "stream": "bookTicker",
            "update_id": data["u"],
            "etime": ms_to_ns(data["T"]),
            "event_time": ms_to_ns(data["E"]),
            "bid_price": float(data["b"]),
            "bid_qty": float(data["B"]),
            "ask_price": float(data["a"]),
            "ask_qty": float(data["A"]),
            "seq": seq,
            "rtime": rtime_ns,
            "source": source,
            "schema_version": SCHEMA_VERSION,
        }
    except KeyError as exc:
        raise FrameParseError(f"bookTicker frame missing field: {exc}") from exc


def parse_trade(data: dict, seq: int, rtime_ns: int, source: str = "capture") -> dict:
    """Map a decoded trade `data` payload to a canonical row dict."""
    try:
        return {
            "symbol": data["s"],
            "stream": "trade",
            "trade_id": data["t"],
            "etime": ms_to_ns(data["T"]),
            "event_time": ms_to_ns(data["E"]),
            "price": float(data["p"]),
            "qty": float(data["q"]),
            "is_buyer_maker": bool(data["m"]),
            "seq": seq,
            "rtime": rtime_ns,
            "source": source,
            "schema_version": SCHEMA_VERSION,
        }
    except KeyError as exc:
        raise FrameParseError(f"trade frame missing field: {exc}") from exc


def stream_kind_of(frame: dict) -> str:
    """Return `"bookTicker"` or `"trade"` based on `frame["stream"]`'s suffix.

    Shared by `parse_combined_frame`, `ws_client.py`, `rotation.py`, and
    Plan 03's dedup stage — the one place this dispatch logic lives.
    """
    try:
        stream_name = frame["stream"]
    except KeyError as exc:
        raise FrameParseError(f"envelope missing field: {exc}") from exc

    if stream_name.endswith("@bookTicker"):
        return "bookTicker"
    if stream_name.endswith("@trade"):
        return "trade"
    raise FrameParseError(f"unrecognized stream suffix: {stream_name!r}")


def parse_combined_frame(
    frame: dict, seq: int, rtime_ns: int, source: str = "capture"
) -> dict:
    """Dispatch a combined-stream envelope to parse_bookticker or parse_trade."""
    kind = stream_kind_of(frame)
    try:
        data = frame["data"]
    except KeyError as exc:
        raise FrameParseError(f"envelope missing field: {exc}") from exc

    if kind == "bookTicker":
        return parse_bookticker(data, seq, rtime_ns, source)
    return parse_trade(data, seq, rtime_ns, source)
