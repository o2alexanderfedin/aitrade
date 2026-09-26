"""The scoring statistics for a Stage-1 forecast, written so that the two
ways of having NO SKILL are visible in the table instead of hidden by it
(D-07-18, D-07-32, corrections C2/C3).

Pure numpy and scipy in, plain Python scalars out. No I/O, no MLflow, no
lake, no polars -- `sim/ticks.py`'s register, so a test can import this in
isolation and a slice runner can call it without dragging a store in.

WHY THERE ARE TWO R-SQUARED NUMBERS HERE, AND NOT ONE. D-07-18(a) names one
reference: the constant-ZERO predictor, `1 - SSE / sum(y**2)`. Measured on
the real 2026-09-18 rows, that single number is satisfied by two different
predictors that have no conditional skill at all:

    predictor (2026-09-18, in sample)  r2_vs_zero  r2_vs_mean  IC (non-tied)
    OLS on the three features          +0.022238   +0.020625   +0.204823
    a CONSTANT at the sample mean      +0.001647   +0.000000   NaN
    the same OLS multiplied by 1e-6    +0.000000   -0.001649   +0.204823

The constant passes the R-squared half while using no feature at all; the
shrunk model passes the rank-IC half while its predictions are six orders
too small to trigger anything. Each wins one half of the gate. Rank IC is
invariant to any monotone rescaling and R-squared is not -- "scale-free
skill versus scale-dependent skill", 07-RESEARCH.md's named pitfall -- so
the gate is only meaningful because it requires BOTH halves AND the second
R-squared reference. `r2_vs_mean` is the number that means "has conditional
signal"; `r2_vs_zero` is the number D-07-18 names. They differ by exactly
`n * y_mean**2`, because `sum((y - ybar)**2) == sum(y**2) - n * ybar**2`.
With `ybar` tiny but nonzero and R-squared itself a couple of percent, that
small ABSOLUTE difference is a large RELATIVE one: measured,
`SS_mean / SS_zero = 0.9983534` and the two R-squareds are 7.8% apart.

`r2_vs_zero_of_constant_train_mean` is the third column that makes the
loophole visible rather than latent, and it is computed from the TRAIN mean
passed in by the caller -- never from `pred.mean()` and never from
`y.mean()`. See `forecast_metrics`' own docstring for why that argument is a
required keyword with no default.

TWO CORRECTIONS TO THE DECISIONS THIS IMPLEMENTS, because a reader will
otherwise re-derive the wrong ones from D-07-18's text:

1. THE TIE FRACTION IS A PER-SEGMENT QUANTITY (correction C3). D-07-18's
   43.9% zero point mass is a property of **2026-09-13 alone** -- 3,012,962
   of 6,864,853 rows, verified. The seven-day pool is 20.01%, the approved
   `val` window is 13.83%, and 2026-09-18 alone is 9.60%. A hardcoded 43.9%
   would describe a day that is inside `train` under either fold layout.
   Nothing in this module carries a tie-fraction constant; every caller gets
   the fraction measured on the rows it actually scored.

2. TIES DO NOT RELIABLY FLATTER ANYTHING (correction C2). D-07-18 says a
   statistic that treats the ties as ordinary observations "flatters any
   model". Measured, the sign goes both ways: on 2026-09-18 (9.77% ties)
   including them INFLATES the IC by 1.3% (+0.207494 all rows vs +0.204823
   non-tied); on 2026-09-13 (43.89% ties) it DEFLATES it by 3.2% (+0.363207
   vs +0.375057). Which way it goes depends on where the model's
   predictions for the tied rows happen to fall, not on the tie fraction.
   So `rank_ic_all` and `rank_ic_non_tied` are both returned and NEITHER is
   presented as a correction of the other: they answer different questions
   -- can the model order the whole population, versus can it order the rows
   whose target actually moved -- and the second is the one a
   threshold-crossing policy monetises.

`scipy.stats.spearmanr` IS `rankdata` (average ranks) THEN ORDINARY PEARSON
-- verified in scipy 1.18.1's `_stats_py.py` source. There is no separate
tie-correction term: the average-rank assignment IS the tie handling, and
Pearson-on-ranks is correct under ties, unlike the `6*sum(d**2)/(n*(n**2-1))`
shortcut, which is not. The exact-equality test `y == 0.0` is likewise
correct rather than sloppy: these are genuine exact zeros produced by an
unchanged mid over the label horizon, not near-zeros -- 2026-09-18's
scorable rows hold only 65,888 distinct target values among 7.85M rows.

A NaN IC IS RETURNED AS NaN AND NEVER AS 0.0. `spearmanr` returns NaN (with
a `ConstantInputWarning`) when an input is constant, which is exactly what a
zero-skill constant predictor is. `np.nan > 0` is `False`, so a naive
comparison downstream fails CLOSED -- but it reports "IC not positive" when
the truth is "the prediction has no ordering at all". `models.gates`
detects the non-finite case separately and says so; this module's job is
only to not destroy the evidence. Measured on scipy 1.18.1: a constant
input emits `ConstantInputWarning` and returns NaN, while a subset of 0 or 1
rows returns NaN with NO warning at all -- so the short-subset case is
guarded explicitly here rather than left to a warning filter that would
never fire.
"""

