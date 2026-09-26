"""The sweep on the fixture: every config scored on every block, one
winner, every loser recorded, and the winner carried across a process
boundary.

THE TWO TESTS THAT ARE NOT ABOUT PLUMBING.
`test_a_config_that_fails_a_gate_on_one_block_is_ineligible_even_with_a_good_mean`
hand-constructs the situation the all-blocks rule exists for -- a config
whose mean IC is THREE TIMES the field's best while one block's R-squared
is negative -- and asserts it does not win. The rest of this file could not
catch that: on the real fixture the only single-block failure carries a mean
IC of +0.167 against the winner's +0.198, so relaxing the rule to "any
block" would leave the winner unchanged and every other assertion here
still true.

`test_a_deliberately_terrible_config_loses_to_a_reasonable_one` is the
anti-vacuity counterpart, and it runs the grid BOTH WAYS ROUND. Asserting
only that the good config wins at position 0 would also be satisfied by a
sweep that returns grid position 0 unconditionally; reversing the grid and
watching the winner move to position 1 is what rules that out.

Every test here builds its own `tmp_path` lake, registry and MLflow
tracking root. Pre-commit hooks 18/19 run the full suite on every commit,
so a test that reached a canonical root would spend an irreversible
validation look once per commit forever (D-07-34).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import budget, negative_log
from models import cache, sweep
from models.gates import gate_forecast
from models.metrics import FORECAST_METRIC_KEYS
from models.regression import (
    GRID,
    GRID_SIZE,
    FEATURE_NAMES,
    ElasticNetTrainer,
    FitContext,
    GridEntry,
    RidgeTrainer,
)
from tests.fixtures import model_fit
from tests.fixtures.model_span import build_model_span_fixture

#: The same reduced span `tests/models/test_cache.py` uses: 6,600 rows and a
#: 6,000 s train entry, which keeps the FULL 17-config x 5-block sweep at
#: about 0.65 s including its MLflow negative-result runs. NO GRID REDUCTION
#: IS USED ANYWHERE IN THIS FILE -- measured, not assumed, so nobody later
#: believes 17 configs were exercised when 3 were.
RIG_ROWS: int = 6_600
RIG_TRAIN_ROWS: int = 6_000

SYMBOL: str = "BTCUSDT"
CODE_HASH: str = "a" * 40
SEED: int = 20260925

RUN_TAGS: dict[str, str] = {
    "code_hash": CODE_HASH,
    "data_hash": "none",
    "seed": str(SEED),
    "env_hash": "b" * 64,
    "model_class": "none (set per config by the sweep)",
}

#: A grid of exactly two configs for the anti-vacuity test: a Ridge that
#: learns, and an ElasticNet whose `alpha` is large enough relative to this
#: target's 1e-6 scale to zero every coefficient -- which makes its
#: prediction CONSTANT, which makes `spearmanr` return NaN, which is
#: `gate_forecast`'s first and distinctly-messaged refusal.
GOOD_CONFIG = GridEntry(RidgeTrainer, {"alpha": 1.0})
TERRIBLE_CONFIG = GridEntry(ElasticNetTrainer, {"alpha": 1e-2, "l1_ratio": 0.85})


def _prepared(tmp_path: Path, lake_root: Path, registry_root: Path, tracking_root):
    """The whole rig one sweep needs: a fixture lake, a `train` frame cached
    through `models.cache.materialize_once` (which costs NO look for a
    `train` role), and a real `features_norm` artifact fit over that very
    frame's feature columns -- train-only parameters, which is what D-07-11
    requires and what a real run does.
    """
    rig = build_model_span_fixture(
        lake_root,
        registry_root,
        tracking_root,
        rows=RIG_ROWS,
        train_rows=RIG_TRAIN_ROWS,
        k=5,
    )
    manifest_id = rig["manifest"]["manifest_id"]
    cache_root = tmp_path / "scratch"
    train = cache.materialize_once(
        manifest_id,
        "train",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
        cache_root=cache_root,
    )
    normalization_manifest_id = model_fit.write_normalization(
        model_fit.measured_params(
            {
                name: train[name].fill_null(float("nan")).to_numpy()
                for name in FEATURE_NAMES
            }
        ),
        lake_root=lake_root,
        registry_root=registry_root,
        train_row_count=train.height,
    )
    return {
        "manifest_id": manifest_id,
        "block_names": list(rig["geometry"]["oof_block_names"]),
        "cache_root": cache_root,
        "train_cache_path": cache.segment_cache_path(
            cache_root, tracking_root, manifest_id, "train"
        ),
        "normalization_manifest_id": normalization_manifest_id,
        "train_rows": train.height,
    }


def _sweep(prepared, *, lake_root, registry_root, tracking_root, **overrides):
    kwargs = {
        "oof_block_names": prepared["block_names"],
        "train_cache_path": prepared["train_cache_path"],
        "registry_root": registry_root,
        "lake_root": lake_root,
        "tracking_root": str(tracking_root),
        "cache_root": prepared["cache_root"],
        "run_tags": dict(RUN_TAGS),
        "normalization_manifest_id": prepared["normalization_manifest_id"],
        "symbol": SYMBOL,
        "code_hash": CODE_HASH,
        "seed": SEED,
    }
    kwargs.update(overrides)
    return sweep.run_oof_sweep(prepared["manifest_id"], **kwargs)


def _metrics(*, ic: float, r2_vs_zero: float, r2_vs_mean: float) -> dict[str, float]:
    """A full `forecast_metrics`-shaped dict, so `gate_forecast` is the thing
    judging these numbers rather than the test's own opinion of them."""
    body = {
        "n_scorable": 1_200,
        "r2_vs_zero": r2_vs_zero,
        "r2_vs_mean": r2_vs_mean,
        "rank_ic_all": ic,
        "rank_ic_non_tied": ic,
        "tie_fraction": 0.14,
        "r2_vs_zero_of_constant_train_mean": -0.003,
    }
    assert sorted(body) == sorted(FORECAST_METRIC_KEYS)
    return body


