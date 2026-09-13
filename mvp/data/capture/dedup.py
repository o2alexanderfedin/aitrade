"""Bounded, TTL-evicted dedup stage for the two-connection redundant feed.

Rejected design: a high-water-mark dedup (`key <= last_seen_key => duplicate`)
is deliberately NOT used here, even though it would use O(1) memory instead
of a seen-set. The whole point of running two independent connections is
that one of them can legitimately deliver a key *late*, out of numeric
order relative to keys the other connection has already delivered (e.g.
connection A misses key 102 due to a brief hiccup but delivers 100, 101,
103; connection B delivers 102 afterward). A high-water-mark design would
see 102 arrive after 103 was already the high-water mark and wrongly call
it a duplicate, silently dropping the one row redundancy exists to save.
`BoundedDedup` instead tracks an explicit seen-set of `(stream, key)`
pairs, bounded by evicting entries older than `ttl_seconds` (not by a
high-water-mark shortcut), so a late-but-genuinely-new key is always kept.
"""

from __future__ import annotations

from collections import OrderedDict

from data.capture.parse import FrameParseError, stream_kind_of


def dedup_key(frame: dict) -> tuple[str, int]:
    """Return the `(stream, id)` dedup key for a decoded combined-stream frame.

    `("bookTicker", data["u"])` for bookTicker frames, `("trade", data["t"])`
    for trade frames — the two dedup keys proven sound by the live A1
    redundancy probe (see evidence/PROBE-RESULTS.md's Addendum).
    """
    stream = stream_kind_of(frame)
    try:
        data = frame["data"]
        if stream == "bookTicker":
            return (stream, data["u"])
        return (stream, data["t"])
    except KeyError as exc:
        raise FrameParseError(f"frame missing dedup field: {exc}") from exc


class BoundedDedup:
    """Bounded, TTL-evicted per-stream seen-set for `(stream, key)` pairs.

    `self._seen` is an `OrderedDict` keyed by `(stream, key)` mapping to the
    `rtime_ns` at which it was first seen. Because duplicates never update
    an existing entry's position, insertion order equals first-seen order,
    which makes front-of-dict eviction (`popitem(last=False)`) correct and
    O(1) amortized per evicted entry — important at ~118 msg/s, where an
    O(n) full-dict scan on every call would not keep up over a multi-day run.

    Invariant (WR-04, 01-REVIEW.md): `ttl_seconds` must exceed the
    worst-case lag between the two producer connections' delivery of the
    SAME logical event, not just typical jitter. `rtime_ns` is
    `time.time_ns()` recorded independently by each connection at the
    moment its own `ws.recv()` returns, and both connections enqueue onto
    one shared queue — nothing guarantees connection A's and B's items
    interleave in strictly increasing `rtime_ns` insertion order (only that
    each single connection's own stream is monotonic). If one connection
    genuinely lags (TCP stall then burst-delivers), eviction is keyed off a
    high-water `_max_seen_rtime_ns` watermark (the max `rtime_ns` observed
    across ANY call so far, not the current call's own value), so a
    momentarily out-of-order/small `rtime_ns` cannot make the effective
    cutoff regress and strand an older, already-evictable entry behind a
    still-fresh one forever — it is evicted the next time the front-of-dict
    scan reaches it, bounded by one TTL window's worth of delay. The
    front-of-dict scan itself still stops at the first entry that is not
    (yet) evictable under the CURRENT watermark, to keep this O(1)
    amortized per call — an out-of-order entry behind that one is evicted
    once the entry in front of it ages out, not immediately.
    """

    def __init__(self, ttl_seconds: float = 120.0) -> None:
        self._ttl_ns = int(ttl_seconds * 1_000_000_000)
        self._seen: OrderedDict[tuple[str, int], int] = OrderedDict()
        self._max_seen_rtime_ns: int | None = None

    def _evict_older_than(self, current_rtime_ns: int) -> None:
        if (
            self._max_seen_rtime_ns is None
            or current_rtime_ns > self._max_seen_rtime_ns
        ):
            self._max_seen_rtime_ns = current_rtime_ns
        cutoff = self._max_seen_rtime_ns - self._ttl_ns
        while self._seen:
            oldest_key = next(iter(self._seen))
            if self._seen[oldest_key] >= cutoff:
                break
            self._seen.popitem(last=False)

    def is_duplicate(self, stream: str, key: int, rtime_ns: int) -> bool:
        """Return True if `(stream, key)` has been seen within the TTL
        window (a duplicate to drop), False if it is new (keep it)."""
        self._evict_older_than(rtime_ns)
        seen_key = (stream, key)
        if seen_key in self._seen:
            return True
        self._seen[seen_key] = rtime_ns
        return False
