---
phase: 07-regression-track-vertical-slice
plan: 11
subsystem: models
tags: [val-look, look-budget, prediction-table, frozen-predictor, forecast-gate, monetization-gate, ceiling-guard, append-only, vertical-slice]

# Dependency graph
requires:
  - phase: 07-02
    provides: "segment manifest 807125015b25 -- compressed_3seg, train 2026-09-12..16, val 2026-09-17..18, budget_allowance 3 for val"
  - phase: 07-10
    provides: "the FROZEN winner e3b4b235d0fe (predictor_id ff91c9aa2a59), chosen on the five OOF blocks and committed to git BEFORE this plan ran"
  - phase: 07-09
    provides: "dq_preflight, called before the first materialize -- it returned ([], []) and refused nothing"
  - phase: 07-04
    provides: "read_frozen_predictor's four refusals, all of which passed on the committed body"
provides:
  - "mvp/data/lake_registry/manifests/BTCUSDT.predictions/f40701838e55...json -- the ONE committed prediction-table manifest, 16,294,059 rows, 181,851,924 bytes"
  - "stage-1-regression MLflow run 5758c91b0c194852ae46597394a9467c -- the full provenance chain"
  - "look_count(val) = 1, permanently. Two of the allowance of three remain."
  - "mvp/scripts/val_per_day_report.py -- the per-day split, re-runnable at zero look cost"

# Tech tracking
commits:
  - "0003497 -- the prediction-table manifest; the look spent, both gates passed, the bytes frozen"
  - "d02cf18 -- the per-day split, and the guardrail that caught my time-unit conversion"

# Metrics
metrics:
  looks_spent_by_this_plan: 1
  look_count_val_after: 1
  allowance_remaining: 2
  n_admitted: 16294059
  n_scorable: 16285445
  r2_vs_zero: 0.014151305572264405
  rank_ic_non_tied: 0.2463008632016707
  sim_pnl_usd_at_traded_lot: 263.876
  prediction_table_bytes: 181851924
---

# Phase 7 Plan 11: the one honest look, spent once, with both gates passing Summary

The full vertical slice ran end to end against the real lake exactly once:
curated store, harness accessor, the frozen regressor read back out of git, a
stored and manifest-addressed prediction table, the event-driven simulator, and
one MLflow run tying every manifest id together. **`look_count(val)` was 0
before and is 1 after. It can never read 0 again.**

Both gates passed. Every forecast number landed inside the band the
train-internal splits predicted before the look — which matters more for being
*inside* it than for being positive, because a result far above the band would
have been a reason to re-check alignment rather than to celebrate.

Read the research disclosure below before reading the numbers as a surprise: it
enumerates every measurement taken during research that touched these two days,
all of which read Parquet directly and none of which spent a look or could steer
model selection.

## The result

| metric | value |
|---|---|
| `n_admitted` | 16,294,059 |
| `n_scorable` | 16,285,445 |
| `n_pred_missing` | 1 |
| `r2_vs_zero` | **+0.014151** |
| `r2_vs_mean` | +0.013563 |
| `r2_vs_zero_of_constant_train_mean` | **+0.000197** (the zero-skill control) |
| `rank_ic_all` | +0.248518 |
| `rank_ic_non_tied` | **+0.246301** |
| `tie_fraction` | 0.138330 |
| `sim_trades` | 70,546 |
| `sim_flips` | 70,545 |
| `sim_rows_in_market` | 16,294,058 |
| `sim_closed_pnl_ticks` | 2,638,760 |
| `sim_pnl_usd` | **$263.8760** at the traded 0.001 BTC lot ($263,876.00 in the per-1-BTC convention) |
| `forecast_gate_passed` | 1 |
| `monetization_gate_passed` | 1 |

The pre-measured band was R-squared 0.013–0.048, rank IC 0.19–0.29, tie fraction
about 13.83%. All three agree: 0.014151, 0.246301, 0.138330.

**The zero-skill control is what makes the forecast gate mean anything.** A
constant predictor at the *train* mean scores +0.000197 on these rows — 72 times
below the model. That mean comes from the frozen body, which captured it at fit
time, because `mode="val"` never opens a train frame and substituting the
evaluation set's own mean would have replaced an honest out-of-sample control
with an in-sample one wearing the same name.

