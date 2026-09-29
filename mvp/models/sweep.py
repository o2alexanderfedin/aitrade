"""The 36-config x 5-block out-of-fold sweep: the winner is picked on data
that is not `val`, by a rule written down here rather than by a human
reading a table.

THE SELECTION RULE, IN FULL, because 07-CONTEXT.md leaves the criterion to
Claude's discretion and Phase 8's cross-class protocol must INHERIT this
rather than reinvent it:

1. A config is ELIGIBLE only if `models.gates.gate_forecast` passes on ALL
   FIVE blocks. One bad block disqualifies. A mean that hides a failing
   fold is exactly how an unstable model wins -- measured on this project's
   own fixture, an ElasticNet at `alpha=1e-6, l1_ratio=0.5` carries a
   positive rank IC on every block and still posts `r2_vs_zero = -0.0073`
   on one of them; averaged, that block disappears.
2. Among eligible configs, rank by the MEAN of `rank_ic_non_tied` across
   the five blocks. Eligibility guarantees that mean is finite and
   positive: the gate refuses a non-finite IC outright, so there is no NaN
   to nan-ignore here and no `nanmean` anywhere in this module.
3. Tie-break on the mean `r2_vs_mean`, then -- if that ties too -- on the
   config's POSITION IN THE GRID, ascending. The third key is what makes
   the winner reproducible rather than dependent on dict ordering, and it
   matters in practice: the four Ridge configs on the fixture sit within
   3e-5 of each other.
4. The winner's per-block IC SPREAD (min, max, standard deviation) is
   reported beside its mean. FCST-05 wants exactly this shape, and the
   approved manifest's one-calendar-day blocks make it directly readable as
   a per-day breakdown.
5. NO ELIGIBLE CONFIG is an explicit, first-class outcome --
   `winner_grid_index is None`, and `SweepResult.winner` raises
   `NoEligibleConfigError`. It is a real finding about the data or the
   grid, not an error to paper over, and no caller may fall back to "best
   available": the runner-up in a field where nothing passed has not earned
   the one honest `val` look.

STRUCTURAL CONFINEMENT TO THE OOF BLOCKS (D-07-04). `run_oof_sweep` has NO
PARAMETER THROUGH WHICH `val` COULD BE NAMED. It takes the already-cached
TRAIN frame by path and a list of block names, and refuses any name that is
not `oof_block_<int>`. So D-07-04 is a property of the signature rather
than of the caller's discipline: a caller who wants to score `val` cannot
express it here, and `models.cache.materialize_once` -- the only door to a
look -- is never reached for any other segment from inside this module.

WHY `selection.json` EXISTS AT ALL, AND WHY IT IS THE MOST IMPORTANT FILE
THIS MODULE WRITES. `--select` and `--freeze` are SEPARATE PROCESSES with a
commit between them (plan 07-10). `SweepResult` is in memory and nothing in
memory survives that gap, so without a durable artifact the executor of
`--freeze` would discover the problem AFTER five irreversible looks were
spent, facing a choice between improvising a format and re-running 85 fits
over ~40M rows. `selection.json` carries the winner, BOTH manifest ids,
every config's per-block metrics, the eligible count, `n_configs`, and the
five OOF `look_run_ids` -- those last because `budget.record_look` returns
a run id that `materialize` throws away, and the run that must cite them
opens later, elsewhere.

IT IS KEYED BY TRACKING ROOT, for `models.cache`'s reason exactly: a
selection made against a scratch store must never be mistaken for one made
against the real store. It lives in the same tracking-root-keyed directory
as the frame cache, and is written with the same `_atomic_write_json`
(copied below, per the repo's cross-module private-helper norm) because a
half-written one is the worst possible outcome for a file that carries five
looks' worth of work.

THE WRITE ORDER, AND THE CRASH WINDOW IT LEAVES. `selection.json` is
written LAST, after every look is recorded and every negative result is
logged. A crash in between therefore leaves the looks SPENT, the five frame
caches PRESENT, and no `selection.json` -- and the re-run costs NO look,
because every cache hits. That is the designed behaviour, not a lucky
accident. The window that does cost something is one level down, inside
`models.cache.materialize_once`: a crash between `record_look` and the
Parquet write spends a look with no cache to show for it, and
`budget_allowance = 3` (D-07-03) absorbs one of those per segment.

NO OOF PREDICTION TABLE IS STORED (D-07-15). Metrics are computed streaming
from the block frame and only the scalars survive; four estimators' tables
on every segment would approach a gigabyte for no downstream reader, which
is the massive-data production this phase is explicitly told to avoid.

NO MLFLOW RUN OF THIS MODULE'S OWN IS EVER OPEN (D-07-30).
`negative_log.record_negative_result` opens its own run per record and
passes no `nested=True`, and `mlflow.start_run` raises a bare `Exception`
when a run is already active. The slice's own tracked run opens LAST, in a
later plan.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from mlflow.utils.validation import MAX_TAG_VAL_LENGTH

from features.normalize import (
    apply_normalization,
    load_normalization,
    normalization_dataset,
)
from harness import negative_log
from harness.kfold import training_rows_for_block
from harness.segments import read_segment_manifest
from models.cache import (
    look_run_ids,
    materialize_once,
    segment_cache_dir,
)
from models.gates import gate_forecast
from models.metrics import forecast_metrics
from models.predictions import assert_decision_order
from models.predictor_id import RECIPE_FIELDS, predictor_id
from models.protocol import FitInputs
from models.regression import (
    GRID,
    ROW_MASK_COLUMN,
    TARGET_NAME,
    FitContext,
    GridEntry,
    validate_feature_names,
)
from models.regression import FEATURE_NAMES as MODEL_FEATURE_NAMES

__all__ = [
    "OOF_BLOCK_NAME_PATTERN",
    "SELECTION_FILENAME",
    "SELECTION_RULE",
    "SELECTION_SCHEMA_VERSION",
    "BlockScore",
    "ConfigScore",
    "NoEligibleConfigError",
    "SelectionError",
    "SweepError",
    "SweepResult",
    "config_recipe",
    "read_selection",
    "run_oof_sweep",
    "select_winner",
    "selection_path",
    "selection_winner_trainer",
    "summarise_config",
]

logger = logging.getLogger(__name__)

#: The ONLY segment-name shape this module will handle (D-07-04). `val`
#: does not match it, and neither does anything else a caller might type.
OOF_BLOCK_NAME_PATTERN = re.compile(r"^oof_block_(\d+)$")

#: The cross-process handoff artifact's filename, inside
#: `models.cache.segment_cache_dir(...)`.
SELECTION_FILENAME: str = "selection.json"

#: Bumped when the body's keys change. Asserted on every read, so a
#: `selection.json` from an older shape cannot be read as a newer one --
#: `models.predictions.PREDICTIONS_SCHEMA_VERSION`'s register.
SELECTION_SCHEMA_VERSION: int = 1

#: The rule, as one line stored IN the artifact, so a human reading a
#: `selection.json` a month later does not have to find this module to
#: learn how its winner was chosen. The full statement is this module's
#: docstring.
SELECTION_RULE: str = (
    "eligible = gate_forecast passes on ALL blocks; rank by mean "
    "rank_ic_non_tied; tie-break on mean r2_vs_mean, then on ascending "
    "grid position"
)

#: How much of a gate's own message survives into a negative result's
#: `reason` tag. `record_negative_result` REFUSES an over-long reason
#: (MLflow truncates rather than raising, so a silently cut-short reason
#: would read as complete), which makes trimming this module's job.
_REASON_BUDGET: int = MAX_TAG_VAL_LENGTH - 256


class SweepError(ValueError):
    """A sweep protocol violation: a segment name that is not an OOF block,
    a train cache from another manifest's directory, a block list that
    disagrees with the manifest, or a row mask whose height does not match
    the rows the fold geometry named.

    A dedicated class for `models.frozen.FrozenPredictorError`'s stated
    reason: a `pytest.raises(ValueError)` meaning "the sweep refused to look
    at val" would otherwise also be satisfied by "sklearn refused an empty
    design matrix".
    """


class NoEligibleConfigError(SweepError):
    """No config passed the forecast gate on every block.

    A distinct class because this is a RESULT, not a malfunction, and the
    one thing a caller must not do about it is fall back to the best
    available config -- so the failure has to be impossible to confuse with
    "something broke".
    """


class SelectionError(SweepError):
    """`selection.json` is absent, of another schema version, or disagrees
    with the live manifests / the live grid it is being applied to."""


def _atomic_write_json(path: Path, body: dict) -> None:
    """Duplicated from `data.store._atomic_write_json` (underscore-private,
    crossed by copying its five lines rather than importing, per the
    project's own stated norm -- `models/cache.py`, `models/frozen.py`,
    `harness/segments.py` and `data/lockbox.py` all do the same).

    This is the SAME helper `models.cache` uses for the provenance sidecars
    that sit beside `selection.json` in that directory, and it is used here
    for a stronger reason than tidiness: this file carries five irreversible
    looks' worth of work across a commit boundary, and a half-written one
    would be worse than an absent one -- absent is a clean refusal, truncated
    is a plausible-looking winner.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(body, sort_keys=True, indent=2))
    tmp_path.replace(path)


