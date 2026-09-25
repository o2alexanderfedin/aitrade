"""D-07-17's frozen predictor: a fitted linear model reduced to a JSON body
of coefficients, plus the registry that refuses to hand one back unless four
independent checks agree.

THIS MODULE IMPORTS numpy AND NEVER sklearn, pickle, cloudpickle OR joblib,
and that is a CONTRACT rather than an accident of what it happened to need.
It is what makes D-07-17's proof possible at all: the coefficient JSON,
re-evaluated by a bare `intercept + X @ coef` in a process where `"sklearn"
is not in sys.modules`, must reproduce `predict()` bit-for-bit. A pickle
could not be checked that way -- it can only be verified by the library that
wrote it, running the version that wrote it, which is precisely the property
a "frozen" predictor must not have. `tests/models/test_frozen_no_sklearn.py`
asserts the ban over this file's IMPORT NODES, never over its text, so these
paragraphs stay free to name the four modules they forbid.

`design` IS A DERIVED PROPERTY, NOT A FIELD, AND THAT IS THE WHOLE POINT.
`models.predictor_id` hashes exactly D-07-14's five fields; `design` is not
one of them, and `degree` therefore has to live INSIDE `hyperparameters` to
be hashed at all. D-07-08's counted grid runs `Ridge` at alpha
{1e-6, 1e-3, 1.0, 100.0} and `Ridge` on `PolynomialFeatures(degree=2,
include_bias=False)` at alpha {1e-3, 1.0, 100.0}, and 07-06 pins BOTH to the
same `model_class` string `"sklearn.Ridge"` -- so three pairs of configs
share alpha, class, seed, code hash and normalisation id. A `design` carried
as a FIELD would let a body claim `"poly2"`, carry nine coefficients and no
`degree` key, and hash a recipe byte-identical to plain Ridge at the same
alpha: three pairs collapsing onto one `predictor_id`, `harness.negative_log`
deduplicating away up to three distinct ineligible configs (falsifying
D-07-20), and a stored table's `predictor=<first 16 chars>` directory naming
a config that never produced it. A property cannot disagree with what it is
computed from. `to_artifact()` still EMITS `design` for readability, and both
doors back into a body (`read_frozen_predictor` and
`frozen_linear_from_artifact`) re-derive it and refuse a disagreement.

THREE FIELDS RIDE IN THE BODY AND NEVER IN THE RECIPE: `train_target_mean`,
`n_rows_fitted`, `n_rows_dropped`. They change `manifest_id` and never
`predictor_id`, and the direction matters in both ways it could be got wrong:

* As HYPERPARAMETERS they would be inside the hash, so every OOF block's fit
  of the same config would get its own `predictor_id` -- inverting D-07-22,
  which needs two runs of one recipe to SHARE a `predictor_id` so that
  differing coefficients show up as differing `manifest_id`s.
* As a SECOND RETURN VALUE they would break the interface. `Trainer.fit(...)
  -> FrozenPredictor` returns exactly ONE object; a tuple fails
  `models.protocol`'s runtime conformance check and is the kind of widening
  Phase 8 must never have to make.

`train_target_mean` exists because `models.metrics`' zero-skill control is a
constant predictor AT THE TRAIN MEAN, and `mode="val"` never reads the train
frame. Without the field, the only mean to hand at scoring time is the
EVALUATION set's, and logging that under a key that says "train" would make
the control column a different, weaker statistic than the one the human
checkpoint asks a developer to read.

THE DETERMINISM BOUNDARY IS THIS JSON, NOT THE FIT (correction C6). This
host's numpy links Apple Accelerate; `threadpoolctl` ships no Accelerate
controller, so `OMP_NUM_THREADS` controls nothing here, and CI is
Linux/OpenBLAS. A coefficient or prediction digest pinned as a committed
constant would be a guaranteed CI failure. What IS asserted -- and was
measured -- is that GIVEN the coefficients, one matvec at fixed shape is
bit-identical in-process, in a fresh subprocess, and across thread counts.

WHY THE BODY CARRIES TWO IDS (D-07-22). `manifest_id` is the self-hash over
everything INCLUDING the coefficients, so `tools/check_manifest_id_integrity`
works on it unchanged; `predictor_id` is the hash of the RECIPE only. Two
runs of the same recipe that disagree on coefficients therefore get the same
`predictor_id` under different `manifest_id`s -- which makes the determinism
claim checkable as DATA on disk, not only as a test that ran once.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np

from data.store import compute_manifest_id

# `_canonical_value` is a SIBLING MODULE's private helper, imported rather
# than copied. The project's copy-don't-import norm (`harness/segments.py`
# :86-94) is about a five-line I/O helper crossing packages; this is a
# twenty-line recursive per-type coercion inside the SAME package, and
# copying it would create exactly the second canonicaliser 07-PATTERNS §C
# forbids -- the body's hyperparameters and the hashed recipe's must be the
# same bytes, or a reader diffing the body is reading something the id does
# not cover.
from models.predictor_id import _canonical_value, predictor_id

__all__ = [
    "FEATURE_NAMES",
    "FROZEN_PREDICTOR_SCHEMA_VERSION",
    "LINEAR_MODEL_CLASSES",
    "REQUIRED_BODY_KEYS",
    "SUPPORTED_DEGREE",
    "FrozenLinearPredictor",
    "FrozenPredictorError",
    "frozen_linear_from_artifact",
    "poly2_design",
    "predictor_registry_path",
    "read_frozen_predictor",
    "write_frozen_predictor",
]

#: The three INPUT feature names, in the pinned order every design matrix in
#: this phase is built in (D-07-09). `mid`, `bid_price` and `ask_price` are
#: BOOKKEEPING and must never reach an estimator -- a raw price level
#: smuggles the day's trend into the design matrix. The nine expanded poly2
#: columns are DERIVED from these three and are never named here.
FEATURE_NAMES: tuple[str, ...] = ("imb_top", "ofi", "trade_flow")

#: `model_class` values whose artifact body is a coefficient JSON this module
#: can rebuild. Pinned by 07-06: the degree-2 Ridge reports `"sklearn.Ridge"`
#: TOO, not `"sklearn.Ridge+poly2"`, so that `degree` inside
#: `hyperparameters` is the only thing separating the two -- which is what
#: makes the collision argument in this module's docstring load-bearing
#: rather than decorative.
LINEAR_MODEL_CLASSES: tuple[str, ...] = (
    "sklearn.LinearRegression",
    "sklearn.Ridge",
    "sklearn.ElasticNet",
)

#: The ONLY polynomial degree this phase expands (D-07-08's fourth
#: estimator). A `degree` present and not equal to this is REFUSED at
#: construction rather than silently read as `"linear"`: a degree-3 body
#: would derive `"linear"`, carry three coefficients, pass every check in
#: this file, and predict a cubic model as if it were a linear one.
SUPPORTED_DEGREE: int = 2

#: Bumped when the body's key set or the meaning of a key changes. A body
#: carrying any other value is refused on read rather than interpreted under
#: this version's rules (`features/tier.py`'s own precedent).
FROZEN_PREDICTOR_SCHEMA_VERSION: int = 1

#: Every key `to_artifact()` emits. Spelled once so a read can refuse an
#: incomplete body by NAME, instead of raising `TypeError` from the
#: dataclass constructor with no mention of which field went missing.
REQUIRED_BODY_KEYS: tuple[str, ...] = (
    "model_class",
    "design",
    "feature_names",
    "coef",
    "intercept",
    "normalization_manifest_id",
    "seed",
    "code_hash",
    "hyperparameters",
    "train_target_mean",
    "n_rows_fitted",
    "n_rows_dropped",
    "predictor_id",
    "schema_version",
)


class FrozenPredictorError(ValueError):
    """Raised for a frozen-predictor protocol violation: a body whose design
    and coefficient count disagree, a `predict` input of the wrong shape or
    dtype, or any of `read_frozen_predictor`'s four refusals.

    A dedicated class, not a bare `ValueError` -- exactly
    `harness.errata.ErrataManifestError`'s stated reason. A bare
    `ValueError` would also match `models.predictor_id`'s own unrelated
    `TypeError`/`ValueError` raises and this module's numpy-level failures,
    so `pytest.raises(ValueError)` would pass for the wrong reason: a test
    meaning "the registry refused a tampered body" would be satisfied by
    "numpy refused a ragged array".
    """


def _atomic_write_json(path: Path, body: dict) -> None:
    """Duplicated from `data.store._atomic_write_json` (underscore-private,
    crossed by copying its five lines rather than importing, per the
    project's own stated norm -- `harness/segments.py` and `data/lockbox.py`
    both do the same)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(body, sort_keys=True, indent=2))
    tmp_path.replace(path)


