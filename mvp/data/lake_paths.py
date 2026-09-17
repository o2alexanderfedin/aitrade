"""Physical lake root and backfill staging root resolution.

Reuses `data.capture.config.validate_data_root` for both roots -- the same
guard the capture daemon uses (exists, writable, not under a
CloudStorage/OneDrive sync path, minimum free space) -- rather than writing
a third guard (see 03-CONTEXT.md's Staging decision).

Neither `/Volumes/ProjectsSSD/aihedgefund/lake/` nor
`/Volumes/ProjectsSSD/aihedgefund/backfill/` exists yet at the start of this
phase (confirmed absent in 03-RESEARCH.md's Environment Availability table),
so `lake_root()`/`backfill_staging_root()` `mkdir(parents=True,
exist_ok=True)` the target before delegating to `validate_data_root` --
`validate_data_root` itself requires the path to already exist.

Physical lake data (Parquet partitions, DQ reports) lives on the SSD volume
under `lake_root()`, never in git -- multi-GB, and the repo containment rule
(everything MVP-related lives under `mvp/`) is about code/config, not data.
The small JSON audit trail CONTEXT.md calls "committed" (manifests, DQ
acknowledgements, lockbox tokens) instead lives under
`LAKE_REGISTRY_ROOT` = `mvp/data/lake_registry/`, git-tracked, so "every
look appears in a diff" is actually true -- a path under
`/Volumes/ProjectsSSD` is invisible to GitHub Actions and to `git log`. This
is a correction from a literal reading of CONTEXT.md's paths (which names
one `lake/` root for everything); used consistently from Plan 02 onward.
`LAKE_REGISTRY_ROOT` is a path constant only -- this module does not create
the directory; the first plan that writes an actual registry file does.
"""

from __future__ import annotations

import os
from pathlib import Path

from data.capture.config import DEFAULT_MIN_FREE_GB, DataRootError, validate_data_root

__all__ = [
    "DEFAULT_LAKE_ROOT",
    "DEFAULT_BACKFILL_STAGING_ROOT",
    "DEFAULT_MLFLOW_TRACKING_ROOT",
    "MLFLOW_TRACKING_ROOT_ENV",
    "LAKE_REGISTRY_ROOT",
    "DataRootError",
    "lake_root",
    "backfill_staging_root",
    "mlflow_tracking_root",
]

PKG_ROOT = Path(__file__).resolve().parent

DEFAULT_LAKE_ROOT = "/Volumes/ProjectsSSD/aihedgefund/lake"
DEFAULT_BACKFILL_STAGING_ROOT = "/Volumes/ProjectsSSD/aihedgefund/backfill"

#: The project's canonical MLflow store -- the ONE tracking root whose
#: absence of a run means "this lockbox token was never consumed"
#: (03-REVIEW-ITER2.md WR-12 remainder). `data.lockbox` refuses to answer
#: that question against any other store: a genuinely initialised MLflow
#: database belonging to some other project would answer "never consumed"
#: about a token it has never heard of, and the access run would then be
#: logged there instead of into the durable record.
#:
#: This is the DEFAULT, not the binding -- see `mlflow_tracking_root`.
DEFAULT_MLFLOW_TRACKING_ROOT = "/Volumes/ProjectsSSD/aihedgefund/mlflow"

#: Environment variable that names the canonical MLflow store on THIS host.
#: Set it when the store does not live at `DEFAULT_MLFLOW_TRACKING_ROOT` --
#: another machine, a CI runner, or the external volume remounted under a
#: different name.
MLFLOW_TRACKING_ROOT_ENV = "AIHF_MLFLOW_TRACKING_ROOT"

# git-committed audit trail root -- see module docstring for the rationale
# behind why this is separate from the physical DEFAULT_LAKE_ROOT.
LAKE_REGISTRY_ROOT = PKG_ROOT / "lake_registry"


def lake_root(
    override: str | None = None, min_free_gb: float = DEFAULT_MIN_FREE_GB
) -> Path:
    """Return the validated physical lake root, creating it if absent."""
    target = override or DEFAULT_LAKE_ROOT
    Path(target).mkdir(parents=True, exist_ok=True)
    return validate_data_root(target, min_free_gb=min_free_gb)


def backfill_staging_root(
    override: str | None = None, min_free_gb: float = DEFAULT_MIN_FREE_GB
) -> Path:
    """Return the validated backfill staging root, creating it if absent."""
    target = override or DEFAULT_BACKFILL_STAGING_ROOT
    Path(target).mkdir(parents=True, exist_ok=True)
    return validate_data_root(target, min_free_gb=min_free_gb)


def mlflow_tracking_root(override: str | None = None) -> Path:
    """Return the canonical MLflow store's RESOLVED path: `override`, else
    `$AIHF_MLFLOW_TRACKING_ROOT`, else `DEFAULT_MLFLOW_TRACKING_ROOT`.

    03-REVIEW-FOLLOWUPS.md WR-04: the constant used to be the binding, with
    no override at all, while its neighbours above both take `override=`. On
    any other host, any CI runner, or after the external volume was remounted
    under a different name, every `open_lockbox` raised before the token was
    even read -- a silent single-machine binding of the lockbox, recorded
    nowhere but that constant.

    Deliberately does NOT `mkdir` or `validate_data_root`, unlike the two
    roots above. This one is not a place to write data; it is the identity
    `data.lockbox._require_canonical_tracking_root` compares against, and a
    canonical root that does not exist must surface as the lockbox's own
    "no existing mlflow.db" refusal rather than as a directory this function
    helpfully created. `resolve()` on both sides is what makes `/tmp` vs
    `/private/tmp`, a trailing slash, and a symlink to the real store all the
    same root.
    """
    target = override or os.environ.get(MLFLOW_TRACKING_ROOT_ENV)
    return Path(target or DEFAULT_MLFLOW_TRACKING_ROOT).expanduser().resolve()