def _block_score(name: str, metrics: dict[str, float]) -> sweep.BlockScore:
    passed, reason = gate_forecast(metrics)
    return sweep.BlockScore(
        segment_name=name,
        metrics=metrics,
        gate_passed=passed,
        gate_reason=reason,
        n_rows_fitted=3_000,
        n_rows_dropped=0,
        fit_seconds=0.01,
    )


def test_the_sweep_scores_every_config_on_every_block_and_names_one_winner(
    tmp_path, lake_root, registry_root, tracking_root
):
    """The full 17-config grid, five per-block metric rows each, one winner.

    `len(GRID) == 17` is asserted explicitly and the sweep is handed no
    `grid=` override, so the count in the result is the count in the module
    literal and not a reduction somebody forgot to mention.
    """
    assert len(GRID) == GRID_SIZE == 17
    prepared = _prepared(tmp_path, lake_root, registry_root, tracking_root)
    result = _sweep(
        prepared,
        lake_root=lake_root,
        registry_root=registry_root,
        tracking_root=tracking_root,
    )

    assert result.n_configs == 17
    assert len(result.configs) == 17
    assert [config.grid_index for config in result.configs] == list(range(17))
    for config in result.configs:
        assert len(config.blocks) == 5
        assert [block.segment_name for block in config.blocks] == prepared[
            "block_names"
        ]
        for block in config.blocks:
            assert sorted(block.metrics) == sorted(FORECAST_METRIC_KEYS)
            assert block.n_rows_fitted > 0

    # Distinct fingerprints, all 17: two configs sharing one would share one
    # negative-result record and D-07-20 would be false.
    assert len({config.config_fingerprint for config in result.configs}) == 17

    assert result.has_winner
    winner = result.winner
    assert winner.eligible and winner.grid_index in result.eligible_grid_indices
    assert winner.mean_rank_ic_non_tied > 0.0
    # The spread is reported beside the mean, and is a real spread on this
    # fixture rather than five copies of one number.
    assert winner.ic_min < winner.mean_rank_ic_non_tied < winner.ic_max
    assert winner.ic_std > 0.0
    # The winner is the best of the eligible field, by the rule.
    best = max(
        (c for c in result.configs if c.eligible),
        key=lambda c: (c.mean_rank_ic_non_tied, c.mean_r2_vs_mean, -c.grid_index),
    )
    assert best.grid_index == winner.grid_index
    # Anti-vacuity: the field is genuinely split, so "eligible" is doing work.
    assert 0 < result.eligible_count < 17

    # Exactly one look per block, and none on val.
    for name in prepared["block_names"]:
        assert (
            budget.look_count(
                prepared["manifest_id"], name, tracking_root=str(tracking_root)
            )
            == 1
        )
    assert (
        budget.look_count(
            prepared["manifest_id"], "val", tracking_root=str(tracking_root)
        )
        == 0
    )


