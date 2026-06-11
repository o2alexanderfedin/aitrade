# Pitfalls Research

**Domain:** Crypto HFT/MFT microstructure ML — Binance perpetual futures, two-stage forecast + monetization, simulation-only MVP
**Researched:** 2026-06-10
**Confidence:** HIGH (Binance data findings verified empirically against live S3 archive + official docs; stack findings verified against official docs and GitHub issues; methodology findings MEDIUM-HIGH from established literature)

---

## Part 0: Validation of the Existing spec.md Catalogue

The spec's pitfalls catalogue (temporal leakage, sample-size illusion, label engineering, data quality, distribution shift, multi-testing/selection bias, forecast-vs-execution gap, production–research drift, capacity/market impact, knife-edge optima, operational reproducibility) **matches current industry consensus** — it covers the López de Prado canon (purged/embargoed CV, effective sample size, selection-bias accounting) plus the practitioner-level execution-gap items. Two enrichments worth adding to spec.md, not corrections:

1. **Deflated Sharpe Ratio (DSR).** The spec tracks a selection-bias *budget* but never *spends* it numerically. Bailey & López de Prado's DSR converts "number of trials" into a haircut on the reported Sharpe. With a Sharpe > 5 gate and a capped-trials regime already in place, computing DSR per gate is nearly free and makes the budget mechanically meaningful instead of advisory.
2. **Regime-mismatch ≠ overfit triage.** With only ~3 months of data, the held-out tail (most recent) may sit in a different regime than all training segments. A held-out failure then burns the lockbox for a reason that is *not* model overfit. Spec should pre-declare a triage rule: regime-tag every segment at data-prep time (vol tercile, trend/chop) so a held-out miss can be classified before deciding whether to rotate the window.

Everything below is **not** in spec.md (or is there only as a one-line symptom without the operational detail that actually bites).

---

## Critical Pitfalls

### Pitfall 1: There is no public L1 history to backfill — the backfill plan is partially dead on arrival

**What goes wrong:**
The plan says "~3 months history (capture + `data.binance.vision` backfill where available)". Verified directly against the archive (2026-06-10):

- **Futures USD-M `bookTicker` archives are frozen.** Monthly files run 2023-05 → **2024-04**; daily files run 2023-05-16 → **2024-03-30**. Nothing newer exists. Binance stopped publishing them (confirmed by dev-community threads asking where the data went).
- **Spot has never had L1 archives at all.** `data/spot/daily/` contains only `aggTrades/`, `klines/`, `trades/`. No bookTicker, no depth — ever.
- `bookDepth` (futures) is 1-second-sampled aggregate depth, **not** tick-level L1 — not a substitute for BBO event streams.

Only **trades/aggTrades/klines** are backfillable for the current period. A team that discovers this in week 4 has zero L1 history and a 3-month data requirement that now takes 3 calendar months of capture to satisfy.

**Why it happens:**
The archive directory structure still *exists* (stale 2023–2024 files), so a quick look suggests bookTicker backfill works. Only listing the actual file dates reveals the freeze.

**How to avoid:**
- **Start live L1 capture on day 1 of the project — before any other engineering.** Wall-clock capture time is the project's hardest constraint; every day of delay is a day less history at MVP exit.
- Decide explicitly: either (a) accept trades-only features for the backfilled period and L1+trades for the captured period (two feature regimes — document it), or (b) buy tick-level L1 history from a vendor (e.g., Tardis.dev sells Binance futures quote/depth ticks), or (c) shrink the history requirement.
- Trades/aggTrades backfill remains fully viable — use it to extend the *trade-flow* feature history even where L1 is capture-only.

**Warning signs:**
Roadmap has "data layer" as phase 2 or later; nobody has run a capture process yet; backfill scripts written before anyone listed actual archive contents.

**Phase to address:** Phase 1 (Stage 0 / project setup) — capture daemon must be running before the spec is even finished. Backfill reconciliation in the Data phase.

---

### Pitfall 2: Spot L1 websocket has no exchange timestamp — "etime is the only clock" is unsatisfiable for spot bookTicker

**What goes wrong:**
The spot `<symbol>@bookTicker` websocket payload is `{u, s, b, B, a, A}` — **no event time, no transaction time**. This is a documented, years-old community complaint that Binance has not fixed in the JSON streams. The project's convention "exchange time `etime` is the only clock" cannot be satisfied for spot L1 captured this way. Futures bookTicker *does* carry `E` (event time) and `T` (transaction time).

**Why it happens:**
Teams test the capture path on futures (which has timestamps), then add spot "the same way" and only notice at feature-join time that the spot quote rows have null etime.

