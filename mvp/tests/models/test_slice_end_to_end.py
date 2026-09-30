"""The whole slice on the fixture lake: curated rows to a fitted model to a
stored prediction table to the simulator to ONE tracked run, with every root
a `tmp_path`.

WHAT THIS FILE IS FOR, BEYOND "IT RUNS". Success criterion 3 is provenance
closure: somebody holding only the MLflow run must be able to rebuild every
byte. That is a claim about a tag set, an id chain and an ORDER, and none of
the three can be checked by reading the code. So this file asserts the two
orderings that measurement forced -- run 3 opens last, and the winner is
frozen before `val` is ever touched -- rather than leaving them in prose.

THE THREE CODE HASHES ARE DELIBERATELY DIFFERENT. Each mode is invoked with
its own literal (`"0"*40`, `"1"*40`, `"2"*40`), because the real run has three
different CLEAN hashes by design: three invocations at three commits. An
all-equal fixture would hide the one thing that breaks on the real run --
`selection.json`'s `config_fingerprint` equals `predictor_id` only at the
SELECT-time hash, so the frozen body must be found by the recipe MINUS
`code_hash`. The durable claim this file makes is "each artifact carries its
own invocation's injected value, and every one of them is clean", never "the
phase has one hash".

THE RIG IS 18,000 ROWS ON A 6,000-ROW TRAIN ENTRY, AND THAT IS A MEASURED
CHOICE. The sweep's cost is set by the train entry, so it is the same 0.65 s
as plan 07-08's. The VAL length is what decides whether the fitted model ever
crosses a quote: its predictions sit about 0.49 ticks (standard deviation)
away from a mid that is always a half tick, and the kernel needs a full 1.5
ticks to trade, so crossings are a ~3sigma tail event. Measured before this
file was written: 599 val rows give 1 trade and exactly $0 (which fails
`gate_monetization` for real), 5,999 give 1, and 11,999 give 12 trades / 50
closed ticks / +$0.0050 against a ceiling of 639 trades / 2,187 ticks. The
short rig is kept for the gate-failure test, where a genuinely worthless
result is better than a patched gate.

NO GRID REDUCTION ANYWHERE. The full `GRID_SIZE` configs run in every
`select`, and `n_configs` is asserted against that symbol rather than a
literal, so nobody can later believe the whole grid was exercised when 3 were.

Every test builds its own `tmp_path` lake, registry, MLflow tracking and
cache roots. Pre-commit hooks 18/19 run the full suite on every commit, so a
test that reached a canonical root would spend an irreversible validation look
once per commit forever (D-07-34).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from mlflow.tracking import MlflowClient

from harness import budget, negative_log
from models import cache, slice as slice_module
from models.conversion import neutral_fill_null_predictions
from models.frozen import read_frozen_predictor
from models.gates import CeilingExceededError, perfect_foresight_ceiling
from models.predictions import load_prediction_table, predictions_dataset
from models.regression import GRID_SIZE, TARGET_NAME
from models.slice import (
    MODE_FREEZE,
    MODE_SELECT,
    MODE_VAL,
    SCORED_SEGMENT_TAG_KEY,
    SLICE_EXPERIMENT_NAME,
    SLICE_STAGE_TAG,
    VAL_SEGMENT_NAME,
    SliceError,
    run_slice,
)
from models.sweep import SelectionError, selection_path
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED
from tests.fixtures.model_span import build_model_span_fixture
from tracking.mlflow_utils import (
    MANDATORY_TAG_KEYS,
    build_tracking_uri,
    read_provenance_tag,
)

#: 11,999 val rows is what makes the model trade at all -- see the module
#: docstring for the measurement.
RIG_ROWS: int = 18_000
RIG_TRAIN_ROWS: int = 6_000
RIG_K: int = 5

#: The short rig, where the fitted model manages 1 trade and exactly $0 and
#: `gate_monetization` therefore FAILS on real numbers rather than on a patch.
SHORT_RIG_ROWS: int = 6_600

SEED: int = 20260925

SELECT_HASH: str = "0" * 40
FREEZE_HASH: str = "1" * 40
VAL_HASH: str = "2" * 40


def _rig(tmp_path, lake_root, registry_root, tracking_root, *, rows=RIG_ROWS) -> dict:
    rig = build_model_span_fixture(
        lake_root,
        registry_root,
        tracking_root,
        rows=rows,
        train_rows=RIG_TRAIN_ROWS,
        k=RIG_K,
    )
    return {
        "manifest_id": rig["manifest"]["manifest_id"],
        "block_names": list(rig["geometry"]["oof_block_names"]),
        "roots": {
            "registry_root": registry_root,
            "lake_root": lake_root,
            "tracking_root": str(tracking_root),
            "cache_root": tmp_path / "scratch",
            "seed": SEED,
        },
    }


def _run(rig: dict, mode: str, code_hash: str, **overrides):
    kwargs = dict(rig["roots"])
    kwargs.update(overrides)
    return run_slice(rig["manifest_id"], mode=mode, code_hash=code_hash, **kwargs)


def _select_freeze_val(rig: dict):
    """All three modes in sequence, each with its own injected hash -- the
    shape the real run has, minus the commits between them."""
    selected = _run(rig, MODE_SELECT, SELECT_HASH)
    frozen = _run(rig, MODE_FREEZE, FREEZE_HASH)
    scored = _run(rig, MODE_VAL, VAL_HASH)
    return selected, frozen, scored


def _client(tracking_root) -> MlflowClient:
    return MlflowClient(build_tracking_uri(str(tracking_root)))


def _runs_in(tracking_root, experiment_name: str) -> list:
    client = _client(tracking_root)
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        return []
    return client.search_runs([experiment.experiment_id])


def _val_cache(rig: dict) -> Path:
    return cache.segment_cache_path(
        rig["roots"]["cache_root"],
        rig["roots"]["tracking_root"],
        rig["manifest_id"],
        VAL_SEGMENT_NAME,
    )


# --------------------------------------------------------------------------
# 1. The cross-process handoff
# --------------------------------------------------------------------------


def test_the_freeze_mode_reads_the_winner_the_select_mode_wrote(
    tmp_path, lake_root, registry_root, tracking_root
):
    """`select` writes `selection.json`; `freeze` reads it in a fresh call
    carrying no in-memory state, and refuses when the file is absent or when
    the manifest id inside it disagrees with the one it is being applied to.

    THE REAL RUN CROSSES THIS BOUNDARY AT A COMMIT. Nothing in memory
    survives that, so the file is the handoff and its refusals are the only
    thing standing between a mismatched winner and five spent looks.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    selected = _run(rig, MODE_SELECT, SELECT_HASH)
    path = selection_path(
        rig["roots"]["cache_root"], rig["roots"]["tracking_root"], rig["manifest_id"]
    )
    assert path.is_file(), f"select wrote no selection.json at {path}"
    assert selected.selection_path == path
    assert selected.predictor_manifest_id is None, (
        "select wrote a registry body -- it must not: the body has to land in "
        "the same commit as the guardrail extension that scans predictors/"
    )
    assert not (Path(registry_root) / "predictors").exists()

    frozen = _run(rig, MODE_FREEZE, FREEZE_HASH)
    assert dict(frozen.winner_hyperparameters) == dict(selected.winner_hyperparameters)
    assert frozen.winner_model_class == selected.winner_model_class
    assert frozen.frozen_body["hyperparameters"] == dict(
        selected.winner_hyperparameters
    )
    assert frozen.n_configs == GRID_SIZE == selected.n_configs

    # The refusal, on a mutated copy, RESTORED afterwards -- 07-08's recorded
    # anti-pattern is leaving a tampered artifact in place and then watching
    # the NEXT refusal fire while asserting on this one's message.
    original = path.read_text()
    body = json.loads(original)
    body["segment_manifest_id"] = "f" * 64
    path.write_text(json.dumps(body))
    try:
        with pytest.raises(SelectionError, match="segment_manifest_id"):
            _run(rig, MODE_FREEZE, FREEZE_HASH)
    finally:
        path.write_text(original)

    moved = path.with_suffix(".json.moved")
    path.rename(moved)
    try:
        with pytest.raises(SelectionError, match="--select"):
            _run(rig, MODE_FREEZE, FREEZE_HASH)
    finally:
        moved.rename(path)