def test_a_config_that_fails_a_gate_on_one_block_is_ineligible_even_with_a_good_mean(
    tmp_path,
):
    """Rule 1, on hand-built numbers, because the fixture cannot pose this
    case sharply enough.

    `hopeful` carries a rank IC of +0.30 on all five blocks -- THREE TIMES
    `steady`'s +0.10 -- and fails one block only on `r2_vs_mean`, i.e. it
    ordered that day's rows well while explaining less than the
    unconditional mean. Averaged over five blocks that failure is invisible;
    under the all-blocks rule it disqualifies.

    The mean is deliberately the HIGHER one, so the two outcomes differ:
    with `all`, `steady` wins at grid position 0; with `any`, `hopeful`
    becomes eligible and its bigger mean takes it. That is what makes this
    test able to see the rule at all.
    """
    good = _metrics(ic=0.10, r2_vs_zero=0.010, r2_vs_mean=0.010)
    steady = sweep.summarise_config(
        grid_index=0,
        model_class="sklearn.Ridge",
        hyperparameters={"alpha": 1.0},
        config_fingerprint="0" * 64,
        blocks=[_block_score(f"oof_block_{j}", dict(good)) for j in range(5)],
    )
    strong = _metrics(ic=0.30, r2_vs_zero=0.020, r2_vs_mean=0.020)
    # One block: the ordering is still excellent, the conditional
    # explanation is not. `gate_forecast`'s third check is what sees it.
    one_bad = _metrics(ic=0.30, r2_vs_zero=0.005, r2_vs_mean=-0.010)
    hopeful_blocks = [_block_score(f"oof_block_{j}", dict(strong)) for j in range(4)]
    hopeful_blocks.append(_block_score("oof_block_4", dict(one_bad)))
    hopeful = sweep.summarise_config(
        grid_index=1,
        model_class="sklearn.ElasticNet",
        hyperparameters={"alpha": 1e-6, "l1_ratio": 0.5},
        config_fingerprint="1" * 64,
        blocks=hopeful_blocks,
    )

    # Vacuity guards: four of five blocks really do pass, and the mean really
    # is the better one. Without both, the assertion below proves nothing.
    assert sum(1 for block in hopeful.blocks if block.gate_passed) == 4
    assert hopeful.mean_rank_ic_non_tied > steady.mean_rank_ic_non_tied

    assert steady.eligible is True
    assert hopeful.eligible is False, (
        "a config that failed one block is eligible -- a mean that hides a "
        "failing fold is how an unstable model wins"
    )
    winner_index, eligible = sweep.select_winner([steady, hopeful])
    assert eligible == (0,)
    assert winner_index == 0, (
        "the one-bad-block config won on its mean; the all-blocks rule is not "
        "being applied"
    )


