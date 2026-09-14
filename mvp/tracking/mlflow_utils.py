"""The only entrypoint for starting an MLflow run.

Enforces the 8-key mandatory tag schema declared in `mvp/spec.md`'s "MLflow tag
schema" section *before* calling `mlflow.start_run` -- a run missing any mandatory
tag is rejected, never silently logged with a partial tag set (spec.md, Pitfall 4
in 02-RESEARCH.md: tags must be passed atomically via `start_run(tags=...)`, never
via a follow-up `set_tags` call). Reuses Phase 1's `validate_data_root` for the
MLflow tracking root guard -- no second cloud-sync/free-space check is written
here.

Callers are responsible for ending the run (`mlflow.end_run()`), or using the
returned `ActiveRun` as a context manager (`with start_tracked_run(...) as run:`) --
`start_tracked_run` itself does not end the run, matching `mlflow.start_run`'s own
contract.
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess

import mlflow

from data.capture.config import DEFAULT_MIN_FREE_GB, DataRootError, validate_data_root

# The 8 mandatory tag keys, verbatim from mvp/spec.md's "MLflow tag schema"
# section -- mvp/tests/tracking/test_mlflow_utils.py asserts these never drift
# apart (the spec is the contract).
MANDATORY_TAG_KEYS: frozenset[str] = frozenset(
    {
        "code_hash",
        "data_hash",
        "seed",
        "env_hash",
        "segment_manifest_id",
        "model_class",
        "fold_config",
        "stage",
    }
)


class MissingTagError(ValueError):
    """Raised when a tag dict passed to `start_tracked_run` is missing a
    mandatory key. Raised before any `mlflow.start_run` call -- no run is
    created in the store."""


def build_tracking_uri(root: str) -> str:
    """Return the `sqlite:///<abs-root>/mlflow.db` tracking URI for `root`.

    Four slashes after `sqlite:` for an absolute path: three literal slashes
    in the URI scheme plus the leading `/` of the resolved absolute path.
    """
    raise NotImplementedError


def compute_code_hash(
    dirty_suffix: str = "-dirty", git_runner=subprocess.run
) -> str:
    """Return git HEAD's SHA, with `dirty_suffix` appended if the working
    tree is not clean.

    `git_runner` is injectable so tests can supply a fake git command runner
    rather than depending on the real repo's dirty/clean state at test time.
    """
    raise NotImplementedError


def compute_env_hash(lock_path: pathlib.Path) -> str:
    """Return the SHA-256 hex digest of `lock_path`'s bytes."""
    raise NotImplementedError


def start_tracked_run(
    tracking_root: str,
    tags: dict[str, str],
    experiment_name: str,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
):
    """Validate `tags` against `MANDATORY_TAG_KEYS` and `tracking_root` against
    `validate_data_root`, then start (and return) an MLflow run with all tags
    set atomically at run creation.

    Raises `MissingTagError` if `tags` is missing any mandatory key, before
    any MLflow call. Re-raises `DataRootError` unmodified if `tracking_root`
    fails validation.
    """
    raise NotImplementedError
