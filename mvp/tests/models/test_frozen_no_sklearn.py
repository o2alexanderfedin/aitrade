"""D-07-17's proof, and the reason the frozen predictor is a JSON body and
not a pickle: the stored coefficients, re-evaluated by a bare numpy dot
product in a process where `"sklearn"` never entered `sys.modules`, reproduce
`predict()` BIT-FOR-BIT.

THIS MODULE IMPORTS NO sklearn, deliberately and permanently. The two
re-evaluation tests rebuild the arithmetic by hand -- `intercept + X @ coef`,
and the nine degree-2 columns written out one by one -- in
`sim/reference.py`'s register: a twin that "shares no arithmetic, no buffer
and no state representation with the thing it checks". A test that imported
the library whose absence is the claim would be checking nothing.

Beside the re-evaluation, every refusal `models.frozen` makes gets its own
test, because a registry read that fails closed only in the docstring is a
registry read that does not fail closed. Each tampered body below is
RE-SIGNED so that it trips exactly ONE refusal and passes every other: a body
edited to break the `predictor_id` cross-check would otherwise be caught by
the self-hash first, and the test would pass for the wrong reason while the
check it names went unexercised.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from data.store import compute_manifest_id
from models import frozen as frozen_module
from models.frozen import (
    FEATURE_NAMES,
    FrozenLinearPredictor,
    FrozenPredictorError,
    predictor_registry_path,
    read_frozen_predictor,
    write_frozen_predictor,
)
from models.protocol import FrozenPredictor, predictor_from_artifact

MVP_ROOT = Path(__file__).resolve().parents[2]

#: Import roots `models/frozen.py` may never reach. `sklearn` because the
#: numpy-only re-evaluation is the whole claim; the other three because a
#: deserialiser is the classic remote-code-execution surface (T-07-13), and
#: `joblib` is the one that pulls `cloudpickle` in without anybody typing it.
BANNED_IMPORT_ROOTS: frozenset[str] = frozenset(
    {"sklearn", "pickle", "cloudpickle", "joblib"}
)

#: Nine DISTINCT, non-zero degree-2 coefficients. Distinctness is the
#: anti-vacuity condition for the expansion test: if the six quadratic
#: coefficients were equal (or zero), swapping two expanded columns would be
#: invisible and the hand-built comparison could not catch a permuted design.
POLY2_COEF: tuple[float, ...] = (
    0.5,
    -0.25,
    0.125,
    1.5,
    -0.75,
    0.375,
    2.25,
    -1.125,
    0.0625,
)


def _build_fixed_features(n: int = 400) -> np.ndarray:
    """An `(n, 3)` float64 stand-in for ALREADY-NORMALISED features, from a
    FIXED FORMULA -- no RNG, no fixture lake, no `tests/fixtures/model_span`.

    Three reasons, all of them operational. (1) This function's source is
    extracted verbatim into the subprocess child below, so it must reference
    no module-level name the child never defines and must need no Parquet on
    disk. (2) `pytest tests/models` runs on EVERY commit (hook 19) and has to
    stay in seconds. (3) numpy makes no cross-version guarantee about
    `default_rng`'s stream, and a determinism test whose input depends on one
    is measuring the RNG.

    The three moduli are coprime to their multipliers and to each other, so
    no two columns coincide and no product of two columns coincides with
    another -- the condition the nine-column expansion test needs to be able
    to see a permutation. The third column is never exactly zero (that would
    need `(i * 13) % 53 == 26.5`), so the last coefficient always bites.
    """
    rows = []
    for i in range(n):
        rows.append(
            (
                (((i * 37) % 211) / 211.0 - 0.5) * 3.0,
                (((i * 53) % 97) / 97.0 - 0.5) * 2.0,
                (((i * 13) % 53) / 53.0 - 0.5) * 1.5,
            )
        )
    return np.array(rows, dtype=np.float64)


def _hash_arrays(arrays) -> str:
    """sha256 over `dtype.str + shape-as-bytes + arr.tobytes()` per array,
    into ONE running hash -- `tests/sim/test_determinism.py`'s own function,
    duplicated here rather than imported because this source is extracted
    verbatim into a child script that cannot import another test module.
    NEVER a `repr()`: float repr formatting carries no bit-stability
    guarantee that raw bytes do not already give.
    """
    import hashlib

    h = hashlib.sha256()
    for arr in arrays:
        h.update(arr.dtype.str.encode())
        h.update(np.array(arr.shape, dtype=np.int64).tobytes())
        h.update(arr.tobytes())
    return h.hexdigest()


def _predictor(**overrides: Any) -> FrozenLinearPredictor:
    """A `design="linear"` predictor with hand-chosen, distinct, non-zero
    coefficients. Every field is spelled so that a test overriding one is
    visibly changing that one thing."""
    fields: dict[str, Any] = {
        "model_class": "sklearn.Ridge",
        "feature_names": FEATURE_NAMES,
        "coef": (0.5, -0.25, 4.0),
        "intercept": 0.0009765625,
        "normalization_manifest_id": "norm-manifest-id-for-a-fixture",
        "seed": 7,
        "code_hash": "code-hash-for-a-fixture",
        "hyperparameters": {"alpha": 1.0},
        "train_target_mean": 1.5e-05,
        "n_rows_fitted": 3600,
        "n_rows_dropped": 11,
    }
    fields.update(overrides)
    return FrozenLinearPredictor(**fields)


def _poly2_predictor(**overrides: Any) -> FrozenLinearPredictor:
    fields: dict[str, Any] = {
        "hyperparameters": {"alpha": 1.0, "degree": 2},
        "coef": POLY2_COEF,
    }
    fields.update(overrides)
    return _predictor(**fields)


def _resign(body: dict[str, Any], registry_root: Path) -> str:
    """Write `body` at its OWN recomputed `manifest_id`, so a body tampered
    to trip one refusal passes the self-hash and every other check.

    Not `write_frozen_predictor`: that one builds the body from a valid
    predictor, which is exactly what a tampered body cannot be.
    """
    body = {key: value for key, value in body.items() if key != "manifest_id"}
    manifest_id = compute_manifest_id(body)
    body["manifest_id"] = manifest_id
    path = predictor_registry_path(registry_root, manifest_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, sort_keys=True, indent=2))
    return manifest_id


def _child_script() -> str:
    """A standalone `python3 -c` child that reads a frozen predictor back
    from a registry and prints the hash of its prediction table -- with
    `_build_fixed_features` and `_hash_arrays` EXTRACTED via
    `inspect.getsource`, never retyped, so the child runs byte-identical
    logic to this module by construction.

    The registry root and the manifest id arrive as `argv`, not as
    interpolated literals: a `tmp_path` string spliced into source is one
    quoting accident away from a child that computes something else.
    """
    funcs_src = "\n\n".join(
        textwrap.dedent(inspect.getsource(fn))
        for fn in (_build_fixed_features, _hash_arrays)
    )
    return (
        "from __future__ import annotations\n"
        "import sys\n"
        "import types\n"
        "from pathlib import Path\n"
        "import numpy as np\n"
        "from models.frozen import read_frozen_predictor\n\n"
        f"{funcs_src}\n\n"
        "registry_root = Path(sys.argv[1])\n"
        "manifest_id = sys.argv[2]\n"
        # The anti-vacuity hook for this guard: with a stub module injected,
        # the assertion below MUST fire. A guard that cannot fail is not a
        # guard, and a real `import sklearn` here would cost the suite a
        # second on every commit to learn the same thing.
        "if '--inject-sklearn' in sys.argv[3:]:\n"
        "    sys.modules['sklearn'] = types.ModuleType('sklearn')\n\n"
        "def leaked():\n"
        "    return sorted(\n"
        "        name\n"
        "        for name in sys.modules\n"
        "        if name == 'sklearn' or name.startswith('sklearn.')\n"
        "    )\n\n"
        "print(leaked())\n"
        "assert not leaked(), f'sklearn reached this process: {leaked()}'\n"
        "predictor = read_frozen_predictor(manifest_id, registry_root=registry_root)\n"
        "pred = predictor.predict(_build_fixed_features())\n"
        "assert not leaked(), f'sklearn reached this process: {leaked()}'\n"
        "print(_hash_arrays([pred]))\n"
    )


#: The child's environment. EVERY thread knob is set, and each covers a
#: different platform -- a comment claiming `OMP_NUM_THREADS` controls this
#: host's BLAS would be FALSE (correction C6): this numpy links Apple
#: Accelerate, whose knob is `VECLIB_MAXIMUM_THREADS`, and `threadpoolctl`
#: ships no Accelerate controller at all. `OMP_NUM_THREADS` and
#: `OPENBLAS_NUM_THREADS` are the right knobs on the Linux CI runner
#: (OpenBLAS wheels); `MKL_NUM_THREADS` covers an Intel-MKL numpy nobody
#: here has. They are set because the test must not depend on which one
#: matters, never because any one of them is why it passes.
def _child_env(numba_cache_dir: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": str(MVP_ROOT),
        "NUMBA_CACHE_DIR": str(numba_cache_dir),
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
    }


# --------------------------------------------------------------------------
# The import ban, over the AST
# --------------------------------------------------------------------------


def _import_roots(source: str) -> set[str]:
    """Every top-level module name `source` imports, from its IMPORT NODES.

    Over the AST and never over the text, for the same reason
    `tests/models/test_protocol.py` scans signatures rather than characters:
    `models/frozen.py`'s docstring has to SAY `sklearn`, `pickle`,
    `cloudpickle` and `joblib` in order to state the ban, and
    `LINEAR_MODEL_CLASSES` carries `"sklearn.Ridge"` as a dispatch key. A
    check spelled `'sklearn' not in source` would forbid exactly the
    documentation and the registry the design requires.

    Both import spellings are covered: `import sklearn.linear_model as lm`
    contributes through `ast.Import`'s `alias.name`, and `from sklearn import
    Ridge` through `ast.ImportFrom`'s `module`.
    """
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_frozen_py_imports_no_sklearn_no_pickle_no_cloudpickle_no_joblib():
    """The contract that makes every other test in this file possible. A
    committed test, not the plan's one-off shell command: the ban has to hold
    on every commit forever, and nothing re-runs a verify line."""
    roots = _import_roots(Path(inspect.getsourcefile(frozen_module)).read_text())
    assert roots & BANNED_IMPORT_ROOTS == set(), (
        f"models/frozen.py imports {sorted(roots & BANNED_IMPORT_ROOTS)}"
    )
    # Anti-vacuity: a scan that walked nothing would satisfy the line above
    # forever. It must see the imports that ARE there.
    assert {"numpy", "json", "data", "models"} <= roots, sorted(roots)


def test_the_import_scan_catches_both_spellings_of_a_banned_import():
    """The counterpart. `import sklearn.linear_model as lm` and `from sklearn
    import Ridge` are different AST nodes, and a scan that only walked one of
    them would pass the test above while missing half the ways the ban can be
    broken. Fed both, plus a `joblib.load` that arrives through an alias."""
    bad = textwrap.dedent(
        '''
        """A docstring naming sklearn, pickle, cloudpickle and joblib."""
        import sklearn.linear_model as lm
        from sklearn import Ridge
        import joblib as _j
        from pickle import loads
        '''
    )
    assert _import_roots(bad) & BANNED_IMPORT_ROOTS == {
        "sklearn",
        "joblib",
        "pickle",
    }
    # And the docstring alone must NOT trip it -- the distinction the whole
    # AST approach exists for.
    docstring_only = '"""sklearn, pickle, cloudpickle, joblib."""\nimport numpy\n'
    assert _import_roots(docstring_only) & BANNED_IMPORT_ROOTS == set()


# --------------------------------------------------------------------------
# The re-evaluation: D-07-17's actual claim
# --------------------------------------------------------------------------


def test_the_coefficient_json_re_evaluated_by_a_bare_dot_product_reproduces_predict_bit_for_bit():
    """`np.array_equal`, not `np.allclose`: this is the same arithmetic in
    the same order over the same bytes, so it must agree EXACTLY. An
    `allclose` here would pass a predictor that quietly centred its inputs.
    """
    predictor = _predictor()
    features = _build_fixed_features()

    # Anti-vacuity, in three parts. A zero coefficient vector reproduces
    # bit-for-bit trivially; a constant prediction column hashes the same no
    # matter what the arithmetic did; and a feature matrix with a repeated
    # column could not tell two coefficients apart.
    assert any(value != 0.0 for value in predictor.coef), predictor.coef
    assert len(set(predictor.coef)) == len(predictor.coef), predictor.coef
    assert features.shape == (400, 3)
    for left in range(3):
        for right in range(left + 1, 3):
            assert not np.array_equal(features[:, left], features[:, right])

    expected = predictor.intercept + features @ np.asarray(
        predictor.coef, dtype=np.float64
    )
    actual = predictor.predict(features)

    assert np.unique(actual).size > 1, "vacuous: the prediction is constant"
    assert actual.dtype == np.float64
    assert actual.shape == (400,)
    assert np.array_equal(actual, expected)


def test_the_degree_two_json_re_evaluated_by_hand_built_columns_reproduces_predict_bit_for_bit():
    """The nine columns written out by hand in sklearn's order.

    The `shape[1] == 9` assertion is not decoration: a column count is the
    only one of this phase's three feature refusals that can catch a price
    column entering through an interaction term (D-07-23). Six columns would
    mean the squares were dropped, ten would mean a bias column
    `include_bias=False` never emits.
    """
    predictor = _poly2_predictor()
    assert predictor.design == "poly2"
    features = _build_fixed_features()
    x0, x1, x2 = features[:, 0], features[:, 1], features[:, 2]
    design = np.column_stack(
        [x0, x1, x2, x0 * x0, x0 * x1, x0 * x2, x1 * x1, x1 * x2, x2 * x2]
    )

    assert design.shape == (400, 9), design.shape
    # Anti-vacuity: nine PAIRWISE DISTINCT columns and nine distinct non-zero
    # coefficients, so that any permutation of the expansion changes the
    # result. With equal coefficients or duplicated columns, a swapped pair
    # would be invisible and this test would prove only that 9 == 9.
    assert len(set(predictor.coef)) == 9, predictor.coef
    assert all(value != 0.0 for value in predictor.coef)
    for left in range(9):
        for right in range(left + 1, 9):
            assert not np.array_equal(design[:, left], design[:, right]), (left, right)

    expected = predictor.intercept + design @ np.asarray(
        predictor.coef, dtype=np.float64
    )
    actual = predictor.predict(features)

    assert np.unique(actual).size > 1, "vacuous: the prediction is constant"
    assert np.array_equal(actual, expected)


def test_a_permuted_expansion_would_have_been_caught_by_the_hand_built_comparison():
    """The counterpart to the test above: swapping two expanded columns must
    change the prediction. Otherwise "the hand-built columns agree" is a
    statement about nothing -- which is exactly what it would be if the
    quadratic coefficients were equal to each other."""
    predictor = _poly2_predictor()
    features = _build_fixed_features()
    x0, x1, x2 = features[:, 0], features[:, 1], features[:, 2]
    correct = np.column_stack(
        [x0, x1, x2, x0 * x0, x0 * x1, x0 * x2, x1 * x1, x1 * x2, x2 * x2]
    )
    # `x0*x1` and `x0*x2` swapped -- the single most plausible mistake in an
    # upper-triangular sweep.
    permuted = np.column_stack(
        [x0, x1, x2, x0 * x0, x0 * x2, x0 * x1, x1 * x1, x1 * x2, x2 * x2]
    )
    coef = np.asarray(predictor.coef, dtype=np.float64)
    assert not np.array_equal(correct @ coef, permuted @ coef)
    assert np.array_equal(
        predictor.predict(features), predictor.intercept + correct @ coef
    )


def test_reading_a_frozen_predictor_and_predicting_works_in_a_subprocess_where_sklearn_was_never_imported(
    tmp_path,
):
    """D-07-17's strongest available statement that the predictor is
    genuinely frozen: a FRESH process, which has never imported sklearn,
    reads the JSON body off disk and reproduces the parent's prediction table
    byte for byte.

    A pickle could not pass this test -- it would need the writing library
    at the writing version, which is the property "frozen" must not have.
    """
    predictor = _poly2_predictor()
    body = write_frozen_predictor(predictor, registry_root=tmp_path / "registry")
    parent_pred = predictor.predict(_build_fixed_features())
    assert np.unique(parent_pred).size > 1, "vacuous: the prediction is constant"
    parent_hash = _hash_arrays([parent_pred])

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            _child_script(),
            str(tmp_path / "registry"),
            body["manifest_id"],
        ],
        cwd=str(MVP_ROOT),
        env=_child_env(tmp_path / "nbc"),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"child process failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 2, (proc.stdout, proc.stderr)
    # The evidence, not only the child's own assertion: the list of
    # sklearn-rooted modules in the child's `sys.modules` is printed and
    # asserted empty HERE too.
    assert lines[0] == "[]", f"sklearn reached the child: {lines[0]}"
    # Shape before value: a crashed child with empty stdout must not be able
    # to coincidentally "match" anything.
    assert re.fullmatch(r"[0-9a-f]{64}", lines[1]), (lines, proc.stderr)
    assert lines[1] == parent_hash


def test_the_childs_no_sklearn_guard_fails_when_a_stub_sklearn_is_injected(tmp_path):
    """The anti-vacuity counterpart: with a stub module stuffed into the
    child's `sys.modules`, the guard MUST fail. Without this, the test above
    would pass just as happily if the guard were `assert True`.

    A stub rather than a real `import sklearn`, so the suite that runs on
    every commit does not pay a second to learn the same thing."""
    predictor = _predictor()
    body = write_frozen_predictor(predictor, registry_root=tmp_path / "registry")

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            _child_script(),
            str(tmp_path / "registry"),
            body["manifest_id"],
            "--inject-sklearn",
        ],
        cwd=str(MVP_ROOT),
        env=_child_env(tmp_path / "nbc"),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0, proc.stdout
    assert "sklearn reached this process" in proc.stderr, proc.stderr


# --------------------------------------------------------------------------
# The body: design derived, never stored
# --------------------------------------------------------------------------


def test_a_frozen_linear_predictor_is_a_frozen_predictor_at_runtime():
    """D-07-27's per-implementation conformance check, for the first
    implementation there is. `isinstance` checks member PRESENCE, so a typo'd
    or missing member is caught here rather than after the first fold -- which
    on a validation segment costs an irreversible look."""
    assert isinstance(_predictor(), FrozenPredictor)
    assert isinstance(_poly2_predictor(), FrozenPredictor)


def test_design_is_a_derived_property_and_not_a_field():
    """The collision D-07-14 exists to prevent, closed one level down. A
    `design` FIELD could carry `"poly2"` beside a recipe with no `degree`,
    hashing identically to plain Ridge at the same alpha."""
    field_names = {field.name for field in dataclasses.fields(FrozenLinearPredictor)}
    assert "design" not in field_names, field_names
    assert isinstance(inspect.getattr_static(FrozenLinearPredictor, "design"), property)
    assert inspect.getattr_static(FrozenLinearPredictor, "design").fset is None

    predictor = _predictor()
    assert predictor.design == "linear"
    with pytest.raises(dataclasses.FrozenInstanceError):
        predictor.design = "poly2"


def test_poly2_and_linear_at_the_same_alpha_have_different_predictor_ids():
    """Because `degree` lives INSIDE `hyperparameters`, which is what
    `predictor_id` hashes. Ridge and Ridge+poly2 share three alphas at an
    identical `model_class`, seed, code hash and normalisation id (07-06 pins
    both to `"sklearn.Ridge"`), so this is the ONLY thing separating them."""
    linear = _predictor(hyperparameters={"alpha": 1.0})
    poly2 = _poly2_predictor(hyperparameters={"alpha": 1.0, "degree": 2})
    assert linear.model_class == poly2.model_class
    assert linear.predictor_id != poly2.predictor_id


def test_a_degree_two_recipe_with_three_coefficients_is_refused_naming_both_counts():
    with pytest.raises(
        FrozenPredictorError, match="needs exactly 9 coefficients, got 3"
    ):
        _predictor(hyperparameters={"alpha": 1.0, "degree": 2})


def test_a_linear_recipe_with_nine_coefficients_is_refused_naming_both_counts():
    with pytest.raises(
        FrozenPredictorError, match="needs exactly 3 coefficients, got 9"
    ):
        _predictor(coef=POLY2_COEF)


def test_an_unimplemented_degree_is_refused_rather_than_read_as_linear():
    """`degree: 3` is the case a value test spelled `degree == 2` would read
    as `"linear"`: three coefficients, every check passed, and a cubic model
    predicted as a linear one."""
    with pytest.raises(FrozenPredictorError, match="only expansion this module"):
        _predictor(hyperparameters={"alpha": 1.0, "degree": 3})


def test_a_non_float64_or_wrongly_shaped_feature_matrix_is_refused_naming_both_shapes():
    predictor = _predictor()
    features = _build_fixed_features()
    with pytest.raises(FrozenPredictorError, match=r"expected shape \(n, 3\).*400, 2"):
        predictor.predict(features[:, :2])
    with pytest.raises(FrozenPredictorError, match="expected dtype float64"):
        predictor.predict(features.astype(np.float32))
    with pytest.raises(FrozenPredictorError, match="must be a numpy ndarray"):
        predictor.predict(features.tolist())


def test_a_nan_in_the_features_leaves_a_nan_in_the_prediction():
    """Stated in `predict`'s docstring and asserted here: the caller's mask
    is the control. A predictor that quietly substituted a number would make
    a dropped row indistinguishable from a predicted one."""
    predictor = _predictor()
    features = _build_fixed_features(8)
    features[3, 1] = np.nan
    pred = predictor.predict(features)
    assert np.isnan(pred[3])
    assert np.isfinite(np.delete(pred, 3)).all()


# --------------------------------------------------------------------------
# The four refusals, each isolated
# --------------------------------------------------------------------------


def test_a_named_but_missing_frozen_predictor_fails_closed(tmp_path):
    """REFUSAL 1. Never a default and never `None` -- an unreadable registry
    entry must never read as "use an untrained model"."""
    with pytest.raises(FrozenPredictorError, match="fail-closed"):
        read_frozen_predictor("0" * 64, registry_root=tmp_path / "registry")


def test_a_body_whose_coefficients_were_edited_is_refused_by_the_recomputed_hash(
    tmp_path,
):
    """REFUSAL 2, first half: the recomputed hash against the id the CALLER
    passed. The honestly-stale case -- coefficients edited, `manifest_id`
    field and filename both left alone."""
    registry_root = tmp_path / "registry"
    body = write_frozen_predictor(_predictor(), registry_root=registry_root)
    path = predictor_registry_path(registry_root, body["manifest_id"])
    tampered = json.loads(path.read_text())
    tampered["coef"] = [9.0, 9.0, 9.0]
    path.write_text(json.dumps(tampered, sort_keys=True, indent=2))

    with pytest.raises(FrozenPredictorError, match="self-hash mismatch"):
        read_frozen_predictor(body["manifest_id"], registry_root=registry_root)


def test_a_body_kept_helpfully_in_sync_with_its_edited_coefficients_is_refused_too(
    tmp_path,
):
    """REFUSAL 2, still the first half: coefficients edited AND the
    `manifest_id` field updated to match, the FILE left at the old id. The
    body is internally consistent and still refused -- `harness.errata.
    read_errata_manifest`'s own stated case."""
    registry_root = tmp_path / "registry"
    body = write_frozen_predictor(_predictor(), registry_root=registry_root)
    path = predictor_registry_path(registry_root, body["manifest_id"])
    tampered = json.loads(path.read_text())
    tampered["coef"] = [9.0, 9.0, 9.0]
    tampered.pop("manifest_id")
    tampered["manifest_id"] = compute_manifest_id(tampered)
    path.write_text(json.dumps(tampered, sort_keys=True, indent=2))

    with pytest.raises(FrozenPredictorError, match="self-hash mismatch"):
        read_frozen_predictor(body["manifest_id"], registry_root=registry_root)


def test_a_body_whose_own_manifest_id_field_was_edited_is_refused_although_it_hashes_to_its_filename(
    tmp_path,
):
    """REFUSAL 2, SECOND half -- the only case the caller's-id comparison
    alone cannot see. `canonicalize_manifest` drops the `manifest_id` key
    before encoding, so editing that ONE field leaves the recomputed hash
    equal to the filename. Only the comparison against the body's own field
    catches it, which is why the check is an `or` and not a single test."""
    registry_root = tmp_path / "registry"
    body = write_frozen_predictor(_predictor(), registry_root=registry_root)
    path = predictor_registry_path(registry_root, body["manifest_id"])
    tampered = json.loads(path.read_text())
    assert compute_manifest_id(tampered) == body["manifest_id"]
    tampered["manifest_id"] = "f" * 64
    path.write_text(json.dumps(tampered, sort_keys=True, indent=2))
    # The premise: the recomputed hash STILL equals the filename, so the
    # first half of the comparison is satisfied and proves nothing here.
    assert compute_manifest_id(json.loads(path.read_text())) == body["manifest_id"]

    with pytest.raises(FrozenPredictorError, match="self-hash mismatch"):
        read_frozen_predictor(body["manifest_id"], registry_root=registry_root)


def test_a_body_whose_recipe_was_edited_is_refused_by_the_re_derived_predictor_id(
    tmp_path,
):
    """REFUSAL 3. The seed is edited and the body RE-SIGNED, so the self-hash
    agrees with both ids and the only thing left to catch it is re-deriving
    `predictor_id` from the recipe fields."""
    registry_root = tmp_path / "registry"
    body = _predictor().to_artifact()
    body["seed"] = 8
    manifest_id = _resign(body, registry_root)

    with pytest.raises(FrozenPredictorError, match="re-derived from this body"):
        read_frozen_predictor(manifest_id, registry_root=registry_root)


def test_a_body_claiming_poly2_with_no_degree_key_is_refused(tmp_path):
    """REFUSAL 4, and the reason it exists. Three coefficients, a `design`
    field saying `"poly2"`, no `degree` in the recipe, a correct
    `predictor_id` and a correct self-hash: every other check passes, and the
    recipe it hashes is byte-identical to plain Ridge at the same alpha. Only
    the emitted-versus-derived comparison refuses it."""
    registry_root = tmp_path / "registry"
    body = _predictor().to_artifact()
    body["design"] = "poly2"
    manifest_id = _resign(body, registry_root)

    with pytest.raises(FrozenPredictorError, match="emits design 'poly2'"):
        read_frozen_predictor(manifest_id, registry_root=registry_root)


def test_a_body_claiming_linear_while_carrying_a_degree_is_refused(tmp_path):
    """REFUSAL 4, the other direction -- a body whose emitted design is
    behind its recipe rather than ahead of it."""
    registry_root = tmp_path / "registry"
    body = _poly2_predictor().to_artifact()
    body["design"] = "linear"
    manifest_id = _resign(body, registry_root)

    with pytest.raises(FrozenPredictorError, match="emits design 'linear'"):
        read_frozen_predictor(manifest_id, registry_root=registry_root)


def test_an_incomplete_body_is_refused_by_name_and_a_foreign_schema_version_too(
    tmp_path,
):
    registry_root = tmp_path / "registry"
    body = _predictor().to_artifact()
    del body["train_target_mean"]
    manifest_id = _resign(body, registry_root)
    with pytest.raises(FrozenPredictorError, match="train_target_mean"):
        read_frozen_predictor(manifest_id, registry_root=registry_root)

    body = _predictor().to_artifact()
    body["schema_version"] = 2
    manifest_id = _resign(body, registry_root)
    with pytest.raises(FrozenPredictorError, match="schema_version is 2"):
        read_frozen_predictor(manifest_id, registry_root=registry_root)


def test_writing_the_same_predictor_twice_refuses_to_overwrite(tmp_path):
    registry_root = tmp_path / "registry"
    predictor = _predictor()
    write_frozen_predictor(predictor, registry_root=registry_root)
    with pytest.raises(FrozenPredictorError, match="refusing to overwrite"):
        write_frozen_predictor(predictor, registry_root=registry_root)


def test_the_body_carries_both_ids_and_the_three_non_recipe_fields(tmp_path):
    """D-07-22's shape, asserted as data: two runs of ONE recipe that
    disagree on coefficients share a `predictor_id` under DIFFERENT
    `manifest_id`s. That is what makes the determinism claim checkable on
    disk rather than only in a test, and it only works because
    `train_target_mean`, `n_rows_fitted` and `n_rows_dropped` are in the body
    and NOT in the hashed recipe."""
    registry_root = tmp_path / "registry"
    first = write_frozen_predictor(_predictor(), registry_root=registry_root)
    second = write_frozen_predictor(
        _predictor(coef=(0.5, -0.25, 4.5), train_target_mean=2e-05, n_rows_fitted=3599),
        registry_root=registry_root,
    )
    assert first["predictor_id"] == second["predictor_id"]
    assert first["manifest_id"] != second["manifest_id"]
    for key in ("train_target_mean", "n_rows_fitted", "n_rows_dropped"):
        assert key in first
        assert key not in first["hyperparameters"]


def test_predictor_from_artifact_rebuilds_a_linear_body_and_applies_the_same_refusals():
    """The protocol's registry now has an entry, and it goes through the same
    door: a body the on-disk reader would refuse is refused here too, so
    neither door can be the lenient one."""
    predictor = _poly2_predictor()
    rebuilt = predictor_from_artifact(predictor.to_artifact())
    assert isinstance(rebuilt, FrozenLinearPredictor)
    assert rebuilt == predictor

    body = predictor.to_artifact()
    body["design"] = "linear"
    with pytest.raises(FrozenPredictorError, match="emits design 'linear'"):
        predictor_from_artifact(body)
