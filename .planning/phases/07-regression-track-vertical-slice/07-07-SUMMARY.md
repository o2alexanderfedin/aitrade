---
phase: 07-regression-track-vertical-slice
plan: 07
subsystem: models
tags: [metrics, gates, perfect-foresight-ceiling, rank-ic, tie-fraction, spec-contract]

# Dependency graph
requires:
  - phase: 07-05
    provides: "models/conversion.py -- neutral_fill_null_predictions and pred_return_to_price, the ONE return-to-price site the ceiling shares with the model's own prediction (D-07-33)"
  - phase: 07-04
    provides: "models/frozen.py -- FrozenLinearPredictor.train_target_mean, the field forecast_metrics' required train_mean keyword is fed from"
  - phase: 07-03
    provides: "tests/fixtures/model_span.py -- the learnable rig whose val segment carries 9 null labels and a real 98-trade ceiling"
  - phase: 06-event-driven-simulator
    provides: "sim/kernel.py's run_sim_checked, sim/arrays.py's sim_arrays, sim/outputs.py's SimResult and the [:fill_count] hazard, sim/ticks.py's four scale constants"
provides:
  - "models/metrics.py -- forecast_metrics(pred, y, *, train_mean) and FORECAST_METRIC_KEYS: n_scorable, both R-squared references, both rank ICs, the per-segment tie fraction, and the constant-at-train-mean control column"
  - "models/gates.py -- perfect_foresight_ceiling, guard_against_ceiling, CeilingExceededError, gate_forecast, gate_monetization, closed_pnl_ticks, ticks_to_usd_per_btc, ticks_to_usd_at_traded_lot, and the two cited reference ceilings"
  - "mvp/spec.md's 'Stage 1 — Regression track' section plus its Contents anchor -- the feature/target contract, the prediction-table contract, the two gates, the determinism guarantee, the normalisation-scope argument and the MLflow stage vocabulary"
