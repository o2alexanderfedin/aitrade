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
    "PROVENANCE_TAG_KEYS",
    "MissingTagError",
    "DataRootError",
    "build_tracking_uri",
    "compute_code_hash",
    "compute_env_hash",
    "log_data_provenance",
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
    *,
    dq_ack_ids: list[str] | None = None,
):
    """Validate `tags` against `MANDATORY_TAG_KEYS` and `tracking_root` against
    `validate_data_root`, then start (and return) an MLflow run with all tags
    set atomically at run creation.

    `dq_ack_ids` (03-CONTEXT.md DATA-07: "Acknowledgement ids are logged as an
    MLflow run tag"): the DQ acknowledgement ids the run's data relied on
    (`data.store.dq_acknowledgement_ids`). When given, they are logged as the
    `dq_ack_ids` tag (sorted, comma-joined; `"none"` for an empty list) in the
    same atomic `start_run(tags=...)` call -- this function stays the only
    MLflow entry point.

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

    run_tags = dict(tags)
    if dq_ack_ids is not None:
        run_tags["dq_ack_ids"] = ",".join(sorted(set(dq_ack_ids))) or "none"
    return mlflow.start_run(experiment_id=experiment_id, tags=run_tags)


#: Tag keys `log_data_provenance` writes. Not part of `MANDATORY_TAG_KEYS`:
#: they are recorded by the LOADER, at the moment data is actually read, and
#: a run that reads no curated data legitimately has none of them.
PROVENANCE_TAG_KEYS: tuple[str, ...] = (
    "dq_ack_ids",
    "dq_ack_sha256",
    "data_manifest_ids",
)


def log_data_provenance(
    *,
    manifest_ids: list[str],
    dq_ack_ids: list[str],
    dq_ack_sha256: list[str],
) -> bool:
    """Record on the CURRENTLY ACTIVE run what a `load_curated` call actually
    relied on. Returns whether a run was active (nothing is logged when none
    is, and that is not an error).

    03-CONTEXT.md DATA-07 requires acknowledgement ids to be logged as an
    MLflow run tag. Until now nothing outside tests ever called
    `dq_acknowledgement_ids`: `load_curated` computed the ids and threw them
    away, so a training run's provenance never showed which DQ findings had
    been waived to let it read that data. `data.store.load_curated` now calls
    this, so the record is written by the code that did the reading.

    WHY `set_tags` AND NOT `start_run(tags=...)`: the atomicity rule this
    module enforces is about the MANDATORY tag schema -- a run must never be
    CREATED with a partial mandatory tag set, so those eight keys go in at
    creation. These three are different: they are not known at run start (the
    loader may be called many times, for many manifests, during a run), they
    are additive, and each value accumulates across calls rather than
    replacing what an earlier read recorded.
    """
    run = mlflow.active_run()
    if run is None:
        return False
    client = mlflow.tracking.MlflowClient()
    existing = client.get_run(run.info.run_id).data.tags
    for key, values in (
        ("dq_ack_ids", dq_ack_ids),
        ("dq_ack_sha256", dq_ack_sha256),
        ("data_manifest_ids", manifest_ids),
    ):
        merged = {v for v in values if v}
        previous = existing.get(key)
        if previous and previous != "none":
            merged |= set(previous.split(","))
        mlflow.set_tag(key, ",".join(sorted(merged)) or "none")
    return True