def test_every_ineligible_config_appears_in_the_negative_result_log_with_a_reason_naming_the_block(  # noqa: E501
    tmp_path, lake_root, registry_root, tracking_root
):
    """D-07-20: one record per ineligible config, queryable back, with a
    reason naming the failing block and the failing statistic.

    ONE record per CONFIG, never one per failing block -- a config that
    fails three blocks was tried once, and five records for it would make
    the log's counts meaningless. The fingerprint is D-07-14's recipe, so a
    record matches back to the `predictor_id` a later successful run of the
    same config would carry.
    """
    prepared = _prepared(tmp_path, lake_root, registry_root, tracking_root)
    result = _sweep(
        prepared,
        lake_root=lake_root,
        registry_root=registry_root,
        tracking_root=tracking_root,
    )
    ineligible = [config for config in result.configs if not config.eligible]
    assert ineligible, "vacuous: every config passed, so nothing was recorded"

    rows = negative_log.query_negative_results(tracking_root=str(tracking_root))
    assert len(rows) == len(ineligible), (
        f"{len(rows)} negative records for {len(ineligible)} ineligible "
        "configs -- a config that fails three blocks must leave ONE trace"
    )
    recorded = {fingerprint for fingerprint, *_rest in rows}
    assert recorded == {config.config_fingerprint for config in ineligible}

    for config in ineligible:
        assert config.negative_run_id is not None
        matched = negative_log.query_negative_results(
            tracking_root=str(tracking_root),
            config_fingerprint=config.config_fingerprint,
        )
        assert len(matched) == 1
        _fingerprint, reason, _when, run_id, code_hash, _data_hash = matched[0]
        assert run_id == config.negative_run_id
        assert code_hash == CODE_HASH
        failing = config.first_failing_block()
        assert failing is not None
        assert failing.segment_name in reason, reason
        assert "gate_forecast" in reason, reason
        # The failing STATISTIC, not merely the fact of failure: the gate's
        # own message names either the R-squared it measured or that the IC
        # was not finite at all.
        assert ("r2_vs" in reason) or ("rank IC is not finite" in reason), reason
        assert len(reason) <= 5_000

    # Every eligible config is absent from the log -- the converse claim.
    for config in result.configs:
        if config.eligible:
            assert config.negative_run_id is None
            assert (
                negative_log.query_negative_results(
                    tracking_root=str(tracking_root),
                    config_fingerprint=config.config_fingerprint,
                )
                == []
            )


def test_a_deliberately_terrible_config_loses_to_a_reasonable_one(
    tmp_path, lake_root, registry_root, tracking_root
):
    """The anti-vacuity test, run BOTH WAYS ROUND.

    Without the reversal a sweep that returned grid position 0
    unconditionally would satisfy every other assertion in this file. With
    it, the winner has to MOVE when the grid moves, which no constant can
    do.

    The terrible config is terrible for a measured reason: `alpha=1e-2`
    against a target of order 1e-6 zeroes every coefficient, the prediction
    becomes constant, and `spearmanr` of a constant is NaN -- which is
    `gate_forecast`'s first and most distinctly-messaged refusal.
    """
    prepared = _prepared(tmp_path, lake_root, registry_root, tracking_root)
    forward = _sweep(
        prepared,
        lake_root=lake_root,
        registry_root=registry_root,
        tracking_root=tracking_root,
        grid=(GOOD_CONFIG, TERRIBLE_CONFIG),
    )
    assert forward.n_configs == 2
    assert forward.eligible_grid_indices == (0,)
    assert forward.winner_grid_index == 0
    assert forward.winner.model_class == "sklearn.Ridge"
    terrible = forward.configs[1]
    assert not terrible.eligible
    assert "rank IC is not finite" in (terrible.first_failing_block() or "").gate_reason

    reversed_result = _sweep(
        prepared,
        lake_root=lake_root,
        registry_root=registry_root,
        tracking_root=tracking_root,
        grid=(TERRIBLE_CONFIG, GOOD_CONFIG),
    )
    assert reversed_result.eligible_grid_indices == (1,)
    assert reversed_result.winner_grid_index == 1, (
        "the winner did not move when the grid moved -- this sweep is naming "
        "a position, not a config"
    )
    assert reversed_result.winner.model_class == "sklearn.Ridge"
    assert (
        reversed_result.winner.config_fingerprint == forward.winner.config_fingerprint
    )

    # The second and third sweeps re-read the cached block frames, so the
    # whole test still costs exactly one look per block.
    for name in prepared["block_names"]:
        assert (
            budget.look_count(
                prepared["manifest_id"], name, tracking_root=str(tracking_root)
            )
            == 1
        )