def _jsonable(value: float | int) -> float | int | None:
    """A metric as JSON, with a non-finite one stored as `null` and an
    integer left an integer.

    JSON has no `NaN` literal. Python's `json.dumps` emits a bare `NaN`
    anyway (`allow_nan=True` by default), which no strict parser and no
    other language's reader accepts -- and this file is read by a later
    process at a later commit, which is precisely when that bites.

    NOTHING IS LOST BY IT. A non-finite metric only ever belongs to an
    INELIGIBLE config: `gate_forecast` refuses a non-finite
    `rank_ic_non_tied` as its first check, so every number on the winner's
    row is finite and round-trips exactly. `n_scorable` is an `int` from
    `forecast_metrics` and stays one, rather than arriving in the file as
    `1200.0` -- a row count is not a measurement.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    number = float(value)
    return number if np.isfinite(number) else None


@dataclass(frozen=True)
class BlockScore:
    """One config scored on one OOF block: the metrics, the gate's verdict
    in its own words, and the fit's own row accounting.

    `n_rows_fitted`/`n_rows_dropped` are reported and are deliberately NOT
    part of the config's hashed recipe -- see `config_recipe`.
    """

    segment_name: str
    metrics: Mapping[str, float]
    gate_passed: bool
    gate_reason: str
    n_rows_fitted: int
    n_rows_dropped: int
    fit_seconds: float


@dataclass(frozen=True)
class ConfigScore:
    """One grid config across every block, with eligibility and the ranking
    statistics already reduced. Built by `summarise_config`, which is where
    the all-blocks rule lives."""

    grid_index: int
    model_class: str
    hyperparameters: Mapping[str, Any]
    config_fingerprint: str
    blocks: tuple[BlockScore, ...]
    eligible: bool
    mean_rank_ic_non_tied: float
    mean_r2_vs_mean: float
    ic_min: float
    ic_max: float
    ic_std: float
    fit_seconds_total: float
    negative_run_id: str | None = None

    def first_failing_block(self) -> BlockScore | None:
        """The first block whose gate refused, in block order -- what a
        negative result's `reason` names."""
        for block in self.blocks:
            if not block.gate_passed:
                return block
        return None