from __future__ import annotations

import warnings

import numpy as np
from scipy.stats import ConstantInputWarning, spearmanr

__all__ = ["FORECAST_METRIC_KEYS", "forecast_metrics"]

#: Exactly what `forecast_metrics` returns, in its declared order. A caller
#: (or a test) asserts against this rather than against a hand-typed literal,
#: so a renamed key is a failure at the one site that owns the names.
FORECAST_METRIC_KEYS: tuple[str, ...] = (
    "n_scorable",
    "r2_vs_zero",
    "r2_vs_mean",
    "rank_ic_all",
    "rank_ic_non_tied",
    "tie_fraction",
    "r2_vs_zero_of_constant_train_mean",
)

#: Below this many rows `spearmanr` returns NaN with no warning at all
#: (measured, scipy 1.18.1: n=0 and n=1 both return NaN silently). The
#: guard exists so the NaN carries a REASON in the docstring rather than
#: arriving from a code path nobody looked at.
_MIN_RANKABLE_ROWS: int = 2


def _require_scorable_pair(
    pred: np.ndarray, y: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Both arrays 1-D float64 and the same length, asserted rather than
    coerced.

    float64 is REQUIRED, for `models.conversion`'s reason: D-07-16 stores
    the prediction table in float64 and the tier's labels are Float64, so a
    float32 array arriving here came from somewhere else and the caller
    should find that out here rather than in a metric that is quietly a few
    digits short.

    A length disagreement is refused rather than broadcast: `pred` and `y`
    are the same decision rows in the same order, so two different lengths
    mean one of them is a different frame -- the single most likely way a
    prediction table gets scored against the wrong segment.
    """
    for name, array in (("pred", pred), ("y", y)):
        if not isinstance(array, np.ndarray):
            raise ValueError(
                f"forecast_metrics: {name} is {type(array)!r}, must be a numpy array"
            )
        if array.dtype != np.float64:
            raise ValueError(
                f"forecast_metrics: {name} has dtype {array.dtype}, must be float64 -- "
                "the prediction table is float64 by D-07-16 and the tier's labels are "
                "Float64, so another dtype means this array came from somewhere else"
            )
        if array.ndim != 1:
            raise ValueError(
                f"forecast_metrics: {name} has {array.ndim} dimensions, must be 1-D"
            )
    if pred.shape != y.shape:
        raise ValueError(
            f"forecast_metrics: pred has shape {pred.shape} and y has shape {y.shape} -- "
            "these are the same decision rows in the same order, so a length "
            "disagreement means one of them is a different frame"
        )
    return pred, y


def _rank_ic(pred: np.ndarray, y: np.ndarray) -> float:
    """`spearmanr(pred, y).statistic`, with the two NaN routes closed off
    deliberately instead of by accident.

    `ConstantInputWarning` is turned into an ERROR and caught, so a constant
    input produces a NaN this function chose to return rather than a warning
    printed to a log nobody reads. Fewer than `_MIN_RANKABLE_ROWS` rows is
    checked FIRST, because scipy returns NaN there with no warning at all
    and a filter would never see it.
    """
    if pred.shape[0] < _MIN_RANKABLE_ROWS:
        return float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConstantInputWarning)
        try:
            return float(spearmanr(pred, y).statistic)
        except ConstantInputWarning:
            return float("nan")


def forecast_metrics(
    pred: np.ndarray, y: np.ndarray, *, train_mean: float
) -> dict[str, float]:
    """The seven numbers `models.gates.gate_forecast` reads, all computed on
    ONE shared scorable row set: `np.isfinite(pred) & np.isfinite(y)`.

    `train_mean` IS A REQUIRED KEYWORD WITH NO DEFAULT, and that is the
    whole design of this signature. The zero-skill control column is "a
    constant predictor at the TRAIN mean", and the only mean available
    inside a scoring function is the EVALUATION set's own. A default would
    therefore be filled in from `y.mean()` -- a different, strictly weaker
    statistic, logged under a key that says `train`, and read by the human
    checkpoint in plan 07-11 as the zero-skill control it is not. In
    `mode="val"` the train frame is never opened at all, so the value cannot
    be recovered later either: it comes from
    `models.frozen.FrozenLinearPredictor.train_target_mean`, which the fit
    captured and the frozen body carries for exactly this purpose.

    The distinction is not academic. In sample, a constant at the sample
    mean scores `r2_vs_zero = +0.001647`; out of sample, a constant at the
    TRAIN mean scores -0.000070, +0.000012, -0.000029 and -0.000007 on the
    four train-internal splits -- noise around zero, which is what a
    zero-skill control is supposed to look like. Substituting the evaluation
    mean would replace the honest out-of-sample control with the in-sample
    one and make the loophole look like a property of the data.

    Returns `FORECAST_METRIC_KEYS`, every one measured on the same rows:

    - `n_scorable` -- how many rows survived the finite mask, as an int.
    - `r2_vs_zero` -- `1 - SSE / sum(y**2)`: the constant-ZERO predictor as
      the reference, the one D-07-18 names.
    - `r2_vs_mean` -- `1 - SSE / sum((y - y.mean())**2)`: the ordinary
      R-squared, and the one that means "has conditional signal".
    - `rank_ic_all` / `rank_ic_non_tied` -- `spearmanr(...).statistic` over
      every scorable row, and over the rows with `y != 0.0`.
    - `tie_fraction` -- `(y == 0.0).mean()` on the scorable rows. Reported
      as a first-class number beside the IC, never folded into it, and never
      compared against a hardcoded constant (correction C3).
    - `r2_vs_zero_of_constant_train_mean` -- `1 - sum((y - train_mean)**2) /
      sum(y**2)`, the zero-skill control column.

    Raises rather than returning a silent `inf`/`nan` when the statistics
    would be undefined: an empty scorable set, an all-zero target (no
    `sum(y**2)` to divide by), or a constant target (no `SS_mean`). Each
    says something a metric cannot, so each is an exception and not a
    number. `train_mean` must itself be finite, because a NaN control column
    is an uninformative column that still fills a slot in a table a human
    is about to read.
    """
    pred, y = _require_scorable_pair(pred, y)
    train_mean = float(train_mean)
    if not np.isfinite(train_mean):
        raise ValueError(
            f"forecast_metrics: train_mean={train_mean!r} is not finite -- the "
            "zero-skill control column would be NaN while still occupying a slot "
            "in the metrics table. It comes from "
            "FrozenLinearPredictor.train_target_mean; an unset field is a bug "
            "there, not a number to report here"
        )

    scorable = np.isfinite(pred) & np.isfinite(y)
    n_scorable = int(scorable.sum())
    if n_scorable == 0:
        raise ValueError(
            "forecast_metrics: no row has BOTH a finite prediction and a finite "
            "target, so every statistic below would be computed on zero rows. An "
            "empty score is not a score"
        )
    p = pred[scorable]
    t = y[scorable]

    sse = float(((t - p) ** 2).sum())
    ss_zero = float((t**2).sum())
    ss_mean = float(((t - t.mean()) ** 2).sum())
    if ss_zero == 0.0:
        raise ValueError(
            f"forecast_metrics: every one of the {n_scorable} scorable targets is "
            "exactly 0.0, so sum(y**2) is 0 and there is nothing for a model to "
            "explain -- an R-squared against the constant-zero predictor is "
            "undefined, not infinite"
        )
    if ss_mean == 0.0:
        raise ValueError(
            f"forecast_metrics: all {n_scorable} scorable targets are the same "
            f"value ({float(t[0])!r}), so SS_mean is 0 and r2_vs_mean is undefined. "
            "A segment whose target never moves cannot score a forecast"
        )

    tied = t == 0.0
    return {
        "n_scorable": n_scorable,
        "r2_vs_zero": 1.0 - sse / ss_zero,
        "r2_vs_mean": 1.0 - sse / ss_mean,
        "rank_ic_all": _rank_ic(p, t),
        "rank_ic_non_tied": _rank_ic(p[~tied], t[~tied]),
        "tie_fraction": float(tied.mean()),
        # The TRAIN mean, and nothing derived from `p` or `t`. The research
        # note's own code sample wrote `p.mean()` here, which is the exact
        # substitution this keyword exists to prevent.
        "r2_vs_zero_of_constant_train_mean": 1.0
        - float(((t - train_mean) ** 2).sum()) / ss_zero,
    }
