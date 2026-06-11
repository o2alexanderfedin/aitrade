# AiHedgeFund — BinanceSwap Forecast + Monetization MVP

## What This Is

A two-stage ML trading system for Binance perpetual futures (swap): Stage 1 forecasts the 10-second midprice return from L1 + trade data; Stage 2 fits a TOB-cross threshold monetization policy on those forecasts in simulation. Built under heavy, explicitly-listed simplifications (zero latency, zero fees, $100 max position) with a documented queue for removing them one by one toward a real-life tradeable model. Equally important deliverable: validation of the **agentic-iteration workflow** — Claude + ML models iterating on the system, with at least one metric-moving improvement produced by the agentic loop.

Source documents: `mvp.md` (MVP definition) and `spec.md` (living spec — wins on conflict).

## Core Value

A reproducible, leakage-proof two-stage pipeline that achieves Net P&L > 0 and annualized Sharpe > 5 on a locked held-out walk-forward window under stated simplifications — produced by a workflow where agentic iteration verifiably improves the model.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Stage 0: living spec (`spec.md`) in seed form with feature/label catalogue wired into CI before any model training
- [ ] Data layer: Binance Spot + Swap L1 + trades, ~3 months history (capture + `data.binance.vision` backfill), Parquet partitioned by symbol/date, nanosecond `etime` as the only clock
- [ ] Trade-side backfill preprocessing for legacy `tradeSide = 0` rows (nearest-quote classification, stored alongside raw)
- [ ] Feature pipeline: state accumulates over every row; decisions emit only on last row of each `etime`; single code path shared by training, inference, and simulator
- [ ] Stage 1 forecast: 10s midprice return predictor in three parallel model classes (regression / LightGBM trees / PyTorch transformer), same folds, same metrics
- [ ] Stage 2 monetization: TOB-cross threshold policy (X bps swept via black-box optimization) fit on out-of-Stage-1-train forecasts
- [ ] Event-driven simulator (numba hot path): taker-only, zero fees, zero latency, $100 max position, flip-only position logic
- [ ] Walk-forward evaluation: 5-segment fold split (Train_S1 | Val_S1 | Train_S2 | Val_S2 | Held-out) with embargo; compressed 3-segment OOF fallback when data-starved
- [ ] Over-fitting controls: selection-bias budget tracking, held-out lockbox (one look per gate), negative-result log, capped hyperparameter search per class
- [ ] MLflow tracking: code hash + data hash + seed + env hash per run
- [ ] Version gates v0 (smoke test) → v1 (threshold sweep) → v2 (multi-symbol) → v3 (universal model), each with side-by-side three-class reporting
- [ ] Agentic loop: at least one model improvement in the v0 → MVP-exit chain produced via the agentic loop and verifiably moving the validation metric

### Out of Scope

- Realistic execution (fees, latency, queue position, market impact, maker orders) — explicitly deferred; the simplification-removal queue is post-MVP, each removal its own version gate
- RL-based monetization — open question (Q4), deferred past MVP; threshold policy first
- Funding-rate P&L — post-MVP queue item 7
- Capacity / size extrapolation — $100 sim results must never be extrapolated linearly (spec DON'T)
- pandas — `polars` + `numpy` + `numba` only; faster on these workloads, avoids index/dtype landmines
- Live trading — MVP is simulation-only; live calibration monitors come when live exists

## Context

- **Domain**: HFT/MFT crypto market microstructure ML. Over-fitting and temporal leakage are the dominant risks, treated as first-class design concerns (see `spec.md` pitfalls catalogue: temporal leakage, sample-size illusion, label engineering, selection bias, forecast-vs-execution gap, production–research drift).
- **Source-of-truth hierarchy**: `spec.md` > `mvp.md`; conflicts resolved by updating `mvp.md`. The spec is live-changing — every leakage audit, post-mortem, or surprise updates it the same day.
- **Validation methodology is prescriptive**: 5-segment sequential folds with embargo gaps; held-out window locked at v0, rotated only at project milestones; compressed 3-segment split with purged+embargoed inner k-fold OOF generation as the data-starved fallback (choice recorded in MLflow per run).
- **Model-class portfolio**: regression (~1–10 µs), trees (~50 µs), transformer (~50 ms) maintained as parallel R&D tracks; production deploys the simplest class that meets the bar; ranking is not stable across realism levels so the comparison re-runs at every realism step.
- **Symbol scope is data-driven**: BTCUSDT perp default, 2nd-tier symbol as a parallel candidate (less crowded); pivot to a universal cross-symbol model if single-symbol data is insufficient.
- **Open questions from mvp.md**: Q1 agentic loop scope (decide before v1), Q2 first simplification to remove (decide at MVP exit), Q3 GPU spec & training budget (needed before v0), Q4 RL introduction (post-v3), Q5 2nd-tier symbol choice.

## Constraints

- **Tech stack**: Python with `polars` + `numpy` + `numba` (sim hot path); PyTorch (transformer), LightGBM (trees), scikit-learn (regression); MLflow tracking; **no pandas** — performance and dtype-safety on these workloads
- **Code location**: everything MVP-related lives under the MVP directory (`mvp/` in this repo, per the strict containment rule in `mvp.md`); nothing MVP-related outside it
- **Timeline**: 1–3 months to MVP exit
- **Compute**: CPU + GPU; regression/trees on CPU, transformer needs GPU from v0 (Q3: specific GPU spec TBD before v0)
- **Time convention**: nanoseconds everywhere as int64 since Unix epoch; exchange time `etime` is the only clock; UTC ISO 8601 with ns precision for human rendering
- **Anti-leakage**: Stage 2 trains only on out-of-Stage-1-train predictions; embargo ≥ label horizon; per-feature information-set proofs in CI
- **Process**: no model training before Stage 0 (`spec.md` seeded + feature/label catalogue wired into CI); PRs touching features/labels must update `spec.md`

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Two sequential stages (forecast → monetization), Stage 2 fit only after Stage 1 locked | Isolates forecast skill from policy fit; prevents the most common silent leakage path | — Pending |
| Three model classes as parallel R&D tracks, no early commitment | Ranking unstable across realism levels; locking in early discards information | — Pending |
| Deploy simplest class that meets the bar | Inference latency matters once zero-latency removed; complexity must beat noise on multiple folds | — Pending |
| polars + numpy + numba, no pandas | Faster on these workloads; avoids index/dtype landmines | — Pending |
| No subsampling, no tradeability filter at MVP | Justified by zero-latency assumption; revisit triggers documented in spec.md | — Pending |
| Sharpe > 5 MVP gate | Achievable only because of zero-cost/zero-latency/small-size; bar for "simplified setup works", not real-world | — Pending |
| 5-segment fold split with 3-segment OOF fallback | Anti-leakage by construction; fallback handles 3-month data starvation | — Pending |
| `mvp/` at repo root as the MVP containment directory | mvp.md says `aitrade/mvp/`; this repo (AiHedgeFund) is the project root, so `mvp/` here is the equivalent — mvp.md/spec.md move inside during Stage 0 | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-10 after initialization*