@dataclass(frozen=True)
class SweepResult:
    """Everything the sweep learned, plus where it wrote it down."""

    segment_manifest_id: str
    normalization_manifest_id: str
    block_names: tuple[str, ...]
    n_configs: int
    configs: tuple[ConfigScore, ...]
    eligible_grid_indices: tuple[int, ...]
    winner_grid_index: int | None
    look_run_ids: Mapping[str, tuple[str, ...]]
    selection_path: Path

    @property
    def has_winner(self) -> bool:
        return self.winner_grid_index is not None

    @property
    def eligible_count(self) -> int:
        return len(self.eligible_grid_indices)

    @property
    def winner(self) -> ConfigScore:
        """The winning `ConfigScore`, or `NoEligibleConfigError`.

        Raising is the whole design: a property that returned the
        highest-mean-IC config regardless of eligibility would let a caller
        spend the phase's one honest `val` look on a model that failed a
        gate, and would read exactly like the eligible case at the call
        site.
        """
        if self.winner_grid_index is None:
            raise NoEligibleConfigError(
                f"run_oof_sweep: none of the {self.n_configs} configs passed "
                f"the forecast gate on all {len(self.block_names)} OOF blocks "
                f"({', '.join(self.block_names)}). This is a RESULT, not a "
                "malfunction: the honest next steps are to widen the grid, "
                "revisit the features, or accept that this segment carries no "
                "learnable signal -- never to promote the best of the failures "
                "to the val look."
            )
        return self.configs[
            [config.grid_index for config in self.configs].index(self.winner_grid_index)
        ]


def config_recipe(
    *,
    model_class: str,
    hyperparameters: Mapping[str, Any],
    seed: int,
    code_hash: str,
    normalization_manifest_id: str,
) -> tuple[dict[str, Any], str]:
    """`(recipe, fingerprint)` for one config: exactly `RECIPE_FIELDS` and
    nothing else, plus `negative_log.config_fingerprint` over it.

    THE FIT'S ROW COUNTS ARE NOT IN HERE, AND THAT IS THE POINT. Every
    block fits the same config on a different training subset, so
    `n_rows_fitted`/`n_rows_dropped` differ per block; folding either into
    the hashed body would give one config five fingerprints, and the
    negative log's deduplication -- `warn_if_already_negative`, which is
    keyed on the fingerprint -- would stop deduplicating anything.
    Likewise the coefficients: the recipe says WHAT WAS TRIED, and a
    config that was tried five times was tried once.

    The fingerprint is asserted equal to `models.predictor_id.predictor_id`
    over the same fields. Both go through `data.store.compute_manifest_id`,
    so the equality holds by construction -- and asserting it is what makes
    a negative-result record queryable by the very id the frozen predictor
    will carry if a later run of the same config succeeds.
    """
    recipe = {
        "model_class": model_class,
        "hyperparameters": dict(hyperparameters),
        "seed": int(seed),
        "code_hash": code_hash,
        "normalization_manifest_id": normalization_manifest_id,
    }
    if set(recipe) != set(RECIPE_FIELDS):
        raise SweepError(
            f"config_recipe: built {sorted(recipe)} but D-07-14's recipe is "
            f"{sorted(RECIPE_FIELDS)} -- the negative log and the predictor "
            "id would then key on different bodies"
        )
    fingerprint = negative_log.config_fingerprint(recipe)
    expected = predictor_id(**recipe)  # type: ignore[arg-type]
    if fingerprint != expected:
        raise SweepError(
            f"config_recipe: config_fingerprint {fingerprint[:12]} does not "
            f"equal predictor_id {expected[:12]} over the same recipe -- both "
            "are data.store.compute_manifest_id, so a disagreement means one "
            "of them canonicalises differently and a negative result could "
            "never be matched back to the predictor it refused"
        )
    return recipe, fingerprint


def summarise_config(
    *,
    grid_index: int,
    model_class: str,
    hyperparameters: Mapping[str, Any],
    config_fingerprint: str,
    blocks: Sequence[BlockScore],
) -> ConfigScore:
    """Reduce one config's per-block scores to eligibility plus the ranking
    statistics. PURE: no MLflow, no lake, no fit -- which is what lets a
    test hand-construct five `BlockScore`s and check the rule directly.

    ELIGIBILITY IS `all(...)`, ACROSS EVERY BLOCK. That single word is rule
    1 of this module's docstring, and swapping it for `any` is the mutation
    `tests/models/test_sweep.py` exists to catch: a config that fails one
    block while carrying a healthy mean would become eligible, and an
    unstable model would be allowed to compete.

    The means are plain `np.mean`, never `nanmean`. For an eligible config
    every block's IC is finite by the gate's own first check; for an
    ineligible one a NaN mean is the honest summary and is reported as
    `null` in `selection.json`.
    """
    if not blocks:
        raise SweepError(
            f"summarise_config: config {grid_index} has no block scores -- an "
            "eligibility verdict over zero blocks would be vacuously True"
        )
    eligible = all(block.gate_passed for block in blocks)
    ics = np.array(
        [float(block.metrics["rank_ic_non_tied"]) for block in blocks],
        dtype=np.float64,
    )
    r2s = np.array(
        [float(block.metrics["r2_vs_mean"]) for block in blocks], dtype=np.float64
    )
    return ConfigScore(
        grid_index=int(grid_index),
        model_class=model_class,
        hyperparameters=dict(hyperparameters),
        config_fingerprint=config_fingerprint,
        blocks=tuple(blocks),
        eligible=eligible,
        mean_rank_ic_non_tied=float(ics.mean()),
        mean_r2_vs_mean=float(r2s.mean()),
        ic_min=float(ics.min()),
        ic_max=float(ics.max()),
        ic_std=float(ics.std(ddof=0)),
        fit_seconds_total=float(sum(block.fit_seconds for block in blocks)),
    )


