"""The two gates of D-07-18, and the perfect-foresight ceiling as CODE
rather than as a number somebody remembers (D-07-19, D-07-32, D-07-35).

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
    "gate_forecast",
    "gate_monetization",
    "guard_against_ceiling",
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


def perfect_foresight_ceiling(
    frame: pl.DataFrame, *, label_column: str = TARGET_COLUMN
) -> dict[str, Any]:
    """Phase 6's measurement, landed as code: what the flip-only rule earns
    on this frame when the prediction IS the realised future mid.

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

    Refuses a trade log whose fills are not all exactly one lot step: the
    "realised at the traded lot" figure presumes it, and a two-lot fill would
    make that label quietly wrong. All fills were exactly one lot on both
    real candidate windows, measured.
    """
    if frame.height == 0:
        raise ValueError(
            "perfect_foresight_ceiling: the frame has no rows -- a ceiling of "
            "zero over zero rows would make guard_against_ceiling fire on every "
            "reported P&L"
        )
    if MID_COLUMN not in frame.columns:
        raise ValueError(
            f"perfect_foresight_ceiling: frame has no {MID_COLUMN!r} column, so "
            "a predicted RETURN cannot be converted to the price the kernel wants"
        )
    pred_return, null_label_rows = _label_returns(frame, label_column)
    mid = np.ascontiguousarray(frame[MID_COLUMN].to_numpy(), dtype=np.float64)
    pred_price, filled = neutral_fill_null_predictions(pred_return, mid)
    if filled != null_label_rows:
        raise ValueError(
            f"perfect_foresight_ceiling: {null_label_rows} null labels but "
            f"{filled} neutral substitutions -- the two counts are the same rows"
        )

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
            f"perfect_foresight_ceiling: fill {offending} traded "
            f"{int(quantities[offending])} at QTY_SCALE, not the one "
            f"{LOT_STEP_SCALED} lot step every fill is assumed to be -- the "
            "'realised at the traded lot' figure below multiplies by one lot "
            "and would be wrong by that fill's size"
        )

    ticks = closed_pnl_ticks(result)
    return {
        "rows_walked": int(frame.height),
        "null_label_rows_filled": int(filled),
        "trades": int(result.counters["trades"]),
        "flips": int(result.counters["flips"]),
        "rows_in_market": int(result.counters["rows_in_market"]),
        "closed_pnl_ticks": ticks,
        "closed_pnl_usd_per_btc": ticks_to_usd_per_btc(ticks),
        "closed_pnl_usd_at_traded_lot": ticks_to_usd_at_traded_lot(ticks),
        "closed_plus_unrealised_usd_at_traded_lot": (
            int(result.equity_scaled[-1]) * TICK_SIZE_SCALED / PRICE_SCALE / QTY_SCALE
        ),
    }


def guard_against_ceiling(
    pnl_ticks: int, ceiling_ticks: int, segment_name: str
) -> None:
    """D-07-19: raise when a reported P&L reaches the segment's measured
    perfect-foresight ceiling. At the ceiling is already too high -- equality
    means the model matched perfect foresight tick for tick, which no
    forecast of a 10-second return does.
    """
    if int(pnl_ticks) >= int(ceiling_ticks):
        raise CeilingExceededError(
            f"{segment_name}: reported {int(pnl_ticks)} closed_pnl_ticks at or "
            f"above the measured perfect-foresight ceiling "
            f"{int(ceiling_ticks)} -- INVESTIGATE. The two likeliest causes, in "
            "order: a prediction table misaligned with the decision rows, or a "
            "label leaking into a feature. (The ceiling is a strong sanity "
            "bound, not a theorem: perfect foresight through the flip-only rule "
            "at x_bps=0 is one particular policy, not the path's P&L maximum, "
            "so this is a reason to investigate rather than a proof of "
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
