"""D-06-05's tick constant and conversion rule (Task 1). D-06-08's
lot-step/position-sizing arithmetic and D-06-20's >$100,000 zero-lot
dead-zone refusal arrive in Task 2 of the same plan.

No polars or numba dependency here, on purpose: this module is importable
and testable in complete isolation, so Plan 06-03's `@njit` kernel and its
polars-boundary wrapper both import ONE already-tested source of these
constants instead of each defining its own copy that could silently drift
from the other's.

`PRICE_SCALE` and `TICK_SIZE_SCALED` are QUANTITY/PRICE fixed-point
scales, not seconds-to-nanoseconds conversions. `tools.check_ms_to_ns_site`
resolves values against the `{1e3, 1e6, 1e9}` time-scale family only (see
that module's own docstring); `1e8` is deliberately outside it, exactly
like `data.time_ns.QTY_SCALE` already is. No allowlist entry belongs here
-- do not add one by reflex just because this module does fixed-point
scaling.

Every constant is a module-level, UPPER_CASE `int`, so a future `@njit`
caller may read it per `tools.check_numba_globals`'s rule.
"""

from __future__ import annotations

import numpy as np

from data.time_ns import QTY_SCALE

__all__ = [
    "PRICE_SCALE",
    "TICK_SIZE_SCALED",
    "price_to_ticks",
]

#: Reuse the project's one 1e-8 fixed-point idiom for prices too (D-06-05,
#: 06-RESEARCH.md Q2), rather than inventing a second scale constant.
PRICE_SCALE: int = QTY_SCALE

#: 0.1 USDT at `PRICE_SCALE` (D-06-05). Pinned by
#: `tests/sim/test_ticks.py::test_tick_size_matches_the_measured_venue_gcd`
#: against the real 2026-09-13 curated bookTicker partition: gcd of every
#: positive adjacent `bid_price`/`ask_price` difference, at `PRICE_SCALE`
#: fixed point, over a bounded 2,000,000-row head, equals 10,000,000.
TICK_SIZE_SCALED: int = 10_000_000

#: The largest discrepancy tolerated between a price and its nearest
#: `PRICE_SCALE` grid point before it is judged "not representable at this
#: scale" rather than "float noise". 06-RESEARCH.md Q2 measured a maximum
#: round-trip error of 1.455e-11 across 34,334,580 real prices -- 1e-6
#: leaves five orders of magnitude of margin. NOTE: rounding to the
#: nearest `PRICE_SCALE` grid point is bounded by half a scale unit
#: (5e-9) for any float64 value in the venue's real price range, so this
#: assertion cannot fire on a realistic BTC price; it exists for a value
#: whose MAGNITUDE is far enough outside that range that float64 itself
#: has already lost precision (see the "not representable" test's own
#: docstring for the measured boundary).
_REPRESENTABILITY_EPSILON: float = 1e-6


def price_to_ticks(price: np.ndarray) -> np.ndarray:
    """Convert an array of USDT prices to an array of int64 tick counts.

    D-06-05's one named, deterministic rule:
    `round(price * PRICE_SCALE) // TICK_SIZE_SCALED` (06-RESEARCH.md Q2 --
    this form agrees with `round(price / 0.1)` and `round(price * 10)` on
    zero of 34,334,580 real prices; chosen because it matches
    `data/time_ns.py`'s existing fixed-point idiom rather than introducing
    float division into the hot path).

    Both assertions below run on the WHOLE array via `np.all`/boolean
    indexing to find the first offender, never a per-element Python loop --
    this function runs on real-day-sized arrays from Plan 06-04/06-06
    onward. Raises `ValueError` naming the first offending index and value,
    rather than silently truncating, when:

    - `price` is not representable at `PRICE_SCALE` within
      `_REPRESENTABILITY_EPSILON` (mirrors `features/kernel.py`'s
      `STATUS_QTY_NOT_REPRESENTABLE` pattern in spirit: an assertion, not a
      silent truncation), or
    - the resulting tick's round-trip (`ticks * TICK_SIZE_SCALED`) sits
      half a tick or more from the scaled price (D-06-05's own round-trip
      proof, asserted on every converted value, not assumed).
    """
    price = np.asarray(price, dtype=np.float64)
    scaled = np.round(price * PRICE_SCALE).astype(np.int64)

    reconstructed = scaled.astype(np.float64) / PRICE_SCALE
    representable = np.abs(reconstructed - price) < _REPRESENTABILITY_EPSILON
    if not np.all(representable):
        bad = int(np.flatnonzero(~representable)[0])
        raise ValueError(
            f"price_to_ticks: price[{bad}]={price[bad]!r} is not "
            f"representable at PRICE_SCALE={PRICE_SCALE} within "
            f"{_REPRESENTABILITY_EPSILON} (nearest grid value "
            f"{reconstructed[bad]!r})"
        )

    ticks = scaled // TICK_SIZE_SCALED

    round_trip_error = np.abs(ticks * TICK_SIZE_SCALED - scaled)
    within_half_a_tick = round_trip_error < (TICK_SIZE_SCALED // 2)
    if not np.all(within_half_a_tick):
        bad = int(np.flatnonzero(~within_half_a_tick)[0])
        raise ValueError(
            f"price_to_ticks: price[{bad}]={price[bad]!r} round-trips "
            f"{int(round_trip_error[bad])} scaled units away from its tick "
            f"({int(ticks[bad])}), at least half a tick "
            f"({TICK_SIZE_SCALED // 2}) away from the scaled input"
        )

    return ticks
