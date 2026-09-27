"""Bare `tmp_path`-derived roots for every `tests/harness/` test -- no
shared fixture object, matching `tests/tracking/test_mlflow_utils.py`'s
and `tests/lockbox/test_token_one_look.py`'s own hermetic pattern. Every
test in this directory writes to its OWN `tmp_path` lake, registry, and
MLflow tracking root -- never the real, git-committed
`mvp/data/lake_registry/` or the real MLflow store.
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

    SETS, not pops -- this used to `os.environ.pop(...)` and claim in its
    docstring that it pointed the store at `tmp_path`. It did not. With the
    variable ABSENT, `data.lake_paths.mlflow_tracking_root(None)` falls
    through to `DEFAULT_MLFLOW_TRACKING_ROOT`, the REAL store at
    /Volumes/ProjectsSSD/aihedgefund/mlflow. Only a test that explicitly
    requested the `tracking_root` fixture below was isolated; any other test
    that reached a canonical-root resolution resolved the real one, and
    `_require_canonical_tracking_root` could not catch it because the real
    root IS canonical. Measured directly before the repair: a probe test
    requesting no fixture resolved /Volumes/ProjectsSSD/aihedgefund/mlflow;
    after it, the `tmp_path` one.

    `tmp_path/mlflow_root` is one path that EITHER this fixture or
    `tracking_root` may create and NEITHER owns exclusively -- hence
    `exist_ok=True` on both sides. Creating it here is deliberate: a
    canonical root that does not exist surfaces as a confusing
    "no existing mlflow.db" refusal rather than as isolation.
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
    and has already created it -- so `exist_ok=True`, not a bare `mkdir()`,
    which would now raise `FileExistsError` in every test that asks for this
    fixture. The directory is shared between the two fixtures and owned
    exclusively by neither."""
    root = tmp_path / "mlflow_root"
    root.mkdir(parents=True, exist_ok=True)
    MlflowClient(build_tracking_uri(str(root))).search_experiments()
    assert (root / "mlflow.db").exists()
    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(root)
    return root
