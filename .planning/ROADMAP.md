# Roadmap: AiHedgeFund — BinanceSwap Forecast + Monetization MVP

## Overview

The roadmap is shaped by one verified fact and two methodology constraints. The fact: Binance L1 history is not backfillable, so the capture daemon is schedule-critical and starts in Phase 1 — every later phase builds on fixtures while real history accrues. The constraints: no model training before Stage 0 (spec seeded, catalogues wired into CI), and all overfitting controls (lockbox quarantine, selection-bias ledger, segment manifests) must exist *before* the first training run. The journey: stand up capture and the living spec, build the canonical data layer with mechanical lockbox, then the two spine components (feature kernel, fold harness) and the simulator in parallel-friendly slices, wire the cheapest model class end-to-end, add trees and transformer for the three-class v0 gate, fit Stage 2 monetization for v1, then prove the agentic loop and scale to multi-symbol/universal models for MVP exit (Net P&L > 0, Sharpe > 5 on locked held-out). All code lives under `mvp/`.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Capture Daemon & Repo Foundation** - Schedule-critical redundant L1+trades capture running day 1, repo skeleton under mvp/ (completed 2026-09-12)
- [ ] **Phase 2: Stage 0 — Living Spec, CI Guardrails & Tracking** - spec.md seeded with corrected policy math, catalogues wired into CI, MLflow foundation
- [ ] **Phase 3: Data Layer — Backfill, Ingest & Lockbox** - Trades backfill, side correction, manifest-addressed store, DQ reports, mechanical lockbox quarantine
- [ ] **Phase 4: Feature & Label Engine** - Single numba streaming code path, catalogued L1 features, leakage-proven labels
- [ ] **Phase 5: Fold Harness & Overfitting Controls** - 5-segment walk-forward with embargo, OOF fallback, selection-bias budget, negative-result log
- [ ] **Phase 6: Event-Driven Simulator** - Numba flip-only sim with integer-tick accounting and oracle tests
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
**Plans**: TBD

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
**Plans**: TBD

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
**Plans**: TBD

### Phase 6: Event-Driven Simulator
**Goal**: A verified-correct simulator whose policy math is proven against oracles before any model output touches it
**Mode:** mvp
**Depends on**: Phase 2, Phase 4
**Requirements**: SIM-01, SIM-02, SIM-03
**Success Criteria** (what must be TRUE):
  1. Numba event-driven simulator runs taker-only, zero-fee, zero-latency, $100-max-notional, flip-only logic as a sequential scan over decision rows (no vectorization)
  2. Oracle tests reproduce hand-computed scenario P&L exactly, including a realized-future-returns oracle that bounds the achievable ceiling — the test class that catches decision-rule dimensional bugs
  3. Integer-tick price accounting in the hot path yields bit-identical P&L across double runs (CI-enforced), with no float-drift errors
**Plans**: TBD

### Phase 7: Regression Track & Vertical Slice
**Goal**: The cheapest model class proves the full Stage-1 plumbing — Trainer protocol, frozen predictors, prediction tables — end to end
**Mode:** mvp
**Depends on**: Phase 4, Phase 5, Phase 6
**Requirements**: FCST-01, FCST-04
**Success Criteria** (what must be TRUE):
  1. Regression track (linear → ridge/elastic-net → small non-linear) trains on harness folds and predicts 10s midprice return, beating the zero baseline on validation
  2. Frozen-predictor interface yields a deterministic predictor producing precomputed prediction tables keyed by segment manifest
  3. The full vertical slice runs end to end on validation data: curated store → feature kernel → trained regressor → prediction table → simulator, with the run manifest in MLflow
**Plans**: TBD

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
| 2. Stage 0 — Living Spec, CI Guardrails & Tracking | 3/4 | In Progress | - |
| 3. Data Layer — Backfill, Ingest & Lockbox | 0/TBD | Not started | - |
| 4. Feature & Label Engine | 0/TBD | Not started | - |
| 5. Fold Harness & Overfitting Controls | 0/TBD | Not started | - |
| 6. Event-Driven Simulator | 0/TBD | Not started | - |
| 7. Regression Track & Vertical Slice | 0/TBD | Not started | - |
| 8. Trees, Transformer & v0 Gate | 0/TBD | Not started | - |
| 9. Stage 2 Monetization & v1 Gate | 0/TBD | Not started | - |
| 10. Agentic Loop | 0/TBD | Not started | - |
| 11. Multi-Symbol, Universal Model & MVP Exit | 0/TBD | Not started | - |

---
*Roadmap created: 2026-06-10*
*Coverage: 44/44 v1 requirements mapped*
