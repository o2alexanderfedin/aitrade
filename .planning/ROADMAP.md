# Roadmap: AiHedgeFund — BinanceSwap Forecast + Monetization MVP

## Overview

The roadmap is shaped by one verified fact and two methodology constraints. The fact: Binance L1 history is not backfillable, so the capture daemon is schedule-critical and starts in Phase 1 — every later phase builds on fixtures while real history accrues. The constraints: no model training before Stage 0 (spec seeded, catalogues wired into CI), and all overfitting controls (lockbox quarantine, selection-bias ledger, segment manifests) must exist *before* the first training run. The journey: stand up capture and the living spec, build the canonical data layer with mechanical lockbox, then the two spine components (feature kernel, fold harness) and the simulator in parallel-friendly slices, wire the cheapest model class end-to-end, add trees and transformer for the three-class v0 gate, fit Stage 2 monetization for v1, then prove the agentic loop and scale to multi-symbol/universal models for MVP exit (Net P&L > 0, Sharpe > 5 on locked held-out). All code lives under `mvp/`.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Capture Daemon & Repo Foundation** - Schedule-critical redundant L1+trades capture running day 1, repo skeleton under mvp/ (completed 2026-09-12)
- [x] **Phase 2: Stage 0 — Living Spec, CI Guardrails & Tracking** - spec.md seeded with corrected policy math, catalogues wired into CI, MLflow foundation
- [x] **Phase 3: Data Layer — Backfill, Ingest & Lockbox** - Trades backfill, side correction, manifest-addressed store, DQ reports, mechanical lockbox quarantine (completed 2026-09-17)
- [x] **Phase 4: Feature & Label Engine** - Single numba streaming code path, catalogued L1 features, leakage-proven labels (completed 2026-09-19)
- [x] **Phase 5: Fold Harness & Overfitting Controls** - 5-segment walk-forward with embargo, OOF fallback, selection-bias budget, negative-result log
- [x] **Phase 6: Event-Driven Simulator** - Numba flip-only sim with integer-tick accounting and oracle tests
- [ ] **Phase 7: Regression Track & Vertical Slice** - Cheapest model class wires Trainer protocol, frozen predictors, prediction tables end-to-end
- [ ] **Phase 8: Trees, Transformer & v0 Gate** - All three classes on same folds, cross-class protocol, held-out locked, v0 smoke gate
- [ ] **Phase 9: Stage 2 Monetization & v1 Gate** - Optuna X sweep on out-of-train predictions, knife-edge rejection, full report suite, v1 gate
- [ ] **Phase 10: Agentic Loop** - Agent query surface, sandboxed iteration, one verifiable metric-moving improvement
- [ ] **Phase 11: Multi-Symbol, Universal Model & MVP Exit** - v2 per-symbol, v3 universal, MVP exit gate evaluation

## Phase Details

### Phase 1: Capture Daemon & Repo Foundation
**Goal**: Irreplaceable L1+trades history is being recorded reliably from day 1, inside a contained mvp/ skeleton
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: DATA-01, DATA-04
**Success Criteria** (what must be TRUE):
  1. Redundant capture daemon records Binance Swap (and Spot, per chosen etime strategy) L1 + trades to Parquet continuously, surviving a single connection drop without data loss
  2. Gap ledger records every capture outage; liveness watchdog flags stalls
  3. All captured timestamps are int64 nanoseconds since epoch with `etime` as the clock and a monotonic per-stream `seq` column written at capture time
  4. Repo skeleton exists under `mvp/` with uv lockfile honoring the numba/numpy/llvmlite pin; nothing MVP-related lives outside `mvp/`
  5. The Tardis.dev buy-vs-wait-vs-two-regime decision is forced, made, and recorded
**Plans**: 4 plans

Plans:
- [x] 01-01-PLAN.md — Walking Skeleton: repo scaffold, canonical schema, live one-shot proof against the real exchange
- [x] 01-02-PLAN.md — Continuous single-connection daemon: atomic Parquet rotation, restart-safe seq, startup liveness assertion, graceful shutdown
- [x] 01-03-PLAN.md — Redundancy: staggered second connection, bounded dedup, reactive gap ledger
- [x] 01-04-PLAN.md — Proactive watchdog, atomic-write hardening test, deploy artifacts (Dockerfile, systemd)

