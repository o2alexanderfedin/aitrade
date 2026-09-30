# Deferred items — Phase 7

Out-of-scope discoveries logged during execution, not fixed. Each names the
plan that found it and why it was left alone.

## D1. `mvp/tests/lockbox/conftest.py` has the same pop-only tracking-root defect

**Found during:** 07-01 Task 3.
**Status:** NOT FIXED. Deliberately out of scope.

Its autouse `isolated_canonical_tracking_root` is the fixture
`tests/harness/conftest.py` was copied from, and it has the identical body:
`os.environ.pop(MLFLOW_TRACKING_ROOT_ENV, None)`. With the variable absent,
`lake_paths.mlflow_tracking_root(None)` resolves to the REAL store at
`/Volumes/ProjectsSSD/aihedgefund/mlflow`, so a lockbox test that resolves a
canonical root without passing its own gets the real one.

Two differences from the harness case, which is why it was left:

1. **Its docstring is honest.** It says "cleared before every test, and
   restored afterwards" — which is exactly what the code does. The harness
   copy claimed to point the store at `tmp_path` and did not; that gap between
   claim and behaviour is what made it a defect rather than a design.
2. **No look flows through it.** `harness.budget.record_look` is the only
   thing that spends a validation look, and no lockbox test calls it. The
   lockbox's own counter is the consumed-token record, which every lockbox
   test already seeds into its own `tmp_path` store explicitly.

07-01's scope was the two conftests the plan named. Fixing a third shared
fixture in the same commit would have put an unrelated blast radius behind
one full-suite green. The repair is mechanical — the same four lines — and
should be taken by whichever later plan first needs lockbox tests to resolve
a canonical root implicitly.

---

## `STATE.md`'s Performance Metrics table has no row for 07-02

Found while 07-03 was recording its own row. The table runs
`... | Phase 06 P07 | ... | Phase 07 P01 | ... | Phase 07 P03 |` -- **07-02 is
absent**, because that plan stopped at its human-verify checkpoint and the
`state.record-metric` call lives after the checkpoint in the executor flow.

NOT fixed here, deliberately. `07-02-SUMMARY.md` carries `duration: ~1h20m`, so
the number is recoverable, but it is a number 07-03 did not measure and
transcribing someone else's into a metrics table is how a table stops meaning
what it says. Whichever plan next runs a `state.*` verb can add it from the
SUMMARY, with a note that it came from there.

---

## Correction C5's 91.2 MiB is ONE DAY, not the `val` segment

**Found during:** 07-05, measuring the real writer's bytes per row.

C5 corrected D-07-15's "~11.3M rows ≈ 270 MB" to "91.2 MiB zstd at 7.86M
rows". The bytes per row is right and reproduced exactly -- 12.17 B/row there,
12.1 to 12.7 B/row measured here through `write_prediction_table` at 100k and
2M rows -- but the row count is a single day. The approved manifest
`807125015b...` admits **16,294,059** rows to `val` (two days, Option A), so
the one table plan 07-11 stores will be **about 190 MiB**, not 91.2 MiB.

NOT a defect and nothing to fix here: D-07-15's decision (store only the
winner) stands, and 07-05 stores nothing. It is an input to 07-11, because
D-07-25 makes a committed prediction-table manifest `stat()`-checked on every
commit from then on, and the number that consequence attaches to is 190 MiB.

---

## The `predictions` tier has no `verify_manifest --full` coverage of its own

**Found during:** 07-05 Task 1.

`tools/check_no_manifest_rewrite` globs `manifests/**`, so a committed
predictions manifest is covered the moment one exists (D-07-38, confirmed --
no guardrail edit was needed). What does NOT exist yet is a predictions
partition inside `tests/fixtures/lake`, so the CI-fixture leg of that hook
exercises the scan over curated/features bytes only. NOT added here: the
fixture lake is committed bytes, and adding a partition to it for coverage of
a tier whose first real table lands in 07-11 is a commit that freezes bytes
nothing reads.

---

## Seven of the nine ElasticNet configs return a coefficient vector of exactly zero

**Found during:** 07-06, timing all 17 configs on the fixture rig.

sklearn's ElasticNet objective carries `1 / (2 n)` on the squared-error term,
so `alpha * l1_ratio` is a soft threshold in per-sample GRADIENT units. The
target is a 10-second return of order 1e-4 and the normalised features are
unit-variance, so the OLS coefficients are order 1e-7 — every grid alpha at or
above 1e-4 thresholds all three to exactly 0.0. Those seven configs ARE the
zero-skill control: their fixture `R^2` of -0.00592 is bit-identical to the
constant-at-train-mean control measured beside them.

NOT FIXED and NOT to be fixed here. The grid is D-07-08's, hand-written and
counted; 07-07's eligibility gates own which configs are viable and D-07-20
writes each failure to the negative-result log. A trainer that refused to be
useless would hide a real property of the grid.

