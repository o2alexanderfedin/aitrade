# Architecture Research

**Domain:** Short-horizon crypto futures ML trading research pipeline (two-stage: forecast → monetization policy in simulation)
**Researched:** 2026-06-10
**Confidence:** HIGH (overall structure — established quant-research patterns, verified against hftbacktest, López de Prado CV literature, Binance public data docs); MEDIUM on backfill specifics (see Integration Points)

## Standard Architecture

Systems of this type (research-grade microstructure ML, simulation-only monetization) converge on a **batch DAG of pure stages over immutable Parquet artifacts**, not a service architecture. Each stage reads versioned artifacts, writes versioned artifacts, and logs to MLflow. There is no long-running server except the capture daemon. The two design forces that dominate everything else are (1) **train/inference/sim parity** — one feature code path — and (2) **temporal leakage prevention** — the fold harness is a first-class component that owns time, not a utility function.

### System Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                      ACQUISITION (only "live" component)                │
│  ┌──────────────────┐              ┌──────────────────────────┐        │
│  │ Capture daemon   │              │ Backfill downloader      │        │
│  │ (WS: L1 + trades,│              │ (data.binance.vision     │        │
│  │  spot + swap)    │              │  zips → Parquet)         │        │
│  └────────┬─────────┘              └────────────┬─────────────┘        │
├───────────┴─────────────────────────────────────┴──────────────────────┤
│                RAW EVENT STORE  (Parquet, symbol/date, append-only,     │
│                 schema-versioned, manifest-addressed — never "latest")  │
├─────────────────────────────────────────────────────────────────────────┤
│  PREPROCESSING (offline, idempotent)                                    │
│  trade-side backfill (tradeSide=0 → nearest-quote) · quality filters    │
│  + audit log · resync tagging · spot/swap clock audit                   │
│         ↓ writes CURATED STORE (raw + corrected columns side by side)   │
├─────────────────────────────────────────────────────────────────────────┤
│  FEATURE/LABEL ENGINE  (single code path — numba streaming kernel)      │
│  state accumulates over EVERY row → emit on last row of each etime      │
│  labels = forward mid returns, computed only on decision rows           │
│         ↓ writes DECISION-ROW MATRIX (features + labels per etime)      │
├─────────────────────────────────────────────────────────────────────────┤
│  FOLD HARNESS  (owns time)                                              │
│  5-segment split Train_S1|Val_S1|Train_S2|Val_S2|Held-out + embargo     │
│  3-segment OOF fallback (purged+embargoed inner k-fold)                 │
│  segment manifests = etime ranges; selection-bias ledger; lockbox gate  │
├──────────────┬─────────────────────────────────┬───────────────────────┤
│ STAGE 1      │  FROZEN PREDICTOR INTERFACE     │ STAGE 2               │
│ 3 parallel   │  predict(X)→ŷ, deterministic,   │ event-driven sim      │
│ tracks:      │  versioned artifact per class;  │ (numba hot path:      │
│ regression / │  predictions PRECOMPUTED on     │  flip-only position   │
│ LightGBM /   │  Train_S2/Val_S2/Held-out       │  state machine) +     │
│ transformer  │  decision rows                  │ X-sweep (Optuna/CMA)  │
├──────────────┴─────────────────────────────────┴───────────────────────┤
│  EVALUATION & REPORTING                                                 │
│  per-fold IC/Sharpe ± std · regime splits · equity curves · trade log  │
│  side-by-side 3-class report · effective sample size · alpha-decay      │
├─────────────────────────────────────────────────────────────────────────┤
│  CROSS-CUTTING                                                          │
│  MLflow (code+data+seed+env hash, segment choice, budget ledger)        │
│  CI (per-feature information-set proofs, catalogue↔code sync,           │
│      train/infer byte-parity test, no-"latest" check)                   │
│  spec.md (feature/label catalogue is the contract CI enforces)          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| Capture daemon | Live WS subscription (bookTicker + trades, spot + swap), durable append, gap/resync tagging | asyncio + websockets, write-ahead JSONL → hourly Parquet rotation |
| Backfill downloader | Pull `data.binance.vision` daily/monthly zips, checksum-verify, normalize to the same schema as capture | requests + zip → polars → Parquet; idempotent by (symbol, date) |
| Raw event store | Immutable Parquet partitioned `symbol/date`, ns-int64 `etime`, schema version column, dataset manifest (hash) | polars scan over hive partitions; manifest JSON with content hashes |
| Preprocessing | Trade-side backfill (`tradeSide_corrected` alongside `tradeSide_raw`), crossed/locked-book filters with audit counts, resync warm-up tags | polars batch jobs, one output partition per input partition |
| Feature/label engine | The ONLY place feature math lives. Streaming state over every row; decision-row emission on last row per `etime`; rolling-only normalization; label forward windows | numba `@njit` kernel over numpy column arrays (extracted from polars); same kernel called by training prep, inference, and sim |
| Fold harness | Segment boundaries + embargo as data (manifests of etime ranges); 5-seg/3-seg choice + reason; purged+embargoed inner k-fold for OOF; lockbox access gate | small pure-python module; manifests logged to MLflow |
| Stage 1 trainers | One trainer per class (sklearn / LightGBM / PyTorch), same folds, same features, same metrics; capped HP search; multi-seed | per-class subpackage implementing a common `Trainer` protocol |
| Frozen predictor | Deterministic `predict(features) → ŷ` artifact; class-agnostic interface consumed downstream; predictions precomputed and stored per segment | saved model + loader; batch-predicts decision rows → Parquet prediction table |
| Simulator | Pure function: (decision rows: etime, bid, ask, ŷ; policy: X) → trade log + equity. Taker-only, zero fee/latency, $100 cap, flip-only | numba `@njit` sequential scan (path-dependent position state → cannot vectorize); hftbacktest is the pattern reference, not a dependency at MVP |
| Policy optimizer | Coarse X grid → black-box refine; knife-edge rejection (robust-region rule); fits on Train_S2 (or OOF), picks on Val_S2 | Optuna (or CMA-ES via `cmaes`); each trial = one sim call over precomputed predictions |
| Evaluation/reporting | Stage-1 IC suite, Stage-2 Sharpe suite, regime splits, per-fold ±std, side-by-side class report, equity overlays | polars + matplotlib; report template versioned; outputs logged as MLflow artifacts |
| MLflow tracking | Run manifest (code/data/seed/env hash), segment-split choice, selection-bias ledger, held-out "one look" annotation | local MLflow server or file store; thin wrapper module so every entrypoint logs uniformly |
| CI leakage tests | Shuffle-future invariance per feature; catalogue↔code sync; train-vs-inference byte parity; embargo ≥ horizon assertion; reject `latest` | pytest suite on small fixture datasets, runs on every PR |

