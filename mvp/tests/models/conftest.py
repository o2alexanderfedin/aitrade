"""Bare `tmp_path`-derived roots for every `tests/models/` test, copied from
`tests/harness/conftest.py` with that file's tracking-root defect already
repaired (see `isolated_canonical_tracking_root` below).

THE PHASE-7 REASON THIS MATTERS MORE HERE. Pre-commit hook 19 runs the FULL
pytest suite on EVERY commit. A models test that reached a canonical root
would therefore spend an irreversible validation look from
`harness.budget` once per commit, not once per deliberate experiment
(D-07-34). `budget.look_count` is 0 for every segment of the issued manifest
and must stay 0 until the plans that spend looks deliberately. Every test in
this directory writes to its OWN `tmp_path` lake, registry, and MLflow
tracking root -- never the real, git-committed `mvp/data/lake_registry/`,
never the real lake, never the real MLflow store.

NO `__init__.py` IN THIS DIRECTORY, deliberately: `mvp/models/` is the
production package this phase builds, and a same-named test package shadows
it. `--import-mode=importlib` (set repo-wide in `pyproject.toml`) is what
makes same-named test modules in sibling directories distinct without one.
"""

from __future__ import annotations

import os
from pathlib import Path

import mlflow
import pytest
from mlflow.tracking import MlflowClient

from data.lake_paths import MLFLOW_TRACKING_ROOT_ENV
from tracking.mlflow_utils import build_tracking_uri


@pytest.fixture(autouse=True)
def isolated_canonical_tracking_root(tmp_path: Path):
    """SETS `AIHF_MLFLOW_TRACKING_ROOT` to this test's own
    `tmp_path/mlflow_root` for the duration of the test, and restores the
    previous value (or its absence) afterwards.

    SETS, not pops. The version of this fixture copied from
    `tests/harness/conftest.py` only called
    `os.environ.pop(MLFLOW_TRACKING_ROOT_ENV, None)` while claiming to point
    the store at `tmp_path`; with the variable ABSENT,
    `data.lake_paths.mlflow_tracking_root(None)` falls through to
    `DEFAULT_MLFLOW_TRACKING_ROOT` -- the REAL store. Popping is isolation
    only for a test that also passes its own root explicitly; setting is
    isolation for every test in the directory, which is the guarantee this
    phase needs. Both files were repaired together.

    `tmp_path/mlflow_root` is one path that EITHER this fixture or
    `tracking_root` may create and NEITHER owns exclusively -- hence
    `exist_ok=True` on both sides.
    """
    previous = os.environ.get(MLFLOW_TRACKING_ROOT_ENV)
    root = tmp_path / "mlflow_root"
    root.mkdir(parents=True, exist_ok=True)
    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(root)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(MLFLOW_TRACKING_ROOT_ENV, None)
        else:
            os.environ[MLFLOW_TRACKING_ROOT_ENV] = previous


@pytest.fixture(autouse=True)
def _end_any_active_run():
    """Belt-and-braces: end any run left active by a failing test, so a
    later test's `start_run()` never raises "Run already active"."""
    yield
    if mlflow.active_run() is not None:
        mlflow.end_run()


@pytest.fixture
def lake_root(tmp_path: Path) -> Path:
    return tmp_path / "lake"


@pytest.fixture
def registry_root(tmp_path: Path) -> Path:
    return tmp_path / "registry"


@pytest.fixture
def tracking_root(tmp_path: Path) -> Path:
    """An INITIALISED MLflow SQLite store at `tmp_path/mlflow_root`, with
    the canonical-root env var already pointed at it -- `budget.py`'s
    `_require_initialised_mlflow_store`/`_require_canonical_tracking_root`
    both refuse an uninitialised or non-canonical store before
    constructing any client, so tests that exercise the real counting
    path need a real, already-migrated store to point at (same pattern as
    `tests/lockbox/test_token_one_look.py:_seed_tracking_db`).

    Same path as `isolated_canonical_tracking_root` above, which runs FIRST
    and has already created it -- so `exist_ok=True`, never a bare
    `mkdir()`, which would raise `FileExistsError` in every test that asks
    for this fixture."""
    root = tmp_path / "mlflow_root"
    root.mkdir(parents=True, exist_ok=True)
    MlflowClient(build_tracking_uri(str(root))).search_experiments()
    assert (root / "mlflow.db").exists()
    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(root)
    return root
