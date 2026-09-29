"""D-07-08's four scikit-learn estimators behind the `Trainer` protocol, and
D-07-09's refusal that keeps a raw price level out of the design matrix.

THIS IS THE ONE MODULE IN `models/` THAT IMPORTS sklearn, and that is a
CONTRACT rather than an accident of what each file happened to need.
`models/frozen.py` is numpy-only so that D-07-17's proof is possible at all
-- a coefficient JSON re-evaluated by a bare `intercept + X @ coef` in a
process where `"sklearn" is not in sys.modules`. `models/predictions.py`,
`models/conversion.py` and `models/protocol.py` never touch an estimator
either. `tests/models/test_frozen_no_sklearn.py` asserts the ban over
`frozen.py`'s IMPORT NODES; the verification block of 07-06 asserts the
converse by grep, that this file is the only importer in the package.

WHAT THIS MODULE DELIBERATELY DOES NOT DO:

1. IT DOES NOT SEARCH. `GRID` is a hand-written, COUNTED, module-level
   literal of 36 configurations (D-07-08). No Optuna, no sampler, no
   `itertools.product` -- FCST-05 and the black-box HPO it names are Phase
   8's territory, and a grid a reader cannot count is a grid whose
   selection-bias budget nobody can count either.
2. IT DOES NOT FIT NORMALISATION. Parameters are resolved from the FEAT-05
   train-only artifact by manifest id and applied frozen (D-07-11). A fit
   that recomputed a mean would make every OOF block's parameters a
   function of the block, which is the leak the artifact exists to close.
3. IT DOES NOT IMPUTE. A non-finite row is MASKED out of the fit and
   COUNTED (`n_rows_fitted` / `n_rows_dropped`), in
   `features.normalize.fit_normalization`'s own words: "a null imputed to
   zero is a made-up observation that moves both the mean and the
   variance". The `train` role is never admission-gated and retains all
   feature nulls (55,843 on 2026-09-12 alone), so refusing them instead
   would refuse the phase's own training window.
4. IT DOES NOT READ A PRICE. `mid`, `bid_price` and `ask_price` are
   BOOKKEEPING. `validate_feature_names` REFUSES them by name, and the
   refusal is scoped to the estimator's inputs rather than to the module
   (D-07-23: `models.conversion` is the one place `mid` is read, to turn a
   predicted return into a price). The `features_norm` artifact carries a
   `mid` row -- so "it came from the normalisation artifact" is not a
   sufficient filter (research assumption A5), and the count assertion on
   the degree-2 expansion is the only one of D-07-23's three refusals that
   can see a price column entering through an INTERACTION term.
5. IT DOES NOT JOIN. A row mask is a Boolean column applied POSITIONALLY at
   equal height. A join's output order is unspecified in polars 1.41.2 and
   is this phase's top risk (T-07-17).

WHY BOTH RIDGE TRAINERS REPORT THE BYTE-IDENTICAL `model_class`
`"sklearn.Ridge"`. The plain Ridge and the degree-2 Ridge share three alpha
values (1e-3, 1.0, 100.0), the same seed, the same code hash and the same
normalisation manifest id. `models.predictor_id.predictor_id` hashes exactly
D-07-14's five fields, and `design` is not one of them -- so `degree: 2`
living INSIDE the poly2 trainer's `hyperparameters` is the ONLY thing
separating those three pairs. Naming the fourth estimator
`"sklearn.Ridge+poly2"` would separate them by class string instead, which
reads as harmless and is not: the `degree` key could then go missing without
a single id colliding, `harness.negative_log` would deduplicate away up to
three distinct ineligible configs (falsifying D-07-20), and a stored table's
`predictor=<first 16 chars>` directory would name a config that never
produced it. `tests/models/test_protocol_conformance.py` asserts the class
strings are equal AND that stripping `degree` collapses 36 distinct ids to
27 -- one collapse per poly2 alpha, because every poly2 alpha is ALSO a plain
Ridge alpha by construction (`poly2 < plain`, asserted). With a distinct
class string that collapse would not happen and the test
would pass for the wrong reason. The expressiveness the pin costs a run
manifest is bought back by the additive `design` tag plan 07-07 logs.

WHY `FitContext` EXISTS, AND WHY THE ROOTS ARE NOT HYPERPARAMETERS.
`FitInputs` (D-07-04's protocol, deliberately not edited here) names a cache
path, column names, a normalisation manifest id and a seed -- everything
that is about the DATA. Resolving that manifest id additionally needs a
symbol, a registry root and a lake root, and the frozen predictor needs the
run's code hash; none of them is data, and every one of them differs between
a test's `tmp_path` and a real run. They are therefore CONSTRUCTOR state,
and they are kept out of `hyperparameters` on purpose: a root inside the
hashed recipe would make `predictor_id` machine-dependent, and `code_hash`
is already a recipe field in its own right. No canonical-root fallback
exists, deliberately -- a trainer that silently reached
`data.lake_paths`' real registry when a test forgot to pass one is exactly
the accident pre-commit hooks 18/19 (the full suite on every commit) would
then pay for once per commit, forever (D-07-34).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

import numpy as np
import polars as pl
from sklearn.linear_model import ElasticNet as SklearnElasticNet
from sklearn.linear_model import LinearRegression as SklearnLinearRegression
from sklearn.linear_model import Ridge as SklearnRidge
from sklearn.preprocessing import PolynomialFeatures

from features.normalize import (
    apply_normalization,
    load_normalization,
    normalization_dataset,
)
from models.frozen import FEATURE_NAMES as FROZEN_FEATURE_NAMES
from models.frozen import (
    SUPPORTED_DEGREE,
    FrozenLinearPredictor,
    expected_coefficient_count,
)
from models.protocol import FitInputs
from spec.catalogue import get_feature, get_label

__all__ = [
    "DIAGNOSTIC_TARGET_NAMES",
    "ELASTIC_NET_MAX_ITER",
    "ELASTIC_NET_MODEL_CLASS",
    "ELASTIC_NET_TOL",
    "FEATURE_NAMES",
    "FORBIDDEN_FEATURE_NAMES",
    "GRID",
    "GRID_SIZE",
    "LINEAR_REGRESSION_MODEL_CLASS",
    "POLY2_COLUMN_COUNT",
    "RIDGE_MODEL_CLASS",
    "ROW_MASK_COLUMN",
    "TARGET_NAME",
    "ElasticNetTrainer",
    "FeatureAllowlistError",
    "FitContext",
    "FitDataError",
    "GridEntry",
    "LinearRegressionTrainer",
    "Poly2RidgeTrainer",
    "RidgeTrainer",
    "build_grid",
    "validate_feature_names",
    "validate_target_name",
]

# Every catalogue name below is a LITERAL argument to `get_feature`/
# `get_label`, copying `features/tier.py:89-101`'s shape exactly:
# `tools/check_catalogue_completeness.py` rejects a dynamically-constructed
# name, which is what makes this module's column list unable to drift away
# from `spec/features.toml` without CI noticing.

#: The three model inputs, in the pinned order every design matrix in this
#: phase is built in (D-07-09).
FEATURE_NAMES: tuple[str, ...] = (
    get_feature("imb_top").name,
    get_feature("ofi").name,
    get_feature("trade_flow").name,
)

#: The columns that must never reach an estimator. `mid` is a CATALOGUE
#: feature and gets a literal lookup; `bid_price` and `ask_price` are v2
#: schema bookkeeping columns (`features.tier.BOOKKEEPING_COLUMNS`' own
#: territory) and are plain literals because the catalogue does not carry
#: them.
FORBIDDEN_FEATURE_NAMES: frozenset[str] = frozenset(
    {get_feature("mid").name, "bid_price", "ask_price"}
)

#: FCST-01's primary target: the 10-second simple midprice return (D-07-10).
TARGET_NAME: str = get_label("ret_10s_mid").name

#: Reported alongside, NEVER fitted in this phase (D-07-10). Named so that
#: `validate_target_name`'s refusal can say which horizon was asked for and
#: why it is diagnostic rather than tradable.
DIAGNOSTIC_TARGET_NAMES: tuple[str, ...] = (
    get_label("ret_1s_mid").name,
    get_label("ret_1min_mid").name,
    get_label("ret_10min_mid").name,
)

#: The MLflow `model_class` tag values, PINNED. See this module's docstring
#: for why the degree-2 trainer reports `RIDGE_MODEL_CLASS` too.
LINEAR_REGRESSION_MODEL_CLASS: str = "sklearn.LinearRegression"
RIDGE_MODEL_CLASS: str = "sklearn.Ridge"
ELASTIC_NET_MODEL_CLASS: str = "sklearn.ElasticNet"

#: `PolynomialFeatures(degree=2, include_bias=False)` on three inputs emits
#: EXACTLY nine columns -- 3 linear + 3 squared + 3 cross -- measured, not
#: assumed (07-RESEARCH.md §Q5(b)). Not 6 (which would drop the squares) and
#: not 10 (which would add the bias column `include_bias=False` suppresses).
#: A literal, cross-checked below against `frozen.expected_coefficient_count`
#: so the number and the derivation cannot drift apart.
POLY2_COLUMN_COUNT: int = 9

#: The single Boolean column a `row_mask_path` Parquet carries.
ROW_MASK_COLUMN: str = "keep"

#: ElasticNet's iteration budget and convergence tolerance, DECLARED as
#: hyperparameters rather than left to sklearn's defaults: a
#: `ConvergenceWarning` is then a property of a named configuration that a
#: reader can see in the recipe, not an accident of a library default that
#: moves between versions.
ELASTIC_NET_MAX_ITER: int = 1_000
ELASTIC_NET_TOL: float = 1e-4

if FEATURE_NAMES != FROZEN_FEATURE_NAMES:
    raise AssertionError(
        f"models.regression: FEATURE_NAMES {FEATURE_NAMES} (three literal "
        f"catalogue lookups) disagrees with models.frozen.FEATURE_NAMES "
        f"{FROZEN_FEATURE_NAMES} -- the fit and the frozen predictor would "
        "build their design matrices in different orders, pairing every "
        "coefficient with the wrong input while still predicting plausible "
        "numbers"
    )

if POLY2_COLUMN_COUNT != expected_coefficient_count(len(FEATURE_NAMES), "poly2"):
    raise AssertionError(
        f"models.regression: POLY2_COLUMN_COUNT is {POLY2_COLUMN_COUNT} but "
        f"models.frozen.expected_coefficient_count says "
        f"{expected_coefficient_count(len(FEATURE_NAMES), 'poly2')} for "
        f"{len(FEATURE_NAMES)} inputs -- a frozen predictor would then refuse "
        "every body this module fits"
    )


class FeatureAllowlistError(ValueError):
    """D-07-23's refusals: a forbidden column NAMED, a feature tuple that is
    not exactly `FEATURE_NAMES`, a target that is not `TARGET_NAME`, or a
    degree-2 expansion whose width is not `POLY2_COLUMN_COUNT`.

    A dedicated class, not a bare `ValueError` -- `models.frozen.
    FrozenPredictorError`'s stated reason, in its own words: a
    `pytest.raises(ValueError)` meaning "the allow-list refused a price
    column" would otherwise be satisfied by "polars refused a ragged
    frame".
    """


class FitDataError(ValueError):
    """The fit boundary refused its DATA rather than its configuration: a
    row mask whose height is not the cache's, a non-Float64 column, a null
    that `.to_numpy()` did not surface as non-finite, a normalisation
    artifact missing a feature, or a frame with no finite row at all."""


@dataclass(frozen=True)
class FitContext:
    """Where a trainer resolves its normalisation artifact from, and what
    code hash it stamps on the predictor it returns.

    Constructor state, never hyperparameters -- see this module's docstring.
    Every field is required: there is no canonical-root fallback, so a test
    that forgets one gets a `TypeError` at construction instead of a
    validation look against the real lake.
    """

    symbol: str
    lake_root: Path
    registry_root: Path
    code_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "lake_root", Path(self.lake_root))
        object.__setattr__(self, "registry_root", Path(self.registry_root))


def validate_feature_names(names: object) -> tuple[str, ...]:
    """D-07-09's refusal. Returns the validated tuple; raises
    `FeatureAllowlistError` naming the offender and the reason.

    Two checks, in this order, because the messages answer different
    questions: a FORBIDDEN name is a price level smuggling the day's trend
    into the design matrix, and any other departure from `FEATURE_NAMES` is
    a column set that would pair coefficients with inputs the frozen
    predictor does not expect.

    The check is on the NAMES a caller asked to fit, before any data is
    read, so a forbidden column costs nothing to refuse -- not a scan of the
    fitted coefficients afterwards, by which point the price has already
    been in the matrix.
    """
    if isinstance(names, str):
        raise FeatureAllowlistError(
            f"validate_feature_names: received the string {names!r} -- a "
            "bare string iterates as characters, so pass a tuple of names"
        )
    ordered = tuple(str(name) for name in names)  # type: ignore[union-attr]
    forbidden = [name for name in ordered if name in FORBIDDEN_FEATURE_NAMES]
    if forbidden:
        raise FeatureAllowlistError(
            f"validate_feature_names: {forbidden} may never reach an "
            f"estimator (D-07-09). The bookkeeping columns are "
            f"{sorted(FORBIDDEN_FEATURE_NAMES)}: a raw price level smuggles "
            "the day's trend into the design matrix, and the model would "
            "score on a level it can only have learned from the window it "
            "was fit in. The features_norm artifact DOES carry a mid row, so "
            "'it came from the normalisation artifact' is not a sufficient "
            f"filter. The three model inputs are {list(FEATURE_NAMES)}; "
            "a predicted return is turned into a price by "
            "models.conversion.predicted_price, which is the only place mid "
            "is read."
        )
    if ordered != FEATURE_NAMES:
        raise FeatureAllowlistError(
            f"validate_feature_names: feature names {list(ordered)} are not "
            f"exactly {list(FEATURE_NAMES)} in that order (D-07-09). The "
            "order is pinned because models.frozen rebuilds the design "
            "matrix positionally from the coefficient vector: a permutation "
            "pairs every coefficient with the wrong input and still predicts "
            "plausible numbers."
        )
    return ordered


def validate_target_name(name: object) -> str:
    """D-07-10's refusal: the only target this phase fits is `TARGET_NAME`.

    The three diagnostic horizons are REPORTED alongside and never fitted,
    so a caller that hands one in is refused by name rather than quietly
    trained -- a `ret_10min_mid` fit scored against the 10-second gate would
    look like a modelling result and be a units mistake.
    """
    target = str(name)
    if target == TARGET_NAME:
        return target
    if target in DIAGNOSTIC_TARGET_NAMES:
        raise FeatureAllowlistError(
            f"validate_target_name: {target!r} is a DIAGNOSTIC horizon "
            f"({list(DIAGNOSTIC_TARGET_NAMES)}) -- reported alongside but "
            f"never fitted in this phase (D-07-10). The fitted target is "
            f"{TARGET_NAME!r}."
        )
    raise FeatureAllowlistError(
        f"validate_target_name: {target!r} is not the fitted target "
        f"{TARGET_NAME!r} (D-07-10) and is not one of the diagnostic "
        f"horizons {list(DIAGNOSTIC_TARGET_NAMES)} either -- refusing to fit "
        "a column whose units this phase has not established."
    )


def _read_row_mask(row_mask_path: Path, *, cache_height: int) -> pl.Series:
    """The OOF training-row selection as a Boolean Series of exactly
    `cache_height` entries, to be applied POSITIONALLY.

    NO JOIN, EVER. A join's output order is unspecified in polars 1.41.2 and
    a silently reordered training set is this phase's top risk (T-07-17).
    The caller derives the mask from `harness.kfold.training_rows_for_block`
    -- which returns a FILTERED FRAME, not a mask -- with
    `keep = np.isin(cache_etime, block_train["etime"].to_numpy())`, legal
    precisely because `etime` is globally unique and strictly sorted across
    the pool (60,926,503 distinct values in 60,926,503 rows, correction C1),
    and asserts `int(keep.sum()) == block_train.height` before writing it:
    a silent shortfall there is a training set quietly smaller than the fold
    geometry says it is.
    """
    frame = pl.read_parquet(row_mask_path, columns=[ROW_MASK_COLUMN])
    if frame.height != cache_height:
        raise FitDataError(
            f"fit: row mask {row_mask_path} has {frame.height} rows but the "
            f"cache has {cache_height} -- the mask is applied POSITIONALLY, "
            "so an unequal height cannot be reconciled and must not be "
            "guessed at"
        )
    column = frame[ROW_MASK_COLUMN]
    if column.dtype != pl.Boolean:
        raise FitDataError(
            f"fit: row mask column {ROW_MASK_COLUMN!r} has dtype "
            f"{column.dtype}, expected Boolean -- a numeric mask would be "
            "truthy on every non-zero value, including a count"
        )
    if column.null_count() != 0:
        raise FitDataError(
            f"fit: row mask column {ROW_MASK_COLUMN!r} has "
            f"{column.null_count()} null(s) -- a null is neither kept nor "
            "dropped, and polars' filter would treat it as dropped without "
            "saying so"
        )
    return column


def _read_columns(inputs: FitInputs) -> dict[str, np.ndarray]:
    """The FOUR columns a fit needs, out of the D-07-05 cache, as float64
    numpy arrays -- and nothing else out of a frame that is 101.28 bytes per
    row.

    At 44,249,548 `train` rows that selection is the difference between
    1.4 GiB and 4.5 GiB (07-RESEARCH.md §Q5(b)). `.select(...)` pins the
    column ORDER to the caller's, rather than trusting `read_parquet`'s
    projection to preserve it.

    THE NULL ACCOUNTING IS THE POINT OF THE REST OF THIS FUNCTION. A
    nullable Float64 column becomes a NaN-filled COPY at `.to_numpy()` and
    does NOT raise -- the standing project rule. So the nulls are counted
    first, converted to NaN EXPLICITLY, asserted gone from the frame, and
    then cross-checked on the numpy side: every null must reappear as a
    non-finite value. A null that arrived as a number means the conversion
    substituted one, and the fit would train on invented observations that
    no dropped-row count could reveal.
    """
    names = [*inputs.feature_names, inputs.target_name]
    frame = pl.read_parquet(inputs.cache_path, columns=names).select(names)
    if inputs.row_mask_path is not None:
        frame = frame.filter(
            _read_row_mask(inputs.row_mask_path, cache_height=frame.height)
        )
    if frame.height == 0:
        raise FitDataError(
            f"fit: {inputs.cache_path} contributes no rows"
            + (" after the row mask" if inputs.row_mask_path is not None else "")
            + " -- refusing to fit an empty design matrix"
        )
    for name in names:
        if frame[name].dtype != pl.Float64:
            raise FitDataError(
                f"fit: column {name!r} has dtype {frame[name].dtype}, "
                "expected Float64 -- a Float32 column changes a prediction "
                "near the half-tick boundary, which is exactly the rounding "
                "that flips a simulator trigger (D-07-16)"
            )
    recorded_nulls = {name: frame[name].null_count() for name in names}
    filled = frame.with_columns(
        [pl.col(name).fill_null(float("nan")) for name in names]
    )
    arrays: dict[str, np.ndarray] = {}
    for name in names:
        if filled[name].null_count() != 0:
            raise FitDataError(
                f"fit: column {name!r} still holds "
                f"{filled[name].null_count()} null(s) after an explicit "
                "fill_null(nan) -- the conversion to numpy would decide "
                "their value silently"
            )
        values = np.asarray(filled[name].to_numpy(), dtype=np.float64)
        surfaced = int((~np.isfinite(values)).sum())
        if surfaced < recorded_nulls[name]:
            raise FitDataError(
                f"fit: column {name!r} carried {recorded_nulls[name]} "
                f"null(s) but only {surfaced} non-finite value(s) survived "
                "the conversion to numpy -- a null that arrives as a number "
                "is a made-up observation, and no dropped-row count would "
                "show it"
            )
        arrays[name] = values
    return arrays


class _SklearnTrainer:
    """The read/mask/normalise/fit path all four estimators share, so there
    is ONE of it.

    A subclass supplies three things and nothing else: the pinned
    `MODEL_CLASS` string, the `hyperparameters` mapping D-07-14 hashes, and
    `_estimator(seed)`. `_design` is the identity here and is overridden
    once, by `Poly2RidgeTrainer`.

    `fit` RETURNS EXACTLY ONE OBJECT. The dropped-row count is a field ON
    the returned predictor, never a second element of a tuple: a tuple would
    fail `isinstance(fitted, FrozenPredictor)` and would make the interface
    Phase 8 must implement against a shape that only linear models have.
    """

    MODEL_CLASS: ClassVar[str]

    def __init__(self, *, context: FitContext) -> None:
        self._context = context
        #: Wall clock of the most recent `fit`, in seconds -- DIAGNOSTIC.
        #: Not a hyperparameter, not a body field of the frozen predictor,
        #: and therefore not in any hash: a recipe whose id moved with the
        #: machine's load would deduplicate nothing. Overwritten by each
        #: fit; plan 07-07's sweep reads it immediately after the call and
        #: logs it as an MLflow metric.
        self.last_fit_seconds: float | None = None

    @property
    def context(self) -> FitContext:
        return self._context

    @property
    def model_class(self) -> str:
        return self.MODEL_CLASS

    @property
    def hyperparameters(self) -> Mapping[str, Any]:
        raise NotImplementedError

    def _estimator(self, *, seed: int) -> Any:
        raise NotImplementedError

    def _design(self, features: np.ndarray) -> np.ndarray:
        """The design matrix actually handed to sklearn. The identity for
        three of the four estimators."""
        return features

    def _normalised_features(
        self, arrays: Mapping[str, np.ndarray], inputs: FitInputs
    ) -> np.ndarray:
        """The three inputs, z-scored by the FEAT-05 artifact's stored
        parameters and by nothing else (D-07-11).

        The artifact may carry a `mid` row -- it is fit over every catalogue
        feature -- and that row is simply never read: `FEATURE_NAMES` is
        what this loop iterates. Research assumption A5, mechanised.
        """
        artifact = load_normalization(
            inputs.normalization_manifest_id,
            normalization_dataset(self._context.symbol),
            registry_root=self._context.registry_root,
            lake_root=self._context.lake_root,
        )
        missing = [name for name in inputs.feature_names if name not in artifact.params]
        if missing:
            raise FitDataError(
                f"fit: normalisation artifact "
                f"{inputs.normalization_manifest_id[:12]} has no parameters "
                f"for {missing} (it carries {sorted(artifact.params)}) -- "
                "refusing to refit them here, which would make every block's "
                "parameters a function of that block (D-07-11)"
            )
        return np.column_stack(
            [
                apply_normalization(arrays[name], artifact.params[name])
                for name in inputs.feature_names
            ]
        )

    def fit(self, inputs: FitInputs) -> FrozenLinearPredictor:
        """Read `inputs`, fit, and return a `FrozenLinearPredictor`.

        The order is the contract: REFUSE the column names before any data
        is read, read only the four needed columns, apply the row mask
        positionally, normalise from the artifact, MASK the non-finite rows
        and count them, fit, and freeze.
        """
        feature_names = validate_feature_names(inputs.feature_names)
        validate_target_name(inputs.target_name)

        arrays = _read_columns(inputs)
        features = self._normalised_features(arrays, inputs)
        target = arrays[inputs.target_name]

        finite = np.isfinite(features).all(axis=1) & np.isfinite(target)
        n_rows_fitted = int(finite.sum())
        n_rows_dropped = int(finite.size - n_rows_fitted)
        if n_rows_fitted == 0:
            raise FitDataError(
                f"fit: all {finite.size} row(s) of {inputs.cache_path} are "
                "non-finite in at least one of the three features or the "
                "target -- there is nothing to fit. A single non-finite row "
                "is masked and counted; ALL of them is a broken cache."
            )

        design = self._design(features[finite])
        estimator = self._estimator(seed=inputs.seed)
        started = time.perf_counter()
        estimator.fit(design, target[finite])
        self.last_fit_seconds = time.perf_counter() - started

        coef = np.asarray(estimator.coef_, dtype=np.float64).ravel()
        if coef.size != design.shape[1]:
            raise FitDataError(
                f"fit: {type(estimator).__name__} returned {coef.size} "
                f"coefficient(s) for a {design.shape[1]}-column design "
                "matrix -- refusing to freeze a body whose coefficients "
                "cannot be paired with its columns"
            )
        return FrozenLinearPredictor(
            model_class=self.model_class,
            feature_names=feature_names,
            coef=tuple(float(value) for value in coef),
            intercept=float(np.asarray(estimator.intercept_).reshape(())),
            normalization_manifest_id=inputs.normalization_manifest_id,
            seed=inputs.seed,
            code_hash=self._context.code_hash,
            hyperparameters=self.hyperparameters,
            train_target_mean=float(target[finite].mean()),
            n_rows_fitted=n_rows_fitted,
            n_rows_dropped=n_rows_dropped,
        )


class LinearRegressionTrainer(_SklearnTrainer):
    """D-07-08's first estimator: OLS, the honest floor.

    NO KNOBS, so `hyperparameters` is `{}` -- and no `random_state` either,
    because `sklearn.linear_model.LinearRegression` has no such parameter:
    `_preprocess_data` centres and `scipy.linalg.lstsq` (LAPACK `gelsd`)
    solves, with nothing random on the path.
    """

    MODEL_CLASS: ClassVar[str] = LINEAR_REGRESSION_MODEL_CLASS

    @property
    def hyperparameters(self) -> Mapping[str, Any]:
        return MappingProxyType({})

    def _estimator(self, *, seed: int) -> Any:
        return SklearnLinearRegression(fit_intercept=True)


class RidgeTrainer(_SklearnTrainer):
    """D-07-08's second estimator: L2, closed-form, deterministic.

    `solver="auto"` resolves to `"cholesky"` on dense float64 input --
    `A = X.T @ X`; `A.flat[::p+1] += alpha`;
    `scipy.linalg.solve(A, Xy, assume_a="pos")` -- and `random_state` is
    consumed only by `sag`/`saga`, a path dense input never takes
    (`tests/models/test_sklearn_environment.py` asserts both halves). The
    seed is passed anyway so the MLflow `seed` tag names a real argument
    rather than a number nothing read.

    Small alphas are safe here and that is MEASURED: the raw three-feature
    Gram has condition number 712, and after the FEAT-05 z-score it is 1.28,
    so squaring it in the normal equations leaves about 1.6. The classic
    "never use normal equations" objection would bite if `mid` were in the
    matrix -- one more reason D-07-09 excludes it.
    """

    MODEL_CLASS: ClassVar[str] = RIDGE_MODEL_CLASS

    def __init__(self, *, alpha: float, context: FitContext) -> None:
        super().__init__(context=context)
        self._alpha = float(alpha)

    @property
    def hyperparameters(self) -> Mapping[str, Any]:
        return MappingProxyType({"alpha": self._alpha})

    def _estimator(self, *, seed: int) -> Any:
        return SklearnRidge(
            alpha=self._alpha,
            fit_intercept=True,
            solver="auto",
            random_state=int(seed),
        )


class ElasticNetTrainer(_SklearnTrainer):
    """D-07-08's third estimator: the L1/L2 mix, by coordinate descent.

    `selection="cyclic"` is what makes it deterministic -- the Cython loop
    then sweeps the coordinates in index order and `random_state` is unused
    (`selection="random"` is the only path that draws from it). The seed is
    still passed, and `max_iter`/`tol` are DECLARED hyperparameters, so a
    `ConvergenceWarning` is a property of a named configuration a reader can
    find in the recipe rather than an accident of a library default.

    sklearn's objective carries `1 / (2 * n_samples)` on the squared-error
    term, so `alpha * l1_ratio` is a soft threshold measured in per-sample
    gradient units. On this project's target -- a 10-second return whose
    standard deviation is order 1e-4 -- the larger alphas on the grid can
    drive every coefficient to exactly zero. A zero vector is a VALID frozen
    predictor (only a non-finite one is refused) and such a config simply
    fails plan 07-07's eligibility gates and is logged as a negative result
    (D-07-20). It is not this module's business to refuse it: an estimator
    that silently declined to be useless would hide a real property of the
    grid.
    """

    MODEL_CLASS: ClassVar[str] = ELASTIC_NET_MODEL_CLASS

    def __init__(
        self,
        *,
        alpha: float,
        l1_ratio: float,
        context: FitContext,
        max_iter: int = ELASTIC_NET_MAX_ITER,
        tol: float = ELASTIC_NET_TOL,
    ) -> None:
        super().__init__(context=context)
        self._alpha = float(alpha)
        self._l1_ratio = float(l1_ratio)
        self._max_iter = int(max_iter)
        self._tol = float(tol)

    @property
    def hyperparameters(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "alpha": self._alpha,
                "l1_ratio": self._l1_ratio,
                "max_iter": self._max_iter,
                "tol": self._tol,
                "selection": "cyclic",
            }
        )

    def _estimator(self, *, seed: int) -> Any:
        return SklearnElasticNet(
            alpha=self._alpha,
            l1_ratio=self._l1_ratio,
            fit_intercept=True,
            max_iter=self._max_iter,
            tol=self._tol,
            selection="cyclic",
            random_state=int(seed),
        )


class Poly2RidgeTrainer(RidgeTrainer):
    """D-07-08's fourth estimator: Ridge on
    `PolynomialFeatures(degree=2, include_bias=False)` -- FCST-01's "small
    non-linear" member, kept inside scikit-learn so trees stay Phase 8's
    territory.

    `hyperparameters` is `{"alpha": a, "degree": 2}`, and `MODEL_CLASS` is
    inherited from `RidgeTrainer` -- byte-identical `"sklearn.Ridge"`. See
    this module's docstring: `degree` inside the hashed recipe is the ONLY
    thing that separates this trainer's three configs from the plain Ridge
    configs at the same alpha, and that is exactly the property
    `models.frozen`'s derived `design` depends on.

    The expansion's width is ASSERTED, not assumed. It is the only one of
    D-07-23's three refusals that can see a price column entering through an
    interaction term: a fourth input would make this 14 columns, which the
    coefficient-count check on the INPUT names cannot notice.
    """

    def __init__(self, *, alpha: float, context: FitContext) -> None:
        super().__init__(alpha=alpha, context=context)

    @property
    def hyperparameters(self) -> Mapping[str, Any]:
        return MappingProxyType({"alpha": self._alpha, "degree": SUPPORTED_DEGREE})

    def _design(self, features: np.ndarray) -> np.ndarray:
        expanded = PolynomialFeatures(
            degree=SUPPORTED_DEGREE, include_bias=False
        ).fit_transform(features)
        if expanded.shape[1] != POLY2_COLUMN_COUNT:
            raise FeatureAllowlistError(
                f"fit: the degree-{SUPPORTED_DEGREE} expansion of "
                f"{features.shape[1]} input(s) has {expanded.shape[1]} "
                f"columns, expected exactly {POLY2_COLUMN_COUNT} (D-07-23's "
                "third refusal). 3 linear + 3 squared + 3 cross is 9; a "
                "wider matrix means a column entered the design that the "
                "three-name allow-list did not see -- which is how a price "
                "level gets in through an interaction term."
            )
        return expanded


@dataclass(frozen=True)
class GridEntry:
    """One configuration: a trainer class and the knobs it is constructed
    with. Not a fitted thing and not a search result -- a literal row of
    `GRID`, so the sweep's `n_configs` is something a reader can count."""

    trainer_class: type
    knobs: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "knobs", MappingProxyType(dict(self.knobs)))

    def build(self, context: FitContext) -> _SklearnTrainer:
        """The trainer itself, bound to `context`. Constructed on demand
        rather than stored, so one `GRID` serves a test's `tmp_path` roots
        and a real run's canonical ones without either seeing the other."""
        return self.trainer_class(context=context, **self.knobs)