def test_no_eligible_config_is_an_explicit_outcome_and_not_a_silent_best_available(
    tmp_path, lake_root, registry_root, tracking_root
):
    """Rule 5. A grid of nothing but dead configs produces a RESULT that says
    so, in three places that must agree: `winner_grid_index is None`,
    `SweepResult.winner` raising, and `selection.json`'s own
    `winner_grid_index: null` making `selection_winner_trainer` raise the
    same way across the process boundary.

    The one thing that must NOT happen is a fallback to the best of the
    failures: it has not earned the phase's single honest `val` look.
    """
    prepared = _prepared(tmp_path, lake_root, registry_root, tracking_root)
    dead = (
        GridEntry(ElasticNetTrainer, {"alpha": 1e-2, "l1_ratio": 0.85}),
        GridEntry(ElasticNetTrainer, {"alpha": 1e-2, "l1_ratio": 0.5}),
    )
    result = _sweep(
        prepared,
        lake_root=lake_root,
        registry_root=registry_root,
        tracking_root=tracking_root,
        grid=dead,
    )
    assert result.eligible_count == 0
    assert result.winner_grid_index is None
    assert result.has_winner is False
    with pytest.raises(sweep.NoEligibleConfigError) as excinfo:
        result.winner
    assert "not a malfunction" in str(excinfo.value)

    body = sweep.read_selection(
        prepared["cache_root"],
        tracking_root,
        prepared["manifest_id"],
        normalization_manifest_id=prepared["normalization_manifest_id"],
    )
    assert body["winner_grid_index"] is None
    assert body["winner"] is None
    assert body["eligible_count"] == 0
    context = FitContext(
        symbol=SYMBOL,
        lake_root=lake_root,
        registry_root=registry_root,
        code_hash=CODE_HASH,
    )
    with pytest.raises(sweep.NoEligibleConfigError):
        sweep.selection_winner_trainer(body, context=context, grid=dead)


def test_the_winner_is_the_same_across_two_runs_of_the_same_sweep(
    tmp_path, lake_root, registry_root, tracking_root
):
    """Determinism of SELECTION, which is a different claim from determinism
    of the fit.

    The second run reads the same cached frames and re-fits with the same
    seed against the same BLAS, so what is being asserted is that the rule
    itself carries no ordering dependence -- a `max` over a set, or a dict
    iteration order, would show up here.
    """
    prepared = _prepared(tmp_path, lake_root, registry_root, tracking_root)
    first = _sweep(
        prepared,
        lake_root=lake_root,
        registry_root=registry_root,
        tracking_root=tracking_root,
    )
    second = _sweep(
        prepared,
        lake_root=lake_root,
        registry_root=registry_root,
        tracking_root=tracking_root,
    )
    assert first.winner_grid_index == second.winner_grid_index
    assert first.eligible_grid_indices == second.eligible_grid_indices
    assert first.winner.mean_rank_ic_non_tied == second.winner.mean_rank_ic_non_tied
    assert first.winner.config_fingerprint == second.winner.config_fingerprint
    # And it cost no second look: the five caches hit.
    for name in prepared["block_names"]:
        assert (
            budget.look_count(
                prepared["manifest_id"], name, tracking_root=str(tracking_root)
            )
            == 1
        )


