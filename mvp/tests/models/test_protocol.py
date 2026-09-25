"""What makes `models/protocol.py` load-bearing rather than decorative.

Two separate claims, and neither is checked by anything else in this repo:

1. CONFORMANCE IS CHECKABLE AT RUNTIME. `mypy` is in CLAUDE.md's stack but
   is NOT one of the pre-commit hooks, so a `typing.Protocol` on its own is
   documentation nothing reads (D-07-27). `@runtime_checkable` plus
   `isinstance` turns it into a presence check that fires at the sweep's
   registration point instead of after the first fold -- but only if the
   check actually DISCRIMINATES, which is what the two paired tests below
   measure: a complete stub passes, the same stub minus one member fails.
   Plan 07-04 onward adds the per-estimator conformance test D-07-27 asks
   for; this file proves the mechanism those tests will rely on.

2. THE NO-SKLEARN-NO-KNOBS RULE IS ABOUT SIGNATURES, NOT ABOUT THE FILE'S
   TEXT. `protocol.py`'s docstring MUST say `sklearn`, `epochs` and
   `early_stopping` in order to state the constraint, and
   `predictor_from_artifact` dispatches on `model_class` values that are
   literally `"sklearn.Ridge"`. So the check reads PARAMETER NAMES and
   ANNOTATION NODES off the AST, and needs no docstring exemption (the
   helper `tools/check_latest_ban.py:_docstring_nodes` provides): a
   docstring is the first `ast.Expr` of a scope's BODY, and
   `_signature_tokens` below descends into `args` and `returns` only, so
   it never reaches one. It DOES reach `ast.Constant` -- a string-quoted
   forward reference like `estimator: 'sklearn.linear_model.Ridge'` is
   one, and `test_the_signature_scan_flags_a_banned_parameter_it_is_shown`
   depends on that. A string ANNOTATION is exactly what the rule is
   about; a docstring is what it must not read.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import numpy as np
import pytest

from models import protocol as protocol_module
from models.protocol import (
    FitInputs,
    FrozenPredictor,
    PredictInputs,
    Trainer,
    UnknownModelClassError,
    predictor_from_artifact,
)

#: Tokens that must not appear in any parameter name or annotation in
#: `protocol.py`. `sklearn` because naming one library's type in the
#: interface is what stops Phase 8 adding LightGBM and a transformer to it;
#: the other four because they are per-class KNOBS that already travel
#: through `Trainer.hyperparameters` (and therefore through
#: `predictor_id`), and a protocol that named them would have to name every
#: future class's knobs too.
BANNED_SIGNATURE_TOKENS: tuple[str, ...] = (
    "sklearn",
    "epochs",
    "batch",
    "device",
    "early_stopping",
)

PROTOCOL_SOURCE_PATH = Path(inspect.getsourcefile(protocol_module))


def _signature_tokens(source: str) -> list[tuple[str, str]]:
    """Every `(function_name, text)` pair a signature contributes: each
    parameter's NAME, each parameter's unparsed ANNOTATION, and the
    unparsed RETURN annotation. Nothing else -- no docstring, no string
    constant, no body."""
    tree = ast.parse(source)
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        spec = node.args
        every_arg = [
            *spec.posonlyargs,
            *spec.args,
            *spec.kwonlyargs,
            *([spec.vararg] if spec.vararg else []),
            *([spec.kwarg] if spec.kwarg else []),
        ]
        for arg in every_arg:
            found.append((node.name, arg.arg))
            if arg.annotation is not None:
                found.append((node.name, ast.unparse(arg.annotation)))
        if node.returns is not None:
            found.append((node.name, ast.unparse(node.returns)))
    return found


def _banned_hits(source: str) -> list[tuple[str, str, str]]:
    """`(function, text, token)` for every banned token any signature text
    contains, case-insensitively."""
    return [
        (function, text, token)
        for function, text in _signature_tokens(source)
        for token in BANNED_SIGNATURE_TOKENS
        if token in text.lower()
    ]


def test_no_parameter_name_or_annotation_in_protocol_py_names_a_class_specific_knob():
    """The rule itself, over EVERY function in the file rather than the four
    public ones a hand-written list would name -- so a fifth signature
    Phase 8 adds is covered the day it is added."""
    source = PROTOCOL_SOURCE_PATH.read_text(encoding="utf-8")
    hits = _banned_hits(source)
    assert not hits, f"protocol.py signature leaks a class-specific token: {hits}"

    texts = _signature_tokens(source)
    functions = {function for function, _text in texts}
    assert len(functions) >= 6, (
        f"vacuous: the scan found only {sorted(functions)} -- protocol.py "
        "declares at least six functions (two properties and one method on "
        "each Protocol, plus predictor_from_artifact)"
    )
    assert any("FitInputs" in text for _f, text in texts), (
        "vacuous: the scan collected no annotation text at all"
    )


def test_the_signature_scan_flags_a_banned_parameter_it_is_shown():
    """The anti-vacuity counterpart: a scan that reported nothing because it
    walks nothing passes the test above forever. Fed a signature with the
    exact leak the rule forbids -- the `(X_train, y_train, X_eval, y_eval)`
    quadruple plus an `early_stopping_rounds` knob -- it must report it."""
    bad = (
        "def fit(self, inputs, *, early_stopping_rounds: int = 0) -> None: ...\n"
        "def build(self, estimator: 'sklearn.linear_model.Ridge') -> None: ...\n"
    )
    hits = _banned_hits(bad)
    tokens = {token for _f, _t, token in hits}
    assert tokens == {"early_stopping", "sklearn"}, hits


def test_protocol_pys_docstring_does_name_sklearn_and_the_knobs_it_forbids():
    """The other half of "signatures, not text": the module prose has to say
    the forbidden words to state the constraint, and the registry has to
    carry `"sklearn.Ridge"` as a dispatch key. A check spelled
    `'sklearn' not in inspect.getsource(...)` would forbid exactly the
    documentation this design needs, so this test pins the words' PRESENCE
    -- if someone later strips the docstring to satisfy a naive grep, this
    fails."""
    docstring = (protocol_module.__doc__ or "").lower()
    for token in ("sklearn", "epochs", "early_stopping", "batch_size", "device"):
        assert token in docstring, f"protocol.py's docstring no longer states {token!r}"


# --------------------------------------------------------------------------
# Conformance: the runtime check must discriminate
# --------------------------------------------------------------------------


class _ConformingPredictor:
    """The minimum a `FrozenPredictor` implementation is: two properties and
    two methods. Deliberately not a model -- `predict` returns its input's
    first column, because what is under test is the BOUNDARY, not a fit."""

    predictor_id = "f" * 64
    model_class = "test.Conforming"

    def predict(self, features: np.ndarray) -> np.ndarray:
        return np.asarray(features, dtype=np.float64)[:, 0]

    def to_artifact(self) -> dict:
        return {"model_class": self.model_class, "predictor_id": self.predictor_id}


class _PredictorMissingToArtifact:
    """`_ConformingPredictor` minus `to_artifact` -- D-07-17's only
    serialisation route, and the member whose absence would otherwise be
    discovered at the end of a sweep when the artifact is written."""

    predictor_id = "f" * 64
    model_class = "test.Incomplete"

    def predict(self, features: np.ndarray) -> np.ndarray:
        return np.asarray(features, dtype=np.float64)[:, 0]


class _ConformingTrainer:
    model_class = "test.Conforming"
    hyperparameters: dict = {}

    def fit(self, inputs: FitInputs) -> _ConformingPredictor:
        return _ConformingPredictor()


class _TrainerWithATypoInFit:
    """The exact accident `isinstance` exists to catch: `ft` instead of
    `fit`. Without the check this registers fine and raises
    `AttributeError` after the first fold's rows have been materialized --
    which, on a validation segment, costs an irreversible look."""

    model_class = "test.Typo"
    hyperparameters: dict = {}

    def ft(self, inputs: FitInputs) -> _ConformingPredictor:
        return _ConformingPredictor()


def test_a_complete_stub_is_a_frozen_predictor_and_one_missing_to_artifact_is_not():
    assert isinstance(_ConformingPredictor(), FrozenPredictor)
    assert not isinstance(_PredictorMissingToArtifact(), FrozenPredictor), (
        "the runtime check does not discriminate -- a predictor with no "
        "to_artifact would pass registration"
    )


def test_a_complete_stub_is_a_trainer_and_one_with_a_typo_in_fit_is_not():
    assert isinstance(_ConformingTrainer(), Trainer)
    assert not isinstance(_TrainerWithATypoInFit(), Trainer), (
        "the runtime check does not discriminate -- a trainer whose fit is "
        "misspelled would pass registration"
    )


def test_issubclass_is_not_available_on_these_protocols_so_use_isinstance():
    """A documented limitation, pinned so a later phase does not spend a
    debugging session on it: both Protocols carry non-method members
    (`predictor_id`, `model_class`, `hyperparameters`), and `typing`
    refuses `issubclass` for those. The conformance check is therefore on
    INSTANCES, which is also where a sweep has one."""
    with pytest.raises(TypeError, match="non-method members"):
        issubclass(_ConformingPredictor, FrozenPredictor)


# --------------------------------------------------------------------------
# The data records, and the registry's refusal
# --------------------------------------------------------------------------


def test_fit_inputs_and_predict_inputs_are_frozen_and_carry_exactly_their_fields():
    """Frozen, because a trainer that could rewrite its own `FitInputs`
    could rewrite the row selection it was given -- the leak `FitInputs`
    exists to make auditable. And `PredictInputs` carries NO `target_name`
    and NO `seed`: a prediction pass that could see a target name is a
    prediction pass that could score itself."""
    fit_fields = tuple(f.name for f in dataclasses.fields(FitInputs))
    assert fit_fields == (
        "cache_path",
        "feature_names",
        "target_name",
        "row_mask_path",
        "normalization_manifest_id",
        "seed",
    )
    predict_fields = tuple(f.name for f in dataclasses.fields(PredictInputs))
    assert predict_fields == (
        "cache_path",
        "feature_names",
        "normalization_manifest_id",
    )
    assert "target_name" not in predict_fields
    assert "seed" not in predict_fields

    inputs = PredictInputs(
        cache_path=Path("/nonexistent/cache.parquet"),
        feature_names=("imb_top", "ofi", "trade_flow"),
        normalization_manifest_id="e" * 64,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        inputs.cache_path = Path("/elsewhere.parquet")


def test_predictor_from_artifact_refuses_an_unregistered_model_class_naming_it():
    """The registry is Phase 8's extension point, and in plan 07-03 it is
    EMPTY -- the linear implementation registers itself in 07-04. An empty
    registry must refuse loudly rather than rebuild a body as whatever is
    registered first."""
    with pytest.raises(UnknownModelClassError, match="lightgbm.LGBMRegressor"):
        predictor_from_artifact({"model_class": "lightgbm.LGBMRegressor"})


def test_predictor_from_artifact_refuses_a_body_that_does_not_say_what_it_is():
    with pytest.raises(KeyError, match="model_class"):
        predictor_from_artifact({"coefficients": [1.0, 2.0, 3.0]})
