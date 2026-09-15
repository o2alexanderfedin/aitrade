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
from tools.git_env import scrubbed_git_env

PKG_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Re-exported so callers can catch `tracking.mlflow_utils.DataRootError`
# without reaching into `data.capture.config` directly -- `start_tracked_run`
# re-raises it unmodified for a bad tracking root.
__all__ = [
    "MANDATORY_TAG_KEYS",
    "MissingTagError",
    "DataRootError",
    "build_tracking_uri",
    "compute_code_hash",
    "compute_env_hash",
    "start_tracked_run",
]

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
    resolved = pathlib.Path(root).resolve()
    return f"sqlite:///{resolved}/mlflow.db"


def compute_code_hash(dirty_suffix: str = "-dirty", git_runner=subprocess.run) -> str:
    """Return git HEAD's SHA, with `dirty_suffix` appended if the working
    tree is not clean.

    `git_runner` is injectable so tests can supply a fake git command runner
    rather than depending on the real repo's dirty/clean state at test time.
    Both subprocess calls pin `cwd=PKG_ROOT` (never the caller's ambient cwd)
    and check `returncode` explicitly -- a silent `""` code_hash (git absent,
    not a git repo, corrupted `.git`, permission error) would still satisfy
    `MANDATORY_TAG_KEYS`'s presence check and get logged as if it were a
    valid, reproducibility-grade git SHA.
    """
    sha_result = git_runner(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=PKG_ROOT,
        env=scrubbed_git_env(),
    )
    if sha_result.returncode != 0 or not sha_result.stdout.strip():
        raise RuntimeError(f"git rev-parse HEAD failed: {sha_result.stderr}")
    sha = sha_result.stdout.strip()

    status_result = git_runner(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=PKG_ROOT,
        env=scrubbed_git_env(),
    )
    if status_result.returncode != 0:
        raise RuntimeError(f"git status --porcelain failed: {status_result.stderr}")
    dirty = bool(status_result.stdout.strip())
    return f"{sha}{dirty_suffix}" if dirty else sha


def compute_env_hash(lock_path: pathlib.Path) -> str:
    """Return the SHA-256 hex digest of `lock_path`'s bytes."""
    return hashlib.sha256(pathlib.Path(lock_path).read_bytes()).hexdigest()


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
    missing = MANDATORY_TAG_KEYS - tags.keys()
    if missing:
        raise MissingTagError(f"missing mandatory tags: {sorted(missing)}")

    # Let DataRootError propagate unmodified -- do not catch and rewrap it.
    resolved_root = validate_data_root(tracking_root, min_free_gb=min_free_gb)

    mlflow.set_tracking_uri(build_tracking_uri(str(resolved_root)))
    experiment = mlflow.get_experiment_by_name(experiment_name)
    if experiment is None:
        experiment_id = mlflow.create_experiment(
            experiment_name, artifact_location=f"{resolved_root}/artifacts"
        )
    else:
        experiment_id = experiment.experiment_id

    return mlflow.start_run(experiment_id=experiment_id, tags=dict(tags))
