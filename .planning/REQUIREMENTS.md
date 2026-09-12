# Requirements: AiHedgeFund — BinanceSwap Forecast + Monetization MVP

**Defined:** 2026-06-10
**Core Value:** A reproducible, leakage-proof two-stage pipeline achieving Net P&L > 0 and annualized Sharpe > 5 on a locked held-out walk-forward window under stated simplifications — produced by a workflow where agentic iteration verifiably improves the model.

## v1 Requirements

Requirements for MVP exit. Each maps to roadmap phases. Source of truth: `mvp.md` + `spec.md` (spec wins), enriched by `.planning/research/`.

### Stage 0 — Living Spec & Conventions

- [ ] **SPEC-01**: spec.md exists in seed form with feature and label catalogues in machine-readable format (structured entries CI can parse, not prose tables)
- [ ] **SPEC-02**: CI validates that every feature/label used in training has a catalogue entry, and rejects `import pandas` and `latest` data references
- [ ] **SPEC-03**: mvp.md decision-rule pseudocode dimensional bug fixed (`mid * (1 + pred)` vs price level) and spec.md amended same-day with: spot-L1 clock exception, trades-backfill side-exactness
- [ ] **SPEC-04**: Sharpe annualization convention, MLflow tag schema, and numba no-globals lint rule pre-declared in spec.md before any run

### Data — Capture, Backfill, Quality

- [ ] **DATA-01**: Redundant capture daemon records Binance Swap (and Spot if feasible) L1 + trades to Parquet with a gap ledger, running from Phase 1 onward (L1 history is not backfillable — verified)
- [ ] **DATA-02**: Trades backfilled from data.binance.vision with per-dataset unit registry handling format heterogeneity (spot ms→µs switch at 2025-01-01, futures ms, header differences)
- [ ] **DATA-03**: Trade-side backfill preprocessing classifies legacy `tradeSide = 0` rows by nearest L1 quote; corrected side stored alongside raw (`tradeSide_raw`, `tradeSide_corrected`)
- [x] **DATA-04**: All timestamps stored as int64 nanoseconds since epoch; `etime` is the only clock (documented local-clock exception for spot L1 if that route is chosen)
- [ ] **DATA-05**: Ingest materializes an `(etime, seq)` arrival-order column so "last row of each etime" is deterministic
- [ ] **DATA-06**: Parquet data lake partitioned by symbol/date with versioned schema; immutable, manifest-addressed artifacts
- [ ] **DATA-07**: Daily data-quality report (dropped-event counts per filter, sparsity, resync warm-up tagging); degradation pauses training until acknowledged
- [ ] **DATA-08**: Held-out lockbox mechanically enforced at the data-loader level (quarantined segments unreadable without an explicit one-look MLflow annotation)

### Features & Labels

- [ ] **FEAT-01**: Single feature-pipeline code path (numba streaming kernel) shared byte-identically by training, inference, and simulator; state accumulates over every row, decisions emit on last row of each `etime`
- [ ] **FEAT-02**: Initial L1 microstructure feature set implemented per catalogue (mid, top-of-book imbalance, OFI, trade-flow features), each with information-set entry
- [ ] **FEAT-03**: CI leakage test proves per-feature information sets (shuffle-future-data invariance)
- [ ] **FEAT-04**: Label generation for 10s midprice return (primary) + diagnostic horizons {1s, 1min, 10min}, embargo ≥ horizon
- [ ] **FEAT-05**: Rolling/expanding normalization computed on training data only

### Evaluation Harness

- [ ] **EVAL-01**: Walk-forward fold harness with 5-segment split (Train_S1 | Val_S1 | Train_S2 | Val_S2 | Held-out) and embargo gaps; segment manifests stored as data
- [ ] **EVAL-02**: Compressed 3-segment fallback (Train shared via purged+embargoed inner k-fold OOF | Val | Held-out) selectable per run with reason recorded in MLflow
- [ ] **EVAL-03**: Selection-bias budget tracked in MLflow; every validation look counted; budget exhaustion forces a fresh window
- [ ] **EVAL-04**: Negative-result log records failed configs
- [ ] **EVAL-05**: Reporting suite: per-fold metrics ± std, regime-split (high/low vol), effective sample size (HAC/block bootstrap), Deflated Sharpe Ratio, equity curves, trade logs, alpha-decay curve
- [ ] **EVAL-06**: Held-out window locked at v0; one look per gate enforced via EVAL/DATA-08 quarantine