# --------------------------------------------------------------------------
# 2-3. The injected code hash, and the absence of git
# --------------------------------------------------------------------------


def test_every_artifact_of_one_invocation_carries_that_invocations_injected_code_hash(
    tmp_path, lake_root, registry_root, tracking_root
):
    """Each artifact carries the hash of the invocation that WROTE it, and
    none of them ends in `-dirty`.

    NOT "the phase has one hash" -- the three modes here are given three
    different literals on purpose, because the real run has three different
    clean hashes, one per commit. The normalisation manifest and the five OOF
    look runs belong to `select`; the frozen body to `freeze`; the
    prediction-table manifest, the `val` look run and run 3 to `val`.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    selected, frozen, scored = _select_freeze_val(rig)

    norm = json.loads(
        (
            Path(registry_root)
            / "manifests"
            / "BTCUSDT.features_norm"
            / f"{selected.normalization_manifest_id}.json"
        ).read_text()
    )
    assert norm["code_hash"] == SELECT_HASH
    assert frozen.frozen_body["code_hash"] == FREEZE_HASH
    table_manifest = json.loads(
        (
            Path(registry_root)
            / "manifests"
            / predictions_dataset("BTCUSDT")
            / f"{scored.prediction_table_manifest_id}.json"
        ).read_text()
    )
    assert table_manifest["code_hash"] == VAL_HASH

    client = _client(tracking_root)
    look_experiment = client.get_experiment_by_name(cache.LOOK_EXPERIMENT_NAME)
    look_runs = client.search_runs([look_experiment.experiment_id])
    by_segment = {run.data.tags["segment_name"]: run for run in look_runs}
    assert sorted(by_segment) == sorted([*rig["block_names"], VAL_SEGMENT_NAME])
    for name in rig["block_names"]:
        assert by_segment[name].data.tags["code_hash"] == SELECT_HASH
    assert by_segment[VAL_SEGMENT_NAME].data.tags["code_hash"] == VAL_HASH

    slice_run = client.get_run(scored.run_id)
    assert slice_run.data.tags["code_hash"] == VAL_HASH
    for observed in (
        norm["code_hash"],
        frozen.frozen_body["code_hash"],
        table_manifest["code_hash"],
        slice_run.data.tags["code_hash"],
        *(run.data.tags["code_hash"] for run in look_runs),
    ):
        assert not observed.endswith("-dirty"), observed


def test_run_slice_never_shells_out_to_git(
    tmp_path, lake_root, registry_root, tracking_root
):
    """The refusal is on the STRING, and the module contains no route to git
    at all -- asserted by AST, not by reading.

    THIS IS WHAT STOPS THE LIBRARY RE-ACQUIRING A DEPENDENCY ON THE WORKING
    TREE. Pre-commit stashes only UNSTAGED changes, so a staged file leaves
    `M  path` in `git status --porcelain` and `compute_code_hash` appends
    `-dirty` during every hook run. A `compute_code_hash()`-plus-refuse inside
    `run_slice` would therefore fail every commit in this repo forever, and
    the tests in this file -- which call it -- would be the first casualties.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    with pytest.raises(SliceError, match="-dirty"):
        _run(rig, MODE_SELECT, "abc1234-dirty")
    with pytest.raises(SliceError, match="non-empty string"):
        _run(rig, MODE_SELECT, "")

    source = Path(slice_module.__file__).read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name):
                called.add(function.id)
            elif isinstance(function, ast.Attribute):
                called.add(function.attr)
    assert "subprocess" not in imported, (
        "models/slice.py imports subprocess -- the library must have no route "
        "to git; the CLI is where git lives"
    )
    assert "compute_code_hash" not in imported | called, (
        "models/slice.py reaches compute_code_hash -- it takes code_hash as a "
        "required parameter precisely so it cannot"
    )
    # Anti-vacuity: the AST walk above must actually be seeing this module's
    # imports and calls, or both assertions pass on an empty set.
    assert {"mlflow", "numpy", "polars"} <= imported
    assert {"run_oof_sweep", "start_tracked_run", "materialize_once"} <= called