#: How many configurations the sweep logs as `n_configs`. A LITERAL, pinned
#: beside the grid it counts and cross-checked against `len(GRID)` at import
#: -- so an edit that adds a row without the reader noticing fails loudly.
#:
#: IT IS ALSO THE SELECTION-BIAS DENOMINATOR, AND IT ONLY EVER GROWS. Rows
#: 17-35 were added AFTER the first sweep of rows 0-16 returned 0 of 17
#: eligible (plan 07-10), which is exactly the cost this number measures: a
#: grid widened in response to a result has been selected on. 17 is not
#: recoverable by deleting rows.
GRID_SIZE: int = 36

#: D-07-08's grid: FIXED, HAND-WRITTEN, COUNTED. One row per configuration,
#: never `itertools.product` and never a sampler -- FCST-05's black-box HPO
#: is Phase 8's (D-07-08: "Hyperparameters are a fixed, hand-written,
#: COUNTED grid -- no Optuna").
#:
#:     LinearRegression                                            1
#:     Ridge          alpha in {1e-6, 1e-3, 1.0, 100.0}            4
#:                    + {3e4, 1e7, 3e7, 1e8, 3e8, 1e9, 3e9, 1e11}  8
#:     ElasticNet     alpha in {1e-6, 1e-4, 1e-2}
#:                    x l1_ratio in {0.15, 0.5, 0.85}              9
#:                    + five (alpha, l1_ratio) pairs on the
#:                      alpha*l1_ratio axis below the measured cliff   5
#:     Ridge + poly2  alpha in {1e-3, 1.0, 100.0}                  3
#:                    + {3e7, 1e8, 3e8, 1e9, 3e9, 1e11}            6
#:                                                                36
#:
#: The NINE poly2 rows share their alphas with nine of the Ridge rows on
#: purpose; `degree` inside `hyperparameters` is what keeps their
#: `predictor_id`s distinct (`tests/models/test_protocol_conformance.py`
#: measures the collapse to 27 when it is removed).
GRID: tuple[GridEntry, ...] = (
    # 1 -- the honest floor.
    GridEntry(LinearRegressionTrainer, {}),
    # 4 -- L2 over four decades of alpha.
    GridEntry(RidgeTrainer, {"alpha": 1e-6}),
    GridEntry(RidgeTrainer, {"alpha": 1e-3}),
    GridEntry(RidgeTrainer, {"alpha": 1.0}),
    GridEntry(RidgeTrainer, {"alpha": 100.0}),
    # 9 -- three alphas x three L1 fractions, written out rather than
    # generated, so the count is visible at the call site.
    GridEntry(ElasticNetTrainer, {"alpha": 1e-6, "l1_ratio": 0.15}),
    GridEntry(ElasticNetTrainer, {"alpha": 1e-6, "l1_ratio": 0.5}),
    GridEntry(ElasticNetTrainer, {"alpha": 1e-6, "l1_ratio": 0.85}),
    GridEntry(ElasticNetTrainer, {"alpha": 1e-4, "l1_ratio": 0.15}),
    GridEntry(ElasticNetTrainer, {"alpha": 1e-4, "l1_ratio": 0.5}),
    GridEntry(ElasticNetTrainer, {"alpha": 1e-4, "l1_ratio": 0.85}),
    GridEntry(ElasticNetTrainer, {"alpha": 1e-2, "l1_ratio": 0.15}),
    GridEntry(ElasticNetTrainer, {"alpha": 1e-2, "l1_ratio": 0.5}),
    GridEntry(ElasticNetTrainer, {"alpha": 1e-2, "l1_ratio": 0.85}),
    # 3 -- the small non-linear member, at the three alphas that overlap
    # Ridge's own.
    GridEntry(Poly2RidgeTrainer, {"alpha": 1e-3}),
    GridEntry(Poly2RidgeTrainer, {"alpha": 1.0}),
    GridEntry(Poly2RidgeTrainer, {"alpha": 100.0}),
    # ==================================================================
    # ROWS 17-35: the widening. APPENDED, never interleaved, so grid
    # positions 0-16 keep naming the same configs the plan 07-10
    # `selection.json` and its negative-result records name.
    #
    # WHY THE L2 LADDER JUMPS FOUR DECADES ABOVE 100, which is the whole
    # point of these rows. sklearn's Ridge minimises
    # `||y - Xw||^2 + alpha*||w||^2` -- the SUM of squared residuals, NOT
    # the mean -- so `alpha` competes with `sum(x^2)`, and on the FEAT-05
    # z-scored design that sum IS THE ROW COUNT: 44,229,781 fitted rows on
    # this segment's train frame. The four alphas above (1e-6 .. 100) can
    # therefore shrink by at most 100/4.42e7 = 2.3e-6, and they measured
    # IDENTICAL to six decimals on all five OOF blocks -- the alpha ladder
    # was never a shrinkage ladder at all. The quantity that matters is the
    # PREDICTION-SCALE ratio sd(Xw_alpha)/sd(Xw_ols), measured train-only
    # (train role costs no look) on this manifest's cached train frame:
    #
    #   alpha      3e4     1e7     3e7     1e8     3e8     1e9     3e9    1e11
    #   linear  0.9993  0.8101  0.5879  0.3007  0.1257  0.0414  0.0142  0.0004
    #   poly2   0.9994  0.8366  0.6534  0.4450  0.3585  0.3344  0.3180  0.1028
    #
    # 3e4 is carried as a WITNESS, not as a candidate: it is the top of the
    # ladder a reader would reach for next, and it measures nothing new.
    # The poly2 row flattens at ~0.32 because its centred 9x9 Gram carries
    # one eigenvalue of 3.78e5 * n (a squared fat-tailed feature), which no
    # alpha below ~1e10 can touch -- so 1e11 is the only poly2 row that
    # reaches the low-amplitude band the linear ladder covers from 1e8 down.
    # 8 -- L2 where L2 on 4.4e7 rows actually bites.
    GridEntry(RidgeTrainer, {"alpha": 3e4}),
    GridEntry(RidgeTrainer, {"alpha": 1e7}),
    GridEntry(RidgeTrainer, {"alpha": 3e7}),
    GridEntry(RidgeTrainer, {"alpha": 1e8}),
    GridEntry(RidgeTrainer, {"alpha": 3e8}),
    GridEntry(RidgeTrainer, {"alpha": 1e9}),
    GridEntry(RidgeTrainer, {"alpha": 3e9}),
    GridEntry(RidgeTrainer, {"alpha": 1e11}),
    # 5 -- ElasticNet along `alpha * l1_ratio`, WHICH IS THE ONLY AXIS IT
    # HAS HERE. This estimator's data term carries `1/(2*n_samples)`, so
    # `alpha*l1_ratio` is a soft threshold in per-sample gradient units and
    # `alpha*(1-l1_ratio)` is an L2 term ~1e-4 -- utterly inert beside the
    # L2 ladder above. The thresholds it is compared against are the
    # per-column |X'(y - ybar)/n| on the same train frame, measured:
    # imb_top 4.328e-5, trade_flow 7.453e-6, ofi 4.138e-6. So EVERY
    # coefficient is exactly zero once alpha*l1_ratio > 4.328e-5 (which is
    # why rows 9-13 were constant), and every live ElasticNet at
    # alpha*l1_ratio > 7.5e-6 -- INCLUDING row 8 -- is an imb_top-ONLY
    # model, not a shrunk three-feature one. These five walk the live band
    # 2.0e-5 .. 4.2e-5 toward that cliff, varying l1_ratio as well as alpha
    # so the product really is the axis rather than an assumption.
    GridEntry(ElasticNetTrainer, {"alpha": 2e-4, "l1_ratio": 0.10}),
    GridEntry(ElasticNetTrainer, {"alpha": 1e-4, "l1_ratio": 0.30}),
    GridEntry(ElasticNetTrainer, {"alpha": 1.2e-4, "l1_ratio": 0.30}),
    GridEntry(ElasticNetTrainer, {"alpha": 3.6e-4, "l1_ratio": 0.10}),
    GridEntry(ElasticNetTrainer, {"alpha": 4e-4, "l1_ratio": 0.10}),
    # 6 -- the same L2 ladder on the degree-2 design, from the first alpha
    # that moves it. Every one of these six is ALSO a plain Ridge alpha
    # above, which is what keeps `poly2 < plain` true and makes the
    # stripped-`degree` collapse exactly nine.
    GridEntry(Poly2RidgeTrainer, {"alpha": 3e7}),
    GridEntry(Poly2RidgeTrainer, {"alpha": 1e8}),
    GridEntry(Poly2RidgeTrainer, {"alpha": 3e8}),
    GridEntry(Poly2RidgeTrainer, {"alpha": 1e9}),
    GridEntry(Poly2RidgeTrainer, {"alpha": 3e9}),
    GridEntry(Poly2RidgeTrainer, {"alpha": 1e11}),
)

if len(GRID) != GRID_SIZE:
    raise AssertionError(
        f"models.regression: GRID has {len(GRID)} entries but GRID_SIZE says "
        f"{GRID_SIZE} -- the counted grid is D-07-08's whole point, and "
        "n_configs is what plan 07-07 logs as the selection-bias budget's "
        "denominator"
    )


def build_grid(context: FitContext) -> tuple[_SklearnTrainer, ...]:
    """Every `GRID` entry as a constructed trainer, in grid order."""
    return tuple(entry.build(context) for entry in GRID)
