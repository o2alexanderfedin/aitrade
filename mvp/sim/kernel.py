"""The event-driven simulator's core: a SEQUENTIAL, integer-only,
flip-only state machine over decision rows (SIM-01, D-06-06/07/08).

THE DECISION RULE THIS KERNEL IMPLEMENTS IS `mvp/spec.md`'s "Decision rule
(Stage 2)" section, quoted here verbatim so a reader never has to
cross-reference it to check this code:

    When flat (position == 0): go long when pred_mid > best_ask + X_price;
    go short when pred_mid < best_bid - X_price; otherwise stay flat.
    When positioned: only the opposite-direction flip is allowed, using
    the same two comparisons -- a long position flips to short when
    pred_mid < best_bid - X_price; a short position flips to long when
    pred_mid > best_ask + X_price. No same-direction or risk-increasing
    order is ever placed.

`mvp.md`'s "Decision logic (explicit)" block is STRUCK and WRONG -- it
multiplied a predicted RETURN by the midprice and compared that price
CHANGE against a price LEVEL, producing wrong signs except by coincidence
(06-CONTEXT.md's `<canonical_refs>`). This kernel is checked against
`spec.md`'s rule only, never `mvp.md`'s struck one.

D-06-07's quantisation: `pred_mid` and `X_price` are converted to int64
ticks before either side of the comparison is evaluated, so the entire
decision is integer (T-06-07 -- `grep -n "float" sim/kernel.py`'s
acceptance check is confined to the conversion/quantisation lines below;
P&L, position and quantity never touch float). `pred` arrives as a RAW
PRICE (the same scale as `bid_price`/`ask_price`, NOT a bare tick count)
and is quantised to the NEAREST tick by
`(round(pred * PRICE_SCALE) + TICK_SIZE_SCALED // 2) // TICK_SIZE_SCALED`
-- deliberately NOT `sim.ticks.price_to_ticks`'s floor-then-round-trip-
proof rule. That proof is for bid/ask, which ARE tick-exact by
construction (06-RESEARCH.md Q2); a model's predicted price is not
expected to already be a tick multiple, and `price_to_ticks`'s round-trip
assertion would wrongly reject almost every realistic prediction (see
`sim/ticks.py`'s own module docstring and 06-02-SUMMARY.md's "half-tick
tie-break" note). `X_price` is derived from the integer `bid_ticks`/
`ask_ticks` the kernel is given, never from a float `mid`:
`x_ticks = (bid_ticks[i] + ask_ticks[i]) * x_bps // 20_000` (twice the
mid, one floor division) -- ONE formula, applied as `+x_ticks` on the long
side and `-x_ticks` on the short side, which is what makes the comparison
symmetric BY CONSTRUCTION (`test_quantised_threshold_is_symmetric_at_0_4_ticks`
proves it with worked arithmetic).

D-06-08/Q13: position size is recomputed FRESH at every entry/flip from
the CURRENT fill price (`ask_ticks[i]` for a long fill, `bid_ticks[i]` for
a short fill), never from a stale entry price -- 06-02-SUMMARY.md's
`sim.ticks.position_size_ticks` is the oracle this kernel's inline
arithmetic (numba cannot call that plain-Python function) is checked
against. A flip closes the existing leg and opens the opposite one in the
SAME decision row -- one trade-log row, not two. Position is always in
{-1, 0, +1}; from a non-zero position only the OPPOSITE-direction trigger
is even evaluated (the same-direction condition is never tested), which is
what makes "no same-direction or risk-increasing order" true by
construction, not a separate guard.

D-06-20: the $100 notional cap floors to zero lots above ~$100,000
(`sim/ticks.py:position_size_ticks`'s own dead zone). This kernel refuses
loudly (`STATUS_ZERO_LOT`), never trading a silent zero quantity.

PNL UNITS -- AN INTERNAL ACCOUNTING SCALE, NOT YET A REPORTED CURRENCY.
`realized_pnl_scaled` and `out_equity_scaled` accumulate
`(price_ticks_diff) * qty_scaled`, signed by position direction -- ticks
times QTY_SCALE-scaled BTC, with NO division back to a PRICE_SCALE-USD
figure. This is an exactly-reproducible integer unit, not the $-per-lot
number 06-RESEARCH.md Q5 reports; converting it to a comparable USD P&L
(dividing by the position's own lot count where it is exactly one lot, or
more generally by QTY_SCALE) is Plan 06-06's job, not this plan's -- see
this plan's own SUMMARY "What Was NOT Done".

NEVER RAISE FROM INSIDE THE `@njit` FUNCTION -- mirrors
`features/kernel.py`'s own rule verbatim, including that a bare `assert`
inside an `@njit` body compiles to a real raise and is therefore equally
forbidden (this is why a negative `pred` is a named status, not an
`assert s >= 0`). A negative status plus `state_i64[SLOT_ERROR_ROW]` is
the whole error channel; `run_sim_checked` is the only place a
`SimStatusError` is ever raised.

OUTPUT ARRAYS ARE THE CALLER'S OWN PREALLOCATION, never a view of an
input and never allocated inside the loop -- mirrors
`features/event_stream.py:event_arrays`/`features/reference.py:
new_outputs`'s discipline. `run_sim_checked` allocates them sized at
`etime.shape[0]` (Pattern 1, 06-RESEARCH.md Q3 -- `n_decision_rows` is the
only bound provable without assuming anything about the caller's
prediction quality; a zero-skill predictor was measured trading 1.88M
times on one real day, 858x the perfect-foresight count).

`side`, recorded in the trade log, is the executed order's direction (+1
buy/long, -1 sell/short). Under this flip-only rule it is ALWAYS equal to
`position_after` by construction -- there is no partial fill or
same-direction order that could make them differ. Both are still recorded
because D-06-13 names both as trade-log columns.

=============================== STATE CONTRACT ==============================

    state_i64 = new_state()

indexed by `STATE_I64_SLOTS`:

    position                int64 in {-1, 0, +1}
    entry_price_ticks       the open leg's fill price, in ticks (0 when flat)
    qty_scaled               the open leg's size, QTY_SCALE-scaled (0 when flat)
    realized_pnl_scaled      int64 accumulator, see "PNL UNITS" above
    fill_count               trade-log rows written so far (== "trades")
    flip_count               of those, how many were a flip (D-06-13)
    rows_in_market            rows where position != 0 after that row's decision
    error_row                 the offending row index of the last negative
                               status; -1 when no status has been returned

`cache=True` writes its `*.nbi`/`*.nbc` next to this file unless
`NUMBA_CACHE_DIR` is set; `tests/conftest.py` pins it for pytest, and any
ad-hoc script must export it itself (this plan's absolute rules).
"""