### The three bounds, and which one the guard takes

| quantity | ticks | the P&L as a fraction |
|---|---|---|
| midprice total variation (the guard's reference, a theorem) | 3,044,660 | **0.8667** |
| decision-row perfect foresight (the leak signal) | 2,913,363 | 0.9057 |
| label-horizon ceiling (a diagnostic) | 1,120,460 | **2.3551** |

### Alpha decay across the diagnostic horizons

| horizon | `r2_vs_zero` | `r2_vs_mean` | `n_scorable` |
|---|---|---|---|
| 1 s | +0.066281 | +0.066116 | 16,291,926 |
| **10 s (the fitted target)** | **+0.014151** | **+0.013563** | 16,285,445 |
| 1 min | +0.002416 | −0.000214 | 16,254,747 |
| 10 min | +0.000386 | −0.019426 | 15,998,499 |

Reported alongside, never fitted. `r2_vs_mean` turns negative at one minute and
further negative at ten, so whatever this model knows is gone well before a
minute — it is a microstructure signal and nothing longer.

## Per day — neither day carried the other

| UTC day | rows | `r2_vs_zero` | `rank_ic_non_tied` | tie fraction | trades | ticks |
|---|---|---|---|---|---|---|
| 2026-09-17 | 8,434,640 | +0.024280 | +0.292625 | 0.176231 | 25,411 | 991,411 |
| 2026-09-18 | 7,859,419 | +0.009745 | +0.206997 | 0.097650 | 45,136 | 1,647,349 |

A two-day `val` was chosen precisely so one look yields two observations, and
both are positive on both measures. They are not interchangeable: 09-17
forecasts better (R-squared two and a half times higher) while 09-18 earns more,
because its tie fraction is 9.8% against 17.6% — nearly twice as many rows
actually move, so the same ordering skill becomes 45,136 trades instead of
25,411. Forecast quality and monetisation do not rank the two days the same way.

The per-day ticks sum exactly to the pooled 2,638,760, residual zero: the
position happened to be flat at the midnight boundary, so the simulator's forced
close cost nothing. I had expected a non-zero residual and said so in the
script; it is still computed rather than assumed, because on another window it
will not be zero.

The per-day numbers come from `mvp/scripts/val_per_day_report.py`, which reads
only the already-paid cache and the stored table — re-running it costs nothing.
Before reporting any per-day figure it recomputes ten pooled metrics and
cross-checks them against the MLflow run to a relative tolerance of 1e-12, and
aborts on disagreement.

## Two expectations the run disagreed with

The plan asked for the disagreements rather than the agreements. There were two.

**`pnl_fraction_of_ceiling` is 2.3551 — the P&L is more than twice the
label-horizon ceiling the plan named as its bound.** This is neither a defect
nor news. The OOF simulation measured the same ratio at 2.55×; the leakage
investigation closed it as H1 with forward association strictly above backward
on all five blocks, a bit-identical full-path re-derivation on 1.86M real events
and a non-vacuous permutation check; and the ceiling was reclassified as a
diagnostic on 2026-09-29 for exactly this reason. The guard's real reference is
the midprice's own total variation — a theorem for a one-lot flip-only policy,
since every leg pays at least a one-tick spread — and the P&L sits at 0.8667 of
it, below as it must be. **Had the guard still been fed the old quantity it
would have fired here, after the irreversible look was already spent.** That fix
earned its keep on this run and nowhere else.

**`n_pred_missing` is 1, not the roughly 8,614 the plan predicted.** The 8,614
was not wrong, only attached to the wrong metric: `n_admitted − n_scorable` is
exactly 8,614, the rows whose ten-second forward label is undefined near the
segment end. Exactly one row in sixteen million lacked a *prediction* and took
the neutral mid fill, which is provably neutral because `pred = mid` yields zero
trades on this very window.

A third, minor: the stored table is 181,851,924 bytes where the plan's linear
extrapolation said about 190 MiB. 173.4 MiB actual, roughly 8% under, because
zstd does better at scale than a per-row extrapolation assumes.

## DISCLOSURE, carried from 07-RESEARCH.md rather than paraphrased

**Disclosure — every research measurement in that document that read the
candidate `val` days (2026-09-17 / 2026-09-18).** All of them read Parquet
directly with polars. **None is a `harness.accessor.materialize` call**, so no
look was spent, no `harness-looks` MLflow run exists, and `budget.look_count`
was still 0 for every segment of every manifest when this plan began. **None can
steer model selection**, which D-07-04 confines to the OOF blocks. Listed in
full so this is auditable rather than asserted:

*Structural — the phase's own decisions require these numbers:*

1. Per-day row counts, null counts and exact-zero counts (§Q4's per-day table).
2. Admission counts for both candidate `val` windows (§Q1's tables) — D-07-01
   needs them to specify the manifest at all.
3. The perfect-foresight ceiling and the `pred = mid` zero-trade oracle for both
   candidate `val` windows (§Q7) — D-07-19 explicitly requires re-measurement.

*Model fits — these are the ones worth disclosing:*

4. An in-sample 3-feature OLS on day 18 and on days 17+18: R²_vs_zero +0.022238
   / +0.029105, R²_vs_mean, coefficients, and the raw/standardised Gram
   condition numbers (§Q3b, §Q4).
5. In-sample rank IC on day 18, all-rows and non-tied, plus the
   constant-at-mean and shrunk-by-1e-6 comparison rows (§Q4's pitfall table) —
   this is the evidence for the R²_vs_zero loophole, and day 13 carries the same
   demonstration.
6. A Gram-vs-dense ridge equivalence check at α ∈ {1e-6, 1, 100, 1e4} on day
   18's rows, and the cross-process/cross-thread determinism hashes of the
   resulting coefficients and prediction array (§Q3a, §Q3c).
7. The prediction-table parquet byte measurement, which used day 18's `etime`/
   `decision_seq` columns and a day-18-fitted `pred` column (§Q1, D-07-15).

Items 4–7 produced numbers that sit **inside** the range the train-internal
splits (days 12–17) already establish, so nothing in the plan depended on them —
they are reported only so nobody mistakes a passing honest look for a surprise.

One figure above is now checkable against the real thing: item 4's in-sample
3-feature OLS scored R²_vs_zero +0.029105 on days 17+18, and the frozen
one-feature model scored +0.014151 out of sample on the same days. The honest
number is about half the in-sample one, which is the ordinary direction.

## The bytes, and what committing the manifest did

The stored table is **181,851,924 bytes — 173.4 MiB of zstd parquet at
16,294,059 rows**, three columns (`etime`, `decision_seq`, `pred`), at
`predictions/symbol=BTCUSDT/segment_manifest=807125015b25…/segment=val/predictor=ff91c9aa2a595513/part-1790835666098050000.parquet`.

From commit `0003497` onward **that parquet can never be deleted or rewritten in
place.** The per-commit `check_no_manifest_rewrite` fast leg stat-compares its
recorded `size_bytes` and `mtime_ns` against the file on every subsequent commit
on this machine; the pre-push leg recomputes its sha256. It can only be
superseded by a NEW manifest. On a machine without the SSD mounted the check
SKIPs honestly. This is already the status quo for the 9.3 GB the lake's other
committed manifests name — it is not a new dependency.

**That guarantee was verified rather than assumed.** `--full` over the real lake
reports 140 manifests checked in 6.1 s, which is fast enough to doubt it read
174 MiB. So the table was copied to a throwaway lake with a throwaway registry
holding only its manifest: the untampered copy passes, and flipping a single
byte at offset 90,000,000 makes it FAIL and name the exact partition. The
original was never touched — its mtime is now part of a committed manifest — and
the copies were deleted.

## Provenance

| field | value |
|---|---|
| `stage-1-regression` run | `5758c91b0c194852ae46597394a9467c` |
| `data_hash` | `f40701838e555bcf811a57f1e24ab2eaa22d2412c100118e308ff22b3a43a106` (= the prediction-table manifest id) |
| `predictor_manifest_id` | `e3b4b235d0fe3c025be7c7cdbf7eaa3d2d5675ad9571d6f6a30b1a45680c2d01` |
| `predictor_id` | `ff91c9aa2a595513f407c4a01ad794ebc4b5ccd3595c61d5dabdcc426298c3ff` |
| `normalization_manifest_id` | `c7749334fb73e090272013dfeb10013e25948b11a0907d56a69ee39d7b8954ba` |
| `segment_manifest_id` | `807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548` |
| `code_hash` | `d9ec46f2e9a331a06be53ceaaef5a3c16b1d2297` (clean) |
| `env_hash` | `536f887cc8ab6dd1511735c1a6f492240b6bfc174587686db2088a8387cacafb` |
| `seed` | 20260925 |
| `fold_config` | `compressed_3seg` |
| `model_class` | `sklearn.ElasticNet` |
| `dq_ack_ids` / `dq_ack_sha256` | none / none — the pre-flight refused nothing |
| `data_manifest_ids` | 11 ids |
| `look_run_ids` | 6 ids, all unique |

`look_run_ids` was queried back from the `harness-looks` runs by
`segment_manifest_id` rather than carried in memory, because the five OOF looks
happened in a different process at an earlier commit. The union is exactly six,
and their own `segment_name` tags read `oof_block_0..4` and `val` — one each.

The run is tagged **`scored_segment_name=val`, not `segment_name`**. The budget
counter matches on `(segment_manifest_id, segment_name)` across every experiment,
so a tracked run that is not a look must never carry that key.

Peak resident memory 7.04 GiB against 32 GiB physical; nothing swapped out from
under it.

## The look budget, after

```
val              look_count=1
oof_block_0      look_count=1
oof_block_1      look_count=1
oof_block_2      look_count=1
oof_block_3      look_count=1
oof_block_4      look_count=1
```

`held_out` is 0 on both committed segment manifests, and every counter on the
second manifest `97964cb27f62` is 0. **Two of the allowance of three remain, and
they exist for a re-run after a genuine diagnosed bug. Not for the runner-up,
which never meets `val`.**

## What was NOT done

- **No held-out window was declared or locked.** That is Phase 8's success
  criterion 4, and `held_out` is still 0.
- **No trees and no transformer.** Phase 8. The roadmap's v0 gate needs all three
  model classes; this phase proved the plumbing with the cheapest one.
- **No Optuna search.** Phase 8.
- **No Stage-2 threshold sweep.** The simulator ran at `x_bps=0` only, to prove
  the wiring. At `x_bps=1` this model trades zero times — the edge is 0.519 bp
  per round trip and one basis point is above it.
- **No reporting suite.** Phase 9.
- **Zero fees and zero latency still carry the P&L**, which is the standing
  simplification and not a result of this plan. The OOF work measured that zero
  fees and not the fill assumption carries the sign.
- **No per-day bootstrap interval**, so "both days positive" is two point
  estimates, not a claim about significance.
- **No per-day breakdown of the diagnostic horizons.**
- `scripts/oof_viability_check.py` is still byte-unchanged on purpose, so it
  still hands the guard the old label-horizon ceiling and exits 1 on four of five
  blocks. It is the byte-identity instrument for the Stage-2 threshold change and
  changing it would destroy the only thing that can prove nothing else moved.
- The three expectation disagreements above are recorded, not smoothed over.

## Success criteria

- **SC1 — met.** The regression track trained on harness folds, predicted the
  10-second midprice return, and its performance against the zero baseline on
  validation is reported as numbers with both R-squared references and the
  non-tied rank IC: `r2_vs_zero` +0.014151, `r2_vs_mean` +0.013563,
  `rank_ic_non_tied` +0.246301, beside `tie_fraction` 0.138330 and the zero-skill
  control +0.000197.
- **SC2 — met.** The frozen predictor produced a deterministic, manifest-keyed
  prediction table; `assert_table_aligned` passed on read-back and the alignment
  was re-asserted independently afterwards on both keys.
- **SC3 — met.** The full slice ran end to end on validation data with the run
  manifest in MLflow, all eight mandatory tags non-empty.
- Exactly one look was spent.