### Phase 2: Stage 0 — Living Spec, CI Guardrails & Tracking
**Goal**: The living spec, CI enforcement, and experiment-tracking foundation exist so that no untracked or uncatalogued training can ever happen
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: SPEC-01, SPEC-02, SPEC-03, SPEC-04, TRACK-01, TRACK-02
**Success Criteria** (what must be TRUE):
  1. spec.md exists in seed form under mvp/ with feature and label catalogues in machine-readable format that CI parses
  2. CI fails any training code using a feature/label without a catalogue entry, any `import pandas`, and any `latest` data reference
  3. spec.md contains the corrected decision-rule pseudocode (dimensional bug fixed), the spot-L1 clock exception, and the trades-backfill side-exactness note
  4. Sharpe annualization convention, MLflow tag schema, and numba no-globals lint rule are pre-declared in spec.md before any run exists
  5. MLflow on SQLite backend records code hash + data hash + seed + env hash for a test run; environment pins are CI-enforced
**Plans**: 4 plans

Plans:
- [x] 02-01-PLAN.md — Move spec.md/mvp.md under mvp/, land all spec corrections, TOML catalogues + registry + renderer
- [x] 02-02-PLAN.md — CI guardrail scripts: catalogue completeness, latest-ban, numba-no-globals, pin assertion
- [x] 02-03-PLAN.md — MLflow tracking wrapper (mlflow-skinny, mandatory tags, root guard reuse, smoke run)
- [x] 02-04-PLAN.md — Wire pre-commit + GitHub Actions, prove every check red-then-green

### Phase 3: Data Layer — Backfill, Ingest & Lockbox
**Goal**: A canonical, immutable, manifest-addressed data lake exists with quality gates and a mechanically enforced held-out quarantine
**Mode:** mvp
**Depends on**: Phase 1, Phase 2
**Requirements**: DATA-02, DATA-03, DATA-05, DATA-06, DATA-07, DATA-08
**Success Criteria** (what must be TRUE):
  1. ~3 months of trades are backfilled from data.binance.vision with a per-dataset unit registry; all timestamps pass the CI plausibility gate despite format heterogeneity
  2. Legacy `tradeSide = 0` rows are classified by nearest L1 quote, with `tradeSide_raw` and `tradeSide_corrected` stored side by side
  3. Data lake is partitioned by symbol/date with versioned schema; artifacts are immutable and manifest-addressed; `(etime, seq)` makes "last row of each etime" deterministic
  4. Daily data-quality report exists (dropped events, sparsity, resync warm-up tags); degradation pauses training until explicitly acknowledged
  5. Lockbox-quarantined segments are physically unreadable through the default loader; access requires an explicit unlock token that is logged to MLflow as a one-look annotation
**Plans**: 6 plans, 4 waves

Plans:
- [x] 03-01-PLAN.md — Acquire + normalize + raw tier (unit registry, backfill client, ms_to_ns rename, raw partition write; real 2026-09-12 slice)
- [x] 03-02-PLAN.md — Curated build + manifest + store loader (trade-side resolution, (etime,seq) materialization, manifest issuance, check_no_manifest_rewrite; RP-1, RP-2)
- [x] 03-03-PLAN.md — Widen: full backfill window, monthly extract-to-disk, bookTicker curated tier, trade-side cross-check
- [x] 03-04-PLAN.md — DQ report (6 checks), pause enforcement, real battery-sleep acknowledgements; RP-4
- [x] 03-05-PLAN.md — Lockbox quarantine: token API, chmod 0000 barrier, containment guardrail; RP-3
- [x] 03-06-PLAN.md — Capture schema v2 (exec_type) + RawArchiveWriter fix + gated daemon restart

### Phase 4: Feature & Label Engine
**Goal**: One leakage-proven feature code path produces the decision-row matrix that training, inference, and the simulator all share
**Mode:** mvp
**Depends on**: Phase 2, Phase 3
**Requirements**: FEAT-01, FEAT-02, FEAT-03, FEAT-04, FEAT-05
**Success Criteria** (what must be TRUE):
  1. A single numba streaming kernel (state accumulates every row, decisions emit on last row per `etime`) produces byte-identical feature output when invoked from the training, inference, and simulator paths
  2. Initial L1 microstructure features (mid, TOB imbalance, OFI, trade-flow) are implemented per catalogue, each with an information-set entry
  3. CI leakage suite passes: per-feature future-shuffle invariance and embargo ≥ label horizon
  4. Labels exist for 10s midprice return (primary) plus diagnostic horizons {1s, 1min, 10min}
  5. Rolling/expanding normalization is computed on training data only, verified by test
**Plans**: 7 plans, 5 waves

