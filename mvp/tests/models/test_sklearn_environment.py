"""Executes two research assumptions that were READ from source but never
RUN (`07-RESEARCH.md` Assumptions Log, A1 and A3).

A1 -- "sklearn 1.9.1's executed behaviour matches its 1.9.1 source as read
(Ridge `auto` -> `cholesky`)". The phase's whole "no seed changes any Stage-1
result" conclusion rests on it: `random_state` is documented as consumed only
by the `sag`/`saga` solvers, so it is inert exactly as long as `auto` keeps
resolving to `cholesky` on dense float64 input.

A1's family, added by plan 07-04 -- "`PolynomialFeatures(degree=2,
include_bias=False)` emits the three linear terms and then the degree-2 terms
in the upper-triangular sweep". Read from the 1.9.1 source
(`combinations_with_replacement`), never executed, and
`models.frozen.poly2_design` rebuilds that design matrix from the stored
coefficients WITHOUT sklearn. A silently different column order would pair
every coefficient with the wrong term and still predict plausible numbers, so
the order is asserted here rather than assumed there. This is the only place
in `tests/models/` that may import sklearn to check it:
`test_frozen_no_sklearn.py`'s whole claim is that it does not.

A3 -- "`scipy.stats.spearmanr(...).statistic` is the accessor name in 1.18.1".
Its NaN-on-constant-input half is what plan 07-07's gate depends on
(D-07-32): a rank IC of 0.0 on a constant prediction vector is a number you
could report; a NaN is a refusal you have to handle.

NO COEFFICIENT VALUE IS ASSERTED AS A LITERAL (correction C6). This host
links Apple Accelerate, CI is Linux/OpenBLAS, and a pinned float would fail
in CI for a reason that has nothing to do with the code. Every assertion here
is about a solver name, an equality BETWEEN two fits on one host, or a NaN.

The A1/A3 tests are pure numpy in, scalars out: no lake, no registry, no
MLflow, no fixture data, so nothing there can spend a validation look.

The last two tests are about the OTHER half of this directory's environment
-- `conftest.py`'s tracking-root isolation. They live here rather than in a
third file because 07-01-PLAN.md's verification pins this directory's
contents to exactly `conftest.py` and this module; without them the new
`tracking_root` fixture and its `exist_ok=True` repair would ship with no
test requesting them at all, which is how the defect they fix survived in
`tests/harness/conftest.py` in the first place.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import ConstantInputWarning, spearmanr
from sklearn.linear_model import Ridge
from sklearn.preprocessing import PolynomialFeatures

from data.lake_paths import DEFAULT_MLFLOW_TRACKING_ROOT, mlflow_tracking_root
from models.frozen import poly2_design


def _dense_float64_design() -> tuple[np.ndarray, np.ndarray]:
    """A small dense float64 `(40, 3)` design and a linear target. Seeded
    locally so the data is fixed; the point of the tests below is that
    nothing about the FIT depends on a seed."""
    rng = np.random.default_rng(7)
    x = rng.standard_normal((40, 3)).astype(np.float64)
    y = (x @ np.array([0.5, -0.25, 2.0], dtype=np.float64) + 0.1).astype(np.float64)
    return x, y


def test_ridge_on_dense_float64_resolves_to_the_cholesky_solver():
    x, y = _dense_float64_design()
    fitted = Ridge(alpha=1.0).fit(x, y)
    assert fitted.solver_ == "cholesky"


def test_two_ridge_fits_with_different_random_state_give_identical_coefficients():
    """`random_state` is documented as used only by `sag`/`saga`. Since the
    test above pins the solver to `cholesky`, changing it must change
    nothing -- bit-for-bit, hence `array_equal` rather than `allclose`."""
    x, y = _dense_float64_design()
    a = Ridge(alpha=1.0, random_state=0).fit(x, y)
    b = Ridge(alpha=1.0, random_state=12345).fit(x, y)

    assert a.solver_ == b.solver_ == "cholesky"
    assert np.array_equal(a.coef_, b.coef_)
    assert a.intercept_ == b.intercept_


def test_sklearns_degree_two_expansion_has_the_nine_columns_frozen_py_documents():
    """The ORDER, in sklearn's own words. `get_feature_names_out` is the
    statement of record: three linear terms, then `x0^2, x0 x1, x0 x2, x1^2,
    x1 x2, x2^2` -- nine for three inputs, not six (which would drop the
    squares) and not ten (which would add the bias column `include_bias=False`
    does not emit)."""
    poly = PolynomialFeatures(degree=2, include_bias=False)
    x, _y = _dense_float64_design()
    poly.fit(x)

    assert list(poly.get_feature_names_out(["x0", "x1", "x2"])) == [
        "x0",
        "x1",
        "x2",
        "x0^2",
        "x0 x1",
        "x0 x2",
        "x1^2",
        "x1 x2",
        "x2^2",
    ]


def test_frozen_poly2_design_reproduces_sklearns_expansion_bit_for_bit():
    """`models.frozen.poly2_design` is what re-evaluates a degree-2 body
    without sklearn, so its column order has to BE sklearn's, not merely
    resemble it. `array_equal`, not `allclose`: both sides are the same
    element-wise products of the same float64 bytes.
    """
    x, _y = _dense_float64_design()
    expected = PolynomialFeatures(degree=2, include_bias=False).fit_transform(x)
    actual = poly2_design(x)

    assert actual.shape == (40, 9) == expected.shape
    assert np.array_equal(actual, expected)

    # Anti-vacuity: the nine columns are pairwise distinct, so the equality
    # above could have failed on a permutation. With two identical columns a
    # swap would be invisible and this would assert only that 9 == 9.
    for left in range(9):
        for right in range(left + 1, 9):
            assert not np.array_equal(expected[:, left], expected[:, right])
    permuted = expected[:, [0, 1, 2, 3, 5, 4, 6, 7, 8]]
    assert not np.array_equal(permuted, expected)


def test_spearmanr_exposes_statistic_and_returns_nan_for_a_constant_input():
    """Two halves, deliberately in two calls.

    The first is A3 proper: the 1.18.1 return value is a
    `SignificanceResult` exposing `.statistic`, not a bare tuple to unpack.

    The second is D-07-32's dependency, and it needs its own call because
    `simplefilter("error", ...)` turns the warning into a raise -- under that
    filter `spearmanr` never returns, so the NaN cannot be observed in the
    same call that proves the warning fires. Raising first proves scipy
    classifies the input as constant rather than silently computing; the
    second call, with the warning ignored, proves the value it would have
    returned is NaN and not 0.0.
    """
    varying_a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    varying_b = np.array([2.0, 1.0, 4.0, 3.0, 5.0])
    result = spearmanr(varying_a, varying_b)
    assert isinstance(result.statistic, float)
    assert np.isfinite(result.statistic)

    constant = np.ones(5, dtype=np.float64)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConstantInputWarning)
        with pytest.raises(ConstantInputWarning):
            spearmanr(constant, varying_b)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConstantInputWarning)
        constant_result = spearmanr(constant, varying_b)
    assert np.isnan(constant_result.statistic)


def test_canonical_tracking_root_resolves_inside_tmp_path_with_no_fixture_asked(
    tmp_path: Path,
):
    """The guarantee `conftest.isolated_canonical_tracking_root` exists to
    give, stated as the thing that was FALSE before the repair: a models test
    that asks for no tracking fixture at all still resolves the canonical
    root to its own `tmp_path`, never to the real store. Measured on the
    unrepaired `tests/harness/conftest.py`, the same probe returned
    `/Volumes/ProjectsSSD/aihedgefund/mlflow`.

    Pre-commit hook 19 runs this whole suite on every commit, so "resolves to
    the real store" is a per-commit cost, not a per-experiment one
    (D-07-34)."""
    resolved = mlflow_tracking_root(None)

    assert resolved == (tmp_path / "mlflow_root").resolve()
    assert resolved != Path(DEFAULT_MLFLOW_TRACKING_ROOT).resolve()


def test_tracking_root_fixture_initialises_a_real_store_without_colliding(
    tracking_root: Path,
):
    """The `tracking_root` fixture hands back an INITIALISED MLflow SQLite
    store, and asking for it does not raise `FileExistsError` even though the
    autouse fixture already created that same directory -- the `exist_ok=True`
    half of the repair, which a bare `mkdir()` would fail on every time."""
    assert (tracking_root / "mlflow.db").exists()
    assert mlflow_tracking_root(None) == tracking_root.resolve()
