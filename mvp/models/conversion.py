"""The unit gap nobody had written down: a return is not a price, and this is
the ONE site where one becomes the other (D-07-23, D-07-33).

`sim.kernel.run_sim_checked`'s `pred` argument is a RAW USDT PRICE, on the
same scale as `bid_price`/`ask_price` -- its module docstring quantises it
against the book with `s = round(pred * PRICE_SCALE)`. The model predicts
`ret_10s_mid`, which `spec/labels.toml` defines as the SIMPLE return
`(mid_{t+10s} - mid_t) / mid_t`. So the two ends of the pipeline speak
different units, and the bridge is one multiplication:

    pred_price = mid * (1.0 + pred_return)

in float64. `mvp.md`'s struck "Decision logic (explicit)" block is what
happens when this conversion is skipped: it compared a predicted price CHANGE
against a price LEVEL, producing wrong signs except by coincidence.

WRITTEN AS ONE NAMED FUNCTION, NOT INLINED AT ITS CALLERS, for the reason
`sim/ticks.py:price_to_ticks` is: a conversion with a round trip worth
asserting deserves somewhere to assert it. `(price / mid) - 1.0` recovers the
return to float64 round-off, and
`test_the_return_to_price_conversion_round_trips_to_float64_roundoff` is where
that is measured rather than assumed. The ceiling computation shares this site
(D-07-33), so the model's prediction and the perfect-foresight bound can never
be converted by two different rules.

`mid` IS READ HERE AND BY NOTHING ELSE. That is what scopes D-07-09's refusal
to the ESTIMATOR'S INPUT COLUMNS rather than to "the module never touches
mid": a raw price level in the design matrix smuggles the day's trend into the
fit, while the same column is REQUIRED to turn a return back into a price. The
simulator itself never sees it -- `run_sim_checked` takes `bid_ticks`,
`ask_ticks` and `pred`, and derives its threshold offset from the two book
sides (`x_ticks = (b + a) * x_bps // 20_000`), so the kernel needs no mid at
all.

AND `mid` MUST NEVER GO THROUGH `sim.ticks.price_to_ticks`. At a one-tick
spread the mid sits exactly on a half tick -- 98.8% of real rows -- and
`price_to_ticks` refuses an exact half-tick round trip BY DESIGN, because for
bid/ask a half-tick value means the input was off-grid. `bid_price`/`ask_price`
are on-grid and are the only two columns that may pass through it. The price
this module returns is a MODEL PREDICTION and is quantised by the kernel's own
symmetric floor/ceil rule, never by that function.

NO POLARS, NO NUMBA, NO LAKE -- numpy only, in `sim/ticks.py`'s register, so
both the slice runner and a test can import it in isolation.
"""

from __future__ import annotations

import numpy as np

__all__ = ["neutral_fill_null_predictions", "pred_return_to_price"]


def _require_aligned_float64(
    values: np.ndarray, name: str, *, reference: np.ndarray | None = None
) -> np.ndarray:
    """One 1-D float64 array, optionally the same length as `reference`.

    float64 is REQUIRED rather than coerced. `run_sim_checked` refuses a
    non-float64 `pred` outright, and D-07-16 stores the table in float64
    because a float32 rounding error near the half-tick boundary flips a
    trigger -- so a silent upcast here would repair the symptom and keep the
    defect.
    """
    array = np.asarray(values)
    if array.dtype != np.float64:
        raise ValueError(
            f"{name} has dtype {array.dtype}, must be float64 -- the "
            "simulator quantises this value to ticks and a float32 rounding "
            "error near the half-tick boundary flips a trigger"
        )
    if array.ndim != 1:
        raise ValueError(f"{name} has {array.ndim} dimensions, must be 1-D")
    if reference is not None and array.shape != reference.shape:
        raise ValueError(
            f"{name} has shape {array.shape}, does not match {reference.shape} "
            "-- these two arrays are the same decision rows in the same order, "
            "so a length disagreement means one of them is a different frame"
        )
    return array


