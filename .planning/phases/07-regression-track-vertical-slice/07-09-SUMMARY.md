---
phase: 07-regression-track-vertical-slice
plan: 09
subsystem: models
tags: [slice-runner, cli, look-budget-refusals, provenance-closure, mlflow-run, end-to-end]

# Dependency graph
requires:
  - phase: 07-08
    provides: "models/cache.py materialize_once + look_run_ids + look_report, and models/sweep.py run_oof_sweep + read_selection + selection_winner_trainer + selection_path -- the cross-process winner handoff this plan's three modes are separated by"
  - phase: 07-07
    provides: "models/metrics.py forecast_metrics, models/gates.py gate_forecast/gate_monetization/perfect_foresight_ceiling/guard_against_ceiling -- the guard was wired into no caller until this plan"
  - phase: 07-05
    provides: "models/predictions.py write_prediction_table/load_prediction_table/assert_table_aligned/partition_overlaps_segment"
  - phase: 07-04
    provides: "models/frozen.py write_frozen_predictor/read_frozen_predictor and the body's two ids"
  - phase: 07-03
    provides: "models/protocol.py FitInputs and models/predictor_id.py RECIPE_FIELDS"
  - phase: 04-features-tier
    provides: "features/normalize.py fit_training_segment/write_normalization_artifact/load_normalization, features/tier.py curated_manifest_input"
  - phase: 03-tracking
    provides: "tracking/mlflow_utils.py start_tracked_run, MANDATORY_TAG_KEYS, log_data_provenance, read_provenance_tag, compute_code_hash (injectable git runner), compute_env_hash"
provides:
  - "models/slice.py -- run_slice with three modes (select/freeze/val), the forced step order, SliceResult, find_frozen_body, select_train_day_manifests, SCORED_SEGMENT_TAG_KEY"
  - "scripts/run_stage1_slice.py -- the five-flag CLI, its refusals, the look report at both ends, and the lake/registry snapshot diff"
  - "the finding that a run tagged segment_name is counted by budget.look_count regardless of experiment"