**What 07-07 needs from this:** the grid's LIVE membership on real data is
likely ~10 of 17 (the number may differ at real scale — the real target's
standard deviation and the real Gram are not the fixture's), while the
selection-bias denominator stays 17. If the intent was nine INFORMATIVE
ElasticNet configs, the alpha decades would have to move down by about four —
a decision for the discuss step of a later plan, not an executor's.

---

## An untracked `.gitignore` sits at the repo root

**Found during:** 07-06, first `git status` before committing.

`/Volumes/ProjectsSSD/aihedgefund/repo/.gitignore` is untracked and ignores
`timesfm/`, `hf-cache/`, `*.safetensors`, `*.ckpt`, `*.pt`, `*.pth` — a model
-weights experiment unrelated to the MVP. `mvp/.gitignore` is the tracked one.

NOT staged, NOT modified, NOT deleted. It is outside `mvp/` and outside this
phase, and whether the repo root should carry it is the repo owner's call.

---

## `perfect_foresight_ceiling` is not a bound, and `guard_against_ceiling` will abort 07-11 AFTER the val look is spent

**Found during:** the zero-look OOF viability run (`mvp/scripts/oof_viability_check.py`,
evidence `07-oof-viability-results.json`).
**Status:** FIXED 2026-09-30 (`21d5fcc`), by explicit instruction. Shape (a) was
taken and then CORRECTED by measurement: the guard's reference is now
`models.gates.mid_total_variation`, not decision-row perfect foresight. The
tighter candidate turned out not to be a bound at all -- on this repo's fixture,
whose path steps one tick at a time while the kernel needs 1.5, decision-row
perfect foresight earns EXACTLY ZERO while the 10-second perfect predictor earns
311 ticks and the fitted model earns 50, so a guard referenced to it would abort
every ordinary run on any segment with sub-tick adjacent moves. Total variation
is a theorem for a one-lot flip-only policy and holds on both. The label-horizon
ceiling keeps its unchanged `ceiling_*` metric keys as a diagnostic, and
decision-row perfect foresight is logged as the leak signal a real prediction
actually approaches. `gate_forecast`, `gate_monetization`, `GRID`, `select_winner`
and the all-blocks eligibility rule are byte-unchanged (`git diff a3a4d22..HEAD --
mvp/models/sweep.py mvp/models/regression.py mvp/harness/` is empty).

**The cost, stated because it is a real loss:** the bound is now strictly
unattainable, so no PREDICTION can make the guard fire. It checks for impossible
results, not leaks. The leak signal is the reported fraction of decision-row
perfect foresight instead.

**What was measured.** The committed frozen winner, simulated on the five
cached OOF blocks at `x_bps=0`, earns **1.44x / 2.21x / 3.21x / 3.01x** the
`ret_10s_mid` perfect-foresight ceiling on blocks 1, 2, 3 and 4 (block 0 is
0.77x). `guard_against_ceiling` therefore raises `CeilingExceededError` on
four of five blocks.

**Why that is the ceiling and not a leak.** `perfect_foresight_ceiling` feeds
the REALISED FUTURE MID at one label's horizon into the flip-only rule. That
input is a price, and `sim/kernel.py`'s symmetric floor/ceil quantisation
fires only when the predicted price clears the touch by a FULL tick. At a
one-tick spread the future mid sits a half tick off the tick grid, so a
horizon-h perfect predictor fires only on moves of ~1.5 ticks within h -- and
the P&L it reports falls monotonically with h. Measured on `oof_block_3`, same
rows, same rule, only the label changed:

| perfect foresight at | trades | closed_pnl_ticks |
|---|---|---|
| `ret_1s_mid` | 11,394 | 1,579,033 |
| `ret_10s_mid` (the guard's input) | 6,643 | 702,556 |
| `ret_1min_mid` | 3,027 | 285,144 |
| `ret_10min_mid` | 941 | 86,019 |
| next DECISION ROW (pred = next row's mid) | 14,972 | 2,580,239 |
| mid's own total variation (a true bound) | -- | 2,763,605 |

A threshold that moves 30x with a choice of label is not a physical bound.
Against the two numbers that are bounds for a one-lot flip-only policy, the
model's 2,257,251 ticks is **87.5%** of decision-row perfect foresight and
**81.7%** of total variation -- below both, on every block.

**The measurement that says the simulator is not what is wrong.**
`oof_block_1` is 2026-09-13, and its ceiling comes out at **2,192 trades /
294,554 closed ticks / $29.4554** -- byte-identical to Phase 6's committed
real-day measurement and to `STATE.md`'s reference for that day. The ceiling
code and the whole sim path reproduce a previously-committed number exactly.

**What this means for 07-11, concretely.** In `models/slice.py`'s `mode="val"`,
`guard_against_ceiling` is called at step 8, AFTER `materialize_once("val")`
has spent the look and AFTER `write_prediction_table` has issued its manifest,
and BEFORE run 3 opens. If `val` behaves like these blocks -- the model earns
~0.14 ticks per admitted row, so ~2.3M ticks over val's 16,294,059 rows
against the quoted 1,120,460-tick ceiling -- then **07-11 spends the look,
writes the table, and raises, logging no MLflow run and no metrics.**
`--resume-from-cache` re-reads the cache and hits the same guard, because the
guard is a function of the data. The look would not be lost, but the plan as
written cannot complete.

**Not fixed, and the repair is a decision not an edit.** Three shapes were
visible from the evidence and none is an executor's call: (a) bound against
perfect foresight at the DECISION-ROW resolution, or against the mid's total
variation, instead of at the label's horizon; (b) keep the horizon ceiling as
a reported diagnostic and stop raising on it; (c) keep it as a raise but state
in `models/gates.py` that it bounds only a horizon-matched policy. The module
docstring already concedes the direction -- "perfect foresight fed to the
flip-only rule at x_bps=0 is ONE PARTICULAR POLICY, not the P&L maximum" --
so this is that caveat turning out to be the operative case rather than the
edge case.

---

## `x_bps` has exactly ONE usable value for this signal, and it is 0

**Found during:** the same run.
**Status:** FIXED 2026-09-30 (`7ff5f45`), by explicit instruction. `x_bps` still
means whole basis points and every existing call site is untouched; a second knob
`x_bps_scaled` counts in units of 1e-4 bps, so a one-tick threshold (130 units on
a $77,000 one-tick spread) is now expressible where whole basis points jump 0 ->
77 with nothing between. Measured on a 4,000-row walk, trades go 1531, 1233, 915,
319, 0 across the new ladder instead of 1531 then nothing. `x_bps=0` is
byte-identical by an exact integer identity, proven by re-running
`scripts/oof_viability_check.py` on `oof_block_1` against the committed evidence.
Two silent wrongs became refusals: a fractional `x_bps` (which `int()` ate --
`x_bps=0.5` returned the zero-threshold log) and a negative one (`x_bps=-1` traded
on all 4,000 rows).

`sim/kernel.py` computes `x_ticks = (bid_ticks + ask_ticks) * x_bps // 20_000`
and `run_sim_checked` coerces `x_bps` with `int()`. On this window that makes
one basis point worth **74 to 79 ticks**, measured. The frozen model's
predictions clear the touch by at most a few ticks, so at `x_bps=1` there are
**zero trigger rows and zero trades on every one of the five blocks** -- not a
smaller trade set, an empty one.

So Stage 2's "X swept" has one feasible point at this signal's amplitude. The
knob that DOES vary the trade set continuously is the coefficient amplitude
itself (measured: 49,912 trades at `coef=6.75e-6` rising monotonically to
75,862 at `3.10e-5` on `oof_block_3`). Whether the sweep axis should become
sub-basis-point, or ticks, or the amplitude itself, is a Stage-2 design
question for a discuss step.

---

## The simplification that dominates the P&L is not on the simplification list

**Found during:** the same run.
**Status:** FIXED 2026-09-30 (`916d373`, corrected in the following commit), by
explicit instruction. Registered in `mvp/mvp.md`'s Monetization assumptions with
its measured evidence, tied to removal-queue items 4 and 5, and guarded by
`mvp/tests/spec/test_simplification_registry.py`. Both `spec.md` pitfalls now
carry status notes: the mid-vs-fillable IC exists as `scripts/fill_skill_gap.py`
(zero looks; crossing the spread removes 17% to 33% of the decision-row rank IC,
and the spread leaves 36% to 50% of the correct calls at or below zero); the
adverse-selection reversion histogram does not.

**THIS ENTRY'S OWN CLAIM WAS WRONG and the registration says so.** "Measured, that
is where the entire P&L comes from" is contradicted by
`.planning/debug/07-oof-pnl-leakage-investigation.md`, committed to this branch
hours later: the side the model takes carries a median 2.884 BTC against its 0.001
BTC order and is at or below that size on 0.19% of rows, so the typical fill is
NOT against a vanishing queue, and latency decay is graceful (84% retained at
20-40 ms). What the sign depends on is ZERO FEES -- a 0.519 bp edge per round trip
that one basis point of required overshoot takes to zero on all five blocks. The
fill assumption needed registering; it is not the one carrying the result.

The list names zero fees, taker-only, zero latency, $100 max position. What it
does not name is the **unconditional fill at the touch**: the simulator fills
0.001 BTC at `ask_ticks[i]` or `bid_ticks[i]` on the same row whose book
produced the feature, with no queue and no adverse selection. Measured, that is
where the entire P&L comes from -- the sign of the frozen prediction agrees
with the sign of the NEXT row's mid change **91.5% to 96.0%** of the time
(82.5% to 94.6% tick-weighted), which is top-of-book queue depletion: the best
bid shrinking toward zero is what precedes the bid ticking down. Monetising it
requires being filled at a quote that is about to disappear.

`spec.md` already anticipates this in its pitfalls -- "track mid-vs-fill skill
gap explicitly (report IC on mid and IC on a fillable proxy)" and
"adverse-selection patterns flagged in trade log (e.g., immediate-after-fill
price reversion histogram)". Neither is implemented. This entry exists so the
first reader of a `Net P&L > 0` claim on this model knows which assumption is
carrying it.
