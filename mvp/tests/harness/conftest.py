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
def isolated_canonical_tracking_root():
    """Points the canonical MLflow store at each test's own `tmp_path`
    through the documented env var (same posture as
    `tests/lockbox/conftest.py`), cleared before and restored after every
    test so one test's store can never leak into the next."""
    previous = os.environ.get(MLFLOW_TRACKING_ROOT_ENV)
    os.environ.pop(MLFLOW_TRACKING_ROOT_ENV, None)
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
    `tests/lockbox/test_token_one_look.py:_seed_tracking_db`)."""
    root = tmp_path / "mlflow_root"
    root.mkdir()
    MlflowClient(build_tracking_uri(str(root))).search_experiments()
    assert (root / "mlflow.db").exists()
    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(root)
    return root
