"""Lockbox tests point the canonical MLflow store at their own `tmp_path`
through the documented environment variable, never through a parameter on the
public API (03-REVIEW-FOLLOWUPS.md WR-04).

`open_lockbox` used to take a public `canonical_tracking_root=` keyword whose
only protection was a docstring saying "tests inject this; nothing else may".
One keyword restored exactly the pre-fix behaviour the pin exists to remove:
query some other store, get "never consumed", log the access run there. The
keyword is gone; a host whose store lives elsewhere sets
`AIHF_MLFLOW_TRACKING_ROOT`, which is a configuration decision rather than a
per-call escape hatch.

This fixture makes that env var test-local: cleared before every test, and
restored afterwards, so one test's tmp_path store can never leak into the
next (or into the real one).
"""

from __future__ import annotations

import os

import pytest

from data.lake_paths import MLFLOW_TRACKING_ROOT_ENV


@pytest.fixture(autouse=True)
def isolated_canonical_tracking_root():
    previous = os.environ.get(MLFLOW_TRACKING_ROOT_ENV)
    os.environ.pop(MLFLOW_TRACKING_ROOT_ENV, None)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(MLFLOW_TRACKING_ROOT_ENV, None)
        else:
            os.environ[MLFLOW_TRACKING_ROOT_ENV] = previous