Plans:
- [x] 04-01-PLAN.md — One ns-constant module + merged event stream and the decision-row rule (merge_sorted, source_rank pin, strict-total-order runtime gate)
- [x] 04-02-PLAN.md — Feature tier plumbing: schema, write-once partition, manifest + load_features, holdout/quarantine refusal (D and D+1), feature-tier DQ checks
- [x] 04-03-PLAN.md — The numba kernel (mid, imb_top, ofi, trade_flow) + pure-Python reference + leakage properties red-first + feature catalogue pinned
- [x] 04-04-PLAN.md — Labels: backward as-of, null-on-gap/past-end, build-D-only-after-D+1, label catalogue + measured quantization
- [x] 04-05-PLAN.md — The real end-to-end build of 2026-09-12..14, warm-up tagging, build stats, feature DQ rows + acknowledgements, load_features round trip
- [x] 04-06-PLAN.md — CI leakage suite (FEAT-03): per-feature/per-label invariance + anti-vacuity sensitivity, embargo assertion, catalogue cross-check, named CI gate
- [x] 04-07-PLAN.md — Single code path: three call sites byte-identical (batch/chunked/per-row) + train-only normalization artifact (FEAT-05)

### Phase 5: Fold Harness & Overfitting Controls
**Goal**: A fold harness that owns time — every split, look, and failure is a tracked artifact before any model trains
**Mode:** mvp
**Depends on**: Phase 2, Phase 3
**Requirements**: EVAL-01, EVAL-02, EVAL-03, EVAL-04
**Success Criteria** (what must be TRUE):
  1. 5-segment walk-forward split (Train_S1 | Val_S1 | Train_S2 | Val_S2 | Held-out) with embargo gaps produces segment manifests stored as data that downstream artifacts reference
  2. Compressed 3-segment fallback with purged+embargoed inner k-fold OOF is selectable per run, with the choice and reason recorded in MLflow
  3. Every validation look increments the selection-bias budget in MLflow; budget exhaustion forces a fresh window
  4. Negative-result log records failed configs in queryable form
**Plans:** 8/8 plans executed (COMPLETE 2026-09-22)

Plans:
**Wave 1**
- [x] 05-00-PLAN.md — widen the built-day pool from 3 to 7 days (D-05-19), independent of every other plan
- [x] 05-01-PLAN.md — thin vertical slice: harness package, purge/embargo constants, 5seg manifest schema, minimal accessor + budget, the walking-skeleton proof

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 05-02-PLAN.md — D-05-02 geometric refusals, purged+embargoed inner k-fold OOF (kfold.py), compressed_3seg issuance
- [x] 05-04-PLAN.md — stale-book row admission (29,058 reproduction) and errata computation (249-cell reproduction), wired into the accessor
- [x] 05-05-PLAN.md — held-out declaration tool (--dry-run), the new data.lockbox quarantine function, the holdout.json writer
- [x] 05-06-PLAN.md — static tripwire (check_harness_accessor_only), the 19th guardrail hook

**Wave 3** *(blocked on Wave 2 completion)*
- [x] 05-03-PLAN.md — budget exhaustion + issuance-time overlap refusal, negative-result log and CLI

**Wave 4** *(blocked on Wave 3 completion)*
- [x] 05-07-PLAN.md — guardrail extension for segments/errata, the first real committed segment and errata manifests, fold_config reason wiring, spec.md Fold harness section

### Phase 6: Event-Driven Simulator
**Goal**: A verified-correct simulator whose policy math is proven against oracles before any model output touches it
**Mode:** mvp
**Depends on**: Phase 2, Phase 4
**Requirements**: SIM-01, SIM-02, SIM-03
**Success Criteria** (what must be TRUE):
  1. Numba event-driven simulator runs taker-only, zero-fee, zero-latency, $100-max-notional, flip-only logic as a sequential scan over decision rows (no vectorization)
  2. Oracle tests reproduce hand-computed scenario P&L exactly, including a realized-future-returns oracle that bounds the achievable ceiling — the test class that catches decision-rule dimensional bugs
  3. Integer-tick price accounting in the hot path yields bit-identical P&L across double runs (CI-enforced), with no float-drift errors
**Plans**: 7 plans, 4 waves — COMPLETE 2026-09-24 (merged to develop as 607200e)

Plans:
**Wave 1**
- [x] 06-01-PLAN.md — Schema v2 migration code: kernel/reference/api emit bid_price/ask_price, tier.py version-scoped write-once identity, DQ-report multi-manifest fix (fixture-only)
- [x] 06-02-PLAN.md — mvp/sim/ticks.py: tick constant + round-trip proof, lot-step measurement, $100-cap zero-lot dead zone

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 06-03-PLAN.md — mvp/sim/kernel.py: the sequential flip-only run_sim state machine, arrays.py boundary, outputs.py, reference.py twin, bitwise equivalence
- [x] 06-04-PLAN.md — The real 7-day schema-v2 rebuild + the v1-vs-v2 label regression proof (249 errata cells, nowhere else)