def pred_return_to_price(mid: np.ndarray, pred_return: np.ndarray) -> np.ndarray:
    """`mid * (1.0 + pred_return)` in float64, and nothing else.

    `mid` is the decision row's own midprice; `pred_return` is the predicted
    10-second simple return for that row. The result is the raw USDT price
    `sim.kernel.run_sim_checked` wants as `pred`.

    `mid` must be finite and strictly positive, asserted rather than assumed.
    A price of zero or below is not a book, and the assertion is also what
    makes a SWAPPED call loud: `pred_return_to_price(pred_return, mid)` passes
    an array of near-zero returns as the midprice and is refused here instead
    of quietly simulating a $1-scale instrument.
    """
    mid = _require_aligned_float64(mid, "mid")
    pred_return = _require_aligned_float64(pred_return, "pred_return", reference=mid)
    finite = np.isfinite(mid)
    if not bool(np.all(finite)):
        bad = int(np.flatnonzero(~finite)[0])
        raise ValueError(
            f"pred_return_to_price: mid[{bad}]={mid[bad]!r} is not finite -- "
            "a decision row with no midprice has no price to convert a return "
            "into, and the kernel would refuse the resulting pred anyway"
        )
    positive = mid > 0.0
    if not bool(np.all(positive)):
        bad = int(np.flatnonzero(~positive)[0])
        raise ValueError(
            f"pred_return_to_price: mid[{bad}]={mid[bad]!r} is not strictly "
            "positive, so it is not a midprice. If the arguments were "
            "swapped, this is what that looks like: the order is (mid, "
            "pred_return)"
        )
    if not bool(np.all(np.isfinite(pred_return))):
        bad = int(np.flatnonzero(~np.isfinite(pred_return))[0])
        raise ValueError(
            f"pred_return_to_price: pred_return[{bad}]="
            f"{pred_return[bad]!r} is not finite. A row with no prediction is "
            "not a conversion failure -- route it through "
            "neutral_fill_null_predictions, which substitutes the neutral mid "
            "and RETURNS THE COUNT of substitutions"
        )
    return mid * (1.0 + pred_return)


def neutral_fill_null_predictions(
    pred_return: np.ndarray, mid: np.ndarray
) -> tuple[np.ndarray, int]:
    """Convert a predicted-return column to the simulator's raw price,
    substituting the NEUTRAL midprice wherever there is no prediction.
    Returns `(pred_price, substitution_count)`.

    A row can genuinely have no prediction: the label is null at the end of a
    partition, a feature is null during warmup, and a fitted model asked to
    score such a row emits NaN rather than a fabricated 0.0. The kernel
    refuses a non-finite `pred` outright (`STATUS_NON_FINITE_PRED`), so those
    rows need a value, and `mid` is the one value measured to be NEUTRAL:
    `pred = mid` yields exactly 0 trades, on this project's fixture and on
    both real candidate `val` windows (correction C4). The mid lies inside the
    spread, and at the minimum one-tick spread it sits exactly on the half
    tick where the kernel's symmetric floor/ceil rule triggers neither side.

    THE SUBSTITUTION IS A ZERO RETURN, NOT A WRITE OF `mid`. Both produce the
    same bits -- `mid * (1.0 + 0.0)` is `mid` exactly in IEEE 754 -- but only
    one of them keeps the conversion at a single site. An inline
    `np.where(missing, mid, mid * (1.0 + pred_return))` is a second copy of
    the rule that the round-trip test does not cover.

    IT RETURNS THE COUNT, AND THE CALLER MUST LOG IT. That is the whole reason
    this is a named function rather than two lines at the call site: a table
    that is ENTIRELY NaN simulates cleanly, reports zero trades, and reads as
    "a model with no signal" when the truth is "a pipeline that predicted
    nothing". Same discipline as `sim.outputs.SimResult.fill_count` -- the
    reader sees the number before the hazard can hide behind it. An
    all-substituted column is not merely reported but REFUSED, because there
    is no honest simulation of it.
    """
    mid = _require_aligned_float64(mid, "mid")
    pred_return = _require_aligned_float64(pred_return, "pred_return", reference=mid)
    if pred_return.size == 0:
        raise ValueError(
            "neutral_fill_null_predictions: the prediction column is empty -- "
            "a run over zero decision rows reports zero trades and says "
            "nothing about a model"
        )
    missing = ~np.isfinite(pred_return)
    count = int(missing.sum())
    if count == pred_return.size:
        raise ValueError(
            f"neutral_fill_null_predictions: every one of {count} predictions "
            "is non-finite. Substituting the neutral mid on all of them would "
            "simulate cleanly and report 0 trades, which reads as 'a model "
            "with no signal' rather than 'nothing was predicted' -- refused "
            "instead of reported"
        )
    price = pred_return_to_price(
        mid=mid, pred_return=np.where(missing, 0.0, pred_return)
    )
    return price, count
