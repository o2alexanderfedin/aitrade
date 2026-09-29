---
phase: 07-regression-track-vertical-slice
plan: 10
subsystem: models
tags: [oof-sweep, look-budget, model-selection, negative-result-log, normalization-artifact, no-eligible-config]

# Dependency graph
requires:
  - phase: 07-02
    provides: "segment manifest 807125015b25 -- compressed_3seg, train 2026-09-12..16, val 17..18, five one-calendar-day oof_blocks, budget_allowance 3"
  - phase: 07-08
    provides: "models/cache.py materialize_once and models/sweep.py run_oof_sweep + selection.json, including the NO-ELIGIBLE-CONFIG outcome this plan met"
  - phase: 07-09
    provides: "dq_preflight, called before the first materialize -- it returned ([], []) and refused nothing"
provides:
  - "mvp/data/lake_registry/manifests/BTCUSDT.features_norm/c7749334fb73...json -- the 7-day window's train-only normalisation artifact at train_end=2026-09-16, ADDITIVE beside the 2026-09-13 one"
  - "five spent OOF looks and their five harness-looks runs, with val untouched at 0"
  - "17 negative-result records -- one per config in the counted grid, because NONE was eligible"
  - "the measured finding that the five OOF blocks are not exchangeable: tie_fraction runs 0.70 / 0.44 / 0.12 / 0.12 / 0.12 across them"
