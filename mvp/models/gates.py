"""The two gates of D-07-18, and the perfect-foresight ceiling as CODE
rather than as a number somebody remembers (D-07-19, D-07-32, D-07-35).

THE GUARD'S REFERENCE IS NO LONGER THE LABEL-HORIZON CEILING (changed
2026-09-29, after the zero-look OOF viability run measured what that quantity
actually is). `perfect_foresight_ceiling` feeds the realised future mid AT ONE
LABEL'S HORIZON into a rule that fires only when the predicted price clears
the touch by a FULL tick -- and at a one-tick spread the future mid sits a
half tick off the grid, so a horizon-h perfect predictor fires only on moves
of about 1.5 ticks within h. The P&L it reports therefore FALLS as h grows.
Measured on `oof_block_3`, same rows, same rule, only the label changed:

    perfect foresight at      trades   closed_pnl_ticks
    ret_1s_mid                11,394          1,579,033
    ret_10s_mid                6,643            702,556   <- was the guard's
    ret_1min_mid               3,027            285,144
    ret_10min_mid                941             86,019
    next DECISION ROW         14,972          2,580,239   <- is the guard's
    mid's total variation          --          2,763,605   (the theorem)

A threshold that moves 30x with a choice of label is not a bound on anything,
and the frozen winner earned 2.55x it while staying below 81.7% of the mid's
total variation on every one of the five blocks. So `guard_against_ceiling`
now takes `mid_total_variation`, and the label-horizon figure is still
measured and still reported, as a DIAGNOSTIC under its own unchanged keys.

WHY TOTAL VARIATION AND NOT DECISION-ROW PERFECT FORESIGHT, which is tighter
on real data and which this module also computes. Decision-row perfect
foresight is NOT A BOUND -- measured, on this repo's own fixture, and that
measurement is why the guard is not referenced to it. The fixture's price path
steps by exactly 0 or +/-1 tick per row at a constant one-tick spread, so the
next row's mid is at most one tick away while the kernel needs the predicted
price to clear the touch by a FULL tick from a half-tick mid, i.e. 1.5 ticks.
Decision-row perfect foresight therefore earns EXACTLY ZERO there, while the
10-second-horizon perfect predictor earns 311 ticks and the fitted model earns
50. A guard referenced to it would abort on every result on any segment whose
adjacent-row mid moves are sub-tick. On the real blocks it fires on 0.12% of
rows -- it lives entirely in a thin tail of multi-tick jumps, and a tripwire
must not depend on that tail existing.

    `mid_total_variation` IS a theorem for a one-lot flip-only policy. Every
    closed leg earns `bid_exit - ask_entry` (long) or `bid_entry - ask_exit`
    (short), each of which is the mid-to-mid move MINUS half a spread at each
    end; the legs are disjoint in time; so the total cannot exceed the sum of
    |mid moves| over the path. With a spread of at least one tick it is
    STRICTLY below, by at least one tick per leg.

THE PRICE OF THE THEOREM, STATED PLAINLY: because it is strictly unattainable,
NO PREDICTION FED THROUGH THIS RULE CAN MAKE THE GUARD FIRE. The guard is
therefore a check on genuinely impossible results -- a P&L earned on a
different row set than the bound was measured over, a fill counted at more
than one lot, a trade log walked past `fill_count` -- and not the leak
detector it was described as. The leak signal survives as a REPORTED ratio:
`decision_row_perfect_foresight` is what a real prediction can actually
approach (the frozen winner reached 87.5% of it on `oof_block_3`), so a model
that passes it is the thing to investigate, and the slice logs that fraction
rather than raising on it.

WHY THE CEILING LIVES HERE. Phase 6 measured 2,192 trades / 294,554 ticks /
$29.4554 on the real 2026-09-13 partition and committed only the OUTPUT, to
`.planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json`;
the script that produced it was ad hoc and is gone. A bound that exists only
in a transcript cannot be re-run against a new segment, and the bound is
PER SEGMENT: 2026-09-13's $29.4554 is not this phase's ceiling (correction
C4). So the measurement is a function with a test, and every plan that needs
a ceiling computes one instead of quoting one.

THE CEILING IS A STRONG SANITY BOUND, NOT A THEOREM. Perfect foresight fed to
the flip-only rule at `x_bps=0` is ONE PARTICULAR POLICY, not the P&L maximum
over that price path -- a coarser policy that declined some one-tick
crossings could in principle earn more. A reported P&L at or above it is
therefore overwhelmingly a bug (a prediction table misaligned with the
decision rows, or a label leaking into a feature), which is why
`guard_against_ceiling` raises and says INVESTIGATE rather than "impossible".

THE TICK-TO-USD CONVERSION, AS TWO EXPLICITLY LABELLED NUMBERS AND ONE
UNAMBIGUOUS EXPRESSION -- never as an algebraic identity, because Phase 6
stated it wrong twice, in both directions:

    1 tick              = TICK_SIZE_SCALED / PRICE_SCALE = $0.10
    lot actually traded = LOT_STEP_SCALED  / QTY_SCALE   = 0.001 BTC

    per 1 BTC convention       ticks * 0.10               (day-13: $29,455.40)
    realised at the traded lot ticks * 0.10 * 0.001       (day-13: $29.4554)
    least ambiguous            equity_scaled[-1] * TICK_SIZE_SCALED
                                 / PRICE_SCALE / QTY_SCALE

The factor between the two conventions is exactly 1000 -- exactly, as the
integer ratio `QTY_SCALE // LOT_STEP_SCALED`, which is what
`ticks_to_usd_at_traded_lot` divides by. The two FLOATS need not be exactly
1000 apart, because neither $0.10 nor 0.001 is binary-exact; the integer
identity is the one that is exact and the one a test should assert. Both
figures are reported, labelled, every time. Note that the least-ambiguous
expression above includes UNREALISED P&L at the last row (measured on the
approved `val` window: 1,120,460 closed ticks against 1,120,530 closed plus
unrealised), so it is reported under its own name and is not the same number
as `closed_pnl_ticks`.

`closed_pnl_ticks` is the Phase 6 convention: the trade-log tick-only walk
`sum over i>=1 of side[i-1] * (price_ticks[i] - price_ticks[i-1])`, and
explicitly NOT the kernel's `realized_pnl_scaled` accumulator, which carries
the QTY_SCALE-scaled quantity as well.

REFERENCE VALUES, cited from STATE.md and NOT from the Phase 6 evidence JSON
(D-07-24: that file reports 2,212 / 293,844, which are PRE-FIX numbers
superseded by 06-07's symmetric-quantisation fix, and it says so in its own
`SUPERSEDED_BY` key):

- 2026-09-13, v2 partition: 2,192 trades / 294,554 ticks / $29.4554.
- The approved `val` window (days 17-18, 16,294,059 admitted rows):
  9,946 trades / 1,120,460 closed ticks / $112.0460 (correction C4).

Neither is asserted by any test in this repo: both would need the real lake,
and reading it costs an irreversible validation look on every commit
(D-07-34). They are here so a future measurement has something to be
surprised by.

NO LAKE, NO MLFLOW, NO REGISTRY. `perfect_foresight_ceiling` takes the
ADMITTED FRAME the caller already has -- the exact frame the simulator will
walk, after admission and errata, in its own row order. Phase 6 read
partitions directly and so never confronted admission; a ceiling measured
over a different row set than the model's P&L is not a bound on it. And
under D-07-05 the ceiling, the prediction and the simulation all read one
cached frame written by ONE look: a ceiling that called `materialize` again
would spend a second look for a number that is a property of the data.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from data.time_ns import QTY_SCALE
from models.conversion import neutral_fill_null_predictions
from sim.arrays import sim_arrays
from sim.kernel import run_sim_checked
from sim.outputs import SimResult
from sim.ticks import LOT_STEP_SCALED, PRICE_SCALE, TICK_SIZE_SCALED
from spec.catalogue import get_label

__all__ = [
    "CEILING_REFERENCE_APPROVED_VAL",
    "CEILING_REFERENCE_2026_09_13",
    "MID_COLUMN",
    "TARGET_COLUMN",
    "USD_PER_BTC_TO_TRADED_LOT_DIVISOR",
    "CeilingExceededError",
    "closed_pnl_ticks",
    "decision_row_perfect_foresight",
    "gate_forecast",
    "gate_monetization",
    "guard_against_ceiling",
    "mid_total_variation",
    "perfect_foresight_ceiling",
    "ticks_to_usd_at_traded_lot",
    "ticks_to_usd_per_btc",
]

#: FCST-01's primary target, by a literal catalogue lookup rather than a
#: string -- `models/regression.py`'s own pattern, but reached without
#: importing that module, which would drag sklearn into a file the simulator
#: side calls.
TARGET_COLUMN: str = get_label("ret_10s_mid").name

#: The bookkeeping column the return-to-price conversion reads. Never an
#: estimator input (D-07-09); see `models/conversion.py`'s docstring for why
#: the same column is forbidden in the design matrix and required here.
MID_COLUMN: str = "mid"

#: Exactly 1000, as integers: `QTY_SCALE // LOT_STEP_SCALED`. This is the
#: factor between the two USD conventions, and it is exact here in a way it
#: is not between the two floats.
USD_PER_BTC_TO_TRADED_LOT_DIVISOR: int = QTY_SCALE // LOT_STEP_SCALED

#: Phase 6's real-day measurement, for orientation only -- see the module
#: docstring on why it is cited from STATE.md and not from the evidence JSON.
CEILING_REFERENCE_2026_09_13: dict[str, float] = {
    "trades": 2192,
    "closed_pnl_ticks": 294_554,
    "closed_pnl_usd_at_traded_lot": 29.4554,
}

#: The approved `val` window (days 17-18), re-measured in 07-RESEARCH.md Q7
#: through the procedure this module implements (correction C4).
CEILING_REFERENCE_APPROVED_VAL: dict[str, float] = {
    "admitted_rows": 16_294_059,
    "trades": 9946,
    "closed_pnl_ticks": 1_120_460,
    "closed_pnl_usd_at_traded_lot": 112.0460,
}


class CeilingExceededError(ValueError):
    """A reported P&L at or above the measured perfect-foresight ceiling.

    A named subclass for `models.frozen.FrozenPredictorError`'s stated
    reason: `pytest.raises(ValueError)` meaning "the ceiling guard bit" would
    otherwise also be satisfied by "the kernel refused a dtype". Still a
    `ValueError`, so the research note's wording holds.
    """


def ticks_to_usd_per_btc(ticks: int) -> float:
    """`ticks * $0.10` -- the P&L a 1 BTC position would have realised.

    NOT the money this project makes. It is reported because 06-RESEARCH.md
    printed this figure under a "per one-lot" label that is wrong, and a
    reader who has seen that label needs both numbers side by side to notice
    the 1000x.
    """
    return int(ticks) * TICK_SIZE_SCALED / PRICE_SCALE


def ticks_to_usd_at_traded_lot(ticks: int) -> float:
    """`ticks * $0.10 * 0.001` -- the money actually realised at the traded
    0.001 BTC lot, and the figure Phase 9's "Net P&L > 0" must use.

    Divided by the exact integer `USD_PER_BTC_TO_TRADED_LOT_DIVISOR` rather
    than multiplied by the literal `0.001`, which is not binary-exact.
    """
    return ticks_to_usd_per_btc(ticks) / USD_PER_BTC_TO_TRADED_LOT_DIVISOR


def closed_pnl_ticks(result: SimResult) -> int:
    """The trade-log tick-only closed-P&L walk, in the Phase 6 convention:
    `sum over i>=1 of side[i-1] * (price_ticks[i] - price_ticks[i-1])`.

    EVERY COLUMN IS SLICED TO `[:fill_count]` FIRST. `new_trade_log(n)`
    preallocates with `np.empty`, so the tail past `fill_count` is
    uninitialised memory and a walk over the full array sums garbage without
    failing.

    Under the flip-only rule `side` and `position_after` are equal on every
    row by construction (`sim/kernel.py`'s own docstring). That is CHECKED
    here rather than assumed, and the walk is computed both ways: if a future
    rule ever introduces a partial fill or a same-direction order, the two
    stop agreeing and this refuses instead of silently reporting the wrong
    one of the two.
    """
    k = int(result.fill_count)
    side = result.trade_log["side"][:k].astype(np.int64)
    position_after = result.trade_log["position_after"][:k].astype(np.int64)
    price_ticks = result.trade_log["price_ticks"][:k].astype(np.int64)
    if not np.array_equal(side, position_after):
        raise ValueError(
            "closed_pnl_ticks: the trade log's `side` and `position_after` "
            "disagree, which the flip-only rule makes impossible -- this walk "
            "signs each closed leg by the PREVIOUS row's direction and the two "
            "columns are no longer interchangeable, so the convention must be "
            "restated before a P&L is reported"
        )
    if k < 2:
        return 0
    return int((side[:-1] * np.diff(price_ticks)).sum())


def _label_returns(frame: pl.DataFrame, label_column: str) -> tuple[np.ndarray, int]:
    """The label column as float64 with its nulls surfaced as NaN, plus the
    null count -- the four-step accounting 07-06 settled on.

    Count the nulls, `fill_null(nan)` EXPLICITLY, assert none remain, then
    cross-check on the numpy side that every null resurfaced as a non-finite
    value. That last check is the one with teeth: a null that arrives as a
    NUMBER is an invented observation, and it would become a fabricated
    trade in the ceiling.
    """
    if label_column not in frame.columns:
        raise ValueError(
            f"perfect_foresight_ceiling: frame has no {label_column!r} column -- "
            "the perfect predictor IS the label, so a frame without it cannot "
            "produce a ceiling"
        )
    column = frame[label_column]
    if column.dtype != pl.Float64:
        raise ValueError(
            f"perfect_foresight_ceiling: {label_column!r} has dtype "
            f"{column.dtype}, must be Float64"
        )
    null_count = int(column.null_count())
    filled = column.fill_null(float("nan"))
    if int(filled.null_count()) != 0:
        raise ValueError(
            f"perfect_foresight_ceiling: {label_column!r} still holds nulls "
            "after fill_null"
        )
    values = filled.to_numpy()
    non_finite = int((~np.isfinite(values)).sum())
    if non_finite != null_count:
        raise ValueError(
            f"perfect_foresight_ceiling: {label_column!r} had {null_count} "
            f"nulls but {non_finite} non-finite values after the polars-to-numpy "
            "boundary -- a null that arrives as a NUMBER is an invented "
            "observation, and here it would become a fabricated trade"
        )
    return np.ascontiguousarray(values, dtype=np.float64), null_count


def _require_simulatable(frame: pl.DataFrame, caller: str) -> np.ndarray:
    """The two refusals every perfect-predictor measurement in this module
    shares, plus the frame's `mid` as a contiguous float64 array.

    A zero-row frame is refused rather than measured: a bound of zero over zero
    rows makes `guard_against_ceiling` fire on every reported P&L, and a bound
    that rejects everything is worse than no bound.
    """
    if frame.height == 0:
        raise ValueError(
            f"{caller}: the frame has no rows -- a bound of zero over zero rows "
            "would make guard_against_ceiling fire on every reported P&L"
        )
    if MID_COLUMN not in frame.columns:
        raise ValueError(
            f"{caller}: frame has no {MID_COLUMN!r} column, so a predicted "
            "RETURN cannot be converted to the price the kernel wants"
        )
    return np.ascontiguousarray(frame[MID_COLUMN].to_numpy(), dtype=np.float64)


def _simulate_at_one_lot(
    frame: pl.DataFrame, pred_price: np.ndarray, caller: str
) -> tuple[SimResult, int]:
    """`run_sim_checked` at a zero threshold over the frame's own decision
    rows, refusing a trade log whose fills are not all exactly one lot step.

    The "realised at the traded lot" figure every caller reports presumes one
    lot; a two-lot fill would make that label quietly wrong. All fills were
    exactly one lot on both real candidate `val` windows, measured.
    """
    arrays = sim_arrays(frame)
    result = run_sim_checked(
        arrays["etime"],
        arrays["bid_ticks"],
        arrays["ask_ticks"],
        pred_price,
        x_bps=0,
    )
    k = int(result.fill_count)
    quantities = result.trade_log["qty_scaled"][:k]
    if k and not bool(np.all(quantities == LOT_STEP_SCALED)):
        offending = int(np.flatnonzero(quantities != LOT_STEP_SCALED)[0])
        raise ValueError(
            f"{caller}: fill {offending} traded "
            f"{int(quantities[offending])} at QTY_SCALE, not the one "
            f"{LOT_STEP_SCALED} lot step every fill is assumed to be -- the "
            "'realised at the traded lot' figure below multiplies by one lot "
            "and would be wrong by that fill's size"
        )
    return result, closed_pnl_ticks(result)


def _pnl_report(frame: pl.DataFrame, result: SimResult, ticks: int) -> dict[str, Any]:
    """The labelled numbers one perfect-predictor simulation produced.

    ONE reporting convention for all of them, so the label-horizon ceiling and
    the decision-row bound can never differ by how they were summarised -- the
    same reason `models.conversion` is one function and not two call sites.
    `closed_plus_unrealised_usd_at_traded_lot` INCLUDES the open leg's mark at
    the last row and is therefore a different number from the closed figure
    (measured on the approved `val` window: 1,120,460 closed against 1,120,530
    closed plus unrealised).
    """
    return {
        "rows_walked": int(frame.height),
        "trades": int(result.counters["trades"]),
        "flips": int(result.counters["flips"]),
        "rows_in_market": int(result.counters["rows_in_market"]),
        "closed_pnl_ticks": int(ticks),
        "closed_pnl_usd_per_btc": ticks_to_usd_per_btc(ticks),
        "closed_pnl_usd_at_traded_lot": ticks_to_usd_at_traded_lot(ticks),
        "closed_plus_unrealised_usd_at_traded_lot": (
            int(result.equity_scaled[-1]) * TICK_SIZE_SCALED / PRICE_SCALE / QTY_SCALE
        ),
    }


def mid_total_variation(frame: pl.DataFrame) -> dict[str, Any]:
    """THE GUARD'S REFERENCE, and the only quantity here that is a theorem: no
    one-lot flip-only policy can extract more ticks than the midprice
    travelled, and the spread is paid on top.

    Every closed leg earns `bid_exit - ask_entry` (long) or
    `bid_entry - ask_exit` (short) in ticks, each of which is the mid-to-mid
    move minus half a spread at each end; the legs are disjoint in time; so the
    sum cannot exceed the sum of |mid moves| over the whole path, and with a
    spread of at least one tick it is STRICTLY below it, by at least one tick
    per leg. The module docstring states what that unattainability costs: no
    prediction can make the guard fire, so the guard checks for impossible
    results and the leak signal is a reported ratio against
    `decision_row_perfect_foresight` instead.

    COMPUTED IN EXACT INTEGER HALF-TICKS, NEVER AS A FLOAT SUM. The mid of a
    one-tick spread is a half tick, so `bid_ticks + ask_ticks` -- twice the mid
    -- is the integer the differences are taken on, exactly as
    `sim/kernel.py` derives its own threshold from the two book sides rather
    than from a float mid. A float accumulation over sixteen million rows would
    make the reported bound depend on summation order.

    Returns `total_variation_half_ticks` (the exact int64), its halved
    `total_variation_ticks`, `bound_ticks`, the two USD conventions at that
    many ticks, and `rows_walked`.

    `bound_ticks` IS WHAT THE GUARD TAKES, and it is `half_ticks // 2` -- the
    FLOOR, never a float. `guard_against_ceiling` compares integers, and an odd
    half-tick count makes the true variation a half tick above this; flooring
    makes the bound half a tick TIGHTER than the theorem, which can only make
    the guard fire earlier and never later. Passing the float would put a
    binary-inexact value on one side of a `>=` in the one comparison that
    decides whether a run halts.

    A ZERO-VARIATION FRAME IS REFUSED. A perfectly flat path admits no P&L at
    all, so a bound of zero would make the guard raise on any result including
    `0` -- the same vacuity `_require_simulatable` refuses a zero-row frame for.
    """
    _require_simulatable(frame, "mid_total_variation")
    arrays = sim_arrays(frame)
    book_sum = arrays["bid_ticks"] + arrays["ask_ticks"]
    half_ticks = int(np.abs(np.diff(book_sum)).sum())
    if half_ticks == 0:
        raise ValueError(
            f"mid_total_variation: the midprice never moved across all "
            f"{frame.height} rows, so the total variation is 0 -- a bound of "
            "zero makes guard_against_ceiling raise on every reported P&L, "
            "including a P&L of exactly zero"
        )
    ticks = half_ticks / 2.0
    return {
        "rows_walked": int(frame.height),
        "total_variation_half_ticks": half_ticks,
        "total_variation_ticks": ticks,
        "bound_ticks": half_ticks // 2,
        "total_variation_usd_per_btc": ticks * TICK_SIZE_SCALED / PRICE_SCALE,
        "total_variation_usd_at_traded_lot": (
            ticks * TICK_SIZE_SCALED / PRICE_SCALE / USD_PER_BTC_TO_TRADED_LOT_DIVISOR
        ),
    }


def decision_row_perfect_foresight(frame: pl.DataFrame) -> dict[str, Any]:
    """THE REPORTED LEAK SIGNAL, never the guard's reference: what the flip-only
    rule earns when the prediction is the NEXT DECISION ROW'S realised mid --
    perfect foresight at the finest resolution this data has.

    WHAT IT IS FOR. Unlike the theorem, this is a number a real prediction can
    approach: the frozen winner reached 87.5% of it on `oof_block_3` and 64% to
    91% across the five blocks. So the fraction of it a model reports is the
    quantity worth looking at when asking whether a label leaked, and the slice
    LOGS that fraction. It is strictly better than the label-horizon ceiling for
    that job, because it is a property of the price path and the rule alone
    rather than of a modelling choice that moves it 30x.

    IT IS NOT A BOUND AND MUST NOT BE GIVEN TO THE GUARD. Measured on this
    repo's own fixture: zero ticks, while the 10-second-horizon perfect
    predictor earns 311 on the same rows and the fitted model earns 50. The
    kernel needs 1.5 ticks of movement from a half-tick mid and the fixture's
    path steps one tick at a time, so the finest horizon sees nothing. On the
    real blocks it fires on 0.12% of rows -- a thin tail of multi-tick jumps.
    Neither is it a bound in theory: a predictor that LIED about the next mid
    could steer the flip sequence into a more profitable path, declining a small
    adverse flip to hold for a larger move.

    THE PREDICTION IS BUILT AS A RETURN AND CONVERTED BY THE ONE SITE
    (D-07-33), not written as a price: `next_mid / mid - 1` through
    `models.conversion.neutral_fill_null_predictions`, so this bound and the
    P&L it bounds can never differ by a conversion. The float round trip
    (`mid * (1 + (next_mid / mid - 1))`) is not the identity to the last bit,
    and that is deliberate -- it is the exact construction the zero-look OOF
    run measured 2,580,239 ticks with on `oof_block_3`, and feeding `next_mid`
    straight in as a price would both break D-07-33 and silently change the
    number.

    THE LAST ROW HAS NO NEXT ROW and is held flat (`next_mid = mid`, a zero
    return, the measured-neutral value that triggers neither side) rather than
    dropped: dropping it would measure the bound over a different row set than
    the P&L it bounds, which is the exact defect D-07-05's shared cached frame
    exists to prevent.
    """
    caller = "decision_row_perfect_foresight"
    mid = _require_simulatable(frame, caller)
    next_mid = np.empty_like(mid)
    next_mid[:-1] = mid[1:]
    next_mid[-1] = mid[-1]
    pred_return = np.ascontiguousarray(next_mid / mid - 1.0, dtype=np.float64)
    pred_price, filled = neutral_fill_null_predictions(pred_return, mid)
    if filled:
        raise ValueError(
            f"{caller}: {filled} of {frame.height} next-row returns were "
            "non-finite, which for a ratio of two midprices means a mid that is "
            "zero or not finite -- that is a broken book, not a missing "
            "prediction, and substituting the neutral mid would hide it"
        )
    result, ticks = _simulate_at_one_lot(frame, pred_price, caller)
    return _pnl_report(frame, result, ticks)


def perfect_foresight_ceiling(
    frame: pl.DataFrame, *, label_column: str = TARGET_COLUMN
) -> dict[str, Any]:
    """Phase 6's measurement, landed as code, and NOW A DIAGNOSTIC RATHER THAN
    THE GUARD'S REFERENCE (see the module docstring): what the flip-only rule
    earns on this frame when the prediction IS the realised future mid at one
    label's horizon.

    `frame` is the ADMITTED segment frame -- the exact rows the simulator
    will walk, in their own order. Not a raw partition.

    The perfect prediction goes through
    `models.conversion.neutral_fill_null_predictions`, the SAME return-to-price
    site the model's own prediction uses (D-07-33), so the ceiling and the
    P&L it bounds can never differ by a conversion. That is precisely how
    Phase 6 lost a plan to a quantisation asymmetry. Rows whose label is null
    get the neutral mid, which is measured to produce zero trades on this
    project's fixture and on both real candidate `val` windows -- so a row
    with no label cannot fabricate a trade.

    Returns, all labelled:

    - `rows_walked`, `null_label_rows_filled`
    - `trades`, `flips`, `rows_in_market` -- the kernel's own three counters
    - `closed_pnl_ticks` -- the trade-log walk (see `closed_pnl_ticks`)
    - `closed_pnl_usd_per_btc` and `closed_pnl_usd_at_traded_lot` -- the two
      conventions, both named in full
    - `closed_plus_unrealised_usd_at_traded_lot` -- the least-ambiguous
      expression, `equity_scaled[-1]` converted once. It INCLUDES the open
      leg's mark at the last row and is therefore a different number from the
      one above (measured on the approved `val` window: 1,120,460 closed
      against 1,120,530 closed plus unrealised).

    THE KEY SET AND EVERY VALUE ARE UNCHANGED by the 2026-09-29 guard change.
    The simulation and the reporting moved into `_simulate_at_one_lot` and
    `_pnl_report`, shared with `decision_row_perfect_foresight` so the two can
    never differ by how they were summarised -- and the proof that nothing
    moved is a re-run of `scripts/oof_viability_check.py` on `oof_block_1`,
    whose committed evidence carries this dict verbatim (2,192 trades /
    294,554 ticks / $29.4554).
    """
    mid = _require_simulatable(frame, "perfect_foresight_ceiling")
    pred_return, null_label_rows = _label_returns(frame, label_column)
    pred_price, filled = neutral_fill_null_predictions(pred_return, mid)
    if filled != null_label_rows:
        raise ValueError(
            f"perfect_foresight_ceiling: {null_label_rows} null labels but "
            f"{filled} neutral substitutions -- the two counts are the same rows"
        )
    result, ticks = _simulate_at_one_lot(frame, pred_price, "perfect_foresight_ceiling")
    return {
        "null_label_rows_filled": int(filled),
        **_pnl_report(frame, result, ticks),
    }


def guard_against_ceiling(pnl_ticks: int, bound_ticks: int, segment_name: str) -> None:
    """D-07-19: raise when a reported P&L reaches the segment's measured
    P&L bound. At the bound is already too high -- the bound is the midprice's
    own total variation at unit size, which no policy paying a spread can
    reach, so equality is already impossible.

    `bound_ticks` MUST BE `mid_total_variation`'s `bound_ticks`, not
    `perfect_foresight_ceiling`'s `closed_pnl_ticks` (changed 2026-09-29; the
    module docstring has the measurement that forced it). The parameter was
    called `ceiling_ticks` and the caller passed the label-horizon ceiling,
    which the frozen winner exceeded by up to 3.21x on four of five OOF blocks
    while staying below the total variation on all five -- so the guard fired on
    a correct result and would have aborted plan 07-11 after the val look was
    already spent.

    WHAT THIS GUARD DOES AND DOES NOT CATCH. Because the bound is strictly
    unattainable, no prediction fed through the decision rule can reach it: this
    is a check on IMPOSSIBLE results -- a P&L earned over a different row set
    than the bound was measured on, a fill counted at more than one lot, a trade
    log walked past `fill_count` -- and not a leak detector. The leak signal is
    the reported fraction of `decision_row_perfect_foresight`, which a real
    prediction genuinely approaches.

    The function's NAME is unchanged on purpose: `scripts/oof_viability_check.py`
    is a committed evidence producer whose output is the byte-identity
    instrument for the Stage-2 threshold change, and renaming an import it
    holds would break the one thing that can prove nothing else moved.
    """
    if int(pnl_ticks) >= int(bound_ticks):
        raise CeilingExceededError(
            f"{segment_name}: reported {int(pnl_ticks)} closed_pnl_ticks at or "
            f"above the measured P&L bound {int(bound_ticks)} -- INVESTIGATE. "
            "The two likeliest causes, in order: a prediction table misaligned "
            "with the decision rows, or a label leaking into a feature. "
            "(Expected to be `mid_total_variation`, the midprice's own total "
            "variation at unit size, which IS a theorem for a one-lot "
            "flip-only policy -- every leg pays at least a one-tick spread, so "
            "no policy reaches it and this result is not merely improbable. "
            "That also means no PREDICTION can produce it: look for a P&L "
            "earned over different rows than the bound, a fill of more than one "
            "lot, or a trade log walked past fill_count, before looking for a "
            "leak. If the number above is a LABEL-HORIZON ceiling, the guard is "
            "being fed the quantity that was measured to move 30x with a choice "
            "of label and it is the input that is wrong, not the result -- "
            "which is a reason to investigate rather than a proof of "
            "impossibility.)"
        )


def gate_forecast(metrics: dict[str, Any]) -> tuple[bool, str]:
    """D-07-18(a) as corrected by D-07-32: four checks in this order, over a
    `models.metrics.forecast_metrics` dict.

    The ORDER is the design. A non-finite `rank_ic_non_tied` is checked FIRST
    and gets its own message, because `np.nan > 0` is `False` -- so a naive
    comparison does fail closed, but it reports "IC not positive" when the
    truth is "the prediction is constant and has no ordering at all". Those
    are different findings and a human reading a table needs to be told
    which.

    Then both R-squared references, because each degenerate model measured in
    07-RESEARCH.md Q4 passes exactly one of them.
    """
    for key in ("rank_ic_non_tied", "r2_vs_zero", "r2_vs_mean"):
        if key not in metrics:
            raise KeyError(
                f"gate_forecast: metrics has no {key!r} -- pass the dict "
                "models.metrics.forecast_metrics returns, whole"
            )
    if not np.isfinite(metrics["rank_ic_non_tied"]):
        return False, (
            "rank IC is not finite -- scipy.stats.spearmanr returns NaN for a "
            "CONSTANT input. The prediction has no ordering at all; this is NOT "
            "a small positive IC and not a weak one. A constant predictor at "
            "the train mean is the measured shape of this failure, and it "
            "scores r2_vs_zero=+0.001647 while using no feature at all."
        )
    if not metrics["r2_vs_zero"] > 0.0:
        return False, (
            f"r2_vs_zero={metrics['r2_vs_zero']:.6g} <= 0 -- does not beat the "
            "constant-ZERO predictor, the reference D-07-18(a) names."
        )
    if not metrics["r2_vs_mean"] > 0.0:
        return False, (
            f"r2_vs_mean={metrics['r2_vs_mean']:.6g} <= 0 while "
            f"r2_vs_zero={metrics['r2_vs_zero']:.6g} > 0 -- it beats the "
            "constant-zero reference but not the unconditional MEAN, i.e. it "
            "learned the drift and nothing conditional. Measured: a constant at "
            "the sample mean scores r2_vs_zero=+0.001647 on 2026-09-18 with no "
            "feature used at all, and a model shrunk by 1e-6 keeps its full "
            "rank IC at r2_vs_zero=+0.000000. This second reference is what "
            "separates skill from scale."
        )
    if not metrics["rank_ic_non_tied"] > 0.0:
        return False, (
            f"rank_ic_non_tied={metrics['rank_ic_non_tied']:.6g} <= 0 -- the "
            "model cannot order the rows whose target actually moved "
            f"(tie_fraction={metrics.get('tie_fraction', float('nan')):.6g} on "
            "this segment)."
        )
    return True, (
        f"ok: r2_vs_zero={metrics['r2_vs_zero']:.6g} "
        f"r2_vs_mean={metrics['r2_vs_mean']:.6g} "
        f"rank_ic_non_tied={metrics['rank_ic_non_tied']:.6g} "
        f"rank_ic_all={metrics.get('rank_ic_all', float('nan')):.6g} "
        f"tie_fraction={metrics.get('tie_fraction', float('nan')):.6g}"
    )


def gate_monetization(sim_result: SimResult) -> tuple[bool, str]:
    """D-07-18(b): strictly more than 0 trades AND strictly more than $0, on
    the trade log sliced to `[:fill_count]`.

    Strictly, both: a run with zero trades has a P&L of exactly $0 and would
    pass a `>= 0` check while having monetised nothing at all, which is the
    exact reading the gate exists to refuse.
    """
    trades = int(sim_result.fill_count)
    ticks = closed_pnl_ticks(sim_result)
    usd = ticks_to_usd_at_traded_lot(ticks)
    if trades <= 0:
        return False, (
            "0 trades -- the simulator never crossed a quote, so there is no "
            "monetization to judge. A P&L of exactly $0 is not a non-negative "
            "result, it is an absent one."
        )
    if not usd > 0.0:
        return False, (
            f"{trades} trades but closed_pnl_ticks={ticks} = ${usd:+.4f} at the "
            f"traded 0.001 BTC lot (${ticks_to_usd_per_btc(ticks):+.2f} per 1 "
            "BTC) -- not strictly more than $0."
        )
    return True, (
        f"ok: {trades} trades, closed_pnl_ticks={ticks} = ${usd:+.4f} at the "
        f"traded 0.001 BTC lot (${ticks_to_usd_per_btc(ticks):+.2f} per 1 BTC)"
    )
