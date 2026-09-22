"""The negative-result log (D-05-22, EVAL-04): a failed configuration is
recorded as an MLflow run carrying an outcome tag, a reason string, and a
config fingerprint -- `config_fingerprint(config) == data.store.
compute_manifest_id(config)` EXACTLY, the same canonicalizer
`harness.segments` already uses for a segment manifest's own id, never a
second implementation.

Copies `harness.budget`'s / `data.lockbox`'s MLflow-first shape: query
FIRST, refuse an uninitialised or non-canonical store BEFORE constructing
any client, and let any query exception PROPAGATE UNMODIFIED -- an empty
result here means "asked MLflow and it found nothing", never "could not
ask".

RE-RUNNING A KNOWN-NEGATIVE FINGERPRINT IS NOT REFUSED (D-05-22): a
configuration that failed once may legitimately be re-run under different
code or data (a bug fix, a corrected dataset) -- `warn_if_already_negative`
logs a WARNING naming the prior run(s) and returns normally. `code_hash`/
`data_hash`, recorded on every negative run via the caller's own `run_tags`
(the same eight-key mandatory schema every MLflow run in this project
carries, `tracking.mlflow_utils.start_tracked_run` enforces it), are what
distinguish a legitimate re-run from a re-explored dead end -- this module
does not attempt that distinction itself, only surfaces the data needed to
make it.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from mlflow.entities import ViewType
from mlflow.tracking import MlflowClient
from mlflow.utils.validation import MAX_TAG_VAL_LENGTH

from data import lake_paths
from data.store import compute_manifest_id
from tracking.mlflow_utils import (
    MLFLOW_MIN_FREE_GB,
    build_tracking_uri,
    start_tracked_run,
)

__all__ = [
    "NegativeLogError",
    "config_fingerprint",
    "record_negative_result",
    "query_negative_results",
    "warn_if_already_negative",
]

logger = logging.getLogger(__name__)


class NegativeLogError(ValueError):
    """Raised for a negative-log protocol violation: an uninitialised or
    non-canonical MLflow store, or a `reason` too long for a single MLflow
    tag (MLflow truncates rather than raising -- WR-01's own failure
    mode; refusing here is safer than silently recording a cut-short
    reason)."""


#: Duplicated from `harness.budget` / `data.lockbox` (05-PATTERNS.md: a
#: private helper with a real behavioural contract, small and stable
#: enough to copy rather than cross-import).
MLFLOW_STORE_REQUIRED_TABLES: frozenset[str] = frozenset(
    {"alembic_version", "experiments", "runs", "tags"}
)

_SQLITE_HEADER = b"SQLite format 3\x00"

#: The stage/outcome this module's own runs are tagged with -- what
#: `query_negative_results` filters on.
NEGATIVE_RESULT_STAGE = "negative_result"

#: Duplicated from `harness.budget` (05-PATTERNS.md: a private helper with
#: a real behavioural contract, copied rather than cross-imported). A
#: `config_fingerprint` is a sha256 hex digest by construction
#: (`config_fingerprint` above, `data.store.compute_manifest_id`) -- this
#: pattern accepts that shape with room to spare, and rejects anything
#: containing a quote, backslash, or other character that could alter the
#: MLflow filter DSL a future caller splices it into.
_FILTER_SAFE_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _require_filter_safe(value: str, label: str) -> None:
    """Refuse (`NegativeLogError`) a value about to be spliced into an
    MLflow `filter_string` via raw f-string interpolation (05-REVIEW.md
    WR-04) -- see `harness.budget._require_filter_safe`'s identically
    named function for the full rationale. Checked BEFORE any MLflow
    client is constructed."""
    if not _FILTER_SAFE_RE.match(value):
        raise NegativeLogError(
            f"query_negative_results: {label} {value!r} contains a "
            f"character outside {_FILTER_SAFE_RE.pattern} -- refusing to "
            "splice it into an MLflow filter_string unescaped "
            "(05-REVIEW.md WR-04)"
        )


def _require_initialised_mlflow_store(store_file: Path) -> None:
    """Refuse (`NegativeLogError`) a `mlflow.db` that is not an already
    initialised MLflow SQLite store -- see `harness.budget`'s identically
    named function for the full rationale (03-REVIEW-ITER2.md WR-12): a
    fresh, empty store would otherwise silently answer "no negative
    results recorded". Opened read-only; never modifies the file."""
    problem: str | None = None
    try:
        with open(store_file, "rb") as fh:
            header = fh.read(len(_SQLITE_HEADER))
        if header != _SQLITE_HEADER:
            problem = f"no SQLite header ({store_file.stat().st_size} bytes)"
        else:
            uri = f"{store_file.resolve().as_uri()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            try:
                tables = {
                    row[0]
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                missing = MLFLOW_STORE_REQUIRED_TABLES - tables
                if missing:
                    problem = f"missing MLflow tables {sorted(missing)}"
                elif (
                    con.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0]
                    < 1
                ):
                    problem = "alembic_version is empty (schema never migrated)"
            finally:
                con.close()
    except (OSError, sqlite3.Error) as exc:
        problem = f"{exc.__class__.__name__}: {exc}"
    if problem is not None:
        raise NegativeLogError(
            f"{store_file} is not an initialised MLflow store ({problem}) -- "
            "refusing to treat its silence as 'no negative results recorded'"
        )


