"""D-07-05's materialize-once frame cache, KEYED BY THE TRACKING ROOT --
and the two look-counting reports that make a leak visible the first time
rather than on the fourth.

WHY THE TRACKING ROOT IS IN THE PATH, WHICH IS THE WHOLE POINT OF THIS
MODULE. A `val` frame materialized during a dry run against a scratch
tracking root is BYTE-IDENTICAL to the one a real run would get, and it
cost nothing: `harness.budget.record_look` wrote its run into the scratch
store, so the canonical store's `look_count` for that segment is still 0.
If that cached frame were then read by the real run, the honest look would
never have been spent, while every record a human can consult -- the
canonical `look_count`, the segment manifest's `budget_allowance`, the
MLflow `harness-looks` experiment -- would read as though it had been. The
budget would be bypassed IN FACT while reading as spent.

Neither existing control can see that bypass. `tools/
check_harness_accessor_only.py` is STATIC: it proves nobody reaches
`features.tier.load_features` behind the accessor's back, and a cache read
does not. `harness.budget.record_look` is RUNTIME: it counts the calls that
reach it, and a cache hit is precisely a call that does not. So the defence
has to live in the cache's own addressing, and it does:
`sha256(resolved tracking_root)[:16]` is a path component of every cached
frame. A scratch-root cache is simply not reachable from a canonical-root
run -- not refused, UNREACHABLE, which is the stronger property because it
needs no check to fire.

`cache_root` IS A REQUIRED KEYWORD WITH NO DEFAULT, deliberately. A
module-level default is a loaded gun in this repo: pre-commit hooks 18/19
run the FULL pytest suite on every commit (D-07-34), so a test that
forgot the argument would write fixture frames into the real scratch tree
once per commit -- and that canonical path is exactly where a later real
run goes looking for a frame it believes it paid for. `CACHE_ROOT` below
names the canonical location for the CLI to pass EXPLICITLY; tests pass a
`tmp_path` and cannot forget to.

WHERE THE CANONICAL CACHE LIVES, AND WHY NOT ANYWHERE ELSE. Outside the
REPO, because a 4.5 GiB train frame under a gitignored directory is still
a stray that a repo walk finds (and `tools/check_no_manifest_rewrite`
walks trees). Outside the LAKE, because `check_no_manifest_rewrite` and
`features.tier`'s tier containment both walk lake roots and a Parquet that
is not a tier partition has no business being found by either. On the SSD,
because the real frames are gigabytes.

THE CACHE IS A DERIVED CONVENIENCE, NEVER AN INPUT TO A MANIFEST (D-07-05).
No manifest id is computed over a cached file, no manifest names one, and
deleting the whole tree costs nothing but a re-materialization -- which is
also why deleting it is not free of BUDGET consequence, and why nothing
here deletes anything.

A NOTE FOR THE OPERATOR, so a re-run is not a surprise. `budget_allowance`
is 3 PER SEGMENT (D-07-03), and part of what those spare looks absorb is a
crash in the window between `harness.budget.record_look` committing its
MLflow run and this module writing the Parquet. A run that dies there has
SPENT the look and left no cache, so the next run legitimately materializes
again and that segment's count goes to 2. That is the allowance working as
designed, not a leak -- but it earns one line in the plan's SUMMARY every
time it happens, because three of them exhausts the segment.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
from collections.abc import Sequence
from pathlib import Path

import polars as pl
from mlflow.entities import ViewType
from mlflow.tracking import MlflowClient

from data import lake_paths
from harness import budget
from harness.accessor import materialize
from models.predictions import assert_decision_order
from tracking.mlflow_utils import build_tracking_uri

__all__ = [
    "CACHE_ROOT",
    "LOOK_EXPERIMENT_NAME",
    "TRACKING_ROOT_DIGEST_LEN",
    "CacheError",
    "canonical_cache_root_is_outside_repo_and_lake",
    "look_report",
    "look_run_ids",
    "materialize_once",
    "repo_root",
    "segment_cache_dir",
    "segment_cache_path",
    "tracking_root_digest",
]

logger = logging.getLogger(__name__)

#: The canonical scratch cache root, for a CLI to pass EXPLICITLY -- never a
#: default on any function in this module. Outside the repo and outside the
#: lake; see this module's docstring for both reasons.
CACHE_ROOT: Path = Path("/Volumes/ProjectsSSD/aihedgefund/scratch/phase07")

#: How many hex characters of the tracking root's sha256 go in the path. 16
#: is `models.predictions.PREDICTOR_DIR_PREFIX_LEN`'s register: long enough
#: that two roots colliding is not a thing that happens, short enough that
#: an operator can read the path.
TRACKING_ROOT_DIGEST_LEN: int = 16

#: Where `harness.budget.record_look` puts a look's run. Named here for a
#: human reading a path, and cross-checked against `record_look`'s own
#: default at import (below) so the two cannot drift. `look_run_ids` does
#: NOT scope its query to it -- see that function's docstring.
LOOK_EXPERIMENT_NAME: str = "harness-looks"

_RECORD_LOOK_EXPERIMENT_DEFAULT = (
    inspect.signature(budget.record_look).parameters["experiment_name"].default
)
if LOOK_EXPERIMENT_NAME != _RECORD_LOOK_EXPERIMENT_DEFAULT:
    raise AssertionError(
        f"models.cache: LOOK_EXPERIMENT_NAME is {LOOK_EXPERIMENT_NAME!r} but "
        f"harness.budget.record_look logs into "
        f"{_RECORD_LOOK_EXPERIMENT_DEFAULT!r} -- this module's own docstring "
        "would then name the wrong experiment to an operator chasing a look"
    )

#: Duplicated from `harness.budget._FILTER_SAFE_RE` / `harness.negative_log`'s
#: copy of it (05-PATTERNS.md: a private helper with a real behavioural
#: contract, small and stable enough to copy rather than cross-import -- both
#: of those modules copied it from each other for the same reason).
#: `tests/models/test_cache.py` asserts this pattern is byte-identical to
#: `budget`'s, so the copy cannot drift.
_FILTER_SAFE_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


class CacheError(ValueError):
    """A frame-cache protocol violation: a cached frame whose provenance
    sidecar disagrees with it, or a value that may not be spliced into an
    MLflow filter string.

    A dedicated class for `models.frozen.FrozenPredictorError`'s stated
    reason: a `pytest.raises(ValueError)` meaning "the cache refused a
    tampered frame" would otherwise also be satisfied by "polars refused a
    missing column".
    """


def _require_filter_safe(value: str, label: str) -> None:
    """Refuse (`CacheError`) a value about to be spliced into an MLflow
    `filter_string` via raw f-string interpolation -- see
    `harness.budget._require_filter_safe` for the full rationale
    (05-REVIEW.md WR-04). Checked BEFORE any MLflow client is constructed,
    so a rejected value never reaches a query."""
    if not _FILTER_SAFE_RE.fullmatch(value):
        raise CacheError(
            f"look_run_ids: {label} {value!r} contains a character outside "
            f"{_FILTER_SAFE_RE.pattern} -- refusing to splice it into an "
            "MLflow filter_string unescaped (05-REVIEW.md WR-04)"
        )


def _atomic_write_json(path: Path, body: dict) -> None:
    """Duplicated from `data.store._atomic_write_json` (underscore-private,
    crossed by copying its five lines rather than importing, per the
    project's own stated norm -- `models/frozen.py`, `harness/segments.py`
    and `data/lockbox.py` all do the same)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(body, sort_keys=True, indent=2))
    tmp_path.replace(path)