def _derive_design(hyperparameters: Mapping[str, Any]) -> str:
    """`"poly2"` when `hyperparameters["degree"]` is present, else
    `"linear"` -- the single site the design is decided, so nothing can
    carry a design that disagrees with the recipe it was hashed from.

    Presence, not value, selects the design: the value is separately pinned
    to `SUPPORTED_DEGREE` by `_require_supported_degree`, so `degree: 3`
    raises instead of quietly reading as `"linear"`.
    """
    return "poly2" if "degree" in hyperparameters else "linear"


def _require_supported_degree(hyperparameters: Mapping[str, Any]) -> None:
    if "degree" not in hyperparameters:
        return
    degree = hyperparameters["degree"]
    if isinstance(degree, bool) or degree != SUPPORTED_DEGREE:
        raise FrozenPredictorError(
            f"FrozenLinearPredictor: hyperparameters['degree'] is "
            f"{degree!r}, and the only expansion this module implements is "
            f"degree {SUPPORTED_DEGREE} (D-07-08's fourth estimator). "
            "Refused rather than read as a linear design -- a body with an "
            "unimplemented degree would carry the wrong number of "
            "coefficients for whatever expansion its trainer actually used."
        )


def expected_coefficient_count(n_features: int, design: str) -> int:
    """How many coefficients `design` needs for `n_features` inputs.

    `"linear"` -> `p`. `"poly2"` -> `p + p*(p+1)//2`: the `p` linear terms
    plus every unordered pair with repetition. For this phase's three inputs
    that is EXACTLY 9, measured -- not 6 (which would drop the squares) and
    not 10 (which would add a bias column `PolynomialFeatures(
    include_bias=False)` does not emit).
    """
    if design == "poly2":
        return n_features + n_features * (n_features + 1) // 2
    return n_features


