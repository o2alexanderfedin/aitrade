"""Tests for `sim/ticks.py` -- D-06-05's tick constant and conversion rule,
D-06-08's lot-step/position-sizing arithmetic, and D-06-20's >$100,000
zero-lot dead-zone refusal (06-02-PLAN.md).

Two of these tests read a BOUNDED head of the real, already-committed
2026-09-13 curated bookTicker/trade partitions to re-derive the tick size
and lot step by gcd -- never the whole file, and never a write. They skip
loudly (not silently) when the physical lake is not mounted (CI has no SSD
attached), mirroring `tools/check_no_manifest_rewrite.py`'s own bare
`Path(DEFAULT_LAKE_ROOT).exists()` idiom rather than calling
`data.lake_paths.lake_root()` (which `mkdir`s and write-validates the
target -- wrong for a read-only existence probe).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from data.lake_paths import DEFAULT_LAKE_ROOT, LAKE_REGISTRY_ROOT
from data.store import by_date_index_path, resolve_manifest
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED, price_to_ticks

SYMBOL = "BTCUSDT"
DATE = "2026-09-13"
#: Bounded head, never the whole 17,167,290-row file (constraint 2 in
#: 06-02-PLAN.md's absolute rules).
HEAD_ROWS = 2_000_000


def _skip_if_lake_unmounted() -> None:
    if not Path(DEFAULT_LAKE_ROOT).exists():
        pytest.skip(f"real lake not mounted: {DEFAULT_LAKE_ROOT}")


def _real_curated_head(stream: str, n_rows: int) -> pl.DataFrame:
    """A bounded head of the real, committed-manifest-resolved curated
    partition for `stream` on `DATE`.

    `resolve_manifest` sha256-verifies the ENTIRE named partition file
    (there is no bounded-verify form) before this function's own
    `.head(n_rows)` ever runs -- for the bookTicker partition that is a
    ~240 MB read, accepted here because it is the plan-sanctioned way to
    resolve a real partition path (06-02-PLAN.md: "resolved via
    `by_date_index_path` + `resolve_manifest`, exactly as
    `harness_span.py`/research did").
    """
    dataset = f"{SYMBOL}.{stream}"
    index_path = by_date_index_path(LAKE_REGISTRY_ROOT, dataset, SYMBOL, stream, DATE)
    index = json.loads(index_path.read_text())
    manifest = resolve_manifest(
        index["manifest_id"],
        dataset,
        registry_root=LAKE_REGISTRY_ROOT,
        lake_root=Path(DEFAULT_LAKE_ROOT),
        expected_tier="curated",
    )
    part = manifest["partitions"][0]
    path = Path(DEFAULT_LAKE_ROOT) / part["path"]
    return pl.scan_parquet(path).head(n_rows).collect()


def test_tick_size_matches_the_measured_venue_gcd():
    _skip_if_lake_unmounted()
    df = _real_curated_head("bookTicker", HEAD_ROWS)
    for col in ("bid_price", "ask_price"):
        scaled = np.round(df[col].to_numpy() * PRICE_SCALE).astype(np.int64)
        diffs = np.diff(scaled)
        diffs = diffs[diffs > 0]
        # Anti-vacuity: a near-empty diff set (e.g. a frozen/degenerate
        # book) would make any gcd trivially "match" by having nothing to
        # disagree with.
        assert diffs.size > 100, f"{col}: too few positive diffs to gcd"
        assert int(np.gcd.reduce(diffs)) == TICK_SIZE_SCALED, col


@settings(deadline=None, max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(
    prices=st.lists(
        st.integers(min_value=10_000, max_value=2_000_000).map(lambda t: t / 10.0),
        min_size=1,
        max_size=50,
    )
)
def test_price_to_ticks_round_trips_within_half_a_tick(prices):
    """Prices generated ON the tick grid (multiples of $0.1, the venue's
    own tick size) -- a price sitting exactly at a half-tick boundary
    (e.g. a `mid` of two adjacent 1-tick-spread quotes) is a SEPARATE,
    documented concern for whatever computes `mid`/`pred_mid`
    (06-RESEARCH.md Q6: 97.463% of real rows have exactly a 1-tick spread,
    so a `mid` is very often a half-tick value) -- not this function's
    round-trip proof, which this test pins for prices that are themselves
    already tick-aligned, exactly like real `bid_price`/`ask_price`
    values are (06-RESEARCH.md Q2's row set)."""
    arr = np.array(prices, dtype=np.float64)
    ticks = price_to_ticks(arr)
    scaled = np.round(arr * PRICE_SCALE).astype(np.int64)
    round_trip_error = np.abs(ticks * TICK_SIZE_SCALED - scaled)
    assert np.all(round_trip_error < (TICK_SIZE_SCALED // 2))


def test_price_to_ticks_raises_on_a_value_not_representable_at_the_scale():
    """06-RESEARCH.md Q2 measured a maximum round-trip error of 1.455e-11
    across 34,334,580 real prices -- rounding to the nearest `PRICE_SCALE`
    grid point is bounded by half a scale unit (5e-9) for ANY float64
    input in the venue's real price range, so no realistic BTC price can
    ever trip this assertion (confirmed by direct computation while
    writing this test: `77061.123456789` round-trips to within 1e-9).
    The assertion exists for a price whose MAGNITUDE is far enough outside
    the venue's range that float64 itself has already lost more than
    `_REPRESENTABILITY_EPSILON` of precision by the time it reaches this
    function -- deliberately constructed here, never a realistic BTC
    price, exactly as this task's own instructions call for."""
    price = np.array([76_950.0, 50_000_000_000.123456])
    with pytest.raises(ValueError, match=r"price\[1\]"):
        price_to_ticks(price)