# --------------------------------------------------------------------------
# 4-5. The two orderings, made mechanical
# --------------------------------------------------------------------------


def test_run_three_opens_even_when_a_gate_fails(
    tmp_path, lake_root, registry_root, tracking_root
):
    """On the SHORT rig the fitted model manages 1 trade and exactly $0, so
    `gate_monetization` fails on real numbers. Run 3 opens anyway, carrying
    the failing verdict, and a negative result is recorded FIRST -- because
    runs cannot nest (D-07-30).

    SC3 must not depend on SC1. A phase that produced no MLflow run because
    the model underperformed would fail the provenance criterion on top of the
    performance one.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root, rows=SHORT_RIG_ROWS)
    _selected, frozen, scored = _select_freeze_val(rig)
    assert scored.monetization_gate_passed is False, (
        f"the short rig was expected to fail gate_monetization; it reported "
        f"{scored.monetization_gate_reason}"
    )
    assert scored.run_id is not None, "run 3 did not open on a failing gate"
    assert scored.metrics["monetization_gate_passed"] == 0.0
    assert scored.negative_run_id is not None

    slice_run = _client(tracking_root).get_run(scored.run_id)
    assert slice_run.data.tags["negative_run_id"] == scored.negative_run_id
    assert float(slice_run.data.metrics["monetization_gate_passed"]) == 0.0
    assert float(slice_run.data.metrics["sim_trades"]) == scored.metrics["sim_trades"]

    recorded = negative_log.query_negative_results(
        tracking_root=rig["roots"]["tracking_root"],
        config_fingerprint=frozen.predictor_id,
    )
    assert len(recorded) == 1, (
        "the failing val result is not queryable by the frozen predictor's own "
        f"predictor_id; got {recorded}"
    )
    fingerprint, reason, _when, run_id, recorded_hash, data_hash = recorded[0]
    assert fingerprint == frozen.predictor_id
    assert "monetization=FAIL" in reason
    assert run_id == scored.negative_run_id
    assert recorded_hash == VAL_HASH, (
        "the negative record carries the freeze-time hash, not the "
        "invocation's own -- the record is about what THIS run measured"
    )
    assert data_hash == scored.prediction_table_manifest_id


def test_the_val_mode_refuses_when_no_frozen_body_exists_yet(
    tmp_path, lake_root, registry_root, tracking_root
):
    """`select`, then `val` with `freeze` SKIPPED: refused, naming the missing
    body and `--freeze` as the remedy.

    This is what makes freeze-before-look mechanical rather than procedural.
    No look is spent by the refusal, which is the whole point of putting it
    ahead of step 6.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    _run(rig, MODE_SELECT, SELECT_HASH)
    with pytest.raises(SliceError, match="no frozen predictor"):
        _run(rig, MODE_VAL, VAL_HASH)
    assert (
        budget.look_count(
            rig["manifest_id"],
            VAL_SEGMENT_NAME,
            tracking_root=rig["roots"]["tracking_root"],
        )
        == 0
    ), "the refusal spent a val look -- it must fire before step 6"