def select_winner(
    configs: Sequence[ConfigScore],
) -> tuple[int | None, tuple[int, ...]]:
    """`(winner_grid_index, eligible_grid_indices)` -- rules 2, 3 and 5.
    PURE, for `summarise_config`'s reason.

    `max` over `(mean IC, mean r2_vs_mean, -grid_index)` implements the
    three keys in order: the negated index makes the EARLIEST grid position
    win a remaining tie, which is the reproducible choice and the one a
    reader can predict from the grid literal alone.
    """
    eligible = tuple(config.grid_index for config in configs if config.eligible)
    if not eligible:
        return None, ()
    by_index = {config.grid_index: config for config in configs}
    winner = max(
        eligible,
        key=lambda index: (
            by_index[index].mean_rank_ic_non_tied,
            by_index[index].mean_r2_vs_mean,
            -index,
        ),
    )
    return winner, eligible


def selection_path(
    cache_root: Path, tracking_root: str | Path, segment_manifest_id: str
) -> Path:
    """`models.cache.segment_cache_dir(...) / SELECTION_FILENAME`.

    The same tracking-root-keyed directory the frame cache uses, for the
    same reason: a selection reached against a scratch store must not be
    readable as one reached against the real store. `cache_root` is
    REQUIRED and never defaulted -- `models.cache`'s own rationale, and it
    applies with more force here, because this file is what a later process
    TRUSTS.
    """
    return (
        segment_cache_dir(cache_root, tracking_root, segment_manifest_id)
        / SELECTION_FILENAME
    )


def read_selection(
    cache_root: Path,
    tracking_root: str | Path,
    segment_manifest_id: str,
    *,
    normalization_manifest_id: str,
) -> dict:
    """`selection.json` as a dict, or a refusal.

    Four refusals, each with its own message because they mean different
    things:

    * ABSENT -- `--select` never ran against this tracking root (or ran
      against another one). Named as the remedy rather than as a missing
      file, because "run --select first" is the action and "no such file"
      is not.
    * SCHEMA VERSION -- an older body read as a newer one.
    * `segment_manifest_id` DISAGREES -- the path already encodes that id,
      so a disagreement means the file was hand-edited or copied between
      directories, and a selection made against another fold geometry is
      the leak this whole phase is built to avoid.
    * `normalization_manifest_id` DISAGREES -- the winner's coefficients
      were fit against one artifact's parameters and would be applied
      against another's. Every prediction would be plausible and wrong.
    """
    path = selection_path(cache_root, tracking_root, segment_manifest_id)
    if not path.exists():
        raise SelectionError(
            f"read_selection: no {SELECTION_FILENAME} at {path} -- run the "
            "OOF sweep (--select) against THIS tracking root first. The "
            "path is keyed by the tracking root on purpose: a selection made "
            "against a scratch store is not a selection made against this one."
        )
    body = json.loads(path.read_text())
    if int(body.get("schema_version", -1)) != SELECTION_SCHEMA_VERSION:
        raise SelectionError(
            f"read_selection: {path} has schema_version "
            f"{body.get('schema_version')!r}, expected "
            f"{SELECTION_SCHEMA_VERSION} -- refusing to read an older body as "
            "this one"
        )
    if body.get("segment_manifest_id") != segment_manifest_id:
        raise SelectionError(
            f"read_selection: {path} records segment_manifest_id "
            f"{str(body.get('segment_manifest_id'))[:12]} but is being applied "
            f"to {segment_manifest_id[:12]} -- the directory name already "
            "encodes that id, so this file was edited or copied, and a winner "
            "chosen on another fold geometry must not be promoted here"
        )
    if body.get("normalization_manifest_id") != normalization_manifest_id:
        raise SelectionError(
            f"read_selection: {path} records normalization_manifest_id "
            f"{str(body.get('normalization_manifest_id'))[:12]} but the live "
            f"artifact is {normalization_manifest_id[:12]} -- the winner's "
            "coefficients were fit against one set of z-score parameters and "
            "would be applied against another, which predicts plausible "
            "numbers from the wrong scale (D-07-11)"
        )
    return body


