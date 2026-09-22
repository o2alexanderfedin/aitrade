"""Selection-bias budget: an MLflow-first, durable look counter (D-05-11..15).

Copies `data.lockbox`'s exact shape (05-PATTERNS.md): query MLflow FIRST,
refuse an uninitialised or non-canonical store BEFORE constructing any
client, and let any query exception PROPAGATE UNMODIFIED -- `0` here means
"asked MLflow and it said no looks", never "could not ask".

GRANULARITY (D-05-13): the pair `(segment_manifest_id, segment_name)`,
summed across every run, every `model_class`, every `stage` that consumes
it. The filter below is intentionally NOT keyed on `model_class` -- a
per-model-class budget would let three model classes spend three budgets
on one window.

EXHAUSTION (D-05-14, 05-03-PLAN.md): `record_look` now REFUSES a look once
`look_count(...) >= budget_allowance` -- `budget_allowance` is a REQUIRED
keyword-only argument on `record_look`, never optional, so a caller cannot
skip the check by omitting it. `exhausted_segments` answers the same
question in bulk, over a list of already-issued manifests, for
`harness.segments.issue_segment_manifest`'s own unconditional issuance-time
overlap refusal (D-05-14's other half: a NEW manifest whose validation
interval overlaps an already-exhausted one is refused at issuance, not
merely at the next look).

THE GUARANTEE, STATED HONESTLY (D-05-15, unchanged by this addition): the
budget counts looks that pass through `record_look` -- which only
`harness.accessor.materialize` calls. A bare `features.tier.load_features`
plus a hand-rolled `etime` filter bypasses this module entirely, exactly as
it bypasses `harness.accessor`; `tools/check_harness_accessor_only.py` is
the static tripwire that catches such a caller, not this module.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import mlflow
from mlflow.entities import ViewType
from mlflow.tracking import MlflowClient

from data import lake_paths
from tracking.mlflow_utils import (
    MLFLOW_MIN_FREE_GB,
    build_tracking_uri,
    start_tracked_run,
)

__all__ = [
    "BudgetError",
    "BudgetExhaustedError",
    "exhausted_segments",
    "look_count",
    "record_look",
]


class BudgetError(ValueError):
    """Raised for a budget-counter protocol violation: an uninitialised or
    non-canonical MLflow store, or (from `record_look`) a caller whose
    `run_tags` has not already set `fold_config`."""


class BudgetExhaustedError(BudgetError):
    """Raised by `record_look` (D-05-14) when a segment's `budget_allowance`
    is already spent -- names the segment, the looks spent, the allowance,
    and the remedy (a new, non-overlapping segment manifest)."""


#: Duplicated from `data.lockbox` (05-PATTERNS.md: a private helper with a
#: real behavioural contract, not `_atomic_write_json`'s 5-line I/O shape,
#: but still small and stable enough to copy rather than cross-import).
MLFLOW_STORE_REQUIRED_TABLES: frozenset[str] = frozenset(
    {"alembic_version", "experiments", "runs", "tags"}
)

_SQLITE_HEADER = b"SQLite format 3\x00"


def _require_initialised_mlflow_store(store_file: Path) -> None:
    """Refuse (`BudgetError`) a `mlflow.db` that is not an already
    initialised MLflow SQLite store -- see `data.lockbox`'s identically
    named function for the full rationale (03-REVIEW-ITER2.md WR-12): a
    fresh, empty store would otherwise silently answer "0 looks spent".
    Opened read-only; never modifies the file."""
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
        raise BudgetError(
            f"{store_file} is not an initialised MLflow store ({problem}) -- "
            "refusing to treat its silence as '0 looks spent'"
        )


def _require_canonical_tracking_root(tracking_root: str, allowed_root: Path) -> None:
    """Refuse a `tracking_root` that is not the project's canonical MLflow
    store -- see `data.lockbox`'s identically named function for the full
    rationale (03-REVIEW-ITER2.md WR-12 remainder): any OTHER genuinely
    initialised store would still answer "0 looks spent" about a segment
    it has never heard of."""
    resolved = Path(tracking_root).resolve()
    allowed = Path(allowed_root).resolve()
    if resolved != allowed:
        raise BudgetError(
            f"tracking root {resolved} is not the project's canonical MLflow "
            f"store {allowed} -- refusing to ask a different store how many "
            "looks have been spent, and refusing to log a look there"
        )


def look_count(
    segment_manifest_id: str,
    segment_name: str,
    *,
    tracking_root: str,
    allowed_root: str | None = None,
) -> int:
    """The number of looks (D-05-11) recorded for the pair
    `(segment_manifest_id, segment_name)`, summed across every run at
    `tracking_root` -- D-05-13's granularity exactly: the `filter_string`
    below ANDs both tags and never adds `model_class`.

    Refuses a `tracking_root` that is not canonical, or whose `mlflow.db`
    is missing or uninitialised, BEFORE constructing any MLflow client
    (same fail-closed direction as `data.lockbox._mlflow_has_consumed`).
    Any exception `MlflowClient(...)`, `.search_experiments()`, or
    `.search_runs()` itself raises PROPAGATES UNMODIFIED -- `0` here means
    "the query succeeded and found nothing", never "the query could not be
    run".
    """
    _require_canonical_tracking_root(
        tracking_root, lake_paths.mlflow_tracking_root(allowed_root)
    )
    store_file = Path(tracking_root).resolve() / "mlflow.db"
    if not store_file.exists():
        raise BudgetError(
            f"tracking root {tracking_root} has no existing mlflow.db -- "
            "refusing to create a fresh store and treat the budget as unspent"
        )
    _require_initialised_mlflow_store(store_file)
    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    experiment_ids = [
        exp.experiment_id for exp in client.search_experiments(view_type=ViewType.ALL)
    ]
    if not experiment_ids:
        return 0
    runs = client.search_runs(
        experiment_ids,
        filter_string=(
            f"tags.segment_manifest_id = '{segment_manifest_id}' and "
            f"tags.segment_name = '{segment_name}'"
        ),
        run_view_type=ViewType.ALL,
    )
    return len(runs)


#: Roles `exhausted_segments` (and `harness.segments.issue_segment_manifest`'s
#: overlap check) look at -- the same `_LOOK_ROLES` set `harness.accessor`
#: uses (D-05-11: only `val`/`oof_block` materializations are looks).
_LOOK_ROLES: frozenset[str] = frozenset({"val", "oof_block"})


def exhausted_segments(
    manifests: list[dict],
    *,
    tracking_root: str,
    allowed_root: str | None = None,
) -> list[dict]:
    """Return one entry per `(manifest, val/oof_block segment)` pair whose
    `look_count(...) >= manifest["budget_allowance"]` (D-05-14), as
    `{"manifest_id", "segment_name", "start_ns", "end_ns"}` dicts --
    `harness.segments.issue_segment_manifest`'s self-discovery calls this,
    unconditionally, over every manifest it globs from
    `registry_root/segments/`, to refuse a new issuance whose validation
    interval overlaps one of these.

    `manifests=[]` (the common case for every `tmp_path`-fresh registry
    this phase's tests use) means the loop below never executes, so
    `look_count` -- and therefore any MLflow query at all -- is never
    reached. This is not a special case written into this function: it is
    what "nothing to check" looks like when there is nothing to iterate.
    A non-empty `manifests` list with a genuinely fresh/uninitialised
    `tracking_root`, by contrast, DOES reach `look_count`, and that
    `BudgetError` propagates unmodified (D-05-12): a manifest exists to
    check, and its tracking root could not be asked.
    """
    exhausted: list[dict] = []
    for manifest in manifests:
        manifest_id = manifest["manifest_id"]
        allowance = manifest["budget_allowance"]
        for entry in manifest["segments"]:
            if entry["role"] not in _LOOK_ROLES:
                continue
            spent = look_count(
                manifest_id,
                entry["name"],
                tracking_root=tracking_root,
                allowed_root=allowed_root,
            )
            if spent >= allowance:
                exhausted.append(
                    {
                        "manifest_id": manifest_id,
                        "segment_name": entry["name"],
                        "start_ns": entry["start_ns"],
                        "end_ns": entry["end_ns"],
                    }
                )
    return exhausted


def record_look(
    segment_manifest_id: str,
    segment_name: str,
    *,
    tracking_root: str,
    run_tags: dict,
    budget_allowance: int,
    experiment_name: str = "harness-looks",
    min_free_gb: float = MLFLOW_MIN_FREE_GB,
) -> str:
    """Record one look (D-05-11) as an MLflow run tagged
    `segment_manifest_id` and `stage="val_look"` (filled here), plus --
    additively, AFTER run creation, the same pattern
    `tracking.mlflow_utils.log_data_provenance` uses for non-mandatory
    tags -- `segment_name`. Returns the new run's `run_id`.

    `run_tags` must already carry `fold_config` (`harness.accessor` sets
    it from the segment manifest's own `layout` before calling this --
    `budget.py` has no manifest of its own to read it from) plus the
    other caller-supplied mandatory keys (`code_hash`, `data_hash`,
    `seed`, `env_hash`, `model_class`). `segment_manifest_id` and `stage`
    are filled here, never by the caller, so they cannot drift from what
    `look_count` filters on.

    `budget_allowance` (D-05-14) is a REQUIRED keyword-only argument, no
    default -- `harness.accessor.materialize` passes the segment
    manifest's own `budget_allowance` field. BEFORE `start_tracked_run` is
    ever called, this queries `look_count` for the pair; a count already
    `>= budget_allowance` raises `BudgetExhaustedError`, naming the
    segment, how many looks were spent, the allowance, and the remedy (a
    new segment manifest whose validation interval does not overlap this
    one) -- no run is logged for a refused look.
    """
    if "fold_config" not in run_tags:
        raise BudgetError(
            "record_look: run_tags must already include 'fold_config' -- "
            "the caller (harness.accessor) sets it from the segment "
            "manifest's own layout name before calling record_look"
        )
    spent = look_count(segment_manifest_id, segment_name, tracking_root=tracking_root)
    if spent >= budget_allowance:
        raise BudgetExhaustedError(
            f"record_look: segment {segment_name!r} of manifest "
            f"{segment_manifest_id!r} is exhausted -- {spent} looks spent "
            f"of {budget_allowance} allowed; issue a new segment manifest "
            "whose validation interval does not overlap this one (D-05-14)"
        )
    tags = dict(run_tags)
    tags["segment_manifest_id"] = segment_manifest_id
    tags["stage"] = "val_look"
    with start_tracked_run(
        str(tracking_root), tags, experiment_name, min_free_gb=min_free_gb
    ) as run:
        mlflow.set_tag("segment_name", segment_name)
        run_id = run.info.run_id
    return run_id
