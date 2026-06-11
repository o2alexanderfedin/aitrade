# Project Research Summary

**Project:** AiHedgeFund — BinanceSwap Forecast + Monetization MVP
**Domain:** Crypto HFT/MFT market-microstructure ML (Binance perpetual futures, two-stage forecast → simulated monetization)
**Researched:** 2026-06-10
**Confidence:** HIGH

## Executive Summary

This is a research-grade quant ML pipeline, not a product with users: a batch DAG of pure stages over immutable, manifest-addressed Parquet artifacts, with exactly one long-running component (the websocket capture daemon). Experts building systems like this converge on two non-negotiables that dominate every other design choice: (1) a **single feature code path** shared by training, inference, and simulation (a numba streaming kernel — state updates on every row, decision emission on the last row per `etime`), and (2) a **fold harness that owns time** — segment manifests, embargo, selection-bias ledger, and lockbox gating as first-class components, not utilities. The mandated stack (polars + numpy + numba, PyTorch/LightGBM/scikit-learn, MLflow) is verified current and compatible, with one load-bearing pin: numba 0.65.1 requires numpy < 2.5.

The single most consequential research finding changes the schedule: **the backfill plan is partially dead on arrival.** Verified directly against data.binance.vision (2026-06-10): futures `bookTicker` (L1) archives froze around 2024-03, and spot L1 archives never existed. Only trades/aggTrades/klines are backfillable for the current period. Every day of usable L1 history equals a day of own capture — so the capture daemon is the schedule-defining, Phase-1 deliverable, started before anything else, built as a reliability-engineered service (redundant connections, gap ledger) because L1 capture gaps are permanently unrecoverable. A human decision is required early: buy historical L1 from Tardis.dev (~3 months immediately) versus wait for capture to accrue (pushes MVP exit right by the same duration) versus accept a two-regime dataset (trades-only features over backfill, L1+trades over capture, with the seam tagged and reported).

The other top risks are methodological and mechanical, and all have cheap, known preventions: the spot bookTicker websocket carries **no exchange timestamp at all** (the "etime is the only clock" convention needs a documented per-stream exception or the spot depth/SBE streams); the mvp.md decision-rule pseudocode has a **dimensional bug** (`pred_return * mid` compared against a price level) that nothing in the current test plan would catch — fix the spec now and gate v0 on oracle/fixture tests; and the held-out **lockbox must be mechanically enforced at the loader level** (separate path, unlock-token API, logged access) because one of the operators is an autonomous agent that will enthusiastically load all available data. The project's existing spec.md pitfalls catalogue matches industry consensus; research adds operational detail (timestamp-unit heterogeneity, `(etime, seq)` total ordering, numba determinism traps, MLflow backend scaling) rather than corrections.

## Key Findings

### Recommended Stack

