---
phase: 07-regression-track-vertical-slice
plan: 10
subsystem: models
tags: [oof-sweep, look-budget, model-selection, frozen-predictor, registry-guardrail, negative-result-log, normalization-artifact, selection-bias, single-feature-winner]

# Dependency graph
requires:
  - phase: 07-02
    provides: "segment manifest 807125015b25 -- compressed_3seg, train 2026-09-12..16, val 17..18, five one-calendar-day oof_blocks, budget_allowance 3"
  - phase: 07-04
    provides: "models/frozen.py write_frozen_predictor/read_frozen_predictor -- the self-hashed coefficient body this plan lands the FIRST real instance of"
  - phase: 07-08
    provides: "models/cache.py materialize_once and models/sweep.py run_oof_sweep + selection.json, including the NO-ELIGIBLE-CONFIG outcome the first sweep met"
  - phase: 07-09
    provides: "dq_preflight, called before the first materialize -- it returned ([], []) and refused nothing"
  - phase: 05-07
    provides: "the per-directory REGISTRY_DIR_NAMES loop and rule 5's per-directory vacuity refusal, which this plan extends to a fourth name"
provides:
  - "mvp/data/lake_registry/predictors/e3b4b235d0fe...json -- THE FROZEN WINNER. predictor_id ff91c9aa2a59, ElasticNet(alpha=1e-4, l1_ratio=0.30), coef [imb_top 1.3252347394352176e-05, ofi 0.0, trade_flow -0.0], intercept 1.2101376491973044e-06, 44,229,781 rows fitted / 224,702 dropped, code_hash f28c1efb (clean)"
  - "mvp/data/lake_registry/manifests/BTCUSDT.features_norm/c7749334fb73...json -- the 7-day window's train-only normalisation artifact at train_end=2026-09-16, ADDITIVE beside the 2026-09-13 one"
  - "predictors/ inside both registry scanners' REGISTRY_DIR_NAMES, so a committed coefficient body cannot be deleted or rewritten in place without the commit being refused"
  - "five spent OOF looks and their five harness-looks runs, with val untouched at 0 across three separate runs of the slice"
  - "44 negative-result records -- 17 at the pre-widening code_hash, 27 at the widened one. 36 is the selection-bias denominator"
  - "the measured finding that the five OOF blocks are not exchangeable: tie_fraction runs 0.70 / 0.44 / 0.12 / 0.12 / 0.12 across them"
  - "the measured finding that the eligibility gate separated six copies of ONE single-feature model by amplitude alone, with their rank ordering held constant"
