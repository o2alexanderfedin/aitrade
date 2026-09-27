"""The Trainer/FrozenPredictor boundary (FCST-04) -- the one interface
Phase 8 must be able to implement LightGBM and a PyTorch transformer
against WITHOUT editing this file.

That constraint is what every choice below is for, and each one is written
down because the naive alternative is the one a later phase reaches for:

1. `fit` takes ONE `FitInputs`, never an `(X_train, y_train, X_eval,
   y_eval)` quadruple. LightGBM's early stopping needs an evaluation set
   DURING the fit, but that is a trainer's private business, carved out of
   the rows it was given; a scikit-learn trainer that will never
   early-stop is not made to carry two dead parameters for the rest of the
   project.
2. `FitInputs` names a cached PARQUET PATH plus column names, never
   materialised arrays. 44,249,548 decision rows at 101 bytes each is
   4.5 GiB, so "just pass the arrays" is not neutral at this scale. A
   scikit-learn trainer calls `.to_numpy()`; a torch trainer streams row
   groups. Neither imposes its shape on the other.
3. `epochs`, `batch_size`, `device`, `n_estimators` and friends are
   HYPERPARAMETERS, not protocol parameters. They already flow through
   `hyperparameters`, which is also exactly what
   `models.predictor_id.predictor_id` hashes (D-07-14). A protocol that
   named them would have to name every future model class's knobs too, and
   the next class after that.
4. `predict` takes ALREADY-NORMALISED features and returns the TARGET's
   unit -- a 10-second simple return (D-07-10), never a price (D-07-23,
   D-07-33). Normalisation is resolved from a manifest (D-07-11) and is
   identical for every model class; putting it inside `predict` would
   duplicate it once per class and would force D-07-17's numpy-only
   re-evaluation test to import the normalisation loader, which is exactly
   the import that test exists to avoid.

NO `sklearn` TYPE APPEARS IN ANY ANNOTATION IN THIS FILE, and the words
`epochs`, `batch`, `device` and `early_stopping` appear in NO parameter
name and NO annotation here. THE RULE IS ABOUT SIGNATURES, NOT ABOUT THIS
FILE'S TEXT, and the distinction is load-bearing: the paragraphs above
must SAY `sklearn`, `epochs` and `early_stopping` in order to state the
constraint at all, and `predictor_from_artifact` dispatches on
`model_class` values that are literally the strings `"sklearn.Ridge"` and
friends. A check spelled `'sklearn' not in inspect.getsource(...)` would
forbid the very documentation and the very registry this design requires;
`tests/models/test_protocol.py` therefore reads parameter names and
annotation nodes off the AST, and reaches no docstring. The only string
constants it does reach are string-quoted forward-reference ANNOTATIONS --
which is exactly what the rule is about, not an exception to it.

WHY `typing.Protocol` AT ALL, IN A REPO THAT HAS NEVER DECLARED AN
INTERFACE. Pluggability here has so far been function injection plus
`NamedTuple`/frozen dataclass (`data/backfill/client.py`'s callable
aliases, `tracking/mlflow_utils.compute_code_hash`'s injected
`git_runner`, `sim/outputs.SimResult`). None of those can express "two
behaviours that must stay implementable by three unrelated libraries", so
this is the first Protocol (D-07-27). And because `mypy` is NOT one of the
pre-commit hooks, a Protocol on its own is documentation the type checker
never reads -- which is why both Protocols are `@runtime_checkable` and
why every implementation gets an explicit `isinstance` conformance test.
`isinstance` checks member PRESENCE, not signatures, so it catches a
typo'd or missing method at the sweep's registration point instead of
after the first fold. That is the whole claim; it is not a type check.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

__all__ = [
    "FitInputs",
    "PredictInputs",
    "FrozenPredictor",
    "Trainer",
    "UnknownModelClassError",
    "PREDICTOR_BUILDERS",
    "predictor_from_artifact",
]


@dataclass(frozen=True)
class FitInputs:
    """Everything a trainer may read, and nothing about HOW to read it.

    `cache_path` is the D-07-05 scratch Parquet for one segment: the
    accessor's frame, materialized ONCE and cached, so a fit, a re-fit
    after a crash and a prediction pass all read the same bytes instead of
    spending a second validation look.

    `row_mask_path` is the OOF training-row selection when it is not the
    whole cache, and `None` when it is. It is a PATH rather than an array
    for the same reason `cache_path` is: the mask for a 7.9M-row block is
    itself worth not holding twice.
    """

    cache_path: Path
    feature_names: tuple[str, ...]
    target_name: str
    row_mask_path: Path | None
    normalization_manifest_id: str
    seed: int


@dataclass(frozen=True)
class PredictInputs:
    """The same shape for the validation/OOF read, minus every fit-only
    field -- so a prediction pass can never be handed a target name or a
    seed and quietly use one."""

    cache_path: Path
    feature_names: tuple[str, ...]
    normalization_manifest_id: str


@runtime_checkable
class FrozenPredictor(Protocol):
    """A fitted model reduced to what is needed to reproduce its
    predictions, and nothing else."""

    @property
    def predictor_id(self) -> str:
        """D-07-14's recipe hash -- `models.predictor_id.predictor_id` over
        the estimator class name, the sorted hyperparameters, the seed, the
        code hash and the normalization manifest id."""

    @property
    def model_class(self) -> str:
        """The MLflow `model_class` tag value for this predictor."""

    def predict(self, features: np.ndarray) -> np.ndarray:
        """`(n, p)` float64 ALREADY-NORMALISED features -> `(n,)` float64
        predictions in the TARGET's unit. Pure and pointwise: no I/O, no
        global state, no batching visible at this boundary."""

    def to_artifact(self) -> dict[str, Any]:
        """A JSON-serialisable body (D-07-17). Never a pickle, and never
        `cloudpickle`: a linear artifact holds coefficients, a boosted one
        a model dump, a network one a weights-file digest -- only the
        dict-ness is protocol."""


@runtime_checkable
class Trainer(Protocol):
    """Fits ONE configuration. Constructed with its own knobs; `fit` takes
    only data."""

    @property
    def model_class(self) -> str:
        """The MLflow `model_class` tag value this trainer will stamp on
        the predictor it returns."""

    @property
    def hyperparameters(self) -> Mapping[str, Any]:
        """The full, JSON-able configuration -- what D-07-14 hashes and
        what MLflow logs. A trainer with no knobs returns `{}`."""

    def fit(self, inputs: FitInputs) -> FrozenPredictor:
        """Read `inputs`, fit, and return a frozen predictor. How the rows
        are read, split or iterated is this trainer's own business."""