## Recommended Project Structure

Everything under `mvp/` per the containment rule (mvp.md, spec.md move inside at Stage 0):

```
mvp/
├── mvp.md                  # moved in at Stage 0
├── spec.md                 # living spec — catalogue is the CI contract
├── configs/                # YAML/TOML: symbols, segment floors, HP budgets,
│                           #   capacity rules, retrain cadence — all declared up front
├── data/
│   ├── capture/            # WS daemon, rotation, gap/resync tagging
│   ├── backfill/           # data.binance.vision downloader, checksum, normalize
│   ├── schema.py           # canonical event schemas (L1, trade), ns-int64 etime
│   ├── store.py            # manifest-addressed Parquet read/write (no "latest")
│   └── quality.py          # filters + daily data-quality report
├── preprocess/
│   └── trade_side.py       # tradeSide=0 backfill (nearest-quote rule)
├── features/
│   ├── engine.py           # numba streaming kernel: state over every row,
│   │                       #   emit on last row per etime — THE single code path
│   ├── catalogue.py        # registry mirroring spec.md feature table (CI-checked)
│   └── normalize.py        # rolling/expanding stats, train-segment-only fit
├── labels/
│   └── returns.py          # ret_10s_mid + diagnostic horizons, forward windows
├── folds/
│   ├── splitter.py         # 5-segment + embargo; 3-segment fallback decision
│   ├── oof.py              # purged+embargoed inner k-fold OOF generation
│   └── ledger.py           # selection-bias budget, lockbox access gate
├── forecast/
│   ├── base.py             # Trainer protocol + FrozenPredictor interface
│   ├── regression/         # sklearn track
│   ├── trees/              # LightGBM track
│   ├── transformer/        # PyTorch track (causal mask + its CI test)
│   └── predict.py          # batch-predict decision rows → prediction Parquet
├── sim/
│   ├── engine.py           # numba flip-only position state machine
│   └── accounting.py       # P&L, fills, trade log, equity curve
├── monetization/
│   └── sweep.py            # X grid + Optuna/CMA-ES, knife-edge rejection
├── eval/
│   ├── stage1.py           # IC suite, calibration, regime splits, eff. sample size
│   ├── stage2.py           # Sharpe suite, turnover, drawdown, alpha-decay
│   └── report.py           # side-by-side 3-class version report
├── tracking/
│   └── mlflow_utils.py     # run manifest (code/data/seed/env hash), uniform logging
├── pipelines/              # thin entrypoints wiring stages: backfill→preprocess→
│   │                       #   features→folds→train→predict→sim→report
│   └── v0_smoke.py         # first end-to-end vertical slice
└── tests/
    ├── leakage/            # shuffle-future invariance, embargo, parity tests
    ├── fixtures/           # small synthetic event streams (crafted edge cases)
    └── ...                 # unit tests per module
```