### Stage 1 — Forecast

- [ ] **FCST-01**: Regression track (linear → ridge/elastic-net → small non-linear) predicting 10s midprice return, scikit-learn
- [ ] **FCST-02**: Gradient-boosted trees track, LightGBM default with early stopping
- [ ] **FCST-03**: Transformer track, PyTorch with causal masking verified by CI leakage test (GPU)
- [ ] **FCST-04**: Frozen-predictor interface: deterministic predictor per class producing precomputed prediction tables keyed by segment manifest
- [ ] **FCST-05**: Cross-class comparison protocol: same folds, same held-out, same metrics; per-fold ranking with std confidence bands; IC@10s primary metric
- [ ] **FCST-06**: Hyperparameter search budget capped per class; multiple seeds per config with seed variance reported

### Simulator

- [ ] **SIM-01**: Event-driven numba simulator: taker-only, zero fees, zero latency, $100 max notional, flip-only position logic, sequential scan (no vectorization — policy is path-dependent)
- [ ] **SIM-02**: Oracle tests validate simulator policy math against hand-computed scenarios (catches decision-rule dimensional bugs)
- [ ] **SIM-03**: Integer-tick price accounting in the hot path to avoid float-drift P&L errors

### Stage 2 — Monetization

- [ ] **MON-01**: TOB-cross threshold policy (X bps) fit via black-box optimization (Optuna: grid/TPE/CMA-ES) on out-of-Stage-1-train prediction tables only
- [ ] **MON-02**: Knife-edge rejection: X selected only from robust regions (metric within declared band across an X-neighborhood); rank stability across folds required
- [ ] **MON-03**: Stage-2 metrics: annualized Sharpe (primary), net P&L, hit rate, bps/round-trip, turnover, max drawdown, Calmar, time-in-market

### Tracking & Reproducibility

- [ ] **TRACK-01**: MLflow with SQL (SQLite) backend; run manifest records code hash + data hash + seed + env hash for every run
- [ ] **TRACK-02**: Environment pinned (numba/numpy/llvmlite compatibility matrix, Python 3.13, uv-managed); CI enforces pins

### Version Gates

- [ ] **GATE-01**: v0 smoke test — single symbol, fixed X, all three classes beat zero baseline end-to-end; held-out window declared and locked
- [ ] **GATE-02**: v1 threshold sweep — X swept per class, picked on Val, reported on held-out, side-by-side three-class report
- [ ] **GATE-03**: v2 multi-symbol — ETH + 2nd-tier symbol, separate per-symbol training per class
- [ ] **GATE-04**: v3 universal model — each class trained jointly on all listed perps
- [ ] **GATE-05**: MVP exit gate evaluated: Net P&L > 0 and annualized Sharpe > 5 on held-out by ≥1 class, with side-by-side metrics for all three

### Agentic Loop

- [ ] **AGNT-01**: Agentic loop scope decided and documented before v1 (mvp.md Q1: pair-programmer / single-loop agent / multi-role)
- [ ] **AGNT-02**: At least one model improvement in the v0 → MVP-exit chain produced via the agentic loop, verifiably moving the validation metric

## v2 Requirements

Deferred to post-MVP (simplification-removal queue). Tracked but not in current roadmap.

### Realism Removal

- **REAL-01**: Taker fees (Binance VIP0 baseline)
- **REAL-02**: Larger position, bounded inventory, per-symbol cap
- **REAL-03**: Non-zero latency (data + order); tradeability filter; subsampling revisit
- **REAL-04**: L1-aware fills, maker-vs-taker decision
- **REAL-05**: Queue position modeling (hftbacktest adoption candidate)
- **REAL-06**: Market impact
- **REAL-07**: Funding-rate P&L

### Methodology Upgrades

- **METH-01**: CPCV as post-MVP robustness check alongside walk-forward
- **METH-02**: RL-based monetization (mvp.md Q4, post-v3)
- **METH-03**: Live calibration monitor with automatic demotion (when live exists)

