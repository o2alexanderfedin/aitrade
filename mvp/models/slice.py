"""The Stage-1 regression slice end to end: curated rows to a fitted model
to a stored prediction table to the simulator to ONE tracked run -- in an
order that two measurements force rather than one person's taste.

THE THREE MODES, AND WHY THE SPLIT IS LOAD-BEARING.

- `mode="select"` -- steps 1 to 4. Spends the five OOF looks, spends NO
  `val` look, and writes NO registry body. It hands its winner on through
  `selection.json` (`models.sweep`), not through memory.
- `mode="freeze"` -- step 5 alone. Refits the winner on the FULL train frame
  from the cache (no look) and writes the frozen body. A SEPARATE entry
  point because that body lands under the real, git-committed registry, and
  the plan that does it for real must commit it in the SAME commit as the
  guardrail extension that teaches the scanners about `predictors/`. A body
  that appeared during `--select` would sit in an unscanned directory across
  a commit boundary and make that one-commit rule a fiction.
- `mode="val"` -- steps 6 to 9. REFUSES unless a frozen body already exists,
  so "freeze before you look" is mechanical rather than procedural: the
  winner's coefficients are immutable in git before they ever meet the
  validation window, and a problem at step 5 is debugged with zero looks
  spent.

`code_hash` IS A REQUIRED PARAMETER AND THIS MODULE NEVER CONSULTS GIT.
Not a stylistic preference -- a measured constraint. `compute_code_hash`
appends `-dirty` whenever `git status --porcelain` is non-empty, and
pre-commit stashes only UNSTAGED changes, so a STAGED file leaves `M  path`
visible and the tree reads dirty during every hook run. Hook 19 is the full
pytest suite, and `tests/models/test_slice_end_to_end.py` calls `run_slice`
many times: a `compute_code_hash()`-plus-refuse inside this module would
raise on EVERY commit anyone ever makes in this repo, including commits that
touch nothing under `mvp/models/`. The same call would also dirty the tree
AS IT RAN -- step 2 issues a normalisation manifest and step 7 a
prediction-table manifest, both untracked -- breaking the two documented
crash-recovery paths as well. So the check here is a pure STRING check
(`code_hash.endswith("-dirty")`), the one injected value is threaded through
every manifest, every look run, every negative record and the tracked run,
and `scripts/run_stage1_slice.py` -- where git legitimately lives -- calls
`compute_code_hash()` once per invocation and passes it in. The repo has no
precedent for computing-and-refusing inside a library: `issue_manifest`,
`issue_segment_manifest` and `write_normalization_artifact` all take
`code_hash` as a parameter.

THE STEP ORDER, FORCED BY D-07-30. `mlflow.start_run` raises when a run is
already active, and neither `harness.budget.record_look` nor
`harness.negative_log.record_negative_result` passes `nested=True`. So the
slice's OWN run opens LAST, after every look and every negative record has
closed, and `_require_no_active_run` asserts that at each of those call
sites instead of trusting the reading order of this file.

RUN 3 OPENS WHATEVER THE GATES SAY. Success criterion 3 asks for a run
manifest in MLflow from which every byte can be rebuilt; a phase that
produced no run because the model underperformed would fail SC3 on top of
SC1. A failing `gate_monetization` is recorded as a negative result FIRST
(runs cannot nest), then run 3 opens and logs the failing numbers.
`guard_against_ceiling` is the one exception and is not a gate: a P&L at the
perfect-foresight ceiling is an investigation halt, so it raises and no run
is opened for it.

REUSE BEFORE WRITING, AT STEPS 2 AND 7. Both `write_normalization_artifact`
and `write_prediction_table` enforce write-once with a PARENT-directory glob
guard, so a run that crashed after either of them could never be re-invoked:
the already-correct file would sit behind a `FileExistsError`. Each step
therefore resolves an existing artifact first and continues, which is what
makes `--resume-from-cache` a no-op at those steps rather than a refusal.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import polars as pl

from data import store
from data.store import (
    manifest_path,
    manifests_for_dataset,
)
from features.normalize import (
    apply_normalization,
    fit_training_segment,
    load_normalization,
    normalization_artifact_path,
    normalization_dataset,
    write_normalization_artifact,
)
from features.tier import FEATURE_COLUMNS, curated_manifest_input
from harness import negative_log
from harness.segments import read_segment_manifest
from models.cache import look_run_ids, materialize_once, segment_cache_path
from models.conversion import neutral_fill_null_predictions
from models.frozen import read_frozen_predictor, write_frozen_predictor
from models.gates import (
    closed_pnl_ticks,
    gate_forecast,
    gate_monetization,
    guard_against_ceiling,
    perfect_foresight_ceiling,
    ticks_to_usd_at_traded_lot,
)
from models.metrics import forecast_metrics
from models.predictions import (
    PREDICTION_TABLE_SCHEMA,
    assert_decision_order,
    assert_table_aligned,
    load_prediction_table,
    partition_overlaps_segment,
    prediction_table_path,
    predictions_dataset,
    write_prediction_table,
)
from models.predictor_id import RECIPE_FIELDS
from models.protocol import FitInputs
from models.regression import (
    DIAGNOSTIC_TARGET_NAMES,
    GRID,
    TARGET_NAME,
    FitContext,
    GridEntry,
    validate_feature_names,
)
from models.regression import FEATURE_NAMES as MODEL_FEATURE_NAMES
from models.sweep import read_selection, run_oof_sweep, selection_winner_trainer
from sim.arrays import sim_arrays
from sim.kernel import run_sim_checked
from tracking.mlflow_utils import (
    MAX_PROVENANCE_TAG_VALUE_CHARS,
    ProvenanceValueTooLong,
    compute_env_hash,
    log_data_provenance,
    start_tracked_run,
)

__all__ = [
    "FEATURES_DAY_ROLE",
    "MODES",
    "MODE_FREEZE",
    "MODE_SELECT",
    "MODE_VAL",
    "SCORED_SEGMENT_TAG_KEY",
    "SLICE_EXPERIMENT_NAME",
    "SLICE_STAGE_TAG",
    "UV_LOCK_PATH",
    "VAL_SEGMENT_NAME",
    "SliceError",
    "SliceResult",
    "dq_preflight",
    "find_frozen_body",
    "run_slice",
    "select_train_day_manifests",
]

logger = logging.getLogger(__name__)

MODE_SELECT: str = "select"
MODE_FREEZE: str = "freeze"
MODE_VAL: str = "val"
MODES: tuple[str, ...] = (MODE_SELECT, MODE_FREEZE, MODE_VAL)

#: The one segment name this module's `mode="val"` will materialize. Named
#: as a constant so the sweep's `oof_block_<int>` confinement (D-07-04) has a
#: single, greppable counterpart: this string appears in exactly one mode.
VAL_SEGMENT_NAME: str = "val"

#: The slice's own MLflow experiment, and the `stage` tag `mvp/spec.md`'s tag
#: schema requires. Distinct from `harness-looks` (where a look's run lives)
#: and from `negative-results`, because the budget counter IS the look run
#: count and a slice run in that experiment would inflate it.
SLICE_EXPERIMENT_NAME: str = "stage-1-regression"
SLICE_STAGE_TAG: str = "stage1_regression"

#: WHICH SEGMENT THIS RUN SCORED -- and it is NOT called `segment_name`, for a
#: reason that was measured rather than reasoned about.
#:
#: `harness.budget.look_count` counts every run in the store whose tags
#: satisfy `segment_manifest_id = <id> AND segment_name = <segment>`. It does
#: not restrict itself to the `harness-looks` experiment (deliberately, and
#: `models.cache.look_run_ids` inherits that). `segment_manifest_id` is one of
#: the eight MANDATORY tags, so a slice run tagged `segment_name="val"` is
#: indistinguishable from a look: measured on the fixture, `look_count(val)`
#: read 2 after ONE honest look, and 3 after a second cache-only invocation
#: that materialized nothing at all. On the real manifest that reads as two of
#: the three allowance slots gone, spent by a bookkeeping tag.
#:
#: The information is worth keeping, so the KEY moves rather than the value.
#: `harness.negative_log`'s own tag set (via `models.sweep`) never carried
#: `segment_name` either, which is why the sweep never tripped this.
SCORED_SEGMENT_TAG_KEY: str = "scored_segment_name"

#: The `role` a feature-day manifest plays in a normalisation artifact's
#: `inputs[]`, matching what `tests/features/test_normalize.py` already
#: writes so the two provenance shapes cannot drift.
FEATURES_DAY_ROLE: str = "features_day"

#: `mvp/uv.lock` -- `env_hash`'s source. Derived from THIS file's location
#: (`mvp/models/slice.py` -> `parents[1]`) rather than from
#: `data.lake_paths.PKG_ROOT`, whose `.parent` hop cost plan 07-08 a false
#: claim that passed its own test.
UV_LOCK_PATH: Path = Path(__file__).resolve().parents[1] / "uv.lock"


class SliceError(ValueError):
    """A slice protocol violation: a dirty injected `code_hash`, an unknown
    mode, a missing frozen body, a `val` mode without a selection, or an
    MLflow run already active where the forced order says none may be.

    A dedicated class for `models.frozen.FrozenPredictorError`'s stated
    reason: a `pytest.raises(ValueError)` meaning "the slice refused to look
    at val without a frozen winner" would otherwise also be satisfied by
    "polars refused a missing column".
    """


@dataclass(frozen=True)
class SliceResult:
    """What one `run_slice` invocation did, in the shape a test and a CLI
    both need. Every field a mode does not reach stays `None`, so a caller
    that reads a `val`-only field off a `select` result gets `None` rather
    than a plausible stale number."""

    mode: str
    segment_manifest_id: str
    code_hash: str
    normalization_manifest_id: str
    train_end_date: str
    train_rows: int
    source_feature_manifest_ids: tuple[str, ...]
    normalization_reused: bool
    # mode="select"
    winner_grid_index: int | None = None
    winner_model_class: str | None = None
    winner_hyperparameters: Mapping[str, Any] | None = None
    eligible_count: int | None = None
    n_configs: int | None = None
    selection_path: Path | None = None
    block_names: tuple[str, ...] = ()
    # mode="freeze"
    predictor_manifest_id: str | None = None
    predictor_id: str | None = None
    frozen_body: Mapping[str, Any] | None = None
    # mode="val"
    prediction_table_manifest_id: str | None = None
    prediction_table_reused: bool | None = None
    run_id: str | None = None
    metrics: Mapping[str, float] | None = None
    forecast_gate_passed: bool | None = None
    forecast_gate_reason: str | None = None
    monetization_gate_passed: bool | None = None
    monetization_gate_reason: str | None = None
    ceiling: Mapping[str, Any] | None = None
    observed_look_run_ids: Mapping[str, tuple[str, ...]] | None = None
    negative_run_id: str | None = None


# --------------------------------------------------------------------------
# The two pure guards the forced order rests on
# --------------------------------------------------------------------------


def _require_clean_code_hash(code_hash: object) -> str:
    """The injected hash, or `SliceError` -- a PURE STRING CHECK with no git
    in it (see this module's docstring for the full reason).

    `-dirty` is refused rather than tolerated because every id this run
    issues embeds it: the normalisation manifest, the prediction-table
    manifest, the frozen body's `manifest_id` AND its `predictor_id`. A
    registry is append-only, so a dirty freeze is immutable forever and
    nothing downstream could ever say which working tree produced it.
    """
    if not isinstance(code_hash, str) or not code_hash.strip():
        raise SliceError(
            f"run_slice: code_hash must be a non-empty string, got "
            f"{code_hash!r} -- a blank hash still satisfies "
            "MANDATORY_TAG_KEYS's presence check and would be logged as if it "
            "were a reproducibility-grade git SHA"
        )
    if code_hash.endswith("-dirty"):
        raise SliceError(
            f"run_slice: code_hash {code_hash!r} ends in '-dirty' -- every "
            "manifest, look run and frozen body this invocation writes would "
            "embed it, in append-only registries, forever. Commit the tree "
            "and re-invoke; do not relax this check."
        )
    return code_hash


def _require_no_active_run(step: str) -> None:
    """`SliceError` when an MLflow run is already active (D-07-30).

    ASSERTED AT THE CALL SITE rather than inferred from the reading order of
    this file. `harness.budget.record_look` and
    `harness.negative_log.record_negative_result` each open their own run and
    pass no `nested=True`, so `mlflow.start_run` would raise a bare
    `Exception` whose message says nothing about which of this module's steps
    ran out of order.
    """
    active = mlflow.active_run()
    if active is not None:
        raise SliceError(
            f"run_slice: an MLflow run ({active.info.run_id}) is already "
            f"active at {step} -- D-07-30: runs cannot nest here, so the "
            "slice's own run must open LAST, after every look and every "
            "negative result has closed"
        )


# --------------------------------------------------------------------------
# Provenance tag sharding (copied, per the repo's private-helper norm)
# --------------------------------------------------------------------------


def _shard_key(key: str, index: int) -> str:
    """Duplicated from `tracking.mlflow_utils._shard_key` (underscore-private,
    crossed by copying its one line rather than importing, per the project's
    own stated norm -- `models/cache.py`, `models/sweep.py`,
    `harness/segments.py` and `data/lockbox.py` all copy a private helper for
    the same reason). `tracking.mlflow_utils.read_provenance_tag` is the
    public inverse and the only correct way to read these back."""
    return key if index == 0 else f"{key}_{index + 1:04d}"


def _shard_values(values: Sequence[str], limit: int) -> list[str]:
    """Duplicated from `tracking.mlflow_utils._shard_values`. MLflow
    TRUNCATES an over-long tag value and logs a warning rather than raising
    (WR-01), so a `look_run_ids` tag that read as complete could end mid-id;
    splitting only on comma boundaries is what keeps every id whole."""
    shards: list[str] = []
    current: list[str] = []
    length = 0
    for value in values:
        if len(value) > limit:
            raise ProvenanceValueTooLong(
                f"run_slice: provenance value of {len(value)} characters "
                f"exceeds the {limit}-character MLflow tag limit"
            )
        addition = len(value) + (1 if current else 0)
        if current and length + addition > limit:
            shards.append(",".join(current))
            current, length = [], 0
            addition = len(value)
        current.append(value)
        length += addition
    shards.append(",".join(current))
    return shards


# --------------------------------------------------------------------------
# Step 2: the normalisation artifact and the provenance that must be TRUE
# --------------------------------------------------------------------------


def select_train_day_manifests(
    bodies: Sequence[Mapping[str, Any]], *, train_start_ns: int, train_end_ns: int
) -> tuple[tuple[str, ...], str]:
    """`(manifest_ids, train_end_date)` for the feature manifests whose rows
    lie inside the TRAIN window -- PURE, so the rule can be checked against
    hand-built bodies rather than only against whichever lake is to hand.

    THIS IS THE FUNCTION THAT KEEPS ONE PROVENANCE CLAIM HONEST. The nearest
    thing to hand is the segment manifest's own
    `upstream_feature_manifest_ids`, which names every day the POOL spans --
    on the approved manifest that is 2026-09-12..18, including the two `val`
    days. Writing those into a normalisation artifact's
    `source_feature_manifest_ids` would make the artifact assert that its
    parameters saw days 17 and 18. False, and false in precisely the record a
    future auditor reads to decide whether the `val` look was honest.

    The filter is `models.predictions.partition_overlaps_segment` on each
    partition's own `etime_min`/`etime_max` against `[train_start_ns,
    train_end_ns)`, never on a date string: the ns clock is the only clock
    (`etime`), and the fixture proves why a date comparison would be wrong --
    its partition is dated `2026-09-13` while its `etime` starts at epoch 0.

    `train_end_date` is then the LAST calendar day among the kept
    partitions -- "the last day inside the train segment", derived rather
    than typed. On the approved manifest that is `2026-09-16`, because train
    ends at the UTC midnight that opens 09-17; on the fixture it is that
    fixture's single date.
    """
    if not bodies:
        raise SliceError(
            "select_train_day_manifests: no upstream feature manifests -- a "
            "normalisation artifact that cannot name what it was fit on is "
            "only auditable while somebody remembers"
        )
    kept: list[str] = []
    dates: list[str] = []
    for body in bodies:
        partitions = [
            part
            for part in body.get("partitions") or []
            if partition_overlaps_segment(
                int(part["etime_min"]),
                int(part["etime_max"]),
                int(train_start_ns),
                int(train_end_ns),
            )
        ]
        if not partitions:
            continue
        kept.append(str(body["manifest_id"]))
        dates.extend(str(part["date"]) for part in partitions if part.get("date"))
    if not kept:
        raise SliceError(
            f"select_train_day_manifests: none of the "
            f"{len(bodies)} upstream feature manifests has a partition "
            f"overlapping the train window [{train_start_ns}, "
            f"{train_end_ns}) -- the normalisation fit would have no "
            "provenance at all"
        )
    if not dates:
        raise SliceError(
            "select_train_day_manifests: the kept partitions carry no 'date' "
            "key, so train_end_date cannot be derived. "
            "features.normalize._train_dates FAILS CLOSED on an input that "
            "names no date, so guessing one here would only move the failure"
        )
    return tuple(kept), max(dates)


def _train_day_inputs(
    manifest: Mapping[str, Any],
    *,
    registry_root: Path,
    train_start_ns: int,
    train_end_ns: int,
) -> tuple[list[dict], str]:
    """`select_train_day_manifests` applied to the real registry: the
    `inputs[]` entries `write_normalization_artifact` wants, plus the derived
    `train_end_date`."""
    symbol = manifest["symbol"]
    dataset = f"{symbol}.features"
    upstream = [str(value) for value in manifest["upstream_feature_manifest_ids"]]
    bodies = [
        json.loads(manifest_path(Path(registry_root), dataset, manifest_id).read_text())
        for manifest_id in upstream
    ]
    kept, train_end_date = select_train_day_manifests(
        bodies, train_start_ns=train_start_ns, train_end_ns=train_end_ns
    )
    logger.info(
        "run_slice: normalisation provenance names %d of %d upstream feature "
        "manifests (train_end_date=%s); excluded %s",
        len(kept),
        len(upstream),
        train_end_date,
        [mid[:12] for mid in upstream if mid not in kept] or "nothing",
    )
    return [
        curated_manifest_input(
            dataset,
            manifest_id,
            registry_root=Path(registry_root),
            role=FEATURES_DAY_ROLE,
        )
        for manifest_id in kept
    ], train_end_date


def _existing_normalization(
    *, lake_root: Path, registry_root: Path, symbol: str, train_end_date: str
) -> str | None:
    """The manifest id of an already-written `features_norm` artifact for
    this `(symbol, train_end_date)`, or `None`.

    REUSE BEFORE WRITING (see the module docstring).
    `write_normalization_artifact` globs the `train_end=` PARENT for
    `part-*.parquet` and raises `FileExistsError` if one is there, so without
    this lookup a `--select` that crashed anywhere after step 2 could never
    be re-run: the free, already-cached sweep would sit unreachable behind a
    refusal about a file that is already correct.

    The id comes from the REGISTRY, matched on the partition's lake-relative
    path -- never from the file name, which embeds only `time.time_ns()`.
    """
    parent = normalization_artifact_path(Path(lake_root), symbol, train_end_date).parent
    existing = sorted(parent.glob("part-*.parquet"))
    if not existing:
        return None
    wanted = str(existing[0].relative_to(Path(lake_root)))
    matches = [
        body["manifest_id"]
        for body in manifests_for_dataset(
            Path(registry_root), normalization_dataset(symbol)
        )
        if any(part.get("path") == wanted for part in body.get("partitions") or [])
    ]
    if len(matches) != 1:
        raise SliceError(
            f"run_slice: {existing[0]} exists but "
            f"{len(matches)} manifests in "
            f"{normalization_dataset(symbol)} name it -- exactly one must, or "
            "the artifact cannot be cited by id. Do not write a second one "
            "beside it."
        )
    return matches[0]


def _resolve_normalization(
    train: pl.DataFrame,
    *,
    manifest: Mapping[str, Any],
    registry_root: Path,
    lake_root: Path,
    code_hash: str,
    fit_allowed: bool,
) -> tuple[str, str, bool, tuple[str, ...]]:
    """`(normalization_manifest_id, train_end_date, reused, source_ids)`.

    `fit_allowed=False` for `mode="freeze"` and `mode="val"`: those run at a
    LATER commit than the fit, and a second artifact issued under their own
    `code_hash` would carry a different manifest id -- which
    `models.sweep.read_selection` would then refuse as a disagreement with
    the `selection.json` the five looks paid for. So they resolve the
    existing one and refuse if it is absent.
    """
    symbol = manifest["symbol"]
    train_segment = _segment(manifest, "train")
    inputs, train_end_date = _train_day_inputs(
        manifest,
        registry_root=registry_root,
        train_start_ns=int(train_segment["start_ns"]),
        train_end_ns=int(train_segment["end_ns"]),
    )
    existing = _existing_normalization(
        lake_root=lake_root,
        registry_root=registry_root,
        symbol=symbol,
        train_end_date=train_end_date,
    )
    if existing is not None:
        logger.info(
            "run_slice: reusing features_norm artifact %s for "
            "(%s, train_end=%s) -- no second fit, no second manifest",
            existing[:12],
            symbol,
            train_end_date,
        )
        return (
            existing,
            train_end_date,
            True,
            tuple(entry["manifest_id"] for entry in inputs),
        )
    if not fit_allowed:
        raise SliceError(
            f"run_slice: no features_norm artifact exists for ({symbol}, "
            f"train_end={train_end_date}) -- mode must be {MODE_SELECT!r} to "
            "fit one. A freeze or a val run at a later commit must cite the "
            "artifact the selection was made against, not issue a new one "
            "under its own code_hash"
        )

    etime = assert_decision_order(train)
    # FEATURE_COLUMNS, all four -- the artifact may carry `mid`; the
    # estimator's own allow-list (models.regression.FORBIDDEN_FEATURE_NAMES)
    # is what keeps a raw price level out of the design matrix.
    values = {
        name: np.asarray(
            train[name].fill_null(float("nan")).to_numpy(), dtype=np.float64
        )
        for name in FEATURE_COLUMNS
    }
    # INCLUSIVE, on the ns clock: `fit_training_segment` keeps
    # `etime <= train_end_etime`, and the train segment's `end_ns` is
    # exclusive.
    fit = fit_training_segment(
        values, etime, train_end_etime=int(train_segment["end_ns"]) - 1
    )
    if fit.train_row_count != train.height:
        raise SliceError(
            f"run_slice: the normalisation fit used {fit.train_row_count} of "
            f"the train frame's {train.height} rows -- every row of a "
            "train-role segment is inside the train window by construction, "
            "so a shortfall means the frame is not this segment's"
        )
    issued = write_normalization_artifact(
        fit.params,
        symbol=symbol,
        train_end_date=train_end_date,
        source_feature_manifest_ids=inputs,
        train_row_count=fit.train_row_count,
        train_etime_range=fit.train_etime_range,
        lake_root=Path(lake_root),
        registry_root=Path(registry_root),
        code_hash=code_hash,
    )
    logger.info(
        "run_slice: issued features_norm manifest %s over %d train rows "
        "(train_end=%s) from %d feature manifests",
        issued["manifest_id"][:12],
        fit.train_row_count,
        train_end_date,
        len(inputs),
    )
    return (
        issued["manifest_id"],
        train_end_date,
        False,
        tuple(entry["manifest_id"] for entry in inputs),
    )


# --------------------------------------------------------------------------
# Step 5's product, found again at step 9's commit
# --------------------------------------------------------------------------


def _canonical_knobs(hyperparameters: Mapping[str, Any]) -> dict[str, Any]:
    """Hyperparameters with every number widened to `float`, so a stored
    `2` and a live `2.0` compare equal. `models.predictor_id` already
    canonicalises for HASHING; this is the weaker comparison the body search
    needs, and it is deliberately not the hash."""
    out: dict[str, Any] = {}
    for key, value in hyperparameters.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            out[str(key)] = value
        else:
            out[str(key)] = float(value)
    return out


def find_frozen_body(
    *,
    registry_root: Path,
    model_class: str,
    hyperparameters: Mapping[str, Any],
    seed: int,
    normalization_manifest_id: str,
) -> tuple[str, dict]:
    """`(manifest_id, body)` for the one frozen predictor matching this
    recipe MINUS `code_hash`.

    WHY `code_hash` IS EXCLUDED FROM THE SEARCH, AND WHY THIS IS NOT A
    LOOKUP BY FINGERPRINT. `selection.json`'s `config_fingerprint` equals
    `predictor_id` over the SELECT-time recipe -- including the SELECT-time
    `code_hash`. Step 5 runs at a later commit by design (the freeze is
    committed before `val` is touched), so the body's `predictor_id` is a
    different string. A lookup by the recorded fingerprint would therefore
    find nothing on the real run while passing on a fixture that runs both
    modes at one HEAD: a green test behind a false fact, which this phase has
    already paid for once.

    Refuses on zero (naming `--freeze` as the remedy) and on more than one:
    D-07-22's two-runs-of-one-recipe case lands two bodies at the same
    `predictor_id` under different `manifest_id`s, and picking either would
    be picking coefficients at random.
    """
    directory = Path(registry_root) / "predictors"
    wanted = _canonical_knobs(hyperparameters)
    matches: list[tuple[str, dict]] = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        body = json.loads(path.read_text())
        if not isinstance(body, dict):
            continue
        if (
            body.get("model_class") == model_class
            and _canonical_knobs(body.get("hyperparameters") or {}) == wanted
            and int(body.get("seed", -1)) == int(seed)
            and body.get("normalization_manifest_id") == normalization_manifest_id
        ):
            matches.append((path.stem, body))
    if not matches:
        raise SliceError(
            f"run_slice: no frozen predictor in {directory} matches the "
            f"selected winner ({model_class} {dict(wanted)}, seed={seed}, "
            f"normalization={normalization_manifest_id[:12]}) -- run "
            f"mode={MODE_FREEZE!r} and COMMIT the body before spending the "
            "val look. Freezing first is what makes the winner's "
            "coefficients immutable in git before they meet the validation "
            "window; skipping it would leave nothing to hold the run to."
        )
    if len(matches) > 1:
        raise SliceError(
            f"run_slice: {len(matches)} frozen predictors in {directory} "
            f"match the selected winner ({[mid for mid, _ in matches]}) -- "
            "D-07-22: two runs of one recipe share a predictor_id under "
            "different manifest_ids, so the coefficients differ and picking "
            "one would be picking at random. Name the body explicitly."
        )
    return matches[0]


# --------------------------------------------------------------------------
# Step 7: the prediction table, reused before written
# --------------------------------------------------------------------------


def _existing_prediction_table(
    *,
    lake_root: Path,
    registry_root: Path,
    symbol: str,
    segment_manifest_id: str,
    segment_name: str,
    predictor_id: str,
) -> tuple[str, pl.DataFrame] | None:
    """`(manifest_id, table)` for an already-stored prediction table on this
    `(segment manifest, segment, predictor)` triple, or `None`.

    REUSE BEFORE WRITING, for `write_prediction_table`'s write-once PARENT
    glob guard: a crash at step 8 (the simulator) or step 9 (MLflow) leaves
    step 7 complete, and `--resume-from-cache` would then fail on its very
    first action -- a refusal about a file that is already exactly right.
    """
    parent = prediction_table_path(
        Path(lake_root), symbol, segment_manifest_id, segment_name, predictor_id
    ).parent
    existing = sorted(parent.glob("part-*.parquet"))
    if not existing:
        return None
    wanted = str(existing[0].relative_to(Path(lake_root)))
    matches = [
        body["manifest_id"]
        for body in manifests_for_dataset(
            Path(registry_root), predictions_dataset(symbol)
        )
        if any(part.get("path") == wanted for part in body.get("partitions") or [])
    ]
    if len(matches) != 1:
        raise SliceError(
            f"run_slice: {existing[0]} exists but {len(matches)} manifests in "
            f"{predictions_dataset(symbol)} name it -- exactly one must, or "
            "the table cannot be cited as this run's data_hash"
        )
    stored = load_prediction_table(
        matches[0],
        predictions_dataset(symbol),
        registry_root=Path(registry_root),
        lake_root=Path(lake_root),
    )
    return matches[0], stored.table


# --------------------------------------------------------------------------
# Small readers
# --------------------------------------------------------------------------


def _segment(manifest: Mapping[str, Any], name: str) -> dict:
    for entry in manifest["segments"]:
        if entry["name"] == name:
            return dict(entry)
    raise SliceError(
        f"run_slice: segment manifest {manifest['manifest_id'][:12]} "
        f"(layout {manifest['layout']!r}) has no segment named {name!r} -- it "
        f"has {sorted(entry['name'] for entry in manifest['segments'])}"
    )


def _oof_block_names(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    blocks = sorted(
        (entry for entry in manifest["segments"] if entry["role"] == "oof_block"),
        key=lambda entry: entry["start_ns"],
    )
    return tuple(str(entry["name"]) for entry in blocks)


def _look_tags(
    *, code_hash: str, seed: int, model_class: str, reason: str | None
) -> dict[str, str]:
    """The tags `harness.accessor.materialize` merges into a LOOK's own run.
    `data_hash` is `"none"` on purpose: at look time nothing has been
    produced yet, and naming a manifest the look did not consume would be
    the inverse of provenance."""
    tags = {
        "code_hash": code_hash,
        "data_hash": "none",
        "seed": str(int(seed)),
        "env_hash": compute_env_hash(UV_LOCK_PATH),
        "model_class": model_class,
    }
    if reason:
        tags["respend_reason"] = reason
    return tags


def dq_preflight(
    segment_manifest: Mapping[str, Any], *, registry_root: Path, lake_root: Path
) -> tuple[list[str], list[str]]:
    """`(ack_ids, ack_sha256)` across EVERY upstream feature manifest this
    run's numbers rest on -- the DQ findings that were waived to let that data
    be built (03-CONTEXT DATA-07) -- and a `SliceError` when any day is non-ok
    with no valid, committed acknowledgement covering its findings.

    IT REFUSES, WHERE ITS PREDECESSOR ONLY COLLECTED. This used to call
    `data.store.dq_acknowledgement_ids`, which is `_dq_pause_findings` with
    the `unacknowledged` half DISCARDED: a `failed` day with no ack made it
    return an empty list rather than raise. The pause verdict was therefore
    reached, at step 9, by nothing -- the only thing that refused on DQ state
    was `features.tier.load_features`' gate 3, inside `materialize`. So this
    calls `store._enforce_dq_pause` itself, which is that same gate's own
    function, and gets the refusal and the `(ack id, sha256)` pairs from one
    call.

    THE SHA COMES BACK FROM THE GATE RATHER THAN A SECOND READ (WR-19). The
    reconstruct-the-path-from-the-id hack this replaces hashed whatever was on
    disk when step 9 ran, which is not necessarily the bytes the pause gate
    honoured. `_enforce_dq_pause` returns the sha of exactly the bytes it
    validated and proved committed, so provenance records what was honoured.

    LOOK-FREE BY CONSTRUCTION, which is why the CLI can call it as a
    pre-flight: it reads committed manifest JSON, `lake_root/dq/date=*/
    report.parquet`, acknowledgement files and `git`. It never touches
    `harness.accessor.materialize`, `harness.budget.record_look` or MLflow,
    so it cannot spend, and cannot count, an irreversible look.

    Called TWICE per `val` invocation on purpose -- once by
    `scripts.run_stage1_slice` before any materialize, once by `run_slice` at
    step 9 -- and it is the SAME function both times, so a green pre-flight
    implies a green step 9 by construction rather than by argument. `--select`
    reaches only the first of the two: `mode="select"` stops at step 4.
    """
    registry_root, lake_root = Path(registry_root), Path(lake_root)
    dataset = f"{segment_manifest['symbol']}.features"
    honoured: dict[str, str] = {}
    for manifest_id in segment_manifest["upstream_feature_manifest_ids"]:
        body = json.loads(
            manifest_path(registry_root, dataset, str(manifest_id)).read_text()
        )
        dates = ", ".join(
            sorted({str(part["date"]) for part in body["partitions"] if "date" in part})
        )
        try:
            acks = store._enforce_dq_pause(
                body, registry_root=registry_root, lake_root=lake_root
            )
        except store.DQPauseError as error:
            raise SliceError(
                f"dq_preflight: upstream feature manifest {manifest_id} of "
                f"{dataset} (date(s) {dates or 'none declared'}) is DQ-paused "
                f"-- {error} Until that is committed this slice cannot read "
                "the day, so no look is worth spending on it."
            ) from error
        for ack_id, sha in acks:
            previous = honoured.get(ack_id)
            if previous is not None and previous != sha:
                raise SliceError(
                    f"dq_preflight: acknowledgement {ack_id} hashed to "
                    f"{previous[:12]} for one upstream manifest and "
                    f"{sha[:12]} for another -- the file changed mid-run, so "
                    "neither sha is the provenance of both reads"
                )
            honoured[ack_id] = sha
    ids = sorted(honoured)
    return ids, [honoured[ack_id] for ack_id in ids]


# --------------------------------------------------------------------------
# The slice
# --------------------------------------------------------------------------


def run_slice(
    segment_manifest_id: str,
    *,
    mode: str,
    code_hash: str,
    registry_root: Path,
    lake_root: Path,
    tracking_root: str,
    cache_root: Path,
    seed: int,
    grid: Sequence[GridEntry] = GRID,
    respend_reason: str | None = None,
) -> SliceResult:
    """Run the slice in `mode`, with every root a parameter and `code_hash`
    injected.

    EVERY ROOT IS A PARAMETER WITH NO DEFAULT, for `models.cache`'s reason
    exactly: pre-commit hooks 18/19 run the full suite on every commit, so a
    canonical default anywhere here would spend irreversible looks once per
    commit. `scripts/run_stage1_slice.py` resolves the canonical roots inside
    its own `main()` and passes them; tests pass `tmp_path`.

    `respend_reason`, when given, is tagged on the `val` look's own run. It
    exists for the one recoverable state nothing else can name -- a crash
    between `record_look` and the cache write, which spends a look and leaves
    no frame -- and the CLI is where the refusals for it live.

    Returns a `SliceResult`. Raises `SliceError` for a protocol violation and
    `models.gates.CeilingExceededError` for a P&L at the perfect-foresight
    ceiling, which is an investigation halt rather than a verdict and
    therefore opens no run.
    """
    if mode not in MODES:
        raise SliceError(f"run_slice: mode {mode!r} is not one of {list(MODES)}")
    code_hash = _require_clean_code_hash(code_hash)
    registry_root, lake_root, cache_root = (
        Path(registry_root),
        Path(lake_root),
        Path(cache_root),
    )
    tracking_root = str(tracking_root)

    manifest = read_segment_manifest(registry_root, segment_manifest_id)
    symbol = manifest["symbol"]
    block_names = _oof_block_names(manifest)
    feature_names = validate_feature_names(MODEL_FEATURE_NAMES)
    context = FitContext(
        symbol=symbol,
        lake_root=lake_root,
        registry_root=registry_root,
        code_hash=code_hash,
    )

    # ---- STEP 1: the train frame. Role `train`, so NOT a look. ----------
    _require_no_active_run("step 1 (materialize train)")
    train = materialize_once(
        segment_manifest_id,
        "train",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=tracking_root,
        run_tags=_look_tags(
            code_hash=code_hash,
            seed=seed,
            model_class="none (train frame, no model yet)",
            reason=None,
        ),
        cache_root=cache_root,
    )
    train_cache_path = segment_cache_path(
        cache_root, tracking_root, segment_manifest_id, "train"
    )

    # ---- STEP 2: normalisation, reused before written -------------------
    normalization_manifest_id, train_end_date, reused, source_ids = (
        _resolve_normalization(
            train,
            manifest=manifest,
            registry_root=registry_root,
            lake_root=lake_root,
            code_hash=code_hash,
            fit_allowed=(mode == MODE_SELECT),
        )
    )
    base = SliceResult(
        mode=mode,
        segment_manifest_id=segment_manifest_id,
        code_hash=code_hash,
        normalization_manifest_id=normalization_manifest_id,
        train_end_date=train_end_date,
        train_rows=train.height,
        source_feature_manifest_ids=source_ids,
        normalization_reused=reused,
        block_names=block_names,
    )
    train_height = train.height
    del train

    if mode == MODE_SELECT:
        # ---- STEPS 3 + 4: five looks and the sweep ----------------------
        # Fused inside `run_oof_sweep` on purpose (07-08's measured
        # block-outer loop): each block is materialized once and all configs
        # are fitted against it while the train cache is warm.
        _require_no_active_run("steps 3-4 (run_oof_sweep)")
        sweep = run_oof_sweep(
            segment_manifest_id,
            oof_block_names=block_names,
            train_cache_path=train_cache_path,
            registry_root=registry_root,
            lake_root=lake_root,
            tracking_root=tracking_root,
            cache_root=cache_root,
            run_tags=_look_tags(
                code_hash=code_hash,
                seed=seed,
                model_class="none (set per config by the sweep)",
                reason=None,
            ),
            normalization_manifest_id=normalization_manifest_id,
            symbol=symbol,
            code_hash=code_hash,
            seed=seed,
            grid=grid,
        )
        _require_no_active_run("after the sweep")
        winner = sweep.winner
        return _replace(
            base,
            winner_grid_index=winner.grid_index,
            winner_model_class=winner.model_class,
            winner_hyperparameters=dict(winner.hyperparameters),
            eligible_count=sweep.eligible_count,
            n_configs=sweep.n_configs,
            selection_path=sweep.selection_path,
        )

    selection = read_selection(
        cache_root,
        tracking_root,
        segment_manifest_id,
        normalization_manifest_id=normalization_manifest_id,
    )

    if mode == MODE_FREEZE:
        # ---- STEP 5: refit the winner on the FULL train frame -----------
        trainer = selection_winner_trainer(selection, context=context, grid=grid)
        predictor = trainer.fit(
            FitInputs(
                cache_path=train_cache_path,
                feature_names=feature_names,
                target_name=TARGET_NAME,
                row_mask_path=None,
                normalization_manifest_id=normalization_manifest_id,
                seed=seed,
            )
        )
        body = write_frozen_predictor(predictor, registry_root=registry_root)
        logger.info(
            "run_slice: froze %s %s as predictor manifest %s "
            "(predictor_id %s) over %d rows",
            predictor.model_class,
            dict(predictor.hyperparameters),
            body["manifest_id"][:12],
            body["predictor_id"][:12],
            predictor.n_rows_fitted,
        )
        return _replace(
            base,
            winner_grid_index=selection["winner_grid_index"],
            winner_model_class=predictor.model_class,
            winner_hyperparameters=dict(predictor.hyperparameters),
            n_configs=selection["n_configs"],
            predictor_manifest_id=body["manifest_id"],
            predictor_id=body["predictor_id"],
            frozen_body=body,
        )

    # ======================= mode == MODE_VAL ===========================
    stored_winner = selection.get("winner") or {}
    if not stored_winner:
        raise SliceError(
            f"run_slice: selection.json for manifest "
            f"{segment_manifest_id[:12]} records no winner -- nothing was "
            "eligible, and the runner-up in a field where nothing passed has "
            "not earned the val look"
        )
    predictor_manifest_id, frozen_body = find_frozen_body(
        registry_root=registry_root,
        model_class=str(stored_winner["model_class"]),
        hyperparameters=stored_winner["hyperparameters"] or {},
        seed=seed,
        normalization_manifest_id=normalization_manifest_id,
    )
    # Read it back through the real reader, so both hashes, the re-derived
    # `predictor_id` and the design/coefficient-count agreement are all
    # verified before a look is spent on it.
    predictor = read_frozen_predictor(
        predictor_manifest_id, registry_root=registry_root
    )
    # THE TAG COMES FROM THE BODY, NEVER FROM A RECOMPUTATION. Step 9 runs
    # at a later commit than step 5, so `predictor_id` recomputed here under
    # today's `code_hash` would silently disagree with the artifact.
    stored_predictor_id = str(frozen_body["predictor_id"])
    if predictor.predictor_id != stored_predictor_id:
        raise SliceError(
            f"run_slice: frozen body {predictor_manifest_id[:12]} stores "
            f"predictor_id {stored_predictor_id[:12]} but re-derives "
            f"{predictor.predictor_id[:12]} -- read_frozen_predictor should "
            "have refused this already, so the two checks disagree"
        )

    # ---- STEP 6: THE ONE HONEST LOOK -----------------------------------
    _require_no_active_run("step 6 (materialize val)")
    val = materialize_once(
        segment_manifest_id,
        VAL_SEGMENT_NAME,
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=tracking_root,
        run_tags=_look_tags(
            code_hash=code_hash,
            seed=seed,
            model_class=predictor.model_class,
            reason=respend_reason,
        ),
        cache_root=cache_root,
    )

    # ---- STEP 7: predict, store POSITIONALLY, reuse before writing -----
    artifact = load_normalization(
        normalization_manifest_id,
        normalization_dataset(symbol),
        registry_root=registry_root,
        lake_root=lake_root,
    )
    features = np.column_stack(
        [
            apply_normalization(
                np.asarray(
                    val[name].fill_null(float("nan")).to_numpy(), dtype=np.float64
                ),
                artifact.params[name],
            )
            for name in feature_names
        ]
    )
    pred_return = np.asarray(predictor.predict(features), dtype=np.float64)
    reuse = _existing_prediction_table(
        lake_root=lake_root,
        registry_root=registry_root,
        symbol=symbol,
        segment_manifest_id=segment_manifest_id,
        segment_name=VAL_SEGMENT_NAME,
        predictor_id=stored_predictor_id,
    )
    if reuse is not None:
        prediction_table_manifest_id, table = reuse
        assert_table_aligned(val, table)
        table_reused = True
        logger.info(
            "run_slice: reusing prediction table %s -- step 7 was already "
            "complete, so this is a no-op rather than a FileExistsError",
            prediction_table_manifest_id[:12],
        )
    else:
        table = pl.DataFrame(
            {
                "etime": val["etime"],
                "decision_seq": val["decision_seq"],
                "pred": pl.Series("pred", pred_return),
            },
            schema=dict(PREDICTION_TABLE_SCHEMA),
        )
        issued = write_prediction_table(
            table,
            symbol=symbol,
            segment_manifest_id=segment_manifest_id,
            segment_name=VAL_SEGMENT_NAME,
            predictor_id=stored_predictor_id,
            lake_root=lake_root,
            registry_root=registry_root,
            code_hash=code_hash,
        )
        prediction_table_manifest_id = issued["manifest_id"]
        table_reused = False
        reread = load_prediction_table(
            prediction_table_manifest_id,
            predictions_dataset(symbol),
            registry_root=registry_root,
            lake_root=lake_root,
        )
        assert_table_aligned(val, reread.table)
        table = reread.table

    # ---- STEP 8: ceiling, simulator, gates, guard -----------------------
    # The ceiling comes from the CACHED val frame. A second `materialize`
    # here would spend a look for a number that is a property of the data.
    ceiling = perfect_foresight_ceiling(val, label_column=TARGET_NAME)
    stored_pred = np.asarray(table["pred"].to_numpy(), dtype=np.float64)
    mid = np.ascontiguousarray(
        np.asarray(val["mid"].to_numpy(), dtype=np.float64), dtype=np.float64
    )
    pred_price, n_pred_missing = neutral_fill_null_predictions(stored_pred, mid)
    arrays = sim_arrays(val)
    sim_result = run_sim_checked(
        arrays["etime"],
        arrays["bid_ticks"],
        arrays["ask_ticks"],
        pred_price,
        x_bps=0,
    )
    target = np.asarray(
        val[TARGET_NAME].fill_null(float("nan")).to_numpy(), dtype=np.float64
    )
    metrics = dict(
        forecast_metrics(stored_pred, target, train_mean=predictor.train_target_mean)
    )
    metrics["n_pred_missing"] = float(n_pred_missing)
    metrics["n_admitted"] = float(val.height)
    for name in DIAGNOSTIC_TARGET_NAMES:
        # D-07-10: reported alongside, never fitted. One `pred` array against
        # the other three horizons -- no extra table, no extra bytes.
        #
        # A DIAGNOSTIC HORIZON CAN HAVE NO SCORABLE ROW AT ALL, and that must
        # not stop the run. `forecast_metrics` refuses a zero-row score (a
        # correct refusal: an empty score is not a score), and a segment
        # SHORTER than a horizon is entirely inside that label's trailing
        # null tail -- measured on a 599-row val segment, where every row is
        # in `ret_10min_mid`'s 600-row tail. Raising there would kill a run
        # over a number the phase explicitly never fits on. So the scorable
        # count is published ALWAYS and the two R-squareds only when there is
        # something to compute them from: absent, never fabricated.
        horizon = np.asarray(
            val[name].fill_null(float("nan")).to_numpy(), dtype=np.float64
        )
        scorable = int((np.isfinite(stored_pred) & np.isfinite(horizon)).sum())
        metrics[f"n_scorable_{name}"] = float(scorable)
        if scorable == 0:
            logger.warning(
                "run_slice: diagnostic horizon %s has no row with both a "
                "finite prediction and a finite target on this %d-row "
                "segment -- reporting n_scorable_%s=0 and no R-squared, "
                "rather than raising over a horizon this phase never fits on",
                name,
                val.height,
                name,
            )
            continue
        diagnostic = forecast_metrics(
            stored_pred, horizon, train_mean=predictor.train_target_mean
        )
        metrics[f"r2_vs_zero_{name}"] = float(diagnostic["r2_vs_zero"])
        metrics[f"r2_vs_mean_{name}"] = float(diagnostic["r2_vs_mean"])
    forecast_passed, forecast_reason = gate_forecast(metrics)
    monetization_passed, monetization_reason = gate_monetization(sim_result)
    pnl_ticks = closed_pnl_ticks(sim_result)
    metrics.update(
        {
            "sim_trades": float(sim_result.fill_count),
            "sim_flips": float(sim_result.counters["flips"]),
            "sim_rows_in_market": float(sim_result.counters["rows_in_market"]),
            "sim_closed_pnl_ticks": float(pnl_ticks),
            "sim_pnl_usd": ticks_to_usd_at_traded_lot(pnl_ticks),
            "ceiling_trades": float(ceiling["trades"]),
            "ceiling_closed_pnl_ticks": float(ceiling["closed_pnl_ticks"]),
            "ceiling_pnl_usd": float(ceiling["closed_pnl_usd_at_traded_lot"]),
            "pnl_fraction_of_ceiling": (
                float(pnl_ticks) / float(ceiling["closed_pnl_ticks"])
                if int(ceiling["closed_pnl_ticks"]) != 0
                else float("nan")
            ),
            "forecast_gate_passed": float(forecast_passed),
            "monetization_gate_passed": float(monetization_passed),
        }
    )
    # NOT a gate: a P&L at the ceiling is an investigation halt, so it raises
    # and opens no run.
    guard_against_ceiling(pnl_ticks, int(ceiling["closed_pnl_ticks"]), VAL_SEGMENT_NAME)

    # A failing gate is recorded BEFORE run 3 opens, because runs cannot
    # nest (D-07-30). One record, on the frozen body's own recipe, so it is
    # queryable by the very predictor_id the body carries.
    negative_run_id: str | None = None
    if not (forecast_passed and monetization_passed):
        recipe = {field: frozen_body[field] for field in RECIPE_FIELDS}
        _require_no_active_run("the val negative-result record")
        negative_log.warn_if_already_negative(recipe, tracking_root=tracking_root)
        negative_run_id = negative_log.record_negative_result(
            recipe,
            reason=(
                f"val gates: forecast={'pass' if forecast_passed else 'FAIL'} "
                f"({forecast_reason}); monetization="
                f"{'pass' if monetization_passed else 'FAIL'} "
                f"({monetization_reason})"
            )[:4_000],
            tracking_root=tracking_root,
            run_tags={
                "code_hash": code_hash,
                "data_hash": prediction_table_manifest_id,
                "seed": str(int(seed)),
                "env_hash": compute_env_hash(UV_LOCK_PATH),
                "model_class": predictor.model_class,
                "fold_config": manifest["layout"],
                "segment_manifest_id": segment_manifest_id,
                SCORED_SEGMENT_TAG_KEY: VAL_SEGMENT_NAME,
            },
        )

    # ---- STEP 9: LAST, the slice's own run -----------------------------
    _require_no_active_run("step 9 (start_tracked_run)")
    observed = look_run_ids(
        segment_manifest_id,
        [*block_names, VAL_SEGMENT_NAME],
        tracking_root=tracking_root,
    )
    recorded = {
        name: list(ids) for name, ids in (selection.get("look_run_ids") or {}).items()
    }
    for name, ids in recorded.items():
        missing = sorted(set(ids) - set(observed.get(name, ())))
        if missing:
            raise SliceError(
                f"run_slice: selection.json recorded look run(s) {missing} "
                f"for {name} but the live store at {tracking_root} does not "
                "have them -- the tracking store was moved or pruned, and a "
                "run manifest citing ids that resolve to nothing is not "
                "provenance"
            )
    for name, ids in observed.items():
        if not ids:
            raise SliceError(
                f"run_slice: segment {name!r} has no look run in "
                f"{tracking_root} -- run 3 would cite an unpaid-for segment"
            )
    flat = sorted({run_id for ids in observed.values() for run_id in ids})

    # The SAME call `scripts.run_stage1_slice` makes before any materialize.
    # Reaching it here is expected to be a formality; it is kept because a
    # caller that is not that CLI has no pre-flight at all.
    dq_ack_ids, dq_ack_sha256 = dq_preflight(
        manifest, registry_root=registry_root, lake_root=lake_root
    )
    tags = {
        "code_hash": code_hash,
        # THE PREDICTION-TABLE MANIFEST: what this run consumed and produced,
        # and the id that chains back through `inputs` to `features_norm` and
        # to the seven feature manifests.
        "data_hash": prediction_table_manifest_id,
        "seed": str(int(seed)),
        "env_hash": compute_env_hash(UV_LOCK_PATH),
        "segment_manifest_id": segment_manifest_id,
        "model_class": predictor.model_class,
        "fold_config": manifest["layout"],
        "stage": SLICE_STAGE_TAG,
    }
    run = start_tracked_run(
        tracking_root, tags, SLICE_EXPERIMENT_NAME, dq_ack_ids=dq_ack_ids
    )
    try:
        run_id = run.info.run_id
        additive = {
            "predictor_id": stored_predictor_id,
            "predictor_manifest_id": predictor_manifest_id,
            "normalization_manifest_id": normalization_manifest_id,
            "prediction_table_manifest_id": prediction_table_manifest_id,
            "fold_config_reason": str(manifest.get("fold_config_reason") or "none"),
            SCORED_SEGMENT_TAG_KEY: VAL_SEGMENT_NAME,
            "train_end_date": train_end_date,
            "forecast_gate_reason": forecast_reason[:4_000],
            "monetization_gate_reason": monetization_reason[:4_000],
        }
        if negative_run_id:
            additive["negative_run_id"] = negative_run_id
        if respend_reason:
            additive["respend_reason"] = respend_reason
        for index, shard in enumerate(
            _shard_values(flat, MAX_PROVENANCE_TAG_VALUE_CHARS)
        ):
            additive[_shard_key("look_run_ids", index)] = shard or "none"
        mlflow.set_tags(additive)
        mlflow.log_params(
            {
                "model_class": predictor.model_class,
                "n_configs": selection["n_configs"],
                "winner_grid_index": selection["winner_grid_index"],
                "feature_names": ",".join(feature_names),
                "target_name": TARGET_NAME,
                "train_rows": train_height,
                "val_rows": val.height,
                **{
                    f"hp_{key}": value
                    for key, value in (stored_winner["hyperparameters"] or {}).items()
                },
            }
        )
        mlflow.log_metrics({key: float(value) for key, value in metrics.items()})
        log_data_provenance(
            manifest_ids=[
                prediction_table_manifest_id,
                normalization_manifest_id,
                segment_manifest_id,
                predictor_manifest_id,
                *[str(v) for v in manifest["upstream_feature_manifest_ids"]],
            ],
            dq_ack_ids=dq_ack_ids,
            dq_ack_sha256=dq_ack_sha256,
        )
    finally:
        mlflow.end_run()
    logger.info(
        "run_slice: stage-1-regression run %s -- %d trades, %d closed ticks "
        "($%+.6f), ceiling %d ticks, forecast_gate=%s monetization_gate=%s",
        run_id,
        int(sim_result.fill_count),
        pnl_ticks,
        ticks_to_usd_at_traded_lot(pnl_ticks),
        int(ceiling["closed_pnl_ticks"]),
        forecast_passed,
        monetization_passed,
    )
    return _replace(
        base,
        winner_grid_index=selection["winner_grid_index"],
        winner_model_class=predictor.model_class,
        winner_hyperparameters=dict(predictor.hyperparameters),
        n_configs=selection["n_configs"],
        predictor_manifest_id=predictor_manifest_id,
        predictor_id=stored_predictor_id,
        frozen_body=frozen_body,
        prediction_table_manifest_id=prediction_table_manifest_id,
        prediction_table_reused=table_reused,
        run_id=run_id,
        metrics=metrics,
        forecast_gate_passed=forecast_passed,
        forecast_gate_reason=forecast_reason,
        monetization_gate_passed=monetization_passed,
        monetization_gate_reason=monetization_reason,
        ceiling=ceiling,
        observed_look_run_ids={name: tuple(ids) for name, ids in observed.items()},
        negative_run_id=negative_run_id,
    )


def _replace(base: SliceResult, **changes: Any) -> SliceResult:
    """`dataclasses.replace` under a local name, so it reads at the call site
    as "the same result, plus what this mode learned"."""
    return replace(base, **changes)