### Structure Rationale

- **`features/` is a chokepoint by design:** no model track or sim may compute its own features; the spec's production–research-drift mitigation ("single feature-pipeline code path") only holds if the structure makes violations obvious in review.
- **`folds/` is a top-level peer, not a util:** segment manifests + selection-bias ledger are the anti-leakage backbone; both Stage 1 and Stage 2 consume from it, neither defines its own time splits.
- **`forecast/` and `monetization/` never import each other:** they communicate only through prediction Parquet tables keyed by segment manifest — this makes "Stage 2 fits only on out-of-Stage-1-train predictions" structurally enforceable (the prediction table for Train_S2 simply does not exist for in-sample data).
- **`pipelines/` holds orchestration, modules hold logic:** every stage callable standalone (re-runnable, idempotent), enabling the agentic loop to re-run only the affected stage.

## Architectural Patterns

### Pattern 1: Single streaming feature kernel (numba) shared by train, inference, and sim

**What:** Feature math lives in one numba `@njit` function that scans event rows sequentially, mutating a state struct (rolling windows, EMAs, OFI accumulators) on *every* row and emitting a feature vector only on the last row of each `etime` group. Training feature-matrix generation calls it in batch over history; the simulator calls the same kernel inline.
**When to use:** Always, here — it is the spec's mandated mitigation for production–research drift, and the per-etime emission rule cannot be expressed cleanly in pure columnar polars ops once state windows span rows.
**Trade-offs:** Numba kernels are harder to debug than polars expressions and restrict you to numpy dtypes; but a polars-batch implementation for training plus a separate streaming one for sim is exactly the dual-code-path anti-pattern the spec forbids. Use polars for I/O, partition handling, and joins; drop to numpy arrays at the kernel boundary.

**Example:**
```python
@numba.njit
def run_features(etime, bid, ask, bid_sz, ask_sz, trade_px, trade_sz, side, out):
    state = init_state()
    j = 0
    for i in range(len(etime)):
        update_state(state, bid[i], ask[i], ...)      # EVERY row
        is_last = (i == len(etime) - 1) or (etime[i + 1] != etime[i])
        if is_last:                                    # decision row only
            emit(state, out, j); j += 1
    return j
```

### Pattern 2: Frozen predictor + precomputed prediction tables

**What:** After Stage 1 HP pick on Val_S1, each class is frozen into a deterministic artifact. Predictions for Train_S2, Val_S2, and Held-out decision rows are computed **once, offline**, and stored as Parquet (`etime, symbol, ŷ, predictor_id, segment_manifest_id`). The Stage 2 sweep and the simulator consume these tables, never the model objects.
**When to use:** Whenever the policy cannot influence the market — true here (zero impact, taker-only, book not updated by our fills), so predictions are independent of policy and the entire X sweep reduces to re-scanning a fixed array. A 50 ms-inference transformer thus costs nothing during the sweep.
**Trade-offs:** Breaks if a later realism step makes features depend on our own fills (queue position, impact) — then the sim must call the predictor in the loop. Design `sim.engine` to accept a prediction *array* now, with the predictor-in-the-loop variant as a documented future extension, not a premature abstraction.

