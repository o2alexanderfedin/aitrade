# Stack Research

**Domain:** Crypto HFT/MFT ML research pipeline — Binance perpetual futures midprice forecasting + simulated monetization
**Researched:** 2026-06-10
**Confidence:** HIGH (core versions verified against PyPI 2026-06-10; data availability verified directly against data.binance.vision S3 and Binance API docs)

> **Constraint honored:** project mandates polars + numpy + numba (NO pandas), PyTorch, LightGBM, scikit-learn, MLflow, Parquet partitioned by symbol/date, nanosecond int64 timestamps. Nothing below contradicts these. This document pins current versions, fills the gaps (capture, backfill, HPO, purged CV, testing/CI), and flags compatibility gotchas.

## Critical Findings First (affect the roadmap)

1. **No L1 backfill exists for the recent 3 months on data.binance.vision.** Verified directly against the S3 bucket on 2026-06-10:
   - **Spot:** only `aggTrades`, `klines`, `trades` — **no bookTicker/L1 dataset has ever existed for spot**.
   - **USDS-M futures:** `bookTicker` daily dumps exist **only from 2023-05-16 to ~2024-03** (HEAD probes: 2024-03-01 → 200, 2024-04-01 → 404, everything after → 404). The dataset was discontinued.
   - **futures `bookDepth`** is current but is aggregate depth at ±1–5% from mid sampled ~every 27–30s (inspected 2026-06-01 file: `timestamp,percentage,depth,notional`, 31,537 rows/day) — useless as L1.
   - **Consequence:** the mvp.md plan "~3 months history (capture + data.binance.vision backfill)" only works for **trades**. L1 history must come from (a) **own capture started in week 1** (every day of capture = a day of training data), and/or (b) a vendor — **Tardis.dev** is the standard source for historical Binance spot + futures quotes/L2 (per-message granularity, back to 2019, paid). Roadmap implication: the capture daemon is the very first deliverable, before anything else.
2. **Spot `bookTicker` websocket stream carries NO timestamp at all** (payload is `u, s, b, B, a, A` — verified in official spot websocket docs). "`etime` is the only clock" is **unsatisfiable for spot L1**; the capture layer must record local receive time for spot L1 and the spec must document this exception. Futures bookTicker does carry `E` (event time) and `T` (transaction time), both **milliseconds**.
3. **Binance timestamps are milliseconds (futures) / optionally microseconds (spot, `timeUnit=MICROSECOND` URL param).** Nanosecond int64 storage is fine (`ms × 1_000_000`), but many events share one `etime` — the spec's "decide on last row of each `etime`" rule is load-bearing, not an edge case.
4. **Trades backfill from data.binance.vision includes `is_buyer_maker`** (verified file header: `id,price,qty,quote_qty,time,is_buyer_maker`), so trade side is **exact** for backfilled trades — the nearest-quote `tradeSide = 0` reclassification applies only to self-captured legacy rows, not to the bulk-download path.

## Recommended Stack

