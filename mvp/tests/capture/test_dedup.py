"""Tests for dedup.py's dedup_key() and BoundedDedup.

The anti-high-water-mark case is the load-bearing test in this file: a
key delivered late by the redundant connection (out of numeric order
relative to keys already seen) must still be kept, not dropped.
"""

from __future__ import annotations

import copy

from data.capture.dedup import BoundedDedup, dedup_key
from tests.fixtures.payloads import SAMPLE_BOOKTICKER_FRAME, SAMPLE_TRADE_FRAME


def test_dedup_key_bookticker_uses_stream_and_update_id() -> None:
    assert dedup_key(SAMPLE_BOOKTICKER_FRAME) == ("bookTicker", 11538015451847)


def test_dedup_key_trade_uses_stream_and_trade_id() -> None:
    assert dedup_key(SAMPLE_TRADE_FRAME) == ("trade", 8072551060)


def test_first_sighting_is_not_a_duplicate() -> None:
    dedup = BoundedDedup(ttl_seconds=120.0)
    assert dedup.is_duplicate("bookTicker", 42, rtime_ns=1000) is False


def test_second_sighting_within_ttl_is_a_duplicate() -> None:
    dedup = BoundedDedup(ttl_seconds=120.0)
    assert dedup.is_duplicate("bookTicker", 42, rtime_ns=1000) is False
    assert dedup.is_duplicate("bookTicker", 42, rtime_ns=1500) is True


def test_late_arrival_from_redundant_connection_is_not_dropped() -> None:
    """The anti-high-water-mark case.

    Connection A delivers 100, 101, 103 (it never delivers 102).
    Connection B later delivers 102 — out of numeric order relative to
    103, which was already seen. A high-water-mark design
    (`key <= last_seen => duplicate`) would wrongly call 102 a duplicate
    of 103. The actual (bounded seen-set) design must not.
    """
    dedup = BoundedDedup(ttl_seconds=120.0)
    assert dedup.is_duplicate("bookTicker", 100, rtime_ns=1_000_000_000) is False
    assert dedup.is_duplicate("bookTicker", 101, rtime_ns=1_100_000_000) is False
    assert dedup.is_duplicate("bookTicker", 103, rtime_ns=1_300_000_000) is False
    # 102, delivered late by connection B, must be kept — not a duplicate.
    assert dedup.is_duplicate("bookTicker", 102, rtime_ns=1_350_000_000) is False


def test_seen_set_is_bounded_and_evicts_aged_entries() -> None:
    ttl_seconds = 5.0
    dedup = BoundedDedup(ttl_seconds=ttl_seconds)
    ttl_ns = int(ttl_seconds * 1_000_000_000)
    burst_size = 5_000

    # Spread the burst across 10 TTL windows worth of rtime.
    for i in range(burst_size):
        rtime_ns = i * (ttl_ns * 10 // burst_size)
        dedup.is_duplicate("bookTicker", i, rtime_ns=rtime_ns)

    # The seen-set must not have grown to the full burst size — eviction
    # of aged entries keeps it bounded to roughly one TTL window's worth.
    assert len(dedup._seen) < burst_size


def test_duplicate_frames_from_two_connections_deep_copy_safe() -> None:
    """Sanity: mutating a copy of a shared fixture frame must not corrupt
    the original — guards against accidental fixture aliasing bugs in
    later tests that mutate `u`/`t` to build synthetic streams."""
    frame_a = copy.deepcopy(SAMPLE_BOOKTICKER_FRAME)
    frame_a["data"]["u"] = 999
    assert dedup_key(SAMPLE_BOOKTICKER_FRAME) == ("bookTicker", 11538015451847)
    assert dedup_key(frame_a) == ("bookTicker", 999)