### Pattern 3: Fold harness as data contract (segment manifests)

**What:** The splitter emits **manifests** — explicit `[start_etime, end_etime)` ranges per segment with embargo gaps materialized — rather than splitting DataFrames. Every downstream artifact (feature matrix slice, prediction table, sim result, MLflow run) carries the manifest ID. The 5-seg vs 3-seg choice is a field in the manifest with its recorded reason.
**When to use:** Always — it makes the leakage audit ("every Stage-2 input proven out-of-Stage-1-train") a join check between artifact metadata, instead of code archaeology.
**Trade-offs:** Slight ceremony; pays for itself the first time an audit or the agentic loop needs to verify provenance mechanically.

### Pattern 4: Manifest-addressed immutable datasets

**What:** Every Parquet dataset (raw, curated, decision-row matrix, predictions) gets a content-hashed manifest; pipelines reference manifest IDs, never paths-with-`latest`. MLflow run records data hash = manifest ID.
**When to use:** From the first byte captured. Retrofitting versioning onto a mutable store is the classic reproducibility failure (spec DON'T: never `latest`).
**Trade-offs:** Storage grows (curated copies alongside raw); at L1+trades for 1–3 symbols × 3 months this is tens of GB — trivially acceptable.

### Pattern 5: Simulator as a pure function with a numba core

**What:** `simulate(decision_rows, predictions, X) → (trade_log, equity_curve)`. No I/O, no MLflow, no global state inside. The flip-only position logic is a sequential state machine (path-dependent: current position gates which signals act), so it is a numba scan, not a vectorized backtest. hftbacktest validates this exact shape (numba JIT event loop over tick data) at far higher fidelity than MVP needs — at zero-latency/zero-fee/taker-only, a custom ~200-line kernel is simpler than adopting hftbacktest, but its design (and its latency/queue models) is the reference for post-MVP realism steps.
**When to use:** Always. Purity makes the sweep embarrassingly parallel across X and makes property tests (e.g., "position never exceeds $100 notional", "long can only be followed by flat-via-flip-to-short") trivial.
**Trade-offs:** None at MVP scale; revisit only when fills must mutate book state.

## Data Flow

### End-to-end pipeline flow (one walk-forward fold, 5-segment)

```
Binance WS ──► capture ──┐
data.binance.vision ─────┴─► RAW Parquet (symbol/date, manifest M_raw)
        ↓ preprocess (trade-side backfill, quality, resync tags)
   CURATED Parquet (M_cur)
        ↓ feature/label engine (every row → state; last-row-per-etime → emit)
   DECISION-ROW MATRIX (M_feat): etime | features… | ret_10s_mid | aux (bid, ask)
        ↓ fold harness: manifest {Train_S1|emb|Val_S1|emb|Train_S2|emb|Val_S2|emb|Held-out}
        ├─► Stage 1: fit on Train_S1 ──► HP pick on Val_S1 ──► FROZEN predictor (per class)
        │        ↓ batch predict
        │   PREDICTION TABLES on Train_S2 / Val_S2 / Held-out (M_pred, per class)
        └─► Stage 2: sim(M_feat ⋈ M_pred on Train_S2, X) per trial
                 ↓ X picked on Val_S2 (knife-edge check)
            LOCKED pipeline (predictor + X)
                 ↓ one shot per gate
            HELD-OUT: end-to-end sim → trade log, equity, Sharpe
        ↓
   EVAL REPORT (side-by-side 3 classes) ──► MLflow (hashes, ledger, artifacts)
```

3-segment fallback changes only the upper-middle: Stage 1 produces purged+embargoed inner-k-fold **OOF predictions over Train**, Stage 2 fits on those; Stage 1 then refits on all of Train; joint pick on Val. Same artifacts, different manifest type — downstream (sim, eval, reporting) is unchanged. This is the standard stacking/meta-model OOF discipline (López de Prado meta-labeling lineage).

### Key Data Flows

1. **Time-alignment flow:** raw events (all rows) → feature state updates (all rows) → emissions (last row per `etime` only). Labels and sim decisions exist *only* at emission rows. Aux market columns (best bid/ask at emission) ride along in the decision-row matrix so the sim never re-reads raw data.
2. **Anti-leakage flow:** Stage 2 inputs are joins against prediction tables whose manifests reference only Train_S2/Val_S2/Held-out (or OOF) ranges. In-sample Stage-1 predictions are never materialized for Stage 2 — absence of the artifact is the enforcement.
3. **Audit flow:** every artifact carries `(code_hash, manifest_id, seed, env_hash)`; CI + MLflow ledger close the loop (selection-bias looks, lockbox one-look annotation).

## Suggested Build Order (dependency-driven)

The governing principle: **thinnest end-to-end vertical slice first** (v0 = single symbol, linear regression, fixed X), then deepen components in parallel. Two long poles — data accumulation (capture must run for weeks to build recent L1 history) and the transformer track (GPU, slow iteration) — should start early and run in the background.

| Order | Component | Depends on | Notes |
|-------|-----------|------------|-------|
| 0 | Stage 0: spec.md seed + catalogue CI wiring + repo skeleton under `mvp/` | — | Mandated prerequisite; CI harness here is reused by all later leakage tests |
| 1 | Data schemas + manifest store (`data/schema.py`, `store.py`) | 0 | Everything downstream depends on the schema; freeze ns-int64 etime convention here |
| 2a | Capture daemon | 1 | **Start immediately** — recent L1 history only accumulates in real time (backfill L1 likely unavailable; see Integration Points) |
| 2b | Backfill downloader (trades/aggTrades; bookTicker if present) | 1 | Parallel with 2a |
| 3 | Preprocessing (trade-side backfill, quality report) | 2 | Needs real data samples to validate the nearest-quote rule |
| 4 | Feature/label engine + per-feature CI leakage tests | 1 (+3 for real data; fixtures suffice to start) | Build against synthetic fixtures first — don't block on data volume |
| 5 | Fold harness (splitter, embargo, ledger; OOF later) | 1 | Pure logic; testable on fixtures. OOF module can land at step 9 |
| 6 | Simulator + accounting | 1, 4 schema | **Parallel with 4/5** — testable with synthetic/oracle predictions before any model exists; oracle-prediction sim also validates decision logic and gives a P&L upper bound |
| 7 | Stage 1 regression track + frozen-predictor interface + prediction tables | 4, 5 | Regression first: fastest wiring, exposes interface problems cheaply; define `base.py` protocol here |
| 8 | v0 smoke pipeline end-to-end (fixed X) + MLflow run manifest + report v0 | 6, 7 | First gate; lock held-out window here |
| 9 | Trees track; transformer track; OOF fallback module | 7, 8 | Trees ≈ days; transformer is the long pole — start its scaffolding during 7 |
| 10 | Stage 2 X-sweep (Optuna/CMA-ES, knife-edge rejection) | 6, 8 | v1 gate |
| 11 | Full eval/report suite (regime splits, eff. sample size, alpha-decay, side-by-side) | 8 | Grows incrementally from v0 report |
| 12 | Multi-symbol / universal extensions | 8–11 | v2/v3; mostly config + per-symbol partitioning if 1–5 were built symbol-parameterized from the start |

**Critical-path warning:** if `data.binance.vision` futures bookTicker is indeed stale (see below), usable L1 history = capture uptime. Step 2a is the schedule-defining task; everything else can be built on fixtures and small samples while history accumulates.

## Anti-Patterns

### Anti-Pattern 1: Dual feature implementations (batch for training, streaming for sim)

**What people do:** Write features as polars/pandas batch expressions for training, then re-implement them as incremental updates inside the backtest loop "for speed."
**Why it's wrong:** The two paths drift silently (window edge handling, NaN policy, etime grouping); reported skill stops matching simulated skill. This is the spec's production–research drift pitfall, and it is the most common defect in homegrown research stacks.
**Do this instead:** One numba streaming kernel (Pattern 1); CI byte-parity test: same input events → identical feature values via the training prep path and the sim path.

### Anti-Pattern 2: Vectorized backtest of a path-dependent policy

**What people do:** `signal.shift(1) * returns` style vectorized P&L.
**Why it's wrong:** Flip-only logic is stateful — whether a signal acts depends on current position; vectorization either ignores the constraint or smuggles in look-ahead via misaligned shifts.
**Do this instead:** Sequential numba scan (Pattern 5). It is still fast enough to sweep hundreds of X values over millions of decision rows in seconds.

### Anti-Pattern 3: Stage 2 fed by in-sample Stage 1 predictions

**What people do:** Fit the threshold on predictions over Stage 1's own training range "just to get started," intending to fix it later.
**Why it's wrong:** In-sample forecasts are optimistically sharp; X tunes to forecast quality that won't exist out-of-sample — the silent leakage path mvp.md explicitly forbids. "Later" never comes because the inflated Sharpe looks like success.
**Do this instead:** Never materialize in-sample prediction tables (Pattern 3); the artifact's absence enforces the rule. OOF generation for the 3-segment fallback.

### Anti-Pattern 4: Fold splitting as a per-script utility

**What people do:** Each trainer/notebook computes its own date splits with ad-hoc embargo handling.
**Why it's wrong:** Embargo bugs (off-by-one day, embargo < horizon, label forward-window overlap) are invisible per-script and invalidate every result; the selection-bias ledger can't count looks it can't see.
**Do this instead:** One fold harness emitting manifests; every data access goes through manifest IDs; lockbox reads gated through `folds/ledger.py`.

### Anti-Pattern 5: Full-period normalization statistics

**What people do:** z-score features with mean/std over the whole dataset before splitting.
**Why it's wrong:** Test-period statistics leak into training features — classic, subtle, and fully eliminated by construction.
**Do this instead:** Rolling/expanding stats inside the feature kernel, or fit normalizers on the training segment only via the fold manifest (spec DO).

### Anti-Pattern 6: Adopting a heavyweight backtesting framework at MVP

**What people do:** Build on hftbacktest/NautilusTrader from day 1 for "future realism."
**Why it's wrong:** Those frameworks earn their complexity through latency, queue-position, and fill modeling — all explicitly out of MVP scope. Their data formats and event loops would dictate the architecture, and the single-feature-code-path mandate is harder to satisfy inside someone else's loop.
**Do this instead:** ~200-line custom numba kernel now; keep hftbacktest as the design reference for the post-MVP latency/queue realism steps (it is the strongest open-source implementation of exactly that roadmap).

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Binance WS (spot + USD-M futures) | Capture daemon: `bookTicker` + `trade`/`aggTrade` streams, durable raw log, reconnect + resync tagging | Primary L1 source. Sequence-gap detection feeds the resync warm-up convention in spec.md |
| data.binance.vision | Daily/monthly zips, `.CHECKSUM` verification, normalize to capture schema | Trades/aggTrades/klines confirmed available for spot + futures. **Futures `bookTicker` dumps exist in the listing but are reported stale (no updates since ~2024)** — verify empirically in step 2b; plan as if L1 backfill is unavailable (MEDIUM confidence) |
| MLflow | Thin wrapper (`tracking/mlflow_utils.py`); every pipeline entrypoint logs run manifest, segment manifest, ledger events, report artifacts | Local file-store backend is sufficient; the wrapper is what makes hash logging uniform and un-skippable |
| Optuna / CMA-ES | Objective = pure sim call over precomputed predictions; study results logged to MLflow per trial (each trial = one ledger "look" on its segment) | Coarse grid first per mvp.md; black-box refine only inside a robust region |
| CI (GitHub Actions or equivalent) | pytest leakage suite + catalogue sync + parity test + `latest`-rejection on every PR | Stage 0 deliverable; spec.md update check for feature/label-touching PRs |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| acquisition ↔ preprocessing | Parquet partitions + manifest | Append-only; preprocessing never mutates raw |
| preprocessing ↔ feature engine | Curated Parquet (raw + corrected columns) | Feature engine reads `tradeSide_corrected`, may use confidence as a feature channel |
| feature engine ↔ everything downstream | Decision-row matrix (features, labels, aux bid/ask) | The only feature source; sim and trainers both consume it |
| folds ↔ Stage 1 / Stage 2 / eval | Segment manifests (etime ranges) | No DataFrame splitting outside the harness |
| forecast ↔ monetization | Prediction Parquet tables keyed by (predictor_id, segment manifest) | **No imports between the packages** — the anti-leakage boundary |
| sim ↔ policy optimizer | Pure function call: arrays in, trade log out | Keeps sweep parallel and property-testable |
| all ↔ tracking | Wrapper module | Single place where hashes/ledger are computed; prevents forgotten logging |

## Scaling Considerations

Scale here is data volume per backtest, not users.

| Scale | Architecture Adjustments |
|-------|--------------------------|
| v0–v1: 1 symbol × ~3 months L1+trades (order 10⁸–10⁹ raw rows; BTCUSDT futures bookTicker alone can run tens of millions of updates/day) | Per-day streaming: feature kernel processes one `symbol/date` partition at a time, carrying state across partition boundaries; never load 3 months of raw into RAM. Decision-row matrix is much smaller (one row per distinct etime) and can live in memory for training |
| v2: +ETH, +2nd-tier | Symbol-parameterized pipelines (config lists), per-symbol partitions; embarrassingly parallel per symbol — no architecture change if built symbol-keyed from step 1 |
| v3: universal model (all perps) | Stacked decision-row matrices with symbol id column; per-class handling (one-hot / embedding / cross-asset attention) lives inside `forecast/` tracks; data store and fold harness unchanged. Capture fan-out (all perps × 2 streams) may need connection pooling/sharding in the daemon |
| Post-MVP realism (latency, queue, impact) | Sim grows toward hftbacktest's shape: predictor-in-the-loop option, latency models, book mutation. This is the one boundary worth a forward-looking seam now (sim accepts prediction array OR callable), nothing more |

### Scaling Priorities

1. **First bottleneck — Stage 1 feature-matrix generation over raw events:** mitigated by the numba kernel + per-partition streaming; cache decision-row matrices by (M_cur, feature-set version) so trainers never re-scan raw data.
2. **Second bottleneck — transformer training wall-clock (and k× under OOF fallback):** bound by Q3 (GPU spec); mitigate with capped context length, capped HP trials (already mandated), and starting the track early in the build order.

## Sources

- [hftbacktest (GitHub)](https://github.com/nkaz001/hftbacktest) and [docs](https://hftbacktest.readthedocs.io/en/latest/reference/backtester.html) — numba JIT event-driven backtester for Binance futures L1/L2; reference for sim architecture and post-MVP latency/queue modeling. HIGH confidence.
- [binance-public-data (official GitHub)](https://github.com/binance/binance-public-data) and [data.binance.vision listing](https://data.binance.vision/?prefix=data%2Ffutures%2Fum%2Fdaily%2FbookTicker%2FBTCUSDT%2F) — confirmed structure (market/frequency/datatype/symbol, daily+monthly, checksums); trades/aggTrades/klines documented for futures; bookTicker folders exist but absent from official docs. HIGH confidence on structure.
- [Binance dev forum: historical bookTicker availability](https://dev.binance.vision/t/how-and-where-can-i-get-the-history-bookticker-data/36122/1) — reports futures bookTicker dumps no longer updated (last ~2024). MEDIUM confidence (forum, single source) — flagged for empirical verification in the backfill step; capture-first design recommended regardless.
- [Purged cross-validation (Wikipedia)](https://en.wikipedia.org/wiki/Purged_cross-validation) and [Advances in Financial ML notes](https://reasonabledeviations.com/notes/adv_fin_ml/) — purging/embargo/OOF discipline underlying the fold harness. HIGH confidence (established literature, López de Prado).
- [Meta-labeling two-stage pattern](https://dev.to/nydartrading/meta-labeling-filtering-bad-trades-before-they-happen-2jbn), [scikit-learn stacking with OOF](https://scikit-learn.org/stable/auto_examples/ensemble/plot_stack_predictors.html) — the OOF-fed second-stage pattern matching the 3-segment fallback. MEDIUM-HIGH confidence.
- Project documents: `mvp.md`, `spec.md`, `.planning/PROJECT.md` — authoritative constraints (time alignment, 5-segment split, containment rule, stack mandates).

---
*Architecture research for: short-horizon crypto futures ML trading research pipeline (two-stage forecast → monetization)*
*Researched: 2026-06-10*
