"""D-06-05's tick constant and conversion rule, D-06-08's lot-step and
position-sizing arithmetic, and D-06-20's >$100,000 zero-lot dead-zone
refusal.

No polars or numba dependency here, on purpose: this module is importable
and testable in complete isolation, so Plan 06-03's `@njit` kernel and its
polars-boundary wrapper both import ONE already-tested source of these
constants instead of each defining its own copy that could silently drift
from the other's.

`PRICE_SCALE`, `TICK_SIZE_SCALED`, `LOT_STEP_SCALED` and
`MAX_NOTIONAL_SCALED` are QUANTITY/PRICE fixed-point scales, not
seconds-to-nanoseconds conversions. `tools.check_ms_to_ns_site`
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
    "INT64_MAX",
    "PRICE_SCALE",
    "TICK_SIZE_SCALED",
    "LOT_STEP_SCALED",
    "MAX_NOTIONAL_SCALED",
    "MAX_NOTIONAL_SCALED_INT64_BOUND",
    "X_BPS_DENOMINATOR",
    "X_BPS_SCALE",
    "X_TICKS_DENOMINATOR",
    "price_to_ticks",
    "position_size_ticks",
    "resolve_x_bps_scaled",
    "x_bps_scaled_int64_bound",
    "XThresholdError",
    "ZeroLotError",
    "NotionalOverflowError",
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

#: 0.001 BTC at `data.time_ns.QTY_SCALE` (D-06-08). Pinned by
#: `test_lot_step_matches_the_measured_venue_gcd` against `trade.qty`,
#: `bid_qty` and `ask_qty` on the same real partition -- all three gcd to
#: 100,000 of 100,000,000 units/BTC, agreeing exactly.
LOT_STEP_SCALED: int = 100_000

#: $100 USD at `PRICE_SCALE` -- the MVP default notional cap (D-06-08,
#: mvp.md). A caller may override it per call; this is the default only.
MAX_NOTIONAL_SCALED: int = 100 * PRICE_SCALE

#: WR-01 (06-REVIEW.md): the largest `max_notional_scaled` for which the
#: intermediate product `max_notional_scaled * QTY_SCALE` -- computed both
#: here and, inline, by `sim/kernel.py`'s `@njit` mirror -- does not
#: overflow a signed int64.
#:
#: Derivation: the product must satisfy
#: `max_notional_scaled * QTY_SCALE <= INT64_MAX`, i.e.
#: `max_notional_scaled <= INT64_MAX // QTY_SCALE`. At
#: `QTY_SCALE=100_000_000`: `9_223_372_036_854_775_807 // 100_000_000 =
#: 92_233_720_368` (`PRICE_SCALE`-scaled units) -- `92_233_720_368 /
#: PRICE_SCALE = $922.34` of notional cap.
#:
#: NOTE ON 06-REVIEW.md's OWN WR-01 TEXT: the review's prose says "$922
#: billion... 9.2 billion times today's $100 MVP default." That arithmetic
#: stopped at the scaled-units number (`92_233_720_368`) and reported it
#: directly as a dollar figure, without the second division by
#: `PRICE_SCALE` needed to leave fixed-point units. The corrected bound
#: above is ~9.22x today's $100 default, not ~9.2 billion times it --
#: reachable by a single caller-chosen `max_notional_scaled` far short of
#: "orders of magnitude", contrary to the review's "not reachable in
#: practice" framing. See `06-REVIEW-FIX.md` for this correction.
MAX_NOTIONAL_SCALED_INT64_BOUND: int = (2**63 - 1) // QTY_SCALE

#: Signed int64's ceiling, named once so the two overflow bounds in this module
#: derive from one spelling rather than two copies of `2**63 - 1`.
INT64_MAX: int = 2**63 - 1

#: The denominator of `spec.md`'s "crosses TOB by X bps" threshold, as a named
#: constant rather than the bare `20_000` that `sim/kernel.py` and
#: `sim/reference.py` each used to spell inline.
#:
#: TWENTY thousand, not ten: the threshold is derived from `bid_ticks +
#: ask_ticks`, which is TWICE the mid, because the kernel is given the two
#: integer book sides and never a float mid (D-06-07). The factor of two lives
#: in this denominator instead of in a division that would leave integer
#: arithmetic.
X_BPS_DENOMINATOR: int = 20_000

#: The unit `x_bps_scaled` counts in: one unit is `1 / X_BPS_SCALE` of a basis
#: point, i.e. 1e-4 bps.
#:
#: WHY THE BPS UNIT NEEDED SUBDIVIDING AT ALL. `spec.md` parameterises Stage 2
#: as "trade when predicted midprice crosses TOB by X bps (X swept)", and an
#: integer X made that sweep degenerate at BTC's price: the zero-look OOF
#: viability run measured ONE basis point at 74 to 79 ticks on the five cached
#: blocks, while the frozen predictor clears the touch by at most a few ticks.
#: `x_bps=1` therefore produced zero trigger rows and zero trades on every
#: block -- not a smaller trade set, an empty one -- leaving `x_bps=0` as the
#: only feasible value of a swept hyperparameter.
#:
#: 10_000 is chosen so ONE TICK of threshold is expressible with room to spare
#: at this venue and price: at `bid + ask = 1_540_001` (a one-tick spread
#: around $77,000) one tick is 130 units and a whole basis point is 770_000
#: units, so the sweep axis has ~77 reachable sub-tick steps per tick instead
#: of a single 77-tick jump. It is NOT a time scale and is deliberately outside
#: `tools.check_ms_to_ns_site`'s `{1e3, 1e6, 1e9}` family, exactly like
#: `PRICE_SCALE` and `data.time_ns.QTY_SCALE` already are.
X_BPS_SCALE: int = 10_000

#: `x_ticks = (bid_ticks + ask_ticks) * x_bps_scaled // X_TICKS_DENOMINATOR`,
#: the ONE formula both the `@njit` kernel and its pure-Python twin apply.
#:
#: The pre-subdivision spelling is recovered EXACTLY, not approximately: for
#: every non-negative integer `k`,
#: `k * X_BPS_SCALE // X_TICKS_DENOMINATOR == k // X_BPS_DENOMINATOR`, because
#: both sides are `floor(k / 20_000)`. That integer identity is what makes
#: `x_bps=1` and `x_bps_scaled=X_BPS_SCALE` the same threshold, and what makes
#: `x_bps=0` byte-identical to every previously committed simulation.
X_TICKS_DENOMINATOR: int = X_BPS_DENOMINATOR * X_BPS_SCALE

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


class XThresholdError(ValueError):
    """The X threshold was given in a form that cannot be honoured exactly.

    A named subclass rather than a bare `ValueError`, for
    `sim.kernel.SimStatusError`'s own stated reason: a caller writing
    `pytest.raises(ValueError)` to mean "the threshold was refused" would
    otherwise also be satisfied by "the kernel refused a dtype".
    """


def resolve_x_bps_scaled(
    x_bps: int | float | None = None, x_bps_scaled: int | float | None = None
) -> int:
    """The one threshold both simulator implementations actually use, in units
    of `1 / X_BPS_SCALE` basis points.

    EXACTLY ONE of the two knobs may be given; neither means a zero threshold,
    which is every committed measurement in phases 6 and 7.

    - `x_bps` is WHOLE basis points and is multiplied by `X_BPS_SCALE`.
    - `x_bps_scaled` is already in the fine unit and is returned as given.

    A FRACTIONAL `x_bps` IS REFUSED, NOT ROUNDED. `run_sim_checked` used to
    coerce with `int(x_bps)`, so `x_bps=0.5` silently became `x_bps=0` -- and
    zero is the one value known to trade, so the run looked healthy while
    simulating a threshold nobody asked for. Rounding it here would repair the
    symptom and keep the defect; the refusal names `x_bps_scaled`, which CAN
    express half a basis point (as 5_000), so the caller has somewhere to go.
    An exactly-integral float (`2.0`) and a numpy integer are both accepted --
    the refusal is about losing a fraction, not about the Python type.

    A NEGATIVE THRESHOLD IS REFUSED. It is not a smaller X, it is a different
    rule: `//` floors toward minus infinity, so a negative value yields a
    negative `x_ticks` and the kernel fires on predictions that have not
    reached the touch at all. Measured at HEAD before this refusal existed:
    `x_bps=-1` traded on all 4,000 rows of a walk where `x_bps=0` traded 1,531
    times.
    """
    if x_bps is not None and x_bps_scaled is not None:
        raise XThresholdError(
            "resolve_x_bps_scaled: pass exactly one of x_bps (whole basis "
            f"points) or x_bps_scaled (units of 1/{X_BPS_SCALE} bps); got "
            f"x_bps={x_bps!r} and x_bps_scaled={x_bps_scaled!r}. Two spellings "
            "of one threshold cannot be reconciled here without guessing which "
            "the caller meant"
        )
    if x_bps is None and x_bps_scaled is None:
        return 0
    if x_bps is not None:
        whole = _as_exact_integer(
            x_bps,
            "x_bps",
            f"whole basis points only -- use x_bps_scaled for a finer "
            f"threshold (one unit is 1/{X_BPS_SCALE} bps, so {X_BPS_SCALE // 2} "
            "is half a basis point)",
        )
        scaled = whole * X_BPS_SCALE
    else:
        scaled = _as_exact_integer(
            x_bps_scaled,
            "x_bps_scaled",
            f"already counted in units of 1/{X_BPS_SCALE} bps, so a fraction "
            "of one unit has no meaning",
        )
    if scaled < 0:
        raise XThresholdError(
            f"resolve_x_bps_scaled: negative threshold ({scaled} at "
            f"1/{X_BPS_SCALE} bps) -- a negative X is not a smaller threshold "
            "but a different rule: the floor division yields a negative "
            "x_ticks and the decision fires on a prediction that has not "
            "reached the touch, i.e. it trades inside the spread"
        )
    return int(scaled)


def _as_exact_integer(value: int | float, name: str, remedy: str) -> int:
    """`value` as a Python `int`, refusing anything that would lose a fraction.

    Accepts `int`, numpy integers and exactly-integral floats; refuses a
    fractional float and a non-numeric value, naming the remedy.
    """
    try:
        as_int = int(value)
    except (TypeError, ValueError) as error:
        raise XThresholdError(
            f"resolve_x_bps_scaled: {name}={value!r} is not a number -- {remedy}"
        ) from error
    if as_int != value:
        raise XThresholdError(
            f"resolve_x_bps_scaled: {name}={value!r} is not an integer and "
            f"would be TRUNCATED to {as_int} -- {remedy}"
        )
    return as_int


def x_bps_scaled_int64_bound(book_sum: int) -> int:
    """The largest `x_bps_scaled` for which `(bid_ticks + ask_ticks) *
    x_bps_scaled` cannot overflow int64, given the largest `bid_ticks +
    ask_ticks` a frame carries.

    WHY THIS IS CHECKED AND NOT ASSUMED, in `MAX_NOTIONAL_SCALED_INT64_BOUND`'s
    register: that product is int64 inside the `@njit` body, where an overflow
    WRAPS rather than raising, and a wrapped product can land NEGATIVE -- which
    is `resolve_x_bps_scaled`'s refused negative-threshold rule arrived at
    silently, on a run that reports trades and a P&L. Unreachable by a real
    sweep (it takes an X of order 1e8 basis points) and cheap to refuse.
    """
    if book_sum <= 0:
        raise XThresholdError(
            f"x_bps_scaled_int64_bound: book_sum={book_sum} is not positive, so "
            "it is not a sum of two tick counts"
        )
    return INT64_MAX // int(book_sum)


class NotionalOverflowError(ValueError):
    """WR-01: `max_notional_scaled` is large enough that `max_notional_scaled
    * QTY_SCALE` would overflow int64 -- refused loudly, never silently
    wrapped. Mirrors `sim/kernel.py`'s `STATUS_NOTIONAL_OVERFLOW`, the
    `@njit` kernel's equivalent guard on the same bound."""

    def __init__(self, max_notional_scaled: int) -> None:
        self.max_notional_scaled = max_notional_scaled
        super().__init__(
            f"position_size_ticks: max_notional_scaled={max_notional_scaled} "
            f"exceeds MAX_NOTIONAL_SCALED_INT64_BOUND="
            f"{MAX_NOTIONAL_SCALED_INT64_BOUND} -- max_notional_scaled * "
            "QTY_SCALE would overflow int64 (D-06-08's caller-overridable "
            "notional cap; see MAX_NOTIONAL_SCALED_INT64_BOUND's own "
            "docstring for the derivation)"
        )