All mandated technologies are current and mutually compatible on Python 3.13 (full detail and pins in STACK.md). The fragile point is the numba ↔ numpy ↔ llvmlite triple pin — manage with a uv lockfile and never let numpy drift to 2.5+. pyarrow is deliberately omitted (polars' native Parquet writer covers partitioned ns-timestamp writes). pandas is banned mechanically via a ruff CI rule, including transitive pulls (skfolio, cryptofeed).

**Core technologies:**
- Python 3.13 + uv 0.11.x — runtime + lockfile; the numba pin makes a real lockfile non-negotiable
- polars 1.41.2 / numpy 2.4.x / numba 0.65.1 — dataframes, math, sim hot path; pass raw int64-ns numpy arrays into `@njit` kernels, never datetime types
- PyTorch 2.12 / LightGBM 4.6 / scikit-learn 1.9 — the three parallel model tracks
- MLflow 3.13 with **SQLite backend from day 1** (never the file store — it degrades at the run volume this project generates)
- Raw `websockets` 16 + orjson for capture (cryptofeed rejected: float-second timestamps break the ns convention); aiohttp for backfill
- Optuna 4.9 (+cmaes, +MLflowCallback) — one HPO framework for capped Stage-1 searches and the Stage-2 X sweep, every trial a countable MLflow row
- In-house purged+embargoed CV (~150 LOC) — no library implements the 5-segment two-stage split; skfolio is pandas-based, mlfinlab paywalled; property-test it with hypothesis

### Expected Features

Full landscape in FEATURES.md. "Users" = the researcher and the agentic loop; "incomplete" = untrustworthy results.

**Must have (table stakes — v0/v1):**
- Data layer: capture + trades backfill + trade-side correction + DQ report + versioned Parquet store — no data, no project
- Machine-readable feature/label catalogues wired into CI leakage tests (future-shuffle invariance, embargo ≥ horizon, transformer causal-mask test)
- Core L1 microstructure features via the single code path — TOB imbalance and Cont-style OFI are the published alpha sources (OBI explains ~65% of short-interval price variance)
- Walk-forward harness with embargo + 3-segment OOF fallback — the evaluation spine, highest-leverage component
- Numba event-driven simulator + trade log + P&L invariants; threshold sweep with knife-edge rejection
- Zero/naive baselines, multi-seed variance, HAC/Newey–West stats, effective sample size in every report
- MLflow manifest (code/data/seed/env hash) + selection-budget tracker + lockbox gating + negative-result log

**Should have (differentiators, post-v1):**
- Deflated Sharpe Ratio at gates — nearly free once the budget tracker counts trials; makes the budget mechanically meaningful
- Alpha-decay curves, mid-vs-fillable-IC gap, adverse-selection diagnostics — de-risk the simplification-removal queue
- Agentic-loop integration surface (MLflow query API, proposal format, spec CI) — the secondary MVP gate
- Feature computation cache; L2 depth *capture* (cheap now, builds history for post-MVP features)

**Defer (anti-features):**
- Execution realism (fees/latency/queue/impact) — the documented removal queue; evaluate hftbacktest at the latency step, don't adopt it at MVP
- RL monetization, live connectors, GUI dashboards, multi-exchange abstraction, Feast-style feature stores, CPCV as primary scheme, distributed infra

### Architecture Approach

Batch DAG of idempotent stages over immutable Parquet (detail in ARCHITECTURE.md), everything under `mvp/`. Five patterns carry the design: (1) single numba streaming feature kernel shared by train/inference/sim; (2) frozen predictors with **precomputed prediction tables** — the X sweep re-scans a fixed array, so a 50ms-inference transformer costs nothing during sweeps; (3) fold harness as data contract — segment manifests of `[start_etime, end_etime)` ranges that every artifact references; (4) manifest-addressed immutable datasets (no `latest`, ever); (5) simulator as a pure function with a ~200-line numba core (path-dependent flip-only logic cannot be vectorized).

**Major components:**
1. Capture daemon + backfill downloader → raw event store (the only "live" component; schedule-critical)
2. Preprocessing (trade-side backfill, quality filters, resync tags) → curated store
3. Feature/label engine — the ONLY place feature math lives → decision-row matrix
4. Fold harness — owns all splitting, embargo, ledger, lockbox gate
5. Stage 1 trainers (3 tracks behind one Trainer protocol) → frozen predictors → prediction tables
6. Simulator + policy optimizer (Optuna X sweep) — `forecast/` and `monetization/` never import each other; prediction tables are the anti-leakage boundary
7. Eval/reporting + MLflow wrapper + CI leakage suite (cross-cutting)

### Critical Pitfalls

Top 5 of 15 (full catalogue with phase mapping in PITFALLS.md):

1. **No public L1 history exists** (futures bookTicker frozen ~2024-03; spot L1 never published) — start capture day 1 with redundant connections + gap ledger; force the Tardis.dev buy-vs-wait decision explicitly in Phase 1
2. **Spot bookTicker websocket has no timestamp** — "etime only clock" is unsatisfiable for spot L1 as-is; choose deliberately (depth@100ms stream, SBE streams, or documented local-clock exception) and CI-enforce non-null etime with documented provenance per stream
3. **"Last row per etime" is undefined without a materialized sequence column** — polars group ordering is execution-plan-dependent; capture must write a monotonic `seq`, decision rule becomes `argmax(etime, seq)`, with a shuffle-invariance CI test
4. **The mvp.md decision rule has a dimensional bug** (`pred_10s_return * mid > best_ask + X` compares a price *change* to a price *level* — never trades) — fix spec.md in Stage 0; one typed bps/price conversion module; oracle test (feed realized future returns as predictions → near-ceiling Sharpe) gates v0
5. **Honor-system lockbox + autonomous agent = silent contamination** — mechanical quarantine: lockbox rows in a separate path the default loader physically cannot return without a logged `unlock_token`; agent sandbox doesn't mount the path

Also load-bearing: archive timestamp-unit heterogeneity (spot switched ms→µs on 2025-01-01; futures CSVs have headers, spot doesn't — unit registry + CI plausibility gate); aggTrades vs trades tape differences and the `m`-flag sign trap; numba foot-guns (globals frozen at compile time, silent int64 overflow, fastmath non-determinism — integer-tick P&L accounting + bit-identical double-run CI test); Sharpe>5 gate needs a pre-declared annualization convention (365-day, fixed-Δt mark-to-market, CI bands) before any held-out look.

## Implications for Roadmap

Governing principles from research: (a) **capture daemon first — it is the schedule**, everything else builds on fixtures while history accrues; (b) thinnest end-to-end vertical slice (v0) early, then deepen tracks in parallel; (c) overfitting controls (ledger, lockbox, manifest) must exist *before* the first training run, not retrofit.

### Phase 1: Stage 0 + Capture Daemon (schedule-critical)
**Rationale:** Every day without capture is a day less L1 history at MVP exit (Pitfall 1); spec.md must be seeded and the policy-math bug fixed before code transcribes it; MLflow backend + tag schema must precede the first run.
**Delivers:** Running, redundant capture daemon (futures + spot, bookTicker + trades, `seq` column, gap ledger, liveness watchdog); repo skeleton under `mvp/`; spec.md seeded with corrected decision-rule pseudocode, per-stream etime definition, Sharpe annualization convention; CI harness with ruff pandas-ban; MLflow SQLite backend + tag schema (budget/lockbox/fold tags); uv lockfile.
**Addresses:** Live capture, raw archival, experiment-tracking foundation (FEATURES A, E).
**Avoids:** Pitfalls 1, 2, 4 (capture schema), 10 (spec fix), 11 (convention), 13 (backend).
**Human decision forced here:** Tardis.dev L1 history buy vs wait-for-capture vs two-regime dataset.

### Phase 2: Data Layer (backfill, ingest, quarantine)
**Rationale:** Everything downstream consumes the canonical store; the loader contract and lockbox quarantine must exist before any training data is touched.
**Delivers:** Backfill downloader (trades/aggTrades, checksum-verified) with per-dataset unit/header registry + CI timestamp-plausibility gate; trade-side backfill with `m`-flag fixture tests and cross-validation metric; merged-stream ordering rule (`T`-preferred etime, `(etime, stream_priority, seq)` tie-break) as a data-prep artifact; manifest-addressed store (no `latest`); daily DQ report; exchangeInfo daily snapshots; source tags (backfill|capture); **lockbox loader quarantine with unlock-token**.
**Uses:** aiohttp, polars hive-partitioned Parquet, zstandard.
**Avoids:** Pitfalls 3, 5, 6, 8, 14, 15.

### Phase 3: Feature/Label Engine + Fold Harness
**Rationale:** The single-code-path kernel and the harness are the two spine components; both are testable on synthetic fixtures — no need to wait for data volume.
**Delivers:** Numba streaming kernel (state every row, emit last-row-per-etime) producing the decision-row matrix; machine-readable feature/label catalogues; CI leakage suite (future-shuffle invariance, embargo ≥ horizon, decision-row shuffle-invariance, synthetic-leak test, train/infer byte-parity); fold splitter with time-unit-only embargo, segment manifests, selection-bias ledger; partition-spanning loader contract (lookback/horizon padding, midnight completeness check).
**Implements:** Patterns 1 and 3 (ARCHITECTURE.md).
**Avoids:** Pitfalls 4 (verification), 12; anti-patterns 1, 4, 5.

### Phase 4: Simulator + Regression Track + v0 Gate
**Rationale:** Sim and the cheapest model class wire the full vertical slice; the oracle test validates policy plumbing before any real model exists and bounds the achievable ceiling.
**Delivers:** Numba flip-only simulator (integer-tick accounting, notional-based turnover, trade log, P&L invariants, bit-identical double-run CI test, pure-Python reference parity); typed bps/price conversion module + policy fixtures + oracle test; Trainer protocol + frozen-predictor interface + prediction tables; zero/naive baselines; v0 smoke pipeline end-to-end with fixed X; **held-out window locked here**; v0 report.
**Uses:** numba (no globals, no fastmath on hot path), scikit-learn, MLflow manifest.
**Avoids:** Pitfalls 9, 10 (verification), 15 (notional accounting); anti-patterns 2, 3.

### Phase 5: Trees + Transformer Tracks + OOF Fallback
**Rationale:** Trees are days of work once the protocol exists; the transformer is the long pole (GPU, slow iteration) — start scaffolding during Phase 4. OOF machinery is a hard prerequisite for Stage 2 under the 3-segment fallback.
**Delivers:** LightGBM and PyTorch tracks on the same folds/features/metrics; causal-mask CI test; purged+embargoed inner k-fold OOF module; multi-seed variance reporting; capped HP search via Optuna with MLflowCallback (every trial counted against the budget).
**Blocked by:** Q3 (GPU spec) — must resolve before transformer training.

### Phase 6: Stage 2 Sweep + Full Eval Suite (v1 gate)
**Rationale:** The X sweep needs prediction tables and the sim; the report layer needs real runs to aggregate. v1 gate = threshold sweep with side-by-side three-class comparison.
**Delivers:** Optuna X sweep (coarse grid → refine) with knife-edge/robust-plateau rejection; Stage-2 fit strictly on out-of-Stage-1-train predictions (enforced by artifact absence); full report generator (per-fold ±std, equity overlays, regime splits incl. tick-regime, effective sample size, HAC stats, DSR at gates, alpha-decay, mid-vs-fill gap, adverse-selection diagnostics).
**Avoids:** Pitfalls 8 (seam-aware splits), 11 (gate with CI bands + DSR); knife-edge overfit.

### Phase 7: Agentic Loop + Multi-Symbol (v2/v3)
**Rationale:** The agentic loop (secondary MVP gate) requires queryable results, the negative-result log, and the mechanical lockbox — all in place by now. Multi-symbol is mostly config if earlier phases were built symbol-parameterized.
**Delivers:** MLflow query surface for agents, structured experiment-proposal format, agent sandbox excluding lockbox path, negative-result log integration; at least one verifiable agent-produced metric improvement; multi-symbol (v2) then universal cross-symbol model (v3) as data allows.

### Phase Ordering Rationale

- **Capture-first is forced by data availability** (verified, not assumed): L1 history only accrues in real time; Phases 2–4 build on fixtures while it accumulates.
- **Controls before training:** ledger, lockbox quarantine, MLflow schema, and the held-out lock all precede the first model — retrofitting any of them invalidates results already produced.
- **Sim parallel with features, before models:** the oracle test decouples sim validation from model existence and catches the policy dimensional bug class at v0, not v1.
- **Cheapest model class wires the interfaces:** regression exposes Trainer/predictor/prediction-table design flaws at minimum cost before the GPU track lands on them.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 1 (capture):** Tardis.dev coverage/pricing verification (flagged MEDIUM in STACK.md); spot etime alternative (depth@100ms vs SBE streams) needs a concrete spike
- **Phase 5 (transformer):** Q3 GPU spec + context-construction cost gating; tokenization/subsampling for tick streams is genuinely open
- **Phase 7 (agentic loop):** Q1 scope decision; proposal-format and sandbox design has no established pattern to copy

Phases with standard patterns (skip research-phase):
- **Phase 2 (backfill/ingest):** plain HTTPS zips + checksums, fully documented; pitfalls already enumerated
- **Phase 4 (sim + regression):** ~200-line numba kernel with hftbacktest as design reference; sklearn track is routine
- **Phase 6 (sweep/eval):** Optuna + López de Prado/Bailey methods are reference-implemented literature

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions/pins verified against PyPI 2026-06-10; data availability verified against live S3 + official docs |
| Features | HIGH | Grounded in established literature (Bailey & López de Prado) + verified data listings; microstructure feature efficacy from published research (MEDIUM-HIGH on exact variance-explained figures) |
| Architecture | HIGH | Established quant-research patterns, validated against hftbacktest and CV literature; MEDIUM only on backfill specifics, which PITFALLS.md resolved empirically |
| Pitfalls | HIGH | Binance findings verified empirically (downloads, S3 listings, official docs); stack traps from official docs + GitHub issues; methodology MEDIUM-HIGH from literature |

**Overall confidence:** HIGH

### Gaps to Address

- **Tardis.dev coverage/pricing not verified this session** — verify during Phase 1 planning; it is the input to the buy-vs-wait human decision
- **GPU spec (Q3)** — unresolved mvp.md question; blocks transformer track (Phase 5) and the CUDA wheel choice; needed before v0 per mvp.md
- **Spot L1 etime strategy** — three viable options identified (depth stream, SBE, local-clock exception); decide and write into spec.md during Phase 1/2, with CI enforcement
- **2nd-tier symbol choice (Q5)** — check tick-size/filter announcement history of candidates first (small caps get re-ticked more often)
- **cryptofeed rejection rationale** based on training knowledge (MEDIUM) — irrelevant unless someone re-proposes it; re-verify then
- **Held-out window length floor** — research flags 2-week held-out Sharpe as statistically fragile; pre-declare the floor in spec.md before v0

## Sources

### Primary (HIGH confidence)
- data.binance.vision S3 listings + HTTP HEAD probes + file downloads (2026-06-10) — L1 backfill freeze, spot L1 absence, unit/header heterogeneity
- PyPI JSON API (2026-06-10) — all version pins and `requires_dist` constraints
- Binance official docs (spot/futures websocket streams, local-order-book management, binance-public-data README) — payload fields, timestamp semantics, `pu`-chain rule
- Context7 `/pola-rs/polars` — partitioned Parquet write semantics
- hftbacktest (GitHub + docs) — sim architecture reference
- Bailey & López de Prado — PBO/DSR; purged/embargoed CV literature
- Project documents: `mvp.md`, `spec.md`, `.planning/PROJECT.md`

### Secondary (MEDIUM confidence)
- dev.binance.vision community threads — bookTicker discontinuation, spot timestamp absence, archive unit inconsistency (corroborated empirically)
- numba/polars/MLflow GitHub issues — determinism, ordering, file-store scaling traps
- EmergentMind microstructure surveys + arXiv 2507.22712 — OBI/OFI explanatory power
- SSRN 4686376 — CPCV vs walk-forward PBO/DSR comparison

### Tertiary (LOW confidence)
- Tardis.dev coverage/pricing claims — ecosystem knowledge, verify before purchase decision

---
*Research completed: 2026-06-10*
*Ready for roadmap: yes*
