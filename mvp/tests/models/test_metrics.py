"""The two ways of having NO SKILL, reconstructed on synthetic data with the
same SHAPE as the real measurement, and each one's half of D-07-18(a) shown
to be winnable.

WHY SYNTHETIC AND NOT THE REAL ROWS. The measured table in
`07-RESEARCH.md` Q4 came from 2026-09-18, a real partition inside `train`.
Reproducing it here through `harness.accessor.materialize` would spend an
irreversible validation look once per commit, because pre-commit hook 19
runs the full suite every time (D-07-34). So the synthetic target is
CALIBRATED to the real one's shape instead, and the calibration is stated
rather than groped for -- see `_synthetic_scoring_set` for the two
population identities that fix the constants.

MEASURED HERE against the real numbers it reconstructs:

| predictor                       | r2_vs_zero  | r2_vs_mean | IC (non-tied) |
|---------------------------------|-------------|------------|---------------|
| real 2026-09-18 OLS             | +0.022238   | +0.020625  | +0.204823     |
| this file's fitted model        | +0.021255   | +0.019653  | +0.150129     |
| real constant at the mean       | +0.001647   | +0.000000  | NaN           |
| this file's constant at the mean| +0.001634   | +0.000000  | NaN           |
| real OLS x 1e-6                 | +0.000000   | -0.001649  | +0.204823     |
| this file's model x 1e-6        | +4.2509e-08 | -0.0016366 | +0.150129     |

`SS_mean / SS_zero` is 0.9983534 on the real rows and 0.9983660 here -- the
ratio that makes the two R-squared references 7.8% apart in relative terms
is reproduced to five digits, which is the property every test below leans
on. The real shrunk model's "+0.000000" is a SIX-DECIMAL ROUNDING of a
strictly positive number; here it is +4.25e-08, and that sign is the whole
loophole (see `test_a_model_shrunk_by_1e_6...`).
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest
from scipy.stats import ConstantInputWarning, spearmanr

from models.metrics import FORECAST_METRIC_KEYS, forecast_metrics

#: The target's sd on the rows whose mid actually moved. Only the RATIOS
#: below matter -- every statistic here is scale-free in `y` except the two
#: R-squareds, which are ratios of sums of squares.
SIGMA: float = 1.0e-3

#: The target's drift on those same rows, as a multiple of `SIGMA`. Chosen,
#: not guessed: with a zero point mass `f`, the pooled
#: `n * ybar**2 / sum(y**2)` works out to `(1 - f) * mu**2 / (mu**2 +
#: sigma**2)`, and setting that to the real day's measured 0.0016466 (i.e.
#: `1 - SS_mean/SS_zero`) at `f = 0.18` gives `mu/sigma = 0.044845`.
MU_OVER_SIGMA: float = 0.044845

#: The exact-zero point mass -- an unchanged mid over the label horizon.
#: 18% sits between the approved `val` window's 13.83% and the pool's
#: 20.01% (correction C3). NOT 43.9%: that is 2026-09-13 alone.
ZERO_FRACTION: float = 0.18

#: The feature's noise, as a multiple of `SIGMA`. A one-feature OLS scores
#: a population R-squared of `1 / (1 + s**2)`; `s = 6.67` puts it at 0.0220,
#: inside the +0.013..+0.048 the real train-internal splits measured.
FEATURE_NOISE_OVER_SIGMA: float = 6.67

N_ROWS: int = 40_000
SEED: int = 20260926

#: A TRAIN mean -- deliberately not this evaluation set's own mean. The real
#: relationship between the two is exactly this: close, and not equal.
TRAIN_MEAN: float = MU_OVER_SIGMA * SIGMA


def _synthetic_scoring_set() -> dict[str, np.ndarray | float]:
    """One `(pred, y)` pair with the real target's shape, plus the two
    degenerate predictors the loophole is made of.

    Construction order matters, the way `tests/fixtures/model_span.py`'s
    does: the target first (drift, spread and exact-zero point mass), then a
    FEATURE derived from it with noise, then an OLS *fitted on those same
    rows* -- in sample, exactly as the research measurement was, because the
    in-sample case is where the constant-at-the-mean trap is large rather
    than negligible.
    """
    rng = np.random.default_rng(SEED)
    unchanged_mid = rng.random(N_ROWS) < ZERO_FRACTION
    y = np.where(
        unchanged_mid,
        0.0,
        MU_OVER_SIGMA * SIGMA + SIGMA * rng.standard_normal(N_ROWS),
    )
    feature = y + FEATURE_NOISE_OVER_SIGMA * SIGMA * rng.standard_normal(N_ROWS)
    design = np.column_stack([np.ones(N_ROWS), feature])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ beta
    return {
        "y": y,
        "fitted": fitted,
        "constant_at_evaluation_mean": np.full(N_ROWS, float(y.mean())),
        "constant_at_train_mean": np.full(N_ROWS, TRAIN_MEAN),
        "shrunk_by_1e_6": fitted * 1e-6,
        "evaluation_mean": float(y.mean()),
    }


def _r2_vs_zero_of_a_constant(y: np.ndarray, constant: float) -> float:
    """`1 - sum((y - c)**2) / sum(y**2)`, written out here so the module
    under test is checked against arithmetic rather than against itself."""
    return 1.0 - float(((y - constant) ** 2).sum()) / float((y**2).sum())


# --------------------------------------------------------------------------
# 1-2. The two degenerate predictors, each winning one half of D-07-18(a)
# --------------------------------------------------------------------------


def test_a_constant_predictor_at_the_sample_mean_scores_positive_r2_vs_zero_and_zero_r2_vs_mean():
    """The first half of the loophole, reconstructed. A predictor that uses
    NO FEATURE AT ALL beats the constant-zero reference -- and scores exactly
    0.0 against the mean, which is the reference that would have caught it.
    """
    data = _synthetic_scoring_set()
    y = data["y"]
    metrics = forecast_metrics(
        data["constant_at_evaluation_mean"], y, train_mean=TRAIN_MEAN
    )
    print(
        "constant at the evaluation mean: "
        f"r2_vs_zero={metrics['r2_vs_zero']:+.6f} "
        f"r2_vs_mean={metrics['r2_vs_mean']:+.6f} "
        f"ic_all={metrics['rank_ic_all']} "
        f"(real 2026-09-18: +0.001647 / +0.000000 / nan)"
    )
    assert metrics["r2_vs_zero"] > 0.0, (
        "the constant-at-the-mean predictor must WIN the r2_vs_zero half of "
        "D-07-18(a) as written -- if it does not, this test is not "
        "reconstructing the loophole"
    )
    assert 1.0e-3 < metrics["r2_vs_zero"] < 3.0e-3, (
        f"r2_vs_zero={metrics['r2_vs_zero']:.6g} is not the same ORDER as the "
        "real +0.001647; the synthetic drift is miscalibrated"
    )
    # Exactly zero, not approximately: the constant IS the mean, so
    # SSE == SS_mean bit-for-bit.
    assert metrics["r2_vs_mean"] == 0.0, (
        "a constant at the evaluation set's own mean has SSE == SS_mean "
        f"exactly, so r2_vs_mean must be 0.0; got {metrics['r2_vs_mean']!r}"
    )
    # And the reason the second reference is the one with teeth: it is not
    # positive, so the corrected gate rejects what the original one passes.
    assert not metrics["r2_vs_mean"] > 0.0


def test_a_model_shrunk_by_1e_6_keeps_its_rank_ic_and_loses_all_its_r2():
    """The mirror image, and the sharper half. Multiplying every prediction
    by 1e-6 is a monotone rescaling, so the rank IC is BIT-IDENTICAL -- while
    R-squared, which is not scale-free, collapses. `r2_vs_zero` stays
    strictly POSITIVE (the real day's "+0.000000" is a six-decimal rounding),
    so D-07-18(a) as originally written PASSES this model. Only the second
    R-squared reference rejects it.
    """
    data = _synthetic_scoring_set()
    y = data["y"]
    honest = forecast_metrics(data["fitted"], y, train_mean=TRAIN_MEAN)
    shrunk = forecast_metrics(data["shrunk_by_1e_6"], y, train_mean=TRAIN_MEAN)
    print(
        f"fitted: r2_vs_zero={honest['r2_vs_zero']:+.6f} "
        f"r2_vs_mean={honest['r2_vs_mean']:+.6f} "
        f"ic_non_tied={honest['rank_ic_non_tied']:+.6f}\n"
        f"x 1e-6: r2_vs_zero={shrunk['r2_vs_zero']:+.9e} "
        f"r2_vs_mean={shrunk['r2_vs_mean']:+.9f} "
        f"ic_non_tied={shrunk['rank_ic_non_tied']:+.6f}"
    )
    assert honest["r2_vs_zero"] > 0.0 and honest["r2_vs_mean"] > 0.0, (
        "the honest model must pass both halves, or this fixture cannot "
        "distinguish skill from its absence"
    )
    assert shrunk["rank_ic_all"] == honest["rank_ic_all"]
    assert shrunk["rank_ic_non_tied"] == honest["rank_ic_non_tied"], (
        "a positive rescaling leaves every rank unchanged, so the IC must be "
        "equal to the BIT, not merely close"
    )
    assert abs(shrunk["r2_vs_zero"]) < 1.0e-4, (
        f"r2_vs_zero={shrunk['r2_vs_zero']:.6g} has not collapsed; 'loses all "
        "its R-squared' is an absolute bound near zero, not an equality to 0.0"
    )
    # THE LOOPHOLE, ASSERTED: D-07-18(a) as written is
    # `r2_vs_zero > 0 and rank_ic_non_tied > 0`, and this model wins both.
    assert shrunk["r2_vs_zero"] > 0.0 and shrunk["rank_ic_non_tied"] > 0.0, (
        "if the shrunk model failed D-07-18(a) as ORIGINALLY written there "
        "would be nothing for D-07-32 to correct"
    )
    assert shrunk["r2_vs_mean"] < 0.0, (
        "the second reference is the only thing that rejects this model: with "
        "a nonzero target mean, SS_mean is smaller than SS_zero by n*ybar**2, "
        f"so r2_vs_mean must go negative; got {shrunk['r2_vs_mean']!r}"
    )


# --------------------------------------------------------------------------
# 3. A NaN IC is NaN
# --------------------------------------------------------------------------


def test_a_constant_prediction_yields_a_nan_ic_rather_than_zero():
    """`spearmanr` of a constant input is NaN, and this module returns that
    NaN instead of a 0.0 that would read as "no correlation measured".

    Also asserts the `ConstantInputWarning` does not ESCAPE: it is turned
    into an error and caught inside `_rank_ic`, so a caller's log stays clean
    and the NaN is a decision rather than a side effect.
    """
    data = _synthetic_scoring_set()
    y = data["y"]
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        metrics = forecast_metrics(
            data["constant_at_train_mean"], y, train_mean=TRAIN_MEAN
        )
    leaked = [w for w in captured if issubclass(w.category, ConstantInputWarning)]
    print(
        f"constant prediction: ic_all={metrics['rank_ic_all']} "
        f"ic_non_tied={metrics['rank_ic_non_tied']} "
        f"escaped ConstantInputWarnings={len(leaked)}"
    )
    assert np.isnan(metrics["rank_ic_all"])
    assert np.isnan(metrics["rank_ic_non_tied"])
    assert metrics["rank_ic_all"] != 0.0, (
        "NaN, not 0.0 -- 'the prediction has no ordering at all' and 'the "
        "ordering carries no information' are different findings"
    )
    assert leaked == [], (
        f"{len(leaked)} ConstantInputWarning(s) escaped to the caller; the "
        "filter in _rank_ic is supposed to convert them into the returned NaN"
    )


# --------------------------------------------------------------------------
# 4. The two references differ by exactly n * ybar**2
# --------------------------------------------------------------------------


def test_the_two_r2_references_differ_by_exactly_n_times_mean_squared():
    """`sum((y - ybar)**2) == sum(y**2) - n * ybar**2`, and therefore the two
    R-squareds share one SSE and differ only in their denominator. Checked
    both ways: the denominators' difference against `n * ybar**2`, and the
    implied SSE recovered from each R-squared.
    """
    data = _synthetic_scoring_set()
    y = data["y"]
    metrics = forecast_metrics(data["fitted"], y, train_mean=TRAIN_MEAN)
    n = metrics["n_scorable"]
    ss_zero = float((y**2).sum())
    ss_mean = float(((y - y.mean()) ** 2).sum())
    print(
        f"SS_zero={ss_zero:.12e} SS_mean={ss_mean:.12e} "
        f"ratio={ss_mean / ss_zero:.7f} (real 2026-09-18: 0.9983534)  "
        f"r2_vs_zero={metrics['r2_vs_zero']:+.6f} "
        f"r2_vs_mean={metrics['r2_vs_mean']:+.6f}  relative gap="
        f"{(metrics['r2_vs_zero'] - metrics['r2_vs_mean']) / metrics['r2_vs_mean']:.4%}"
    )
    assert ss_zero - ss_mean == pytest.approx(n * y.mean() ** 2, rel=1e-9)
    sse_from_zero = (1.0 - metrics["r2_vs_zero"]) * ss_zero
    sse_from_mean = (1.0 - metrics["r2_vs_mean"]) * ss_mean
    assert sse_from_zero == pytest.approx(sse_from_mean, rel=1e-12), (
        "both R-squareds must be built from ONE SSE over one row set; a "
        "difference here means the two are measured on different rows"
    )
    assert metrics["r2_vs_zero"] > metrics["r2_vs_mean"], (
        "with a positive target mean SS_mean < SS_zero, so the zero reference "
        "is the more flattering of the two -- which is why it is not the only "
        "one reported"
    )
    assert ss_mean / ss_zero == pytest.approx(0.99837, abs=5e-5), (
        "the synthetic ratio has drifted from the real day's 0.9983534; the "
        "7.8% relative gap the two references are worth depends on it"
    )


# --------------------------------------------------------------------------
# 5. The non-tied IC, the tie fraction, and the subset being non-empty
# --------------------------------------------------------------------------


def test_the_non_tied_ic_is_computed_on_the_rows_whose_target_moved_and_the_tie_fraction_reports_the_rest():
    """Two statistics answering two questions (correction C2), plus the
    anti-vacuity check that the non-tied subset is NOT EMPTY -- without it
    `rank_ic_non_tied` could be silently computed on zero rows and returned
    as a NaN that looks like the constant-input case.
    """
    data = _synthetic_scoring_set()
    y = data["y"]
    metrics = forecast_metrics(data["fitted"], y, train_mean=TRAIN_MEAN)
    moved = y != 0.0
    n_moved = int(moved.sum())
    expected = float(spearmanr(data["fitted"][moved], y[moved]).statistic)
    direction = (
        "DEFLATE" if metrics["rank_ic_non_tied"] > metrics["rank_ic_all"] else "INFLATE"
    )
    print(
        f"tie_fraction={metrics['tie_fraction']:.5f} on {metrics['n_scorable']} "
        f"scorable rows; non-tied rows={n_moved}; "
        f"ic_all={metrics['rank_ic_all']:+.6f} "
        f"ic_non_tied={metrics['rank_ic_non_tied']:+.6f} -- here the ties "
        f"{direction} the IC by "
        f"{abs(metrics['rank_ic_non_tied'] / metrics['rank_ic_all'] - 1.0):.1%} "
        "(measured both directions on real data: +1.3% on 2026-09-18, -3.2% "
        "on 2026-09-13 -- neither statistic corrects the other)"
    )
    # Anti-vacuity FIRST: a statistic on an empty subset is not a statistic.
    assert n_moved > 0
    assert n_moved > metrics["n_scorable"] // 2, (
        f"only {n_moved} of {metrics['n_scorable']} rows moved -- the non-tied "
        "IC would be a statistic about a minority and the comparison with "
        "ic_all would be uninformative"
    )
    assert metrics["tie_fraction"] == pytest.approx(
        1.0 - n_moved / metrics["n_scorable"], rel=1e-12
    )
    assert metrics["rank_ic_non_tied"] == expected, (
        "the non-tied IC must be spearmanr over exactly the y != 0.0 rows, to the bit"
    )
    assert metrics["rank_ic_non_tied"] != metrics["rank_ic_all"], (
        "the two ICs are different statistics on this data; if they were "
        "equal the tie fraction would be zero and the test vacuous"
    )
    assert 0.09 < metrics["tie_fraction"] < 0.21, (
        f"tie_fraction={metrics['tie_fraction']:.4f} is outside the real "
        "per-segment range (9.60% on 2026-09-18, 13.83% on the approved val "
        "window, 20.01% pooled) -- 43.9% is 2026-09-13 alone (C3)"
    )


# --------------------------------------------------------------------------
# 6. The train mean cannot be substituted
# --------------------------------------------------------------------------


def test_forecast_metrics_requires_the_train_mean_and_does_not_fall_back_to_the_evaluation_mean():
    """Without this test the substitution is INVISIBLE: replacing
    `train_mean` with `t.mean()` inside the module leaves every other test in
    this file green.

    Three claims. (1) Omitting the keyword is a `TypeError`, so no caller can
    get the control column without saying which mean it is. (2) Two different
    train means give two DIFFERENT control columns -- the assertion a
    `t.mean()` substitution fails, because under it both calls return the
    same number. (3) Each control column equals its own arithmetic.
    """
    data = _synthetic_scoring_set()
    y = data["y"]
    pred = data["fitted"]

    with pytest.raises(TypeError):
        forecast_metrics(pred, y)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        forecast_metrics(pred, y, float(y.mean()))  # type: ignore[misc]

    evaluation_mean = float(y.mean())
    at_train = forecast_metrics(pred, y, train_mean=TRAIN_MEAN)
    at_evaluation = forecast_metrics(pred, y, train_mean=evaluation_mean)
    print(
        f"train_mean={TRAIN_MEAN:.9g} -> control="
        f"{at_train['r2_vs_zero_of_constant_train_mean']:+.9f}; "
        f"evaluation_mean={evaluation_mean:.9g} -> control="
        f"{at_evaluation['r2_vs_zero_of_constant_train_mean']:+.9f}"
    )
    assert (
        at_train["r2_vs_zero_of_constant_train_mean"]
        != at_evaluation["r2_vs_zero_of_constant_train_mean"]
    ), (
        "two different train means produced the SAME control column, which is "
        "what a fallback to the evaluation set's own mean looks like"
    )
    assert at_train["r2_vs_zero_of_constant_train_mean"] == pytest.approx(
        _r2_vs_zero_of_a_constant(y, TRAIN_MEAN), rel=1e-12
    )
    assert at_evaluation["r2_vs_zero_of_constant_train_mean"] == pytest.approx(
        _r2_vs_zero_of_a_constant(y, evaluation_mean), rel=1e-12
    )
    # The evaluation mean is the ARGMIN of sum((y - c)**2), so substituting it
    # does not merely change the control -- it maximises it, i.e. it makes the
    # zero-skill baseline look as strong as it possibly can.
    assert (
        at_evaluation["r2_vs_zero_of_constant_train_mean"]
        > at_train["r2_vs_zero_of_constant_train_mean"]
    )
    # Everything else is unaffected -- the control column is the only place
    # the keyword is read.
    for key in FORECAST_METRIC_KEYS:
        if key != "r2_vs_zero_of_constant_train_mean":
            assert at_train[key] == at_evaluation[key], key
    # And a NaN train mean is refused rather than silently filling the slot.
    with pytest.raises(ValueError, match="train_mean"):
        forecast_metrics(pred, y, train_mean=float("nan"))


# --------------------------------------------------------------------------
# 7. One shared scorable row set
# --------------------------------------------------------------------------


def test_every_statistic_is_computed_on_one_shared_scorable_row_set():
    """NaNs in DIFFERENT places in `pred` and `y`: `n_scorable` must be the
    INTERSECTION, and every statistic must equal what the same function
    returns when handed the already-filtered arrays.

    The second half is the one with teeth. A count assertion alone would
    pass a module that masked correctly for R-squared and not for the IC.
    """
    data = _synthetic_scoring_set()
    y = data["y"].copy()
    pred = data["fitted"].copy()
    pred[[3, 7, 11, 5000]] = np.nan
    y[[7, 9, 13, 6000]] = np.nan  # index 7 overlaps deliberately
    finite_both = np.isfinite(pred) & np.isfinite(y)
    expected_n = int(finite_both.sum())
    print(
        f"pred NaNs=4 y NaNs=4 overlapping=1 -> expected n_scorable="
        f"{expected_n} of {N_ROWS}"
    )
    metrics = forecast_metrics(pred, y, train_mean=TRAIN_MEAN)
    assert metrics["n_scorable"] == expected_n == N_ROWS - 7
    prefiltered = forecast_metrics(
        pred[finite_both], y[finite_both], train_mean=TRAIN_MEAN
    )
    assert metrics == prefiltered, (
        "handing in the already-masked arrays must change nothing; a "
        "difference means at least one statistic saw a different row set"
    )
    assert set(metrics) == set(FORECAST_METRIC_KEYS)


# --------------------------------------------------------------------------
# 8. The refusals this module adds beyond the plan's list (deviation Rule 2)
# --------------------------------------------------------------------------


def test_an_undefined_statistic_is_refused_rather_than_returned_as_inf_or_nan():
    """The research note's code sample divides by `sum(y**2)` and by
    `SS_mean` unguarded. Both can be zero on a real segment -- an all-zero
    target (every mid unchanged) and a constant target -- and each would then
    return `inf` or `nan` into a metrics table a human reads as a result.
    Each says something a number cannot, so each is an exception.
    """
    zeros = np.zeros(8, dtype=np.float64)
    with pytest.raises(ValueError, match="exactly 0.0"):
        forecast_metrics(np.arange(8, dtype=np.float64), zeros, train_mean=0.0)
    constant_target = np.full(8, 3.0e-4)
    with pytest.raises(ValueError, match="SS_mean is 0"):
        forecast_metrics(
            np.arange(8, dtype=np.float64), constant_target, train_mean=0.0
        )
    with pytest.raises(ValueError, match="An empty score is not a score"):
        forecast_metrics(np.full(8, np.nan), np.full(8, np.nan), train_mean=0.0)


def test_a_misaligned_or_wrong_dtype_pair_is_refused_at_the_boundary():
    """`pred` and `y` are the same decision rows in the same order. A length
    disagreement is the likeliest shape of "scored against the wrong
    segment", and float32 is the likeliest shape of "came from somewhere
    other than the float64 prediction table"."""
    y = np.linspace(-1.0, 1.0, 16)
    with pytest.raises(ValueError, match="different frame"):
        forecast_metrics(np.zeros(15), y, train_mean=0.0)
    with pytest.raises(ValueError, match="must be float64"):
        forecast_metrics(np.zeros(16, dtype=np.float32), y, train_mean=0.0)
    with pytest.raises(ValueError, match="must be 1-D"):
        forecast_metrics(np.zeros((16, 1)), y, train_mean=0.0)