def _require_canonical_tracking_root(tracking_root: str, allowed_root: Path) -> None:
    """Refuse a `tracking_root` that is not the project's canonical MLflow
    store -- see `harness.budget`'s identically named function for the
    full rationale (03-REVIEW-ITER2.md WR-12 remainder): any OTHER
    genuinely initialised store would still answer "no negative results"
    about a configuration it has never heard of."""
    resolved = Path(tracking_root).resolve()
    allowed = Path(allowed_root).resolve()
    if resolved != allowed:
        raise NegativeLogError(
            f"tracking root {resolved} is not the project's canonical MLflow "
            f"store {allowed} -- refusing to ask a different store which "
            "configurations have already been recorded negative, and "
            "refusing to log a negative result there"
        )


def config_fingerprint(config: dict) -> str:
    """The content-addressed id of `config` -- `data.store.
    compute_manifest_id` EXACTLY, the same function `harness.segments`
    uses for a segment manifest's own `manifest_id`. Never a second
    canonicalizer: two structurally-identical configs get the same
    fingerprint regardless of key order, exactly as two structurally-
    identical manifests get the same id."""
    return compute_manifest_id(config)


def record_negative_result(
    config: dict,
    *,
    reason: str,
    tracking_root: str,
    run_tags: dict,
    experiment_name: str = "harness-negative-results",
    min_free_gb: float = MLFLOW_MIN_FREE_GB,
) -> str:
    """Record `config` as a negative result (D-05-22): an MLflow run
    tagged `stage="negative_result"`, `outcome="negative"`, `reason`, and
    `config_fingerprint` (filled here). Returns the new run's `run_id`.

    `run_tags` must already carry the other mandatory tag keys
    `tracking.mlflow_utils.start_tracked_run` requires (`code_hash`,
    `data_hash`, `seed`, `env_hash`, `model_class`, `fold_config`,
    `segment_manifest_id`) -- this function does not fill any of those in,
    unlike `harness.budget.record_look`, which owns `segment_manifest_id`
    itself: a negative result has no segment of its own to name.

    Refuses (`NegativeLogError`) a `reason` longer than MLflow's own tag
    value cap (`MAX_TAG_VAL_LENGTH`) BEFORE calling `start_tracked_run` --
    MLflow truncates an over-long tag value rather than raising
    (`tracking.mlflow_utils`'s own WR-01 finding), which would otherwise
    silently record a reason that looks complete and reads truncated.
    """
    if len(reason) > MAX_TAG_VAL_LENGTH:
        raise NegativeLogError(
            f"record_negative_result: reason is {len(reason)} characters, "
            f"exceeding MLflow's {MAX_TAG_VAL_LENGTH}-character tag limit -- "
            "MLflow truncates rather than raising (WR-01); refusing to "
            "record a reason that would silently be cut short"
        )
    tags = dict(run_tags)
    tags["stage"] = NEGATIVE_RESULT_STAGE
    tags["outcome"] = "negative"
    tags["reason"] = reason
    tags["config_fingerprint"] = config_fingerprint(config)
    with start_tracked_run(
        str(tracking_root), tags, experiment_name, min_free_gb=min_free_gb
    ) as run:
        run_id = run.info.run_id
    return run_id