**Wave 3** *(blocked on Wave 2 completion)*
- [x] 06-05-PLAN.md — Cross-process determinism (sha256) + refuse-on-null/dropped-row contract
- [x] 06-06-PLAN.md — Path-dependence, flip-invariant, hand-computed/zero-prediction oracles, real-day perfect-foresight reconciliation

**Wave 4** *(blocked on Wave 3 completion)*
- [x] 06-07-PLAN.md — spec.md Simulator section + phase-wide verification pass

### Phase 7: Regression Track & Vertical Slice
**Goal**: The cheapest model class proves the full Stage-1 plumbing — Trainer protocol, frozen predictors, prediction tables — end to end
**Mode:** mvp
**Depends on**: Phase 4, Phase 5, Phase 6
**Requirements**: FCST-01, FCST-04
**Success Criteria** (what must be TRUE):
  1. Regression track (linear → ridge/elastic-net → small non-linear) trains on harness folds and predicts 10s midprice return, beating the zero baseline on validation
  2. Frozen-predictor interface yields a deterministic predictor producing precomputed prediction tables keyed by segment manifest
  3. The full vertical slice runs end to end on validation data: curated store → feature kernel → trained regressor → prediction table → simulator, with the run manifest in MLflow
**Plans**: 11 plans, 9 waves

Plans:
- [x] 07-01-PLAN.md — scikit-learn locked, the pin guardrail widened, the `tests/models/` floor (wave 1)
- [x] 07-02-PLAN.md — the 7-day `compressed_3seg` segment manifest, alone in its wave: clean tree, 19.35 GiB peak, effectively one-shot (wave 2, **checkpoint**)
- [x] 07-03-PLAN.md — Trainer/FrozenPredictor protocol naming no sklearn type, `predictor_id`, the learnable fixture rig (wave 3)
- [x] 07-04-PLAN.md — the frozen predictor as coefficient JSON, never a pickle, with cross-process determinism (wave 4)
- [x] 07-05-PLAN.md — the predictions tier, the return→price conversion, row-alignment proof (wave 4)
- [x] 07-06-PLAN.md — four estimators over a counted 17-config grid, the Gram oracle, the three price-column refusals (wave 5)
- [x] 07-07-PLAN.md — metrics with BOTH R² references, the gates, the re-measured ceiling, spec.md (wave 5)
- [x] 07-08-PLAN.md — materialize-once cache keyed by tracking root, the OOF sweep, the negative-result log (wave 6)
- [x] 07-09-PLAN.md — `run_slice`, the CLI and its refusals, the fixture end-to-end proof (wave 7)
- [x] 07-10-PLAN.md — real normalisation, the five OOF looks, the winner frozen and committed (wave 8, **checkpoint**) — Tasks 1 and 2 done. Normalisation committed; five OOF looks spent; the grid widened 17→36 after 0 of 17 passed, re-scored at ZERO additional looks, 9 of 36 eligible; **ElasticNet(alpha=1e-4, l1_ratio=0.30) frozen as `predictors/e3b4b235d0fe…json` in ONE commit (`b75edc6`) with both registry guardrail extensions**, and it is a ONE-FEATURE model (`imb_top` only). `look_count(val)` is still **0**. **Task 3, the blocking human-verify checkpoint, is OPEN** — see 07-10-SUMMARY.md's DISCLOSURE section.
- [ ] 07-11-PLAN.md — the ONE honest `val` look, the stored table, the simulator, the disclosure (wave 9, **checkpoint**) — **OUTSTANDING and NON-AUTONOMOUS. Must not run until Task 3 of 07-10 is approved.**

**Window**: train 2026-09-12..16, val 2026-09-17..18. `budget_allowance = 3`; the phase spends 6 looks total (5 OOF + 1 val) and no more.
**Cross-cutting**: waves 2, 8 and 9 are the only ones that touch anything irreversible, and all three are non-autonomous checkpoints.