def test_the_selection_json_round_trips_the_winner_and_refuses_a_mismatched_manifest_id(
    tmp_path, lake_root, registry_root, tracking_root
):
    """The cross-process handoff, and its three refusals.

    `--select` and `--freeze` are separate processes with a commit between
    them, so this file is the ONLY thing that survives. It has to rebuild the
    winning config exactly, and it has to refuse rather than guess when
    either manifest id it carries disagrees with the live one -- a winner
    chosen on another fold geometry, or applied against another set of
    z-score parameters, predicts plausible numbers from the wrong world.
    """
    prepared = _prepared(tmp_path, lake_root, registry_root, tracking_root)
    result = _sweep(
        prepared,
        lake_root=lake_root,
        registry_root=registry_root,
        tracking_root=tracking_root,
    )
    path = sweep.selection_path(
        prepared["cache_root"], tracking_root, prepared["manifest_id"]
    )
    assert path == result.selection_path and path.is_file()
    # Outside the lake and outside the repo, like the frame cache beside it.
    assert not path.is_relative_to(lake_root)
    assert not path.is_relative_to(Path(__file__).resolve().parents[3])

    body = sweep.read_selection(
        prepared["cache_root"],
        tracking_root,
        prepared["manifest_id"],
        normalization_manifest_id=prepared["normalization_manifest_id"],
    )
    assert body["n_configs"] == 17
    assert body["winner_grid_index"] == result.winner_grid_index
    assert body["eligible_count"] == result.eligible_count
    assert body["block_names"] == prepared["block_names"]
    assert body["segment_manifest_id"] == prepared["manifest_id"]
    assert body["normalization_manifest_id"] == prepared["normalization_manifest_id"]
    # The five look run ids, which cannot be carried in memory: `materialize`
    # returns a frame and drops the id `record_look` handed it.
    assert sorted(body["look_run_ids"]) == prepared["block_names"]
    for name in prepared["block_names"]:
        assert len(body["look_run_ids"][name]) == 1
        assert body["look_run_ids"][name] == list(result.look_run_ids[name])
    # Per-block metrics for EVERY config, winner and loser alike -- scalars
    # only, no prediction table anywhere.
    assert len(body["configs"]) == 17
    assert all(len(config["blocks"]) == 5 for config in body["configs"])

    context = FitContext(
        symbol=SYMBOL,
        lake_root=lake_root,
        registry_root=registry_root,
        code_hash=CODE_HASH,
    )
    rebuilt = sweep.selection_winner_trainer(body, context=context)
    assert rebuilt.model_class == result.winner.model_class
    assert dict(rebuilt.hyperparameters) == dict(result.winner.hyperparameters)

    # Refusal 1: the segment manifest id inside disagrees with the one the
    # path encodes. Only a hand-edit or a copy between directories can do
    # this, and both mean the selection was made on another fold geometry.
    original = path.read_text()
    tampered = json.loads(original)
    tampered["segment_manifest_id"] = "f" * 64
    path.write_text(json.dumps(tampered))
    with pytest.raises(sweep.SelectionError) as excinfo:
        sweep.read_selection(
            prepared["cache_root"],
            tracking_root,
            prepared["manifest_id"],
            normalization_manifest_id=prepared["normalization_manifest_id"],
        )
    assert "segment_manifest_id" in str(excinfo.value)
    # Restored, so the next refusal is provoked by the next thing and not by
    # the leftovers of this one.
    path.write_text(original)

    # Refusal 2: the normalisation artifact moved under the winner's feet.
    with pytest.raises(sweep.SelectionError) as excinfo:
        sweep.read_selection(
            prepared["cache_root"],
            tracking_root,
            prepared["manifest_id"],
            normalization_manifest_id="e" * 64,
        )
    assert "normalization_manifest_id" in str(excinfo.value)

    # Refusal 3: absent. Named as the remedy, not as a missing file.
    path.unlink()
    with pytest.raises(sweep.SelectionError) as excinfo:
        sweep.read_selection(
            prepared["cache_root"],
            tracking_root,
            prepared["manifest_id"],
            normalization_manifest_id=prepared["normalization_manifest_id"],
        )
    assert "--select" in str(excinfo.value)