def selection_winner_trainer(
    selection: Mapping[str, Any],
    *,
    context: FitContext,
    grid: Sequence[GridEntry] = GRID,
):
    """Rebuild the winning config as a constructed trainer, cross-checking
    the stored recipe against `grid` -- or raise.

    THE GRID CROSS-CHECK IS WHY BOTH THE INDEX AND THE RECIPE ARE STORED. A
    commit sits between `--select` and `--freeze`; a `GRID` reordered in
    that commit would leave the stored index pointing at a DIFFERENT
    config, with nothing about the index itself looking wrong. So the
    trainer is built and its own `model_class` and `hyperparameters` are
    compared to the stored pair. `hyperparameters` is compared, not
    `GridEntry.knobs`: the poly2 trainer's `degree` lives in the former and
    not the latter, and `degree` is the only thing separating three of its
    configs from three plain-Ridge ones.

    `NoEligibleConfigError` when the sweep found no winner -- the same
    refusal `SweepResult.winner` makes, so the in-memory and the
    across-a-commit paths behave identically.
    """
    index = selection.get("winner_grid_index")
    if index is None:
        raise NoEligibleConfigError(
            "selection_winner_trainer: this selection records no winner "
            f"({selection.get('eligible_count')} of "
            f"{selection.get('n_configs')} configs were eligible) -- there is "
            "nothing to freeze, and the best of the failures must not be "
            "promoted to the val look"
        )
    index = int(index)
    if int(selection.get("n_configs", -1)) != len(grid):
        raise SelectionError(
            f"selection_winner_trainer: the selection was made over "
            f"{selection.get('n_configs')} configs but this grid has "
            f"{len(grid)} -- the stored index cannot be trusted to name the "
            "same config"
        )
    if not 0 <= index < len(grid):
        raise SelectionError(
            f"selection_winner_trainer: winner_grid_index {index} is outside "
            f"the {len(grid)}-entry grid"
        )
    trainer = grid[index].build(context)
    stored = selection.get("winner") or {}
    if trainer.model_class != stored.get("model_class") or dict(
        trainer.hyperparameters
    ) != dict(stored.get("hyperparameters") or {}):
        raise SelectionError(
            f"selection_winner_trainer: grid position {index} is "
            f"{trainer.model_class} {dict(trainer.hyperparameters)} but the "
            f"selection recorded {stored.get('model_class')} "
            f"{stored.get('hyperparameters')} -- the grid changed between "
            "--select and --freeze, and the stored index now names a "
            "different config than the one that won"
        )
    return trainer


def _require_oof_block_names(names: Sequence[str]) -> tuple[str, ...]:
    """Every name matches `oof_block_<int>`, or `SweepError` (D-07-04).

    This is the mechanical half of "selection never touches `val`". The
    structural half is that no parameter of `run_oof_sweep` can carry a
    segment name to `materialize_once` except this list.
    """
    if not names:
        raise SweepError(
            "run_oof_sweep: no OOF block names given -- a sweep over zero "
            "blocks would call every config eligible without scoring anything"
        )
    ordered = tuple(str(name) for name in names)
    offenders = [name for name in ordered if not OOF_BLOCK_NAME_PATTERN.fullmatch(name)]
    if offenders:
        raise SweepError(
            f"run_oof_sweep: {offenders} is not an OOF block name "
            f"(/{OOF_BLOCK_NAME_PATTERN.pattern}/). Model selection happens on "
            "the oof_block_* segments and NEVER on val (D-07-04): only the "
            "winner is ever materialized against val, and that one look is "
            "the phase's entire honest evaluation."
        )
    return ordered


def _manifest_blocks(manifest: Mapping[str, Any]) -> list[dict]:
    """The manifest's `oof_block` entries, sorted by `start_ns`, with
    `blocks[j]["name"] == f"oof_block_{j}"` ASSERTED.

    `harness.kfold.training_rows_for_block` indexes this list POSITIONALLY
    and reads `blocks[0]["start_ns"]`/`blocks[-1]["end_ns"]` as the whole
    train range. A list in another order would purge the wrong window while
    returning a perfectly plausible number of rows.
    """
    blocks = sorted(
        (dict(entry) for entry in manifest["segments"] if entry["role"] == "oof_block"),
        key=lambda entry: entry["start_ns"],
    )
    if not blocks:
        raise SweepError(
            f"run_oof_sweep: manifest {manifest['manifest_id'][:12]} "
            f"(layout {manifest['layout']!r}) has no oof_block entries -- the "
            "sweep has nothing to select on, and val is not an option"
        )
    for index, entry in enumerate(blocks):
        if entry["name"] != f"oof_block_{index}":
            raise SweepError(
                f"run_oof_sweep: sorted by start_ns, block {index} is named "
                f"{entry['name']!r} and not 'oof_block_{index}' -- "
                "harness.kfold.training_rows_for_block indexes this list "
                "positionally, so a mismatch purges the wrong window and "
                "still returns a plausible row count"
            )
    return blocks


def _write_block_row_mask(
    *,
    path: Path,
    cache_etime: np.ndarray,
    block_train: pl.DataFrame,
    segment_name: str,
) -> int:
    """The positional Boolean mask for one block's training rows, written to
    `path`; returns how many rows it keeps.

    NO JOIN, EVER. `harness.kfold.training_rows_for_block` returns a
    FILTERED FRAME rather than a mask, and the conversion is
    `np.isin(cache_etime, block_train["etime"])` -- legal precisely because
    `etime` is globally unique and strictly ascending across the pool
    (60,926,503 distinct values in 60,926,503 rows, correction C1), which
    `assert_decision_order` has already proven on this very cache. A join
    would be the alternative and polars documents its output order as
    unspecified; a silently reordered training set is this phase's TOP risk
    (T-07-17, D-07-31).

    The kept count is asserted equal to the filtered frame's own height,
    with BOTH numbers in the message: a shortfall means the mask keeps fewer
    rows than the fold geometry assigned, and a training set quietly smaller
    than the geometry says is a difference no downstream number would show.
    """
    keep = np.isin(cache_etime, block_train["etime"].to_numpy())
    kept = int(keep.sum())
    if kept != block_train.height:
        raise SweepError(
            f"run_oof_sweep: the row mask for {segment_name} keeps {kept} of "
            f"{cache_etime.size} cached rows, but "
            f"harness.kfold.training_rows_for_block named "
            f"{block_train.height} -- every training etime must be present in "
            "the cache exactly once, and a shortfall is a training set "
            "quietly smaller than the fold geometry says it is"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {ROW_MASK_COLUMN: pl.Series(ROW_MASK_COLUMN, keep, pl.Boolean)}
    ).write_parquet(path)
    return kept