affects: [07-10, 07-11, "Phase 8's cross-class runner", "Phase 9's Stage-2 policy fit"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Inject the value, never acquire it: code_hash is a required PARAMETER of run_slice and the module imports no subprocess and calls no compute_code_hash, AST-asserted. Pre-commit stashes only UNSTAGED changes, so a staged file reads the tree dirty during every hook run -- a library that computed-and-refused would fail every commit in this repo forever"
    - "Give the fixture the SHAPE of the real run, not its convenience: the three modes are invoked with three DIFFERENT injected hashes, because the real run has three clean hashes at three commits. A one-hash fixture would leave the frozen-body lookup silently wrong and green"
    - "Reuse before writing, wherever a write-once PARENT glob guard exists. Both write_normalization_artifact and write_prediction_table refuse a second file in the same directory, so a crash after either leaves the correct artifact sitting behind a FileExistsError. Resolving it first is what makes a recovery invocation a no-op"
    - "A bookkeeping tag can spend a budget. budget.look_count matches (segment_manifest_id, segment_name) across EVERY experiment, and segment_manifest_id is mandatory -- so an additive segment_name tag on any run is a counted look. The key moved to scored_segment_name; the value did not change"
    - "Publish the scorable count always and the statistic only when there is something to compute it from. A diagnostic horizon longer than the segment has zero scorable rows and forecast_metrics correctly refuses; absent is honest, fabricated is not, and fatal is wrong for a number the phase never fits on"
    - "When the fixture is structurally blind to a rule, test the rule PURELY. One upstream feature manifest covering one date cannot distinguish 'the five train days' from 'every day the pool spans'; hand-built bodies whose dates and etimes deliberately disagree can"

key-files:
  created:
    - mvp/models/slice.py
    - mvp/scripts/run_stage1_slice.py
    - mvp/tests/models/test_slice_end_to_end.py
    - mvp/tests/models/test_slice_cli.py
    - mvp/tests/models/test_script_numba_cache_dir.py
  modified: []

key-decisions:
  - "THE PLAN'S OWN RUN-3 TAG LIST WOULD HAVE SPENT THE VAL BUDGET. The plan asks run 3 to carry `segment_name=\"val\"` additively. `harness.budget.look_count` counts every run whose tags satisfy `segment_manifest_id = <id> AND segment_name = <segment>`, across ALL experiments (deliberately -- `models.cache.look_run_ids` inherits that), and `segment_manifest_id` is one of the eight MANDATORY tags. Measured on the fixture with the plan's tag name: look_count(val) read 2 after ONE honest look and 3 after a second, cache-only invocation that materialized nothing. On the approved manifest that is two of three allowance slots gone, spent by bookkeeping. The key is now SCORED_SEGMENT_TAG_KEY = 'scored_segment_name'; the value is unchanged; `models.sweep`'s negative-result tags never carried segment_name, which is why the sweep never tripped this."
  - "THE FROZEN BODY IS FOUND BY THE RECIPE MINUS code_hash, never by selection.json's config_fingerprint. That fingerprint equals predictor_id over the SELECT-time recipe, code_hash included, and the freeze runs at a LATER commit by design (the body is committed before val is touched) -- so on the real run the committed body's predictor_id is a different string and a fingerprint lookup would find nothing. It would also PASS on a fixture that ran all three modes at one HEAD: a green test behind a false fact, which this phase has paid for once already (07-08's repo-root check). The fixture therefore uses three different hashes, and the mutation that re-adds code_hash to the match fails 13 tests."
  - "train_end_date IS DERIVED, NOT TYPED. The plan names the literal '2026-09-16'. Hard-coding it would make the module unusable on the fixture, whose partition is dated 2026-09-13 while its etime starts at epoch 0 -- and a date-string filter is wrong for the same reason. `select_train_day_manifests` keeps the upstream feature manifests whose partitions overlap `[train.start_ns, train.end_ns)` via `models.predictions.partition_overlaps_segment`, and takes train_end_date as the last calendar day among the kept ones. Measured on hand-built per-day bodies spanning 09-12..18: 5 manifests kept, train_end_date 2026-09-16. On the fixture: 1 manifest, 2026-09-13."
  - "A DIAGNOSTIC HORIZON WITH ZERO SCORABLE ROWS KILLED THE RUN, and that was found by the short rig rather than reasoned about. A val segment shorter than a label horizon sits entirely inside that label's trailing null tail -- on 599 rows every row is in ret_10min_mid's 600-row tail -- and `forecast_metrics` correctly refuses a zero-row score. D-07-10 calls these numbers 'reported alongside but never fitted', so raising over one is the wrong failure. n_scorable_<horizon> is now published always; the two R-squareds only when there is something to compute them from."
  - "THE CLEAN-TREE PRECONDITION AND THE CLEAN-HASH PRECONDITION ARE ONE CHECK, STATED ONCE. `compute_code_hash` appends '-dirty' from exactly `git status --porcelain`, so the script makes ONE git call per invocation and every flag inherits the refusal. It also makes the plan's 'the frozen body exists at the committed path' check need no second git call: porcelain lists an untracked file as `?? path`, so a clean tree plus a body that `read_frozen_predictor` just read IS a tracked body. A `git ls-files` there would be a weaker statement of the same fact."
  - "--resume-from-cache takes look_count(val) >= 1, and the three (cache, count) states are PROVEN to partition. A test walks all three -- (absent, 0), (present, >=1), (absent, >=1) -- asserting the owning flag proceeds and BOTH others refuse, then exercises --resume-from-cache at count 2 as well. An exact-equality precondition would strand the post-respend state exactly where the other two flags also refuse it: the same unnameable-state trap --respend-val-look closes, reopened one state further along."
  - "A FIFTH TEST FILE, tests/models/test_slice_cli.py, beyond the plan's named files. The plan's acceptance criteria ask for the refusals to be 'demonstrated by running them'; a transcript in a SUMMARY is not a control. 07-08's own recorded lesson is that a refusal exercised by nothing is a refusal nobody can be sure fires, and these refusals stand between a dry run and an irreversible look. 18 tests, every one checking the CAUSE NAMED rather than the exit code."

# Metrics
duration: "~2.6 h"
completed: 2026-09-26
---

# Phase 7 Plan 9: run_slice, the CLI, and the fixture end-to-end proof Summary

The whole Stage-1 slice now runs from curated rows to one MLflow run that carries everything needed to rebuild it, and five CLI flags stand between an operator and the one irreversible look — each of them fired in a test, and the three recoverable states proven to partition with none stranded.

## The end-to-end result on the fixture

One rig, 18,000 rows on a 6,000-row train entry, five OOF blocks, 11,999 `val` rows. Three invocations, three different injected code hashes:

| step | outcome |
|---|---|
| accessor → `train` frame | 6,000 rows, no look (role `train`) |
| normalisation | `features_norm` manifest issued over 4 `FEATURE_COLUMNS`, `train_end_date` derived as `2026-09-13`, provenance naming 1 of 1 train-window feature manifest |
| sweep (5 looks) | 9 of 17 configs eligible, winner `sklearn.Ridge α=0.001` |
| freeze | body written, `predictor_id` embedding the freeze-time hash |
| `val` look | 1 look, 11,999 rows |
| prediction table | manifest issued, read back, `assert_table_aligned` against the cached frame |
| simulator | **12 trades, 50 closed ticks, $+0.005000** |
| ceiling | **639 trades, 2,187 ticks, $+0.218700 — the slice reported 2.2862% of it** |
| gates | `gate_forecast` PASS, `gate_monetization` PASS |
| `guard_against_ceiling` | **did not fire, correctly** — and fires when forced (below) |
| run 3 | one `stage-1-regression` run, 21 tags, 24 metrics |

Anti-vacuity is asserted, not implied: `0 < 12 trades < 639`, the winner's coefficient vector is not all zeros, and `0 < pnl_fraction_of_ceiling < 1`.

**`guard_against_ceiling` is now wired into a caller** — 07-07's carried item, closed. It is exercised by handing the kernel perfect foresight's own prediction while the slice believes it is scoring the model, so step 8 reaches the guard with the two numbers equal and raises. Equality is already too high: it means the model matched perfect foresight tick for tick.

## The bug the plan's own tag list would have caused

**A bookkeeping tag can spend an irreversible budget.** `harness.budget.look_count` counts every run whose tags satisfy `segment_manifest_id = <id> AND segment_name = <segment>` — across **every** experiment, deliberately, which is why `models.cache.look_run_ids` inherits the same filter. `segment_manifest_id` is one of the eight mandatory tags. So run 3, tagged `segment_name="val"` as the plan asks, is indistinguishable from a look.

Measured with the plan's tag name:

| invocation | honest looks spent | `look_count(val)` reads |
|---|---|---|
| `select` + `freeze` + `val` | 1 | **2** |
| a second `val` (cache hit, materialized nothing) | 0 | **3** |

On the approved manifest, `budget_allowance = 3`: two of the three slots gone, spent by a tag, with every record a human consults agreeing that the looks were real. The key is now `scored_segment_name`; the value is unchanged; a test asserts `"segment_name" not in tags` with the mechanism in its message. `models.sweep`'s negative-result tag set never carried `segment_name`, which is why the sweep never tripped this and why the bug was this plan's to find.

## `run_slice` never consults git, proven by AST

`models/slice.py` imports no `subprocess` and calls no `compute_code_hash` — asserted by walking the module's own AST for `Import`/`ImportFrom` names and `Call` targets, with an anti-vacuity check (`{"mlflow","numpy","polars"} <= imported` and `{"run_oof_sweep","start_tracked_run","materialize_once"} <= called`) so both assertions cannot pass on an empty set. The refusal is a pure string check: `code_hash.endswith("-dirty")` raises, `""` raises.

The reason is measured, not stylistic: pre-commit stashes only UNSTAGED changes, so a staged file leaves `M  path` in `git status --porcelain` and `compute_code_hash` appends `-dirty` during every hook run. Thirteen end-to-end tests call `run_slice`; a compute-and-refuse inside the library would fail every commit in this repo forever, including commits touching nothing under `mvp/models/`.

## Run 3's tags, and that it opens on a failing gate

Eight mandatory — `code_hash`, `data_hash` (= the prediction-table manifest id), `seed`, `env_hash`, `segment_manifest_id`, `model_class`, `fold_config`, `stage` — every one real, none the spec's `"n/a"` placeholder. Then additively: `predictor_id`, `predictor_manifest_id`, `normalization_manifest_id`, `prediction_table_manifest_id`, `fold_config_reason`, `scored_segment_name`, `train_end_date`, `forecast_gate_reason`, `monetization_gate_reason`, `look_run_ids` (sharded on comma boundaries, read back through `read_provenance_tag`), plus `negative_run_id` and `respend_reason` when they apply, plus `log_data_provenance`'s `data_manifest_ids` / `dq_ack_ids` / `dq_ack_sha256`. 21 non-`mlflow.*` tags on the fixture run.

`look_run_ids` is **re-queried** from the store, never carried in memory: `models.cache.look_run_ids` over the five blocks plus `val`, with `selection.json`'s recorded ids asserted to be a SUBSET of the live ones and every segment required to have at least one. The plan asks for "exactly six entries"; that is asserted as **`len(ids) == look_count(name) >= 1` per segment** instead, because after a `--respend-val-look` `val` legitimately has two run ids and an exact count would refuse the state the phase documents as recoverable.

**Run 3 opens on a failing gate, and the gate fails on real numbers.** The 599-row `val` rig earns the fitted model 1 trade and exactly $0, which `gate_monetization` refuses ("a P&L of exactly $0 is not a non-negative result, it is an absent one"). The negative result is recorded FIRST — runs cannot nest — keyed on the frozen body's own five recipe fields, so it is queryable by the very `predictor_id` the body carries; then run 3 opens carrying `monetization_gate_passed = 0.0`, the failing trade count, and the negative run's id. SC3 does not depend on SC1.

## Every flag's refusals, fired

`--select`, `--freeze`, `--spend-val-look`, `--resume-from-cache`, `--respend-val-look`. Each refusal is checked by the **cause it names**, not by a non-zero exit.

**Shared by all five:** a dirty tree. Demonstrated in the suite with an injected `git status --porcelain` output on each of the five flags, and demonstrated for real against my actual working tree with `tmp_path` roots:

```
FAIL: the working tree is dirty (code_hash=6468d316634ccf77b988f02b3fffce4004c903f5-dirty). Every
manifest, look run and frozen body this invocation would write embeds that string, in append-only
registries, forever. Commit first ...
```

**`--freeze`:** no `selection.json` ("run --select against THIS tracking root"), and a `segment_manifest_id` inside it that disagrees with the one the path encodes. The tampered file is RESTORED before the next assertion — 07-08's recorded anti-pattern is watching the *next* refusal fire while asserting on this one's message.

**`--spend-val-look`,** four independently:
1. no frozen winner — in both shapes (no selection at all; a selection whose winner was never frozen). Neither spends a look, because the check precedes step 6.
2. a dirty tree.
3. `look_count(val) != 0`.
4. a `val` cache under a DIFFERENT tracking root's digest — a dry-run cache is byte-identical to a paid one and cost nothing; it cannot be *read* from here, but the operator is told before rather than after. Removing it, the same invocation then succeeds and `look_count(val)` goes to 1.

**The partition, walked:**

| state | `--spend-val-look` | `--resume-from-cache` | `--respend-val-look` |
|---|---|---|---|
| cache absent, count 0 | **proceeds** | refuses ("no val cache") | refuses ("no look has been spent") |
| cache present, count 1 | refuses ("already been looked at") | **proceeds**, count stays 1 | refuses ("the val cache IS present") |
| cache absent, count 1 | refuses ("already been looked at") | refuses ("no val cache") | **proceeds**, count → 2 |
| cache present, count 2 | refuses | **proceeds** — not stranded | refuses |

That last row is why the precondition is `>= 1`: an exact-equality check would refuse the post-respend state while both other flags also refuse it.

**`--respend-val-look`** additionally refuses a missing `--reason`, and refuses `""`, `"TODO"`, `"crash"`, `"fix it later please"` as placeholders (under 40 characters, or on the placeholder list) — before it consults the cache or the count. At an exhausted allowance it names the count, the allowance, and that the remedy is a NEW segment manifest (D-05-14), never another look. The accepted reason is asserted to land on exactly two runs: the second look run and the slice run.

**`--help`** resolves no canonical root, proven two ways without touching one: every root flag's argparse default is `None` (a default expression would have fired at parser construction), and `main(["--help"])` with both resolvers patched to raise exits 0 without either firing. Separately demonstrated against the real lake — `mtime_ns 1790371397329056031`, 8 children, byte-identical before and after a `--help` run, which a `lake_root()` write-probe would have changed.

## Mutation checks

Each states its observable BEFORE running, and the file's sha256 is printed before the edit, after the edit, and after the restore.

**M1 — `SCORED_SEGMENT_TAG_KEY` back to the plan's `"segment_name"`.**
*Stated before:* two tests fail — the mandatory-tag test on `"segment_name" not in tags`, and the exactly-once-look test because `look_count(val)` reads 2 instead of 1.
*Result:* `2 failed, 10 passed`, and **both** predicted assertions fired verbatim (`assert 'segment_name' not in {...}` and `assert 2 == 1`). Prediction matched exactly; the budget consequence was exercised by the mutation run itself, not only asserted about.
Hashes `6f4bc0507d4a` → `7137dadeb470` → `6f4bc0507d4a`.

**M2 — step 7's reuse-before-writing branch removed.**
*Stated before:* the second `mode="val"` hits `write_prediction_table`'s write-once parent glob guard and raises `FileExistsError` (not an assertion failure); the exactly-once-look test and, in the CLI file, the partition test's `--resume-from-cache` leg plus the respend test.
*Result:* `4 failed, 26 passed`, all four on `FileExistsError`. **One more than predicted:** the ceiling-guard test also failed, because it too re-invokes `mode="val"`. Same mechanism, incomplete prediction — recorded rather than glossed. The allowance test correctly did NOT fail: it refuses before reaching step 7.
Hashes `6f4bc0507d4a` → `187c830bbe62` → `6f4bc0507d4a`.

**M3 — `find_frozen_body` also matches on `code_hash`** (the natural-looking version, and the real-run bug).
*Stated before:* `mode="val"` at the val hash cannot find the body frozen at the freeze hash, so `SliceError: no frozen predictor`; 8 of 12 e2e tests plus the CLI tests reaching `--spend-val-look`.
*Result:* `13 failed, 17 passed` — **9** e2e (I undercounted: `n_pred_missing` also runs the full trio) and 4 CLI. Every failure carried the named message. The point of the mutation is what it proves about the fixture: with one shared hash across the three modes, this mutation would have been invisible.
Hashes `6f4bc0507d4a` → `67908a34c1d6` → `6f4bc0507d4a`.

**M4 — the train-window filter dropped from `select_train_day_manifests`.**
*Stated before:* **exactly one** test fails — the pure one — and all twelve fixture tests stay green, because the fixture has one upstream manifest covering one date and cannot tell the two rules apart.
*Result:* `1 failed, 12 passed`, exactly as stated. The assertion that fired was the list equality (`assert [...bodies[:5]] == list(kept)`), one **earlier** than the "a val day's feature manifest reached the normalisation provenance" message I named — so the `train_end_date` consequence was measured separately against the mutated module: **7 manifests kept, `train_end_date = 2026-09-18`**, against 5 and `2026-09-16` shipped. 07-08's discipline applied.
Hashes `6f4bc0507d4a` → `1d02d0402219` → `6f4bc0507d4a`.

**M5 — the `guard_against_ceiling` call deleted from step 8.**
*Stated before:* exactly one test fails, on `DID NOT RAISE CeilingExceededError`; nothing else, because on the honest rig 50 ticks against a 2,187-tick ceiling never reaches the guard.
*Result:* `1 failed, 12 passed`, on `Failed: DID NOT RAISE CeilingExceededError`. Prediction matched exactly.
Hashes `6f4bc0507d4a` → `7a15f5ec576b` → `6f4bc0507d4a`.

`git diff --stat` empty after every restore.

## Deviations from plan

**1. [Rule 1] Run 3 does not carry a `segment_name` tag.** The plan's additive tag list names it; it makes `budget.look_count` count the slice's own run as a look. Measured (table above). `SCORED_SEGMENT_TAG_KEY = "scored_segment_name"` carries the same value under a key the budget filter cannot match, and M1 is the mutation that re-creates the bug.

**2. [Rule 1] `look_run_ids` is asserted per segment as `len(ids) == look_count(name) >= 1`, not "exactly six".** After a `--respend-val-look` — a state this same plan documents and provides a flag for — `val` has two look runs. An exactly-six assertion would refuse the recovery path the phase designs.

**3. [Rule 2] `train_end_date` and `source_feature_manifest_ids` are DERIVED, via a pure `select_train_day_manifests`, not typed as `"2026-09-16"`.** The literal cannot work on the fixture (dated 2026-09-13 with etimes from epoch 0), and a date-string filter is wrong for the same reason. The ns-overlap rule gives 5 manifests / `2026-09-16` on per-day bodies spanning 09-12..18, and 1 / `2026-09-13` on the fixture.

**4. [Rule 1] A diagnostic horizon with zero scorable rows no longer kills the run.** Found by the short rig: a 599-row `val` segment is entirely inside `ret_10min_mid`'s 600-row null tail. `n_scorable_<horizon>` is published always; the R-squareds only when computable.

**5. [Rule 2] A fifth test file, `tests/models/test_slice_cli.py` (18 tests).** The plan names only `test_script_numba_cache_dir.py` for Task 2 and asks the refusals to be "demonstrated by running them". A transcript is not a control, and 07-08's own lesson is that an untested refusal is a refusal nobody can be sure fires.

**6. [Rule 2] A thirteenth end-to-end test, the pure `select_train_day_manifests` one.** Added after M4 showed the fixture is structurally blind to the provenance rule.

**7. [Rule 3] `--spend-val-look`'s "committed at the registry path" check makes no second git call.** `git status --porcelain` lists an untracked file as `?? path`, so the clean-tree refusal already implies a body that `read_frozen_predictor` just read is tracked. The script makes exactly one git call per invocation, as the plan requires.

**8. `NUMBA_CACHE_DIR` is pinned to `tempfile.gettempdir() / "aihf-numba-cache"`, not the literal `/tmp/nbc`.** Same expression `tests/conftest.py` uses, so the script path and the pytest path agree and a portable tempdir is honoured. Both are outside `mvp/`; the subprocess test asserts containment rather than a literal.

**9. Steps 3 and 4 are fused, because 07-08 fused them.** `run_oof_sweep` owns the five `materialize_once` calls in its block-outer loop. `run_slice` calls it once, with `_require_no_active_run` in front.

## Known stubs

None. Every function this plan wrote has a caller and a test.

## Threat flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: budget-counter-collision | mvp/models/slice.py | Any run in the store tagged with both `segment_manifest_id` and `segment_name` is counted by `harness.budget.look_count`, whatever experiment it lives in and whatever it did. `segment_manifest_id` is mandatory on every tracked run, so the collision surface is one additive tag wide and is not confined to this module. Mitigated here by `SCORED_SEGMENT_TAG_KEY` plus a test asserting the bare key's absence, but a future plan adding a tracked run should be told: **never name a tag `segment_name` unless the run IS a look.** |

T-07-33 (provenance closure), T-07-34 (an accidental val look), T-07-35 (a drifting `predictor_id`) and T-07-36 (stray numba artifacts) were implemented as registered.

## Verification

- `pytest tests/models/test_slice_end_to_end.py` — 13 passed in 13.4 s.
- `pytest tests/models/test_slice_cli.py` — 18 passed in 13.2 s.
- `pytest tests/models/test_script_numba_cache_dir.py` — 2 passed in 5.2 s.
- `pytest tests/models` — **229 passed in 49.4 s** (was 187 in 16.1 s; +42, and the runtime is now on every commit forever).
- `pytest tests` — **1,364 passed in 199 s** (was **1,331** in 174 s; +33 = 13 + 18 + 2).
- All 19 pre-commit hooks passed on all five commits; `--no-verify` never used; `git status` clean.
- `python -m scripts.run_stage1_slice --help` exits 0 and leaves the real lake root's `mtime_ns` and child count unchanged.

**`budget.look_count` on all twelve canonical counters — `val` and `oof_block_0..4` of both `807125015b25…` and `97964cb27f62…` — read BEFORE the first commit and AFTER the last: 0 and 0. Unchanged.** `/Volumes/ProjectsSSD/aihedgefund/scratch` still does not exist.

## Cost note for wave 8

Nothing here changes 07-08's estimate: each of the 85 sweep fits still re-reads the train cache because `FitInputs` names a path by design. `run_slice` adds, per `mode="val"` invocation, **one** further full read of the `val` cache (for the frame), one `perfect_foresight_ceiling` pass over it, and one simulator pass — three linear passes over ~16M rows on the approved window, against the sweep's 85 reads of the train frame. Measured on the 18,000-row fixture the whole three-mode sequence is 2.5 s wall including imports and numba warm-up, which says nothing about the real window and is recorded only so nobody mistakes it for a projection.

## Self-Check: PASSED

Files verified present: `mvp/models/slice.py`, `mvp/scripts/run_stage1_slice.py`, `mvp/tests/models/test_slice_end_to_end.py`, `mvp/tests/models/test_slice_cli.py`, `mvp/tests/models/test_script_numba_cache_dir.py`. Commits verified in `git log`: `d209b79`, `bb4fb71`, `6468d31`, `20be295`, `27327ed`.

## What was NOT done

- **No look was spent against a canonical root, and no real segment was materialized.** Every test and every measurement ran against the fixture lake with `tmp_path` roots. All twelve canonical counters read 0 before the first commit and 0 after the last.
- **`--select`, `--freeze`, `--spend-val-look`, `--resume-from-cache` and `--respend-val-look` have never been run against the real lake, the real registry or the real MLflow store.** The only real invocation was `--help`, plus one `--spend-val-look` that refused on a dirty tree with `/tmp` roots. Plans 07-10 and 07-11 own the real runs.
- **Nothing was written under `mvp/data/lake_registry/`.** No `predictors/` directory exists under the real registry, no real `features_norm` artifact was issued, no real prediction table was written, and `mvp/data/lake_registry/` is byte-unchanged.
- **No guardrail, tool, `spec.md` section, manifest or lockfile was touched.** The guardrail extension that teaches the scanners about `predictors/` is 07-10's, and it must land in the same commit as the first real frozen body — this plan deliberately created no body outside a `tmp_path`.
- **The real-data cost of `mode="val"` is estimated, not measured.** Three linear passes over ~16M rows is arithmetic, not a timing.
- **`selection.json` has never been written against the real manifest** `807125015b25…`, and `look_run_ids` has never queried the canonical store.
- **`budget_allowance` exhaustion is tested only through a PATCHED manifest read.** The fixture's own allowance is 50, and hand-editing a manifest body would fail the registry's id-integrity check, so the comparison is tested and the real 3-slot ceiling is not.
- **The `--respend-val-look` state is SIMULATED by deleting the cache, not by crashing between `record_look` and the Parquet write.** The resulting on-disk state is identical; the crash itself is not reproduced.
- **`mode="freeze"` has never crossed a real commit boundary.** The cross-process handoff is proven across two `run_slice` calls in one pytest process with different injected hashes; the real run crosses a git commit as well, and only the file contents are what survive either way.
- **Determinism of the refit is not claimed.** `mode="freeze"` refits the winner on the full train frame; that the coefficients reproduce on another machine is 07-04's frozen-JSON boundary, not this.
- **FCST-01 and FCST-04 stay Pending.** A slice that runs end to end on a synthetic fixture is the mechanism, not the requirement: no real fold has been scored, nothing real has been frozen, and the real `val` window has not been read.
