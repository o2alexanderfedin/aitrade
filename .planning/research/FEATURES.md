# Feature Research

**Domain:** Short-horizon crypto futures ML trading research pipeline (Binance perp 10s midprice forecasting + threshold-policy monetization in simulation)
**Researched:** 2026-06-10
**Confidence:** HIGH (evaluation/overfitting practices grounded in established literature — Bailey & López de Prado; Binance data availability verified against data.binance.vision listings; microstructure feature efficacy verified against published research)

This document enumerates what serious quant-research / HFT-MFT ML pipelines contain, so the project's requirements (`mvp.md`, `spec.md`) can be checked for completeness. "Users" here = the researcher and the agentic loop; "product feels incomplete" = research results are untrustworthy.

## Feature Landscape

### Table Stakes (Results Are Untrustworthy Without These)

#### A. Data Capture & Quality

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Historical backfill from `data.binance.vision` | Free, official, the only way to get ~3 months of history without having captured it. Futures-UM daily/monthly `bookTicker` (L1) and `trades`/`aggTrades` confirmed available per symbol | LOW | Verified: directory listings exist for `futures/um/daily/bookTicker/BTCUSDT` and `aggTrades`. Daily files appear next day; monthly at month start. Spot side analogous. Checksum files provided — verify them |
| Live WebSocket capture (bookTicker + aggTrade streams) | Backfill alone has gaps/lag; capture extends history forward and is the path to eventual live parity | MEDIUM | Standard practice: track update IDs / sequence numbers, detect gaps, reconnect + tag resync events. spec.md already requires resync tagging + warm-up |
| Raw data archival (immutable, append-only) | Reprocessing with fixed bugs requires originals; derived data must always be regenerable from raw | LOW | Store raw alongside corrected (spec.md already does this for `tradeSide_raw` / `tradeSide_corrected`) |
| Data quality monitoring (daily DQ report) | Crossed/locked books, stale BBO, sequence gaps, sparse-trade windows, outage holes silently poison features and labels. Every serious shop gates training on DQ | MEDIUM | Per-day, per-symbol counts: crossed-book events, gap count/duration, dropped rows per sanity filter, trade/BBO rates vs. baseline. spec.md mandates "any degradation pauses training until acknowledged" — needs an actual report artifact + threshold config |
| Canonical storage: Parquet partitioned by symbol/date, schema-versioned | Reproducibility and fast columnar scans; partition pruning for fold construction | LOW | Already decided. Add explicit dataset version IDs (content hash or snapshot tag); CI rejects `latest` (spec.md DON'T) |
| Trade-side classification backfill (`tradeSide = 0` → nearest-quote rule) | Trade-flow features (signed volume, trade imbalance) are core predictors; unsigned trades cripple them | LOW | Quote-rule (closer to bid ⇒ sell) is the standard Lee–Ready-style approach when aggressor flag is missing. Keep a confidence channel — classification errors propagate into flow features |
| Spot+Swap clock audit | Mixing two feeds requires knowing whether `etime` semantics match (spec.md DON'T: don't mix without auditing) | LOW | One-time audit + documented per-dataset clock source; cheap, prevents a silent leakage class |

#### B. Feature / Label Engineering

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Feature catalogue with per-feature information set | The leakage-proofing backbone; every serious pipeline documents what each feature can legally see | LOW (process) | Already seeded in spec.md. The catalogue must be machine-checkable (CI cross-references code ↔ catalogue) |
| Label catalogue with horizon + embargo per label | Embargo ≥ horizon is the minimum anti-leakage rule for forward-return labels | LOW | Already seeded. Overlapping forward windows → report effective sample size |
| Core L1 microstructure features | These are the published, verified alpha sources at 1s–10min horizons | MEDIUM | Minimum set: mid, spread (abs + bps), **top-of-book imbalance** (research: OBI explains ~65% of short-interval price variance vs ~32% for trade imbalance alone), **order-flow imbalance (OFI**, Cont-style, computed from BBO size/price deltas — linear relationship with short-term price change is the canonical result**)**, signed trade-flow imbalance over multiple windows, trade intensity/arrival rate, rolling realized vol, return EMAs at several decay scales, time-since-last-trade / sparsity flag |
| Single feature-pipeline code path (train = inference = sim) | Production–research drift is a named pitfall; dual implementations always diverge | MEDIUM | Stateful accumulator design: state updates on every row, decision emission on last row per `etime`. The integration test "same input bytes → same feature values from both paths" is the enforcement |
| Rolling/expanding normalization computed on train only | Full-period normalization is a classic leakage vector | LOW | Already a spec.md DO |
| Stationary feature transforms | Raw prices are non-stationary; returns/imbalances/ratios are what works at these horizons | LOW | Mostly free if the core feature set above is used |

#### C. Evaluation Harness & Leakage Controls

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Walk-forward harness with embargoed sequential segments | Simulates the rolling-retrain production schedule; the project's 5-segment (Train_S1/Val_S1/Train_S2/Val_S2/Held-out) design is a correct two-stage instantiation | HIGH | The harness is the spine of the project — fold construction, embargo insertion, segment-size floors, 3-segment OOF fallback with purged+embargoed inner k-fold. Highest-leverage component to get right early |
| Leakage tests in CI | "Prove it per feature" beats "review it per PR". Shuffle-future-data invariance is cheap and catches most feature leaks | MEDIUM | Three test families: (1) future-shuffle invariance per feature, (2) embargo ≥ label horizon assertion from the catalogues, (3) transformer causal-mask test (perturb future tokens, assert outputs unchanged) |
| OOF prediction generation for Stage 2 | Stage 2 must train on realistic out-of-sample forecast quality — the #1 silent leakage path in two-stage pipelines | MEDIUM | Purged k-fold inner CV (López de Prado) when using the compressed 3-segment split |
| Zero/naive baselines, always reported | Every model class "must beat zero baseline" (v0 gate) needs the baseline implemented and in every report | LOW | Zero-return baseline for IC/R²; also report sign-of-last-trade and raw-imbalance naive signals — if ML can't beat a one-line heuristic, that's the finding |
| Statistical honesty layer | Millions of autocorrelated 10s rows ≠ millions of independent samples; naive t-stats overstate everything | MEDIUM | HAC/Newey–West SEs, block bootstrap CIs, effective sample size in every report (all already spec.md DOs — need a shared stats utility so every report uses the same implementation) |
| Multi-seed runs with variance reporting | Separates real improvements from seed noise; prerequisite for the "margin > noise" deployment rule | LOW | Mean ± std across ≥3 seeds per config |

#### D. Simulation & Monetization

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Event-driven simulator (numba hot path) | Threshold-policy P&L is path-dependent (position state, flip logic); vectorized backtests can't express it correctly | HIGH | Taker-only at TOB, zero fees/latency, $100 cap, flip-only — deliberately narrow. Replays the same decision-row stream as training. Must be fast enough for black-box X sweeps (hence numba) |
| Trade log output | Per-trade records (timestamp, side, price, size, position after, bps captured) are the audit trail for every P&L claim | LOW | Feeds hit rate, avg bps per round trip, turnover, adverse-selection diagnostics |
| Position & P&L accounting with invariant checks | Wrong P&L accounting is a silent killer; assertions (position ≤ cap, flip-only transitions, P&L reconciles with trade log) catch it | LOW | Cheap property tests over random forecast streams |
| Black-box threshold optimization (Optuna/CMA-ES) | X-bps sweep is the Stage-2 fit; needs reproducible search with logged trials | LOW | Coarse grid first; every trial logged to MLflow and counted against budget |

#### E. Experiment Tracking & Reproducibility

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| MLflow tracking with full run manifest | code hash + data hash + seed + env hash per run — the minimum for "which exact pipeline produced this number" | LOW | Already decided. Add: fold-split choice (5-seg vs 3-seg) + reason, model class, version gate, as run tags |
| Pinned, hashed environment | Env drift breaks reproducibility claims | LOW | Lockfile (uv/pip-tools) + env hash in manifest |
| Per-version frozen eval windows, committed | Version gates (v0–v3) need fixed eval windows so comparisons across versions mean something | LOW | Window definitions in config, committed with the gate |

#### F. Reporting

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Standard report template per version gate | Side-by-side three-class table (IC@10s, P&L, Sharpe, hit rate, turnover, drawdown), per-fold ± std, overlaid held-out equity curves, trade-log summary, one-page "what changed" | MEDIUM | mvp.md prescribes the contents. Make it generated-from-MLflow, not hand-assembled — the agentic loop needs machine-readable results |
| Regime-split metrics (high/low vol; trending/choppy) | Crypto regimes flip weekly–monthly; a strategy that only works in one regime within 3 months of data is probably noise | LOW | Regime labeler (rolling vol quantiles) + split aggregation in the report layer |

### Differentiators (Quality Multipliers — Most Shops Don't Have These)

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Selection-bias budget tracked in MLflow | Makes the multiple-testing problem visible and enforceable instead of folkloric; rare even in professional shops | MEDIUM | Every Val look = a logged event with a counter per validation segment; budget exhaustion forces a fresh window. Needs to be low-friction or it gets bypassed |
| Held-out lockbox with explicit access gating | One-look-per-gate is the strongest cheap defense against overfitting-by-iteration; gating access via an explicit "spending my one look" MLflow annotation makes violations auditable | LOW | Pure process + a small tooling wrapper around held-out data loading (e.g., loader refuses without an annotation token) |
| Negative-result log | Prevents re-exploring dead ends under new names — directly multiplies the value of a capped selection budget; essential for an agentic loop that lacks human memory | LOW | Structured records (config hash, metric, conclusion) queryable before launching new experiments |
| Deflated Sharpe Ratio / PBO (CSCV) reporting | Converts "best Sharpe found after N trials" into "Sharpe likely to be real given N trials"; literature shows walk-forward alone has the highest probability of backtest overfitting among CV schemes | MEDIUM | DSR needs the trial count (which the selection-budget tracker already provides — strong synergy). PBO via combinatorially symmetric CV is heavier; DSR first |
| Alpha-decay curve per signal | Tells you *now* which alpha will die when latency is added later — de-risks the entire simplification-removal queue | MEDIUM | IC as a function of evaluation lag (0ms, 50ms, 100ms, 500ms, 1s after decision row). spec.md already requires it in the standard report |
| Mid-vs-fillable-proxy IC gap | High mid-IC ≠ monetizable; tracking IC against a fillable price proxy (cross-the-spread fill) quantifies the forecast-vs-execution gap before realism is added | LOW | Second label in the catalogue + one more report column |
| Knife-edge rejection in threshold selection | Auto-rejecting X optima without a robust plateau (e.g., ≥90% of peak metric across a declared X-band) kills the most common Stage-2 overfit | LOW | A filter in the Optuna/sweep post-processing |
| Adverse-selection diagnostics in trade log | Immediate post-fill price-reversion histogram flags trades where being filled = being picked off; informs the maker/taker decision post-MVP | LOW | Computed from trade log + L1 replay |
| Calibration tracking (predicted vs realized return std) | Detects regime breaks and over-confident models before P&L does; the research-time version of the future live monitor | LOW | Per-fold and rolling calibration ratio in reports |
| Three-class comparison harness (same folds, same metrics, per-fold confidence bands) | The disciplined "deploy simplest class that meets the bar" rule needs apples-to-apples machinery; most pipelines compare models ad hoc | MEDIUM | Shared fold/feature/metric infrastructure with per-class adapters; comparison itself logged as a selection event |
| Agentic-loop integration surface | The secondary MVP gate. Machine-readable run results + spec.md as CI-enforced contract + negative-result log = what lets an agent propose changes grounded in evidence | MEDIUM | Concretely: MLflow query CLI/API for agents, structured experiment-proposal format, CI that blocks feature/label changes lacking catalogue updates |
| Universal cross-symbol model capability | ~N× training data; may rescue the 5-segment split from data starvation; v3 gate | HIGH | Symbol embedding/one-hot for trees, stacked features for regression, cross-asset attention for transformer |
| Feature computation caching keyed by (data hash, feature version) | Sweeps and agentic iteration re-run folds constantly; caching turns hours into minutes | MEDIUM | Content-addressed Parquet feature store (lightweight, not Feast) |

### Anti-Features (Deliberately NOT Building at MVP)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Realistic execution (fees, latency, queue position, market impact, maker orders) | "Backtest should be realistic" | Each realism dimension is its own research project; bundling them in destroys the controlled-simplification methodology and the v0–v3 comparability | The documented simplification-removal queue, one item per version gate. When queue/latency realism arrives, evaluate adopting **hftbacktest** (numba/Rust, L2/L3 replay, queue-position + latency models, Binance examples) instead of extending the in-house sim |
| Full L2/L3 order book reconstruction & depth features | "More book = more alpha" | L1 `bookTicker` + trades is what's backfillable from data.binance.vision for the needed history; depth capture has no 3-month history and triples data-engineering scope. OFI/imbalance from L1 already captures the majority of short-horizon explanatory power | L1-only at MVP; start *capturing* depth now (cheap) so history exists when depth features become a funded experiment |
| RL-based monetization | "Threshold policy is primitive" | RL on top of a noisy forecast in a zero-cost sim is an overfitting machine with a huge selection footprint; sample-inefficient; un-auditable policy | Threshold policy with swept X (open question Q4 defers RL past v3) |
| Live/paper trading connectors | "Get to real trading sooner" | MVP gate is simulation-only; live infra (keys, order management, monitoring) is a different engineering domain and a distraction before realism exists in the sim | Keep the single feature code path so live inference is a drop-in later |
| GUI dashboards / web frontends | "Easier to see results" | Pure overhead at MVP scale; the MLflow UI plus generated static reports cover it | MLflow UI + per-gate markdown/HTML reports committed to the repo |
| Multi-exchange data abstraction layer (cryptofeed-style) | "Future-proofing" | Binance-only is in scope; a generic layer adds schema indirection that obscures exactly the per-feed quirks (clock source, update semantics) the spec demands be audited | Binance-specific capture with documented semantics |
| Generic feature-store infrastructure (Feast etc.) | "Industry standard MLOps" | Heavyweight, online/offline-serving oriented; this project needs deterministic batch replay, not serving | Content-addressed Parquet cache (see differentiators) |
| Funding-rate P&L | "It's real P&L" | Horizon is 10s and holding periods are short; funding accrual is noise at MVP scale and adds accounting complexity | Post-MVP queue item 7 (already queued) |
| Large-scale AutoML / massive hyperparameter search | "Squeeze the models" | Directly burns the selection-bias budget; thousands of trials on 3 months of autocorrelated data manufactures false positives | Capped N-trial search per class (already specified); spend trials only after feature improvements plateau |
| Distributed training / orchestration infra (Ray, k8s, Airflow) | "Scalability" | Single-symbol L1 data at 3 months fits one machine; infra burns weeks of the 1–3 month budget | One box, CPU + one GPU; `make`/scripts for orchestration |
| Subsampling & tradeability filtering | "Standard HFT hygiene" | Already analyzed in spec.md: under zero latency every row is tradeable; premature filtering discards label density the small dataset needs | Deferred with explicit revisit triggers (Ljung–Box residual autocorrelation, metric instability) — already in spec.md |
| pandas-based tooling | Familiarity, library compatibility | Project constraint: polars+numpy+numba only; pandas dtype/index landmines and speed | Already excluded |
| CPCV as the primary eval scheme at MVP | Literature shows CPCV beats walk-forward on PBO/DSR | Combinatorial paths multiply compute k-fold-choose-2×; the two-stage sequential structure doesn't map cleanly onto CPCV paths; walk-forward also matches the production retrain schedule, which CPCV doesn't | Keep walk-forward as primary (it mirrors production); add DSR reporting now, consider CPCV as a post-MVP robustness check on Stage 1 alone |

## Feature Dependencies

```
data.binance.vision backfill ─┐
WebSocket capture ────────────┼──> Raw archival ──> DQ monitoring ──> Canonical Parquet store (versioned)
                              │                                            │
Trade-side backfill ──────────┘                                            │
                                                                           v
Feature catalogue + Label catalogue ──required by──> Feature pipeline (single code path)
        │                                                  │
        └──> Leakage tests in CI <─────────────────────────┤
                                                           v
                              Walk-forward harness (5-seg / 3-seg OOF fallback)
                                     │                      │
                                     v                      v
                          Stage 1 training (3 classes)   Simulator (numba) <── Position/P&L invariants
                                     │                      │
                          OOF / out-of-train forecasts ─────┤
                                     │                      v
                                     └────────> Stage 2 threshold fit (Optuna/CMA-ES)
                                                            │
MLflow manifest (code+data+seed+env hash) <── every run ────┤
        │                                                   v
        ├──> Selection-bias budget tracker          Trade log ──> Adverse-selection diagnostics
        ├──> Held-out lockbox gating                        │
        ├──> Negative-result log                            v
        └──> Report generator (per-fold ±std, equity curves, regime splits,
             DSR, alpha-decay, mid-vs-fill gap, effective sample size)
                          │
                          v
             Agentic-loop integration (MLflow query API + proposal format + spec CI)

DSR reporting ──requires──> Selection-bias budget (trial counts)
3-class comparison ──requires──> Walk-forward harness (same folds) + multi-seed runs
Feature cache ──enhances──> threshold sweeps + agentic iteration speed
hftbacktest adoption ──conflicts with──> MVP zero-latency taker-only sim (post-MVP only)
```

### Dependency Notes

- **Leakage CI requires the catalogues:** the tests are generated from catalogue entries (information set, horizon, embargo) — the catalogue must be machine-readable, not prose-only.
- **DSR requires the selection-budget tracker:** the deflated Sharpe formula needs the number and variance of trials; tracking looks in MLflow makes DSR computable for free. Build the tracker first.
- **Stage 2 requires OOF machinery:** in the 3-segment fallback, Stage 2 literally cannot be fit correctly without purged+embargoed inner k-fold OOF generation — it is a hard prerequisite, not a nice-to-have.
- **Simulator and Stage 1 share the decision-row stream:** both consume "last row per `etime`" emissions from the same feature pipeline; building them against separate data paths reintroduces production–research drift.
- **Agentic loop requires reporting + negative-result log + spec CI:** an agent without queryable results and recorded dead-ends will re-run failed experiments and burn the selection budget.
- **DQ monitoring gates everything downstream:** spec.md mandates training pauses on DQ degradation — so the report and thresholds must exist before v0, not after.

## MVP Definition

### Launch With (v0–v1 gates)

- [ ] Backfill + capture + trade-side correction + Parquet store + DQ report — no data, no project
- [ ] Feature/label catalogues wired into CI with leakage tests — Stage 0 prerequisite, explicitly required before any training
- [ ] Core L1 microstructure feature set (mid, spread, TOB imbalance, OFI, trade-flow imbalance, vol, EMAs, sparsity flags) via single code path — the verified alpha sources
- [ ] Walk-forward harness with embargo + 3-segment OOF fallback — the evaluation spine
- [ ] Numba event-driven simulator with trade log + P&L invariants — Stage 2 is unfittable without it
- [ ] Three-class training tracks + zero/naive baselines + multi-seed variance — v0 gate is "each class beats zero baseline"
- [ ] MLflow manifest (code/data/seed/env hash) + selection-budget tracker + held-out lockbox gating + negative-result log — overfitting controls are first-class, not retrofit
- [ ] Report generator: per-fold ±std, side-by-side classes, equity curves, regime splits, effective sample size, HAC stats — v1 gate needs the side-by-side comparison
- [ ] Threshold sweep with knife-edge rejection (Optuna) — v1 gate

### Add After Validation (v1.x–v3)

- [ ] DSR reporting — trigger: selection-budget tracker accumulating real trial counts (cheap once tracker exists)
- [ ] Alpha-decay curves + mid-vs-fill IC gap + adverse-selection diagnostics — trigger: first model class passing v1; informs Q2 (which simplification to remove first)
- [ ] Feature computation cache — trigger: sweep/iteration wall-clock pain
- [ ] Agentic-loop tooling (MLflow query surface, proposal format) — trigger: Q1 decision before v1; needed for the secondary MVP gate
- [ ] Multi-symbol then universal cross-symbol model — v2/v3 gates; also the escape hatch if single-symbol data starves the 5-segment split
- [ ] L2 depth *capture* (not features) — trigger: as soon as capture infra is stable; builds history for post-MVP

### Future Consideration (post-MVP)

- [ ] Realistic execution stack (fees → size → latency → fills → queue → impact → funding) — the documented removal queue; evaluate hftbacktest at the latency/queue step
- [ ] CPCV robustness check on Stage 1 — after walk-forward results exist to compare against
- [ ] RL monetization — Q4, post-v3 at earliest
- [ ] Live calibration monitors / paper trading — when live exists

## Feature Prioritization Matrix

| Feature | Research Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Walk-forward harness + OOF fallback | HIGH | HIGH | P1 |
| Data layer (backfill, capture, DQ, store) | HIGH | MEDIUM | P1 |
| Leakage CI from catalogues | HIGH | MEDIUM | P1 |
| Core microstructure features, single code path | HIGH | MEDIUM | P1 |
| Numba simulator + trade log | HIGH | HIGH | P1 |
| MLflow manifest + lockbox + budget + negative log | HIGH | MEDIUM | P1 |
| Report generator (per-fold, side-by-side, regime) | HIGH | MEDIUM | P1 |
| Threshold sweep + knife-edge rejection | HIGH | LOW | P1 |
| DSR reporting | HIGH | MEDIUM | P2 |
| Alpha-decay / mid-vs-fill / adverse-selection diagnostics | HIGH | MEDIUM | P2 |
| Agentic-loop integration surface | HIGH (secondary gate) | MEDIUM | P2 |
| Feature cache | MEDIUM | MEDIUM | P2 |
| Universal cross-symbol model | MEDIUM | HIGH | P2/P3 (v3) |
| L2 depth capture | MEDIUM | LOW | P2 |
| CPCV, RL, realistic execution, live connectors | — | HIGH | P3 (post-MVP) |

## Comparable-System Feature Analysis

| Capability | hftbacktest | mlfinlab / López de Prado toolkit | Microsoft Qlib | This project's approach |
|---------|--------------|--------------|--------------|--------------|
| Execution realism | Queue position, feed+order latency, L2/L3 replay, numba/Rust | None (not a simulator) | Coarse daily/minute simulation | Deliberately minimal taker-at-TOB sim at MVP; hftbacktest is the candidate when realism arrives |
| Anti-leakage CV | None | Purged k-fold, embargo, CPCV — the reference implementations | Rolling/walk-forward workflows | 5-segment two-stage walk-forward + purged OOF fallback (stricter than any off-the-shelf scheme because of the two-stage structure) |
| Overfitting metrics | None | DSR, PBO/CSCV | Limited | Selection-bias budget + lockbox + negative-result log (process), DSR (P2 metric) |
| Experiment management | None | None | Built-in recorder/workflow | MLflow with full manifest + budget tracking layered on top |
| Feature/label governance | None | Labeling utilities (triple-barrier etc.) | Alpha library (daily-frequency oriented) | CI-enforced catalogues with information-set proofs — beyond all three |

Takeaway: no off-the-shelf system covers this project's combination (two-stage anti-leakage walk-forward + tick-level sim + governance). The composition is custom, but each pattern has reference implementations worth consulting rather than re-deriving (purged CV from López de Prado; fill/latency models from hftbacktest later).

## Sources

- [hftbacktest (GitHub)](https://github.com/nkaz001/hftbacktest) — HFT backtesting features: queue position, latency models, L2/L3, numba; Binance examples — HIGH confidence
- [hftbacktest docs](https://hftbacktest.readthedocs.io/) — HIGH
- [Order Flow Imbalance in Market Microstructure (EmergentMind survey)](https://www.emergentmind.com/topics/order-flow-imbalance) and [Order Book Imbalance in High-Frequency Markets](https://www.emergentmind.com/topics/order-book-imbalance-obi) — OBI explains ~65% of short-interval price variance; trade imbalance alone ~32%; OFI–price linear relationship (Cont et al.) — MEDIUM-HIGH (survey of published papers)
- [Order Book Filtration and Directional Signal Extraction at High Frequency (arXiv 2507.22712)](https://arxiv.org/html/2507.22712v1) — regularized linear models with multi-level features dominate short-horizon OOS prediction — MEDIUM
- [Bailey & López de Prado — The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) — PBO/CSCV definition — HIGH
- [Backtest Overfitting in the ML Era (SSRN 4686376)](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID4686376_code4361537.pdf?abstractid=4686376&mirid=1) — CPCV outperforms walk-forward and k-fold on PBO and DSR in controlled synthetic tests — MEDIUM-HIGH
- [Purged cross-validation (Wikipedia)](https://en.wikipedia.org/wiki/Purged_cross-validation) — HIGH for definitions
- [binance-public-data (GitHub)](https://github.com/binance/binance-public-data) and [data.binance.vision futures-um listings](https://data.binance.vision/?prefix=data%2Ffutures%2Fum%2Fdaily%2FbookTicker%2FBTCUSDT%2F) — futures bookTicker + trades/aggTrades daily/monthly availability — HIGH (verified directory listings)
- [Tick Data vs Order Book Snapshots (CoinAPI)](https://www.coinapi.io/blog/tick-data-vs-order-book-snapshots-complete-guide-crypto-trading) — sequence-gap detection, snapshot-resync capture practices — MEDIUM
- Project documents: `mvp.md`, `spec.md`, `.planning/PROJECT.md` — primary scope sources

---
*Feature research for: short-horizon crypto futures ML trading research pipelines*
*Researched: 2026-06-10*