from __future__ import annotations

import numpy as np
from numba import njit

from data.time_ns import QTY_SCALE
from sim.ticks import (
    LOT_STEP_SCALED,
    MAX_NOTIONAL_SCALED,
    PRICE_SCALE,
    TICK_SIZE_SCALED,
)

__all__ = [
    "STATUS_OK",
    "STATUS_ARRAY_LENGTH_MISMATCH",
    "STATUS_NON_FINITE_PRED",
    "STATUS_ZERO_LOT",
    "STATUS_TRADE_LOG_OVERFLOW",
    "STATUS_NEGATIVE_PRED",
    "STATUS_MESSAGES",
    "STATE_I64_SLOTS",
    "SimStatusError",
    "new_state",
    "run_sim",
    "run_sim_checked",
]

#: Success. Every other status is NEGATIVE, mirroring `features/
#: reference.py`'s own vocabulary-only-status-codes pattern (this package
#: has no separate reference-first module yet, so the vocabulary lives
#: here).
STATUS_OK: int = 0
STATUS_ARRAY_LENGTH_MISMATCH: int = -1
STATUS_NON_FINITE_PRED: int = -2
STATUS_ZERO_LOT: int = -3
STATUS_TRADE_LOG_OVERFLOW: int = -4
STATUS_NEGATIVE_PRED: int = -5

STATUS_MESSAGES: dict[int, str] = {
    STATUS_OK: "ok",
    STATUS_ARRAY_LENGTH_MISMATCH: (
        "an input or output array is not the length the kernel was asked "
        "to walk; numba would have written out of bounds"
    ),
    STATUS_NON_FINITE_PRED: "pred[row] is NaN or +/-inf",
    STATUS_ZERO_LOT: (
        "the $100 notional cap floors to zero lots at this row's fill "
        "price (D-06-20's dead zone, ~$100,000 at the measured lot step) "
        "-- never a silent zero-quantity trade"
    ),
    STATUS_TRADE_LOG_OVERFLOW: (
        "the trade log is full; nothing was truncated, the row that would "
        "have overflowed it was not written"
    ),
    STATUS_NEGATIVE_PRED: (
        "pred[row] scales to a negative price; the nearest-tick formula "
        "rounds toward +inf for a negative scaled value and would "
        "quantise it wrongly rather than loudly"
    ),
}

