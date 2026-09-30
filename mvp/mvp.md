# MVP — BinanceSwap Forecast + Monetization (1–3 months)

## Goal

Build the **first predictive + monetization model** for BinanceSwap futures under heavy simplifications, *and* validate the **agentic-iteration workflow** (Claude + transformers iterating on the model). Profit under simplifications is easy; the deliverable is the loop that produced it.

## System

Two AI-optimized models fit **sequentially**:

1. **Stage 1 — Forecast.** ML predictor for **10s midprice return**. **Three model classes maintained as parallel R&D tracks**: regression, gradient-boosted trees, transformer. Trained first. For production at any given realism level, deploy the simplest class that meets the bar; re-evaluate when simplifications are removed — ranking is not stable across realism levels.
2. **Stage 2 — Monetization.** TOB-cross threshold policy fit *on Stage 1 forecasts* in simulation, hyperparameters tuned by black-box optimization. Fit only after Stage 1 is locked. RL is an open question.

Both stages are designed for **rolling windows with periodic re-training**; walk-forward in eval simulates this schedule.

## Setup

- **Location**: everything lives under `aitrade/mvp/` — code, configs, notebooks, this doc, [`spec.md`](spec.md). Strict: nothing MVP-related sits outside this directory. (Inside `mvp/`, organize freely: `data/`, `features/`, `forecast/`, `monetization/`, `sim/`, `eval/`, `tests/`, etc.)
- Compute: CPU + GPU (specific spec TBD).
- Tracking: MLflow.
- Python stack: `polars` + `numpy` + `numba` (sim hot path). **No `pandas`** — `polars` is faster on these workloads. Per-track additions: PyTorch (transformer), LightGBM (trees, default), scikit-learn (regression). Detail in [`spec.md`](spec.md).

## Assumptions (all simplifications, listed for later removal)

### Data

- Binance Spot + Swap L1 + trades.
- ~3 months of history (capture + `data.binance.vision` backfill where available).
- Exchange time `etime` is the only clock.
- Zero latency for both market data and orders.
- **Trade-side backfill** for legacy data where `tradeSide = 0`: classify by nearest L1 quote at trade time — closer to bid ⇒ sell (`-1`), closer to ask ⇒ buy (`+1`), tie ⇒ unknown (`0`). Applied as an offline preprocessing step; corrected side stored alongside raw.
- **Time alignment**: feature state accumulates over **every** incoming row (no rows skipped during ingest). **Training labels, inference, and simulator decisions** are emitted only on the **last row of each `etime`** — a proxy for "decide at end of received packet, not in the middle". Detail in [`spec.md`](spec.md).
- **Subsampling & tradeability filter**: MVP default is **no subsampling, no tradeability filter** (justified by the zero-latency assumption — every row is tradeable by definition). Both are revisited when latency is introduced. Detail in [`spec.md`](spec.md).

### Forecast

- **Symbol scope (data-driven)**: try single-symbol first. Default starting symbol is BTCUSDT perp, but **2nd-tier symbols may be easier to forecast** (less crowded, more behavioral inefficiency) — pick one as a parallel candidate and compare. If single-symbol data is insufficient for stable training (possible at 3 months of history), pivot to a **universal model trained jointly on all listed perpetuals**. If single-symbol works, extend to other symbols; universal becomes a later upgrade.
- Single target: 10s midprice return.
- Zero-latency assumption ⇒ fast non-tradeable alpha realization is *not* a concern at MVP. (Becomes a concern when latency is added.)

### Monetization