# --------------------------------------------------------------------------
# 6-8. Provenance closure
# --------------------------------------------------------------------------


def test_the_slice_runs_curated_store_to_model_to_table_to_simulator_to_one_tracked_run(
    tmp_path, lake_root, registry_root, tracking_root
):
    """Success criterion 3, end to end, with the anti-vacuity bounds that stop
    an inert pipeline passing every structural assertion."""
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    selected, frozen, scored = _select_freeze_val(rig)

    assert selected.normalization_manifest_id
    assert selected.n_configs == GRID_SIZE
    assert 0 < selected.eligible_count <= GRID_SIZE

    client = _client(tracking_root)
    look_experiment = client.get_experiment_by_name(cache.LOOK_EXPERIMENT_NAME)
    look_runs = client.search_runs([look_experiment.experiment_id])
    per_segment: dict[str, int] = {}
    for run in look_runs:
        name = run.data.tags["segment_name"]
        per_segment[name] = per_segment.get(name, 0) + 1
    assert per_segment == {
        **{name: 1 for name in rig["block_names"]},
        VAL_SEGMENT_NAME: 1,
    }, per_segment

    slice_runs = _runs_in(tracking_root, SLICE_EXPERIMENT_NAME)
    assert len(slice_runs) == 1, f"{len(slice_runs)} stage-1-regression runs"
    assert slice_runs[0].info.run_id == scored.run_id

    stored = load_prediction_table(
        scored.prediction_table_manifest_id,
        predictions_dataset("BTCUSDT"),
        registry_root=Path(registry_root),
        lake_root=Path(lake_root),
    )
    assert stored.table.height > 0
    assert stored.predictor_id == frozen.predictor_id

    # ---- anti-vacuity -------------------------------------------------
    predictor = read_frozen_predictor(
        frozen.predictor_manifest_id, registry_root=Path(registry_root)
    )
    assert any(coefficient != 0.0 for coefficient in predictor.coef), (
        "the winner's coefficient vector is all zeros -- every structural "
        "assertion above would pass on a pipeline that learned nothing"
    )
    trades = scored.metrics["sim_trades"]
    ceiling_trades = scored.metrics["ceiling_trades"]
    assert 0 < trades < ceiling_trades, (
        f"{trades} trades against a perfect-foresight ceiling of "
        f"{ceiling_trades} -- an inert pipeline trades 0 and a leaking one "
        "reaches the ceiling"
    )
    assert np.isfinite(scored.metrics["sim_pnl_usd"])
    assert 0.0 < scored.metrics["pnl_fraction_of_ceiling"] < 1.0
    print(
        f"fixture slice: {int(trades)} trades, "
        f"{int(scored.metrics['sim_closed_pnl_ticks'])} closed ticks, "
        f"${scored.metrics['sim_pnl_usd']:+.6f} against a ceiling of "
        f"{int(ceiling_trades)} trades / "
        f"{int(scored.metrics['ceiling_closed_pnl_ticks'])} ticks / "
        f"${scored.metrics['ceiling_pnl_usd']:+.6f} "
        f"({scored.metrics['pnl_fraction_of_ceiling']:.4%} of it)"
    )