def _scoring_columns(
    frame: pl.DataFrame, *, feature_names: Sequence[str], params: Mapping[str, Any]
) -> tuple[np.ndarray, np.ndarray]:
    """`(normalised_features, target)` for one block frame, as float64.

    THE NULL ACCOUNTING IS THE POINT, and it is
    `models.regression._read_columns`' exactly (copied rather than reached
    for, since that one is private and reads a `FitInputs`): a nullable
    Float64 column becomes a NaN-filled COPY at `.to_numpy()` WITHOUT
    raising. So nulls are counted first, filled with NaN explicitly, and
    cross-checked on the numpy side -- a null that arrived as a NUMBER is an
    invented observation, and here it would become an invented prediction
    scored against an invented target.

    Normalisation is applied from the FEAT-05 artifact's stored parameters
    and is never refit (D-07-11). It happens HERE, in the caller, because
    `FrozenPredictor.predict` takes ALREADY-NORMALISED features by design:
    the transform is identical for every model class, and putting it inside
    `predict` would duplicate it once per class.
    """
    names = [*feature_names, TARGET_NAME]
    arrays: dict[str, np.ndarray] = {}
    for name in names:
        column = frame[name]
        if column.dtype != pl.Float64:
            raise SweepError(
                f"run_oof_sweep: block column {name!r} has dtype "
                f"{column.dtype}, expected Float64 -- a Float32 column changes "
                "a prediction near the half-tick boundary (D-07-16)"
            )
        nulls = int(column.null_count())
        filled = column.fill_null(float("nan"))
        if int(filled.null_count()) != 0:
            raise SweepError(
                f"run_oof_sweep: block column {name!r} still holds nulls after "
                "an explicit fill_null(nan)"
            )
        values = np.asarray(filled.to_numpy(), dtype=np.float64)
        surfaced = int((~np.isfinite(values)).sum())
        if surfaced < nulls:
            raise SweepError(
                f"run_oof_sweep: block column {name!r} carried {nulls} null(s) "
                f"but only {surfaced} non-finite value(s) survived the "
                "conversion to numpy -- a null that arrives as a number is an "
                "invented observation"
            )
        arrays[name] = values
    missing = [name for name in feature_names if name not in params]
    if missing:
        raise SweepError(
            f"run_oof_sweep: the normalisation artifact has no parameters for "
            f"{missing} -- refusing to refit them here, which would make every "
            "block's parameters a function of that block (D-07-11)"
        )
    features = np.column_stack(
        [apply_normalization(arrays[name], params[name]) for name in feature_names]
    )
    return features, arrays[TARGET_NAME]


def _negative_reason(config: ConfigScore, n_blocks: int) -> str:
    """The `reason` tag for an ineligible config: how many blocks failed,
    which one failed first, and that block's own gate message.

    Trimmed to `_REASON_BUDGET` deliberately and VISIBLY (`[...]`), because
    `negative_log.record_negative_result` REFUSES an over-long reason --
    MLflow truncates rather than raising, and a reason that reads complete
    while being cut short is worse than one that says where it stopped.
    """
    first = config.first_failing_block()
    failed = sum(1 for block in config.blocks if not block.gate_passed)
    head = (
        f"ineligible: {failed} of {n_blocks} OOF blocks failed gate_forecast; "
        f"first failure on {first.segment_name if first else '?'}: "
    )
    tail = first.gate_reason if first else "no block score recorded"
    room = _REASON_BUDGET - len(head)
    if len(tail) > room:
        tail = tail[: max(room - 5, 0)] + "[...]"
    return head + tail