def query_negative_results(
    *,
    tracking_root: str,
    config_fingerprint: str | None = None,
    allowed_root: str | None = None,
) -> list[tuple[str, str, int, str, str, str]]:
    """Every recorded negative result at `tracking_root`, as
    `(fingerprint, reason, when, run_id, code_hash, data_hash)` tuples,
    oldest first -- `when` is the run's own `start_time` (epoch
    milliseconds, MLflow's own unit). `config_fingerprint`, when given,
    filters to that one configuration's own recorded negatives.

    `run_view_type=ViewType.ALL` (T-05-17): includes soft-deleted runs, so
    a routine MLflow-UI cleanup of a negative-result run does not silently
    un-warn a re-explored dead end -- the same discipline `harness.budget.
    look_count` and `data.lockbox._mlflow_has_consumed` already apply.

    Refuses a `tracking_root` that is not canonical, or whose `mlflow.db`
    is missing or uninitialised, BEFORE constructing any MLflow client.
    Any exception `MlflowClient(...)`, `.search_experiments()`, or
    `.search_runs()` itself raises PROPAGATES UNMODIFIED -- an empty list
    here means "the query succeeded and found nothing", never "the query
    could not be run".
    """
    if config_fingerprint is not None:
        _require_filter_safe(config_fingerprint, "config_fingerprint")
    _require_canonical_tracking_root(
        tracking_root, lake_paths.mlflow_tracking_root(allowed_root)
    )
    store_file = Path(tracking_root).resolve() / "mlflow.db"
    if not store_file.exists():
        raise NegativeLogError(
            f"tracking root {tracking_root} has no existing mlflow.db -- "
            "refusing to create a fresh store and treat no negative results "
            "as recorded"
        )
    _require_initialised_mlflow_store(store_file)
    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    experiment_ids = [
        exp.experiment_id for exp in client.search_experiments(view_type=ViewType.ALL)
    ]
    if not experiment_ids:
        return []
    filter_parts = [f"tags.stage = '{NEGATIVE_RESULT_STAGE}'"]
    if config_fingerprint is not None:
        filter_parts.append(f"tags.config_fingerprint = '{config_fingerprint}'")
    runs = client.search_runs(
        experiment_ids,
        filter_string=" and ".join(filter_parts),
        run_view_type=ViewType.ALL,
    )
    rows = [
        (
            run.data.tags.get("config_fingerprint", ""),
            run.data.tags.get("reason", ""),
            run.info.start_time,
            run.info.run_id,
            run.data.tags.get("code_hash", ""),
            run.data.tags.get("data_hash", ""),
        )
        for run in runs
    ]
    rows.sort(key=lambda row: row[2])
    return rows


def warn_if_already_negative(
    config: dict, *, tracking_root: str, allowed_root: str | None = None
) -> None:
    """Log a `logging.WARNING` (never raise) naming every prior run that
    already recorded `config`'s own fingerprint negative -- `reason`,
    `when`, and `run_id` for each -- then return normally. The caller
    proceeds regardless: `code_hash`/`data_hash` on the resulting run are
    what distinguish this re-run from the earlier dead end (D-05-22), a
    judgement this function does not make on the caller's behalf.

    Any exception `query_negative_results` itself raises (an uninitialised
    or non-canonical `tracking_root`) PROPAGATES UNMODIFIED -- this
    function adds no silent fallback to "nothing recorded" on a query
    failure.
    """
    fingerprint = config_fingerprint(config)
    rows = query_negative_results(
        tracking_root=tracking_root,
        config_fingerprint=fingerprint,
        allowed_root=allowed_root,
    )
    for _fingerprint, reason, when, run_id, _code_hash, _data_hash in rows:
        logger.warning(
            "config_fingerprint %s was already recorded negative "
            "(reason=%r, when=%s, run_id=%s) -- proceeding anyway (D-05-22): "
            "this run's own code_hash/data_hash distinguish a legitimate "
            "re-run from a re-explored dead end",
            fingerprint,
            reason,
            when,
            run_id,
        )