- Zero exchange fees.
- Taker-only.
- **Entry**: when predicted midprice (10s horizon) crosses TOB by X bps. X is a swept hyperparameter.
- **Exit / flip**: opposite-direction prediction crossing TOB by X bps. No time-based exit.
- **Risk**: max position $100 notional. No risk increase from a non-zero position — once long, only short orders allowed; once short, only long.
- **Liquidity removal not tracked**: simulator does not deduct our fills from the book or otherwise update it. Our orders are treated as non-impacting. Consistent with the small-position and zero-impact assumptions; revisited when capacity / market-impact realism is added.
- **Unconditional fill at the touch** — *registered 2026-09-30.* The simulator fills the whole 0.001 BTC lot at `ask_ticks[i]` (long) or `bid_ticks[i]` (short) **on the same decision row whose book produced the feature**: no queue position, no partial fill, no adverse selection, and no check that the quote is still there when the order arrives. Removing it is queue items 4 and 5 below.

  **It is NOT what the sign of the P&L depends on, and that was measured rather than assumed.** `.planning/debug/07-oof-pnl-leakage-investigation.md` looked for the vanishing queue this assumption would be hiding and did not find one: on a 749,932-row real slice the side the model takes carries a **median 2.884 BTC resting** against its 0.001 BTC order, and rests at or below the order size on only **0.19%** of rows (the untaken side's median is 14.996 BTC, 5.2x). As a *taker* crossing a spread into a queue that deep, the unconditional fill is close to right. What the sign does depend on is **zero fees**, the first item in the removal queue: the realised edge is 6,250,827 ticks over 155,927 trades = **40.09 ticks per round trip = 0.519 bp** of a ~$77,000 mid, and the viability script's own `x_bps=1` probe — one basis point of required overshoot — produces zero trades and zero P&L on all five blocks. This bullet is a registration, not a reordering of the queue.

  **What it does cost, measured** (zero-look runs on the five cached OOF blocks; `.planning/phases/07-regression-track-vertical-slice/evidence/07-oof-viability-results.json` and `07-fill-skill-gap.json`, from `scripts/oof_viability_check.py` and `scripts/fill_skill_gap.py`):

  - The frozen predictor's **sign agrees with the next row's mid change 91.5% to 96.0%** of the time (82.5% to 94.6% tick-weighted) while its 10-second R² is only 0.006 to 0.035. The skill it monetises is contemporaneous, not a forecast: `imb_top` at lag 0 is **top-of-book depletion**, and the forward association with the mid change dominates the backward one on all five blocks — which is what rules out leakage rather than confirming it (verdict H1 in that document).
  - **Crossing the spread removes 17% to 33% of the decision-row skill**: rank IC +0.079 to +0.213 against the next row's mid change, against +0.052 to +0.176 for what a long entered and closed *at the touch* actually realises.
  - Of the rows where the mid moved the way the prediction said, **the spread leaves 36% to 50% of them at or below zero** (6,500 of 13,003 on `oof_block_0`; 74,874 of 208,448 on `oof_block_4`).
  - Delaying the prediction by a single decision row costs P&L on **all five** blocks — `frozen_lag1` retains 96.7% to 98.3% — and by a hundred rows retains only 28.8% to 51.5%. At a realistic 20–40 ms the same investigation measured 84% retained, i.e. latency degrades this gracefully rather than cliff-edge.

  **What removing it would require**: an L1-aware fill model (does our order cross, and at what depth), a queue-position model at the touch (where in the FIFO queue our order sits, and whether the quote is consumed before we reach it), and an adverse-selection accounting of fills that happen *because* someone better informed traded. Those are queue items 4 and 5, and they are **untested** — the resting-size and latency numbers above bound the objection, they do not answer it.

  **What `spec.md` already asked for, and what exists**: its "Label engineering" pitfall requires "IC on mid and IC on a fillable proxy" and its "Forecast-vs-execution gap" pitfall requires an "immediate-after-fill price reversion histogram". The **skill-gap IC now exists** (`scripts/fill_skill_gap.py`, numbers above). The **reversion histogram does not** — see `spec.md`'s own status notes on both.

## Decision logic (explicit)

**Struck (dimensional bug, SPEC-03).** An earlier draft of this section multiplied the
predicted return by the midprice and compared that price *change* against a price
*level* (`best_ask`/`best_bid` plus an offset) — the two sides of the comparison were
not in the same units, so the resulting threshold crossings were wrong except by
coincidence. The corrected pseudocode is authoritative in
[`spec.md`](spec.md)'s "Decision rule (Stage 2)" section (`spec.md` wins per this
doc's own conflict rule, see Stage 0 below). Read that section, not this one, for the
actual entry/exit condition.

## Two-stage training pipeline

### Stage 1 — Price forecast

- **Fit**: ML predictor on L1 + trade features → 10s midprice return.
- **Model classes**: all three (regression / trees / transformer) maintained as parallel R&D tracks (see *Model-class portfolio* below).
- **Hyperparameter pick**: on Stage-1 validation fold (Val_S1).
- **Frozen output**: a deterministic predictor used by Stage 2 — one per class.

#### Model-class portfolio (parallel tracks)

All three classes are developed and evaluated in parallel; we do not commit to one early. Their relative ranking is **not stable across realism levels** — under zero-cost / zero-latency a simple regression may be near-optimal; under realistic fees, latency, and queue dynamics the ordering can flip (alpha decays faster than complex models can fit, but more complex models can also use richer state). Locking in early throws away information needed when simplifications are removed.

| Class | Inference latency | Library defaults |
| --- | --- | --- |
| Regression — linear → ridge / elastic-net → non-linear (kernel ridge, GAM, polynomial features, small MLP) | ~1 µs (linear) → ~10 µs (non-linear) | scikit-learn |
| Gradient-boosted trees | ~50 µs | LightGBM (CPU-fast default); alt: CatBoost, XGBoost |
| Transformer | ~50 ms | PyTorch |

**Production deployment rule.** At any realism level, deploy the **simplest class that meets the bar**. A more complex class is preferred only when, on multiple folds:

- IC@10s improvement over the simpler class exceeds the per-fold IC standard deviation (margin > noise), and
- Downstream Stage-2 Sharpe improvement is similarly above noise, and
- Stability across folds is at least as good.

Equal-or-marginally-better complex models are rejected for prod **but their R&D track continues**. Inference latency matters when the zero-latency assumption is later removed: later orders capture less of the fast alpha realization, so simpler models are favored at parity.

**Comparison protocol.** All three classes trained and evaluated on **the same folds, same held-out, same metrics**. Per-fold ranking reported with per-fold std as a confidence band. The deployment selection is itself a hyperparameter and burns selection-bias budget — re-do the comparison whenever realism changes.

#### Metrics (Stage 1)

- Main: **IC of 10s return prediction**.
- Diagnostic: IC at {1s, 1min, 10min}, RMSE, directional accuracy, R² vs. zero-return baseline.
- Stability: per-fold IC mean & std; per-symbol IC dispersion.
- Calibration: predicted vs. realized return std.
- Robustness: regime-split IC (high-vol vs. low-vol; trending vs. choppy).

### Stage 2 — Monetization

- **Fit**: threshold policy (X bps) on Stage-1 forecasts produced over data **Stage 1 was not trained on** (Train_S2). Black-box optimization (CMA-ES / Optuna).
- **Hyperparameter pick**: on Stage-2 validation fold (Val_S2).
- **Inputs to fit**: forecast, L1 quotes, trade tape, current position.

#### Metrics (Stage 2)

- Main: **annualized Sharpe**.
- Other: net P&L, hit rate, average bps captured per round trip, turnover, max drawdown, Calmar, time-in-market, per-symbol Sharpe.
- Stability: per-fold Sharpe mean & std; rank stability of X across folds.
- Robustness: regime-split Sharpe (high-vol vs. low-vol).

### Sequence rule (anti-leakage)

Stage 2's training data are Stage 1's predictions on **out-of-Stage-1-train** data. Mixing is the most common silent leakage path in two-stage pipelines and is forbidden by the per-fold split below.

## Stage 0 — Living spec (before v0)

The first deliverable is a **living spec** at [`spec.md`](spec.md). It captures the engineering, modelling, and evaluation rules the project operates under: conventions, feature catalogue, label catalogue, DOs/DONTs, known HFT/MFT pitfalls with mitigations and enforcement points, glossary, change log.

**Live-changing**: every leakage audit, post-mortem, surprise, or version-gate review updates `spec.md` the same day. If `mvp.md` and `spec.md` disagree, **`spec.md` wins** and `mvp.md` is updated to match.

Stage 0 is the prerequisite for v0 — no model training begins until `spec.md` exists in seed form and the feature/label catalogue is wired into CI.

## Iteration plan

Stage 0 (`spec.md`) is a prerequisite for v0. Each version runs **all three model classes in parallel** on the same folds and held-out. Version labels refer to data scope / realism — model class is a parallel axis.

**v0 — Smoke test.** Single symbol, fixed X, all three classes. Each must beat zero baseline.

**v1 — Threshold sweep.** Sweep X per class; pick on Val, report on held-out. Side-by-side comparison.

**v2 — Multi-symbol.** Add ETH, then 2nd-tier symbol; separate per-symbol training per class.

**v3 — Universal model.** Each class trained jointly on all listed perps (regression on stacked features, trees with symbol embedding / one-hot, transformer with cross-asset attention).

**v4+ — Begin removing simplifications.** Order TBD (see Open Questions). **Re-run the model-class comparison at every realism step** — winner can change.

Each version: one frozen eval window, MLflow-tracked, committed.

## Success criteria

### Primary gate (MVP exit)

- **Net P&L > 0** *and* **annualized Sharpe > 5** on a held-out walk-forward window, under the stated simplifications, achieved by **at least one of the three model classes**.
- Side-by-side metrics for all three classes reported regardless, so a re-evaluation under removed simplifications has a baseline.
- Note: Sharpe > 5 is achievable here because of zero-cost / zero-latency / small-size assumptions. It is *not* a real-world bar; it is the bar for "the simplified setup works." The number is expected to drop as simplifications are removed in later stages — that's the point.

### Secondary gate (workflow validation)

- At least one model improvement in the v0 → MVP-exit chain produced via the agentic loop (not hand-written by the human), and verifiably moved the metric on the validation window.

## Validation methodology

The pipeline is two-stage and rolling-retrained, so each fold has **five sequential segments** (with embargo gaps between every consecutive pair):

```text
| Train_S1 | Val_S1 | Train_S2 | Val_S2 | Held-out |
   S1 fit    S1 hp     S2 fit     S2 hp    one shot
             pick                  pick    per gate
```

- **Train_S1** — Stage 1 trains here only.
- **Val_S1** — Stage 1 hyperparameters picked here (selection-bias burns here).
- **Train_S2** — Stage 1 *predicts* (no retrain); Stage 2 fits its policy on those predictions. Stage 1 has not trained on this segment, so Stage 2 sees realistic out-of-sample forecasts.
- **Val_S2** — Stage 2 hyperparameters picked here.
- **Held-out** — end-to-end pipeline (Stage 1 inference → Stage 2 trade decisions) evaluated **once per gate**.

**Rolling walk-forward** shifts this whole structure forward at the configured retrain cadence. The held-out window is locked at v0 and rotated only when crossing a project milestone, not per run.

- Frozen held-out window declared once at v0; agents cannot redefine.
- Metrics per stage: see *Two-stage training pipeline*.
- Leakage audit per version: every feature listed with information set vs. label timestamp; every Stage-2 input proven to come from out-of-Stage-1-train predictions.
- Reproducibility: code hash + data hash + seed + env hash recorded with every run.

### Fallback when data history is limited

3 months of single-symbol data — combined with crypto's weekly-to-monthly regime shifts — may starve the 5-segment split or place Stage 1 and Stage 2 in different regimes. The fallback collapses to **3 segments**, with Stage 2 still seeing realistic out-of-sample forecasts via OOF generation:

```text
| Train (shared by S1 + S2) | Val | Held-out |
```

- **Stage 1 on Train**: produce **out-of-fold (OOF) predictions** over Train via purged + embargoed inner k-fold (or block CV). Each prediction is on a sub-block Stage 1 was *not* trained on inside the inner fold. Then refit Stage 1 on all of Train for downstream use.
- **Stage 2 on Train**: fits on the OOF predictions. Both stages thus share the same calendar range, but Stage 2 is exposed to realistic OOS forecast quality.
- **Val**: Stage 1 (final) predicts, Stage 2 (final) acts; both stages evaluated **jointly**. Selection-bias budget burns once for both stages' hyperparameter choices on this segment.
- **Held-out**: end-to-end one-shot, unchanged.

**Use the compressed split when** any of the 5 segments would fall below a declared sample-size floor, *or* regime evidence argues against pushing Train_S1 further back. Record the choice (and reason) in MLflow per run.

Costs:

- ~k× Stage 1 training compute (one fit per inner fold + one final fit on Train).
- Stage 1 hyperparameters must be fixed before OOF generation. If they need tuning, carve a small Val_S1 inside Train and use it for Stage-1-only hyperparameter pick before OOF generation.
- Slight residual leakage if Stage 1 hyperparameters are tuned on Train and then reused for OOF; document and cap by keeping the Stage 1 search small.

**Parallel mitigation** — the universal cross-symbol model (see *Forecast* section) effectively multiplies training tokens ~N× and may restore feasibility of the 5-segment split without OOF gymnastics. Try this before committing to the compressed split if single-symbol data is the bottleneck.

## Over-fitting controls

Over-fitting is the dominant risk at both stages. Treated as a first-class design concern, not a post-hoc check.

### Stage 1

- Hyperparameter search budget capped (N trials) **per model class**; every trial counts toward that class's selection-bias budget. **The cross-class comparison (regression / trees / transformer) is itself a selection** and burns additional budget; never run it on the held-out. Use the same folds, features, and metrics across classes so comparisons are apples-to-apples.
- Early stopping on Val_S1 (where applicable: trees use early-stopping rounds; transformer uses Val loss; regression uses cross-validated regularization).
- Regularization defaults per class: regression → ridge / elastic-net / kernel bandwidth; trees → max-depth / min-leaf / num-leaves / L1+L2 leaf penalty; transformer → dropout, weight decay, gradient clipping.
- Model capacity bounded relative to available data per class (capacity rule in config).
- Multiple seeds per config; seed variance reported alongside mean.
- Embargo prevents temporal leakage between consecutive folds.

### Stage 2

- Keep policy parameter count small (X plus position cap; resist adding more).
- Coarse X grid first; refine only if the coarse maximum sits in a robust region, not on a knife-edge.
- Walk-forward Sharpe **stability** is the gate, not a single-fold Sharpe.
- Regime-split Sharpe (high-vol vs. low-vol) reported and required to be non-catastrophic.
- Reject configs whose Sharpe rank is unstable across folds.

### Cross-cutting

- **Selection-bias budget**: every "look" at any validation segment is counted; budget exhaustion forces a fresh window.
- **Held-out lockbox**: touched once per gate. Any second look invalidates it; a fresh held-out window must be declared.
- **Negative-result log**: failed configs recorded so the same dead-end isn't re-explored under a different name.
- **Rolling retrain cadence** declared up front (e.g., weekly) and matched in walk-forward and live.

## Results format (per version)

- **Side-by-side per model class** (regression / trees / transformer): IC@10s, P&L, Sharpe, hit rate, turnover, drawdown.
- Per-fold metric ± std for each class; recommended deployment class flagged with reason.
- Equity curve on held-out window per class (overlaid).
- Per-symbol attribution (when multi-symbol or universal).
- Trade log: count, hit rate, average bps captured.
- One-page "what changed, which class moved, and why."

## Open questions (TBD)

- **Q1. Agentic loop scope for MVP.** Decide before v1 — affects what "agent-driven improvement" means for the secondary gate. Options:

  - (a) Claude Code as pair-programmer, human drives.
  - (b) One Claude agent in a loop proposing changes from MLflow results; human approves PRs.
  - (c) Multi-role agents (Researcher / Critic) even at MVP scale.

- **Q2. First simplification to remove post-MVP.** Candidates: taker fees → larger position size → non-zero latency → maker option → multi-symbol → universal model. Decide at MVP exit, informed by which simplification looks most fragile in the v0–v3 results — and whether the model-class ranking shifts.
- **Q3. GPU spec & training budget.** CPU+GPU is confirmed; specific GPU and per-run wall-clock budget needed before v0. Regression and tree tracks run on CPU throughout; the transformer track needs GPU from v0 onward.
- **Q4. RL introduction.** If/when RL replaces or augments the threshold policy. Likely deferred past MVP; revisit once v3 exists.
- **Q5. 2nd-tier symbol choice.** Liquidity vs. behavioral diversity tradeoff (e.g., SOL vs. DOGE vs. BNB).

## Simplification removal queue (post-MVP, tentative)

Order is informed by R1 and will be locked at MVP exit:

1. Taker fees (Binance VIP0 baseline).
2. Larger position; bounded inventory; per-symbol cap.
3. Non-zero latency (data + order).
4. L1-aware fills (spread crossing); maker-vs-taker decision.
5. Queue position modeling.
6. Market impact.
7. Funding-rate P&L.

Each removal is its own version with its own gate: net P&L > 0 must hold; Sharpe will compress and that's expected.

**Items 4 and 5 together remove the "unconditional fill at the touch" assumption** (see the Monetization assumptions above). The 2026-09-30 measurements put **item 1 first on the evidence, not merely on the list**: the frozen predictor's realised edge is 0.519 bp per round trip, and one basis point of required overshoot beyond the touch takes every trade on all five OOF blocks to zero — so taker fees are what the sign of this P&L rests on. The fill and latency assumptions cost less than that (median 2.884 BTC resting on the taken side against a 0.001 BTC order; 84% of the P&L retained at 20–40 ms), though crossing the spread does already remove 17% to 33% of the decision-row skill. Q2's "first simplification to remove post-MVP" now has a number behind its default answer.