def test_the_tracked_run_carries_every_mandatory_tag_plus_the_four_additive_ids(
    tmp_path, lake_root, registry_root, tracking_root
):
    """All eight mandatory tags present and non-empty, plus `predictor_id`,
    `normalization_manifest_id`, `prediction_table_manifest_id` and
    `look_run_ids` -- and `data_hash` IS the prediction-table manifest id,
    which is what chains the run back to `features_norm` and the feature
    manifests through `inputs`.

    `look_run_ids` is read through `read_provenance_tag`, the only correct
    reader: MLflow truncates an over-long tag value rather than raising, so
    the bare key alone silently stops at 8,000 characters' worth.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    selected, frozen, scored = _select_freeze_val(rig)
    tags = _client(tracking_root).get_run(scored.run_id).data.tags

    missing = sorted(MANDATORY_TAG_KEYS - tags.keys())
    assert not missing, missing
    for key in sorted(MANDATORY_TAG_KEYS):
        assert tags[key].strip(), f"mandatory tag {key} is blank"
        assert tags[key] != "n/a", (
            f"mandatory tag {key} is the spec's placeholder -- this is the "
            "phase that lands all four of the deferrable ones for real"
        )
    assert tags["data_hash"] == scored.prediction_table_manifest_id
    assert tags["stage"] == SLICE_STAGE_TAG
    assert tags["segment_manifest_id"] == rig["manifest_id"]
    assert tags["fold_config"] == "compressed_3seg"
    assert tags["model_class"] == frozen.winner_model_class

    assert tags["predictor_id"] == frozen.predictor_id
    assert tags["normalization_manifest_id"] == selected.normalization_manifest_id
    assert tags["prediction_table_manifest_id"] == scored.prediction_table_manifest_id
    assert tags[SCORED_SEGMENT_TAG_KEY] == VAL_SEGMENT_NAME
    assert "segment_name" not in tags, (
        "run 3 carries a `segment_name` tag -- harness.budget.look_count "
        "counts every run whose (segment_manifest_id, segment_name) pair "
        "matches, across ALL experiments, so this run would be counted as a "
        "look and the val budget would read as spent twice"
    )

    ids = read_provenance_tag(tags, "look_run_ids")
    assert len(ids) == len(rig["block_names"]) + 1, ids
    recorded = json.loads(selected.selection_path.read_text())["look_run_ids"]
    for name, block_ids in recorded.items():
        assert set(block_ids) <= set(ids), (
            f"selection.json recorded look runs for {name} that run 3 does not cite"
        )
    # And the provenance keys `log_data_provenance` owns.
    assert scored.prediction_table_manifest_id in read_provenance_tag(
        tags, "data_manifest_ids"
    )
    assert "dq_ack_ids" in tags


def test_the_predictor_id_tag_equals_the_committed_bodys_field(
    tmp_path, lake_root, registry_root, tracking_root
):
    """The tag comes from the BODY, never from a recomputation at the current
    hash -- and on this fixture the two genuinely differ, because `freeze` and
    `val` are invoked with different literals.

    `predictor_id` embeds `code_hash`. Step 9 runs at a later commit than step
    5 by design, so a recomputation here would silently disagree with the
    artifact it claims to describe.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    _selected, frozen, scored = _select_freeze_val(rig)
    body = json.loads(
        (
            Path(registry_root) / "predictors" / f"{frozen.predictor_manifest_id}.json"
        ).read_text()
    )
    tags = _client(tracking_root).get_run(scored.run_id).data.tags
    assert tags["predictor_id"] == body["predictor_id"]
    assert body["code_hash"] == FREEZE_HASH != VAL_HASH, (
        "the fixture no longer runs freeze and val at different hashes, so "
        "'the tag is not recomputed' is no longer being tested"
    )
    stored_table = load_prediction_table(
        scored.prediction_table_manifest_id,
        predictions_dataset("BTCUSDT"),
        registry_root=Path(registry_root),
        lake_root=Path(lake_root),
    )
    assert stored_table.predictor_id == body["predictor_id"]


