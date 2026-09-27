"""D-07-14's recipe hash: the id that says WHICH MODEL produced a
prediction table, as distinct from which fold geometry it was produced
against.

FCST-04's literal wording -- a prediction table "keyed by segment
manifest" -- is necessary but not sufficient. The segment manifest id
alone collides the moment a second estimator predicts the same segment,
and Phase 7 already has four while Phase 8 adds three more. So the key is
the triple `(segment_manifest_id, segment_name, predictor_id)`, and this
module owns the third element.

WHAT IS IN THE RECIPE, AND WHY EXACTLY THESE FIVE FIELDS. The estimator
class name, the sorted hyperparameters, the seed, the code hash and the
normalization manifest id. Every one of them can change a prediction while
leaving the other four untouched, and nothing else can: the fold geometry
is the first element of the triple, the segment is the second, and the
DATA is pinned by the segment manifest's own upstream feature manifest
ids. `degree` for a polynomial expansion is NOT a sixth field -- it lives
INSIDE `hyperparameters`, which is what stops a degree-2 ridge at
alpha=1.0 hashing identically to a plain ridge at alpha=1.0.

WHAT IS NOT IN IT: the fitted coefficients. Two runs of the SAME recipe
that disagree on coefficients get the SAME `predictor_id` under DIFFERENT
`manifest_id`s (D-07-22), which is what makes D-07-12's determinism claim
checkable as data rather than only as a test.

WHY `data.store.compute_manifest_id` AND NEVER A SECOND HASHER. Exactly
`harness.negative_log.config_fingerprint`'s reason, in its own words: two
structurally-identical configs must get the same id regardless of key
order, precisely as two structurally-identical manifests do. That function
canonicalises with `json.dumps(..., sort_keys=True)`, which sorts NESTED
keys too -- so a hyperparameter mapping built in a different order hashes
identically, and no caller has to remember to sort anything.

WHY THE VALUES ARE COERCED BEFORE HASHING. `json.dumps` raises
`TypeError` on a numpy scalar, and a sweep is exactly where one arrives:
an `alpha` pulled out of a numpy grid is an `np.float64`, not a `float`.
Left uncoerced, the failure would surface in the middle of a search, after
hours of fitting. Coerced, `np.float64(0.1)` and `0.1` produce the SAME
id, which is the honest answer -- they are the same configuration. The
coercion is per-type (`np.integer -> int`, `np.floating -> float`,
`np.bool_ -> bool`), never a blanket `float()`: `degree: 2` silently
becoming `2.0` would change the poly2 id for no reason a reader could see.
Anything that is still not JSON-able after coercion RAISES
`UnhashableHyperparameterError` naming the offending key -- never
`str(value)`, which would happily hash a numpy array's truncated `repr`
and call two different arrays the same model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from data.store import compute_manifest_id

__all__ = [
    "RECIPE_FIELDS",
    "UnhashableHyperparameterError",
    "predictor_id",
]

#: The five fields of D-07-14's recipe, in the order the decision names
#: them. Spelled once so a test can assert the hashed body's key set is
#: EXACTLY this and nothing else.
RECIPE_FIELDS: tuple[str, ...] = (
    "model_class",
    "hyperparameters",
    "seed",
    "code_hash",
    "normalization_manifest_id",
)


class UnhashableHyperparameterError(TypeError):
    """A hyperparameter value cannot be canonicalised into JSON, so the
    recipe cannot be hashed -- refused, never coerced through `repr`."""

    def __init__(self, path: str, value: object) -> None:
        self.path = path
        self.value = value
        super().__init__(
            f"predictor_id: hyperparameter {path} has value of type "
            f"{type(value).__name__}, which is not JSON-able and has no "
            "defined scalar coercion -- pass a plain int/float/bool/str/"
            "None, a mapping, or a sequence of those. Hashing its repr "
            "would give two different values the same predictor_id."
        )


def _canonical_value(value: object, path: str) -> Any:
    """`value` as a JSON-able Python object, coerced per-type.

    `bool` is tested BEFORE `int` in both the numpy and the builtin branch,
    because `bool` subclasses `int` and `np.bool_` is not an `np.integer`
    but reads like one -- an unordered check turns `True` into `1` and
    loses the distinction `json.dumps` otherwise keeps.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, np.generic):
        item = value.item()
        if item is None or isinstance(item, (bool, int, float, str)):
            return item
        raise UnhashableHyperparameterError(path, value)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, sub in value.items():
            if not isinstance(key, str):
                raise UnhashableHyperparameterError(f"{path}[{key!r}] (key)", key)
            out[key] = _canonical_value(sub, f"{path}[{key!r}]")
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(sub, f"{path}[{i}]") for i, sub in enumerate(value)]
    raise UnhashableHyperparameterError(path, value)


def predictor_id(
    *,
    model_class: str,
    hyperparameters: Mapping[str, Any],
    seed: int,
    code_hash: str,
    normalization_manifest_id: str,
) -> str:
    """D-07-14's `predictor_id`: `data.store.compute_manifest_id` over a
    plain dict of exactly `RECIPE_FIELDS` and nothing else.

    Keyword-only, because five same-typed-looking strings and an int in
    positional order is a defect waiting to happen: swapping `code_hash`
    and `normalization_manifest_id` would produce a perfectly valid-looking
    id for the wrong recipe.
    """
    if not isinstance(model_class, str):
        raise TypeError(
            f"predictor_id: model_class must be a str (got "
            f"{type(model_class).__name__}) -- a class OBJECT would hash "
            "its module path and its repr, which move independently of the "
            "model"
        )
    for name, value in (
        ("code_hash", code_hash),
        ("normalization_manifest_id", normalization_manifest_id),
    ):
        if not isinstance(value, str):
            raise TypeError(
                f"predictor_id: {name} must be a str (got {type(value).__name__})"
            )
    recipe = {
        "model_class": model_class,
        "hyperparameters": _canonical_value(dict(hyperparameters), "hyperparameters"),
        "seed": int(seed),
        "code_hash": code_hash,
        "normalization_manifest_id": normalization_manifest_id,
    }
    return compute_manifest_id(recipe)
