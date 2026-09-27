"""sklearn's Ridge, checked against arithmetic that never imports it -- and
the trainer's coefficients, checked against a constructed truth.

WHY AN ORACLE AT ALL (T-07-24). `Ridge(solver="auto")` on dense float64 is
the centred normal equations and nothing else: `_preprocess_data` subtracts
the column means and the target mean, `_solve_cholesky` forms
`X_c.T @ X_c + alpha*I` and solves it, and `_set_intercept` recovers
`b = y_mean - x_mean @ w`. Every quantity in that is a function of the
`(p+1) x (p+1)` augmented Gram matrix and the `(p+1)` right-hand side, both
accumulable in ONE streaming pass -- so an independent implementation is
~15 lines and costs nothing to run. `ridge_gram_streaming` below is that
implementation, and it SHARES NO CODE WITH `models/regression.py`: the
independence is what makes it an oracle rather than a tautology
(`sim/reference.py`'s register, one layer up).

WHAT THE TOLERANCE MEANS. Research measured the same comparison on 7,854,835
real day-18 rows at alpha in {1e-6, 1, 100, 1e4}: maximum relative
coefficient difference 7.1e-15, absolute intercept difference 1.3e-19 --
float64 round-off, not a numerical difference. But it measured it against
`_solve_cholesky`'s TEXTUAL math and numpy's dense solve, because sklearn was
not installed in that session. These tests are therefore the first time the
comparison is real, and `rtol=1e-9` is loose by six orders of magnitude
against the measured figure: any failure here is a genuine disagreement, not
a tolerance argument.

Conditioning is why the normal equations are safe on this matrix, and that
was measured too: the raw three-feature Gram has condition number 712, and
after the FEAT-05 z-score it is 1.28 -- so squaring it leaves about 1.6. The
classic objection would bite if `mid` were in the matrix, which is one more
reason D-07-09 excludes it.

NOT ONE COEFFICIENT IS PINNED AS A LITERAL ANYWHERE IN THIS FILE
(correction C6). This host's numpy links Apple Accelerate and CI is
Linux/OpenBLAS, so a committed constant would be a guaranteed CI failure that
says nothing. Every claim here is either an agreement between two
computations in the same process, or a recovery of a truth this file
CONSTRUCTS.

X IS DELIBERATELY NOT ZERO-MEAN. Every column has a nonzero mean and the
target has an offset, because the centring terms are the part of the oracle
that a mutation can silently delete: on a zero-mean matrix
`- np.outer(sx, sx) / n` subtracts zero and the test would pass with the
centring removed.
"""

from __future__ import annotations

import numpy as np
import pytest
from sklearn.linear_model import Ridge as SklearnRidge

from models.frozen import poly2_design
from models.regression import (
    FEATURE_NAMES,
    TARGET_NAME,
    LinearRegressionTrainer,
    Poly2RidgeTrainer,
    RidgeTrainer,
)
from tests.fixtures import model_fit

#: Per-column means of the fixture design matrix. Nonzero, see the module
#: docstring -- the centring has to be exercised.
COLUMN_MEANS: tuple[float, ...] = (3.0, -2.0, 0.5)

#: The intercept the fixture target carries, likewise nonzero.
TARGET_OFFSET: float = 7.25

ALPHAS: tuple[float, ...] = (1e-6, 1.0, 100.0)