# --------------------------------------------------------------------------
# 9-10. The nesting constraint and the exactly-once look
# --------------------------------------------------------------------------


def test_no_slice_run_is_open_while_a_look_or_a_negative_result_is_recorded(
    tmp_path, lake_root, registry_root, monkeypatch, tracking_root
):
    """`mlflow.active_run()` is `None` at every `materialize_once` and every
    `record_negative_result`, checked at the call site rather than assumed --
    and when a run IS forced open, the refusal surfaces instead of being
    swallowed.

    D-07-30 is the reason run 3 opens last. `mlflow.start_run` raises when a
    run is already active and neither `record_look` nor
    `record_negative_result` passes `nested=True`.
    """
    import mlflow

    observed: list[tuple[str, bool]] = []
    real_materialize = slice_module.materialize_once
    real_record = negative_log.record_negative_result

    def watched_materialize(*args, **kwargs):
        observed.append((f"materialize:{args[1]}", mlflow.active_run() is None))
        return real_materialize(*args, **kwargs)

    def watched_record(*args, **kwargs):
        observed.append(("negative", mlflow.active_run() is None))
        return real_record(*args, **kwargs)

    monkeypatch.setattr(slice_module, "materialize_once", watched_materialize)
    monkeypatch.setattr(negative_log, "record_negative_result", watched_record)

    rig = _rig(tmp_path, lake_root, registry_root, tracking_root, rows=SHORT_RIG_ROWS)
    _select_freeze_val(rig)
    assert observed, "neither call site was reached"
    assert [label for label, was_clear in observed if not was_clear] == [], observed
    assert sum(1 for label, _ in observed if label == "negative") > 0, (
        "no negative result was recorded on the short rig, so the second half "
        "of the nesting constraint went untested"
    )

    # Force a run open and assert the refusal surfaces rather than being
    # swallowed into "the look failed".
    rig2 = _rig(
        tmp_path / "second", lake_root / "b", registry_root / "b", tracking_root
    )
    forced = mlflow.start_run()
    try:
        with pytest.raises((SliceError, Exception), match="active"):
            _run(rig2, MODE_SELECT, SELECT_HASH)
    finally:
        mlflow.end_run()
        assert forced is not None