def run_oof_sweep(
    segment_manifest_id: str,
    *,
    oof_block_names: Sequence[str],
    train_cache_path: Path,
    registry_root: Path,
    lake_root: Path,
    tracking_root: str,
    cache_root: Path,
    run_tags: Mapping[str, str],
    normalization_manifest_id: str,
    symbol: str,
    code_hash: str,
    seed: int,
    grid: Sequence[GridEntry] = GRID,
) -> SweepResult:
    """Fit every config on every OOF block's own training rows, score it on
    that block, record every loser, pick a winner by the rule in this
    module's docstring, and write `selection.json`.

    NO PARAMETER ABOVE CAN NAME `val` (D-07-04). `train_cache_path` is the
    already-materialized train frame -- a `train`-role materialization costs
    no look -- and it is required to live inside this manifest's own
    tracking-root-keyed cache directory, so a train frame belonging to
    ANOTHER fold geometry cannot be fitted on here. `oof_block_names` is the
    only route to `materialize_once`, and every entry must match
    `oof_block_<int>`.

    THE LOOP IS BLOCK-OUTER, CONFIG-INNER, and that is a measured choice
    rather than a stylistic one. Each block's frame is materialized once (one
    look) and its scoring arrays are built once, then all configs are fitted
    against it while the train cache stays warm in the page cache. The
    config-outer alternative reads each block's columns once per config --
    36x the block I/O for identical arithmetic. Results are collected by
    `(grid_index, block)` and reduced per config afterwards, so the loop
    order is invisible in the output.

    ONE NEGATIVE-RESULT RECORD PER INELIGIBLE CONFIG (D-07-20), never one
    per failing block: a config that fails three blocks was tried once.
    `warn_if_already_negative` runs FIRST, so a re-run names the prior runs
    instead of silently doubling the log.

    Returns a `SweepResult` whose `winner` raises when nothing was eligible.
    """
    block_names = _require_oof_block_names(oof_block_names)
    manifest = read_segment_manifest(Path(registry_root), segment_manifest_id)
    blocks = _manifest_blocks(manifest)
    known = {entry["name"]: index for index, entry in enumerate(blocks)}
    unknown = [name for name in block_names if name not in known]
    if unknown:
        raise SweepError(
            f"run_oof_sweep: {unknown} are not oof_block entries of manifest "
            f"{segment_manifest_id[:12]} (it has {sorted(known)})"
        )

    cache_dir = segment_cache_dir(cache_root, tracking_root, segment_manifest_id)
    train_cache_path = Path(train_cache_path)
    if not train_cache_path.is_file():
        raise SweepError(
            f"run_oof_sweep: train cache {train_cache_path} does not exist -- "
            "materialize the train segment through "
            "models.cache.materialize_once first (it costs no look)"
        )
    if train_cache_path.parent.resolve() != cache_dir.resolve():
        raise SweepError(
            f"run_oof_sweep: train cache {train_cache_path} is not inside this "
            f"manifest's own cache directory {cache_dir} -- a train frame from "
            "another segment manifest carries another fold geometry, and "
            "fitting on rows this geometry never assigned to training is "
            "precisely the leak the harness exists to prevent"
        )

    feature_names = validate_feature_names(MODEL_FEATURE_NAMES)
    context = FitContext(
        symbol=symbol,
        lake_root=Path(lake_root),
        registry_root=Path(registry_root),
        code_hash=code_hash,
    )
    artifact = load_normalization(
        normalization_manifest_id,
        normalization_dataset(symbol),
        registry_root=Path(registry_root),
        lake_root=Path(lake_root),
    )

    train_etime_frame = pl.read_parquet(train_cache_path, columns=["etime"])
    cache_etime = assert_decision_order(train_etime_frame)
    logger.info(
        "run_oof_sweep: train cache %s has %d rows; %d configs x %d blocks",
        train_cache_path,
        cache_etime.size,
        len(grid),
        len(block_names),
    )

    mask_paths: dict[str, Path] = {}
    for name in block_names:
        block_train = training_rows_for_block(
            train_etime_frame,
            blocks,
            known[name],
            purge_ns=manifest["purge_ns"],
            embargo_ns=manifest["embargo_ns"],
        )
        path = cache_dir / f"row_mask_{name}.parquet"
        kept = _write_block_row_mask(
            path=path,
            cache_etime=cache_etime,
            block_train=block_train,
            segment_name=name,
        )
        mask_paths[name] = path
        logger.info(
            "run_oof_sweep: %s trains on %d of %d cached rows",
            name,
            kept,
            cache_etime.size,
        )

    trainers = [entry.build(context) for entry in grid]
    recipes: list[tuple[dict[str, Any], str]] = [
        config_recipe(
            model_class=trainer.model_class,
            hyperparameters=trainer.hyperparameters,
            seed=seed,
            code_hash=code_hash,
            normalization_manifest_id=normalization_manifest_id,
        )
        for trainer in trainers
    ]
    fingerprints = [fingerprint for _recipe, fingerprint in recipes]
    if len(set(fingerprints)) != len(fingerprints):
        raise SweepError(
            f"run_oof_sweep: the {len(grid)}-entry grid produces only "
            f"{len(set(fingerprints))} distinct config fingerprints -- two "
            "configs would share one negative-result record and D-07-20's "
            "'every failing config leaves a trace' would be false"
        )

    scores: dict[int, list[BlockScore]] = {index: [] for index in range(len(grid))}
    for name in block_names:
        frame = materialize_once(
            segment_manifest_id,
            name,
            registry_root=Path(registry_root),
            lake_root=Path(lake_root),
            tracking_root=tracking_root,
            run_tags=dict(run_tags),
            cache_root=cache_root,
        )
        assert_decision_order(frame)
        features, target = _scoring_columns(
            frame, feature_names=feature_names, params=artifact.params
        )
        del frame
        for index, trainer in enumerate(trainers):
            fitted = trainer.fit(
                FitInputs(
                    cache_path=train_cache_path,
                    feature_names=feature_names,
                    target_name=TARGET_NAME,
                    row_mask_path=mask_paths[name],
                    normalization_manifest_id=normalization_manifest_id,
                    seed=seed,
                )
            )
            pred = fitted.predict(features)
            if pred.shape[0] != target.shape[0]:
                raise SweepError(
                    f"run_oof_sweep: config {index} predicted "
                    f"{pred.shape[0]} rows for {name}'s {target.shape[0]} "
                    "scored rows -- the two are the same rows in the same "
                    "order by construction, so a length disagreement means "
                    "one of them is a different frame"
                )
            metrics = forecast_metrics(
                pred, target, train_mean=fitted.train_target_mean
            )
            passed, reason = gate_forecast(metrics)
            scores[index].append(
                BlockScore(
                    segment_name=name,
                    metrics=metrics,
                    gate_passed=passed,
                    gate_reason=reason,
                    n_rows_fitted=int(fitted.n_rows_fitted),
                    n_rows_dropped=int(fitted.n_rows_dropped),
                    fit_seconds=float(trainer.last_fit_seconds or 0.0),
                )
            )
        del features, target

    configs: list[ConfigScore] = []
    for index, trainer in enumerate(trainers):
        summary = summarise_config(
            grid_index=index,
            model_class=trainer.model_class,
            hyperparameters=trainer.hyperparameters,
            config_fingerprint=fingerprints[index],
            blocks=scores[index],
        )
        logger.info(
            "run_oof_sweep: config %d %s %s eligible=%s mean_ic=%s %.3fs",
            index,
            summary.model_class,
            dict(summary.hyperparameters),
            summary.eligible,
            summary.mean_rank_ic_non_tied,
            summary.fit_seconds_total,
        )
        if not summary.eligible:
            recipe = recipes[index][0]
            # D-07-30: no run of this module's own is open here, and
            # `record_negative_result` opens its own per record.
            negative_log.warn_if_already_negative(
                recipe, tracking_root=str(tracking_root)
            )
            tags = dict(run_tags)
            tags["fold_config"] = manifest["layout"]
            tags["segment_manifest_id"] = segment_manifest_id
            tags["model_class"] = summary.model_class
            run_id = negative_log.record_negative_result(
                recipe,
                reason=_negative_reason(summary, len(block_names)),
                tracking_root=str(tracking_root),
                run_tags=tags,
            )
            summary = replace(summary, negative_run_id=run_id)
        configs.append(summary)

    winner_index, eligible = select_winner(configs)
    observed_ids = look_run_ids(
        segment_manifest_id, block_names, tracking_root=tracking_root
    )
    result = SweepResult(
        segment_manifest_id=segment_manifest_id,
        normalization_manifest_id=normalization_manifest_id,
        block_names=block_names,
        n_configs=len(grid),
        configs=tuple(configs),
        eligible_grid_indices=eligible,
        winner_grid_index=winner_index,
        look_run_ids={name: tuple(ids) for name, ids in observed_ids.items()},
        selection_path=selection_path(cache_root, tracking_root, segment_manifest_id),
    )
    _atomic_write_json(result.selection_path, _selection_body(result, manifest))
    logger.info(
        "run_oof_sweep: wrote %s -- %d of %d configs eligible, winner %s",
        result.selection_path,
        result.eligible_count,
        result.n_configs,
        result.winner_grid_index,
    )
    return result


