---
phase: 07-regression-track-vertical-slice
plan: 08
subsystem: models
tags: [frame-cache, look-budget, oof-sweep, model-selection, negative-result-log, selection-json]

# Dependency graph
requires:
  - phase: 07-06
    provides: "models/regression.py -- the counted 17-config GRID, GridEntry.build, FitContext, ROW_MASK_COLUMN, validate_feature_names, and the four trainers behind the Trainer protocol"
  - phase: 07-07
    provides: "models/metrics.py forecast_metrics(pred, y, *, train_mean) and models/gates.py gate_forecast -- the seven scalars and the four-check verdict the sweep ranks on"
  - phase: 07-05
    provides: "models/predictions.py assert_decision_order -- run on every frame the cache writes and every frame it reads back"
  - phase: 07-04
    provides: "models/frozen.py -- FrozenLinearPredictor.train_target_mean (the zero-skill control the metrics need) and n_rows_fitted/n_rows_dropped as BODY fields, which is why they are absent from the hashed recipe"
  - phase: 07-03
    provides: "models/protocol.py FitInputs (a cache PATH plus a row-mask path, never arrays) and models/predictor_id.py RECIPE_FIELDS"
  - phase: 05-fold-harness
    provides: "harness/accessor.py materialize (the only door to a look), harness/budget.py look_count + record_look + _FILTER_SAFE_RE, harness/kfold.py training_rows_for_block, harness/negative_log.py's four functions, harness/segments.py read_segment_manifest"
provides:
  - "models/cache.py -- materialize_once (at most one accessor call per segment per TRACKING ROOT), segment_cache_path/segment_cache_dir, tracking_root_digest, look_report, look_run_ids, CACHE_ROOT, CacheError"
  - "models/sweep.py -- run_oof_sweep, the pure summarise_config/select_winner pair that owns the selection rule, SweepResult/ConfigScore/BlockScore, config_recipe, and the selection.json writer plus its reader (read_selection, selection_winner_trainer, selection_path)"
  - "the selection rule itself, written down in models/sweep.py's docstring for Phase 8's cross-class protocol to inherit"