# Slot indices. UPPER_CASE so `check_numba_globals` treats them as
# compile-time constants a future @njit reader may take.
SLOT_POSITION: int = 0
SLOT_ENTRY_PRICE_TICKS: int = 1
SLOT_QTY_SCALED: int = 2
SLOT_REALIZED_PNL_SCALED: int = 3
SLOT_FILL_COUNT: int = 4
SLOT_FLIP_COUNT: int = 5
SLOT_ROWS_IN_MARKET: int = 6
SLOT_ERROR_ROW: int = 7

#: Name -> index. Declaration order IS the array order.
STATE_I64_SLOTS: dict[str, int] = {
    "position": SLOT_POSITION,
    "entry_price_ticks": SLOT_ENTRY_PRICE_TICKS,
    "qty_scaled": SLOT_QTY_SCALED,
    "realized_pnl_scaled": SLOT_REALIZED_PNL_SCALED,
    "fill_count": SLOT_FILL_COUNT,
    "flip_count": SLOT_FLIP_COUNT,
    "rows_in_market": SLOT_ROWS_IN_MARKET,
    "error_row": SLOT_ERROR_ROW,
}


class SimStatusError(ValueError):
    """A negative status from `run_sim`, named and located."""


def new_state() -> np.ndarray:
    """A fresh run's carry-in state. Nothing is seeded from a previous run."""
    state_i64 = np.zeros(len(STATE_I64_SLOTS), dtype=np.int64)
    state_i64[SLOT_ERROR_ROW] = -1
    return state_i64