### Phase 8: Trees, Transformer & v0 Gate
**Goal**: All three model classes compete on identical folds and metrics, and the v0 smoke gate passes with the held-out window locked
**Mode:** mvp
**Depends on**: Phase 7
**Requirements**: FCST-02, FCST-03, FCST-05, FCST-06, EVAL-06, GATE-01, AGNT-01
**Success Criteria** (what must be TRUE):
  1. LightGBM (early stopping) and PyTorch transformer (GPU, causal masking verified by CI leakage test) tracks train on the same folds, features, and metrics as the regression track
  2. Hyperparameter search is budget-capped per class via Optuna with every trial counted; multiple seeds per config with seed variance reported
  3. Cross-class comparison report exists: per-fold ranking with std confidence bands, IC@10s as primary metric
  4. v0 gate passes: single symbol, fixed X, all three classes beat the zero baseline end-to-end; the held-out window is declared and locked behind the lockbox (one look per gate from here on)
  5. Agentic loop scope (mvp.md Q1) is decided and documented in spec.md before v1 work begins
**Plans**: TBD

### Phase 9: Stage 2 Monetization & v1 Gate
**Goal**: A robust threshold policy is fit without leakage and the v1 gate reports all three classes side by side on held-out
**Mode:** mvp
**Depends on**: Phase 5, Phase 6, Phase 8
**Requirements**: MON-01, MON-02, MON-03, EVAL-05, GATE-02
**Success Criteria** (what must be TRUE):
  1. TOB-cross threshold X is swept via Optuna (grid/TPE/CMA-ES) strictly on out-of-Stage-1-train prediction tables — Stage-2 code structurally cannot read Stage-1 training data
  2. Selected X comes from a robust plateau (metric within declared band across an X-neighborhood) with rank stability across folds; knife-edge optima are rejected
  3. Stage-2 reports include annualized Sharpe (primary), net P&L, hit rate, bps/round-trip, turnover, max drawdown, Calmar, and time-in-market
  4. Full reporting suite runs: per-fold metrics ± std, regime splits (high/low vol), effective sample size (HAC/block bootstrap), Deflated Sharpe Ratio, equity curves, trade logs, alpha-decay curve
  5. v1 gate passes: X swept per class, picked on Val, reported on held-out, with a side-by-side three-class report
**Plans**: TBD

### Phase 10: Agentic Loop
**Goal**: The agentic-iteration workflow is operational and has verifiably improved the model
**Mode:** mvp
**Depends on**: Phase 3, Phase 9
**Requirements**: AGNT-02
**Success Criteria** (what must be TRUE):
  1. The agent iterates through a structured surface: MLflow query access, negative-result log, and experiment-proposal format, inside a sandbox that physically excludes the lockbox path
  2. At least one agent-produced model improvement is merged in the v0 → MVP-exit chain, with before/after MLflow runs proving the validation metric moved
  3. The improvement survives the overfitting controls: counted against the selection-bias budget, robust across folds, not a knife-edge artifact
**Plans**: TBD

### Phase 11: Multi-Symbol, Universal Model & MVP Exit
**Goal**: The pipeline generalizes beyond one symbol and the MVP exit gate is formally evaluated
**Mode:** mvp
**Depends on**: Phase 9, Phase 10
**Requirements**: GATE-03, GATE-04, GATE-05
**Success Criteria** (what must be TRUE):
  1. v2 gate: ETH + the chosen 2nd-tier symbol train separately per symbol per class, with side-by-side three-class reporting per symbol
  2. v3 gate: each class trains jointly on all listed perps as a universal model, with side-by-side reporting against per-symbol baselines
  3. MVP exit gate is evaluated on the locked held-out window: Net P&L > 0 and annualized Sharpe > 5 achieved by at least one class, with side-by-side metrics for all three
  4. Every held-out look consumed at v2/v3/exit is a logged one-look unlock per gate
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Capture Daemon & Repo Foundation | 4/4 | Complete | 2026-09-13 |
| 2. Stage 0 — Living Spec, CI Guardrails & Tracking | 4/4 | Complete | 2026-09-14 |
| 3. Data Layer — Backfill, Ingest & Lockbox | 7/6 | Complete    | 2026-09-17 |
| 4. Feature & Label Engine | 7/7 | Complete    | 2026-09-19 |
| 5. Fold Harness & Overfitting Controls | 8/8 | Complete    | 2026-09-22 |
| 6. Event-Driven Simulator | 7/7 | Complete    | 2026-09-24 |
| 7. Regression Track & Vertical Slice | 10/11 | In Progress — wave 9 outstanding, non-autonomous; 07-10's approval checkpoint open |  |
| 8. Trees, Transformer & v0 Gate | 0/TBD | Not started | - |
| 9. Stage 2 Monetization & v1 Gate | 0/TBD | Not started | - |
| 10. Agentic Loop | 0/TBD | Not started | - |
| 11. Multi-Symbol, Universal Model & MVP Exit | 0/TBD | Not started | - |

---
*Roadmap created: 2026-06-10*
*Coverage: 44/44 v1 requirements mapped*