affects: [07-09, 07-10, 07-11, "Phase 8's FCST-05 hyperparameter search", "Phase 8's cross-model-class selection"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Address the artifact so the wrong reader CANNOT reach it, rather than checking that it did not. The tracking root's sha256 is a path COMPONENT of every cached frame, so a scratch-root cache is unreachable from a canonical-root run -- unreachable needs no check to fire, unlike a guard that has to be called"
    - "A required keyword with no default as a structural defence, extended from 07-07's train_mean to a WRITE path: `cache_root` has no module-level default because a test that forgot it would write fixture frames into the real scratch tree once per commit under hooks 18/19, and that canonical path is exactly where a later real run looks for a frame it believes it paid for"
    - "Confine a decision in the SIGNATURE, not in the caller's discipline: run_oof_sweep has no parameter through which `val` could be named (the train frame arrives as an already-cached path) and refuses any segment name that is not oof_block_<int>. D-07-04 becomes a property of the type, and the plan's own verification is a one-line inspect.signature check"
    - "Factor the rule a test must see into a PURE function. summarise_config/select_winner take metric dicts and return a verdict with no MLflow, no lake and no fit, which is what lets a hand-built five-block case check the all-blocks rule directly -- and what makes the all->any mutation a single token in a single function"
    - "Cross-check a copied constant in the TEST, not at import. models/cache.py copies budget's _FILTER_SAFE_RE per the repo's private-helper-copying norm, and test_cache.py asserts the two patterns are byte-identical -- a drift check that lives where reaching into another module's private name is legitimate"
    - "Assert an import-time agreement with a DEFAULT ARGUMENT you are documenting: LOOK_EXPERIMENT_NAME is checked against inspect.signature(budget.record_look).parameters['experiment_name'].default, so a docstring that names the wrong experiment to an operator chasing a look cannot survive a rename"
    - "Store a non-finite metric as JSON null, never as a bare NaN literal. json.dumps emits NaN by default and no strict parser accepts it -- and this file is read by a later process at a later commit, which is exactly when that bites. Nothing is lost: the gate refuses a non-finite IC, so every number on the winner's row is finite"

key-files:
  created:
    - mvp/models/cache.py
    - mvp/models/sweep.py
    - mvp/tests/models/test_cache.py
    - mvp/tests/models/test_sweep.py
  modified: []

key-decisions:
  - "The cache-key mutation was caught one assertion EARLIER than predicted, and that is a near-miss worth recording. The stated observable was 'look_count under root B stays 0'; the test failed on `path_b != path_a` instead, so the budget consequence was never exercised by the test run. It was then measured directly against the mutated module: root B received the val frame with ZERO materialize calls and look_count(B) = 0, and against the restored module the same script shows 2 calls and look_count(B) = 1. If somebody later deletes the path-inequality assertion, the look_count assertion behind it does still bite -- but only the standalone measurement proves that, not the mutation run."
  - "run_oof_sweep takes the TRAIN CACHE AS A PATH rather than a train segment name. A `train_segment_name=\"train\"` parameter would have been the obvious shape and it would have been a hole: `val` could be passed through it. With the path, the only route from a caller to a segment name is `oof_block_names`, every entry of which is refused unless it matches oof_block_<int>. The path is additionally required to sit inside this manifest's own tracking-root-keyed cache directory, which is what stops a train frame carrying ANOTHER fold geometry from being fitted on."
  - "One negative-result record per ineligible CONFIG, never one per failing block, and the fingerprint is D-07-14's recipe alone. A config that fails three blocks was tried once; five records for it would make the log's counts meaningless. n_rows_fitted/n_rows_dropped differ per block by construction, so folding either into the hashed body would give one config five fingerprints and warn_if_already_negative would stop deduplicating anything. config_recipe ASSERTS config_fingerprint(recipe) == predictor_id(**recipe), which is what makes a negative record matchable to the predictor_id a later successful run of the same config will carry."
  - "The loop is BLOCK-OUTER, CONFIG-INNER, against the plan's stated nesting, for a measured reason: each block's frame is materialized once and its scoring arrays built once, and the 17 consecutive fits then read the same train cache while it is warm. Config-outer reads each block's columns 17 times for identical arithmetic. Results are collected by (grid_index, block) and reduced afterwards, so the loop order is invisible in the output."
  - "selection.json is written even when NOTHING is eligible, with winner_grid_index: null. The five looks were paid for and their run ids deserve durability, and `--freeze`'s reader then refuses with a specific 'no eligible config' rather than the ambiguous 'file absent'. NoEligibleConfigError is raised identically by SweepResult.winner (in memory) and selection_winner_trainer (across the commit), so the two paths cannot disagree."
  - "selection_winner_trainer BUILDS the trainer and compares its own model_class and hyperparameters to the stored pair, rather than comparing GridEntry.knobs. The poly2 trainer's `degree` lives in hyperparameters and not in knobs, and `degree` is the only thing separating three of its configs from three plain-Ridge ones at the same alpha -- a knobs comparison would call them equal. This is also why both the index AND the recipe are stored: a GRID reordered in the commit between --select and --freeze would leave the index pointing at a different config with nothing about the index looking wrong."
  - "The masks come from harness.kfold.training_rows_for_block via np.isin on the cached etime, with the kept count asserted against the filtered frame's own height and BOTH numbers in the message. Not a join: polars documents join output order as unspecified and a silently reordered training set is this phase's top risk (T-07-17). The np.isin conversion is legal precisely because assert_decision_order has already proven etime strictly ascending, hence unique, on that very cache."
  - "No grid reduction anywhere in the tests. The full 17x5 sweep with its 8 MLflow negative-result runs takes 0.65 s on a 6,600-row span, so the plan's permission to reduce was not needed and nobody can later believe 17 configs were exercised when 3 were."

patterns-established:
  - "Pattern: when a mutation is caught by an earlier assertion than the one you predicted, measure the predicted observable SEPARATELY against the mutated module. The mutation run proves the test file bites; only the standalone measurement proves the failure mode is the one you described."
  - "Pattern: give the hand-built counter-case a BETTER mean than the honest config, not a worse one. The all-blocks rule can only be seen by a case where the two rules name DIFFERENT winners; the fixture's own single-block failure carries mean IC +0.167 against the winner's +0.198, so relaxing all->any leaves the winner unchanged and every fixture assertion still true. Measured: that mutation failed exactly one test, the hand-built one."
  - "Pattern: run the grid BOTH WAYS ROUND for anti-vacuity. Asserting the good config wins at position 0 is also satisfied by a sweep that returns position 0 unconditionally; watching the winner move to position 1 when the grid reverses is what rules that out."
  - "Anti-pattern recorded: writing a tampered artifact in a test and then testing the NEXT refusal without restoring it. The normalization-id refusal silently fired the segment-id refusal instead, and the test passed its `pytest.raises` while asserting on the wrong message -- caught only because the message assertion was specific."

# Metrics
duration: "~1.7 h"
completed: 2026-09-26
---

# Phase 7 Plan 8: Materialize-once cache, the OOF sweep, and the negative-result log Summary

A segment's rows are now read from the tier at most once per tracking root, the winner is chosen on five blocks that the sweep's own signature makes it incapable of confusing with `val`, and the choice survives the commit that separates choosing from freezing.

## What was built

`mvp/models/cache.py` (452 lines) turns one `harness.accessor.materialize` call into a Parquet under a scratch tree, and never calls the accessor again for that `(segment manifest, segment, tracking root)` triple. `mvp/models/sweep.py` (964 lines) fits all 17 configs on all five OOF blocks' own training rows, scores each on its block, records every loser, ranks the survivors by a rule stated in the module docstring, and writes `selection.json`.

## The cache key, and the bypass it closes

    cache_root / sha256(resolved tracking_root)[:16] / <segment_manifest_id> / <segment>.parquet

The tracking-root digest is the load-bearing component. A `val` frame materialized during a dry run against a scratch MLflow store is byte-identical to the one a real run would get and it cost nothing, because `record_look` wrote its run into the scratch store. Keyed on the manifest alone, that file would be readable by the real run: the honest look would never have been spent while the canonical `look_count`, the manifest's `budget_allowance` and the `harness-looks` experiment all read as though it had. Neither existing control can see it — `tools/check_harness_accessor_only.py` is static and a cache hit bypasses nothing it looks for; `record_look` is runtime and a cache hit is a call that never reaches it.

**Proof that a scratch-root cache cannot be read under the canonical root.** Measured twice, once against a deliberately mutated module and once against the real one, with two initialised `tmp_path` stores A and B and a counter wrapped around `materialize`:

| `segment_cache_dir` | after materializing `val` under A | after asking for `val` under B |
|---|---|---|
| `cache_root / manifest_id` (mutated) | 1 call, `look_count(A)=1`, `look_count(B)=0` | **1 call**, `look_count(B)=0` — bypassed |
| `cache_root / digest / manifest_id` (shipped) | 1 call, `look_count(A)=1`, `look_count(B)=0` | **2 calls**, `look_count(B)=1` — closed |

`cache_root` is a required keyword with no default anywhere in either module. `CACHE_ROOT` names the canonical location `/Volumes/ProjectsSSD/aihedgefund/scratch/phase07` for a CLI to pass explicitly, and `canonical_cache_root_is_outside_repo_and_lake()` derives the repo root from `lake_paths.PKG_ROOT.parent` and the lake from the `DEFAULT_LAKE_ROOT` constant (never through `lake_root()`, which would `mkdir` the real lake as a side effect of answering a question). That directory does not exist on this machine and nothing in this plan created it.

## `selection.json`: the round trip, the refusals, and the run ids

Written atomically (`.tmp` then `replace`) into the same tracking-root-keyed directory, 80,039 bytes for 17 configs × 5 blocks on the fixture — and the same size on real data, because it holds only scalars.

It carries `segment_manifest_id`, `normalization_manifest_id`, `fold_config`, `symbol`, `block_names`, `n_configs`, `eligible_count`, `eligible_grid_indices`, `winner_grid_index`, the winner's `model_class`/`hyperparameters`/`config_fingerprint`/mean IC/IC spread, every config's per-block metrics and gate reasons, and **`look_run_ids`** — five names, one run id each, verified equal to `budget.look_count` per segment inside `look_run_ids` itself. Those ids exist in the file because they cannot be carried in memory: `record_look` returns a run id, `materialize` returns only a frame and drops it, and the run that must cite them opens in a later process at a later commit.

`read_selection` refuses four ways, each with its own message: absent (named as the remedy, "run --select against THIS tracking root", not as a missing file), wrong `schema_version`, `segment_manifest_id` disagreeing with the one the path encodes, and `normalization_manifest_id` disagreeing with the live artifact. `selection_winner_trainer` then rebuilds the winner and refuses if `n_configs` or the stored recipe disagrees with the live grid. All four refusals and the successful rebuild are tested.

## The sweep on the fixture: 9 of 17 eligible

6,600-row span, 6,000 s train entry, five 1,200 s blocks. The full grid in 0.65 s including eight MLflow negative-result runs.

| grid | config | verdict | mean IC |
|---|---|---|---|
| 0 | LinearRegression | eligible | +0.197482 |
| 1–4 | Ridge α ∈ {1e-6, 1e-3, 1.0, 100.0} | eligible | +0.197482 … **+0.197512** |
| 5 | ElasticNet α=1e-6 l1=0.15 | eligible | +0.193189 |
| 6 | ElasticNet α=1e-6 l1=0.5 | **ineligible @ oof_block_3** | +0.167211 |
| 7–13 | ElasticNet, seven remaining | **ineligible @ oof_block_0** (5 of 5 blocks) | NaN |
| 14–16 | Ridge+poly2 α ∈ {1e-3, 1.0, 100.0} | eligible | +0.191248 … +0.191294 |

Winner: **grid index 4, `sklearn.Ridge` α=100.0, mean `rank_ic_non_tied` +0.197512**, spread min +0.170685 / max +0.228863 / std 0.022947. The four Ridge rows sit within 3e-5 of each other, which is exactly why the third tie-break key (ascending grid position) is in the rule rather than left to iteration order.

Config 6 is the case the all-blocks rule exists for: positive rank IC on every block, and `r2_vs_zero = -0.00732351` on `oof_block_3` alone. Configs 7–13 are the other failure shape: `alpha` large enough against this target's 1e-6 scale to zero every coefficient, so the prediction is constant, `spearmanr` returns NaN, and `gate_forecast`'s first and distinctly-messaged check refuses it.

**"No eligible config" does this:** `eligible_count == 0`, `winner_grid_index is None`, `has_winner is False`, `SweepResult.winner` raises `NoEligibleConfigError` naming it a result and not a malfunction, `selection.json` is still written (the looks were paid for) with `winner: null`, and `selection_winner_trainer` raises the same error across the process boundary. Tested with a two-entry grid of dead ElasticNets. No path falls back to best-available.

## Every ineligible config, queryable back

Eight records for eight ineligible configs — one each, never one per failing block. `query_negative_results(tracking_root=...)` returns exactly eight rows; querying by each config's own `config_fingerprint` returns exactly one, whose `run_id` matches the `negative_run_id` on the in-memory `ConfigScore` and whose `code_hash` is the run's. Each reason names the count of failing blocks, the first failing block by name, and that block's own gate message:

- `ineligible: 1 of 5 OOF blocks failed gate_forecast; first failure on oof_block_3: r2_vs_zero=-0.00732351 <= 0 -- does not beat the constant-ZERO predictor…`
- `ineligible: 5 of 5 OOF blocks failed gate_forecast; first failure on oof_block_0: rank IC is not finite -- scipy.stats.spearmanr returns NaN for a CONSTANT input…`

`warn_if_already_negative` runs first, on every ineligible config, so a re-run names the prior runs instead of doubling the log. The converse is asserted too: every eligible config has `negative_run_id is None` and returns an empty query. Reasons are trimmed visibly (`[...]`) to `MAX_TAG_VAL_LENGTH - 256`, because `record_negative_result` refuses an over-long reason rather than letting MLflow truncate it silently.

## Mutation checks

**M1 — `all` → `any` in `summarise_config` (the eligibility rule).**
Observable stated before running: *only* the hand-built test should fail — `hopeful.eligible` flips to `True` and `select_winner` returns 1 instead of 0 — while the full-fixture winner test should still pass, because the fixture's single-block failure carries mean IC +0.167 against the winner's +0.198 and would not win even if eligible.
Result: `1 failed, 8 passed`. The failure was `test_a_config_that_fails_a_gate_on_one_block_is_ineligible_even_with_a_good_mean`, on `assert True is False`. Prediction matched exactly, which is the evidence that the hand-built case is load-bearing and the fixture cannot see this rule.
Hashes: `4ab08394…` → `ca04854f…` → `4ab08394…`.

**M2 — drop `tracking_root_digest(...)` from `segment_cache_dir` (the bypass).**
Observable stated before running: root B gets a cache hit, `look_count(B)` stays 0, and `test_a_cache_written_under_one_tracking_root_is_not_read_under_another` fails on that assertion; nothing else breaks, because every other test uses one root.
Result: `1 failed, 14 passed` — the right test, but on `path_b != path_a`, an assertion **earlier** than the one predicted. The look_count consequence was therefore *not* exercised by the mutation run, so it was measured separately against the mutated module (table above): 1 `materialize` call total, `look_count(B) = 0`. A near-miss recorded rather than glossed: the test file does bite, and the specific mechanism I claimed needed its own measurement.
Hashes: `303b4494…` → `cec454f2…` → `303b4494…`.

Both files restored byte-identically; `git diff --stat` empty after each.

## Deviations from plan

**1. [Rule 3] The loop is block-outer, config-inner, not config-outer as the plan's text says.** Same arithmetic, same output ordering (results are collected by `(grid_index, block)` and reduced afterwards), 17× less block I/O and a warm train cache across each block's 17 fits. Documented in `run_oof_sweep`'s docstring as a measured choice.

**2. [Rule 2] `run_oof_sweep` takes `train_cache_path`, not a train segment name.** The plan says "takes the train cache"; the obvious reading (`train_segment_name="train"`) would have been a parameter through which `val` could be passed, contradicting the same plan's structural-confinement requirement. Additionally the path must resolve inside this manifest's own tracking-root-keyed cache directory — a train frame from another fold geometry is a real leak and this is the cheap check that catches it.

**3. [Rule 2] Four additions the plan did not name.** A provenance sidecar `<segment>.cache.json` beside every cached frame (rows, etime range, and `look_count` on both sides of the call, so an operator chasing a look has the before/after in the file); a distinct-fingerprint check over the whole grid before any fitting, because two configs sharing a fingerprint would share one negative record and falsify D-07-20; `_manifest_blocks` asserting `blocks[j]["name"] == f"oof_block_{j}"` after sorting by `start_ns`, because `training_rows_for_block` indexes positionally and a mis-ordered list purges the wrong window while returning a plausible row count; and non-finite metrics stored as JSON `null` rather than as a bare `NaN` literal no strict parser accepts.

**4. [Rule 2] A ninth sweep test.** The plan lists eight; `test_no_eligible_config_is_an_explicit_outcome_and_not_a_silent_best_available` was added because the acceptance criteria require that outcome to be "a distinct, explicit return value" and nothing else would have checked the three places that must agree about it.

**5. `look_run_ids` is NOT scoped to the `harness-looks` experiment**, though the plan's parenthetical says "in the `harness-looks` experiment". `budget.look_count` searches every experiment; a narrower query here could return fewer ids than that function counts, and the invariant the plan's own test demands (`len(ids) == look_count(...)`) would break. The filter string is byte-identical to `look_count`'s, the equality is asserted inside the function per segment, and `LOOK_EXPERIMENT_NAME` is kept as a documented constant cross-checked at import against `record_look`'s own default.

## Known stubs

None. `guard_against_ceiling` remains wired into no caller — that is 07-07's carried item and is out of this plan's scope (this plan computes no P&L).

## Threat flags

None. The two registered mitigations were implemented as written: T-07-29 by the tracking-root digest (proven by observing the second look), T-07-30 by the signature plus the `oof_block_<int>` refusal, T-07-31 by one negative record per ineligible config, T-07-32 by the copied `_require_filter_safe` with a test asserting the pattern is byte-identical to `budget`'s.

## Verification

- `pytest tests/models/test_cache.py` — 6 passed in 5.4 s.
- `pytest tests/models/test_sweep.py` — 9 passed in 8.3 s.
- `pytest tests/models` — 186 passed in 15.8 s (was 171).
- `pytest tests` — **1330 passed** in 173.9 s (was **1315**; +15 = 6 cache + 9 sweep).
- `inspect.signature(run_oof_sweep).parameters` contains no `val`; parameters are `segment_manifest_id, oof_block_names, train_cache_path, registry_root, lake_root, tracking_root, cache_root, run_tags, normalization_manifest_id, symbol, code_hash, seed, grid`.
- All 19 pre-commit hooks passed on every one of the three commits; `--no-verify` never used.
- `git status` clean; no scratch-cache path anywhere in the tree.

**`budget.look_count` on all twelve canonical counters — `val` and `oof_block_0..4` of both `807125015b25…` and `97964cb27f62…` — read before the first commit and after the last: 0 and 0. Unchanged.** `/Volumes/ProjectsSSD/aihedgefund/scratch` does not exist.

## Self-Check: PASSED

Files verified present: `mvp/models/cache.py`, `mvp/models/sweep.py`, `mvp/tests/models/test_cache.py`, `mvp/tests/models/test_sweep.py`. Commits verified in `git log`: `27507a7`, `1bbe326`, `6bdfced`.

## What was NOT done

- **No real segment was materialized and no look was spent.** Every test and every measurement ran against the fixture lake with `tmp_path` roots. The five OOF looks are wave 8's (plan 07-10) to spend; this plan built the machinery that will spend them and deliberately spent none.
- **`selection.json` has never been written against the real manifest** `807125015b25…`, and `look_run_ids` has never queried the canonical store's `harness-looks` experiment.
- **No CLI, no `run_slice`, no MLflow run of the slice's own.** `run_oof_sweep` is a library function; plan 07-09 owns the runner, its modes and its refusals. Nothing in this plan opens a tracked run, which is what keeps D-07-30 satisfied by construction rather than by care.
- **The real-data cost of the sweep is estimated, not measured.** Each of the 85 fits re-reads the train cache, because `FitInputs` names a path by design (D-07-03's protocol) — on the approved manifest's ~44M train rows that is 85 reads of roughly 1.4 GiB of projected columns. Block-outer nesting keeps 80 of those 85 warm in the page cache, and the five block frames are read once each, but nothing here has been timed on real data and the per-config wall clock (0.002–0.005 s on the fixture) says nothing about it. If a real config's fit exceeds a sane budget in wave 8, that is a finding to report, not a reason to shrink the grid.
- **No prediction table was written anywhere**, asserted by a before/after lake and registry snapshot showing zero new files and no `*predictions*` path under either root (D-07-15).
- **Determinism of the FIT is not claimed.** `test_the_winner_is_the_same_across_two_runs_of_the_same_sweep` asserts determinism of SELECTION — that the rule carries no ordering dependence — on the same machine, the same BLAS and the same cached frames. Cross-machine fit reproducibility is 07-04's frozen-JSON boundary, not this.
- **`budget_allowance` was never exercised toward exhaustion.** The fixture's allowance is 50; `BudgetExhaustedError` propagating out of `materialize_once` is asserted nowhere, only documented as never swallowed.
- **FCST-01 stays Pending.** A sweep that names a winner on a synthetic fixture is the mechanism, not the requirement: no real fold has been scored, nothing has been frozen, and `val` has not been read.
- **No guardrail, tool, manifest, `spec.md` section or lockfile was touched**; `mvp/data/lake_registry/` is byte-unchanged.