affects: [07-11, "any re-plan of the regression track's grid or its segment geometry", "Phase 8's cross-class selection", "Stage 2's threshold policy, via the scale-invariance finding"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Observability without a repo file: the slice CLI refuses a dirty tree, so the logging/warning/per-fit instrumentation this run needed lived in a scratchpad wrapper OUTSIDE the repo that imports `scripts.run_stage1_slice.main` unchanged. Same entry point, same behaviour, nothing to commit"
    - "A plan's expected row counts must be derived through the SAME purge the materialize applies. Four of the five expected OOF training-row counts were each exactly 3,115 high -- the purge band's own size -- because they were computed off the raw 44,457,598 rather than the cached 44,454,483"
    - "A guardrail test whose registry holds ONLY the tampered artifact proves nothing: the GLOBAL vacuity guard returns the same exit code, so the test stays green with the extension removed. Both new id-integrity tests keep a healthy `manifests/` manifest beside the predictor body on purpose, which is what leaves exit 1 exactly one available cause"

key-files:
  created:
    - mvp/data/lake_registry/manifests/BTCUSDT.features_norm/c7749334fb73e090272013dfeb10013e25948b11a0907d56a69ee39d7b8954ba.json
    - mvp/data/lake_registry/predictors/e3b4b235d0fe3c025be7c7cdbf7eaa3d2d5675ad9571d6f6a30b1a45680c2d01.json
  modified:
    - mvp/tools/check_manifest_append_only.py
    - mvp/tools/check_manifest_id_integrity.py
    - mvp/tests/tools/test_check_manifest_append_only.py
    - mvp/tests/tools/test_check_manifest_id_integrity.py

key-decisions:
  - "THE WINNER IS A ONE-FEATURE MODEL. `imb_top` alone; `ofi` and `trade_flow` are exactly 0.0 in the frozen body and on all five OOF blocks. Verified from the 180 committed coefficient vectors, not read off a claim."
  - "THE SELECTION-BIAS DENOMINATOR IS 36, not 9 and not 17. The grid was widened from 17 to 36 AFTER seeing that 0 of 17 passed; the negative-result log holds 44 records because `code_hash` is in the hashed recipe and the 17 re-tried configs got fresh fingerprints."
  - "THE GATE SEPARATED SIX COPIES OF ONE MODEL BY AMPLITUDE. Grids 8, 25, 26, 27, 28 and 29 are the same imb_top-only fit at five distinct amplitudes (27 and 28 share the L1 product 3.6e-5 reached two ways). Rank correlation cannot see a positive scale factor, so their per-block rank ICs agree to nine significant figures -- and the gate passed three and refused three on where `r2_vs_zero` landed."
  - "MEAN RANK IC BARELY DISCRIMINATES AMONG THE ELIGIBLE: 0.272056 (winner) against 0.271238 / 0.271180 / 0.271062 / 0.270995 / 0.268886 / 0.262117 for the other eligible rows. The winner is chosen on roughly the fourth decimal."
  - "NO BAR WAS MOVED. `gate_forecast`, `gate_baseline`, the all-blocks requirement, the reference band, `select_winner` and GRID are byte-unchanged in this task; `git show --stat` on the one commit lists five paths and none of them is a gate."
  - "FCST-01 and FCST-04 were NOT marked complete. FCST-04 asks for a frozen predictor producing PRECOMPUTED PREDICTION TABLES keyed by segment manifest; no prediction table exists for a real segment until 07-11 runs. Marking either now would assert something false."
  - "The one-commit rule proved itself on the real registry rather than in a fixture: `test_real_committed_registry_is_append_only` went RED the moment the frozen body was on disk unstaged (rule 5's per-directory vacuity refusal, naming `predictors/`) and green the moment it was staged."
  - "NO CONFIG WAS ELIGIBLE IN THE FIRST SWEEP -- 0 of 17. That is the first-class outcome models/sweep.py was built to express, and `--freeze` was run then too, refusing with NoEligibleConfigError after a train CACHE HIT that spent no look."
  - "TWELVE of the first seventeen configs fail on oof_block_0 ALONE and pass every other block. On 2026-09-12 they post r2_vs_zero = -0.11 while carrying the HIGHEST rank IC of the five blocks (0.34). Right ordering, wrong scale."
  - "The mechanism is measured, not guessed: tie_fraction on oof_block_0 is 0.7023 against 0.4389 / 0.1226 / 0.1241 / 0.1151 on the others. 2026-09-12 is a full 24 h day (verified against its partition's etime span) carrying 4.19M book updates against 09-16's 9.87M -- 48.5/s against 114/s."
  - "FIVE of the first seventeen configs (grid 9-13) could never have produced a non-constant predictor: every ElasticNet whose alpha*l1_ratio exceeds the measured max |X'(y-ybar)/n| of 4.328e-5 shrinks every coefficient to exactly zero."
  - "The 2026-09-13 normalisation artifact is byte-identical throughout: sha256 d0aff38813423618cf4b3409592eda68ff2d56829cea73596b0536db5989f73e. The new one is additive at its own train_end (D-07-11)."

patterns-established:
  - "Pattern: when a gate's message quotes a number, check whether it is measured or canned before repeating it. gate_forecast's not-finite branch names r2_vs_zero=+0.001647 as 'the measured shape of this failure' -- that is a 07-RESEARCH Q4 literal in models/gates.py:383, not a measurement of the run that printed it."
  - "Pattern: a per-block spread is not one number. The winner has mean IC 0.272 with ic_min 0.192 and ic_max 0.375 -- and in the first sweep the all-blocks rule refused that very spread on the block whose IC was second highest."
  - "Pattern: a scale-invariant selection metric plus a scale-sensitive gate means the gate, not the metric, picks the winner. Six identical-ordering models went FAIL/FAIL/PASS/PASS/PASS/FAIL purely on amplitude."

requirements-completed: []

# Metrics
duration: "~35 min (first --select, itself 10 min 37 s) + ~25 min (widening + re-select, zero looks) + ~50 min (freeze, guardrails, mutation checks, suite)"
completed: 2026-09-29
---

# Phase 7 Plan 10: Five OOF looks, a widened grid, and the winner frozen before val Summary

**One number out of the order book — the top-of-book size imbalance — is now
the whole model, its coefficient is committed to git where a rewrite is
refused, and the validation window has still never been read.**

Three runs of the slice sit behind that sentence, and the order matters for
reading the rest: a first sweep over 17 configurations in which **nothing was
eligible**; a widening of the grid to 36 and a re-score that cost **no look**
because every frame came from cache; and this task's `--freeze`, which refit
the winner on the train cache and wrote its coefficients into the registry.
`look_count("val")` read 0 before all three and reads 0 after all three.

## The frozen winner, in one place (what 07-11 needs)

| field | value |
|---|---|
| `predictor_manifest_id` | `e3b4b235d0fe3c025be7c7cdbf7eaa3d2d5675ad9571d6f6a30b1a45680c2d01` |
| `predictor_id` | `ff91c9aa2a595513f407c4a01ad794ebc4b5ccd3595c61d5dabdcc426298c3ff` |
| `normalization_manifest_id` | `c7749334fb73e090272013dfeb10013e25948b11a0907d56a69ee39d7b8954ba` |
| `segment_manifest_id` | `807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548` |
| model | `sklearn.ElasticNet`, grid position **26** of 36 |
| hyperparameters | `alpha=1e-4, l1_ratio=0.30, max_iter=1000, tol=1e-4, selection=cyclic` |
| `coef` | `imb_top 1.3252347394352176e-05`, `ofi 0.0`, `trade_flow -0.0` |
| `intercept` | `1.2101376491973044e-06` |
| `train_target_mean` | `1.2207031283365225e-06` |
| rows | 44,229,781 fitted, 224,702 dropped (of the 44,454,483-row train cache) |
| `seed` / `code_hash` | `20260925` / `f28c1efb6080bf31039e21ccc11de6279264cd06` (clean) |
| per-block rank IC | mean **0.272056**, min 0.192094, max 0.375235, std 0.073615 |
| file sha256 | `3d7734a757e0fa5d51bd67b3a40ce14513ee7d045672ad8a6072dcf112cc89e0` |

`design` is `"linear"`, derived from the recipe rather than stored. The body
reads back through `read_frozen_predictor` with its self-hash re-derived from
the body, its `predictor_id` re-derived from the five recipe fields, and the
filename stem all three agreeing — checked by running it, not by reading the
writer.

**The winner crossed the process boundary on disk, not in memory.** `--select`
ran at `code_hash` `9c5f9456` and wrote
`selection.json`; `--freeze` ran two commits later at `f28c1efb` and read it.
The body is located by the recipe **minus `code_hash`**, which is why the
select-time `config_fingerprint` (`a2efe6c235de`) and the freeze-time
`predictor_id` (`ff91c9aa2a59`) differ by design.

## DISCLOSURE: how this winner was obtained

This section is the honest record. Every number in it was recomputed here from
the committed evidence or from the frozen body — none is transcribed from a
prior claim, and one prior claim turned out to be wrong (finding 6).

### 1. It is a ONE-FEATURE model

`ofi` and `trade_flow` are exactly `0.0` and `-0.0` — in the frozen full-train
body and on every one of the five OOF blocks. Only `imb_top` survives.

This is not shrinkage of three features toward zero; it is L1 selecting one.
The widening commit measured `|X'(y - ybar)/n|` per column on the train frame:
`imb_top` 4.328e-5, `trade_flow` 7.453e-6, `ofi` 4.138e-6. Any ElasticNet
whose `alpha * l1_ratio` exceeds 7.453e-6 kills both of the others, and any
above 4.328e-5 kills all three. The winner's product is 3.0e-5 — above the
first threshold, below the second. **Every live ElasticNet in the widened grid
is therefore an `imb_top`-only model, including grid 8 from the original 17.**

### 2. The selection-bias denominator is 36

Not 9 (the eligible count) and not 17 (the original grid). The grid was
widened from 17 to 36 configurations **after** the first sweep showed 0 of 17
eligible, and `GRID_SIZE` only grows: 36 is the number of configurations this
phase has tried. It is not recoverable by deleting rows.

The negative-result log holds **44 records** — verified by querying the live
`harness-negative-results` experiment, which returns 44 runs over 44 distinct
`config_fingerprint`s: **17 at `code_hash` `30efe7c1`** (the pre-widening run)
and **27 at `9c5f9456`** (36 minus the 9 eligible). The 17 re-tried configs got
fresh fingerprints because `code_hash` is inside the hashed recipe, so
`warn_if_already_negative` could not fire on them. None of the 44 carries a
`segment_name` or a `scored_segment_name` tag, so none can be miscounted as a
look.

### 3. The gate separated identical-ranking models by amplitude

Six configurations — grids **8, 25, 26, 27, 28, 29** — are the same
`imb_top`-only model at different amplitudes. Within each block they are
positive multiples of one feature, so their **rank ordering is identical**:
rank correlation cannot see a positive scale factor. Measured, on all five
blocks, their `rank_ic_non_tied` values agree to **nine significant figures**
(block 0: `0.342703924133` through `0.342703924300`, a spread of 1.7e-10 —
rounding in the rank of ~4M float products, not a difference in what the model
orders).

Their mean rank ICs likewise agree to nine figures: `0.272056441665`,
`…682`, `…678`, `…673`, `…666`. **The selection metric cannot tell them apart.**

The gate can, because `r2_vs_zero` is scale-sensitive, and on `oof_block_0`
seventy per cent of the returns are exactly zero, so predicting motion there
is punished in proportion to how loudly you predict it:

| grid | `alpha`·`l1_ratio` | coef on block 0 | `r2_vs_zero` block 0 | verdict |
|---|---|---|---|---|
| 8 | 1.5e-5 | 3.0951e-05 | **−0.029429** | refused (block 0) |
| 25 | 2.0e-5 | 2.6108e-05 | **−0.012582** | refused (block 0) |
| 26 | 3.0e-5 | 1.6430e-05 | +0.007312 | **eligible — the winner** |
| 27 | 3.6e-5 | 1.0621e-05 | +0.010435 | eligible |
| 28 | 3.6e-5 | 1.0619e-05 | +0.010435 | eligible |
| 29 | 4.0e-5 | 6.7473e-06 | +0.008842 | refused (block 4) |

Three passed, three were refused, with ordering skill held exactly constant.
Grids 27 and 28 reach the **same** L1 product two ways (1.2e-4 × 0.30 and
3.6e-4 × 0.10) and come out identical to four significant figures, which is
the widening commit's own pre-registered prediction landing.

**One correction to how this is usually stated.** Two of the three refusals
(8 and 25) are what the sentence above describes — the MSE on the tie-heavy
block. The third, grid 29, was refused on a **different** branch and a
different block: at `alpha·l1_ratio = 4.0e-5` it shrank `imb_top` to exactly
zero on `oof_block_4`, so `spearmanr` returned NaN for a constant input and
`gate_forecast`'s not-finite branch refused it there. It is the same axis, one
notch further along, but it is not an MSE verdict.

The per-block scale ratios differ from block to block (the winner against grid
8: 0.531 / 0.500 / 0.424 / 0.451 / 0.406) because each block is fitted
separately on its own training rows.

### 4. Mean rank IC barely discriminates among the eligible

All nine eligible configurations, by the selection rule's own metric:

| grid | model | mean rank IC |
|---|---|---|
| 26 | ElasticNet α=1e-4, l1=0.30 | **0.272056** ← winner |
| 27 | ElasticNet α=1.2e-4, l1=0.30 | 0.272056 |
| 28 | ElasticNet α=3.6e-4, l1=0.10 | 0.272056 |
| 31 | Ridge+poly2 α=1e8 | 0.271238 |
| 20 | Ridge α=1e8 | 0.271180 |
| 21 | Ridge α=3e8 | 0.271062 |
| 22 | Ridge α=1e9 | 0.270995 |
| 32 | Ridge+poly2 α=3e8 | 0.268886 |
| 33 | Ridge+poly2 α=1e9 | 0.262117 |

OLS (grid 0, ineligible) sits at 0.271184 and the whole Ridge ladder at
≈0.2710. **The winner is chosen on roughly the fourth decimal**, and the
tie-break inside the top three (mean `r2_vs_mean`, then ascending grid
position) is what actually separated 26 from 27 and 28.

### 5. A specification concern about `gate_forecast`, reported and NOT acted on

For a positive multiple of a single feature, a threshold policy's trade *set*
is invariant to that multiple up to the intercept: scaling `c·x + b` and
scaling the threshold with it selects the same rows. So Stage 2's threshold
sweep can re-optimise away exactly the degree of freedom this gate selected
on, while the ordering the gate held constant is the thing Stage 2 cannot
recover.

Read plainly: the gate's `r2_vs_zero` leg is doing amplitude calibration on
Stage 1, and Stage 2 recalibrates amplitude anyway. That does not make the
gate wrong — a Stage-1 forecast that loses to predicting zero is worth
knowing about — but it means the CHOICE among six ordering-equivalent models
was made on a quantity the next stage re-fits. **Nothing was changed. No gate,
threshold, eligibility rule, reference band, selection rule or grid row was
touched in this task.** It is written down for whoever plans the next grid.

### 6. The earlier "do-nothing model" risk is falsified — but not at 12.6 ticks

The concern was that L1 had shrunk the model into something that never clears
the spread. It has not. Arithmetic on the frozen body, stating its three
inputs:

- frozen `coef[imb_top]` = 1.3252347394352176e-05, a **fractional** 10-second
  return per 1σ of the z-scored feature (`spec.md`: `ret_10s_mid` is
  `(mid_{t+10s} − mid_t) / mid_t`)
- `mid` mean over the train window = 76,933.19 USDT, read from the
  `features_norm` artifact the predictor is keyed to
- tick = 0.1 USDT (`sim/ticks.py`, pinned against the venue's measured gcd)

1.3252347e-05 × 76,933.19 = **1.0195 USDT = 10.20 ticks per σ**, against a
half-spread of **0.5 tick** at the 1-tick spread that holds 97.5% of the time.
Twenty times the half-spread. It is not a do-nothing model.

**The 12.6 ticks/σ figure carried in from the prior discussion is wrong for
this body.** It is `oof_block_0`'s own fit of the same configuration
(1.6430e-05 × 76,933.19 / 0.1 = 12.64). The frozen predictor is the full-train
refit and its coefficient is smaller. The conclusion is unchanged; the number
is not.

For scale: one σ of the target itself is 1.447e-04 (measured on 2026-09-13 per
decision row) = 111 ticks, so the model's per-σ prediction is 9.2% of a target
σ. **This is arithmetic on spreads. Nothing has been simulated — no P&L, no
Sharpe, no fill.**

## The one commit, and proof the guardrail protects it

`b75edc6` — `feat(07-10): freeze the winner, and the guardrail that makes it immutable`

```
 mvp/data/lake_registry/predictors/e3b4b235d0fe...json  (new)
 mvp/tests/tools/test_check_manifest_append_only.py
 mvp/tests/tools/test_check_manifest_id_integrity.py
 mvp/tools/check_manifest_append_only.py
 mvp/tools/check_manifest_id_integrity.py
```

Five paths, one commit, all 19 hooks green, never `--no-verify`. The two source
edits are one name each: `"predictors"` joins `REGISTRY_DIR_NAMES` in both
scanners. `check_no_manifest_rewrite.py` is untouched — a predictor body names
no lake bytes, so it has nothing to stat — and no `.pre-commit-config.yaml` or
CI change was needed, so `test_ci_pre_commit_parity.py` passes unmodified
(confirmed by running it, 4 passed).

Both checkers now report the new directory in scope:

```
check_manifest_id_integrity : checked 143 manifest(s) for id/filename self-consistency
check_manifest_append_only  : PASS: 143 committed manifest(s) append-only ...
  by registry directory: errata/=1, manifests/=139, predictors/=1, segments/=2
```

### The one-commit rule enforced itself on the real registry

Not a fixture argument. With the body written to disk and the guardrail edited
but the body **not yet staged**, the real-tree test went red:

```
test_real_committed_registry_is_append_only
  AssertionError: Left contains one more item:
  'HEAD and the index both track 0 manifests under
   mvp/data/lake_registry/predictors/ -- refusing a vacuous pass'
```

`git add` on the body turned it green. That is rule 5's per-directory vacuity
refusal (D-05-07's lesson, D-07-22's requirement) firing on the exact window
the one-commit rule exists to close.

### The seven new tests

`test_check_manifest_append_only.py` (4):

- `test_predictors_directory_is_covered_and_counted` — `([], 2)` and
  `predictors/=1` in the breakdown line
- `test_untracked_predictor_body_is_a_vacuous_pass_failure_naming_only_predictors`
  — the vacuity refusal names `predictors/` and does **not** name `manifests/`
- `test_committed_rewrite_of_a_frozen_predictor_body_is_caught` — double the
  one live coefficient, commit it, rule 2a reports `modified`
- `test_uncommitted_rewrite_of_a_frozen_predictor_body_is_caught` — the same
  edit left in the working tree, rule 3 reports
  `modified in the working tree`. This is the half that actually fires on a
  developer's machine, which makes the hook a gate rather than an audit.

`test_check_manifest_id_integrity.py` (3):

- `test_main_checks_the_predictors_directory_too` — `checked 2 manifest(s)`
- `test_main_fails_and_names_a_hand_edited_predictor_body` — exit 1, the
  filename in the message, `body hand-edited or corrupted`
- `test_check_manifest_file_passes_on_an_untouched_predictor_body`

All seven build the body through the **production writer**,
`models.frozen.write_frozen_predictor`, not a hand-rolled lookalike: what the
tests protect has to be what `--freeze` actually emits, down to the key set the
self-hash covers.

**One design trap worth naming, because it is how a green test proves nothing.**
Had the id-integrity tamper test put *only* a tampered predictor body in the
scratch registry, then with `"predictors"` removed `_iter_manifest_files` would
return empty, the **global** vacuity guard would fire, and `main()` would still
exit 1 — the test would pass while saying nothing about predictors at all. The
existing `segments`/`errata` tamper tests have exactly that shape. Both new
tests therefore keep a healthy `manifests/` manifest beside the body, which
leaves exit 1 exactly one available cause. This was the specific failure mode
07-08 shipped once already.

### The mutation check, on both files

Three hashes each, and `git diff --stat` against the commit as the machine
check on byte-identical restore.

`mvp/tools/check_manifest_id_integrity.py`

| | sha256 |
|---|---|
| H1, at `b75edc6` | `76a8560c818421a7f90cd8b9c80773b85a708ddd5b5c43e3baaf050d6b42e69a` |
| H2, reverted | `2e9f3976aca0b83ae73fa1fe658f59078d5cf4784d4ab0888b142719ab5368cc` (**changed** — the edit was not inert) |
| H3, restored | `76a8560c818421a7f90cd8b9c80773b85a708ddd5b5c43e3baaf050d6b42e69a` (**= H1**, and `git diff --stat` empty) |

The reverted block — a real semantic revert, not a deleted line:

```python
-REGISTRY_DIR_NAMES: tuple[str, ...] = (
-    "manifests",
-    "segments",
-    "errata",
-    "predictors",
-)
+REGISTRY_DIR_NAMES: tuple[str, ...] = ("manifests", "segments", "errata")
```

With it reverted, `test_main_fails_and_names_a_hand_edited_predictor_body`
FAILED on `assert main([]) == 1` → `assert 0 == 1`. **A hand-edited coefficient
went unnoticed.** `test_main_checks_the_predictors_directory_too` FAILED too,
on `checked 1 manifest(s)` where it wants 2.

`mvp/tools/check_manifest_append_only.py`

| | sha256 |
|---|---|
| H1, at `b75edc6` | `68cd8fec2dfecd79bc2a290d0e66ba8eda02bfd9b7272db50fb75a769404297f` |
| H2, reverted | `9b64d5b9c35845822103c6ad342010f049f179a378d3b9b2f4d69a91f65c52a1` (**changed**) |
| H3, restored | `68cd8fec2dfecd79bc2a290d0e66ba8eda02bfd9b7272db50fb75a769404297f` (**= H1**, `git diff --stat` empty) |

With `frozenset({"manifests", "segments", "errata", "predictors"})` reverted to
the three-name set, all four new tests FAILED, and the shape of the failures is
the point:

- `test_committed_rewrite_of_a_frozen_predictor_body_is_caught`:
  `assert any(...), errors` → `assert False` with **`errors` empty**. The
  rewrite is not reported as a lesser problem; it is invisible.
- `test_uncommitted_rewrite_of_a_frozen_predictor_body_is_caught`: same.
- the vacuity test: no error naming `predictors/`.
- the counted test: `([], 1) == ([], 2)`.

Restores used `git checkout -- <specific path>`, never a blanket reset.

## The look budget, on all sixteen counters

Queried by `harness.budget.look_count` against the real store at
`/Volumes/ProjectsSSD/aihedgefund/mlflow` — 2 committed segment manifests × 8
segment names — immediately before this task's first command and again after
its last.

| manifest | train | **val** | held_out | oof_block_0 | oof_block_1 | oof_block_2 | oof_block_3 | oof_block_4 |
|---|---|---|---|---|---|---|---|---|
| `807125015b25` BEFORE | 0 | **0** | 0 | 1 | 1 | 1 | 1 | 1 |
| `807125015b25` AFTER | 0 | **0** | 0 | 1 | 1 | 1 | 1 | 1 |
| `97964cb27f62` BEFORE | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0 |
| `97964cb27f62` AFTER | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0 |

**Total 5 before, total 5 after. `look_count("val")` is 0 on both manifests.**
Nothing moved, and the reason is in the `--freeze` log rather than in an
argument:

```
materialize_once: CACHE HIT .../train.parquet (44454483 rows) -- no look spent
run_slice: reusing features_norm artifact c7749334fb73 ... no second fit
OK: looks spent by this invocation: none
lake: 0 created, 0 changed, 0 removed
registry: 1 created, 0 changed, 0 removed
  + predictors/e3b4b235d0fe...json
```

Stronger than the counters: the `harness-looks` experiment holds exactly five
runs; their `segment_name` tags are exactly the five block names; all five
carry the ORIGINAL `code_hash` `30efe7c1`, i.e. no run was added by the
widening or by the freeze. `--freeze` took 4.7 s.

## Full suite

**1399 passed in 314 s**, run by me from `mvp/` with `./.venv/bin/python3 -m
pytest tests -q`, and again as pre-commit hook 19 during the commit. The
baseline at the widening commit was 1392; the seven new guardrail tests account
for the difference exactly.


---

# The record of the two earlier runs

## Run 2: the widened grid, 9 of 36 eligible, zero looks spent

Committed as `9c5f945` (the grid) and `f28c1ef` (the result), with the raw
numbers as JSON under `evidence/07-12-*.json` rather than as prose. In brief,
because those commit messages carry the detail:

- **Why widen.** The original 17-config grid never sampled shrinkage at all.
  sklearn's `Ridge` minimises the SUM of squared residuals, so on the z-scored
  design `alpha` competes with `sum(x²)`, which IS the row count — 44,229,781.
  Rows 1–4 (`alpha` 1e-6…100) can shrink the fit by at most 2.3e-6, which is
  why they measured identical to six decimals on all five blocks. Nineteen rows
  were APPENDED, so positions 0–16 keep naming the configs the first sweep's
  `selection.json` and its seventeen negative records name.
- **Pre-registered.** `evidence/07-12-prereg.json` was written BEFORE the
  re-run, from a train-only Gram/shrinkage calibration
  (`evidence/07-12-shrinkage-calibration.json`) — the train role costs no look
  and no `oof_block` frame was read to design the ladder.
- **Zero looks.** Every OOF frame was a cache hit and the `features_norm`
  artifact was reused; all six cached frames, all six provenance sidecars and
  both `features_norm` parquets are byte-identical before and after. Only
  `selection.json` changed.
- **9 of 36 eligible**: grids 20/21/22 (Ridge 1e8/3e8/1e9), 26/27/28
  (ElasticNet), 31/32/33 (Ridge+poly2 1e8/3e8/1e9). Winner by the unchanged
  rule: grid 26.

## Run 1: the first sweep, 0 of 17 eligible

Everything below is the record as written on 2026-09-29 after the first
`--select`, preserved because "the grid was widened after seeing 0 of 17"
only means something if the 0-of-17 record survives. Its two-commit count,
its "nothing was frozen" statements and its "Tasks 2 and 3 are unexecutable"
deviation describe that run and are superseded by the sections above.

**The regression grid can order the rows that move on every one of the five out-of-fold days, and on the quietest of them its squared error is worse than predicting zero — so none of the seventeen configurations earned the validation look, and none was frozen.**

### Performance

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

### The look budget, on all sixteen counters

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

### What the run did, against what the plan expected

| measurement | expected by the plan | observed | verdict |
|---|---|---|---|
| train rows | 44,454,483 | 44,454,483 | agrees |
| `train_end_date` | 2026-09-16 | 2026-09-16 | agrees, and DERIVED |
| source feature manifests | 5 of the segment's 7 | 5 (09-17, 09-18 excluded by name in the log) | agrees |
| oof_block_0..4 admitted | 4,137,073 / 6,864,853 / 11,291,365 / 12,142,146 / 9,814,111 | identical | agrees |
| oof_block_0..4 training rows | 40,242,038 / 37,488,354 / 32,997,430 / 32,094,158 / 34,530,706 | 40,238,923 / 37,485,239 / 32,994,315 / 32,091,043 / 34,530,706 | **the plan is wrong by 3,115 on the first four** |
| `code_hash` | no `-dirty` | `30efe7c11a8c2d6f3dd568d078a9ee143565becc` | clean |
| `dq_preflight` | `([], [])`, refuses nothing | `dq_ack_ids none` | agrees |

#### The 3,115 rows, and why only four blocks felt them

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

### The result: 0 of 17 eligible

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

#### Why oof_block_0 refuses everything

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

### The negative-result log

**Seventeen records — one per config, all seventeen of them.** Cross-checked two ways:
`selection.json` carries 17 non-null `negative_run_id`s over 17 distinct `config_fingerprint`s,
and the live `harness-negative-results` experiment holds exactly 17 runs whose ids are the same
set and whose fingerprints are the same set. One reason string, in full:

    ineligible: 1 of 5 OOF blocks failed gate_forecast; first failure on oof_block_0:
    r2_vs_zero=-0.110394 <= 0 -- does not beat the constant-ZERO predictor, the reference
    D-07-18(a) names.

### Per-config wall clock

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

### The artifact

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

### Deviations from Plan

#### 1. [Rule 3 — blocking] The CLI produces no progress output, so the plan's "check as it goes" list was unobservable

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

#### 2. [Finding, not fixed] The plan's expected OOF training-row counts are 3,115 high on four of five blocks

Reported above rather than absorbed. The observed counts are right.

#### 3. [Rule 4 — STOP] Tasks 2 and 3 are unexecutable: there is no winner

`--freeze` was run to prove this rather than assert it, and refused:

    NoEligibleConfigError: selection_winner_trainer: this selection records no winner
    (0 of 17 configs were eligible) -- there is nothing to freeze, and the best of the
    failures must not be promoted to the val look

It cost no look: the log shows `CACHE HIT … no look spent` for the train frame, and `val`
read 0 before and 0 after.

### A design note the next plan needs, whichever option is chosen

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

---

## Known Stubs

None. No placeholder value, empty collection or "coming soon" string was
introduced. The frozen body's two zero coefficients are a MEASURED L1
selection result, not a stub — see disclosure finding 1.

## Threat Flags

None. The two files changed are CI guardrails and the one artifact added is a
read-only JSON body in an append-only registry; no network endpoint, auth
path, file-access pattern or schema at a trust boundary was introduced.

The plan's own threat register is satisfied and measured: **T-07-37** (the val
budget) — no `val` materialize, no `--spend-val-look`, counters printed before
and after; **T-07-38** (the frozen coefficients) — self-hashed body in an
append-only registry with both scanners extended in the same commit and the
vacuity rule shown live; **T-07-39** (a normalization artifact rewritten in
place) — unchanged from run 1, both parquets byte-identical; **T-07-40** (a
result too good to be true) — the reference-band comparison is carried forward
below and the checkpoint is where a human reads it.

## Deviations from Plan

### 1. [Finding, reported] The prior 12.6 ticks/σ figure is `oof_block_0`'s fit, not the frozen body's

Recomputed from the frozen coefficient: **10.20 ticks/σ**, not 12.6. Disclosure
finding 6 states the three inputs and the correction. The conclusion the number
supported — the winner is not a do-nothing model — is unchanged and, at 20× the
half-spread, not close to the boundary.

### 2. [Finding, reported] The third refusal in the six-amplitude set is a different gate branch

Grids 8 and 25 were refused on `oof_block_0`'s `r2_vs_zero`. Grid 29 was
refused on `oof_block_4` because it collapsed to a constant there and
`spearmanr` returned NaN. Same shrinkage axis, one notch further, different
branch. Reported rather than smoothed into "all three failed on MSE".

### 3. [Rule 2 — test design] The tamper tests were built to exclude a false pass

The existing `segments`/`errata` tamper tests would stay green with the
guardrail extension removed, because the global vacuity guard returns the same
exit code when the registry scans to empty. Both new id-integrity tests keep a
healthy `manifests/` manifest beside the tampered body so exit 1 has exactly
one cause. No existing test was modified.

### 4. [Not a deviation, recorded] `FCST-01` / `FCST-04` were not marked complete

FCST-04 asks for a frozen predictor producing **precomputed prediction tables
keyed by segment manifest**; no prediction table exists for a real segment
until 07-11 spends the val look. `requirements.mark-complete` was therefore not
run, and `REQUIREMENTS.md` still reads Pending for both.

### 5. Run 1's deviations, carried forward unchanged

Its three are recorded in the nested Run-1 record: the CLI's missing
`logging.basicConfig` (fixed with a wrapper outside the repo), the plan's
OOF training-row counts being 3,115 high on four of five blocks (the plan was
wrong; the observed counts are right), and the then-correct STOP on there being
no winner to freeze.

## Against the pre-phase reference band

The plan asks for an OOF result far ABOVE the train-internal band (R² vs zero
0.013–0.048, rank IC 0.19–0.29) to be treated as a warning, not good news. For
the winner: `r2_vs_zero` runs +0.0073 / +0.0379 / +0.0162 / +0.0054 / +0.0059
— **below or inside** the band on every block, which for a shrunk model is the
expected direction. Rank IC runs 0.343 / 0.375 / 0.243 / 0.192 / 0.207 —
**block 1 exceeds the band's top (0.29) and block 0 sits near it**, and those
are the same two blocks flagged in run 1. That excess is a property of the
blocks, not of the winner: every ordering-equivalent config in the grid carries
the same five numbers to nine figures. It is flagged here rather than
celebrated, and it is one of the things the checkpoint asks a human to weigh.

## What was NOT done

- **No `val` look. `look_count("val")` is 0 on both committed segment
  manifests, queried before this task's first command and after its last.**
  Plan 07-11 was not run; `--spend-val-look`, `--respend-val-look` and
  `--resume-from-cache` were never passed; nothing ran in `mode="val"` — not
  against the real lake, not against a scratch tracking root, not as a dry run.
  No run in the store carries a `val` `segment_name` tag, and no run carries
  `scored_segment_name` at all.
- **No gate, threshold or rule was moved.** `models/gates.py`,
  `models/sweep.py`'s `select_winner`, the all-blocks eligibility rule, the
  reference band and `GRID` are byte-unchanged in this task. The one commit
  touches five paths and none of them is a model file.
- **No config was promoted that the rule did not choose**, no runner-up was
  frozen, no second body was written, and `GRID` was not widened again.
- **Nothing was simulated.** No P&L, no Sharpe, no fill, no prediction table,
  no `sim` invocation. Disclosure finding 6 is arithmetic on spreads and says
  so.
- **No committed manifest body was rewritten**, and neither `features_norm`
  parquet was touched: `d0aff388…` (2026-09-13) and `86d0127f…` (2026-09-16)
  are byte-identical to what their manifests recorded.
- **Nothing was deleted from the scratch cache.**
  `/Volumes/ProjectsSSD/aihedgefund/scratch/phase07/0d85c8daacc2fe5e/807125015b25…/`
  still holds the train frame, the five block frames, the five row masks and
  `selection.json` — which is what keeps a re-run at zero additional looks.
- **`check_no_manifest_rewrite.py` was not touched**, deliberately: a predictor
  body names no lake bytes, so it has nothing to stat.
- **No `.pre-commit-config.yaml` or CI workflow change was made**, so
  `test_ci_pre_commit_parity.py` passes unmodified — confirmed by running it,
  not assumed.
- **`FCST-01` and `FCST-04` were not checked off**, and
  `requirements.mark-complete` was not run. See deviation 4.
- **`gsd-sdk state.advance-plan` was not run.** Task 3 is a blocking
  human-verify checkpoint and 07-11 is outstanding, so the plan counter is
  hand-set to 10 of 11 with wave 9 named as non-autonomous rather than advanced
  past a gate nobody has passed.
- **`--no-verify` was never used.** All 19 hooks ran on the commit, including
  the new shellcheck gate.
- **The observability wrapper was not committed**, by design: `--freeze`
  refuses a dirty tree, so it cannot be a repo file, and it is not production
  code.
- **The capture daemon was not started, signalled or touched**, and `torch` is
  still absent from `mvp/.venv`.

## Self-Check

**PASSED.** Verified on disk and in git rather than asserted:

- `mvp/data/lake_registry/predictors/e3b4b235d0fe…json` exists, and its
  recomputed `manifest_id`, its `manifest_id` field and its filename stem all
  agree; `read_frozen_predictor` re-derives `predictor_id` `ff91c9aa2a59…`.
- `mvp/data/lake_registry/manifests/BTCUSDT.features_norm/c7749334fb73…json`
  exists.
- commit `b75edc6` resolves in `git log` and `git show --stat` lists exactly the
  five expected paths.
- `git status --porcelain` is empty after both mutation restores; both tool
  files hash to their H1 values.
- `check_manifest_id_integrity` and `check_manifest_append_only` both exit 0
  with `predictors/=1` in scope.
- the full suite is 1399 passed, run by me.
- all 16 look counters re-queried after the last command: total 5, `val` 0 on
  both manifests.

## AWAITING: Task 3, the blocking human-verify checkpoint

**`val` has not been looked at and every decision here is still free to
change.** Plan 07-11 must not run until the developer approves this winner. The
specific things worth a human's judgement, in order:

1. The winner is **one feature**. Is a single-feature Stage-1 model what this
   phase should carry into the val look, or is the right response a feature
   revision first?
2. The choice among the eligible was made on the **fourth decimal** of a metric
   that cannot distinguish six of the candidates at all — the gate's amplitude
   leg picked the winner, and Stage 2 re-fits amplitude (disclosure 5).
3. The denominator is **36**, and it only grows.
4. `oof_block_1`'s rank IC (0.375) exceeds the pre-phase band's top. Per the
   plan's own rule that is a warning sign rather than good news.
5. Approving means 07-11 spends the one planned `val` look, leaving two —
   reserved for a re-run after a genuine diagnosed bug, **not** for trying the
   runner-up.