affects: [07-11, "any re-plan of the regression track's grid or its segment geometry", "Phase 8's cross-class selection"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Observability without a repo file: the slice CLI refuses a dirty tree, so the logging/warning/per-fit instrumentation this run needed lived in a scratchpad wrapper OUTSIDE the repo that imports `scripts.run_stage1_slice.main` unchanged. Same entry point, same behaviour, nothing to commit"
    - "A plan's expected row counts must be derived through the SAME purge the materialize applies. Four of the five expected OOF training-row counts were each exactly 3,115 high -- the purge band's own size -- because they were computed off the raw 44,457,598 rather than the cached 44,454,483"

key-files:
  created:
    - mvp/data/lake_registry/manifests/BTCUSDT.features_norm/c7749334fb73e090272013dfeb10013e25948b11a0907d56a69ee39d7b8954ba.json
  modified: []

key-decisions:
  - "NO CONFIG WAS ELIGIBLE -- 0 of 17. This is the first-class outcome models/sweep.py was built to express, not a malfunction, and the plan's Tasks 2 and 3 are therefore unexecutable as written: there is no winner to freeze and none to approve. --freeze was RUN anyway, to turn that claim from a reading into a measurement: it refused with NoEligibleConfigError after a train CACHE HIT that spent no look."
  - "The plan's own Task 2 one-commit rule is why NOTHING of Task 2 was committed. The predictor body and both guardrail extensions must land together (D-07-22); with no body obtainable, landing the guardrails alone would break exactly the coupling the rule exists to enforce. The tools are byte-unchanged."
  - "TWELVE of the seventeen configs fail on oof_block_0 ALONE and pass every other block. On 2026-09-12 they post r2_vs_zero = -0.11 while carrying the HIGHEST rank IC of the five blocks (0.34). Right ordering, wrong scale."
  - "The mechanism is measured, not guessed: tie_fraction on oof_block_0 is 0.7023 against 0.4389 / 0.1226 / 0.1241 / 0.1151 on the others. 2026-09-12 is a full 24 h day (verified against its partition's etime span) carrying 4.19M book updates against 09-16's 9.87M -- 48.5/s against 114/s. Seventy per cent of its 10-second midprice returns are exactly zero. A model fit on the four busier days predicts motion on rows that do not move, and the all-rows R-squared refuses it while the movers-only IC does not see it."
  - "FIVE of the seventeen configs (grid 9-13) could never have produced a non-constant predictor. Every ElasticNet at alpha >= 1e-4 with l1_ratio >= 0.5, and every one at alpha = 1e-2, shrank all three coefficients to zero on all five blocks -- rank IC NaN, gate refused on 5 of 5. On a target whose scale is a 10-second return, those alphas are larger than any coefficient. 29% of the counted grid was dead on arrival."
  - "The 2026-09-13 normalisation artifact is byte-identical before and after: sha256 d0aff38813423618cf4b3409592eda68ff2d56829cea73596b0536db5989f73e, hashed at both ends. The new one is additive at its own train_end (D-07-11)."

patterns-established:
  - "Pattern: when a gate's message quotes a number, check whether it is measured or canned before repeating it. gate_forecast's not-finite branch names r2_vs_zero=+0.001647 as 'the measured shape of this failure' -- that is a 07-RESEARCH Q4 literal in models/gates.py:383, not a measurement of the run that printed it."
  - "Pattern: a per-block spread is not one number. The winner-shaped config here has mean IC 0.271 with ic_min 0.194 and ic_max 0.377 -- a spread a mean-ranked selection would have called excellent, while the all-blocks eligibility rule refused it on the very block whose IC was highest."

requirements-completed: []

# Metrics
duration: "~35 min (the --select run itself: 10 min 37 s)"
completed: 2026-09-29
---

# Phase 7 Plan 10: Train-window normalisation, five OOF looks, and no eligible config Summary

**The regression grid can order the rows that move on every one of the five out-of-fold days, and on the quietest of them its squared error is worse than predicting zero — so none of the seventeen configurations earned the validation look, and none was frozen.**

## Performance

- **Duration:** ~35 min end to end; the `--select` invocation itself 10 min 37 s (2026-09-29T02:31:48Z → 02:42:25Z)
- **Peak RSS:** 10.13 GiB (sampled every 20 s)
- **Swap: the host was pushed 3.03 GiB deeper into it.** `vm.swapusage` read 4,842 MB used of
  a 6,144 MB swapfile before the run and 7,943 MB of a 9,216 MB one at peak — macOS grew the
  swapfile by 3 GiB in three steps, first at 02:35:29 (as `oof_block_0`'s last poly2 fits gave
  the block frame back and `oof_block_1` was about to be read), then at 02:37:49 and 02:39:49.
  **Throughput did not degrade**, which is what makes this pressure rather than thrashing:
  per-block fit seconds for grid 0 were 2.5 / 1.5 / 1.4 / 1.4 / 1.5 and for grid 15 were
  4.1 / 3.2 / 2.9 / 1.2 / 1.7 — flat or faster after the first block. The host has 32 GiB and
  was in active use by other applications throughout. An earlier draft of this summary said
  swap "did not grow"; that was read off the first four samples and was wrong.
- **Tasks:** 1 of 3 (Task 1 complete; Tasks 2 and 3 unexecutable — see below)
- **Commits:** 2 (the normalisation manifest; this summary)

## The look budget, on all sixteen counters

Two committed segment manifests × eight segment names, queried against the real store at
`/Volumes/ProjectsSSD/aihedgefund/mlflow` by `harness.budget.look_count`.

| manifest | train | **val** | held_out | oof_block_0 | oof_block_1 | oof_block_2 | oof_block_3 | oof_block_4 |
|---|---|---|---|---|---|---|---|---|
| `807125015b25` BEFORE | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0 |
| `807125015b25` AFTER | 0 | **0** | 0 | 1 | 1 | 1 | 1 | 1 |
| `97964cb27f62` BEFORE | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0 |
| `97964cb27f62` AFTER | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0 |

**`look_count("val")` is 0.** Five looks spent, exactly one per OOF block, exactly as planned.
Summed across all sixteen counters the total is 5.

The `harness-looks` experiment holds exactly five runs and their `segment_name` tags are exactly
the five block names — no sixth run, and nothing tagged `val`. **All seventeen** negative-result
runs lack a `segment_name` tag (`all('segment_name' not in r.data.tags for r in negs)` → True,
not a spot check on one), and none carries `scored_segment_name` either, so none of them can be
counted as a look.

**One `code_hash`, threaded, across all twenty-two tracked runs.** The five look runs and the
seventeen negative-result runs carry exactly one distinct `code_hash` between them —
`30efe7c11a8c2d6f3dd568d078a9ee143565becc`, byte-identical to the one the `features_norm`
manifest embeds — and none ends in `-dirty`. That is the "`compute_code_hash()` once per
invocation, threaded through everything" claim measured rather than read: step 2's own manifest
dirtied the tree before any of the five looks was recorded, so a second `compute_code_hash()`
anywhere in the run would have been dirty by construction. The look runs' `data_hash` is
`none` on all five, as `_look_tags` intends.

## What the run did, against what the plan expected

| measurement | expected by the plan | observed | verdict |
|---|---|---|---|
| train rows | 44,454,483 | 44,454,483 | agrees |
| `train_end_date` | 2026-09-16 | 2026-09-16 | agrees, and DERIVED |
| source feature manifests | 5 of the segment's 7 | 5 (09-17, 09-18 excluded by name in the log) | agrees |
| oof_block_0..4 admitted | 4,137,073 / 6,864,853 / 11,291,365 / 12,142,146 / 9,814,111 | identical | agrees |
| oof_block_0..4 training rows | 40,242,038 / 37,488,354 / 32,997,430 / 32,094,158 / 34,530,706 | 40,238,923 / 37,485,239 / 32,994,315 / 32,091,043 / 34,530,706 | **the plan is wrong by 3,115 on the first four** |
| `code_hash` | no `-dirty` | `30efe7c11a8c2d6f3dd568d078a9ee143565becc` | clean |
| `dq_preflight` | `([], [])`, refuses nothing | `dq_ack_ids none` | agrees |

### The 3,115 rows, and why only four blocks felt them

The first four expected training-row counts are each high by exactly 3,115 — the size of the
purge band itself (44,457,598 raw minus 44,454,483 cached). The plan derived them from the raw
day totals rather than from the purged train cache. Those 3,115 rows sit in the last 600 s of
2026-09-16, which `val`'s leading purge band removes; that window lies INSIDE `oof_block_4`'s
own span, so for block 4 they were already excluded as the scored block's own rows and its
count needs no correction. For blocks 0–3 they would have been training rows, so each loses
exactly 3,115.

Verified rather than argued: the committed artifact's `train_etime_max` is
`1789602599845000000`, below the `1789602600000000000` at which the band opens. The observed
counts are correct; the plan's arithmetic was not.

## The result: 0 of 17 eligible

Cells are `r2_vs_zero`; `*` marks a block where `gate_forecast` refused.

| # | model | hyperparameters | oof_block_0 | oof_block_1 | oof_block_2 | oof_block_3 | oof_block_4 |
|---|---|---|---|---|---|---|---|
| 0 | LinearRegression | — | **−0.1129\*** | +0.0564 | +0.0340 | +0.0139 | +0.0159 |
| 1 | Ridge | α=1e-6 | **−0.1129\*** | +0.0564 | +0.0340 | +0.0139 | +0.0159 |
| 2 | Ridge | α=1e-3 | **−0.1129\*** | +0.0564 | +0.0340 | +0.0139 | +0.0159 |
| 3 | Ridge | α=1.0 | **−0.1129\*** | +0.0564 | +0.0340 | +0.0139 | +0.0159 |
| 4 | Ridge | α=100.0 | **−0.1129\*** | +0.0564 | +0.0340 | +0.0139 | +0.0159 |
| 5 | ElasticNet | α=1e-6, l1=0.15 | **−0.1118\*** | +0.0565 | +0.0341 | +0.0138 | +0.0158 |
| 6 | ElasticNet | α=1e-6, l1=0.50 | **−0.1091\*** | +0.0568 | +0.0342 | +0.0136 | +0.0158 |
| 7 | ElasticNet | α=1e-6, l1=0.85 | **−0.1066\*** | +0.0570 | +0.0342 | +0.0135 | +0.0158 |
| 8 | ElasticNet | α=1e-4, l1=0.15 | **−0.0294\*** | +0.0552 | +0.0305 | +0.0100 | +0.0122 |
| 9 | ElasticNet | α=1e-4, l1=0.50 | −0.0010\* | −0.0000\* | +0.0000\* | −0.0001\* | −0.0000\* |
| 10 | ElasticNet | α=1e-4, l1=0.85 | −0.0010\* | −0.0000\* | +0.0000\* | −0.0001\* | −0.0000\* |
| 11 | ElasticNet | α=1e-2, l1=0.15 | −0.0010\* | −0.0000\* | +0.0000\* | −0.0001\* | −0.0000\* |
| 12 | ElasticNet | α=1e-2, l1=0.50 | −0.0010\* | −0.0000\* | +0.0000\* | −0.0001\* | −0.0000\* |
| 13 | ElasticNet | α=1e-2, l1=0.85 | −0.0010\* | −0.0000\* | +0.0000\* | −0.0001\* | −0.0000\* |
| 14 | Ridge+poly2 | α=1e-3 | **−0.1104\*** | +0.0591 | +0.0356 | +0.0132 | +0.0165 |
| 15 | Ridge+poly2 | α=1.0 | **−0.1104\*** | +0.0591 | +0.0356 | +0.0132 | +0.0165 |
| 16 | Ridge+poly2 | α=100.0 | **−0.1104\*** | +0.0591 | +0.0356 | +0.0132 | +0.0165 |

Two distinct failure shapes:

**Twelve configs (0–8, 14–16) fail on `oof_block_0` and nothing else.** Their rank IC across
the five blocks is 0.340 / 0.377 / 0.241 / 0.194 / 0.204 — mean 0.271, min 0.194, max 0.377,
std 0.074. A mean-ranked selection would have promoted grid 14 (Ridge+poly2, mean IC 0.2726) on
those numbers. The all-blocks eligibility rule refused it, on the block whose IC was the second
highest of the five.

**Five configs (9–13) never produced a non-constant predictor.** `rank_ic_non_tied` is NaN on
all five blocks — `spearmanr` of a constant — and `r2_vs_zero` is within 1e-3 of zero
everywhere. Every ElasticNet at α ≥ 1e-4 with l1_ratio ≥ 0.5, and every one at α = 1e-2,
shrank all three coefficients to exactly zero. Their fits took 0.3–0.5 s each against 1–4 s for
the rest, which is the shrinkage terminating immediately.

### Why oof_block_0 refuses everything

| block | date | book updates | rate | tie_fraction | scored rows |
|---|---|---|---|---|---|
| oof_block_0 | 2026-09-12 | 4,193,137 | 48.5/s | **0.7023** | 4,136,550 |
| oof_block_1 | 2026-09-13 | 6,864,853 | 79.5/s | 0.4389 | 6,864,852 |
| oof_block_2 | 2026-09-14 | 11,323,694 | 131/s | 0.1226 | 11,285,128 |
| oof_block_3 | 2026-09-15 | 12,203,294 | 141/s | 0.1241 | 12,137,023 |
| oof_block_4 | 2026-09-16 | 9,872,620 | 114/s | 0.1151 | 9,806,089 |

**The five blocks are not exchangeable.** On 2026-09-12 seventy per cent of the 10-second
midprice returns are exactly zero, against twelve per cent three days later. 2026-09-12 is a
full 24-hour day — verified against its partition's own `etime` span, not assumed — so this is
a quiet market, not a short capture. A model fit on the four busier days predicts motion on
rows that do not move; the squared error on that 70% swamps the gain on the 30% that do, and
`r2_vs_zero` goes negative. `rank_ic_non_tied` scores only the movers, so it never sees the
problem — which is precisely why D-07-18(a) requires both.

Against the pre-phase train-internal reference band (R² vs zero 0.013–0.048, rank IC
0.19–0.29): blocks 3 and 4 sit inside it; block 2 sits at its top; **block 1 exceeds it on both
axes** (R² 0.056, IC 0.377) and block 0's IC 0.340 exceeds it too. Per the plan's own rule, an
OOF result above that band is a warning sign rather than good news, and it is flagged here
rather than celebrated.

## The negative-result log

**Seventeen records — one per config, all seventeen of them.** Cross-checked two ways:
`selection.json` carries 17 non-null `negative_run_id`s over 17 distinct `config_fingerprint`s,
and the live `harness-negative-results` experiment holds exactly 17 runs whose ids are the same
set and whose fingerprints are the same set. One reason string, in full:

    ineligible: 1 of 5 OOF blocks failed gate_forecast; first failure on oof_block_0:
    r2_vs_zero=-0.110394 <= 0 -- does not beat the constant-ZERO predictor, the reference
    D-07-18(a) names.

## Per-config wall clock

No config approached the 15-minute rule, and **no `ConvergenceWarning` was emitted** — the
warning filter was set to `always` precisely so that one would have been attributable to a
single fit rather than printed once per source location.

| # | model | α | total (5 blocks) | per block |
|---|---|---|---|---|
| 0 | LinearRegression | — | 8.22 s | 2.5 / 1.5 / 1.4 / 1.4 / 1.5 |
| 1–4 | Ridge | 1e-6 … 100 | 4.99–5.24 s | ≈1.0 each |
| 5–8 | ElasticNet (live) | 1e-6, 1e-4·0.15 | 3.43–3.65 s | 0.5–1.1 |
| 9–13 | ElasticNet (collapsed) | 1e-4, 1e-2 | 1.87–1.95 s | 0.3–0.5 |
| 14–16 | Ridge+poly2 | 1e-3 … 100 | 13.23–15.23 s | 1.2–4.7 |

Total estimator time 96.9 s. The sweep's 8 min of wall clock is dominated by re-reading the
634 MiB train cache once per fit (85 reads) and by the five block materializations (≈9 s each).

## The artifact

- `normalization_manifest_id` **`c7749334fb73e090272013dfeb10013e25948b11a0907d56a69ee39d7b8954ba`**
- partition `features_norm/symbol=BTCUSDT/train_end=2026-09-16/part-1790649157622680000.parquet`,
  4 rows (`imb_top`, `mid`, `ofi`, `trade_flow`), sha256 `86d0127f04c0…`
- `code_hash` `30efe7c11a8c2d6f3dd568d078a9ee143565becc` — clean
- `inputs` names 5 feature manifests, rows 4,193,137 / 6,864,853 / 11,323,694 / 12,203,294 /
  9,872,620; `etime_range` `[1789171200002000000, 1789602599845000000]`
- self-hash re-derived and confirmed; `check_manifest_id_integrity` and
  `check_manifest_append_only` both green as pre-commit hooks 15 and 16

**The 2026-09-13 artifact is byte-identical across this plan.** `sha256` of its parquet before
the run and after it: `d0aff38813423618cf4b3409592eda68ff2d56829cea73596b0536db5989f73e`,
both times, matching the sha256 its own manifest recorded in September.

## Deviations from Plan

### 1. [Rule 3 — blocking] The CLI produces no progress output, so the plan's "check as it goes" list was unobservable

`scripts/run_stage1_slice.py` never calls `logging.basicConfig`, so every `logger.info` in
`models/slice.py` and `models/sweep.py` — the row counts the plan asks to be checked against
the manifest, the reuse decisions, the per-config verdicts — is discarded. Python's default
warning filter additionally prints a `ConvergenceWarning` once per source location, so across
45 ElasticNet fits the plan's "record config, wall clock, warning text" would have been
unsatisfiable.

Fixed by invoking the SAME `main(["--select"])` through a wrapper held OUTSIDE the repo
(`--select` refuses a dirty tree, so the instrumentation could not be a repo file). It adds
`basicConfig(INFO)`, `captureWarnings(True)`, `simplefilter("always")`, and one log line per
`_SklearnTrainer.fit` call. No production behaviour changed; nothing was committed.

### 2. [Finding, not fixed] The plan's expected OOF training-row counts are 3,115 high on four of five blocks

Reported above rather than absorbed. The observed counts are right.

### 3. [Rule 4 — STOP] Tasks 2 and 3 are unexecutable: there is no winner

`--freeze` was run to prove this rather than assert it, and refused:

    NoEligibleConfigError: selection_winner_trainer: this selection records no winner
    (0 of 17 configs were eligible) -- there is nothing to freeze, and the best of the
    failures must not be promoted to the val look

It cost no look: the log shows `CACHE HIT … no look spent` for the train frame, and `val`
read 0 before and 0 after.

## A design note the next plan needs, whichever option is chosen

If the remedy is a NEW segment manifest with comparable blocks — D-05-14's remedy, and the one
the evidence points at — **it is not free of design work, because the normalisation artifact is
keyed on `(symbol, train_end_date)` alone.** `models.slice._existing_normalization` globs the
`train_end=<date>` parent for `part-*.parquet` and returns the manifest naming it. So a new
manifest whose train window still ends on 2026-09-16 — for example train 2026-09-13..16,
dropping the quiet day — derives the same `train_end_date`, finds `c7749334`'s parquet already
there, and REUSES it: an artifact whose `inputs` name 2026-09-12 and whose z-score parameters
were fit including it. Not a `val` leak, and not a rewrite of committed bytes, but a provenance
claim that would no longer be true of the window it was applied to, and `--select` would never
fit a fresh artifact for that window. Stated here, not fixed here.

## Known Stubs

None.

## What was NOT done

- **No `val` look. `look_count("val")` is 0 on both committed segment manifests.** Plan 07-11
  was not run, `--spend-val-look` was never passed, and no invocation ran in `mode="val"` —
  not against the real lake, not against a scratch tracking root, not as a dry run.
- **No predictor body exists.** `mvp/data/lake_registry/predictors/` was not created, on disk
  or in git.
- **Neither guardrail was extended.** `tools/check_manifest_append_only.py`,
  `tools/check_manifest_id_integrity.py` and both their test files are byte-unchanged.
  The plan requires the body and both extensions in ONE commit (D-07-22); with no body
  obtainable, committing the guardrails alone would break exactly the coupling that rule
  exists to enforce. The preparatory checks for that landing were done and are clean: no
  test pins either `REGISTRY_DIR_NAMES` literal, and `git log --all` shows no path
  containing `predictors` anywhere in history, so adding the name cannot make rule 2a's
  whole-history walk fail retroactively.
- **No mutation check was run**, because it belongs to the edit that was not made.
- **No grid was shrunk, no config was dropped, no `max_iter` was raised**, and no
  best-of-the-failures was promoted.
- **No `gsd-sdk state.advance-plan`, `state.record-metric` or `requirements.mark-complete` was
  run.** The plan did not complete, so advancing the counter or checking off FCST-01/FCST-04
  would assert something false. STATE.md was hand-edited instead: the plan counter still reads
  9 of 11, the status reads blocked, and the new blocker names the decision.
- **The observability wrapper was not committed**, by design — `--select` refuses a dirty tree,
  so it could not be a repo file, and it is not production code.
- **Nothing in the scratch cache was deleted.** The five block frames and the train frame
  remain at
  `/Volumes/ProjectsSSD/aihedgefund/scratch/phase07/0d85c8daacc2fe5e/807125015b25…/`, which
  is what makes a re-run of the sweep cost zero additional looks.
- The capture daemon was not started, signalled or touched.

## Self-Check

**PASSED.** Verified on disk: the `features_norm` manifest
`c7749334fb73…json` exists; this summary exists; commit `cb21c94` resolves in `git log`;
`git diff HEAD -- mvp/tools mvp/tests/tools` is empty (both guardrails and both test files
byte-unchanged); `mvp/data/lake_registry/predictors/` does not exist. Look counts re-queried
against the live store after the last commit: `val` 0.