def tracking_root_digest(tracking_root: str | Path) -> str:
    """`sha256(resolved tracking_root)[:TRACKING_ROOT_DIGEST_LEN]`.

    RESOLVED, not as given: `/Volumes/ProjectsSSD/aihedgefund/mlflow`,
    `./mlflow` from that parent, and a symlink to it are ONE store, and a
    cache keyed on the literal string would let a run reach the same
    store's frames under three different digests -- which is a correctness
    problem in the harmless direction (a needless re-materialization, a
    needless look) but a budget problem all the same.
    """
    resolved = str(Path(tracking_root).resolve())
    return hashlib.sha256(resolved.encode()).hexdigest()[:TRACKING_ROOT_DIGEST_LEN]


def segment_cache_dir(
    cache_root: Path, tracking_root: str | Path, segment_manifest_id: str
) -> Path:
    """`cache_root / <tracking-root digest> / <segment_manifest_id>` -- the
    one directory a run's cached frames, its provenance sidecars and (plan
    07-08's `models.sweep`) its `selection.json` all share.

    Both key components are load-bearing and they answer different
    questions: the segment manifest id says WHICH FOLD GEOMETRY these rows
    came from, and the tracking-root digest says WHICH BUDGET PAID FOR
    THEM. A cache keyed on the first alone is the budget bypass this
    module's docstring opens with.
    """
    return (
        Path(cache_root)
        / tracking_root_digest(tracking_root)
        / str(segment_manifest_id)
    )