@njit(cache=True)
def run_sim(
    etime,
    bid_ticks,
    ask_ticks,
    pred,
    x_bps,
    max_notional_scaled,
    lot_step_scaled,
    fee_bps,
    latency_ns,
    out_trade_etime,
    out_trade_side,
    out_trade_price_ticks,
    out_trade_qty_scaled,
    out_trade_position_after,
    out_equity_scaled,
    state_i64,
):
    """Sequential scan over `n = etime.shape[0]` decision rows, writing one
    equity value per row and at most one trade-log row per row.

    Returns `STATUS_OK` (0) or a negative status, with the offending row
    index in `state_i64[SLOT_ERROR_ROW]`. `fee_bps`/`latency_ns` are
    accepted parameters but applied nowhere in this phase (D-06-16 -- the
    MVP simplification stays exactly zero; a future post-MVP change reads
    them at the fee/latency application site, not written here).
    """
    n = etime.shape[0]

    if (
        bid_ticks.shape[0] != n
        or ask_ticks.shape[0] != n
        or pred.shape[0] != n
        or out_equity_scaled.shape[0] != n
    ):
        state_i64[SLOT_ERROR_ROW] = -1
        return STATUS_ARRAY_LENGTH_MISMATCH

    capacity = out_trade_etime.shape[0]
    if (
        out_trade_side.shape[0] != capacity
        or out_trade_price_ticks.shape[0] != capacity
        or out_trade_qty_scaled.shape[0] != capacity
        or out_trade_position_after.shape[0] != capacity
    ):
        state_i64[SLOT_ERROR_ROW] = -1
        return STATUS_ARRAY_LENGTH_MISMATCH

    position = state_i64[SLOT_POSITION]
    entry_price_ticks = state_i64[SLOT_ENTRY_PRICE_TICKS]
    qty_scaled = state_i64[SLOT_QTY_SCALED]
    realized_pnl_scaled = state_i64[SLOT_REALIZED_PNL_SCALED]
    fill_count = state_i64[SLOT_FILL_COUNT]
    flip_count = state_i64[SLOT_FLIP_COUNT]
    rows_in_market = state_i64[SLOT_ROWS_IN_MARKET]

    status = STATUS_OK
    error_row = -1

    for i in range(n):
        p = pred[i]
        if not np.isfinite(p):
            status = STATUS_NON_FINITE_PRED
            error_row = i
            break

        # Nearest-tick quantisation (D-06-07) -- NOT a floor, and NOT
        # `sim.ticks.price_to_ticks`'s round-trip-proved rule (see the
        # module docstring: that proof is for bid/ask, not a prediction).
        s = np.int64(round(p * PRICE_SCALE))
        if s < 0:
            # Not a bare `assert` -- see the module docstring's
            # NEVER-RAISE-FROM-@njit rule. The formula below rounds
            # toward +inf for a negative scaled value, so a negative
            # prediction would quantise wrongly rather than loudly if
            # this check did not exist.
            status = STATUS_NEGATIVE_PRED
            error_row = i
            break
        pred_ticks = (s + TICK_SIZE_SCALED // 2) // TICK_SIZE_SCALED

        b = bid_ticks[i]
        a = ask_ticks[i]
        # ONE formula, applied identically as +x_ticks (long) / -x_ticks
        # (short) -- the symmetry this buys is proven in
        # test_quantised_threshold_is_symmetric_at_0_4_ticks.
        x_ticks = (b + a) * x_bps // 20_000

        long_trigger = pred_ticks > a + x_ticks
        short_trigger = pred_ticks < b - x_ticks

        triggered = False
        is_flip = False
        new_position = position
        fill_price_ticks = 0
        side = 0

        if position == 0:
            if long_trigger:
                triggered = True
                new_position = 1
                fill_price_ticks = a
                side = 1
            elif short_trigger:
                triggered = True
                new_position = -1
                fill_price_ticks = b
                side = -1
        elif position == 1:
            # Only the opposite-direction flip is ever evaluated -- the
            # same-direction condition (long_trigger) is never tested,
            # which is what makes "no same-direction or risk-increasing
            # order" true by construction (D-06-08).
            if short_trigger:
                triggered = True
                is_flip = True
                new_position = -1
                fill_price_ticks = b
                side = -1
        else:  # position == -1
            if long_trigger:
                triggered = True
                is_flip = True
                new_position = 1
                fill_price_ticks = a
                side = 1

        if triggered:
            # Fresh sizing at the CURRENT fill price (Q13) -- inline
            # mirror of `sim.ticks.position_size_ticks` (numba cannot call
            # that plain-Python function); `tests/sim/test_kernel.py`'s
            # equivalence sweep checks this arithmetic against it.
            price_scaled = fill_price_ticks * TICK_SIZE_SCALED
            qty_unrounded = (max_notional_scaled * QTY_SCALE) // price_scaled
            new_qty_scaled = (qty_unrounded // lot_step_scaled) * lot_step_scaled
            if new_qty_scaled == 0:
                status = STATUS_ZERO_LOT
                error_row = i
                break
            if fill_count >= capacity:
                # Never silently truncate -- refuse before writing.
                status = STATUS_TRADE_LOG_OVERFLOW
                error_row = i
                break

            if is_flip:
                # Realize P&L on the OLD leg, signed by the OLD position's
                # direction, BEFORE the state is overwritten below.
                realized_pnl_scaled += (
                    position * (fill_price_ticks - entry_price_ticks) * qty_scaled
                )

            out_trade_etime[fill_count] = etime[i]
            out_trade_side[fill_count] = side
            out_trade_price_ticks[fill_count] = fill_price_ticks
            out_trade_qty_scaled[fill_count] = new_qty_scaled
            out_trade_position_after[fill_count] = new_position
            fill_count += 1
            if is_flip:
                flip_count += 1

            position = new_position
            entry_price_ticks = fill_price_ticks
            qty_scaled = new_qty_scaled

        # Equity mark, EVERY row (D-06-13): the current position at
        # bid_ticks if long, ask_ticks if short, never a rounded mid
        # (Pattern 3).
        if position == 1:
            unrealized = (b - entry_price_ticks) * qty_scaled
        elif position == -1:
            unrealized = (entry_price_ticks - a) * qty_scaled
        else:
            unrealized = 0
        out_equity_scaled[i] = realized_pnl_scaled + unrealized

        if position != 0:
            rows_in_market += 1

    state_i64[SLOT_POSITION] = position
    state_i64[SLOT_ENTRY_PRICE_TICKS] = entry_price_ticks
    state_i64[SLOT_QTY_SCALED] = qty_scaled
    state_i64[SLOT_REALIZED_PNL_SCALED] = realized_pnl_scaled
    state_i64[SLOT_FILL_COUNT] = fill_count
    state_i64[SLOT_FLIP_COUNT] = flip_count
    state_i64[SLOT_ROWS_IN_MARKET] = rows_in_market
    if status != STATUS_OK:
        state_i64[SLOT_ERROR_ROW] = error_row

    return status


def _status_detail(
    status: int,
    state: np.ndarray,
    etime: np.ndarray,
    bid_ticks: np.ndarray,
    ask_ticks: np.ndarray,
    pred: np.ndarray,
) -> str:
    """The message `run_sim_checked` raises with: the status, its meaning,
    the offending row index, and that row's values -- mirrors
    `features/reference.py:_status_detail`'s shape."""
    row = int(state[SLOT_ERROR_ROW])
    detail = f"run_sim: status {status} ({STATUS_MESSAGES.get(status, 'unknown status')}) at row {row}"
    if 0 <= row < etime.shape[0]:
        detail = (
            f"{detail}: etime={int(etime[row])}, bid_ticks={int(bid_ticks[row])}, "
            f"ask_ticks={int(ask_ticks[row])}, pred={float(pred[row])!r}"
        )
    return detail


def run_sim_checked(
    etime: np.ndarray,
    bid_ticks: np.ndarray,
    ask_ticks: np.ndarray,
    pred: np.ndarray,
    *,
    x_bps: int = 0,
    max_notional_scaled: int = MAX_NOTIONAL_SCALED,
    lot_step_scaled: int = LOT_STEP_SCALED,
    fee_bps: int = 0,
    latency_ns: int = 0,
    state: np.ndarray | None = None,
) -> dict:
    """`run_sim` over bare numpy decision-row arrays, raising
    `SimStatusError` on a negative status. This is what everything outside
    this module calls.

    `fee_bps`/`latency_ns` default to 0 HERE (D-06-16), not on `run_sim`
    itself -- numba `@njit` does not reliably support keyword defaults
    across all call shapes. `state=None` starts a fresh run; passing a
    state back in resumes a sequential scan mid-flight.

    Returns `{"trade_log": {...}, "fill_count": int, "equity_scaled":
    ndarray, "counters": {...}}` -- Task 2 (`sim/outputs.py`) rewires this
    into a `SimResult` NamedTuple with the same field names; every trade
    log column here is allocated at `etime.shape[0]` (Pattern 1) and its
    tail past `fill_count` is uninitialised memory, exactly as `sim/
    outputs.py:new_trade_log`'s docstring will state -- callers must slice
    to `[:fill_count]`.
    """
    if etime.ndim != 1 or bid_ticks.ndim != 1 or ask_ticks.ndim != 1 or pred.ndim != 1:
        raise ValueError("run_sim_checked: etime/bid_ticks/ask_ticks/pred must be 1-D")
    if (
        etime.dtype != np.int64
        or bid_ticks.dtype != np.int64
        or ask_ticks.dtype != np.int64
    ):
        raise ValueError("run_sim_checked: etime/bid_ticks/ask_ticks must be int64")
    if pred.dtype != np.float64:
        raise ValueError("run_sim_checked: pred must be float64")

    if state is None:
        state = new_state()

    n = etime.shape[0]
    trade_log = {
        "etime": np.empty(n, dtype=np.int64),
        "side": np.empty(n, dtype=np.int8),
        "price_ticks": np.empty(n, dtype=np.int64),
        "qty_scaled": np.empty(n, dtype=np.int64),
        "position_after": np.empty(n, dtype=np.int8),
    }
    equity_scaled = np.empty(n, dtype=np.int64)

    status = run_sim(
        etime,
        bid_ticks,
        ask_ticks,
        pred,
        int(x_bps),
        int(max_notional_scaled),
        int(lot_step_scaled),
        int(fee_bps),
        int(latency_ns),
        trade_log["etime"],
        trade_log["side"],
        trade_log["price_ticks"],
        trade_log["qty_scaled"],
        trade_log["position_after"],
        equity_scaled,
        state,
    )
    if status != STATUS_OK:
        raise SimStatusError(
            _status_detail(status, state, etime, bid_ticks, ask_ticks, pred)
        )

    fill_count = int(state[SLOT_FILL_COUNT])
    counters = {
        "trades": fill_count,
        "flips": int(state[SLOT_FLIP_COUNT]),
        "rows_in_market": int(state[SLOT_ROWS_IN_MARKET]),
    }
    return {
        "trade_log": trade_log,
        "fill_count": fill_count,
        "equity_scaled": equity_scaled,
        "counters": counters,
    }