class ZeroLotError(ValueError):
    """D-06-20: the notional cap floors to zero lots at this price.

    Raised, never silently returned as `0` -- a run that yields zero lots
    is indistinguishable from a strategy that found no signal.
    """

    def __init__(self, price_usd: float, max_notional_usd: float) -> None:
        self.price_usd = price_usd
        self.max_notional_usd = max_notional_usd
        super().__init__(
            f"position_size_ticks: ${max_notional_usd} notional cap floors "
            f"to zero lots at price ${price_usd} (D-06-20 dead zone -- at "
            "the MVP defaults this floor sits at $100,000; a caller-"
            "overridden max_notional_scaled/lot_step_scaled moves it)"
        )


def position_size_ticks(
    price_ticks: int,
    *,
    max_notional_scaled: int = MAX_NOTIONAL_SCALED,
    lot_step_scaled: int = LOT_STEP_SCALED,
) -> int:
    """Quantity, in `QTY_SCALE`-scaled BTC units, for one entry/flip at
    `price_ticks`.

    D-06-08: `floor(max_notional / price / lot_step) * lot_step`, computed
    FRESH from the given price on every call -- never from a position's
    original entry price (D-06-08's own wording, "at entry price",
    singular; 06-RESEARCH.md Q13's resolution). Raises `ZeroLotError`
    naming the price and the cap when the floor lands on zero lots
    (D-06-20), rather than returning `0` silently.
    """
    # WR-01 (06-REVIEW.md): re-derived and closed 2026-09-24 --
    # `MAX_NOTIONAL_SCALED_INT64_BOUND`'s own docstring has the exact
    # arithmetic. This module's plain Python ints would not overflow on
    # their own, but `sim/kernel.py`'s `@njit` mirror uses int64 and must
    # refuse the identical bound -- checked here so both the pure-Python
    # oracle (this function, which `sim/reference.py` also calls) and the
    # kernel (via its own inline `STATUS_NOTIONAL_OVERFLOW` check) agree.
    if max_notional_scaled > MAX_NOTIONAL_SCALED_INT64_BOUND:
        raise NotionalOverflowError(max_notional_scaled)
    price_scaled = price_ticks * TICK_SIZE_SCALED
    qty_scaled_unrounded = (max_notional_scaled * QTY_SCALE) // price_scaled
    qty_scaled = (qty_scaled_unrounded // lot_step_scaled) * lot_step_scaled
    if qty_scaled == 0:
        raise ZeroLotError(
            price_usd=price_scaled / PRICE_SCALE,
            max_notional_usd=max_notional_scaled / PRICE_SCALE,
        )
    return qty_scaled