### Core Technologies (mandated — versions verified on PyPI 2026-06-10)

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.13 (3.12 fallback) | Runtime | All core libs publish 3.13 wheels (numba classifiers: 3.10–3.14; torch: 3.10–3.14; polars: ≤3.13; scikit-learn requires ≥3.11). 3.13 is the mainstream 2026 choice; avoid 3.14 until polars publishes 3.14 classifiers |
| polars | 1.41.2 | DataFrames, Parquet IO, feature pipelines | Mandated. Native hive-partitioned writes (`write_parquet(partition_by=["symbol","date"])`, zstd default), `Datetime("ns")` time unit, lazy/streaming engine for >RAM scans |
| numpy | 2.4.6 | Array math, numba interchange | Mandated. **Compatible with numba 0.65.1 (requires numpy <2.5)** — do not let the lockfile drift to numpy 2.5+ until numba catches up |
| numba | 0.65.1 | JIT for simulator hot path | Mandated. Pins `numpy>=1.22,<2.5` and `llvmlite>=0.47,<0.48` — the single most fragile pin in the stack; manage with uv lockfile |
| PyTorch (torch) | 2.12.0 | Transformer track | Mandated. CUDA wheels via the pytorch.org index (`--index-url https://download.pytorch.org/whl/cu128`); CPU wheel fine for CI |
| LightGBM | 4.6.0 | Trees track | Mandated default for trees. Note: 4.6.0 released 2025-02 — slow but stable release cadence; accepts numpy arrays directly (no pandas needed) |
| scikit-learn | 1.9.0 | Regression track, metrics | Mandated. Requires Python ≥3.11. Use only `numpy`-array APIs (never feed it DataFrames) to keep the no-pandas rule airtight |
| MLflow | 3.13.0 | Experiment tracking | Mandated. MLflow 3.x line (3.0 landed mid-2025); use local `mlruns/` + SQLite backend at MVP scale; log code hash + data hash + seed + env hash as run tags |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| websockets | 16.0 | Capture daemon: raw Binance combined streams | Always — own the wire format; subscribe `btcusdt@bookTicker/btcusdt@trade` combined streams, write raw messages + local recv ns timestamp |
| orjson | 3.11.9 | JSON decode in capture hot loop | Always — 5–10× faster than stdlib `json`, returns ints as ints (no float mangling of IDs) |
| aiohttp | 3.14.1 | data.binance.vision bulk downloader, REST snapshots | Backfill phase — daily zip downloads are plain HTTPS; no SDK needed |
| Optuna | 4.9.0 | Black-box HPO: Stage-1 capped searches, Stage-2 X sweep | Always — one framework for both `GridSampler` (coarse X sweep), `TPESampler` (default), and `CmaEsSampler` |
| cmaes | 0.13.0 | CMA-ES backend for Optuna's `CmaEsSampler` | Installed as Optuna optional dep; standalone `cma` (pycma) not needed — Optuna unifies trial logging |
| optuna-integration | 4.9.0 | `MLflowCallback` — every Optuna trial → MLflow run | Always — makes the selection-bias budget countable (trials are first-class MLflow rows) |
| statsmodels | 0.14.6 | Newey–West/HAC standard errors, Ljung–Box | Eval/reporting — spec mandates HAC and autocorrelation tests; works on numpy arrays (don't pass it DataFrames) |
| scipy | 1.17.1 | Rank IC (spearmanr), block bootstrap helpers, stats | Eval/reporting |
| matplotlib | 3.10.9 | Equity curves, alpha-decay plots, MLflow artifacts | Reporting — log figures as MLflow artifacts |
| zstandard | 0.25.0 | Compress raw capture NDJSON archives | Capture — raw-message archive alongside Parquet (replayability/audit) |

**Deliberately absent:** pyarrow. polars' native Rust Parquet writer covers partitioned writes, zstd, statistics, and ns timestamps — pyarrow is only needed if you later want `pyarrow.dataset` scanning tricks polars can't do (it can, today). One less heavyweight dependency.

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| uv 0.11.x | Package/env management, lockfile | The 2026 standard. The numba↔numpy↔llvmlite triple-pin makes a real lockfile non-negotiable; `uv lock` + `uv sync --frozen` in CI |
| ruff 0.15.x | Lint + format (replaces black/isort/flake8) | Add `lint.flake8-tidy-imports.banned-api` rule banning `pandas` imports — enforce the no-pandas rule mechanically in CI |
| pytest 9.x + pytest-cov | Test runner | CI gates: feature information-set proofs, train/infer/sim single-code-path byte-equivalence test |
| hypothesis 6.x | Property-based testing | Ideal for the spec's CI leakage proofs: "shuffling future rows must not change feature values at t", embargo ≥ horizon invariants, last-row-per-etime decision invariants |
| mypy 2.x (or pyright) | Type checking | numba-decorated functions need `# type: ignore` shims or a stubs layer; don't fight it — exclude the `@njit` hot path module from strict mode |
| pre-commit 4.x | Hook runner | ruff + mypy + "spec.md touched if features/labels touched" custom hook |
| GitHub Actions | CI | CPU jobs for regression/trees/sim tests; transformer smoke test on CPU (1 batch); GPU runs stay local/cloud |

## Installation

```bash
# Project init (uv manages Python too)
uv init mvp && cd mvp
uv python pin 3.13

# Core (mandated)
uv add "polars==1.41.*" "numpy==2.4.*" "numba==0.65.*" \
       "lightgbm==4.6.*" "scikit-learn==1.9.*" "mlflow==3.13.*"

# PyTorch with CUDA (adjust cu version to the chosen GPU; Q3 in mvp.md)
uv add "torch==2.12.*" --index https://download.pytorch.org/whl/cu128

# Capture + backfill
uv add "websockets==16.*" "orjson==3.11.*" "aiohttp==3.14.*" "zstandard==0.25.*"

# HPO + eval
uv add "optuna==4.9.*" "cmaes==0.13.*" "optuna-integration[mlflow]==4.9.*" \
       "statsmodels==0.14.*" "scipy==1.17.*" "matplotlib==3.10.*"

# Dev
uv add --dev "pytest==9.*" "pytest-cov" "hypothesis==6.*" "ruff==0.15.*" "mypy" "pre-commit"
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Raw `websockets` capture daemon | cryptofeed 2.4.1 | Never for this project — cryptofeed normalizes timestamps to **float seconds** (breaks the int64-ns convention), adds an abstraction layer between you and the exchange payload, and its normalization hides fields. Fine for casual multi-exchange work; wrong for a capture layer whose raw bytes are the source of truth |
| Raw `websockets` capture daemon | binance-futures-connector 4.2.0 / new modular `binance-sdk-*` | Use the official connector only for occasional REST calls (exchange info, snapshots). Its websocket wrapper adds thread/callback machinery you don't need; the raw stream protocol is trivial |
| Plain aiohttp downloader for data.binance.vision | `binance-public-data` repo scripts | The official scripts are pandas-era and clunky; the bucket is plain HTTPS zips with a checksums file — a 100-line async downloader with checksum verification is cleaner and testable |
| Optuna (`GridSampler`/`TPESampler`/`CmaEsSampler`) | standalone pycma (`cma`) | Only if you outgrow Optuna's CMA-ES (e.g., need restarts/BIPOP control). At MVP, Stage 2 has ~1–2 params — Optuna `GridSampler` first (spec mandates coarse grid + robust-region check), CMA-ES later |
| In-house purged+embargoed CV (~150 LOC on int64 ns) | skfolio (`CombinatorialPurgedCV`, `WalkForward`), timeseriescv, mlfinlab | skfolio is sklearn-compatible but pandas-based (violates the ban transitively); timeseriescv is unmaintained; mlfinlab is paywalled (Hudson & Thames license). The project's 5-segment split + embargo + lockbox + 3-segment OOF fallback is custom anyway — no library implements it. Write it once, property-test it with hypothesis |
| LightGBM | CatBoost / XGBoost | Already named as alternates in mvp.md; bring in only if LightGBM's track stalls — same numpy-array API style, swap is cheap |
| MLflow local backend | W&B, Aim | Only if multi-user/hosted tracking becomes a need. MLflow is mandated and its run-tag model fits the code-hash/data-hash/seed/env-hash manifest |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| pandas (also transitively) | Mandated ban; index/dtype landmines, slower on these workloads. Beware **transitive** pulls: skfolio, cryptofeed, the official binance-public-data scripts, some statsmodels convenience APIs | polars + numpy; CI ruff rule banning `import pandas` |
| numpy 2.5+ (when released) | numba 0.65.1 hard-pins `numpy<2.5`; an unpinned upgrade breaks every `@njit` import | Pin `numpy==2.4.*` in uv lock until numba's support table moves |
| `pl.Datetime` math in the numba hot path | numba's nopython mode has poor datetime64 support; mixed-unit math is the exact landmine the ns-int64 convention exists to avoid | Pass raw `int64` ns columns (`.to_numpy()`) into `@njit` kernels |
| cryptofeed for capture | Float-seconds timestamps lose sub-ms ordering; normalization layer between you and raw payloads | Raw `websockets` + orjson, archive raw messages |
| data.binance.vision as L1 source | Spot L1 never existed there; futures bookTicker dumps end ~2024-03 (verified) | Own capture from day 1 + Tardis.dev for historical L1 if 3 months of history is needed before 3 months of capture elapse |
| `latest` data versions / unpinned deps in pipelines | Spec DON'T; silent run-to-run drift | uv lockfile, content-hashed Parquet manifests, data hash in MLflow tags |
| Binance aggTrades as the primary trade tape | Aggregation hides intra-window flow (spec pitfall: "agg-trade aggregation hiding intra-window flow") | `trades` daily dumps + `@trade` stream; aggTrades only as a cross-check |

## Stack Patterns by Variant

**If historical L1 before capture-start is required (likely, given 1–3 month timeline):**
- Buy Binance spot + USDS-M futures quote (bookTicker) history from Tardis.dev; their datasets are per-message with exchange + local timestamps.
- Because data.binance.vision cannot supply it (verified above), and 3 months of own capture pushes MVP exit to the right by the same 3 months.

**If GPU is remote/cloud (Q3 unresolved):**
- Keep MLflow tracking URI on a shared volume or a small tracked server; train transformer with `torch.compile` off first (correctness), on later (speed).
- Because run-manifest reproducibility (code/data/seed/env hash) must survive machine hops.

**If single-symbol data starves the 5-segment split (mvp.md fallback):**
- The in-house CV module must implement both 5-segment and compressed 3-segment+OOF from one config object, recorded per-run in MLflow.
- Because the choice is a per-run, logged decision in the spec — not a code fork.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| numba 0.65.1 | numpy ≥1.22, <2.5; llvmlite ≥0.47, <0.48 | **The load-bearing pin.** numpy 2.4.6 OK today; numba historically lags new numpy minors by ~1–2 quarters |
| numba 0.65.1 | Python 3.10–3.14 | Classifiers verified; 3.13 safe |
| scikit-learn 1.9.0 | Python ≥3.11 | Drops 3.10 — another reason to standardize on 3.13 |
| polars 1.41.2 | Python 3.10–3.13 | 3.14 classifier absent — stay on 3.13 |
| torch 2.12.0 | Python 3.10–3.14; CUDA cu126/cu128/cu130 wheels | Pick CUDA build when Q3 (GPU spec) resolves |
| polars ↔ numba | via `.to_numpy()` on int64/float64 columns | Zero-copy for non-null numeric columns; nulls force a copy + float cast — keep hot-path columns non-nullable by construction |
| polars `write_parquet` | partition_by hive writes, zstd default, ns timestamps | Verified in polars source via Context7; no pyarrow needed (`use_pyarrow=False` default) |
| Optuna 4.9.0 ↔ cmaes 0.13.0 | `CmaEsSampler` requires `cmaes` package | Install explicitly; Optuna treats it as optional |
| MLflow 3.13.0 | Optuna via optuna-integration 4.9.0 `MLflowCallback` | Trial→run mapping; nest under a parent run per study |
| Binance futures ws `E`/`T` | int64 ns storage | ms precision → multiply by 1_000_000; many rows share one etime (spec's last-row rule handles) |
| Binance spot `bookTicker` ws | — | **No timestamp field at all**; local recv-time clock required for spot L1 (spec exception needed) |

## Sources

- PyPI JSON API (2026-06-10) — all version numbers and `requires_dist` pins above (HIGH)
- data.binance.vision S3 bucket listing + HTTP HEAD probes (2026-06-10) — spot/futures dataset availability, futures bookTicker cutoff between 2024-03-01 and 2024-04-01, bookDepth format from downloaded 2026-06-01 file (HIGH, primary source)
- https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams — spot bookTicker payload has no timestamp; `timeUnit=MICROSECOND` option; ms default (HIGH)
- https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Individual-Symbol-Book-Ticker-Streams — futures bookTicker `E`/`T` ms fields, real-time update speed (HIGH)
- Context7 `/pola-rs/polars` — `write_parquet` signature: `partition_by`, `compression="zstd"` default, `use_pyarrow=False` default (HIGH)
- numba PyPI metadata — `numpy<2.5`, `llvmlite<0.48` pins, Python 3.10–3.14 classifiers (HIGH)
- cryptofeed float-seconds timestamp normalization — training data + repo familiarity, not re-verified against current source (MEDIUM; verify before ruling it out if someone proposes it)
- Tardis.dev as the standard historical L1/L2 crypto vendor — ecosystem knowledge, coverage claims not re-verified this session (MEDIUM; verify coverage/pricing in the data-phase research)

---
*Stack research for: crypto HFT/MFT ML research pipeline (Binance perpetual futures)*
*Researched: 2026-06-10*
