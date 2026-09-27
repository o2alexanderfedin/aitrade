"""D-07-09 and D-07-23's refusal: no raw price level ever reaches an
estimator, asserted THREE ways because no single assertion sees all the ways
one can get in.

1. A feature list naming `mid`, `bid_price` or `ask_price` RAISES -- the
   refusal itself, not the omission. It fires before any data is read, which
   is why the tests below hand in a cache path that does not exist and still
   expect the raise.
2. The FITTED coefficient vector has exactly three entries, in the pinned
   order `("imb_top", "ofi", "trade_flow")`. This one catches a fourth
   column that arrived past the name check.
3. The degree-2 design matrix has EXACTLY nine columns. This is the only one
   of the three that can see a price column entering through an INTERACTION
   term: a fourth input leaves the three input NAMES untouched and the
   expansion at 14 columns, so neither of the other two assertions moves.

The reason (3) is not paranoia: `mid` IS in the `features_norm` artifact --
the normalisation fit covers every catalogue feature -- so "it came from the
normalisation artifact" is not a sufficient filter (research assumption A5).
`test_the_normalization_artifact_may_carry_mid_and_the_fit_still_refuses_to_
use_it` builds exactly that artifact and fits against it.

Every test writes to its own `tmp_path` lake and registry through
`tests/fixtures/model_fit.py`, and none reaches a canonical root: pre-commit
hook 19 runs the full suite on every commit, so a test that did would spend
an irreversible validation look once per commit forever (D-07-34).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from features.normalize import load_normalization, normalization_dataset
from models.frozen import poly2_design
from models.regression import (
    FEATURE_NAMES,
    FORBIDDEN_FEATURE_NAMES,
    POLY2_COLUMN_COUNT,
    TARGET_NAME,
    ElasticNetTrainer,
    FeatureAllowlistError,
    LinearRegressionTrainer,
    Poly2RidgeTrainer,
    RidgeTrainer,
)
from tests.fixtures import model_fit

#: One cheap constructor per estimator class -- the four of D-07-08, so a
#: refusal removed from ONE of them (or from a `fit` a subclass overrides
#: later) fails here rather than at the first fold of a real sweep.
TRAINER_FACTORIES: dict[str, dict] = {
    "LinearRegression": {"class": LinearRegressionTrainer, "knobs": {}},
    "Ridge": {"class": RidgeTrainer, "knobs": {"alpha": 1.0}},
    "ElasticNet": {
        "class": ElasticNetTrainer,
        "knobs": {"alpha": 1e-6, "l1_ratio": 0.5},
    },
    "Ridge+poly2": {"class": Poly2RidgeTrainer, "knobs": {"alpha": 1e-3}},
}


def _trainer(label: str, tmp_path: Path):
    factory = TRAINER_FACTORIES[label]
    return factory["class"](
        context=model_fit.make_context(tmp_path), **factory["knobs"]
    )


# ---------------------------------------------------------------- refusal 1


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_FEATURE_NAMES))
@pytest.mark.parametrize("label", sorted(TRAINER_FACTORIES))
def test_a_feature_list_naming_mid_or_bid_price_or_ask_price_raises(
    label, forbidden, tmp_path
):
    """D-07-23's first refusal, over every forbidden name and every
    estimator.

    THE CACHE PATH DOES NOT EXIST, deliberately. The raise must come from the
    NAMES, before a single byte is read -- if the check had drifted to "scan
    the fitted coefficients afterwards", this test would fail with a
    file-not-found instead, and the price would already have been in the
    matrix.
    """
    names = (forbidden, *FEATURE_NAMES[1:])
    inputs = model_fit.make_fit_inputs(
        tmp_path / "no-such-cache.parquet",
        "0" * 64,
        feature_names=names,
    )
    with pytest.raises(FeatureAllowlistError) as excinfo:
        _trainer(label, tmp_path).fit(inputs)
    message = str(excinfo.value)
    assert forbidden in message, message
    assert "day's trend" in message, message


@pytest.mark.parametrize(
    "names",
    [
        ("ofi", "imb_top", "trade_flow"),
        ("imb_top", "ofi"),
        ("imb_top", "ofi", "trade_flow", "trade_flow"),
        (),
    ],
    ids=["permuted", "short", "duplicated", "empty"],
)
def test_a_feature_list_that_is_not_exactly_the_three_pinned_names_raises(
    names, tmp_path
):
    """The other half of the name check. A PERMUTATION is the dangerous
    member of this list: every name is allowed, the count is right, and the
    frozen predictor would rebuild the design matrix positionally -- pairing
    every coefficient with the wrong input while still predicting plausible
    numbers.
    """
    inputs = model_fit.make_fit_inputs(
        tmp_path / "no-such-cache.parquet", "0" * 64, feature_names=names
    )
    with pytest.raises(FeatureAllowlistError, match="not exactly"):
        _trainer("Ridge", tmp_path).fit(inputs)


def test_a_diagnostic_horizon_is_refused_as_a_target_naming_itself(tmp_path):
    """D-07-10: `ret_1s_mid`, `ret_1min_mid` and `ret_10min_mid` are
    REPORTED alongside and never fitted in this phase. A fit on one of them
    scored against the 10-second gate would look like a modelling result and
    be a units mistake."""
    inputs = model_fit.make_fit_inputs(
        tmp_path / "no-such-cache.parquet", "0" * 64, target_name="ret_1min_mid"
    )
    with pytest.raises(FeatureAllowlistError, match="DIAGNOSTIC"):
        _trainer("Ridge", tmp_path).fit(inputs)


# ---------------------------------------------------------------- refusal 2


@pytest.mark.parametrize("label", ["LinearRegression", "Ridge", "ElasticNet"])
def test_the_fitted_linear_coefficient_vector_has_exactly_three_entries_in_the_pinned_order(
    label, tmp_path
):
    """D-07-23's second refusal, MEASURED on a fit rather than argued from
    the input list: three coefficients, and `feature_names` is the pinned
    order."""
    inputs, _columns = model_fit.learnable_fit_inputs(tmp_path, rows=2_000)
    predictor = _trainer(label, tmp_path).fit(inputs)
    assert len(predictor.coef) == 3, predictor.coef
    assert predictor.feature_names == ("imb_top", "ofi", "trade_flow")
    assert predictor.design == "linear"
    assert np.all(np.isfinite(np.asarray(predictor.coef)))


# ---------------------------------------------------------------- refusal 3


def test_the_degree_two_design_matrix_has_exactly_nine_columns(tmp_path):
    """D-07-23's third refusal, and the ONLY one that can see a price column
    entering through an interaction term.

    Neither of the other two can: a fourth input leaves the three allowed
    NAMES in place (so the name check passes) and would be reported as
    `len(coef) == 14` rather than as a forbidden name -- a number nobody is
    looking at unless a test pins it. Nine is 3 linear + 3 squared + 3 cross:
    not 6 (which would drop the squares) and not 10 (which would add the bias
    column `include_bias=False` suppresses).
    """
    inputs, _columns = model_fit.learnable_fit_inputs(tmp_path, rows=2_000)
    predictor = _trainer("Ridge+poly2", tmp_path).fit(inputs)
    assert len(predictor.coef) == 9
    assert POLY2_COLUMN_COUNT == 9
    assert predictor.design == "poly2"
    assert predictor.feature_names == ("imb_top", "ofi", "trade_flow")
    # The INPUT names stay three even though the design is nine wide -- which
    # is exactly why the coefficient count of refusal 2 cannot be the check
    # here, and why this one is a separate assertion.
    assert len(predictor.feature_names) == 3


def test_the_nine_column_count_fires_when_a_fourth_column_reaches_the_expansion(
    tmp_path,
):
    """Anti-vacuity for refusal 3: the count must be able to FAIL.

    Handed a four-column matrix -- the shape a smuggled `mid` would produce
    -- the expansion is 14 columns wide and the trainer refuses, naming both
    counts. Without this test, `len(coef) == 9` on a three-column fit would
    pass forever even if the check had been deleted.
    """
    trainer = _trainer("Ridge+poly2", tmp_path)
    four_columns = np.arange(40, dtype=np.float64).reshape(10, 4)
    with pytest.raises(FeatureAllowlistError) as excinfo:
        trainer._design(four_columns)
    message = str(excinfo.value)
    assert "14" in message and "9" in message, message
    assert "interaction term" in message, message
    # And the arithmetic the refusal is checking, stated independently: four
    # inputs give 4 + 10 = 14, three give 3 + 6 = 9.
    assert poly2_design(four_columns).shape[1] == 14
    assert poly2_design(four_columns[:, :3]).shape[1] == 9


def test_the_normalization_artifact_may_carry_mid_and_the_fit_still_refuses_to_use_it(
    tmp_path,
):
    """Research assumption A5, mechanised: the real `features_norm` artifact
    is fit over every catalogue feature, `mid` included, so provenance
    through the artifact is not a filter.

    The artifact here carries FOUR rows. The fit reads three of them, and
    `mid`'s mean and standard deviation sit there unread.
    """
    inputs, _columns = model_fit.learnable_fit_inputs(
        tmp_path, rows=2_000, include_mid_in_artifact=True
    )
    artifact = load_normalization(
        inputs.normalization_manifest_id,
        normalization_dataset(model_fit.SYMBOL),
        registry_root=tmp_path / "registry",
        lake_root=tmp_path / "lake",
    )
    assert sorted(artifact.params) == ["imb_top", "mid", "ofi", "trade_flow"], (
        "vacuous: the artifact this test is about does not carry a mid row"
    )
    predictor = _trainer("Ridge", tmp_path).fit(inputs)
    assert len(predictor.coef) == 3
    assert predictor.feature_names == FEATURE_NAMES
    assert "mid" not in predictor.feature_names


# ------------------------------------------- the fit boundary's own refusals


def test_a_frame_with_some_non_finite_rows_fits_and_reports_the_dropped_count(
    tmp_path,
):
    """Non-finite rows are MASKED and COUNTED, never imputed and never
    fatal: the `train` role is not admission-gated and retains all feature
    nulls (55,843 on 2026-09-12 alone), so a refusal here would refuse the
    phase's own training window.

    The counts are BODY fields on the predictor, not hyperparameters: inside
    the hashed recipe they would give every OOF block's fit of one config a
    different `predictor_id`, destroying the deduplication D-07-20 relies on.
    """
    planted = 37
    inputs, _columns = model_fit.learnable_fit_inputs(
        tmp_path, rows=2_000, nan_rows=planted
    )
    predictor = _trainer("Ridge", tmp_path).fit(inputs)
    assert predictor.n_rows_dropped == planted
    assert predictor.n_rows_fitted == 2_000 - planted
    assert "n_rows_dropped" not in predictor.hyperparameters
    assert "n_rows_fitted" not in predictor.hyperparameters
    body = predictor.to_artifact()
    assert body["n_rows_dropped"] == planted
    # The dropped rows are excluded from the zero-skill control's mean too,
    # which is the only place that number can be captured honestly.
    assert np.isfinite(predictor.train_target_mean)


def test_a_frame_whose_every_row_is_non_finite_raises(tmp_path):
    """Masking is not the same as tolerating: one non-finite row is a
    measurement, all of them is a broken cache."""
    from models.regression import FitDataError

    rows = 200
    columns = model_fit.learnable_columns(rows)
    columns[TARGET_NAME][:] = np.nan
    manifest_id = model_fit.write_normalization(
        model_fit.measured_params({name: columns[name] for name in FEATURE_NAMES}),
        lake_root=tmp_path / "lake",
        registry_root=tmp_path / "registry",
        train_row_count=rows,
    )
    cache = model_fit.write_cache(
        tmp_path / "cache" / "all-nan.parquet", columns, nan_to_null=True
    )
    with pytest.raises(FitDataError, match="non-finite"):
        _trainer("Ridge", tmp_path).fit(model_fit.make_fit_inputs(cache, manifest_id))


def test_a_row_mask_of_the_wrong_height_raises_naming_both_counts(tmp_path):
    """The mask is applied POSITIONALLY and never joined -- a join's output
    order is unspecified in polars 1.41.2 (T-07-17). An unequal height is
    therefore unreconcilable and must be refused rather than guessed at."""
    from models.regression import FitDataError

    inputs, _columns = model_fit.learnable_fit_inputs(tmp_path, rows=500)
    mask_path = model_fit.write_row_mask(
        tmp_path / "mask.parquet", np.ones(499, dtype=bool)
    )
    masked = model_fit.make_fit_inputs(
        inputs.cache_path, inputs.normalization_manifest_id, row_mask_path=mask_path
    )
    with pytest.raises(FitDataError) as excinfo:
        _trainer("Ridge", tmp_path).fit(masked)
    assert "499" in str(excinfo.value) and "500" in str(excinfo.value)


def test_a_row_mask_selects_positionally_and_the_fitted_count_is_its_sum(tmp_path):
    """The mask's live counterpart: the same cache, two masks, and
    `n_rows_fitted` follows the mask rather than the cache.

    The two fits must also DISAGREE on coefficients -- otherwise a mask that
    was read and thrown away would pass the count assertion (the count could
    come from anywhere) and this test would say nothing about selection.
    """
    rows = 1_000
    inputs, _columns = model_fit.learnable_fit_inputs(tmp_path, rows=rows)
    keep = np.zeros(rows, dtype=bool)
    keep[: rows // 2] = True
    first_half = model_fit.write_row_mask(tmp_path / "first.parquet", keep)
    second_half = model_fit.write_row_mask(tmp_path / "second.parquet", ~keep)

    trainer = _trainer("Ridge", tmp_path)
    head = trainer.fit(
        model_fit.make_fit_inputs(
            inputs.cache_path,
            inputs.normalization_manifest_id,
            row_mask_path=first_half,
        )
    )
    tail = trainer.fit(
        model_fit.make_fit_inputs(
            inputs.cache_path,
            inputs.normalization_manifest_id,
            row_mask_path=second_half,
        )
    )
    assert head.n_rows_fitted == rows // 2
    assert tail.n_rows_fitted == rows - rows // 2
    assert head.coef != tail.coef, (
        "vacuous: both halves produced identical coefficients, so this test "
        "cannot tell a mask that was applied from one that was read and "
        "discarded"
    )
    # Same recipe, different rows: D-07-22's whole point -- one
    # `predictor_id`, two bodies.
    assert head.predictor_id == tail.predictor_id