def poly2_design(features: np.ndarray) -> np.ndarray:
    """`(n, p)` -> `(n, p + p*(p+1)//2)` in EXACTLY
    `sklearn.preprocessing.PolynomialFeatures(degree=2,
    include_bias=False)`'s column order: the `p` linear terms first, then
    the degree-2 terms in the upper-triangular sweep `(i, j) for i in
    range(p) for j in range(i, p)`.

    For `p = 3` that is `(x0, x1, x2, x0*x0, x0*x1, x0*x2, x1*x1, x1*x2,
    x2*x2)`. Verified against sklearn 1.9.1 rather than assumed:
    `tests/models/test_poly2_design_matches_sklearn.py` asserts both
    `get_feature_names_out()`'s order and bit-for-bit equality of the
    matrices, because a silently permuted column pairs every coefficient
    with the wrong term and still predicts plausible numbers.
    """
    n_features = features.shape[1]
    columns = [features[:, index] for index in range(n_features)]
    for i in range(n_features):
        for j in range(i, n_features):
            columns.append(features[:, i] * features[:, j])
    return np.column_stack(columns)


@dataclass(frozen=True, kw_only=True)
class FrozenLinearPredictor:
    """A fitted linear (or degree-2) model as its coefficients, satisfying
    `models.protocol.FrozenPredictor`.

    KEYWORD-ONLY, for `models.predictor_id.predictor_id`'s reason in its own
    words: a positional order over `normalization_manifest_id`, `code_hash`
    and `model_class` -- three strings that look alike -- is a defect waiting
    to happen, and swapping two of them yields a perfectly valid-looking id
    for the wrong recipe.

    `design` is a PROPERTY. See this module's docstring for why a field
    would reopen D-07-14's collision one level down.
    """

    model_class: str
    feature_names: tuple[str, ...]
    coef: tuple[float, ...]
    intercept: float
    normalization_manifest_id: str
    seed: int
    code_hash: str
    hyperparameters: Mapping[str, Any]
    train_target_mean: float
    n_rows_fitted: int
    n_rows_dropped: int

    def __post_init__(self) -> None:
        """Normalise the containers, then refuse every body that can never
        predict correctly -- at construction, which is the cheapest place to
        find out.

        The numeric coercions are not cosmetic. `json.dumps` raises
        `TypeError` on a numpy scalar, and `sklearn`'s `coef_` is a numpy
        ARRAY of `np.float64`; a `to_artifact()` that inherited them would
        fail in the middle of a 17-config sweep, hours in, which is a bad
        place to learn the body was never JSON-able. `hyperparameters` goes
        through `models.predictor_id`'s own per-type coercion so the body
        and the hashed recipe hold the same bytes; a value that survives
        neither raises `UnhashableHyperparameterError` naming the key.
        """
        object.__setattr__(
            self, "feature_names", tuple(str(name) for name in self.feature_names)
        )
        object.__setattr__(self, "coef", tuple(float(value) for value in self.coef))
        object.__setattr__(self, "intercept", float(self.intercept))
        object.__setattr__(self, "train_target_mean", float(self.train_target_mean))
        object.__setattr__(self, "seed", int(self.seed))
        object.__setattr__(self, "n_rows_fitted", int(self.n_rows_fitted))
        object.__setattr__(self, "n_rows_dropped", int(self.n_rows_dropped))
        object.__setattr__(
            self,
            "hyperparameters",
            MappingProxyType(
                _canonical_value(dict(self.hyperparameters), "hyperparameters")
            ),
        )

        if not self.feature_names:
            raise FrozenPredictorError(
                "FrozenLinearPredictor: feature_names is empty -- a "
                "predictor that names no inputs cannot validate the width "
                "of anything it is handed"
            )
        if len(set(self.feature_names)) != len(self.feature_names):
            raise FrozenPredictorError(
                f"FrozenLinearPredictor: feature_names {self.feature_names} "
                "repeats a name -- a duplicated column would pair two "
                "coefficients with one input and hide the mistake in a "
                "plausible prediction"
            )
        _require_supported_degree(self.hyperparameters)

        design = self.design
        expected = expected_coefficient_count(len(self.feature_names), design)
        if len(self.coef) != expected:
            raise FrozenPredictorError(
                f"FrozenLinearPredictor: design {design!r} over "
                f"{len(self.feature_names)} inputs needs exactly {expected} "
                f"coefficients, got {len(self.coef)} -- derived from "
                f"hyperparameters['degree'] = "
                f"{self.hyperparameters.get('degree')!r}. A body whose "
                "design and coefficient count disagree can never predict "
                "correctly."
            )
        for label, value in (
            ("intercept", self.intercept),
            ("train_target_mean", self.train_target_mean),
        ):
            if not np.isfinite(value):
                raise FrozenPredictorError(
                    f"FrozenLinearPredictor: {label} is {value!r} -- a "
                    "non-finite value here poisons every prediction (or, "
                    "for train_target_mean, every zero-skill control) and "
                    "would be discovered as a NaN metric instead of as a "
                    "refused fit"
                )
        for index, value in enumerate(self.coef):
            if not np.isfinite(value):
                raise FrozenPredictorError(
                    f"FrozenLinearPredictor: coef[{index}] is {value!r} -- "
                    "a non-finite coefficient makes every prediction NaN"
                )
        for label, value in (
            ("n_rows_fitted", self.n_rows_fitted),
            ("n_rows_dropped", self.n_rows_dropped),
        ):
            if value < 0:
                raise FrozenPredictorError(
                    f"FrozenLinearPredictor: {label} is {value} -- a row "
                    "count cannot be negative"
                )

    @property
    def design(self) -> str:
        """`"linear"` or `"poly2"`, DERIVED from the hashed recipe.

        Load-bearing rather than descriptive: three of D-07-08's four
        estimators have three coefficients and the fourth has nine, so
        without this the sklearn-free re-evaluation cannot know how to
        rebuild the design matrix. And derived rather than stored, so it
        cannot disagree with the `degree` that `predictor_id` actually
        hashes.
        """
        return _derive_design(self.hyperparameters)

    @property
    def predictor_id(self) -> str:
        """D-07-14's recipe hash, RE-DERIVED on every access through
        `models.predictor_id` -- never a stored second source of truth that
        could drift from the recipe beside it."""
        return predictor_id(
            model_class=self.model_class,
            hyperparameters=self.hyperparameters,
            seed=self.seed,
            code_hash=self.code_hash,
            normalization_manifest_id=self.normalization_manifest_id,
        )

    def _design_matrix(self, features: np.ndarray) -> np.ndarray:
        if not isinstance(features, np.ndarray):
            raise FrozenPredictorError(
                f"predict: features must be a numpy ndarray, got "
                f"{type(features).__name__} -- a list of lists would be "
                "silently upcast, and a polars frame would arrive with its "
                "columns in whatever order the caller built it in"
            )
        expected_width = len(self.feature_names)
        if features.ndim != 2 or features.shape[1] != expected_width:
            raise FrozenPredictorError(
                f"predict: expected shape (n, {expected_width}) for "
                f"feature_names {self.feature_names}, received "
                f"{features.shape} -- refusing to guess which columns were "
                "meant"
            )
        if features.dtype != np.float64:
            raise FrozenPredictorError(
                f"predict: expected dtype float64, received "
                f"{features.dtype} -- a float32 design matrix changes a "
                "prediction near the half-tick boundary, which is exactly "
                "the rounding that flips a simulator trigger (D-07-16)"
            )
        if self.design == "poly2":
            return poly2_design(features)
        return features

    def predict(self, features: np.ndarray) -> np.ndarray:
        """`(n, p)` ALREADY-NORMALISED float64 features -> `(n,)` float64
        predictions in the TARGET's unit (a 10-second simple return,
        D-07-10 -- never a price, D-07-23).

        `intercept + X_design @ coef` and nothing else. Pure and pointwise:
        no I/O, no global state, and deliberately no path that looks at
        `features` to decide anything, in `features.normalize.
        apply_normalization`'s register -- everything scored by a run is
        scored with the run's own coefficients, whatever data it is handed.

        A NaN in `features` LEAVES A NaN in the output, by design. Masking
        here would hide the row from the caller that owns the decision
        (`models.regression` masks non-finite rows before fitting and the
        metrics layer masks before scoring); a predictor that quietly
        substituted a number would make a dropped row indistinguishable
        from a predicted one.
        """
        design_matrix = self._design_matrix(features)
        return self.intercept + design_matrix @ np.asarray(self.coef, dtype=np.float64)

    def to_artifact(self) -> dict[str, Any]:
        """The JSON-able body D-07-17 asks for -- every numeric a plain
        Python `float`/`int`, every container a `list`/`dict`.

        `design` is EMITTED, for a reader diffing the file, and is
        re-derived and cross-checked by both doors that read a body back. It
        is emitted for readability and trusted nowhere.
        """
        return {
            "model_class": self.model_class,
            "design": self.design,
            "feature_names": list(self.feature_names),
            "coef": [float(value) for value in self.coef],
            "intercept": float(self.intercept),
            "normalization_manifest_id": self.normalization_manifest_id,
            "seed": int(self.seed),
            "code_hash": self.code_hash,
            "hyperparameters": dict(self.hyperparameters),
            "train_target_mean": float(self.train_target_mean),
            "n_rows_fitted": int(self.n_rows_fitted),
            "n_rows_dropped": int(self.n_rows_dropped),
            "predictor_id": self.predictor_id,
            "schema_version": FROZEN_PREDICTOR_SCHEMA_VERSION,
        }