def segment_cache_path(
    cache_root: Path,
    tracking_root: str | Path,
    segment_manifest_id: str,
    segment_name: str,
) -> Path:
    """Where one segment's cached frame lives:
    `segment_cache_dir(...) / f"{segment_name}.parquet"`."""
    return segment_cache_dir(cache_root, tracking_root, segment_manifest_id) / (
        f"{segment_name}.parquet"
    )


def _sidecar_path(frame_path: Path) -> Path:
    """The provenance JSON beside a cached frame -- `<segment>.cache.json`.

    Written AFTER the frame, and its absence is a CACHE HIT anyway (see
    `materialize_once`): making the hit conditional on it would widen the
    window in which a crash costs a second look, and this module exists to
    narrow that window, not to widen it for tidier bookkeeping.
    """
    return frame_path.with_suffix(".cache.json")


def _read_cached_frame(frame_path: Path) -> pl.DataFrame:
    """A cached frame, with `assert_decision_order` run on it and its
    provenance sidecar cross-checked when one is present.

    The order assertion runs on a READ as well as on arrival, for
    `models.predictions.assert_decision_order`'s own stated reason: the
    sequential simulator scan is silently wrong on a reordered frame, and a
    cache file is exactly the kind of artifact somebody eventually
    regenerates by hand.
    """
    frame = pl.read_parquet(frame_path)
    assert_decision_order(frame)
    sidecar = _sidecar_path(frame_path)
    if sidecar.exists():
        recorded = json.loads(sidecar.read_text())
        if int(recorded["rows"]) != frame.height:
            raise CacheError(
                f"materialize_once: cached frame {frame_path} has "
                f"{frame.height} rows but its provenance sidecar records "
                f"{recorded['rows']} -- one of the two was rewritten, and a "
                "frame whose provenance cannot be trusted must not stand in "
                "for a look that was paid for"
            )
    else:
        logger.warning(
            "materialize_once: cached frame %s has no provenance sidecar -- "
            "reading it anyway (a missing sidecar is a crash between the "
            "frame write and the sidecar write, and re-materializing would "
            "spend a second look to recover bookkeeping)",
            frame_path,
        )
    return frame