**How to avoid:**
Pick one, deliberately, and write it into spec.md Conventions:
- Use the spot **diff-depth stream** (`@depth@100ms`), which carries `E`, and derive BBO from a maintained local book; or
- Use Binance **SBE market-data streams** for spot — the SBE best-bid-ask stream includes `eventTime` (requires SBE decoding work); or
- Stamp spot bookTicker with local receive time and document the clock-source exception (weakens the etime-only invariant; the spec's "DON'T mix Spot and Swap timestamps without auditing" becomes load-bearing).

**Warning signs:**
Capture schema has a nullable `etime` column; spot and futures L1 rows pass through the same parser without a per-stream field map.

**Phase to address:** Data capture phase (schema design), enforced by a CI check that every captured row has non-null `etime` with documented provenance.

---

### Pitfall 3: Archive format heterogeneity — microseconds vs milliseconds, headers vs no headers

**What goes wrong:**
Verified empirically by downloading archives (2026-06-10):

- **Spot archive timestamps switched from milliseconds to microseconds on 2025-01-01** (16-digit vs 13-digit epoch values). Futures archives are still milliseconds.
- **Futures CSVs contain a header row; spot CSVs do not.** Same dataset family, different parse rules.
- Live websocket streams are milliseconds by default (microseconds opt-in via `timeUnit=MICROSECOND` on spot).

A naive shared parser silently produces timestamps off by 10³ (spot vs futures) or eats the first row as data / treats data as a header. Off-by-10³ in the ns convention means joins don't crash — they just match nothing, or match wrongly, and features go quietly to zero/NaN.

**Why it happens:**
The unit change is buried in the binance-public-data README; nothing in the file name or schema signals it. Mixed-vintage backfills (2024 files + 2026 files) have mixed units *within one symbol's history*.

**How to avoid:**
- Per-dataset **unit registry**: (market, dataset, date-range) → timestamp unit, header present y/n. Conversion to int64 ns happens in exactly one place, driven by the registry.
- **Plausibility gate in CI**: every ingested etime must fall inside [capture window ± 1 day] after conversion. A µs value parsed as ms lands in year ~58,000 — trivially catchable, but only if someone checks.
- Checksum-verify every archive file (CHECKSUM sidecars exist) and log per-file row counts.

**Warning signs:**
Any timestamp-parsing code with a hardcoded `* 1_000_000`; date-range plots of ingested data with empty or absurd ranges; spot/futures join producing near-zero match rates.

**Phase to address:** Data phase (ingest layer), with the plausibility gate wired into CI per the spec's existing enforcement style.

---

### Pitfall 4: "Last row of each etime" is undefined without a materialized arrival-sequence column

**What goes wrong:**
The spec's decision-row rule depends on "latest in arrival order within the file." Arrival order is **physical row order**, which evaporates: polars `group_by` output order is non-deterministic without `maintain_order=True` (documented, with open issues about non-deterministic agg results); parquet repartitioning, predicate-pushdown scans, and any re-sort destroy intra-etime order; sorting by `etime` alone cannot recover it because the tied rows are exactly the problem. Result: training, inference, and simulator can each pick a *different* "last row" for the same etime — a silent train/serve skew inside the single-code-path design that was supposed to prevent train/serve skew.

Related: polars 1.35+ has documented **non-deterministic float aggregation** (multithreaded sums), so even "same data, same code" can produce different feature values run-to-run — poisonous for the reproducibility hashes in MLflow.

**Why it happens:**
Row order feels like a property of the data; in columnar/lazy engines it's a property of the *execution plan*.

**How to avoid:**
- Capture writes a **monotonic per-stream sequence number** (`seq`) into every row at ingest. For backfilled archives, use file row index + trade ID / update ID. The decision-row rule becomes `arg max (etime, seq)` — order-independent and provable.
- Treat `(etime, seq)` as the mandatory sort key everywhere; ban implicit reliance on frame order in code review.
- For reproducibility-critical aggregations, pin polars version, set thread count in the env hash, and prefer order-explicit constructs (`sort` + `last`, `maintain_order=True` where the streaming engine isn't needed).

**Warning signs:**
Feature values change between two runs on identical inputs; sim and training disagree on decision-row count per day; any `group_by(...).last()` without a preceding explicit sort on a total ordering.

**Phase to address:** Data phase (capture schema: `seq` column) + Feature pipeline phase (decision-row selection test in CI: shuffle input rows → identical decision rows out).

---

### Pitfall 5: aggTrades vs trades semantics — backfill and capture see different tapes

**What goes wrong:**
Three distinct traps stacked together:

1. **Futures aggTrade excludes insurance-fund and ADL trades** (official docs: "Only market trades will be aggregated"). The `trades` dataset includes them. Liquidation flow — exactly the bursts that matter most for short-horizon prediction around cascades — differs between the two sources.
2. **Aggregation merges fills** (same price, same taker order). Trade-count features, size-distribution features, and inter-trade-arrival features computed on aggTrades vs raw trades are systematically different. If backfill uses archive `trades` and live capture uses `@aggTrade` (or vice versa), the feature distribution shifts exactly at the backfill/capture boundary — which is also roughly where train ends and held-out begins. A model can "learn" the seam.
3. **`isBuyerMaker` sign confusion**: `m = true` means the *buyer was the maker*, i.e., the **taker/aggressor sold** ⇒ tradeSide = −1. The inverted mapping is one of the most common Binance bugs in the wild, and it flips every flow feature's sign. It would also corrupt the project's nearest-quote tradeSide backfill validation (the two methods would "disagree" because one is sign-flipped).

**Why it happens:**
The names are similar, the schemas mostly overlap, and the side flag is phrased from the maker's perspective while everyone reasons from the taker's.

**How to avoid:**
- Pick **one tape source per market** (recommend raw `trades` for futures: includes liquidation-adjacent flow; aggTrades acceptable if used *consistently* in both backfill and capture) and record the choice in spec.md.
- CI fixture test: a handful of hand-labeled trades (price vs prevailing BBO) asserting the `m`→side mapping.
- Cross-validate `tradeSide_corrected` (nearest-quote method) against `m`-derived side on modern data where both exist; agreement rate is itself a data-quality metric (also calibrates how trustworthy the legacy `tradeSide = 0` backfill is).
- Detect tape gaps via trade-ID continuity (`f`/`l` ranges on aggTrades; `id` on trades) per day, logged in the daily data-quality report.

**Warning signs:**
Flow-feature distributions shift at the backfill/capture seam; OFI-style features anticorrelate with returns (sign flip); trade counts per minute differ between two "equivalent" sources.

**Phase to address:** Data phase (source choice + side-mapping test), Feature phase (seam-shift check: feature-distribution comparison across the backfill/capture boundary).

---

### Pitfall 6: Event-time is not a total order — E vs T, cross-stream ordering, and the simulator's event loop

**What goes wrong:**
Futures payloads carry both `E` (event/push time) and `T` (transaction/matching-engine time); they differ by milliseconds. Different streams (bookTicker, aggTrade, depth) are pushed independently — interleaving across streams is **not** guaranteed in `E` order, and equal-`T` events across streams have no defined relative order. An event-driven simulator that merges streams by one timestamp field while features were built on another field replays history in a different order than the feature pipeline saw it — a subtle production–research drift *inside the research stack itself*. Quote-trade misordering at the same ms also corrupts the nearest-quote tradeSide backfill (classifying a trade against a quote that the matching engine produced *after* the trade).

**Why it happens:**
"etime" feels like one thing; Binance gives you two per event plus undefined cross-stream interleaving.

**How to avoid:**
- Spec must define `etime` **per stream** (recommend: `T` where present — matching-engine time — falling back to `E`), plus a deterministic tie-break: `(etime, stream_priority, seq)`. Trades before quotes at equal timestamps, or quotes first — either is defensible, but it must be *one rule shared by feature pipeline and simulator*.
- The simulator consumes the **same merged, ordered event stream artifact** the feature pipeline consumes — merge once at data-prep time, not independently in two codebases.

**Warning signs:**
Sim P&L changes when the stream-merge code is refactored; tradeSide backfill disagrees with `m`-flag side at far above baseline rate specifically for trades at BBO-change instants.

**Phase to address:** Data phase (merged-stream artifact + ordering rule in spec.md), verified again in Simulator phase (replay-determinism test).

---

### Pitfall 7: Capture gaps in L1 are permanent — websocket operations are a first-class reliability problem

**What goes wrong:**
Binance force-disconnects websocket connections every 24h; ping/pong timeouts, local network blips, and process restarts add more gaps. For **trades**, gaps are patchable from next-day archives or REST. For **L1/BBO there is no patch source** (Pitfall 1) — every capture gap is a permanent hole in the most important dataset. Three months of single-host capture with naive reconnect logic typically yields dozens of multi-second-to-multi-minute holes, each of which (a) breaks rolling features, (b) invalidates labels whose 10s window spans the hole, and (c) can hide exactly the volatile moments that matter (disconnects correlate with volatility spikes — load on exchange infra). Depth-based capture additionally requires the documented resync dance (futures: `pu` of each event must equal previous `u`, else REST snapshot + replay); broken `pu` chains that aren't detected give you a *silently wrong book*, worse than a gap.

**Why it happens:**
Capture is treated as a script, not a service. The 24h disconnect is in the docs but only bites in production.

**How to avoid:**
- **Redundant capture**: ≥2 independent connections (ideally separate hosts/regions), staggered reconnect schedules so the 24h kills never coincide; merge + dedupe by (stream, update-id/trade-id).
- Gap ledger as a first-class dataset: every hole recorded with start/end etime; features get the spec's existing post-resync warm-up applied after *every* gap, not just depth resyncs; labels whose forward window crosses a gap are dropped, not zero-filled.
- If using depth streams: enforce the `pu`-chain check, auto-resnapshot on break, and tag the resync (spec already mandates the tag — this is the implementation that makes it real).
- Monitor capture liveness (no-event watchdog per stream) from day 1.

**Warning signs:**
Capture process uptime measured in days without a gap report existing; "reconnect" handled by a bare `while True` with no dedupe; book occasionally crossed for extended periods (broken pu chain).

**Phase to address:** Phase 1 / Data capture phase — this is the single highest-leverage piece of engineering in the project because its failures are unrecoverable.

---

### Pitfall 8: Tick-size and contract-spec regime changes mid-history

**What goes wrong:**
Binance changes tick sizes on running contracts (BTCUSDT perp went 0.01 → 0.1 in Feb 2022; multiple USD-M perp and spot tick-size update rounds were announced through 2025, including September and December 2025 — i.e., plausibly *inside this project's 3-month window* for whatever symbols are chosen). A tick-size change discontinuously shifts: spread distribution, mid stickiness, top-of-book sizes, imbalance features, and the meaning of "X bps beyond TOB". A model trained across the seam learns two microstructures; a threshold X tuned before the seam is miscalibrated after it. Filters (minNotional, stepSize) also change and matter later for realism (see Pitfall 12).

**Why it happens:**
exchangeInfo is fetched once, treated as static, and never versioned with the data.

**How to avoid:**
- Snapshot `exchangeInfo` daily into the data lake; treat tick size as a time-varying column joined onto features.
- CI check: tick-size change inside any train/val/held-out segment triggers a flagged warning in the run manifest; regime-split metrics (already in spec) add a tick-regime split when a seam exists.
- For 2nd-tier symbol selection (Q5), check the symbol's tick-size/filter announcement history first — small caps get re-ticked more often.

**Warning signs:**
Spread histogram is bimodal over the history; X-threshold optimum is unstable across folds that straddle a known announcement date.

**Phase to address:** Data phase (exchangeInfo snapshots), Stage-2/eval phase (seam-aware splits).

---

### Pitfall 9: numba foot-guns — frozen globals, silent int64 overflow, fastmath/parallel non-determinism, float P&L

**What goes wrong:**
Four distinct, documented traps, all relevant to the sim hot path:

1. **Globals are compile-time constants.** A jitted function capturing a module-level config (e.g., `X_BPS`, `MAX_POS`) bakes the value in at first compile; later changes are silently ignored (and the behavior differs for same-module vs imported globals). In a threshold-sweep loop this produces *every sweep iteration evaluating the first X value* — the sweep "runs" and returns a flat curve, or worse, a plausible-looking but wrong one.
2. **Silent int64 overflow.** Numba does not raise on integer overflow — it wraps. Nanosecond arithmetic is exactly where this bites: `ns_duration * scale`, squared time deltas, accumulating ns offsets. A wrapped timestamp comparison flips a branch once in 10⁹ events and is undiscoverable by eyeball.
3. **`fastmath=True` and `parallel=True` break determinism.** Reordered float reductions give run-to-run different sums; `fastmath` also breaks NaN propagation (`x != x` checks can be optimized away). Bitwise-reproducible sim P&L — required for the MLflow code-hash/data-hash reproducibility contract — is incompatible with both unless reductions are restructured.
4. **Float P&L accumulation.** Accumulating millions of small float64 P&L increments accrues error; flip-only logic comparing `position == 0.0` on floats misfires after rounding drift.

**How to avoid:**
- All config enters jitted functions as **arguments** (or typed structrefs), never as captured globals. Lint rule: no module-level name lookups inside `@njit` bodies.
- Do P&L and position accounting in **integer ticks and integer base-quantity quanta**; convert to float only for reporting. This kills both overflow-adjacent float drift and `== 0` comparisons (integers compare exactly).
- Default `fastmath=False`, `parallel=False` on the sim hot path; the event loop is sequential anyway. Reproducibility test in CI: run sim twice on a fixture day, assert bit-identical trade logs.
- Add explicit overflow-guard asserts on ns arithmetic in debug builds (numba supports `assert`); property-test the hot path against a slow pure-Python reference implementation on small fixtures.

**Warning signs:**
Sweep curve suspiciously flat; sim P&L differs in the 8th decimal between runs; "cached" numba behavior changes after unrelated edits (stale `cache=True` artifacts across env changes — include numba version in env hash).

**Phase to address:** Simulator phase (integer accounting + reference-implementation test), with the no-globals lint in Stage 0 CI.

---

### Pitfall 10: The decision rule in mvp.md has a dimensional bug — and nothing in the plan would catch it

**What goes wrong:**
mvp.md's pseudocode: `if pred_10s_return * mid > best_ask + X_bps_in_price: long`. `pred_10s_return * mid` is a **price change** (e.g., +$5); `best_ask + X` is a **price level** (e.g., $63,090). The intended condition is `mid * (1 + pred_10s_return) > best_ask + X_bps_in_price`. As written, the comparison is always false for any realistic return — the strategy never trades — or, if someone "fixes" it by dropping `mid`, compares a return to a price. The deeper pitfall: **unit/dimension errors in bps-threshold logic are the most common implementation bug in this exact strategy family** (bps vs fraction ×10⁴ confusion, bps-of-mid vs bps-of-ask, one-sided vs two-sided thresholds), and the current test plan (CI leakage proofs, feature catalogues) has nothing that would catch policy-math errors.

**Why it happens:**
Pseudocode written for intent, transcribed literally into code; bps conversions done inline at multiple call sites.

**How to avoid:**
- Fix the spec pseudocode now (cheap; it's a living spec).
- One **typed conversion module** for price/return/bps math; thresholds computed in one place.
- Policy unit tests with hand-computed fixtures: "mid 100.00, ask 100.01, pred +50bps, X=2bps ⇒ long"; include zero-prediction and sign-flip cases. A "sim must trade on an obviously profitable synthetic signal" smoke test (oracle test: feed the *realized* future return as the prediction — Sharpe should be enormous; if not, the policy/sim plumbing is broken). The oracle test also bounds the achievable ceiling for Stage 2.
- Trade-count sanity band per day in the v0 gate (zero trades or trades-every-tick both fail).

**Warning signs:**
v0 sim produces zero trades, or hit rate ~50% with near-zero bps captured on the oracle signal.

**Phase to address:** Update spec.md in Stage 0 (immediately); oracle + fixture tests in Simulator phase, wired into the v0 smoke-test gate.

---

### Pitfall 11: Sharpe > 5 gate with an undefined annualization convention and a too-short held-out window

**What goes wrong:**
Two compounding issues:

1. **The annualization convention is unstated and the gate is gameable.** Annualized Sharpe from high-frequency P&L depends entirely on the sampling grid of the equity curve (per-trade? per-10s? per-1s mark-to-market? daily?) and the scaling factor (crypto is 24/7: 365 days, 86,400s/day — not 252). With autocorrelated, overlapping 10s P&L increments, naive √N scaling overstates Sharpe substantially (the spec knows this for IC t-stats but doesn't apply it to the *gate metric itself*).
2. **Held-out Sharpe has enormous estimation error on a short window.** With ~3 months of data, the held-out segment is maybe 2 weeks. The standard error of an annualized Sharpe estimate over 2 weeks is large; a true-Sharpe-3 strategy passes a Sharpe-5 gate (or true-7 fails it) at material probability. One-shot lockbox evaluation makes this worse: a single noisy draw decides the gate.

**How to avoid:**
- Define in spec.md before v0: equity sampled at fixed Δt (recommend 1s mark-to-market on mid), Sharpe = mean/std of Δt returns × √(365·86400/Δt), with an HAC/block-bootstrap **confidence interval reported next to the point estimate**. The gate becomes "point estimate > 5 *and* CI lower bound > 0" or similar — pre-declared.
- Pre-declare the held-out window *length floor* alongside the sample-size floor the spec already has for fold segments.
- Report DSR (Part 0) at the gate to account for trials burned.

**Warning signs:**
Two reasonable annualization choices give Sharpe 4 vs 8 on the same run; gate debates happen *after* seeing held-out numbers.

**Phase to address:** Stage 0 (write the convention into spec.md), Evaluation-harness phase (implementation + CI on the metric function with golden fixtures).

---

### Pitfall 12: Labels, lookbacks, and folds crossing partition boundaries

**What goes wrong:**
Data is partitioned `symbol/date`. A 10s label at 23:59:55 needs the next day's file; a 5-minute rolling feature at 00:02 needs the previous day's. Per-day processing (the natural pattern with daily archive files) silently produces NaN/zero labels and cold-start features at every midnight — ~0.1% of rows, biased to a specific time-of-day, invisibly degrading both training and the daily data-quality report. The same off-by-one family appears at **fold boundaries**: embargo implemented in *rows* instead of *time* (event-time spacing is irregular — N rows ≠ 10 seconds), embargo applied between train→val but not before held-out, label windows ending inside the embargo, normalization statistics computed once over the full frame before splitting (the single most common walk-forward leak in practice), and early-stopping on Val followed by a refit that includes Val.

**Why it happens:**
Partitioning and folding both create artificial edges in a continuous stream; every consumer must handle edges, and each handles them slightly differently.

**How to avoid:**
- Loader contract: every consumer requests `[start − max_lookback, end + max_horizon]` and the loader spans partitions transparently; warm-up rows are flagged and excluded from decisions (reuse the post-resync warm-up machinery).
- Embargo and horizons specified **only in time units (ns)**, never rows, everywhere.
- Walk-forward harness owns *all* splitting, embargo, and normalization-stat scoping; model code never sees a raw frame. Test: train on synthetic data where the label is a deterministic function of *future* values — any leak makes OOS accuracy >> chance, and the harness must show chance-level.
- Midnight-adjacent label completeness check in the daily data-quality report.

**Warning signs:**
IC dips or label-NaN spikes clustered at UTC midnight; embargo parameter expressed as an integer row count anywhere in config; a `fit_transform` on the full dataset anywhere upstream of the split.

**Phase to address:** Feature-pipeline phase (loader contract) + Evaluation-harness phase (synthetic-leak test in CI).

---

### Pitfall 13: MLflow as deployed will collapse under this project's run volume, and the selection-bias ledger needs schema-first design

**What goes wrong:**
The plan generates runs at high rate: 3 model classes × capped HP trials × multiple seeds × walk-forward folds × version gates. The default **file-store backend degrades badly at thousands of runs** (documented: minutes to load a few hundred runs; the file store is in maintenance mode), and concurrent writers can corrupt run metadata. Separately: params are truncated at the backend limit (6,000 chars; historically 500) — long feature lists or config JSONs logged as params get silently cut, breaking the "reproduce from manifest" contract. And the spec's selection-bias budget / lockbox-access annotations only work if they're **queryable** — free-text notes can't be summed into a budget. Per-fold metrics logged as separate runs (vs. steps/nested runs) make cross-fold std — the project's core stability statistic — painful to compute, so people stop computing it.

**How to avoid:**
- SQLite (single host) or Postgres backend from day 1; artifacts to a real artifact dir; never the bare file store.
- Manifest contract: configs are **artifacts** (full YAML/JSON files), params hold only scalars; code/data/env hashes as tags.
- Design the tag schema before v0: `lookbox_access=true`, `validation_segment=Val_S1`, `selection_budget_cost=1`, `split_type=5seg|3seg`, `model_class`, `fold_id`. Budget tracking is then a query, not a ritual.
- Nested runs: parent = config, children = folds/seeds; cross-fold aggregation computed by the reporting script from children.

**Warning signs:**
MLflow UI takes >10s to list runs; a "reproduce run X" drill fails because the config param was truncated; budget audit requires reading run notes by hand.

**Phase to address:** Stage 0 / infrastructure phase (backend + tag schema), before the first training run exists.

---

### Pitfall 14: The held-out lockbox is honor-system — and one of the operators is an autonomous agent

**What goes wrong:**
The spec gates held-out access by "an explicit annotation in MLflow" — a *policy* control. The secondary MVP goal is an agentic loop where Claude proposes and runs experiments against MLflow results. An agent (or a tired human) that can read the data lake can read the held-out window: a glob over parquet partitions, a "let me just check the date range" exploration, a plotting script that loads everything — none of which announces itself as a lockbox violation. Agents are *specifically* good at enthusiastically loading all available data. One silent look and the lockbox is invalid with nobody knowing.

**Why it happens:**
Policy controls assume the violator knows they're violating. Exploratory code doesn't.

**How to avoid:**
- **Mechanical quarantine**: held-out date ranges live in a physically separate path (`data/lockbox/...`) excluded from the default dataset loader; the loader *cannot* return lockbox rows without an explicit `unlock_token` argument whose every use is MLflow-logged. File permissions or a separate bucket if paranoia warrants.
- The agentic loop's sandbox simply doesn't mount the lockbox path. Gate evaluations run via a dedicated script (human-invoked) that logs the access.
- CI greps notebooks/scripts for raw-path access patterns bypassing the loader.

**Warning signs:**
Any code path reads parquet by glob instead of through the dataset API; agent transcripts show full-date-range loads.

**Phase to address:** Data phase (loader + quarantine layout) — must exist before the Agentic-loop phase starts, and before v0 burns the first look.

---

### Pitfall 15: Backfill/capture seam and "$100 notional" sim details that quietly invalidate later realism steps

**What goes wrong:**
Two smaller traps worth pre-empting because they're cheap now and expensive later:

1. **The seam between backfilled and captured data is a feature-distribution discontinuity** (different sources, Pitfall 5; different completeness; possibly trades-only vs L1+trades, Pitfall 1). If folds are laid out so train is mostly backfill and held-out is mostly capture, "distribution shift" findings are partly artifacts of the data supply chain.
2. **$100 notional is below/at Binance UM minimums.** BTCUSDT perp has minNotional = 100 USDT and quantity stepSize (0.001 BTC ≈ $63–100+ depending on price); the sim's fractional positions are unexecutable live, and the "flip" action is a 2× notional trade (~$200) crossing the spread — turnover accounting must count notional traded, not position changes, or the post-MVP fee step (queue item 1) will surprise by 2×.

**How to avoid:**
- Tag every row with `source=backfill|capture`; report per-source feature distributions; prefer fold layouts that don't align the seam with the train/held-out boundary (or at minimum report the alignment).
- Sim trade log records *notional traded per fill* from day 1; turnover metric defined on it. Note the minNotional/stepSize collision in spec.md's simplification queue so the "larger position" realism step starts informed.

**Warning signs:**
Held-out IC drop coincides exactly with the seam date; fee-impact projections at the realism step differ ~2× from realized.

**Phase to address:** Data phase (source tags), Simulator phase (notional-based turnover), spec.md note in Stage 0.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Single websocket capture connection | Simpler ops | Permanent L1 holes (no patch source) | Never for L1; OK for trades (archive-patchable) |
| Parsing archives with hardcoded units/headers | Ships today | Silent 10³ timestamp errors when vintage mixes | Never — unit registry is ~50 lines |
| MLflow file store "for now" | Zero setup | Migration + corrupted meta under concurrency at ~10³ runs | Never — SQLite is also zero setup |
| Float P&L accumulation in sim | Natural code | Non-reproducible runs; `==0` position bugs | Never on hot path; fine in reports |
| bps math inline at call sites | Fast to write | Dimensional bugs (Pitfall 10) invisible to leak-audits | Never — one conversion module |
| `group_by(...).last()` relying on frame order | Idiomatic-looking | Non-deterministic decision rows (Pitfall 4) | Never — `(etime, seq)` sort key |
| Skipping exchangeInfo snapshots | One less job | Tick-regime seams undetectable retroactively | Never — daily snapshot is trivial |
| Honor-system lockbox | No infra work | Invalidated held-out, silently | Never once the agentic loop exists |
| Trades-only features over backfill period | 3 months history immediately | Two feature regimes; seam artifacts | Acceptable **if** seam is tagged and reported |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| data.binance.vision | Assuming all datasets are current | List actual file dates first; futures bookTicker frozen at 2024-04; spot has no L1 ever |
| data.binance.vision | One CSV parser for spot + futures | Per-dataset registry: futures has headers, spot doesn't; spot µs since 2025-01-01, futures ms |
| data.binance.vision | Trusting daily files appear at fixed time | They land ~T+1 with variable delay and occasional missing days; verify CHECKSUMs; gap-detect via trade-ID continuity |
| Futures websocket | Ignoring 24h forced disconnect / ping-pong | Staggered redundant connections; dedupe by update/trade ID; gap ledger |
| Futures @depth | Skipping the `pu == prev u` chain check | Enforce chain; auto-resync via REST snapshot (`fapi/v1/depth?limit=1000`); tag resync + warm-up |
| Spot @bookTicker | Expecting an `E` timestamp | None exists in JSON stream; use depth diff stream, SBE streams, or documented local-clock exception |
| aggTrade (futures) | Treating it as the full tape | Insurance-fund and ADL trades excluded; pick one tape source consistently |
| Trades `m` flag | `m=true` ⇒ buy | `m=true` ⇒ buyer was maker ⇒ **taker sold** ⇒ side = −1; fixture-test it |
| exchangeInfo | Fetch once, hardcode tick size | Daily snapshots; tick size as time-varying column (changes announced through 2025) |
| MLflow | File store + concurrent runs | SQLite/Postgres backend; configs as artifacts not params (6k-char truncation) |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| polars `maintain_order=True` everywhere | Streaming engine disabled; OOM on month-scale scans | Sort-then-aggregate patterns; maintain_order only where determinism needs it | >RAM-sized scans (~weeks of L1 ticks) |
| polars multithreaded float aggs | Run-to-run metric jitter breaks repro hashes | Pin version; fix thread count in env hash; order-explicit reductions | Any parallel float sum (1.35+ documented) |
| numba `cache=True` across env changes | Stale behavior after upgrades | Include numba/LLVM version in env hash; clear cache in CI | Any dependency bump |
| Per-event Python↔numba boundary crossing | Sim 100× slower than expected | Batch event arrays into single jitted loop; structs-of-arrays | >10⁶ events/day (i.e., immediately) |
| Many small parquet files (per symbol/day/stream) | Scan-planning dominates; slow backtests | Compact to larger row groups; partition pruning by date range | ~10³ files |
| MLflow per-fold runs × seeds × classes | UI unusable; cross-fold stats hand-rolled | Nested runs + tag schema + SQL backend | ~10³ runs (a few sweeps) |
| Transformer on unsubsampled tick rows | Months of GPU time on redundant tokens | Spec's subsampling triggers exist — also gate transformer context construction cost explicitly before v0 (Q3) | First transformer training run |

## Security & Integrity Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| API keys in capture configs committed to repo | Key leak (even read-only keys reveal account) | Market-data streams need **no** keys — capture should run keyless; any keyed REST use via env/secret store |
| Agent sandbox with write access to raw data lake | Agent "fixes" data in place; provenance destroyed | Raw zone read-only; agents write only to derived zones + experiment dirs |
| Lockbox readable by default loaders | Silent held-out contamination (Pitfall 14) | Physical path separation + unlock-token API + logged access |
| Mutable archive re-downloads overwriting raw | Backfill vintage drift ("latest" by another name) | Content-hash raw files on ingest; immutable raw zone; re-download = new version |

## "Looks Done But Isn't" Checklist

- [ ] **Capture daemon:** Often missing redundant connections + gap ledger — verify a forced-disconnect drill produces a recorded, warm-up-handled gap, not a silent hole
- [ ] **Backfill ingest:** Often missing unit/header registry — verify a 2024 spot file and a 2026 spot file produce continuous ns timestamps
- [ ] **Decision-row selection:** Often missing `(etime, seq)` total order — verify shuffled input yields identical decision rows
- [ ] **Trade-side features:** Often missing `m`-flag fixture test — verify hand-labeled trades classify correctly, and nearest-quote backfill agrees with `m` on modern data
- [ ] **Simulator:** Often missing oracle test — verify feeding realized future returns as predictions produces near-ceiling Sharpe and plausible trade counts
- [ ] **Threshold policy:** Often missing dimensional unit tests — verify the mvp.md pseudocode bug (`pred*mid` vs price level) is fixed in spec and covered by fixtures
- [ ] **Walk-forward harness:** Often missing synthetic-leak test — verify a future-function label scores at chance OOS through the full harness
- [ ] **Sharpe gate:** Often missing pre-declared annualization + CI — verify the metric function is fixture-tested and the convention is in spec.md before any held-out look
- [ ] **MLflow:** Often missing reproduce-drill — verify a month-old run can be re-executed from its manifest alone (config artifact, not truncated params)
- [ ] **Lockbox:** Often missing mechanical enforcement — verify the default loader physically cannot return held-out rows

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Discovered L1 backfill impossible mid-project | HIGH | Buy vendor tick data (Tardis.dev) or re-baseline timeline to capture-accrued history; re-plan folds |
| L1 capture gaps found late | HIGH (data unrecoverable) | Gap ledger retroactively from update-ID discontinuities; drop affected labels; add redundancy going forward |
| Sign-flipped trade side discovered post-training | MEDIUM | Fix mapping; regenerate flow features (new feature names per spec rule); retrain — selection-bias budget unaffected if no validation looks reused |
| Timestamp unit bug in backfill | MEDIUM | Re-run ingest from immutable raw with fixed registry; data-hash change forces clean re-runs |
| Lockbox contaminated (any uncontrolled look) | MEDIUM | Spec's own rule: declare fresh held-out window; record incident; add mechanical quarantine |
| Numba global-capture bug in completed sweep | MEDIUM | Invalidate sweep runs in MLflow (tag), refactor config-as-arguments, re-run sweep; budget re-spent — log it honestly |
| MLflow file-store corruption | LOW-MEDIUM | Migrate to SQL backend (mlflow supports export/import); runs are re-creatable from manifests if Pitfall 13 contract held |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| 1. No public L1 backfill | Phase 1 (Setup/Stage 0): capture running day 1 | Capture uptime dashboard; decision recorded: vendor-buy vs trades-only backfill |
| 2. Spot bookTicker has no etime | Data capture (schema) | CI: non-null etime with documented clock source per stream |
| 3. Archive unit/header heterogeneity | Data ingest | CI plausibility gate on converted timestamps; mixed-vintage fixture test |
| 4. Decision-row ordering | Data capture (`seq` col) + Feature pipeline | Shuffle-invariance test on decision-row selection |
| 5. aggTrades/trades + side mapping | Data ingest | `m`-flag fixture test; backfill-vs-flag agreement metric; seam distribution report |
| 6. E/T + cross-stream ordering | Data prep (merged-stream artifact) | Sim replay-determinism test; ordering rule in spec.md |
| 7. Permanent L1 capture gaps | Data capture (redundancy + gap ledger) | Forced-disconnect drill; gap report in daily DQ |
| 8. Tick-size regime changes | Data (exchangeInfo snapshots) + Eval (seam-aware splits) | Tick-regime flag in run manifests |
| 9. numba foot-guns | Simulator | Bit-identical double-run test; pure-Python reference parity; no-globals lint |
| 10. Policy dimensional bug | Stage 0 (fix spec) + Simulator | Oracle test + policy fixtures in v0 gate |
| 11. Sharpe convention + held-out noise | Stage 0 (spec) + Eval harness | Metric golden-fixture tests; CI bands in gate reports; DSR at gates |
| 12. Partition/fold boundary leaks | Feature pipeline + Eval harness | Synthetic-leak test; midnight label-completeness check |
| 13. MLflow scaling + ledger schema | Phase 1 (infrastructure) | Reproduce-from-manifest drill; budget computed by query |
| 14. Lockbox honor-system vs agents | Data (loader quarantine), before Agentic-loop phase | Loader cannot return lockbox rows without logged token |
| 15. Backfill/capture seam + notional accounting | Data (source tags) + Simulator | Per-source distribution report; notional-based turnover in trade log |

## Sources

**Verified empirically (2026-06-10):**
- data.binance.vision S3 listings — futures UM bookTicker monthly ends `2024-04`, daily ends `2024-03-30`; spot daily contains only aggTrades/klines/trades; futures UM daily dataset inventory ([bucket listing](https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/daily/))
- Archive downloads: spot 2026 daily kline = no header + microsecond timestamps; spot 2024 = no header + milliseconds; futures 2026 = header + milliseconds

**Official documentation:**
- [Binance USD-M Aggregate Trade Streams](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Aggregate-Trade-Streams) — insurance fund / ADL exclusion; E vs T fields
- [How to manage a local order book (USD-M)](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly) — `pu` chain rule, resync procedure
- [binance-public-data README](https://github.com/binance/binance-public-data/blob/master/README.md) — spot microseconds from 2025-01-01
- [Binance spot WebSocket streams](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams) and [SBE market data](https://developers.binance.com/docs/binance-spot-api-docs/sbe-market-data-streams)

**Community / issue trackers:**
- [dev.binance.vision: where to get history bookTicker data](https://dev.binance.vision/t/how-and-where-can-i-get-the-history-bookticker-data/36122/1); [bookTicker data-ordering issue](https://dev.binance.vision/t/data-issue-with-um-btcusdt-futures-booktickers-historical-data/18604)
- [dev.binance.vision: missing timestamp in spot @bookTicker](https://dev.binance.vision/t/missing-timestamp-information-in-symbol-bookticker/15530); [add Event Time to spot book streams](https://dev.binance.vision/t/add-event-time-field-to-spot-websocket-book-streams/1390)
- [dev.binance.vision: timestamp format inconsistency in archives](https://dev.binance.vision/t/timestamp-format-inconsistency-in-historical-archives/33550)
- [Binance announcement: tick size updates, USD-M perps 2025-09](https://www.binance.com/en/support/announcement/detail/0f7995f922734628892b4b66d9d62e1a); [spot tick size updates 2025-12](https://www.binance.com/en/support/announcement/detail/6ad93c9028d54983a39d73d114faf99b); [BTCUSDT perp tick change](https://www.binance.com/en/support/announcement/detail/81e6795b0bae49828cbd52479094a987)
- [numba FAQ: globals as compile-time constants](https://numba.readthedocs.io/en/0.51.1/user/faq.html); [numba #9664 cross-module globals](https://github.com/numba/numba/issues/9664)
- [polars #2112 maintain_order](https://github.com/pola-rs/polars/issues/2112); [polars #25202 non-deterministic float group_by sums](https://github.com/pola-rs/polars/issues/25202); [polars #8190 nondeterministic groupby+agg](https://github.com/pola-rs/polars/issues/8190); [Rho Signal: polars ordering](https://www.rhosignal.com/posts/polars-ordering/)
- [MLflow backend stores docs (file store maintenance mode)](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/); [MLflow #1902 file-store performance](https://github.com/mlflow/mlflow/issues/1902); [MLflow #4078 param length limit](https://github.com/mlflow/mlflow/issues/4078)

**Established literature (training knowledge, MEDIUM-HIGH):**
- López de Prado, *Advances in Financial Machine Learning* — purged/embargoed CV, sample-size correction (already reflected in spec.md)
- Bailey & López de Prado — Deflated Sharpe Ratio (recommended addition, Part 0)

---
*Pitfalls research for: crypto HFT/MFT ML trading research (Binance perpetual futures)*
*Researched: 2026-06-10*