def ridge_gram_streaming(
    features: np.ndarray, target: np.ndarray, alpha: float, chunk: int = 1_000_000
) -> tuple[np.ndarray, float]:
    """`(coefficients, intercept)` of a ridge fit with an intercept, from
    SUFFICIENT STATISTICS ONLY.

    One pass, accumulating the augmented Gram `S = [X 1]^T [X 1]` (a
    `(p+1) x (p+1)` matrix) and `b = [X 1]^T y` (a `(p+1)` vector) in chunks,
    then the centred solve. For `p = 3` that is 4x4 + 4 numbers regardless of
    how many rows went in -- which is why the same routine is the practical
    answer at 44M rows, not only a test device.

    Written from the mathematics, importing nothing from
    `models.regression`: two implementations that shared a line would agree
    about that line whether or not it was right.
    """
    features = np.asarray(features, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    p = features.shape[1]
    gram = np.zeros((p + 1, p + 1), dtype=np.float64)
    rhs = np.zeros(p + 1, dtype=np.float64)
    for start in range(0, features.shape[0], chunk):
        block = features[start : start + chunk]
        block_target = target[start : start + chunk]
        augmented = np.empty((block.shape[0], p + 1), dtype=np.float64)
        augmented[:, :p] = block
        augmented[:, p] = 1.0
        gram += augmented.T @ augmented
        rhs += augmented.T @ block_target
    n = gram[p, p]
    sum_x = gram[:p, p]
    sum_y = rhs[p]
    centred_gram = gram[:p, :p] - np.outer(sum_x, sum_x) / n
    centred_rhs = rhs[:p] - sum_x * sum_y / n
    coefficients = np.linalg.solve(centred_gram + alpha * np.eye(p), centred_rhs)
    intercept = sum_y / n - (sum_x / n) @ coefficients
    return coefficients, float(intercept)


def _matrix(rows: int = 5_000, *, seed: int = 11) -> tuple[np.ndarray, np.ndarray]:
    """A normalised-scale design matrix with nonzero column means, and a
    target built from it plus noise. Not the fixture's learnable rig: this
    file is about arithmetic, and a wider signal makes a disagreement in the
    solve easier to attribute than a near-zero coefficient would."""
    rng = np.random.default_rng(seed)
    features = rng.standard_normal((rows, 3)) + np.asarray(COLUMN_MEANS)
    target = (
        TARGET_OFFSET
        + 1.5 * features[:, 0]
        - 0.75 * features[:, 1]
        + 0.25 * features[:, 2]
        + 0.5 * rng.standard_normal(rows)
    )
    return features, target


def _max_relative(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.max(np.abs(left - right) / np.maximum(np.abs(right), 1e-300)))


# ------------------------------------------------------------ the comparison


@pytest.mark.parametrize("alpha", ALPHAS)
def test_sklearn_ridge_coefficients_agree_with_the_streaming_gram_oracle_at_three_alphas(
    alpha,
):
    """The claim, at three alphas spanning eight decades. The OBSERVED
    maximum relative difference is printed, because that number is the
    finding -- "allclose passed" would hide a drift from 1e-15 to 1e-10."""
    features, target = _matrix()
    fitted = SklearnRidge(alpha=alpha, fit_intercept=True, solver="auto").fit(
        features, target
    )
    oracle_coef, oracle_intercept = ridge_gram_streaming(features, target, alpha)

    print(
        f"alpha={alpha:g}: max relative coefficient difference "
        f"{_max_relative(fitted.coef_, oracle_coef):.3e}, absolute intercept "
        f"difference {abs(fitted.intercept_ - oracle_intercept):.3e}"
    )
    assert np.allclose(fitted.coef_, oracle_coef, rtol=1e-9, atol=0.0)
    assert np.allclose(fitted.intercept_, oracle_intercept, rtol=1e-9, atol=1e-12)
    # Anti-vacuity of the fixture itself: a matrix whose coefficients were
    # zero would make any agreement trivial.
    assert np.min(np.abs(oracle_coef)) > 0.1
    assert np.min(np.abs(np.asarray(COLUMN_MEANS))) > 0.0, (
        "the centring terms are inert on a zero-mean matrix"
    )


def test_the_oracle_and_sklearn_disagree_when_alpha_disagrees():
    """Anti-vacuity for the comparison: without it, an "oracle" that simply
    returned sklearn's own answer would pass the test above at every alpha.

    alpha 1.0 against alpha 100.0 on the same matrix must NOT be close -- and
    the difference is reported, so a shrinkage too small to matter would be
    visible rather than merely "not close".
    """
    features, target = _matrix()
    fitted = SklearnRidge(alpha=1.0, fit_intercept=True, solver="auto").fit(
        features, target
    )
    mismatched, mismatched_intercept = ridge_gram_streaming(features, target, 100.0)
    print(
        "alpha mismatch (sklearn 1.0 vs oracle 100.0): max relative "
        f"difference {_max_relative(fitted.coef_, mismatched):.3e}"
    )
    assert not np.allclose(fitted.coef_, mismatched, rtol=1e-9, atol=0.0)
    assert not np.allclose(fitted.intercept_, mismatched_intercept, rtol=1e-9)
    # And the same alpha DOES agree, so the disagreement above is about alpha
    # and not about the comparison being broken in general.
    matched, _intercept = ridge_gram_streaming(features, target, 1.0)
    assert np.allclose(fitted.coef_, matched, rtol=1e-9, atol=0.0)


def test_the_streaming_oracle_is_chunk_size_invariant():
    """A chunk-boundary bug is exactly what a streaming accumulator gets
    wrong -- and it would be invisible in the test above, which uses one
    chunk for the whole matrix."""
    features, target = _matrix()
    whole, whole_intercept = ridge_gram_streaming(
        features, target, 1.0, chunk=features.shape[0]
    )
    chunked, chunked_intercept = ridge_gram_streaming(
        features, target, 1.0, chunk=1_000
    )
    ragged, ragged_intercept = ridge_gram_streaming(features, target, 1.0, chunk=997)
    print(
        "chunk invariance: max relative difference "
        f"{max(_max_relative(chunked, whole), _max_relative(ragged, whole)):.3e}"
    )
    assert np.allclose(chunked, whole, rtol=1e-12, atol=0.0)
    assert np.allclose(ragged, whole, rtol=1e-12, atol=0.0)
    assert chunked_intercept == pytest.approx(whole_intercept, rel=1e-12)
    assert ragged_intercept == pytest.approx(whole_intercept, rel=1e-12)
    # 997 does not divide 5,000, so the last block is short -- the case a
    # fixed-stride accumulator mishandles.
    assert features.shape[0] % 997 != 0


# ----------------------------------- the same check through the PRODUCTION path


def _identity_cache(tmp_path, features: np.ndarray, target: np.ndarray):
    """A cache plus an IDENTITY normalisation artifact, so the design matrix
    a trainer builds is exactly `features` and a coefficient can be compared
    with something computed outside the fit."""
    columns = {name: features[:, index] for index, name in enumerate(FEATURE_NAMES)}
    columns[TARGET_NAME] = target
    manifest_id = model_fit.write_normalization(
        model_fit.identity_params(FEATURE_NAMES, rows=features.shape[0]),
        lake_root=tmp_path / "lake",
        registry_root=tmp_path / "registry",
        train_row_count=features.shape[0],
    )
    cache_path = model_fit.write_cache(tmp_path / "cache" / "oracle.parquet", columns)
    return model_fit.make_fit_inputs(cache_path, manifest_id)


def test_the_ridge_trainers_own_coefficients_agree_with_the_oracle(tmp_path):
    """The comparison that covers the code this plan actually wrote.

    The test above compares sklearn with the oracle and would pass on a
    trainer that reversed `coef_`, dropped the intercept or normalised twice
    -- none of which is sklearn's business. This one routes the same matrix
    through `RidgeTrainer.fit`, reads the FROZEN body, and checks that
    against the oracle.
    """
    features, target = _matrix(rows=3_000)
    inputs = _identity_cache(tmp_path, features, target)
    predictor = RidgeTrainer(alpha=1.0, context=model_fit.make_context(tmp_path)).fit(
        inputs
    )

    oracle_coef, oracle_intercept = ridge_gram_streaming(features, target, 1.0)
    observed = _max_relative(np.asarray(predictor.coef), oracle_coef)
    print(f"RidgeTrainer.fit vs oracle: max relative difference {observed:.3e}")
    assert np.allclose(np.asarray(predictor.coef), oracle_coef, rtol=1e-9, atol=0.0)
    assert predictor.intercept == pytest.approx(oracle_intercept, rel=1e-9)
    # The coefficients must be in FEATURE_NAMES order, not sorted or
    # reversed: the oracle's columns are in that order by construction, so a
    # permutation shows up as a failure above rather than as a shrug.
    assert predictor.feature_names == FEATURE_NAMES
    assert predictor.n_rows_fitted == features.shape[0]


def test_the_ols_trainer_recovers_a_constructed_truth_exactly(tmp_path):
    """Coefficient FIDELITY, with no library on the other side of the
    comparison: a noiseless `y = 7.25 + 2*x0 - x1 + 0.5*x2` must come back as
    (2, -1, 0.5) and 7.25.

    This is the test that fails if `fit` ever reverses, rounds or re-scales
    what sklearn returned -- and the truth is CONSTRUCTED here, so no
    coefficient literal is pinned against a particular BLAS (correction C6).
    """
    features, _noisy = _matrix(rows=2_000)
    truth = np.asarray([2.0, -1.0, 0.5])
    target = TARGET_OFFSET + features @ truth
    inputs = _identity_cache(tmp_path, features, target)

    predictor = LinearRegressionTrainer(context=model_fit.make_context(tmp_path)).fit(
        inputs
    )
    error = float(np.max(np.abs(np.asarray(predictor.coef) - truth)))
    print(f"OLS exact recovery: max absolute coefficient error {error:.3e}")
    assert np.allclose(np.asarray(predictor.coef), truth, rtol=0.0, atol=1e-10)
    assert predictor.intercept == pytest.approx(TARGET_OFFSET, abs=1e-9)
    # A prediction from the FROZEN body reproduces the target it was built
    # from -- the numpy-only path, on the fit's own coefficients.
    assert np.allclose(predictor.predict(features), target, rtol=0.0, atol=1e-9)


def test_the_poly2_trainer_recovers_a_nine_term_truth_in_sklearns_column_order(
    tmp_path,
):
    """The same fidelity check for the expansion, which is where an order
    mistake would be invisible: nine coefficients, and the only way they can
    all come back is if the trainer's expansion and
    `models.frozen.poly2_design` agree column for column.

    `rtol=1e-4` rather than exact, because the smallest grid alpha (1e-3)
    really does shrink the fit -- MEASURED at 1.5e-5 relative on this matrix,
    which is ridge doing its job, not a defect. Shrinkage is a function of
    the data and alpha, not of the BLAS, so the 6x headroom is stable across
    hosts; a permuted column would show up as an O(1) relative error, four
    orders past this bound.
    """
    features, _noisy = _matrix(rows=2_000)
    truth = np.asarray([2.0, -1.0, 0.5, 0.7, -0.3, 0.2, 0.1, 0.4, -0.6])
    assert truth.size == 9
    target = TARGET_OFFSET + poly2_design(features) @ truth
    inputs = _identity_cache(tmp_path, features, target)

    predictor = Poly2RidgeTrainer(
        alpha=1e-3, context=model_fit.make_context(tmp_path)
    ).fit(inputs)
    observed = _max_relative(np.asarray(predictor.coef), truth)
    print(f"poly2 recovery at alpha=1e-3: max relative error {observed:.3e}")
    assert len(predictor.coef) == 9
    assert np.allclose(np.asarray(predictor.coef), truth, rtol=1e-4, atol=0.0)
    assert predictor.design == "poly2"
    # Every term is distinct in magnitude, so a permuted column pairs a
    # coefficient with the wrong term and the assertion above fails.
    assert len(set(np.abs(truth).tolist())) == 9