def materialize_once(
    segment_manifest_id: str,
    segment_name: str,
    *,
    registry_root: Path,
    lake_root: Path,
    tracking_root: str,
    run_tags: dict,
    cache_root: Path,
) -> pl.DataFrame:
    """`harness.accessor.materialize` AT MOST ONCE per `(segment manifest,
    segment, tracking root)`; every later call reads the Parquet cache.

    `cache_root` is REQUIRED and has no default -- see this module's
    docstring for why a default would be a loaded gun under hooks 18/19.

    The order of operations, each step for a stated reason:

    1. CACHE HIT: the frame is read back, `assert_decision_order` runs on
       it, and it is returned WITHOUT calling `materialize` -- so no look is
       recorded, which is the entire point.
    2. CACHE MISS: `budget.look_count` is logged BEFORE the call, then
       `materialize` runs, then `assert_decision_order` runs on the returned
       frame THE INSTANT it returns -- the cheapest possible statement that
       the sequential scan downstream is valid, and simultaneously a proof
       that `etime` is a unique key on this frame (strictly ascending
       implies unique). Only then is the frame written, atomically, and
       `look_count` logged again.
    3. `harness.budget.BudgetExhaustedError` is NEVER swallowed. An
       exhausted segment must stop the run, not fall through to a stale
       cache or to zero rows.

    THE ORDER ASSERTION PRECEDES THE WRITE, not the other way round. A frame
    that fails it is not cached at all: caching it would turn one bad
    materialization into a permanently poisoned input that costs no look to
    read, which is the worst of both properties.

    `train`-role segments cost no look (`harness.accessor._LOOK_ROLES` is
    `{"val", "oof_block"}`), and are cached here all the same -- a 4.5 GiB
    frame re-read from the tier through the purge/embargo gate on every
    step is the OTHER expense D-07-05 is about.
    """
    frame_path = segment_cache_path(
        cache_root, tracking_root, segment_manifest_id, segment_name
    )
    if frame_path.exists():
        frame = _read_cached_frame(frame_path)
        logger.info(
            "materialize_once: CACHE HIT %s (%d rows) -- no look spent",
            frame_path,
            frame.height,
        )
        return frame

    spent_before = budget.look_count(
        segment_manifest_id, segment_name, tracking_root=tracking_root
    )
    logger.info(
        "materialize_once: CACHE MISS for segment %r of manifest %s -- "
        "look_count BEFORE materialize: %d",
        segment_name,
        segment_manifest_id[:12],
        spent_before,
    )
    frame = materialize(
        segment_manifest_id,
        segment_name,
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=tracking_root,
        run_tags=run_tags,
    )
    etime = assert_decision_order(frame)

    frame_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = frame_path.with_suffix(frame_path.suffix + ".tmp")
    frame.write_parquet(tmp_path)
    tmp_path.replace(frame_path)

    spent_after = budget.look_count(
        segment_manifest_id, segment_name, tracking_root=tracking_root
    )
    _atomic_write_json(
        _sidecar_path(frame_path),
        {
            "segment_manifest_id": segment_manifest_id,
            "segment_name": segment_name,
            "tracking_root_digest": tracking_root_digest(tracking_root),
            "rows": int(frame.height),
            "etime_min": int(etime[0]) if etime.size else None,
            "etime_max": int(etime[-1]) if etime.size else None,
            "look_count_before": int(spent_before),
            "look_count_after": int(spent_after),
        },
    )
    logger.info(
        "materialize_once: wrote %s (%d rows) -- look_count AFTER materialize: %d",
        frame_path,
        frame.height,
        spent_after,
    )
    return frame


def look_report(
    segment_manifest_id: str,
    segment_names: Sequence[str],
    *,
    tracking_root: str,
) -> dict[str, int]:
    """`{segment_name: look_count}` for every name given, PRINTED as well as
    returned.

    Called at the top AND the bottom of every run that touches a look, so a
    leak is visible the first time it happens rather than on the fourth,
    when `budget_allowance` runs out and the remedy is a whole new segment
    manifest (D-05-14). Printed rather than only logged because the operator
    reading a slice run's stdout is the person who has to notice.
    """
    counts = {
        name: budget.look_count(segment_manifest_id, name, tracking_root=tracking_root)
        for name in segment_names
    }
    print(
        f"look_report: manifest {segment_manifest_id[:12]} at "
        f"{Path(tracking_root).resolve()}"
    )
    for name, count in counts.items():
        print(f"  {name:16s} look_count={count}")
    return counts


