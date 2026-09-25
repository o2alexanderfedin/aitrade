"""D-07-14's recipe hash, asserted field by field.

The claim `models/predictor_id.py` makes is that its id moves when, and
only when, one of five named things about a fit moves. A hash is the kind
of code that looks right while ignoring an argument entirely -- so the
sensitivity test below varies each of the five ALONE and asserts all six
ids (the base plus five variants) are distinct, rather than asserting a
single inequality that any one live field would satisfy.
"""

from __future__ import annotations

import numpy as np
import pytest

from data.store import compute_manifest_id
from models.predictor_id import (
    RECIPE_FIELDS,
    UnhashableHyperparameterError,
    predictor_id,
)

BASE = {
    "model_class": "sklearn.Ridge",
    "hyperparameters": {"alpha": 1.0, "fit_intercept": True, "solver": "auto"},
    "seed": 20260925,
    "code_hash": "a" * 40,
    "normalization_manifest_id": "b" * 64,
}


def test_predictor_id_hashes_exactly_the_five_recipe_fields_and_nothing_else():
    """Acceptance criterion in the plan's own words: `predictor_id` is
    literally a `compute_manifest_id` call over a five-key dict. Asserted
    by recomputing the id from a hand-written five-key body -- if the
    function ever grows a sixth field, or drops one, this equality breaks
    and no amount of reading the source can hide it."""
    assert set(RECIPE_FIELDS) == set(BASE)
    expected = compute_manifest_id(dict(BASE))
    assert predictor_id(**BASE) == expected
    assert len(expected) == 64


def test_predictor_id_is_stable_under_hyperparameter_key_reordering():
    """`harness.negative_log.config_fingerprint`'s reason, inherited: two
    structurally-identical configurations get the same id regardless of key
    order, because `data.store.canonicalize_manifest` sorts keys -- nested
    ones included, which is the half that matters here since the
    hyperparameters are a nested mapping.

    The anti-vacuity half: the two mappings must genuinely differ in
    iteration order, or this asserts nothing at all."""
    forward = {"alpha": 1.0, "fit_intercept": True, "solver": "auto"}
    backward = {"solver": "auto", "fit_intercept": True, "alpha": 1.0}
    assert list(forward) != list(backward), (
        "vacuous: the two hyperparameter mappings iterate in the same order"
    )
    assert predictor_id(**{**BASE, "hyperparameters": forward}) == predictor_id(
        **{**BASE, "hyperparameters": backward}
    )


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("model_class", "sklearn.ElasticNet"),
        ("hyperparameters", {"alpha": 2.0, "fit_intercept": True, "solver": "auto"}),
        ("seed", 20260926),
        ("code_hash", "c" * 40),
        ("normalization_manifest_id", "d" * 64),
    ],
)
def test_predictor_id_changes_when_each_of_the_five_recipe_fields_changes(
    field, changed
):
    """One field at a time, and the whole set collected below -- a field
    the hash ignored would show up as a duplicate id here and nowhere
    else."""
    assert predictor_id(**{**BASE, field: changed}) != predictor_id(**BASE)


def test_the_five_single_field_variants_and_the_base_are_six_distinct_ids():
    """The parametrisation above proves each variant differs from the base;
    it cannot prove two variants do not collide with EACH OTHER. Six
    distinct ids does."""
    variants = [
        {"model_class": "sklearn.ElasticNet"},
        {"hyperparameters": {"alpha": 2.0, "fit_intercept": True, "solver": "auto"}},
        {"seed": 20260926},
        {"code_hash": "c" * 40},
        {"normalization_manifest_id": "d" * 64},
    ]
    ids = {predictor_id(**BASE)} | {
        predictor_id(**{**BASE, **variant}) for variant in variants
    }
    assert len(ids) == 1 + len(variants), f"collision among {sorted(ids)}"


def test_predictor_id_coerces_a_numpy_scalar_hyperparameter_to_the_plain_python_id():
    """THE CHOSEN BEHAVIOUR IS COERCE, NOT REFUSE, and this test is where
    that choice is stated: `np.float64(0.1)` and `0.1` are the same
    configuration and get the same id, so a sweep that pulls `alpha` out of
    a numpy grid cannot fork the id space away from a hand-written config.

    `np.int64(2)` coerces to `2` and NOT to `2.0` -- asserted here rather
    than assumed, because a blanket `float()` coercion would silently give
    `degree: 2` the id of a `degree: 2.0` nobody wrote."""
    numpy_params = {
        "alpha": np.float64(1.0),
        "fit_intercept": np.bool_(True),
        "solver": "auto",
    }
    assert predictor_id(**{**BASE, "hyperparameters": numpy_params}) == predictor_id(
        **BASE
    )

    integral = predictor_id(**{**BASE, "hyperparameters": {"degree": np.int64(2)}})
    assert integral == predictor_id(**{**BASE, "hyperparameters": {"degree": 2}})
    assert integral != predictor_id(**{**BASE, "hyperparameters": {"degree": 2.0}})


def test_predictor_id_refuses_a_hyperparameter_it_cannot_canonicalise_naming_the_key():
    """The anti-vacuity counterpart to the coercion above: coercion is
    per-type, not a blanket `str()`. A numpy ARRAY has no scalar coercion,
    so it raises and names its own key -- hashing its `repr` would give two
    arrays that differ past the truncation the same `predictor_id`."""
    with pytest.raises(UnhashableHyperparameterError, match="coef_prior"):
        predictor_id(
            **{**BASE, "hyperparameters": {"coef_prior": np.arange(4).reshape(2, 2)}}
        )


def test_an_extra_hyperparameter_key_changes_the_id_so_ridge_and_poly2_cannot_collide():
    """D-07-08's fourth estimator is a ridge on a degree-2 polynomial
    expansion, and it shares its three alpha values with the plain ridge.
    `degree` lives INSIDE `hyperparameters` (never as a sixth recipe
    field), so the two are distinguished only if an added key moves the id.
    Plan 07-06 owns the full 17-id count across the whole grid; this is the
    one-pair floor it rests on."""
    plain = predictor_id(**{**BASE, "hyperparameters": {"alpha": 1.0}})
    poly2 = predictor_id(**{**BASE, "hyperparameters": {"alpha": 1.0, "degree": 2}})
    assert plain != poly2


def test_predictor_id_refuses_a_class_object_where_a_model_class_string_belongs():
    """`str(SomeClass)` is `"<class 'pkg.mod.SomeClass'>"` -- a string that
    moves when the module is renamed and that nobody would recognise in an
    MLflow tag. Refused at the boundary instead."""
    with pytest.raises(TypeError, match="model_class must be a str"):
        predictor_id(**{**BASE, "model_class": float})