def test_the_sweep_refuses_a_segment_name_that_is_not_an_oof_block(
    tmp_path, lake_root, registry_root, tracking_root
):
    """D-07-04 as a mechanical refusal, not as a convention.

    `val` is refused BEFORE the manifest is even read, so the refusal cannot
    cost a look on the way to happening -- which is asserted, because a
    refusal that spent what it was refusing to spend would be worse than no
    refusal at all.
    """
    prepared = _prepared(tmp_path, lake_root, registry_root, tracking_root)
    for names in (["val"], ["oof_block_0", "val"], ["held_out"], ["oof_block"]):
        with pytest.raises(sweep.SweepError) as excinfo:
            _sweep(
                prepared,
                lake_root=lake_root,
                registry_root=registry_root,
                tracking_root=tracking_root,
                oof_block_names=names,
            )
        assert "D-07-04" in str(excinfo.value), names
    with pytest.raises(sweep.SweepError):
        _sweep(
            prepared,
            lake_root=lake_root,
            registry_root=registry_root,
            tracking_root=tracking_root,
            oof_block_names=[],
        )
    # A well-formed name that this manifest does not carry is a different
    # refusal, and it must not be the D-07-04 one.
    with pytest.raises(sweep.SweepError) as excinfo:
        _sweep(
            prepared,
            lake_root=lake_root,
            registry_root=registry_root,
            tracking_root=tracking_root,
            oof_block_names=["oof_block_9"],
        )
    assert "not oof_block entries" in str(excinfo.value)

    for name in ("val", *prepared["block_names"]):
        assert (
            budget.look_count(
                prepared["manifest_id"], name, tracking_root=str(tracking_root)
            )
            == 0
        ), f"a refused sweep spent a look on {name}"
    assert not sweep.selection_path(
        prepared["cache_root"], tracking_root, prepared["manifest_id"]
    ).exists()


def test_no_prediction_table_is_written_during_a_sweep(
    tmp_path, lake_root, registry_root, tracking_root
):
    """D-07-15: OOF selection persists SCALARS, never a table.

    The lake and the registry are snapshotted AFTER the rig is built (the
    normalisation artifact is a legitimate write and belongs to the setup,
    not to the sweep) and compared file by file afterwards. Storing four
    estimators' tables on every segment would approach a gigabyte for no
    downstream reader, which is the massive-data production this session is
    told to avoid.

    Everything the sweep does write -- five row masks and `selection.json` --
    lands in the tracking-root-keyed scratch tree, which is outside both the
    lake and the repo.
    """
    prepared = _prepared(tmp_path, lake_root, registry_root, tracking_root)

    def snapshot(root: Path) -> set[Path]:
        return {path for path in Path(root).rglob("*") if path.is_file()}

    lake_before = snapshot(lake_root)
    registry_before = snapshot(registry_root)
    assert lake_before, "vacuous: the fixture lake is empty"

    result = _sweep(
        prepared,
        lake_root=lake_root,
        registry_root=registry_root,
        tracking_root=tracking_root,
    )

    assert snapshot(lake_root) - lake_before == set(), (
        "the sweep wrote into the lake -- D-07-15 stores only the one table "
        "that feeds the simulator, and that is not this one"
    )
    assert snapshot(registry_root) - registry_before == set()
    # No `predictions` dataset came into existence anywhere under either root.
    assert not list(Path(lake_root).rglob("*predictions*"))
    assert not list(Path(registry_root).rglob("*predictions*"))

    scratch = {
        path.name
        for path in prepared["cache_root"].rglob("*")
        if path.is_file() and path.suffix in (".parquet", ".json")
    }
    assert "selection.json" in scratch
    assert sum(1 for name in scratch if name.startswith("row_mask_")) == 5
    assert result.selection_path.is_file()
    assert not prepared["cache_root"].is_relative_to(lake_root)
