"""D-07-27's conformance tests for the four REAL estimators, and the
collision the grid would otherwise hide.

`mypy` is not one of the pre-commit hooks, so nothing in CI reads a type
annotation and a `typing.Protocol` is documentation on its own.
`tests/models/test_protocol.py` (plan 07-03) proved the MECHANISM -- that
`@runtime_checkable` plus `isinstance` discriminates, that a stub minus one
member is rejected, and that no parameter name or annotation in
`protocol.py` names a class-specific knob. This file is the other half it
promised: the same checks against the 36 configurations that actually exist,
plus the two things only a real grid can be asked.

WHAT IS NEW HERE, AND WHAT IS DELIBERATELY NOT REPEATED. The signature rule
is re-asserted at the IMPLEMENTATION site rather than at `protocol.py`,
because that is where a knob would sprout: a Phase-8 trainer that needed
`early_stopping_rounds` would add it to its own `fit`, not to the Protocol.
`test_protocol.py`'s scan of `protocol.py` and its anti-vacuity counterpart
are not copied -- pre-commit runs the full suite on every commit, and a
duplicated scan is a second thing to maintain that checks the same file.

THE LOAD-BEARING TEST IN THIS FILE is
`test_every_grid_entry_has_a_distinct_predictor_id`. `models.
predictor_id` hashes exactly D-07-14's five fields; `design` is not one of
them, and both Ridge trainers report the byte-identical `model_class`
`"sklearn.Ridge"` on purpose. So `degree: 2` inside the poly2 trainer's
`hyperparameters` is the ONLY thing separating its nine configs from the
plain Ridge configs at the same nine alphas -- and the test measures the
collapse to 27 when it is removed, which is the only form of that claim that
cannot be satisfied by a coincidence. Plan 07-03's five-way sensitivity test
structurally cannot catch it: `design` is not one of the five fields it
varies.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from models.frozen import FrozenLinearPredictor
from models.predictor_id import predictor_id
from models.protocol import FitInputs, FrozenPredictor, Trainer, predictor_from_artifact
from models.regression import (
    GRID,
    GRID_SIZE,
    RIDGE_MODEL_CLASS,
    ElasticNetTrainer,
    FitContext,
    GridEntry,
    LinearRegressionTrainer,
    Poly2RidgeTrainer,
    RidgeTrainer,
    build_grid,
)
from tests.fixtures import model_fit

#: Fixed recipe fields for the id tests: the point is that the ids differ
#: because the CONFIGS differ, so everything else is held byte-identical.
FIXED_SEED: int = 20260925
FIXED_CODE_HASH: str = "c" * 40
FIXED_NORMALIZATION_ID: str = "d" * 64

#: One cheap config per class -- what the fitted-predictor conformance test
#: needs, and nothing more: the claim is about the TYPE of what `fit`
#: returns, not about any coefficient.
CHEAPEST_PER_CLASS: dict[str, GridEntry] = {
    "LinearRegression": GridEntry(LinearRegressionTrainer, {}),
    "Ridge": GridEntry(RidgeTrainer, {"alpha": 1.0}),
    "ElasticNet": GridEntry(ElasticNetTrainer, {"alpha": 1e-6, "l1_ratio": 0.5}),
    "Ridge+poly2": GridEntry(Poly2RidgeTrainer, {"alpha": 1e-3}),
}

#: The four concrete trainer classes, for the signature scan.
TRAINER_CLASSES: tuple[type, ...] = (
    LinearRegressionTrainer,
    RidgeTrainer,
    ElasticNetTrainer,
    Poly2RidgeTrainer,
)

#: Tokens no trainer's `fit` signature may name. Same list as
#: `test_protocol.py`'s, for the same reason: every one of them is a
#: per-class knob that already travels through `hyperparameters` and
#: therefore through `predictor_id`.
BANNED_SIGNATURE_TOKENS: tuple[str, ...] = (
    "sklearn",
    "epochs",
    "batch",
    "device",
    "early_stopping",
    "X_train",
    "y_eval",
)


def _context(tmp_path: Path) -> FitContext:
    return model_fit.make_context(tmp_path)


# ------------------------------------------------------- runtime conformance


@pytest.mark.parametrize("index", range(GRID_SIZE))
def test_every_grid_entry_is_a_trainer_at_runtime(index, tmp_path):
    """All 36, one parametrised case each, so a typo'd method name shows up
    at the top of a sweep instead of after the first fold.

    `isinstance` against a `@runtime_checkable` Protocol checks member
    PRESENCE, not signatures. That is exactly the accident worth catching
    here -- `def ft(self, inputs)` -- and it is not a type check.
    """
    trainer = GRID[index].build(_context(tmp_path))
    assert isinstance(trainer, Trainer)
    assert isinstance(trainer.model_class, str) and trainer.model_class
    assert dict(trainer.hyperparameters) == dict(trainer.hyperparameters)


def test_the_grid_is_thirty_six_configs_composed_the_way_d_07_08_counts_them(tmp_path):
    """The COUNT is the claim (D-07-08: a fixed, hand-written, counted grid,
    no Optuna), and it is also the selection-bias denominator: rows 17-35 were
    appended after the first sweep of rows 0-16 returned 0 of 17 eligible, so
    36 is the number of configurations this phase has tried, not the number it
    currently likes. 1 OLS + 12 Ridge + 14 ElasticNet + 9 Ridge-on-poly2,
    asserted per class so a row moved between groups is not invisible."""
    assert len(GRID) == GRID_SIZE == 36
    per_class: dict[type, int] = {}
    for entry in GRID:
        per_class[entry.trainer_class] = per_class.get(entry.trainer_class, 0) + 1
    assert per_class == {
        LinearRegressionTrainer: 1,
        RidgeTrainer: 12,
        ElasticNetTrainer: 14,
        Poly2RidgeTrainer: 9,
    }
    # And every entry really builds -- a grid whose rows cannot be
    # constructed is a count of nothing.
    assert len(build_grid(_context(tmp_path))) == GRID_SIZE


@pytest.mark.parametrize("label", sorted(CHEAPEST_PER_CLASS))
def test_every_fitted_estimator_is_a_frozen_predictor_at_runtime(label, tmp_path):
    """The second half of D-07-27, on a real fit: whatever each class
    returns satisfies `FrozenPredictor`, and the registry can rebuild it.

    The round-trip is what ties this to 07-04: `predictor_from_artifact`
    dispatches on `model_class`, so a class string this module invented
    without registering would be refused here rather than at the first
    attempt to reload a committed body.
    """
    inputs, _columns = model_fit.learnable_fit_inputs(tmp_path, rows=1_000)
    trainer = CHEAPEST_PER_CLASS[label].build(_context(tmp_path))
    fitted = trainer.fit(inputs)

    assert isinstance(fitted, FrozenPredictor)
    assert not isinstance(fitted, tuple), (
        "fit returned a tuple -- the dropped-row count is a FIELD on the "
        "predictor, not a second return value, or the interface Phase 8 "
        "implements has a shape only linear models have"
    )
    assert isinstance(fitted, FrozenLinearPredictor)
    rebuilt = predictor_from_artifact(fitted.to_artifact())
    assert isinstance(rebuilt, FrozenPredictor)
    assert rebuilt.predictor_id == fitted.predictor_id
    assert rebuilt.coef == fitted.coef
    assert trainer.last_fit_seconds is not None and trainer.last_fit_seconds > 0.0


def test_an_object_shaped_like_a_grid_entrys_trainer_minus_fit_is_not_a_trainer(
    tmp_path,
):
    """Anti-vacuity, anchored to the REAL trainers rather than to a
    hand-written stub (`test_protocol.py` covers the stub case).

    `RidgeTrainer` with `fit` shadowed by a non-callable attribute keeps its
    `model_class` and `hyperparameters` and is still rejected -- so the
    conformance assertions above are discriminating, not decorative.
    """

    class _RidgeWithoutFit(RidgeTrainer):
        fit = None  # type: ignore[assignment]

    broken = _RidgeWithoutFit(alpha=1.0, context=_context(tmp_path))
    assert broken.model_class == RIDGE_MODEL_CLASS
    assert dict(broken.hyperparameters) == {"alpha": 1.0}
    assert not isinstance(broken, Trainer), (
        "runtime_checkable accepted a trainer whose fit is not callable -- "
        "then every isinstance assertion in this file is vacuous"
    )


# ------------------------------------------------- the signature constraint


def _signature_violations(function: object, *, name: str) -> list[str]:
    """Every banned token any parameter NAME or ANNOTATION of `function`
    contains, plus a complaint if the parameter list is not exactly
    `(self, inputs)`.

    Reads `inspect.signature`, not file text: the rule is about SIGNATURES
    (`protocol.py`'s docstring has to SAY `sklearn` and `early_stopping` in
    order to state it, and this module's own `BANNED_SIGNATURE_TOKENS` names
    them too).
    """
    signature = inspect.signature(function)  # type: ignore[arg-type]
    violations: list[str] = []
    texts: list[str] = []
    for parameter in signature.parameters.values():
        texts.append(parameter.name)
        if parameter.annotation is not inspect.Parameter.empty:
            texts.append(str(parameter.annotation))
    for text in texts:
        for token in BANNED_SIGNATURE_TOKENS:
            if token.lower() in text.lower():
                violations.append(f"{name}: {text!r} names {token!r}")
    if tuple(signature.parameters) != ("self", "inputs"):
        violations.append(
            f"{name}: fit takes {tuple(signature.parameters)}, not "
            "(self, inputs) -- data-only, per D-07-04"
        )
    return violations


@pytest.mark.parametrize("trainer_class", TRAINER_CLASSES, ids=lambda c: c.__name__)
def test_no_trainers_fit_widens_the_protocol_with_a_model_class_knob(trainer_class):
    """The Phase-8 constraint where it would actually break: an estimator's
    own `fit`.

    `Trainer.fit` takes ONE `FitInputs` and nothing else, so LightGBM's
    early-stopping set and a transformer's epochs stay inside
    `hyperparameters` -- where `predictor_id` already hashes them -- instead
    of becoming protocol parameters every future class must carry.
    """
    violations = _signature_violations(
        trainer_class.fit, name=f"{trainer_class.__name__}.fit"
    )
    assert not violations, violations


def test_the_signature_scan_flags_a_fit_it_is_shown_with_a_knob_in_it():
    """Anti-vacuity: a scan that walked nothing would pass the four cases
    above forever. Shown the exact widening the rule forbids -- an eval set
    and an early-stopping budget -- it must report both."""

    class _WidenedTrainer:
        def fit(self, inputs, X_eval=None, *, early_stopping_rounds: int = 0):
            return None

    violations = _signature_violations(_WidenedTrainer.fit, name="widened")
    assert any("early_stopping" in item for item in violations), violations
    assert any("(self, inputs)" in item for item in violations), violations


# --------------------------------------------------- the 36 distinct recipes


def _ids_for(hyperparameter_sets: list[tuple[str, dict]]) -> list[str]:
    return [
        predictor_id(
            model_class=model_class,
            hyperparameters=knobs,
            seed=FIXED_SEED,
            code_hash=FIXED_CODE_HASH,
            normalization_manifest_id=FIXED_NORMALIZATION_ID,
        )
        for model_class, knobs in hyperparameter_sets
    ]


def test_every_grid_entry_has_a_distinct_predictor_id(tmp_path):
    """The collision that would otherwise be invisible, and the measurement
    that proves `degree` is what prevents it.

    Held byte-identical across all 36: the seed, the code hash and the
    normalisation manifest id. So two configs can only differ through
    `model_class` and `hyperparameters` -- and both Ridge trainers report the
    SAME `model_class`, which is asserted here rather than assumed. Strip
    `degree` and NINE pairs become byte-identical recipes: 27 distinct ids,
    `harness.negative_log` deduplicating away nine real ineligible configs
    (falsifying D-07-20), and a stored table's `predictor=<first 16>`
    directory naming a config that never produced it.
    """
    trainers = build_grid(_context(tmp_path))
    recipes = [(t.model_class, dict(t.hyperparameters)) for t in trainers]
    ids = _ids_for(recipes)
    assert len(ids) == GRID_SIZE
    assert len(set(ids)) == GRID_SIZE, {
        identifier: [r for r, i in zip(recipes, ids, strict=True) if i == identifier]
        for identifier in ids
        if ids.count(identifier) > 1
    }

    # The pin the collapse depends on: the degree-2 trainer is a
    # `"sklearn.Ridge"`, byte-for-byte.
    assert Poly2RidgeTrainer.MODEL_CLASS == RidgeTrainer.MODEL_CLASS
    assert Poly2RidgeTrainer.MODEL_CLASS == RIDGE_MODEL_CLASS

    # The measured collapse. Remove `degree` from the recipe and the NINE
    # poly2 configs become the nine Ridge configs at the same nine alphas.
    stripped = [
        (model_class, {k: v for k, v in knobs.items() if k != "degree"})
        for model_class, knobs in recipes
    ]
    collapsed = _ids_for(stripped)
    assert len(set(collapsed)) == 27, sorted({f"{c}:{k}" for c, k in stripped})
    assert len(set(ids)) - len(set(collapsed)) == 9


def test_the_three_shared_alphas_are_the_ones_degree_separates(tmp_path):
    """Which nine pairs, named -- so the number 27 above is a consequence of
    a stated overlap rather than a magic constant."""
    trainers = build_grid(_context(tmp_path))
    plain = {
        t.hyperparameters["alpha"]
        for t in trainers
        if type(t) is RidgeTrainer  # noqa: E721 -- Poly2RidgeTrainer subclasses it
    }
    poly2 = {
        t.hyperparameters["alpha"] for t in trainers if isinstance(t, Poly2RidgeTrainer)
    }
    assert poly2 == {1e-3, 1.0, 100.0, 3e7, 1e8, 3e8, 1e9, 3e9, 1e11}
    assert poly2 < plain, (plain, poly2)
    assert len(poly2) == 9


def test_two_fits_of_one_config_on_different_rows_share_an_id_and_differ_in_body(
    tmp_path,
):
    """D-07-22, from the other side: `n_rows_fitted` and
    `n_rows_dropped` are BODY fields, so the same recipe fit on different
    rows keeps ONE `predictor_id`.

    Inside `hyperparameters` they would give every OOF block's fit of one
    config a different id, which is what would destroy the deduplication
    D-07-20 relies on.
    """
    inputs, _columns = model_fit.learnable_fit_inputs(tmp_path, rows=1_000)
    trainer = RidgeTrainer(alpha=1.0, context=_context(tmp_path))
    whole = trainer.fit(inputs)
    subset = trainer.fit(
        FitInputs(
            cache_path=inputs.cache_path,
            feature_names=inputs.feature_names,
            target_name=inputs.target_name,
            row_mask_path=model_fit.write_row_mask(
                tmp_path / "half.parquet",
                [index % 2 == 0 for index in range(1_000)],
            ),
            normalization_manifest_id=inputs.normalization_manifest_id,
            seed=inputs.seed,
        )
    )
    assert whole.predictor_id == subset.predictor_id
    assert whole.n_rows_fitted != subset.n_rows_fitted
    assert whole.coef != subset.coef