def _selection_body(result: SweepResult, manifest: Mapping[str, Any]) -> dict:
    """The `selection.json` body -- everything `--freeze` and plan 07-09's
    run 3 need, and nothing that would make it big.

    `look_run_ids` is here because those ids cannot be carried in memory
    across the commit between the two processes; run 3 re-queries them live
    and asserts the live set COVERS these, so an id recorded here and absent
    there means the store was moved or pruned, which is worth failing over.

    Per-block METRICS are stored for every config, not only the winner: the
    loser's numbers are what a human reads to decide whether the grid was
    the problem, and they are scalars -- no prediction table is stored
    anywhere (D-07-15).
    """
    winner = result.winner if result.has_winner else None
    return {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "selection_rule": SELECTION_RULE,
        "segment_manifest_id": result.segment_manifest_id,
        "normalization_manifest_id": result.normalization_manifest_id,
        "fold_config": manifest["layout"],
        "symbol": manifest["symbol"],
        "block_names": list(result.block_names),
        "n_configs": result.n_configs,
        "eligible_count": result.eligible_count,
        "eligible_grid_indices": list(result.eligible_grid_indices),
        "winner_grid_index": result.winner_grid_index,
        "winner": (
            None
            if winner is None
            else {
                "grid_index": winner.grid_index,
                "model_class": winner.model_class,
                "hyperparameters": dict(winner.hyperparameters),
                "config_fingerprint": winner.config_fingerprint,
                "mean_rank_ic_non_tied": _jsonable(winner.mean_rank_ic_non_tied),
                "mean_r2_vs_mean": _jsonable(winner.mean_r2_vs_mean),
                "ic_min": _jsonable(winner.ic_min),
                "ic_max": _jsonable(winner.ic_max),
                "ic_std": _jsonable(winner.ic_std),
            }
        ),
        "look_run_ids": {name: list(ids) for name, ids in result.look_run_ids.items()},
        "configs": [
            {
                "grid_index": config.grid_index,
                "model_class": config.model_class,
                "hyperparameters": dict(config.hyperparameters),
                "config_fingerprint": config.config_fingerprint,
                "eligible": config.eligible,
                "mean_rank_ic_non_tied": _jsonable(config.mean_rank_ic_non_tied),
                "mean_r2_vs_mean": _jsonable(config.mean_r2_vs_mean),
                "ic_min": _jsonable(config.ic_min),
                "ic_max": _jsonable(config.ic_max),
                "ic_std": _jsonable(config.ic_std),
                "fit_seconds_total": config.fit_seconds_total,
                "negative_run_id": config.negative_run_id,
                "blocks": [
                    {
                        "segment_name": block.segment_name,
                        "gate_passed": block.gate_passed,
                        "gate_reason": block.gate_reason,
                        "n_rows_fitted": block.n_rows_fitted,
                        "n_rows_dropped": block.n_rows_dropped,
                        "fit_seconds": block.fit_seconds,
                        "metrics": {
                            key: _jsonable(value)
                            for key, value in block.metrics.items()
                        },
                    }
                    for block in config.blocks
                ],
            }
            for config in result.configs
        ],
    }