def predictor_registry_path(registry_root: Path, manifest_id: str) -> Path:
    """Single source of truth for a frozen predictor's on-disk path --
    `registry_root/predictors/<manifest_id>.json`, flat, mirroring
    `harness.errata.errata_manifest_path` and
    `harness.segments.segment_manifest_path` (D-07-22: a sibling
    content-addressed registry, same rule)."""
    return Path(registry_root) / "predictors" / f"{manifest_id}.json"


def write_frozen_predictor(
    predictor: FrozenLinearPredictor, *, registry_root: Path
) -> dict[str, Any]:
    """Write `predictor`'s body to `registry_root/predictors/` under its own
    self-hash and return the body, `manifest_id` included.

    `registry_root` is KEYWORD-ONLY and has NO DEFAULT, deliberately: the
    canonical `mvp/data/lake_registry/` is append-only and git-committed, so
    a caller that forgot to say where must fail rather than land a
    fixture-derived body there permanently.

    `manifest_id` is inserted AFTER hashing, which changes nothing --
    `data.store.canonicalize_manifest` drops the `manifest_id` key before
    encoding, so the id covers the coefficients and every other key, and
    never itself. Refuses to overwrite an existing path: a content-addressed
    body at the same id is either identical (nothing to do) or a hash
    collision (not something to resolve by writing).
    """
    body = predictor.to_artifact()
    manifest_id = compute_manifest_id(body)
    body["manifest_id"] = manifest_id
    path = predictor_registry_path(registry_root, manifest_id)
    if path.exists():
        raise FrozenPredictorError(
            f"write_frozen_predictor: {path} already exists -- refusing to "
            "overwrite a content-addressed body (append-only registry)"
        )
    _atomic_write_json(path, body)
    return body


