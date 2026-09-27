"""FCST-04's "deterministic predictor", asserted where it is actually
checkable: the PREDICTION TABLE, given the coefficients.

WHY THE BOUNDARY IS HERE AND NOT AT THE FIT (correction C6, measured). Fit
reproducibility is a property of whichever BLAS happens to be linked. This
host's numpy links **Apple Accelerate**; `threadpoolctl` ships no Accelerate
controller, so `OMP_NUM_THREADS` and friends control nothing locally, and CI
is Linux/OpenBLAS. Table reproducibility GIVEN the coefficients is one matvec
at fixed shape, and research measured it bit-identical across two fresh
processes, across `VECLIB_MAXIMUM_THREADS=1`, and across
`VECLIB_MAXIMUM_THREADS=2`. So the coefficient JSON is the determinism
boundary, and that is what these tests assert.

NOT ONE DIGEST IS PINNED AS A LITERAL ANYWHERE IN THIS FILE, deliberately.
Accelerate and OpenBLAS will not agree bit-for-bit on a reduction order, so a
committed constant would be a guaranteed CI failure that says nothing about
determinism. Every assertion compares two hashes computed in the same run on
the same host.

Mirrors `tests/sim/test_determinism.py`'s three-test shape -- two equality
tests plus a deliberate sentinel that makes them mean something. Without the
sentinel, both equality tests would pass on any constant array.
"""

from __future__ import annotations

import inspect
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np

from models.frozen import (
    FEATURE_NAMES,
    FrozenLinearPredictor,
    read_frozen_predictor,
    write_frozen_predictor,
)

MVP_ROOT = Path(__file__).resolve().parents[2]