class UnknownModelClassError(ValueError):
    """`predictor_from_artifact` was handed a `model_class` no builder is
    registered for -- refused loudly, never rebuilt as a best guess."""

    def __init__(self, model_class: object, known: tuple[str, ...]) -> None:
        self.model_class = model_class
        self.known = known
        super().__init__(
            f"predictor_from_artifact: no builder registered for "
            f"model_class {model_class!r} -- registered: "
            f"{list(known)}. Register the builder in "
            "models.protocol.PREDICTOR_BUILDERS; do not widen this "
            "Protocol."
        )


#: `model_class` -> the function that rebuilds a `FrozenPredictor` from a
#: `to_artifact()` body. THE EXTENSION POINT: Phase 8 adds an entry here
#: and edits nothing else in this module. Empty at import; plan 07-04's
#: linear builder is registered lazily by `_register_builtin_builders`
#: below, on the first dispatch.
PREDICTOR_BUILDERS: dict[str, Callable[[Mapping[str, Any]], FrozenPredictor]] = {}


def _register_builtin_builders() -> None:
    """Register `models.frozen`'s coefficient-JSON builder for every
    `model_class` Phase 7 produces.

    THE IMPORT IS INSIDE THE FUNCTION BODY, never at module scope, and that
    is not a style choice: `models.frozen` is free to import this module
    (Phase 8's builders will need `FitInputs`), and a module-scope import
    here would close the cycle. `setdefault`, so a builder a later phase
    registered by hand -- by adding an entry, which is the whole point of
    the registry -- is never overwritten by this one.
    """
    from models.frozen import LINEAR_MODEL_CLASSES, frozen_linear_from_artifact

    for model_class in LINEAR_MODEL_CLASSES:
        PREDICTOR_BUILDERS.setdefault(model_class, frozen_linear_from_artifact)


def predictor_from_artifact(artifact: Mapping[str, Any]) -> FrozenPredictor:
    """Rebuild a `FrozenPredictor` from a `to_artifact()` body, dispatching
    on `artifact["model_class"]` through `PREDICTOR_BUILDERS`.

    DELIBERATELY A FREE FUNCTION, not a classmethod: a Protocol cannot
    carry one, and the registry of known classes is what Phase 8 extends --
    by adding an entry, not by editing the Protocol. Raises
    `UnknownModelClassError` for an unregistered class and `KeyError` for a
    body with no `model_class` at all, because a body that cannot say what
    it is must not be rebuilt as whatever happens to be registered first.

    `artifact["model_class"]` is read BEFORE the builders are registered, so
    a body that cannot say what it is still raises `KeyError` and never
    pays for an import it has no use for.
    """
    model_class = artifact["model_class"]
    _register_builtin_builders()
    builder = PREDICTOR_BUILDERS.get(model_class)
    if builder is None:
        raise UnknownModelClassError(model_class, tuple(sorted(PREDICTOR_BUILDERS)))
    return builder(artifact)