def _require_body_keys(body: Mapping[str, Any], source: str) -> None:
    missing = [key for key in REQUIRED_BODY_KEYS if key not in body]
    if missing:
        raise FrozenPredictorError(
            f"{source}: body is missing required field(s) {missing} -- "
            "refusing an incomplete body rather than defaulting a field "
            "the id was computed over"
        )
    version = body["schema_version"]
    if version != FROZEN_PREDICTOR_SCHEMA_VERSION:
        raise FrozenPredictorError(
            f"{source}: body schema_version is {version!r}, this module "
            f"reads {FROZEN_PREDICTOR_SCHEMA_VERSION} -- refusing to "
            "interpret another version's key set under this one's rules"
        )


def _require_design_agrees(body: Mapping[str, Any], source: str) -> None:
    """The fourth refusal: the EMITTED `design` must equal the one derived
    from `hyperparameters`, and `len(coef)` must match that derived design.

    Without this, a body carrying `design: "poly2"`, nine coefficients and
    NO `degree` key would predict plausibly while hashing a recipe
    byte-identical to plain Ridge at the same alpha -- D-07-14's collision,
    re-entered through the side door. The check reads the RAW dict, because
    `FrozenLinearPredictor` has no field to disagree with: construction
    would simply derive `"linear"` and then refuse the nine coefficients
    with a message about the count, saying nothing about the claim the file
    actually made.
    """
    hyperparameters = body["hyperparameters"]
    if not isinstance(hyperparameters, Mapping):
        raise FrozenPredictorError(
            f"{source}: hyperparameters is a "
            f"{type(hyperparameters).__name__}, not a mapping -- the "
            "recipe cannot be hashed and the design cannot be derived"
        )
    emitted = body["design"]
    derived = _derive_design(hyperparameters)
    if emitted != derived:
        raise FrozenPredictorError(
            f"{source}: body emits design {emitted!r} but its "
            f"hyperparameters derive {derived!r} (degree = "
            f"{hyperparameters.get('degree')!r}) -- refusing a body whose "
            "stated design disagrees with the recipe its predictor_id was "
            "hashed from"
        )