### Platform Generalization (vision — earn via MVP first)

- **PLAT-01**: Venue adapter interface extracted from the Binance capture/ingest seam (second venue proves the abstraction; candidates: another crypto exchange, then equities/FX feeds)
- **PLAT-02**: Stock exchange / money-market data plane (sessioned markets: calendars, halts, auctions, corporate actions — new adapter concerns the 24/7 crypto beachhead doesn't exercise)
- **PLAT-03**: Domain-neutral target/label abstraction so Stage 1 can forecast non-price signals (e.g., news-landscape prediction) on the same fold harness, catalogues, and overfitting controls
- **PLAT-04**: Cross-domain reporting — same side-by-side model-class protocol applied per domain/venue

## Out of Scope

| Feature | Reason |
|---------|--------|
| Live trading | MVP is simulation-only; live comes after realism removals |
| pandas anywhere in the stack | polars is faster on these workloads; CI-enforced ban |
| Vectorized backtesting | Flip-only policy is path-dependent; structurally wrong |
| Linear capacity extrapolation from $100 positions | spec.md DON'T; capacity is its own post-MVP track |
| hftbacktest at MVP | Pays for latency/queue realism explicitly out of MVP scope; ~200-line numba kernel suffices |
| L2/L3 features at MVP | L1 + trades scope per mvp.md (L2 capture-only may start early as a cheap option — roadmap decision) |
| Time-based exits in policy | mvp.md: flip-only on opposite TOB-cross |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| SPEC-01 | Phase 2 | Pending |
| SPEC-02 | Phase 2 | Pending |
| SPEC-03 | Phase 2 | Pending |
| SPEC-04 | Phase 2 | Pending |
| DATA-01 | Phase 1 | In Progress (walking skeleton proven live in Plan 01; redundancy/gap-ledger/continuous-daemon in Plans 02-04) |
| DATA-02 | Phase 3 | Pending |
| DATA-03 | Phase 3 | Pending |
| DATA-04 | Phase 1 | Complete |
| DATA-05 | Phase 3 | Pending |
| DATA-06 | Phase 3 | Pending |
| DATA-07 | Phase 3 | Pending |
| DATA-08 | Phase 3 | Pending |
| FEAT-01 | Phase 4 | Pending |
| FEAT-02 | Phase 4 | Pending |
| FEAT-03 | Phase 4 | Pending |
| FEAT-04 | Phase 4 | Pending |
| FEAT-05 | Phase 4 | Pending |
| EVAL-01 | Phase 5 | Pending |
| EVAL-02 | Phase 5 | Pending |
| EVAL-03 | Phase 5 | Pending |
| EVAL-04 | Phase 5 | Pending |
| EVAL-05 | Phase 9 | Pending |
| EVAL-06 | Phase 8 | Pending |
| FCST-01 | Phase 7 | Pending |
| FCST-02 | Phase 8 | Pending |
| FCST-03 | Phase 8 | Pending |
| FCST-04 | Phase 7 | Pending |
| FCST-05 | Phase 8 | Pending |
| FCST-06 | Phase 8 | Pending |
| SIM-01 | Phase 6 | Pending |
| SIM-02 | Phase 6 | Pending |
| SIM-03 | Phase 6 | Pending |
| MON-01 | Phase 9 | Pending |
| MON-02 | Phase 9 | Pending |
| MON-03 | Phase 9 | Pending |
| TRACK-01 | Phase 2 | Pending |
| TRACK-02 | Phase 2 | Pending |
| GATE-01 | Phase 8 | Pending |
| GATE-02 | Phase 9 | Pending |
| GATE-03 | Phase 11 | Pending |
| GATE-04 | Phase 11 | Pending |
| GATE-05 | Phase 11 | Pending |
| AGNT-01 | Phase 8 | Pending |
| AGNT-02 | Phase 10 | Pending |

**Coverage:**
- v1 requirements: 44 total (header previously said 41 — corrected to actual checkbox count during roadmap creation)
- Mapped to phases: 44
- Unmapped: 0 ✓

---
*Requirements defined: 2026-06-10*
*Last updated: 2026-06-10 after roadmap creation (traceability populated)*