def _build_fixed_features(n: int = 400) -> np.ndarray:
    """An `(n, 3)` float64 stand-in for ALREADY-NORMALISED features, from a
    FIXED FORMULA -- no RNG, no fixture lake.

    Duplicated verbatim from `tests/models/test_frozen_no_sklearn.py` rather
    than imported, for `tests/sim/test_determinism.py:_price_at_ticks`'s
    stated reason: this source is extracted into a `python3 -c` child script,
    and a cross-test-module import would break that extraction's
    self-containment (`tests/models/` has no `__init__.py`, by design -- a
    same-named test package would shadow `mvp/models/`).
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
    """sha256 over `dtype.str + shape-as-bytes + arr.tobytes()` per array --
    never a `repr()`. Duplicated for the same extraction reason as
    `_build_fixed_features` above.
    """
    import hashlib

    h = hashlib.sha256()
    for arr in arrays:
        h.update(arr.dtype.str.encode())
        h.update(np.array(arr.shape, dtype=np.int64).tobytes())
        h.update(arr.tobytes())
    return h.hexdigest()


def _fixed_predictor(last_coef: float = 4.0) -> FrozenLinearPredictor:
    """A frozen predictor from LITERAL coefficients, with `last_coef` the one
    knob the sentinel test moves by a single ULP.

    The last coefficient is the LARGEST, on purpose. A one-ULP change to a
    coefficient whose term contributes only a small fraction of the sum can
    round away entirely in the final addition -- which would make the
    sentinel test flaky-looking on some hosts and, worse, silently
    uninformative. At 4.0 against features in [-0.75, 0.75] the last term
    dominates the prediction, so one ULP of coefficient is of the order of
    one ULP of result.

    Every field is a literal and no module-level name is referenced except
    the two the child script imports itself.
    """
    return FrozenLinearPredictor(
        model_class="sklearn.Ridge",
        feature_names=FEATURE_NAMES,
        coef=(0.5, -0.25, last_coef),
        intercept=0.0009765625,
        normalization_manifest_id="norm-manifest-id-for-a-fixture",
        seed=7,
        code_hash="code-hash-for-a-fixture",
        hyperparameters={"alpha": 1.0},
        train_target_mean=1.5e-05,
        n_rows_fitted=3600,
        n_rows_dropped=11,
    )


def _child_script() -> str:
    """The child, EXTRACTED from this module with `inspect.getsource` and
    never retyped, so it runs byte-identical logic to the parent by
    construction.

    Every extracted function references only names the child's own header
    imports (`np`, `FEATURE_NAMES`, `FrozenLinearPredictor`) -- a reference to
    a module-level name defined only here would be a `NameError` in the child
    alone, the one place this suite cannot watch it fail.
    """
    funcs_src = "\n\n".join(
        textwrap.dedent(inspect.getsource(fn))
        for fn in (_build_fixed_features, _hash_arrays, _fixed_predictor)
    )
    return (
        "from __future__ import annotations\n"
        "import numpy as np\n"
        "from models.frozen import FEATURE_NAMES, FrozenLinearPredictor\n\n"
        f"{funcs_src}\n\n"
        "pred = _fixed_predictor().predict(_build_fixed_features())\n"
        "print(_hash_arrays([pred]))\n"
    )


def _child_env(numba_cache_dir: Path) -> dict[str, str]:
    """EVERY thread knob, each covering a different platform. A comment
    claiming `OMP_NUM_THREADS` controls this host's BLAS would be FALSE
    (correction C6): this numpy links Apple Accelerate, whose knob is
    `VECLIB_MAXIMUM_THREADS`, and `threadpoolctl` has no controller for it at
    all. `OMP_NUM_THREADS`/`OPENBLAS_NUM_THREADS` are the right knobs on the
    Linux CI runner's OpenBLAS wheels, `MKL_NUM_THREADS` on an Intel-MKL
    numpy nobody here has. They are set so the test does not depend on which
    one matters -- never because any one of them is why it passes.
    """
    return {
        **os.environ,
        "PYTHONPATH": str(MVP_ROOT),
        "NUMBA_CACHE_DIR": str(numba_cache_dir),
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
    }


def test_two_same_process_prediction_tables_hash_identical():
    predictor = _fixed_predictor()
    features = _build_fixed_features()

    first = predictor.predict(features)
    second = predictor.predict(features)

    # Anti-vacuity: a constant prediction column hashes identically no matter
    # what the arithmetic did, which is what the ULP sentinel below is for --
    # but a table with one distinct value would make even that unreachable.
    assert np.unique(first).size > 1, "vacuous: the prediction is constant"
    assert _hash_arrays([first]) == _hash_arrays([second])


def test_subprocess_prediction_table_hash_is_identical_to_the_parent_process(tmp_path):
    parent_hash = _hash_arrays([_fixed_predictor().predict(_build_fixed_features())])

    proc = subprocess.run(
        [sys.executable, "-c", _child_script()],
        cwd=str(MVP_ROOT),
        env=_child_env(tmp_path / "nbc"),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"child process failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )

    child_hash = proc.stdout.strip()
    # Shape before value: a crashed child with empty stdout must not be
    # allowed to coincidentally "match" anything.
    assert re.fullmatch(r"[0-9a-f]{64}", child_hash), (
        f"child stdout is not a well-formed sha256 hex digest: "
        f"{child_hash!r}; stderr={proc.stderr!r}"
    )
    print(f"in-process hash  : {parent_hash}")
    print(f"subprocess hash  : {child_hash}")
    assert child_hash == parent_hash


def test_a_one_bit_coefficient_change_changes_the_table_hash():
    """THE SENTINEL that makes the two equality tests mean something. Without
    it they would pass on any constant array, and on a `predict` that ignored
    its coefficients entirely.

    One ULP (`np.nextafter`), not a visible perturbation: the claim being
    protected is BIT equality, so the smallest representable change must be
    enough to break it.
    """
    base = _fixed_predictor()
    perturbed_last = float(np.nextafter(base.coef[-1], np.inf))
    # The step must actually have moved the value -- `nextafter` on an
    # already-infinite or NaN input returns something this assertion catches.
    assert perturbed_last != base.coef[-1]
    assert perturbed_last == base.coef[-1] + np.spacing(base.coef[-1])
    perturbed = _fixed_predictor(last_coef=perturbed_last)

    features = _build_fixed_features()
    base_pred = base.predict(features)
    perturbed_pred = perturbed.predict(features)

    # A COUNT, not merely "not equal": a single differing row would also
    # satisfy `!=`, and would mean the perturbation was at the edge of
    # rounding away rather than genuinely propagating. Most rows must move.
    differing = int(np.count_nonzero(base_pred != perturbed_pred))
    print(f"rows whose prediction bits moved by one ULP of coef: {differing}/400")
    print(f"unperturbed hash : {_hash_arrays([base_pred])}")
    print(f"one-ULP hash     : {_hash_arrays([perturbed_pred])}")
    assert differing > 200, differing
    # And the difference is genuinely of ULP size, not a sign that the two
    # predictors differ in some larger way.
    assert np.allclose(base_pred, perturbed_pred, rtol=1e-12, atol=0.0)

    assert _hash_arrays([base_pred]) != _hash_arrays([perturbed_pred])


def test_a_predictor_read_back_from_its_registry_body_predicts_the_same_bits_as_the_one_that_wrote_it(
    tmp_path,
):
    """D-07-22's data-level determinism claim, the half that must HOLD: one
    recipe, one `manifest_id`, the same bits after a JSON round trip.

    The half that would SIGNAL non-determinism in the real run is the other
    one -- two bodies at the same `predictor_id` under DIFFERENT
    `manifest_id`s. That is why the body carries both ids: it makes the claim
    checkable by looking at the registry, not only by re-running a test.
    """
    registry_root = tmp_path / "registry"
    predictor = _fixed_predictor()
    body = write_frozen_predictor(predictor, registry_root=registry_root)
    reloaded = read_frozen_predictor(body["manifest_id"], registry_root=registry_root)

    features = _build_fixed_features()
    written_pred = predictor.predict(features)
    reloaded_pred = reloaded.predict(features)

    assert np.unique(written_pred).size > 1, "vacuous: the prediction is constant"
    assert reloaded.coef == predictor.coef
    assert reloaded.predictor_id == predictor.predictor_id
    assert _hash_arrays([written_pred]) == _hash_arrays([reloaded_pred])