def _predictor_from_body(body: Mapping[str, Any], source: str) -> FrozenLinearPredictor:
    """Every check a body must pass that does not need its filename, then
    construct. Shared by both doors into a body (`read_frozen_predictor`
    and `frozen_linear_from_artifact`) so neither can be the lenient one.
    """
    _require_body_keys(body, source)
    _require_design_agrees(body, source)
    predictor = FrozenLinearPredictor(
        model_class=body["model_class"],
        feature_names=tuple(body["feature_names"]),
        coef=tuple(body["coef"]),
        intercept=body["intercept"],
        normalization_manifest_id=body["normalization_manifest_id"],
        seed=body["seed"],
        code_hash=body["code_hash"],
        hyperparameters=body["hyperparameters"],
        train_target_mean=body["train_target_mean"],
        n_rows_fitted=body["n_rows_fitted"],
        n_rows_dropped=body["n_rows_dropped"],
    )
    stored_predictor_id = body["predictor_id"]
    if predictor.predictor_id != stored_predictor_id:
        raise FrozenPredictorError(
            f"{source}: stored predictor_id {stored_predictor_id!r} does "
            f"not match {predictor.predictor_id!r} re-derived from this "
            "body's own recipe fields (model_class, hyperparameters, seed, "
            "code_hash, normalization_manifest_id) -- refusing a body whose "
            "recipe hash no longer describes its recipe"
        )
    return predictor