def look_run_ids(
    segment_manifest_id: str,
    segment_names: Sequence[str],
    *,
    tracking_root: str,
) -> dict[str, list[str]]:
    """`{segment_name: [run_id, ...]}` -- the runs `budget.look_count`
    COUNTED, identified rather than tallied.

    IT EXISTS BECAUSE THE IDS CANNOT BE CARRIED IN MEMORY.
    `budget.record_look` returns a run id, but `harness.accessor.materialize`
    returns only a frame and discards it, and the run that needs those ids
    (the slice's own tracked run, plan 07-09/07-11) opens in a LATER PROCESS
    at a later commit -- `models.sweep` writes them into `selection.json` for
    exactly that reason. Re-querying them is therefore the only route, and
    this is that query.

    THE FILTER IS BYTE-IDENTICAL TO `budget.look_count`'s, AND DELIBERATELY
    NOT SCOPED TO `LOOK_EXPERIMENT_NAME`. `look_count` searches every
    experiment; a narrower query here could return fewer ids than that
    function counts, and the whole value of this function is that
    `len(ids) == look_count(...)` for every segment. That equality is
    asserted INSIDE this function, per segment, so the two can never drift
    into disagreeing silently -- `look_count` is called first anyway, which
    is also what inherits its canonical-root, initialised-store and
    filter-safety refusals before any client is built here.
    """
    _require_filter_safe(segment_manifest_id, "segment_manifest_id")
    for name in segment_names:
        _require_filter_safe(name, "segment_name")
    counts = {
        name: budget.look_count(segment_manifest_id, name, tracking_root=tracking_root)
        for name in segment_names
    }
    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    experiment_ids = [
        exp.experiment_id for exp in client.search_experiments(view_type=ViewType.ALL)
    ]
    ids: dict[str, list[str]] = {}
    for name in segment_names:
        if not experiment_ids:
            ids[name] = []
        else:
            runs = client.search_runs(
                experiment_ids,
                filter_string=(
                    f"tags.segment_manifest_id = '{segment_manifest_id}' and "
                    f"tags.segment_name = '{name}'"
                ),
                run_view_type=ViewType.ALL,
            )
            ids[name] = [run.info.run_id for run in runs]
        if len(ids[name]) != counts[name]:
            raise CacheError(
                f"look_run_ids: segment {name!r} of manifest "
                f"{segment_manifest_id[:12]} has look_count={counts[name]} "
                f"but this query returned {len(ids[name])} run id(s) -- the "
                "two read the same tags in the same store, so a disagreement "
                "means one of the two queries has drifted and neither number "
                "can be reported"
            )
    return ids


def repo_root() -> Path:
    """The repository root, derived from THIS file's own location:
    `mvp/models/cache.py` -> `parents[2]`.

    NOT `data.lake_paths.PKG_ROOT.parent`, which was the first thing tried
    here and was WRONG in a way that still passed. `PKG_ROOT` is
    `Path(__file__).resolve().parent` inside `mvp/data/lake_paths.py`, so it
    is `mvp/data/` and its parent is `mvp/` -- one level short. The check
    below then proved "outside `mvp/`", which does not imply "outside the
    repo", and it only came out True because `CACHE_ROOT` happens to sit on
    a wholly different path. A weaker fact passing for the stronger one.

    The derivation is GUARDED rather than trusted: `mvp/pyproject.toml` must
    exist under the result, so a future move of this file fails loudly here
    instead of silently narrowing the claim again.
    """
    root = Path(__file__).resolve().parents[2]
    if not (root / "mvp" / "pyproject.toml").is_file():
        raise CacheError(
            f"repo_root: derived {root} from {Path(__file__).resolve()} but "
            "there is no mvp/pyproject.toml under it -- this module moved and "
            "the parents[2] hop no longer reaches the repository root. Fix the "
            "hop rather than letting a containment check prove a weaker fact "
            "than it claims."
        )
    return root


def canonical_cache_root_is_outside_repo_and_lake() -> bool:
    """`True` when `CACHE_ROOT` sits outside BOTH the repo tree and the
    default lake root -- the property this module's docstring claims, made
    checkable rather than asserted in prose.

    `DEFAULT_LAKE_ROOT` is read as a CONSTANT rather than through
    `lake_paths.lake_root()`, which would `mkdir` the real lake as a side
    effect of answering a question about a path.
    """
    cache = CACHE_ROOT.resolve()
    lake = Path(lake_paths.DEFAULT_LAKE_ROOT).resolve()
    return not cache.is_relative_to(repo_root()) and not cache.is_relative_to(lake)