def test_the_val_look_is_spent_exactly_once_across_the_whole_slice(
    tmp_path, lake_root, registry_root, tracking_root
):
    """`look_count(val)` is 1 at the end, and a SECOND `mode="val"` against
    the same cache does not make it 2.

    The second invocation exercises both reuse-before-writing branches at
    once: `materialize_once` hits its cache (no look) and step 7 loads the
    stored prediction table instead of hitting
    `write_prediction_table`'s write-once guard.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    _selected, _frozen, first = _select_freeze_val(rig)
    counter = dict(
        val=budget.look_count(
            rig["manifest_id"],
            VAL_SEGMENT_NAME,
            tracking_root=rig["roots"]["tracking_root"],
        )
    )
    assert counter["val"] == 1, counter
    assert first.prediction_table_reused is False

    second = _run(rig, MODE_VAL, VAL_HASH)
    assert second.prediction_table_reused is True
    assert second.prediction_table_manifest_id == first.prediction_table_manifest_id, (
        "the second invocation issued a SECOND prediction-table manifest"
    )
    assert (
        budget.look_count(
            rig["manifest_id"],
            VAL_SEGMENT_NAME,
            tracking_root=rig["roots"]["tracking_root"],
        )
        == 1
    ), "the second val invocation spent another look"
    for name in rig["block_names"]:
        assert (
            budget.look_count(
                rig["manifest_id"], name, tracking_root=rig["roots"]["tracking_root"]
            )
            == 1
        )


# --------------------------------------------------------------------------
# 11-12. The ceiling guard, and the metric that tells "no signal" from
#        "nothing was predicted"
# --------------------------------------------------------------------------


def test_the_ceiling_guard_fires_on_a_fabricated_pnl_inside_the_slice(
    tmp_path, lake_root, registry_root, monkeypatch, tracking_root
):
    """THE GUARD'S TEETH, and the only construction that can produce them.

    The fabrication is at the simulator, not at the guard: the slice believes it
    is scoring the model while the kernel walks a DIFFERENT PRICE PATH -- the
    val frame's own book with every deviation from its first row tripled -- fed
    perfect foresight's prediction on that path. The P&L that comes back was
    earned over rows the bound was never measured on, which is failure mode #1
    in the guard's own message, and it exceeds what any policy on the real path
    could produce. The guard raises and no run is opened.

    WHY THE FABRICATION HAD TO BECOME THIS (changed 2026-09-29 with the guard's
    reference; `models/gates.py`'s module docstring has the measurement). The
    guard's bound is now `mid_total_variation`, which is a THEOREM: every closed
    leg pays at least a one-tick spread, so perfect foresight on the REAL path
    comes in strictly below it and the previous fabrication -- perfect foresight
    on the real rows -- can no longer reach the guard by construction, not by
    accident. Nor can any other prediction. That is the price of a bound that is
    actually a bound, and it is why the widened path is the honest construction:
    the results this guard exists to catch are the impossible ones.

    The tighter candidate cannot be used instead: decision-row perfect foresight
    earns exactly ZERO on this rig's one-tick-step path while the model earns 50
    ticks, so a guard referenced to it would abort every ordinary run here --
    asserted in `tests/models/test_gates.py`.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    _selected, _frozen, scored = _select_freeze_val(rig)
    bound_ticks = int(scored.metrics["mid_total_variation_bound_ticks"])
    ceiling_ticks = int(scored.metrics["ceiling_closed_pnl_ticks"])
    real_pnl = int(scored.metrics["sim_closed_pnl_ticks"])
    assert 0 < real_pnl < bound_ticks, (
        f"the honest run's {real_pnl} ticks is not strictly inside the "
        f"{bound_ticks}-tick bound, so this rig cannot show the guard "
        "distinguishing an ordinary result from an impossible one"
    )

    real_sim = slice_module.run_sim_checked
    widening = 3

    def fabricated(etime, bid_ticks, ask_ticks, _pred, **kwargs):
        # The same book, with every deviation from row 0 tripled -- a path whose
        # total variation is three times the one the bound was measured on.
        base_bid = int(bid_ticks[0])
        wide_bid = base_bid + widening * (bid_ticks - base_bid)
        wide_ask = wide_bid + (ask_ticks - bid_ticks)
        wide_mid = (wide_bid + wide_ask) * (TICK_SIZE_SCALED / PRICE_SCALE) / 2.0
        next_mid = np.empty_like(wide_mid)
        next_mid[:-1] = wide_mid[1:]
        next_mid[-1] = wide_mid[-1]
        perfect, substituted = neutral_fill_null_predictions(
            np.ascontiguousarray(next_mid / wide_mid - 1.0, dtype=np.float64),
            np.ascontiguousarray(wide_mid, dtype=np.float64),
        )
        assert substituted == 0
        return real_sim(
            etime,
            np.ascontiguousarray(wide_bid, dtype=np.int64),
            np.ascontiguousarray(wide_ask, dtype=np.int64),
            perfect,
            **kwargs,
        )

    monkeypatch.setattr(slice_module, "run_sim_checked", fabricated)
    with pytest.raises(CeilingExceededError, match=str(bound_ticks)) as excinfo:
        _run(rig, MODE_VAL, VAL_HASH)
    message = str(excinfo.value)
    print("slice guard:", message)
    assert str(ceiling_ticks) not in message, (
        "the guard named the label-horizon ceiling, which means the slice is "
        "still handing it the quantity that moves 30x with a choice of label"
    )
    assert "mid_total_variation" in message


def test_the_slice_reports_n_pred_missing_as_a_metric(
    tmp_path, lake_root, registry_root, tracking_root
):
    """A non-finite FEATURE makes a non-finite prediction, and the slice
    counts them.

    WITHOUT THIS METRIC AN ALL-NULL TABLE READS AS "NO SIGNAL" RATHER THAN
    "NOTHING WAS PREDICTED". The two are different findings: `n_scorable`
    already accounts for missing LABELS, so a null label is not the failure
    this counts -- the fixture's features are finite on every row by
    construction. The cache is rewritten with NaN in one feature on a handful
    of rows, row count unchanged so the provenance sidecar still agrees.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    _run(rig, MODE_SELECT, SELECT_HASH)
    _run(rig, MODE_FREEZE, FREEZE_HASH)

    # The cache is filled HERE rather than by a first `mode="val"` run,
    # because a run that had already stored its prediction table would reuse
    # it and the damaged features would never reach a prediction. One look is
    # spent, exactly as a real paid-for look would be, and the later
    # `mode="val"` hits the cache.
    cache.materialize_once(
        rig["manifest_id"],
        VAL_SEGMENT_NAME,
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=rig["roots"]["tracking_root"],
        run_tags={
            "code_hash": VAL_HASH,
            "data_hash": "none",
            "seed": str(SEED),
            "env_hash": "b" * 64,
            "model_class": "none (cache fill)",
        },
        cache_root=rig["roots"]["cache_root"],
    )
    path = _val_cache(rig)
    val = pl.read_parquet(path)
    damaged_rows = 7
    values = val["imb_top"].to_numpy().copy()
    values[:damaged_rows] = np.nan
    val = val.with_columns(pl.Series("imb_top", values))
    assert int(val["imb_top"].is_nan().sum()) == damaged_rows
    val.write_parquet(path)

    scored = _run(rig, MODE_VAL, VAL_HASH)
    assert scored.metrics["n_pred_missing"] == float(damaged_rows), (
        f"n_pred_missing={scored.metrics['n_pred_missing']} for "
        f"{damaged_rows} non-finite feature rows"
    )
    logged = _client(tracking_root).get_run(scored.run_id).data.metrics
    assert float(logged["n_pred_missing"]) == float(damaged_rows)
    assert float(logged["n_admitted"]) == float(val.height)
    # The ceiling is a property of the DATA and is unaffected by a broken
    # feature -- which is also why step 8 never re-materializes for it.
    assert int(scored.metrics["ceiling_closed_pnl_ticks"]) == int(
        perfect_foresight_ceiling(val, label_column=TARGET_NAME)["closed_pnl_ticks"]
    )


# --------------------------------------------------------------------------
# 13. The provenance rule THIS FIXTURE CANNOT SEE
# --------------------------------------------------------------------------


def test_only_the_train_days_feature_manifests_reach_the_normalisation_artifact():
    """`select_train_day_manifests` keeps the manifests whose rows lie inside
    the train window and derives `train_end_date` from the LAST of them --
    checked on hand-built bodies, because the fixture cannot check it at all.

    THE FIXTURE HAS EXACTLY ONE UPSTREAM FEATURE MANIFEST covering exactly one
    date, so "keep the five train days and exclude days 17 and 18" is
    INDISTINGUISHABLE on it from "keep everything". Replacing the whole filter
    with `manifest["upstream_feature_manifest_ids"]` would leave every other
    test in this file green while making the normalisation artifact assert that
    its parameters saw the two `val` days -- false, and false in precisely the
    record an auditor reads to decide whether the val look was honest. This is
    the test that is not vacuous, so it is a PURE one.

    THE FILTER IS ON THE ns CLOCK, NEVER ON THE DATE STRING, and the bodies
    below are built to make that visible: their `date` fields run 09-12..09-18
    while their `etime` values are plain integers. A date-string filter would
    still pass on them by accident; only the ns overlap gives the right answer
    when the two disagree, which is exactly the fixture's own condition (its
    partition is dated 2026-09-13 with etimes starting at epoch 0).
    """
    day_ns = 86_400 * 1_000_000_000
    bodies = [
        {
            "manifest_id": f"{index:064d}",
            "partitions": [
                {
                    "date": f"2026-09-{12 + index}",
                    "etime_min": index * day_ns,
                    "etime_max": (index + 1) * day_ns - 1,
                }
            ],
        }
        for index in range(7)
    ]
    kept, train_end_date = slice_module.select_train_day_manifests(
        bodies, train_start_ns=0, train_end_ns=5 * day_ns
    )
    assert [body["manifest_id"] for body in bodies[:5]] == list(kept)
    assert train_end_date == "2026-09-16"
    assert f"{5:064d}" not in kept and f"{6:064d}" not in kept, (
        "a val day's feature manifest reached the normalisation provenance"
    )

    # The two refusals, both of which fail CLOSED rather than guessing.
    with pytest.raises(SliceError, match="no upstream feature manifests"):
        slice_module.select_train_day_manifests(
            [], train_start_ns=0, train_end_ns=day_ns
        )
    with pytest.raises(SliceError, match="overlapping the train window"):
        slice_module.select_train_day_manifests(
            bodies, train_start_ns=99 * day_ns, train_end_ns=100 * day_ns
        )
    with pytest.raises(SliceError, match="no 'date' key"):
        slice_module.select_train_day_manifests(
            [
                {
                    "manifest_id": "a" * 64,
                    "partitions": [{"etime_min": 0, "etime_max": 9}],
                }
            ],
            train_start_ns=0,
            train_end_ns=day_ns,
        )