def frozen_linear_from_artifact(artifact: Mapping[str, Any]) -> FrozenLinearPredictor:
    """Rebuild a `FrozenLinearPredictor` from an IN-MEMORY `to_artifact()`
    body -- `models.protocol.PREDICTOR_BUILDERS`' entry for every
    `model_class` in `LINEAR_MODEL_CLASSES`.

    Applies every refusal `read_frozen_predictor` does except the self-hash,
    which needs an id to compare against and therefore belongs to the
    on-disk door alone.
    """
    return _predictor_from_body(artifact, "frozen_linear_from_artifact")


def read_frozen_predictor(
    manifest_id: str, *, registry_root: Path
) -> FrozenLinearPredictor:
    """Read `registry_root/predictors/<manifest_id>.json` back into a
    `FrozenLinearPredictor`, refusing four ways -- all
    `FrozenPredictorError`, `harness.errata.read_errata_manifest`'s shape
    with a fourth check that registry does not need:

    1. NAMED-BUT-MISSING FAILS CLOSED. Never a default, never `None`. A
       predictor a caller asked for by id and did not get must stop the
       caller, not hand it an untrained model.
    2. THE SELF-HASH IS RE-VERIFIED AGAINST BOTH the id the caller passed
       AND the body's own `manifest_id` field, so a hand-edited body whose
       `manifest_id` was "helpfully" kept in sync with edited coefficients
       is caught exactly as an honestly stale one is.
    3. `predictor_id` IS RE-DERIVED from the body's five recipe fields and
       cross-checked against the stored one.
    4. The emitted `design` is cross-checked against the derived one, and
       `len(coef)` against that design.

    `registry_root` is KEYWORD-ONLY, matching `write_frozen_predictor`.

    WHY A BODY CARRIES BOTH IDS (D-07-22). `manifest_id` covers everything
    including the coefficients; `predictor_id` covers the recipe alone. Two
    runs of the SAME recipe that disagree on coefficients therefore land at
    the same `predictor_id` under DIFFERENT `manifest_id`s -- which is what
    makes D-07-12's determinism claim checkable as data on disk and not only
    as a test that ran once on one host.
    """
    path = predictor_registry_path(registry_root, manifest_id)
    if not path.exists():
        raise FrozenPredictorError(
            f"frozen predictor {manifest_id!r} is missing at {path} -- "
            "fail-closed: a named predictor that cannot be read is never "
            "degraded to a default or to None"
        )
    body = json.loads(path.read_text())
    if not isinstance(body, dict):
        raise FrozenPredictorError(
            f"{path}: body is a {type(body).__name__}, not a JSON object"
        )
    body_manifest_id = body.get("manifest_id")
    recomputed = compute_manifest_id(body)
    if recomputed != manifest_id or body_manifest_id != manifest_id:
        raise FrozenPredictorError(
            f"{path}: frozen predictor self-hash mismatch -- named id "
            f"{manifest_id}, body's own manifest_id field "
            f"{body_manifest_id!r}, recomputed {recomputed} from its own "
            "body -- refusing to trust a tampered or mismatched predictor"
        )
    return _predictor_from_body(body, str(path))