affects: [07-08, 07-09, 07-10, 07-11, "Phase 8's model-selection gates", "Phase 9's Net P&L reporting"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "A required keyword with no default as the only structural defence against a plausible substitution: the only mean available inside a scoring function is the evaluation set's own, so a default would be filled in silently and logged under a key that says 'train'"
    - "The gate's fixtures are the RESEARCH TABLE's own measured rows as literal dicts, not a reconstruction: the gate is a pure function of a metrics dict, so feeding it the numbers measured on real data is a stronger claim than feeding it something shaped like them"
    - "A behavioural proof of a key link: monkeypatch the one conversion site to raise and assert the caller fails. An identity check on the imported name would pass a module that imported the function and then inlined the same arithmetic -- and an inline copy produces the SAME numbers, so no value assertion could see it"
    - "A cross-check between two columns that are equal BY CONSTRUCTION (side vs position_after) as the thing that catches an unsliced read of uninitialised memory before the arithmetic is even wrong -- measured: the slice-dropping mutation failed on the disagreement check, not on a wrong total"
    - "An exact INTEGER identity where the floats cannot be exact: the 1000x between the two USD conventions is asserted as QTY_SCALE // LOT_STEP_SCALED, and the float ratio only to 1e-12, because neither $0.10 nor 0.001 is binary-exact"
    - "Anti-vacuity before anything else in a ceiling test: a ceiling of zero trades makes the guard raise on every reported P&L, so trades > 0 and ticks > 0 are the first two assertions"

key-files:
  created:
    - mvp/models/metrics.py
    - mvp/models/gates.py
    - mvp/tests/models/test_metrics.py
    - mvp/tests/models/test_gates.py
  modified:
    - mvp/spec.md

key-decisions:
  - "The two degenerate predictors are rejected by DIFFERENT checks, and that had to be measured rather than assumed. The constant-at-train-mean predictor is caught by the non-finite-IC check (a constant prediction has no ranks), and the 1e-6-shrunk model is caught by r2_vs_mean -- it passes both halves of D-07-18(a) as originally written, because its r2_vs_zero is +4.25e-08 and the research table's '+0.000000' is a six-decimal rounding of a strictly positive number. If it were exactly 0.0 the original gate would already have rejected it and D-07-32 would be correcting nothing."
  - "The gate tests use 07-RESEARCH.md Q4's measured rows as literal metrics dicts rather than synthetic arrays. gate_forecast is a pure function of a dict; asserting that it rejects the predictors MEASURED to have no skill is a stronger statement than asserting it rejects a reconstruction, and test_metrics.py is where forecast_metrics is shown to PRODUCE those numbers from arrays. One end-to-end test wires the two modules so a renamed key cannot slip between them."
  - "Four refusals added to forecast_metrics that the research note's code sample does not have (deviation Rule 2): an empty scorable set, an all-zero target, a constant target, and a non-finite train_mean. Each would otherwise return inf or nan into a table a human reads as a result."
  - "closed_pnl_ticks computes the walk BOTH ways -- signed by side[i-1] and by position_after[i-1] -- and refuses when they disagree. They are equal by construction under the flip-only rule, which is exactly why the check is cheap; and it is what fired first when the [:fill_count] slice was mutated away, before any total was wrong."
  - "spec.md's new subsection is '### Determinism guarantee (Stage 1)', not the plan's bare '### Determinism guarantee': the Simulator section already owns a heading by that exact text, and two identical ### headings in one document produce colliding anchors. Verified: all 17 Contents links resolve and no heading text is duplicated."
  - "The Contents anchor is '#stage-1--regression-track', with a DOUBLE hyphen. That is the correct GitHub slug for 'Stage 1 — Regression track' -- the em dash is dropped and both surrounding spaces become hyphens -- and it was verified with the same slugger that resolves all 16 pre-existing Contents links."
  - "FCST-01 left Pending, following 07-01/03/04/05/06. Gates that a fixture's numbers pass are FCST-01's mechanism, not its satisfaction: no real segment has been scored, no model selected, nothing logged to MLflow."

patterns-established:
  - "Pattern: state the observable BEFORE the mutation run, including the UNCERTAIN part. The slice-dropping mutation was predicted to fail one test for certain and three more 'likely, because np.empty may hand back zeros' -- all four failed, and naming the uncertainty is what made the result informative rather than lucky."
  - "Anti-pattern recorded: a hand-written 'model' of the form `0.15 * y + noise`. It is not a fit and scores a NEGATIVE r2_vs_mean (measured), so an end-to-end gate test built on one fails for a reason that has nothing to do with the gate. Use lstsq."

# Metrics
duration: "~2.6 h"
completed: 2026-09-26
---

# Phase 7 Plan 07: The Gates, The Ceiling, and The Contract Summary

"Beats the zero baseline" is now a statement a zero-skill model cannot satisfy, and the perfect-foresight ceiling is a function with a test instead of a number in a transcript. Two models that were MEASURED to have no skill each win one half of the gate as originally written -- a constant at the train mean scores a positive R-squared while using no feature at all, and the same fit shrunk by 1e-6 keeps its entire rank IC -- so the gate now requires both halves, a second R-squared reference, and reads a non-finite IC as its own distinct failure.

## Performance

| | |
|---|---|
| Suite | 1,293 -> 1,315 tests (22 added: 9 metrics, 13 gates) |
| `pytest tests/models` | 158 -> 180 tests, 14.4 s (runs on every commit, hook 19) |
| Full suite | 181 s before, 186 s after -- the 22 new tests cost about 5 s, and the fixture ceiling run is 1,799 rows of real kernel |
| Looks spent | ZERO. `budget.look_count` is 0 on `val` and all five `oof_block_*` of BOTH manifests, read before the first commit and again after the last |

## Commits

| Hash | Subject |
|---|---|
| `791b533` | feat(07-07): the scoring statistics, with the loophole made visible |
| `590aaf5` | feat(07-07): the two gates and the perfect-foresight ceiling, as committed code |
| `bc45ab6` | docs(07-07): the regression track's contract in spec.md |

## The Two Degenerate Predictors, Measured Here

The synthetic target is CALIBRATED to the real one's shape rather than groped for. Two population identities fix the constants: with a zero point mass `f`, the pooled `n·ȳ²/Σy²` is `(1-f)·μ²/(μ²+σ²)`, set to the real day's 0.0016466 at `f = 0.18`; and a one-feature OLS scores `1/(1+s²)`, set to 0.0220 by `s = 6.67σ`. 40,000 rows, seed 20260926.

| predictor | `r2_vs_zero` | `r2_vs_mean` | IC all | IC non-tied |
|---|---|---|---|---|
| real 2026-09-18 OLS | +0.022238 | +0.020625 | +0.207494 | +0.204823 |
| **this repo's fitted model** | **+0.021255** | **+0.019653** | +0.132560 | +0.150129 |
| real constant at the mean | +0.001647 | +0.000000 | NaN | NaN |
| **this repo's constant at the eval mean** | **+0.001634** | **+0.000000** (exactly) | NaN | NaN |
| real OLS x 1e-6 | +0.000000 | -0.001649 | +0.207494 | +0.204823 |
| **this repo's model x 1e-6** | **+4.250915431e-08** | **-0.001636644** | +0.132560 | +0.150129 |

`SS_mean/SS_zero` is 0.9983534 on the real rows and 0.9983660 here; the relative gap between the two R-squareds is 7.8% there and 8.15% here.

**Which loophole each number closes.**

- The constant at the mean uses NO FEATURE AT ALL and still scores `r2_vs_zero = +0.001634 > 0`. It wins the R-squared half of D-07-18(a) as written. `r2_vs_mean` is **exactly 0.0** -- not approximately, because the constant IS the mean so `SSE == SS_mean` bit-for-bit -- which is the reference that sees it. Its IC is NaN, so the corrected gate rejects it at the FIRST check with the NaN message.
- The 1e-6-shrunk model keeps its rank IC **bit-identically** (a positive rescaling leaves every rank untouched, so the assertion is `==`, not `approx`) and its `r2_vs_zero` collapses to +4.25e-08 -- **still strictly positive**. It therefore passes BOTH halves of D-07-18(a) as originally written, which is asserted in the test rather than described. Only `r2_vs_mean = -0.001637` rejects it, and with a nonzero target mean that sign is deterministic: `SS_mean = Σy² - n·ȳ²`, so shrinking to nothing leaves `r2_vs_mean ≈ -n·ȳ²/SS_mean`.

The third column, `r2_vs_zero_of_constant_train_mean`, is computed from the passed-in TRAIN mean and nothing else. The research note's own code sample computes it as `((t - p.mean())**2)` -- the exact substitution the required keyword exists to prevent -- and that line was not copied.

## The NaN-IC Failure Message, Quoted

```
rank IC is not finite -- scipy.stats.spearmanr returns NaN for a CONSTANT input. The
prediction has no ordering at all; this is NOT a small positive IC and not a weak one.
A constant predictor at the train mean is the measured shape of this failure, and it
scores r2_vs_zero=+0.001647 while using no feature at all.
```

The contrast case is asserted in the same test, so a refactor cannot collapse the two: a FINITE negative IC produces `rank_ic_non_tied=-0.01 <= 0 -- the model cannot order the rows whose target actually moved (tie_fraction=0.0977 on this segment).` and the test asserts that this message does **not** contain "no ordering at all".

**Measured on scipy 1.18.1, and it changed the implementation.** A constant input emits `ConstantInputWarning` and returns NaN -- so the warning filter catches it. But a subset of **0 or 1 rows returns NaN with NO warning at all**, so a filter on `ConstantInputWarning` alone would never see that case. `_rank_ic` therefore checks the row count FIRST and the warning second.

## The Re-Measured Ceiling, and the Guard at Its Boundary

`perfect_foresight_ceiling` walks the ADMITTED frame, converts the label through `models.conversion.neutral_fill_null_predictions` -- the same site the model's own prediction uses -- and runs `run_sim_checked(..., x_bps=0)` at every other default.

**On the fixture's `val` segment (1,799 admitted rows, 9 of them with a null label):**

```
trades=98  flips=97  rows_in_market=1799  closed_pnl_ticks=311
  per 1 BTC convention       $31.1000
  realised at the 0.001 lot  $0.031100
  closed + unrealised        $0.031300
```

This reproduces, to the trade and to the tick, the ad hoc walk `tests/models/test_fixture_rig.py` performs inline -- asserted in `test_the_ceiling_function_reproduces_the_ad_hoc_walk_the_transcript_used`, which is what makes "landed as code" a check rather than a claim.

**The reference values for the real windows are documented in `models/gates.py` and asserted nowhere**, because reading the real lake costs an irreversible look on every commit:

| segment | admitted rows | trades | closed ticks | realised at the traded lot |
|---|---|---|---|---|
| 2026-09-13 v2 (Phase 6's) | 6,864,853 | 2,192 | 294,554 | $29.4554 |
| **the approved `val` window (days 17-18)** | **16,294,059** | **9,946** | **1,120,460** | **$112.0460** |

Day 13's $29.46 is not this phase's ceiling (correction C4), and both are cited from STATE.md rather than from the Phase 6 evidence JSON, which reports pre-fix 2,212 / 293,844 and says so in its own `SUPERSEDED_BY` key (D-07-24).

**The guard, at the boundary and one tick below it.** `guard_against_ceiling(1_120_459, 1_120_460, "val")` returns. `guard_against_ceiling(1_120_460, 1_120_460, "val")` raises `CeilingExceededError`:

```
val: reported 1120460 closed_pnl_ticks at or above the measured perfect-foresight
ceiling 1120460 -- INVESTIGATE. The two likeliest causes, in order: a prediction table
misaligned with the decision rows, or a label leaking into a feature. (The ceiling is a
strong sanity bound, not a theorem: perfect foresight through the flip-only rule at
x_bps=0 is one particular policy, not the path's P&L maximum, so this is a reason to
investigate rather than a proof of impossibility.)
```

## The Tie Fraction, Per Segment

Never pooled, and never hardcoded -- nothing in `models/metrics.py` carries a tie-fraction constant; every caller gets the fraction measured on the rows it actually scored.

| segment | rows | exact zeros | fraction |
|---|---|---|---|
| 2026-09-12 | 4,193,137 | 2,905,138 | 69.28% |
| 2026-09-13 | 6,864,853 | 3,012,962 | **43.89%** -- D-07-18's number, and ONE DAY |
| 2026-09-14 | 11,323,694 | 1,384,076 | 12.22% |
| 2026-09-15 | 12,203,294 | 1,505,638 | 12.34% |
| 2026-09-16 | 9,872,620 | 1,128,446 | 11.43% |
| 2026-09-17 | 8,482,081 | 1,485,738 | 17.52% |
| 2026-09-18 | 7,986,824 | 767,021 | 9.60% |
| pool | 60,926,503 | 12,189,019 | 20.01% |
| the approved `val` window (scorable rows) | — | — | 13.83% |
| this repo's synthetic fixture | 40,000 | 7,293 | 18.23% |
| the model-span test fixture's `val` | 1,799 | — | (9 NULL labels, a different thing) |

And the ties do not reliably flatter anything (correction C2): including them INFLATES the IC by 1.3% on 2026-09-18 and DEFLATES it by 3.2% on 2026-09-13. On this repo's synthetic set they deflate it by 13.3%. The two statistics answer different questions; `models/metrics.py` returns both and presents neither as a correction of the other.

## Mutation Checks

Four, each with the observable stated BEFORE the run and three hashes. All four predictions matched.

### M1 -- the control column reads `t.mean()` instead of the passed `train_mean`

`models/metrics.py` `2600a5b9568a1891ea294bc6c97742ee` -> `72cc8678009a01ea7a98cc5027ad98ee` -> `2600a5b9568a1891ea294bc6c97742ee`

**Predicted:** exactly one failure -- `test_forecast_metrics_requires_the_train_mean_and_does_not_fall_back_to_the_evaluation_mean`, at its "two different train means produce two different control columns" assertion. The `TypeError` assertions still hold because the signature is untouched.
**Observed:** 1 failed, 8 passed. `assert 0.0016340125657001714 != 0.0016340125657001714` -- the two calls returned the same number, which is precisely what the substitution looks like.

### M2 -- the two R-squared denominators swapped

`2600a5b9...` -> `aef4bfc81cf6167113fafbd340e567ad` -> `2600a5b9...`

**Predicted:** exactly three failures -- the constant-at-the-mean test (`r2_vs_mean` becomes +0.001634 instead of exactly 0.0), the shrunk-model test (its `r2_vs_zero` goes negative), and the two-references test (the inequality reverses). Tests 5-7 and the refusals are untouched.
**Observed:** 3 failed, 6 passed -- exactly those three.

### M3 -- `>=` becomes `>` in `guard_against_ceiling` (the plan's required check)

`models/gates.py` `7f9e4652a2fc6b86aecd8c3d5cefdee3` -> `301ae5456bddd274368b39506663d8a6` -> `7f9e4652a2fc6b86aecd8c3d5cefdee3`

**Predicted:** exactly one failure -- `test_a_fabricated_pnl_at_the_ceiling_raises_and_one_tick_below_does_not`, at its FIRST `pytest.raises` (the P&L exactly AT the ceiling). `ceiling-1` must still not raise and `ceiling+1` must still raise, so nothing else moves.
**Observed:** 1 failed, 11 passed. `Failed: DID NOT RAISE CeilingExceededError` at the at-the-ceiling call.

### M4 -- the `[:fill_count]` slices dropped from `closed_pnl_ticks`

`7f9e4652...` -> `50a2478b2f1c2460cee09ea26fe0ee45` -> `7f9e4652...`

**Predicted:** the uninitialised-tail test fails FOR CERTAIN (its padded log has a deliberately poked tail). The ad-hoc-agreement test and both monetization cases read logs whose tails are genuinely uninitialised memory, so they are LIKELY but not GUARANTEED to fail -- `np.empty` may hand back zeros. The uncertainty was stated rather than a count.
**Observed:** 4 failed, 9 passed -- the certain one and all three likely ones. And the failure came from an unexpected direction worth recording: it was the `side`-vs-`position_after` cross-check that fired, not a wrong total. The uninitialised tail disagreed between the two columns before the arithmetic had a chance to be wrong.

## The spec.md Section

One new top-level section at line 367 and one Contents line at line 47. `git diff --numstat` is `150 0` -- **zero deletions**, two hunks, nothing else in the file touched.

- `## Stage 1 — Regression track`, anchored from Contents as `[Stage 1 — Regression track](#stage-1--regression-track)`. The **double hyphen is correct**, not a typo: GitHub's slugger drops the em dash and turns both surrounding spaces into hyphens. Verified with a slugger that also resolves all 16 pre-existing Contents links -- 17 of 17 resolve, 0 broken.
- Six `###` subsections, mirroring the Simulator section's shape: `Feature and target contract`, `Prediction table contract`, `Beating the zero baseline`, `Determinism guarantee (Stage 1)`, `Normalisation scope`, `MLflow stage vocabulary`.
- `check_spec_diff` exits 0 (hook 11 green on the commit). `tests/spec` and `tests/tracking` pass unmodified, 139 tests -- the tag-schema section that `test_mlflow_utils.py` parses by `text.index("## MLflow tag schema")` was not touched, and the new section deliberately never writes that string with its `##` prefix, which would have made the parser start at the wrong place.

## Deviations from Plan

### Auto-fixed and auto-added

**1. [Rule 2 - missing correctness] Four refusals `forecast_metrics` needed and the research sample lacks**
- **Found during:** Task 1.
- **Issue:** the research note's code sample divides by `Σy²` and by `SS_mean` unguarded and accepts any `train_mean`. An all-zero target (every mid unchanged), a constant target, an empty scorable set and a NaN `train_mean` are all reachable on a real segment, and each would put `inf` or `nan` into a table a human reads as a result.
- **Fix:** each raises `ValueError` with a message saying what the number would have meant. Two extra tests cover them.
- **Files:** `mvp/models/metrics.py`, `mvp/tests/models/test_metrics.py`. **Commit:** `791b533`.

**2. [Rule 2 - missing correctness] A dtype/alignment boundary on `forecast_metrics`**
- **Issue:** `pred` and `y` are the same decision rows in the same order. Nothing stopped a caller scoring a prediction table against the wrong segment -- numpy would broadcast or raise something unhelpful.
- **Fix:** 1-D, float64 and equal-shape all asserted, with the float64 requirement carrying `models/conversion.py`'s reason (the prediction table is float64 by D-07-16). **Commit:** `791b533`.

**3. [Rule 2 - missing correctness] The one-lot assumption behind the "realised" label, asserted**
- **Issue:** "realised at the traded lot" presumes every fill is exactly one `LOT_STEP_SCALED`. Research confirmed it on both real windows, but a two-lot fill would silently make the labelled figure wrong.
- **Fix:** `perfect_foresight_ceiling` refuses a trade log whose fills are not all one lot step, naming the offending fill. **Commit:** `590aaf5`.

**4. [Rule 2 - missing correctness] `closed_pnl_ticks` refuses a log it cannot sign**
- **Issue:** the walk signs each closed leg by the previous row's direction, and `side`/`position_after` are interchangeable only because the flip-only rule makes them equal. A future partial-fill rule would break that silently.
- **Fix:** both walks computed, disagreement refused. This turned out to be what catches an unsliced read (M4). **Commit:** `590aaf5`.

### Plan text corrected

**5. `### Determinism guarantee` renamed to `### Determinism guarantee (Stage 1)`**
- The Simulator section already owns a heading with that exact text. Two identical `###` headings collide as anchors, and a living spec gets linked. The parallel with the Simulator section -- which the plan asked for -- is still obvious from the name.

**6. Three extra tests the plan did not list, and one it listed that needed a different construction**
- Added: `test_gate_forecast_passes_the_honest_measured_fit` (anti-vacuity -- a gate that rejected everything would satisfy every rejection test in the file), `test_the_gate_reads_a_metrics_dict_computed_from_arrays_not_only_a_literal` (the two modules' key names, which neither file would otherwise pin), and `test_the_ceiling_routes_its_prediction_through_the_one_conversion_site` (the plan's `key_links` entry, proved by monkeypatching the site to raise -- an inline copy of `mid * (1 + ret)` produces the same numbers, so no value assertion anywhere could see it).
- The end-to-end test's first draft used `0.15 * y + noise` as the "honest model". Measured, that is not a fit and scores a NEGATIVE `r2_vs_mean`, so the gate correctly failed it. Replaced with a real `lstsq`.

### Verification commands

The plan's `<verify>` blocks call `uv run --locked --directory mvp pytest`. Run as `./.venv/bin/pytest` from `mvp/` instead, per the executor's standing constraint against `uv run` for anything long-lived. Same interpreter, same lockfile-installed environment; hooks 18/19 invoke their own command string unchanged.

## Look Budget

`harness.budget.look_count` read directly against `/Volumes/ProjectsSSD/aihedgefund/mlflow` (a read-only `search_runs`), before the first commit and again after the last:

| manifest | `val` | `oof_block_0..4` |
|---|---|---|
| `97964cb2...` (3-day Phase 5 reference) | 0 | 0, 0, 0, 0, 0 |
| `807125015b...` (the approved 7-day geometry) | 0 | 0, 0, 0, 0, 0 |

Twelve counters, unchanged. **`harness.accessor.materialize` was never called against the real lake in this plan.** The four ceiling tests call it against the fixture lake in `tmp_path`, whose manifest carries its own allowance of 50.

## Research Disclosure, Carried Forward Verbatim

Every research measurement that read the candidate `val` days (2026-09-17 / 2026-09-18) read Parquet directly with polars. **None is a `harness.accessor.materialize` call**, so no look was spent, no `harness-looks` MLflow run exists, and `budget.look_count` is still 0 for every segment of every manifest. **None can steer model selection**, which D-07-04 confines to the OOF blocks. The measurements are: per-day row/null/exact-zero counts; admission counts for both candidate windows; the perfect-foresight ceiling and the `pred = mid` zero-trade oracle for both candidate windows; an in-sample 3-feature OLS on day 18 and days 17+18; in-sample rank IC on day 18 all-rows and non-tied plus the constant-at-mean and shrunk-by-1e-6 rows; a Gram-vs-dense ridge equivalence check; and the prediction-table parquet byte measurement. The model-fit items produced numbers INSIDE the range the train-internal splits (days 12-17) already establish, so nothing in the plan depends on them -- they are reported only so nobody mistakes a passing honest look for a surprise.

## Threat Register Outcomes

| Threat ID | Disposition | How |
|---|---|---|
| T-07-25 (tampering with the result, via the forecast gate) | mitigated | both R-squared references required positive; the constant-at-train-mean control scored in every table from a required keyword; a non-finite IC failed with its own message and the message asserted; both degenerate predictors reconstructed from the measured rows and each rejected; M1 and M2 prove two of the three checks bite |
| T-07-26 (an implausibly high P&L) | mitigated | `guard_against_ceiling` raises at or above the per-segment ceiling, with its bite proven AT the boundary and one tick below (M3); the ceiling itself is measured by committed code that reproduces the ad hoc walk exactly |
| T-07-27 (a remembered number) | mitigated | the ceiling is a function with four tests; its reference values are cited from STATE.md, not from the superseded evidence JSON, and asserted by no test because that would cost a look |
| T-07-28 (spec.md's machine-read sections) | mitigated | additive-only diff (150 insertions, 0 deletions, 2 hunks); `check_spec_diff` green; `tests/tracking/test_mlflow_utils.py` passes unmodified; the new section never writes `## MLflow tag schema` with its heading prefix |
| T-07-22 (normalisation leakage) | accepted, now DOCUMENTED | `spec.md`'s "Normalisation scope" subsection carries the magnitude argument: zero leak for any model with an intercept, two scalars per feature from ~44M rows for Ridge/ElasticNet. This was 07-06's outstanding item |

## What Was NOT Done

- **No real segment was scored.** `forecast_metrics` has never seen `val`. Every number in this plan comes from the fixture, from calibrated synthetic data, or from 07-RESEARCH.md's measurements -- and the real ceiling of 9,946 / 1,120,460 / $112.0460 is documented in `models/gates.py` and asserted by nothing.
- **The ceiling was NOT re-measured against the real lake by this plan.** Correction C4's measurement stands as research. Re-running it here would have called `materialize` and spent a look.
- **`guard_against_ceiling` is not wired into any caller yet.** It is a function with a test; the plan that runs the simulator on `val` is the one that must call it, and nothing yet forces that call.
- **`gate_monetization` does not compute Sharpe, fees, or latency.** It answers exactly D-07-18(b) -- more than 0 trades and more than $0 -- at the MVP's zero-fee, zero-latency defaults.
- **No MLflow run was created.** The `stage` vocabulary (`val_look`, `negative_result`, `stage1_regression`) is written into `spec.md` as a contract; `stage1_regression` is not yet emitted by any code.
- **FCST-01 and FCST-04 remain Pending**, following 07-01/03/04/05/06.
- **The two USD conventions are not asserted to be exactly 1000x apart as floats**, and cannot be: neither $0.10 nor 0.001 is binary-exact. What is asserted exactly is the integer `QTY_SCALE // LOT_STEP_SCALED == 1000`; the floats agree with it to `rel=1e-12`.
- **`closed_plus_unrealised_usd_at_traded_lot` is reported, not gated.** It includes the open leg's mark at the last row and is a different number from `closed_pnl_ticks` (measured on the real window: 1,120,460 closed against 1,120,530 closed plus unrealised).

## Self-Check: PASSED

- `mvp/models/metrics.py` (275 lines, min 90), `mvp/models/gates.py` (443, min 120), `mvp/tests/models/test_metrics.py`, `mvp/tests/models/test_gates.py`, `mvp/spec.md`: all FOUND on disk.
- Commits `791b533`, `590aaf5`, `bc45ab6`: all FOUND in `git log --all`.
- `mvp/tests/models/__init__.py` does NOT exist (the standing prohibition).
- `load_features` appears in none of the four new files (hook 17's rule; hook 17 green on all three commits).
- `budget.look_count` 0 on all twelve counters, before the first commit and after the last.
- Full suite 1,315 passed. `pytest tests/models` 180 passed.
