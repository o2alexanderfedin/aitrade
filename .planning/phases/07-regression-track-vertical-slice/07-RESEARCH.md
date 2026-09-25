# Phase 7: Regression Track & Vertical Slice - Research

**Researched:** 2026-09-24
**Domain:** scikit-learn linear regression over a purged walk-forward harness; frozen predictors; prediction tables; the first end-to-end Stage-1 slice
**Confidence:** **HIGH (measured)** for everything checked against this repo and this data pool — manifest geometry, admission counts, ceilings, memory, timings, target statistics, the accessor/simulator contracts, the guardrail requirements and the MLflow run structure. **MEDIUM** for exactly two things: scikit-learn's *runtime* behaviour (sklearn and scipy are NOT installed — every sklearn claim is read from the 1.9.1 source or PyPI metadata, never executed; §Q3a) and the Trainer Protocol (§Q5 — a design proposal, not a measurement). **Read the Assumptions Log before treating §Q3 or §Q5 as settled.**

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-07-01.** A NEW segment manifest is issued over the full 7-day pool
  (2026-09-12..18), layout `compressed_3seg`, via the existing
  `harness.segments.issue_segment_manifest`. The committed 3-day manifest
  `97964cb27f62aa07201e2e52f788240d4f7801582c7cac25ac21107aa9e330e2` is NOT
  reused and NOT modified — it stays an untouched reference with 0 looks spent.
  Measured at discuss time: `budget.look_count` returns 0 for `val` and all five
  `oof_block_*` of that manifest, so no window is yet poisoned.
- **D-07-02.** `layout = "compressed_3seg"`, not `5seg`, even though 7 days would
  now support five segments. Reason: a `5seg` manifest declares a real held-out
  segment, and declaring + locking held-out is Phase 8's success criterion 4.
  `fold_config_reason` must say exactly this. The inner k-fold OOF blocks are also
  what the model sweep needs (D-07-04).
- **D-07-03.** `budget_allowance = 3`, replacing Phase 5's unsized placeholder of
  5. Counted, not guessed: the phase makes exactly **one** honest `val` look (the
  winning estimator's final evaluation), and reserves two for a re-run after a
  genuine, diagnosed bug. Any plan that would spend a fourth look on `val` is a
  plan that must be rewritten, not a budget that must be raised.
- **D-07-04.** Model SELECTION happens on the `oof_block_*` segments, never on
  `val`. The four estimators compete on the OOF blocks; only the winner is ever
  materialized against `val`.
- **D-07-05.** A "look" is one `harness.accessor.materialize` call. The slice
  therefore materializes each segment ONCE and caches the returned frame to a
  local Parquet under the run's own scratch directory; every downstream step
  (fit, predict, simulate, re-run after a crash) reads the cache, never
  re-materializes. The cache is a derived convenience, never an input to a
  manifest, and lives outside the repo.
- **D-07-06.** The new manifest's `held_out` entry is the same zero-width sentinel
  Phase 5 used (`start_ns == end_ns == covered_end_ns`). Phase 8 declares the real
  one.
- **D-07-07.** The manifest body is issued from a CLEAN tree so its `code_hash`
  carries no `-dirty` suffix (the Phase 5 manifest's does, and that is a
  reproducibility wart this phase does not repeat).

- **D-07-08.** Four estimators, in this order, all from scikit-learn:
  1. `LinearRegression` (OLS) — the honest floor.
  2. `Ridge` — closed-form, deterministic by construction.
  3. `ElasticNet` — `selection="cyclic"` and an explicit `random_state`.
  4. `Ridge` on `PolynomialFeatures(degree=2, include_bias=False)` — the
     "small non-linear" member of FCST-01's own wording, kept inside
     scikit-learn so trees stay Phase 8's territory.
  Hyperparameters are a fixed, hand-written, COUNTED grid — no Optuna.
- **D-07-09.** Model inputs are exactly three columns: `imb_top`, `ofi`,
  `trade_flow`. `mid`, `bid_price` and `ask_price` are BOOKKEEPING and must never
  reach an estimator — a raw price level smuggles the day's trend into the design
  matrix. A test must assert the refusal, not merely the omission.
- **D-07-10.** Target is `ret_10s_mid` (FCST-01's primary). The diagnostic
  horizons `ret_1s_mid` / `ret_1min_mid` / `ret_10min_mid` are reported alongside
  but never fitted in this phase.
- **D-07-11.** Normalization comes from the FEAT-05 train-only artifact
  (`BTCUSDT.features_norm`), resolved by manifest, never recomputed at fit time.
  The existing artifact is keyed `train_end=2026-09-13`; the new 7-day train
  window needs a NEW artifact partition at its own `train_end`, written
  additively. The old one is not touched.
- **D-07-12.** Determinism (FCST-04's "deterministic predictor") is proven the way
  Phase 6 proved kernel determinism: hash the prediction table produced by a FRESH
  SUBPROCESS and compare it to the in-process hash, and include a deliberate-
  sentinel mutation so a green result means something. Fits run single-threaded
  (`OMP_NUM_THREADS=1` / `threadpoolctl`) because BLAS thread count changes
  float64 reduction order.
- **D-07-13.** `scikit-learn==1.9.*` is added to `pyproject.toml` and locked with
  `uv lock` / `uv sync --frozen`. Verified at discuss time from PyPI metadata:
  requires `numpy>=1.24.1` (no upper bound), `scipy`, `joblib`, `narwhals`,
  `threadpoolctl` — so the numba pin `numpy==2.4.*` holds, and nothing in that
  closure requires pandas. Two things must be re-checked after the lock: that
  `numpy` is still 2.4.6, and whether Phase 2's pin-assertion guardrail enumerates
  pins and therefore needs the new entry. `scipy` arrives as a transitive dep and
  its `spearmanr` is what D-07-18 needs.

- **D-07-14.** A prediction table is keyed by the triple
  `(segment_manifest_id, segment_name, predictor_id)`, where
  `predictor_id = sha256` over the estimator class name, the sorted
  hyperparameters, the seed, the code hash, and the normalization manifest id.
  FCST-04's literal wording ("keyed by segment manifest") is necessary but not
  sufficient — the manifest id alone collides the moment a second estimator
  predicts the same segment, and Phase 8 has three model classes.
- **D-07-15.** Only the table that actually feeds the simulator is STORED (the
  winning estimator on `val`, ~11.3M rows ≈ 270 MB). OOF model selection computes
  its metrics streaming and persists only the scalar metrics. Storing all four
  estimators' tables on every segment would approach a gigabyte for no
  downstream reader, which is exactly the massive-data production this session
  is told to avoid. Measure the real bytes and record them in the SUMMARY.
- **D-07-16.** A stored table's schema is `etime Int64, decision_seq Int64,
  pred Float64`. Float64, not float32: the simulator quantises a prediction to
  ticks with a symmetric floor/ceil rule, and a float32 rounding error near the
  half-tick boundary can flip a trigger — the precise hazard Phase 6 spent a plan
  fixing. `(etime, decision_seq)` is the join key back to the decision row, since
  many rows share an `etime`.
- **D-07-17.** The frozen predictor serialises as its COEFFICIENTS in JSON (the
  intercept, the coefficient vector, the feature names in order, the
  normalization manifest id, the `predictor_id`), never as a pickle. A four-number
  JSON is diffable, reviewable, survives a scikit-learn version bump, and can be
  re-evaluated by a numpy dot product in a test that does not import sklearn at
  all — which is the strongest possible check that the frozen predictor is
  genuinely frozen. Tables live in the lake under a `predictions/` dataset,
  manifest-addressed through the existing `data.store.issue_manifest`, not in the
  repo and not as MLflow artifacts.

- **D-07-18.** "Beats the zero baseline" is TWO gates, both required:
  (a) **Forecast.** Out-of-sample R² > 0 against the constant-zero predictor, AND
      a rank IC (`scipy.stats.spearmanr`) computed on the NON-TIED subset. The
      target carries a 43.9% point mass at exactly zero; a statistic that treats
      those ties as ordinary observations flatters any model. The tie fraction is
      reported as a first-class number beside the IC, never folded into it.
  (b) **Monetization.** The simulator on `val` yields strictly more than 0 trades
      and strictly more than $0.
- **D-07-19.** The perfect-foresight ceiling is a hard guard, not a footnote. Any
  reported P&L at or above the measured ceiling for that segment raises an
  exception. The measured reference is 2,192 trades / 294,554 ticks / $29.46 on
  the v2 2026-09-13 partition; the ceiling for the new `val` segment must be
  re-measured for that segment before it is used as a bound.
- **D-07-20.** Every estimator/hyperparameter config that fails either gate is
  written to the negative-result log via
  `harness.negative_log.record_negative_result` before the sweep moves on. A
  failed config that leaves no trace is a config the project will pay to re-try.
- **D-07-21.** All logging goes through `tracking.mlflow_utils.start_tracked_run`
  and the mandatory tag schema. `mlflow.sklearn.autolog` is NOT available —
  `mlflow-skinny` is what is installed and it carries no sklearn flavor; a plan
  that assumes autolog will fail at import.

### Claude's Discretion

- The internal shape of the Trainer protocol, the module split inside
  `mvp/models/`, plan/wave decomposition, and test naming are at Claude's
  discretion, subject to the decisions above and to Phase 8 being able to add
  LightGBM and a transformer WITHOUT editing the protocol.

### Deferred Ideas (OUT OF SCOPE)

- **Optuna-driven hyperparameter search with a counted trial budget** — Phase 8 (FCST-05 names it).
- **Declaring and locking the real held-out window** — Phase 8 success criterion 4. Note for that phase's discuss: "forward in time" has nowhere to go while capture is stopped.
- **Retiring the v1 feature partitions** — needs a mechanism (a v1 manifest stops resolving once its bytes move); Phase 8's call per Phase 6's decision log.
- **Reporting suite: HAC/Newey-West standard errors, Ljung-Box, block bootstrap, equity-curve figures** — Phase 9 (EVAL-05); `statsmodels` and `matplotlib` are not installed and are not added here.
- **Removing the zero-fee / zero-latency simplifications** — already parameters with zero defaults; a call-site change whenever the project decides to.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| FCST-01 | Regression track (linear → ridge/elastic-net → small non-linear) predicting 10s midprice return, scikit-learn | §Standard Stack (verified 1.9.1 closure, no pandas); §Q3 (which estimators are deterministic and why); §Q4 (how to score the target); measured OOS skill on train-internal days: R² 0.013–0.048, rank IC 0.19–0.29 — the track has real signal, the gate is winnable |
| FCST-04 | Frozen-predictor interface: deterministic predictor per class producing precomputed prediction tables keyed by segment manifest | §Q5 (Trainer/FrozenPredictor Protocol that survives Phase 8 unedited); §Q6 (`BTCUSDT.predictions` dataset contract, guardrail impact); §Q2 (provable row alignment); §Q3a (what "deterministic" can actually be proven on this host — measured bit-identical across processes and thread counts) |
</phase_requirements>

---

## Summary

The whole phase turns on one observable the project has never produced: a `pred`
array that a fitted model wrote, walking the same decision rows the simulator
walks, in the same order, with one MLflow run tying the manifest ids together.
Everything hard about it is at the seams, not in the model. The model itself is
almost trivially skilful — measured on train-internal days only (days 12–17, so
no expectation is formed about the honest `val` look), a three-feature
train-only-normalised OLS generalises to an out-of-sample R² of **1.3%–4.8%**
against the constant-zero predictor and a **Spearman rank IC of 0.19–0.29**,
stable across every held-back day. FCST-01's forecast gate will pass comfortably;
the plan's risk budget belongs at the plumbing, not the fit.

Three measured findings change the plan as CONTEXT.md left it. **First, the
prediction table cannot be joined back by row order** — polars 1.41.2's `join`
documents its default ordering as "might differ across Polars versions or even
between different runs", and the accessor's own errata masking performs exactly
such a join on every `val` frame. Order happens to be preserved today (measured,
both with and without errata hits) which is precisely what makes it dangerous:
the assertion must be written now, while it passes, not after a polars bump
silently reorders 7.8M rows. **Second, `mlflow.start_run` raises a bare
`Exception` when a run is already active** (measured, mlflow-skinny 3.13.0), and
neither `record_look` nor `record_negative_result` passes `nested=True` — so the
slice's own run cannot be open while a look or a negative result is recorded. The
run structure is forced: looks and negatives first, the slice's own
`stage-1-regression` run afterwards, cross-referencing their run ids. **Third,
`threadpoolctl` cannot control this host's BLAS at all** — the venv's numpy 2.4.6
is built against Apple Accelerate, and threadpoolctl ships controllers only for
OpenBLAS, BLIS, FlexiBLAS, MKL and OpenMP. D-07-12's stated mechanism is inert on
the dev Mac. Determinism was therefore measured directly instead, and it holds:
the Gram matrix, the solved coefficients and the prediction array are bit-identical
across fresh processes and across `VECLIB_MAXIMUM_THREADS` of 1, 2 and unset.

The one place D-07-18's own premise is wrong is worth stating plainly: the 43.9%
zero point mass is a property of **2026-09-13 alone** (verified: 3,012,962 /
6,864,853 = 43.8897%). Across the pool the tie fraction runs from 9.6% to 69.3%,
and on the recommended `val` it is 9.8%–13.8%. Measured on real data, the ties do
not reliably flatter anything — on day 13 including them *deflates* the IC by 3.2%.
The real trap is the other half of that gate: **a constant predictor at the train
mean earns R² > 0 against the constant-zero predictor with exactly zero
conditional skill** (+0.001647 measured on day 18), while a model shrunk by 1e-6
keeps its full IC of +0.207 and an R² of 0.000000. The two statistics can be won
separately by two different degenerate models, so the gate needs R² against the
sample mean as well, and a NaN IC (which is what `spearmanr` returns for a
constant input) must be read as FAIL, not as absent.

**Primary recommendation:** issue the manifest as train = days 12–16 / val = days
17–18 (k=5 then yields OOF blocks of 23.99999989 h — one calendar day each, and a
two-day `val` buys a per-day robustness split inside the single honest look);
store the prediction table as the model's **return**, converting to the
simulator's raw-price `pred` with the one expression `mid * (1.0 + pred)` at the
sim boundary; and make the frozen-predictor JSON — not the sklearn fit — the
determinism boundary that D-07-12's subprocess test hashes.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Fold geometry, purge/embargo, look counting | `mvp/harness/` | — | D-05-11..15; `materialize` is the only sanctioned door and the 19th pre-commit hook enforces it statically |
| Train-only normalisation parameters | `mvp/features/normalize.py` | `mvp/data/store.py` (manifest) | FEAT-05 already owns the Welford fit, the artifact schema and the write-once refusal; Phase 7 adds a partition, not code |
| Estimator fitting, hyperparameter grid | `mvp/models/` (new) | scikit-learn | The only genuinely new tier this phase adds |
| Frozen predictor serialisation + evaluation | `mvp/models/` (new) | numpy only | D-07-17: the evaluation path must not import sklearn, so the "frozen" claim is checkable |
| Prediction-table bytes + manifest | `mvp/data/store.py` | lake `predictions/` tier | `issue_manifest` already provides content addressing, path write-once and sha256-on-read |
| Decision rule, tick quantisation, P&L | `mvp/sim/` | — | SIM-01..03 complete; Phase 7 is a caller, never an editor |
| Metrics, gates, ceiling guard | `mvp/models/` (new) | scipy.stats | Phase 9 (EVAL-05) owns HAC/bootstrap/figures; this phase owns only R², IC, tie fraction and the two gates |
| Run manifest, look/negative records | `mvp/tracking/`, `mvp/harness/budget.py`, `mvp/harness/negative_log.py` | — | Phase 2/5 modules; Phase 7 is a caller. All three open their own MLflow run and must not be nested |

---

## Standard Stack

### Core (new this phase)

| Library | Version | Purpose | Why standard |
|---------|---------|---------|--------------|
| scikit-learn | **1.9.1** | The four estimators, `PolynomialFeatures` | Mandated by FCST-01 and CLAUDE.md. `[VERIFIED: PyPI JSON API 2026-09-24]` latest of the 1.9 line, uploaded 2026-09-10; `cp313-macosx_12_0_arm64` and `cp313-manylinux_2_28_x86_64` wheels both present |
| scipy | **1.18.1** | `scipy.stats.spearmanr` (D-07-18) | Arrives as a hard sklearn dependency; `requires_dist: numpy<2.8,>=2.0.0`, `requires_python >=3.12` — compatible with the `numpy==2.4.*` numba pin and Python 3.13 `[VERIFIED: PyPI]` |

### Transitive closure (what `uv lock` will actually add)

| Package | Version | Pulled by | Deps of its own |
|---------|---------|-----------|-----------------|
| scikit-learn | 1.9.1 | direct | `numpy>=1.24.1`, `scipy>=1.10.0`, `joblib>=1.4.0`, `narwhals>=2.0.1`, `threadpoolctl>=3.5.0` |
| scipy | 1.18.1 | scikit-learn | `numpy<2.8,>=2.0.0` |
| joblib | 1.6.0 | scikit-learn | **`cloudpickle>=3.0`** |
| cloudpickle | 3.1.2 | joblib 1.6.0 | none |
| narwhals | 2.26.0 | scikit-learn | none required (`pandas` only under the `pandas` extra) |
| threadpoolctl | 3.7.0 | scikit-learn | none |

All rows `[VERIFIED: PyPI JSON API, 2026-09-24]`.

**The one entry D-07-13's own verification missed: `cloudpickle`.** joblib 1.6.0
made it a hard dependency. It has no dependencies of its own and no pandas
anywhere, so the conclusion is unchanged — but a plan whose acceptance criterion
enumerates the expected new lock entries must list six, not five.

**No pandas anywhere in the closure** — verified by reading every `requires_dist`
above. `narwhals` is the only member that could pull it and does so only under an
extra nobody requests. This is mechanically enforced, not merely checked:
`tools/check_pin_versions.py`'s `BANNED_PACKAGES = frozenset({"pandas"})` fails
the commit if `uv.lock` ever contains a pandas entry at any version.

**Installation:**
```bash
# from mvp/ ; short-lived, so uv is fine (never `uv run` for the long jobs)
uv add "scikit-learn==1.9.*"
uv lock && uv sync --frozen
```

### Alternatives considered

| Instead of | Could use | Tradeoff |
|------------|-----------|----------|
| sklearn `Ridge` | The 4×4 Gram accumulation in §Q3c | Verified numerically equivalent to 7e-15 relative, ~50× cheaper in memory, and streamable — but FCST-01 says "scikit-learn", and a hand-rolled fit is a second implementation to defend. Use sklearn for the fit; use the Gram form only as the ORACLE a test checks sklearn against |
| `scipy.stats.spearmanr` | `np.corrcoef(rankdata(a), rankdata(b))` | That IS spearmanr (verified from scipy 1.18.1 source, §Q4). Use scipy's — it is already a dependency, and it emits the `ConstantInputWarning` a hand-rolled version would silently swallow |
| `mlflow.sklearn.autolog` | Explicit `mlflow.log_metric`/`log_param` | Not an option at all: `mlflow-skinny` 3.13.0 is installed and carries no sklearn flavor (D-07-21). Import fails |

---

## Architecture Patterns

### System architecture: the vertical slice

```
                              ┌─────────────────────────────────────────┐
  lake/features/ (7 v2        │ harness.segments.issue_segment_manifest  │  ONE-TIME, committed
  partitions, 60,926,503 ────►│  reads all 7 upstream manifests,        │  ~5 min, ~19 GB peak
  decision rows)              │  derives purge/embargo/admission/OOF     │  spends NO look
                              └──────────────────┬──────────────────────┘
                                                 │ segment_manifest_id (committed JSON)
                                                 ▼
  ┌──────────────────────────────────────────────────────────────────────────────┐
  │                        harness.accessor.materialize                          │
  │   role=train  → purge/embargo only,  NO admission, NO errata,  NO look       │
  │   role=oof_block / val → admission + errata + record_look  (COUNTED)         │
  └───────┬───────────────────────────────────────────┬──────────────────────────┘
          │ train frame (free, re-callable)           │ val frame (ONE look, ever)
          ▼                                           ▼
  ┌───────────────────┐                      ┌──────────────────────┐
  │ scratch Parquet   │  ◄── D-07-05 ──►     │ scratch Parquet      │   outside the repo,
  │ cache (train)     │                      │ cache (val)          │   keyed by tracking root
  └────────┬──────────┘                      └──────────┬───────────┘
           │                                            │
           ▼                                            │
  ┌────────────────────────┐                            │
  │ features.normalize     │ fit_training_segment on the│
  │ NEW features_norm      │ train frame → write_        │
  │ partition (train_end=) │ normalization_artifact      │
  └────────┬───────────────┘                            │
           │ norm_manifest_id                           │
           ▼                                            │
  ┌──────────────────────────────────────────┐          │
  │ kfold.training_rows_for_block(train, j)  │          │  purely a train-side filter,
  │   5 OOF folds × 4 estimators × grid      │          │  no look, no accessor call
  │   → SELECTION  (D-07-04)                 │          │
  │   losers → negative_log.record_negative  │          │
  └────────┬─────────────────────────────────┘          │
           │ winning (class, hyperparams)               │
           ▼                                            ▼
  ┌──────────────────────┐        ┌──────────────────────────────────────┐
  │ FrozenPredictor JSON │───────►│ pred_return = X_norm @ coef + b      │
  │ coef/intercept/names │ numpy  │ (numpy only — no sklearn import)      │
  │ norm_id/predictor_id │  dot   └───────────────┬──────────────────────┘
  └──────────────────────┘                        │
                                                  ├─► lake/predictions/  (etime, decision_seq, pred)
                                                  │    + issue_manifest  (committed JSON)
                                                  ▼
                        pred_price = mid * (1.0 + pred_return)      ◄── spec.md's decision rule,
                                                  │                     ONE conversion site
                                                  ▼
                              sim.arrays.sim_arrays(val_frame)
                              sim.kernel.run_sim_checked(etime, bid_ticks, ask_ticks, pred_price,
                                                         x_bps=0, ...)
                                                  │
                                    ┌─────────────┴─────────────┐
                                    ▼                           ▼
                        gate (b): trades > 0, $ > 0     D-07-19 ceiling guard:
                                                        P&L < measured ceiling for THIS segment
                                                  │
                                                  ▼
                         ONE tracking.start_tracked_run("stage-1-regression")
                         — opened LAST, after every look and negative is closed
```

### Recommended module layout

```
mvp/models/
├── __init__.py
├── protocol.py        # Trainer + FrozenPredictor Protocols, FitData/Prediction shapes (§Q5)
├── sklearn_track.py   # the four estimators as Trainer implementations; the ONLY sklearn importer
├── frozen.py          # FrozenLinearPredictor: JSON (de)serialise + numpy-only evaluate; NO sklearn import
├── predictor_id.py    # D-07-14's sha256 over (class, sorted hyperparams, seed, code_hash, norm_manifest_id)
├── metrics.py         # r2_vs_zero, r2_vs_mean, rank_ic (+ tie_fraction, NaN handling)
├── gates.py           # D-07-18's two gates, D-07-19's ceiling guard
├── prediction_table.py# write/read the BTCUSDT.predictions dataset; the alignment assertion
└── slice.py           # the end-to-end script; owns the cache, the run sequencing, the CLI modes

mvp/tests/models/      # NO __init__.py (mvp/models/ exists — five prior instances, all bugs)
```

### Pattern 1: the cache is the look, the look is not the call

**What:** `materialize` is called exactly once per segment per phase; its return
value is immediately written to a scratch Parquet, and every later step reads the
Parquet.

**Why it is load-bearing here and was not in Phase 5/6:** the pre-commit hook
`pytest tests -x -q` runs the **entire** suite on every commit (hook 19 of 19).
Any test that reaches `materialize` against the real registry/lake/tracking roots
spends a real look **per commit**. Tests must use `tests/fixtures/lake` +
`tests/fixtures/lake_registry` and a `tmp_path` tracking root, never the canonical
ones. `harness.budget._require_canonical_tracking_root` refuses a non-canonical
root, which is the safety net — but it refuses by raising, so a test that expects
to succeed must supply a fixture-scale canonical root of its own.

**Key the cache by tracking root.** A dry run against a scratch tracking root
produces a `val` frame that is byte-identical to the real one and cost nothing. If
that cache is then read by the real run, the honest look was never spent and the
budget was bypassed in fact while reading as spent. Put the tracking root's
resolved path (or its sha256) in the cache filename.

### Pattern 2: the frozen-predictor JSON is the determinism boundary

**What:** D-07-12 hashes a prediction table from a fresh subprocess. Make the
subprocess load the **JSON coefficients** and re-evaluate `X_norm @ coef + b` in
numpy. Do not make it re-run the sklearn fit.

**Why:** fit reproducibility is a property of whatever BLAS happens to be linked
(§Q3a: Accelerate on this Mac, OpenBLAS on Linux CI, and neither is controlled by
the knob D-07-12 names). Table reproducibility given the coefficients is a
property of one matvec at fixed shape, which was measured bit-identical across
processes and thread counts. The first claim is what FCST-04 actually needs
("deterministic predictor"); the second is a bonus. Prove the first structurally;
measure the second and report it honestly.

**Corollary — never pin a coefficient hash in a committed test.** A hash literal
measured on Accelerate will fail on the Linux CI runner's OpenBLAS for reasons
that have nothing to do with the code. Assert *self-consistency* (in-process vs
subprocess, same host) and *numerical* agreement (`np.allclose` against the Gram
oracle), never a cross-platform byte literal.

### Pattern 3: one conversion site for return → price

`run_sim_checked`'s `pred` is a **raw price** in USDT, the same scale as
`bid_price`/`ask_price` (`sim/kernel.py` module docstring: "`pred` arrives as a RAW
PRICE (the same scale as `bid_price`/`ask_price`, NOT a bare tick count)"). The
model outputs a return. `spec.md`'s "Decision rule (Stage 2)" gives the
conversion: `pred_mid = mid * (1 + pred_10s_return)`.

Write that expression **once**, in the slice, immediately before `run_sim_checked`,
and have the perfect-foresight ceiling measurement call the *same* function. Then
model and ceiling cannot diverge by a conversion difference, which is exactly how
Phase 6 lost a plan to a quantisation asymmetry.

### Anti-patterns to avoid

- **Storing a price-valued `pred` in the table.** It bakes a particular row's
  `mid` into the artifact, so a future re-join against a differently-admitted
  frame produces plausible nonsense. Store the return; Stage 2's X-threshold sweep
  (MON-01) is on the return scale too.
- **Letting `mid` into the design matrix.** D-07-09 forbids it. Note that the
  existing `features_norm` artifact *does* carry a `mid` row (4 features:
  `imb_top`, `mid`, `ofi`, `trade_flow`) — so "it came from the normalisation
  artifact" is not a sufficient filter. The refusal must be an explicit
  allow-list assertion on the feature-name tuple.
- **Holding the slice's MLflow run open across a `materialize` call.** Raises
  (§Q8, measured).
- **Trusting post-join row order.** §Q2.
- **Re-issuing the segment manifest to fix something.** It costs ~5 minutes and
  ~19 GB, and once a look is spent against it, `issue_segment_manifest` will
  refuse any overlapping replacement (`_refuse_overlap_with_exhausted_segments`).
  Get it right once.

---

## Don't Hand-Roll

| Problem | Don't build | Use instead | Why |
|---------|-------------|-------------|-----|
| Ranking with ties | `6Σd²/(n(n²-1))` | `scipy.stats.spearmanr` | The shortcut formula is wrong under ties, and 9.8%–69.3% of this target IS a tie |
| Purged k-fold training rows | A second fold filter | `harness.kfold.training_rows_for_block` | Already the identical formula the accessor uses; a second one is the leak |
| Welford mean/std over 44M rows | `np.mean`/`np.std` at fit time | `features.normalize.fit_training_segment` + `write_normalization_artifact` | FEAT-05 owns the train-only boundary, the write-once refusal and the artifact schema; recomputing at fit time is the leak D-07-11 forbids |
| Content-addressed artifact ids | A new hasher | `data.store.compute_manifest_id` | `harness.negative_log.config_fingerprint` already reuses it; a second canonicaliser means two configs that are the same get different ids |
| Tick conversion for bid/ask | `round(price*10)` | `sim.ticks.price_to_ticks` via `sim.arrays.sim_arrays` | Round-trip-proved on 34M real prices, and it *refuses* off-grid values instead of truncating |
| Prediction quantisation | `price_to_ticks(pred)` | Nothing — the kernel does it | The kernel's floor(long)/ceil(short) rule is deliberately NOT `price_to_ticks`; Phase 6 spent a plan on that distinction |
| Ridge normal equations | — | `sklearn.linear_model.Ridge(solver="auto")` | Verified from source: `auto` → `cholesky` → `X.T@X` + `scipy.linalg.solve(assume_a="pos")`. Your hand-rolled Gram form is the *oracle*, not the *implementation* |

**Key insight:** every one of these has already been written once in this repo,
with a docstring explaining a trap it closes. The phase's entire new surface is
`mvp/models/`; anything that feels like it needs writing outside that directory is
probably already there.

---

## Q1 — The 7-day `compressed_3seg` manifest

### The required segment dict shape

`_validate_compressed_3seg_shape` (segments.py:154) compares tuples for **exact
equality**, so the caller passes exactly three dicts, in this order, with these
names and roles:

```python
segments = [
    {"name": "train",    "role": "train",    "start_ns": <int>, "end_ns": <int>},
    {"name": "val",      "role": "val",      "start_ns": <int>, "end_ns": <int>},
    {"name": "held_out", "role": "held_out", "start_ns": <int>, "end_ns": <int>},
]
```

- Names must be `("train", "val", "held_out")`; roles the same. Any other name,
  any other order, any extra top-level entry → `ValueError`.
- The `oof_block_0..4` entries are **computed by the function** from the `train`
  entry's own range (`kfold.purged_embargoed_blocks(train.start, train.end, k)`)
  and appended. A caller that supplies them fails the shape check.
- Only these four keys are read. `_validate_segments`, `_derive_*` and
  `mask_errata_cells` never look for anything else.

### What `_validate_compressed_3seg_shape` and `_validate_segments` constrain

| Rule | Where | Effect on this manifest |
|------|-------|-------------------------|
| names/roles/order exact | `_validate_compressed_3seg_shape` | as above |
| no `train`/`val` pair overlaps | `_validate_segments` (itertools.combinations) | `train.end_ns == val.start_ns` is legal (half-open) |
| declared order == chronological order | same | train before val |
| every `train`/`val` entry inside `[covered_start_ns, covered_end_ns)` | same | `train.start_ns >= 1789171200002000000` and `val.end_ns <= 1789775999957000000` |
| exactly one `held_out` | same | required, even as a sentinel |
| `held_out.start_ns >= last train/val end_ns` | same | `held_out.start_ns == val.end_ns` satisfies it |
| `held_out` EXEMPT from the upper coverage bound | same (D-05-16) | the sentinel may sit at `covered_end_ns` |
| each `oof_block` contained in some `train` entry | same | automatic — they are derived from it |

`covered_start_ns` / `covered_end_ns` come from `_covered_range`, which reads only
each upstream manifest's own `partitions[].etime_min/max` — no partition is parsed.
`[VERIFIED: read from the 7 committed v2 manifests]`

```
covered_start_ns = 1789171200002000000   (2026-09-12's first etime)
covered_end_ns   = 1789775999957000000   (2026-09-18's last etime)
```

### The zero-width `held_out` sentinel

`{"name": "held_out", "role": "held_out", "start_ns": 1789775999957000000,
"end_ns": 1789775999957000000}` — identical in shape to the committed 3-day
manifest's, which used `start == end == covered_end_ns` of its own pool. Three
things follow, all verified:

- `materialize(..., "held_out")` raises unconditionally (gate 2, before any data
  is resolved), so the sentinel is unreadable by construction.
- It is a real purge/embargo source for `train`: the band
  `[covered_end_ns − 600s, covered_end_ns + 601s)` is computed and merged. For
  either recommended layout it is entirely inside `val`'s own band, so it changes
  nothing.
- Its `admission.counts` come out `{admitted: 0, excluded_stale: 0,
  excluded_undefined: 0}` (measured), exactly as the 3-day manifest's did.

### `_refuse_starved_oof_blocks`: the arithmetic

It refuses any block whose `oof_training_row_counts[name] == 0`, where that count
is `kfold.training_rows_for_block(df, blocks, j, purge_ns, embargo_ns).height` —
i.e. rows of the whole `[blocks[0].start, blocks[-1].end)` range surviving the
band `[block_j.start − purge_ns, block_j.end + purge_ns + embargo_ns)`.

`[VERIFIED: imported from harness.purge_embargo]` `PURGE_HORIZON_NS = 600_000_000_000`
(600 s, = `max(LABEL_HORIZON_NS.values())`, the 10-minute diagnostic label);
`FOLD_EMBARGO_NS = 1_000_000_000` (1 s, = `TRADE_FLOW_WINDOW_NS`). Combined band
= block width + **1201 s**.

Block widths are far larger than the band, so starvation is structurally
impossible here — and it was measured, not argued:

| Layout | train span | k=5 block width | smallest OOF training row count |
|--------|-----------|-----------------|---------------------------------|
| **A** (days 12–16) | 5 d | 86,399,999,600,000 ns = **23.99999989 h** | **32,094,158** (`oof_block_3`) |
| **B** (days 12–17) | 6 d | 103,679,999,600,000 ns = 28.79999989 h | **38,698,582** (`oof_block_2`) |

Both `[VERIFIED: measured via harness.kfold against the real 7 v2 partitions]`.
`(train_end − train_start)` is exactly divisible by 5 in both layouts, so the
"last block absorbs the remainder" branch adds nothing.

### `upstream_feature_manifest_ids`

All **seven v2** feature manifest ids, and only those. `[VERIFIED: each manifest's
own `partitions[].path` inspected — every one names a `part-v2-*.parquet` and
carries `schema_version: 2`]`

```python
UPSTREAM_V2 = [
    "91154024ba400fb2d183828f4b6b745d73862b08313cbef33cf5c957503df005",  # 2026-09-12  4,193,137 rows
    "5e4ce973b196b60fbdec22bf771ff2c9d517c739d7150cd218a535842eb55f6a",  # 2026-09-13  6,864,853
    "1863b8d255d17312e3c6135af19f75e85cd9250d307ed43b8f6a604f95adcbc0",  # 2026-09-14 11,323,694
    "2da1b5b97f97b28b1df1500ed91596304d78ba6888900390795721c7bcd2e1a1",  # 2026-09-15 12,203,294
    "0bfa8d286d90280251ddabbad340e07f3fd193545de294a3feefb73ec5a63924",  # 2026-09-16  9,872,620
    "dcf3b39dad199f1814b3e0b27a506368b50bfabda191bc476a0549da25859fa3",  # 2026-09-17  8,482,081
    "d21771bda03691c6c492e6c3fefd6603d09d223e0f5876dc7e2d66a07359948e",  # 2026-09-18  7,986,824
]                                                                        # total 60,926,503
```

The committed 3-day manifest names the **v1** ids for 09-12/13/14 — do not copy
them. Each date's `by-date` pointer already resolves to the v2 manifest, which is
a cheap cross-check the plan should assert rather than trusting the list above.

### Where `admission` defaults come from

`admission` is caller-supplied **for the policy only**; `admission["counts"]` is
derived and any caller value is discarded (segments.py:694–697). `apply_admission_policy`
reads exactly two keys: `max_age_ns` and `exclude_undefined_age`.
`row_admission.STALE_BOOK_MAX_AGE_NS = 5 * NS_PER_SECOND` is the decided project
constant (D-05-21) but `issue_segment_manifest` does **not** default to it — the
caller must pass it. The committed 3-day manifest passed:

```python
admission = {"policy": "stale_book", "max_age_ns": 5_000_000_000, "exclude_undefined_age": True}
```

Pass the same, sourced from `row_admission.STALE_BOOK_MAX_AGE_NS` rather than a
literal, so the manifest cannot understate the project constant.

### `errata_id` and `version`: a measured no-op

`read_errata_manifest` cross-checks `symbol` **and `version`** against the segment
manifest and fails closed. The committed errata manifest
`22190ad925929001855c4cec56c7ddbd00043d4717ec02ac96fc9b71e3e31b58` carries
`{"symbol": "BTCUSDT", "version": 1, "computed_from_dates": ["2026-09-12",
"2026-09-13", "2026-09-14"]}` and 249 cells (69 `ret_10s_mid` + 153 `ret_1s_mid`
on 09-12; 27 `ret_1s_mid` on 09-13).

`[VERIFIED: measured]` **All 249 cells exist in the v2 partitions and all 249 are
already null there** — in v1 every one of them was finite. The v2 rebuild applied
the fixed `null_stale` rule, so masking them is a provable no-op:
`mask_errata_cells` on the v2 day-12 partition changed `ret_10s_mid`'s null count
from 56,512 to 56,512.

Two correct choices, both defensible; this is a planner decision:

| Choice | Consequence |
|--------|-------------|
| `errata_id = "22190ad9…"`, `version = 1` | D-05-20's gate stays live and fail-closed; masking is a measured no-op. `version` here is the *catalogue* version, not the feature `schema_version` (which is 2) — `read_errata_manifest`'s own docstring says "catalogue version" |
| `errata_id = None`, `version = 2` | Honest ("v2 needs no errata") but opts out of a gate for a reason that lives in a SUMMARY rather than in code |

**Recommended: the first.** Keep the gate wired and record the measured no-op in
the SUMMARY — a live gate that currently masks nothing is strictly better than a
disabled one.

### Concrete, ready-to-adapt call sketch

```python
# scripts/issue_phase7_segment_manifest.py  — COMMIT THIS FILE FIRST, then run it,
# so compute_code_hash() has no "-dirty" suffix (D-07-07).
from pathlib import Path
from data import lake_paths
from harness import row_admission
from harness.segments import issue_segment_manifest
from tracking.mlflow_utils import compute_code_hash

COVERED_START_NS = 1_789_171_200_002_000_000   # 2026-09-12 first etime
COVERED_END_NS   = 1_789_775_999_957_000_000   # 2026-09-18 last  etime
UTC_MIDNIGHT_09_17 = 1_789_603_200_000_000_000  # == 1789603200 * 10**9

# RECOMMENDED (Option A): train = days 12-16, val = days 17-18.
segments = [
    {"name": "train",    "role": "train",
     "start_ns": COVERED_START_NS,   "end_ns": UTC_MIDNIGHT_09_17},
    {"name": "val",      "role": "val",
     "start_ns": UTC_MIDNIGHT_09_17, "end_ns": COVERED_END_NS},
    {"name": "held_out", "role": "held_out",                      # D-07-06 sentinel
     "start_ns": COVERED_END_NS,     "end_ns": COVERED_END_NS},
]

manifest = issue_segment_manifest(
    "compressed_3seg",
    segments,
    upstream_feature_manifest_ids=UPSTREAM_V2,      # all seven v2 ids, above
    admission={"policy": "stale_book",
               "max_age_ns": row_admission.STALE_BOOK_MAX_AGE_NS,
               "exclude_undefined_age": True},
    errata_id="22190ad925929001855c4cec56c7ddbd00043d4717ec02ac96fc9b71e3e31b58",
    budget_allowance=3,                             # D-07-03
    fold_config_reason=(
        "compressed_3seg selected over 5seg although the 7-day pool would support "
        "five segments: a 5seg manifest declares a REAL held_out segment, and "
        "declaring + locking held-out is Phase 8 success criterion 4 (D-07-02). "
        "held_out here is the zero-width sentinel at covered_end_ns (D-07-06). "
        "The inner k=5 purged+embargoed OOF blocks are also what Phase 7's model "
        "selection runs on, never val (D-07-04)."
    ),
    symbol="BTCUSDT",
    version=1,                                      # catalogue version; matches the errata manifest
    code_hash=compute_code_hash(),                  # must come out clean, no "-dirty"
    registry_root=lake_paths.LAKE_REGISTRY_ROOT,
    lake_root=lake_paths.lake_root(),
    tracking_root=str(lake_paths.mlflow_tracking_root()),   # read-only here; spends nothing
    k=5,
)
```

### What the derived body will contain — measured in advance

**Option A (RECOMMENDED): train = days 12–16, val = days 17–18**

| entry | `[start_ns, end_ns)` | raw rows | `admission.counts` |
|-------|----------------------|----------|--------------------|
| `train` | `[1789171200002000000, 1789603200000000000)` | 44,457,598 | stale 152,207 / undefined 55,843 / admitted 44,249,548 |
| `val` | `[1789603200000000000, 1789775999957000000)` | 16,468,904 | stale 174,845 / undefined 0 / **admitted 16,294,059** |
| `held_out` | `[1789775999957000000, 1789775999957000000)` | 0 | 0 / 0 / 0 |
| `oof_block_0` | `[1789171200002000000, 1789257600001600000)` | 4,193,137 | 221 / 55,843 / 4,137,073 |
| `oof_block_1` | `[1789257600001600000, 1789344000001200000)` | 6,864,853 | 0 / 0 / 6,864,853 |
| `oof_block_2` | `[1789344000001200000, 1789430400000800000)` | 11,323,695 | 32,330 / 0 / 11,291,365 |
| `oof_block_3` | `[1789430400000800000, 1789516800000400000)` | 12,203,294 | 61,148 / 0 / 12,142,146 |
| `oof_block_4` | `[1789516800000400000, 1789603200000000000)` | 9,872,619 | 58,508 / 0 / 9,814,111 |

`effective_intervals: {"train": [[1789171200002000000, 1789602600000000000]]}`
(the last 600 s of 2026-09-16 is purged by `val`), `purged_row_count: {"train": 3115}`,
`embargoed_row_count: {"train": 0}`, `purge_ns: 600000000000`, `embargo_ns: 1000000000`,
`oof_training_row_counts: {0: 40,242,038, 1: 37,488,354, 2: 32,997,430, 3: 32,094,158, 4: 34,530,706}`.

**Option B: train = days 12–17, val = day 18** — `train`
`[1789171200002000000, 1789689600000000000)` 52,939,679 raw / 52,684,188 admitted;
`val` `[1789689600000000000, 1789775999957000000)` 7,986,823 raw / **7,859,419
admitted**; `purged_row_count: {"train": 23672}`; OOF blocks 28.8 h each,
`oof_training_row_counts` 38.7M–47.8M; OOF admitted 5,071,473 / 9,553,612 /
13,973,534 / 13,892,842 / 10,192,727.

All `[VERIFIED: measured by replaying `row_admission.stale_book_age_ns` +
`apply_admission_policy` + `kfold` against the real 7 v2 partitions — the identical
functions `issue_segment_manifest` calls, in the identical order, on the identical
full-pool pre-slice frame]`

### Recommendation: Option A

1. **k=5 gives one-calendar-day OOF blocks** (23.99999989 h, drifting 400 µs) —
   `oof_block_1` is exactly 2026-09-13, `oof_block_2` exactly 09-14, and so on.
   Per-block metrics become directly interpretable as per-day metrics, which is
   what FCST-05's "per-fold ranking with std confidence bands" will want in Phase 8.
2. **A two-day `val` buys a robustness split inside the one honest look.** Slice
   the cached frame by UTC day and report both days' metrics separately. D-07-03
   allows exactly one look; this is the only way to get two independent validation
   observations out of it.
3. **The extra train data Option B buys is worth nothing — measured.** Fitting on
   days 12–16 and testing on day 17 gives R²=0.047253; fitting on only 14–16 gives
   0.047509. Five days of train is already past the point where this feature set
   improves.
4. **Cost is within D-07-15's own budget.** The stored table is 16,294,059 rows;
   extrapolating the measured 91.2 MiB at 7,859,419 rows gives ≈190 MiB parquet
   zstd, under D-07-15's stated ~270 MB.
5. Ceiling measured for both (§Q7), so either can be chosen without further work.

### Cost and sequencing of issuance

`[VERIFIED: measured end-to-end on the real pool]`

| step | cost |
|------|------|
| load + concat 7 v2 partitions | 5.8 s, **5.75 GiB** polars frame (101.28 bytes/row) |
| `_derive_purge_embargo_fields`: `.to_list()` of 52.9M etimes | 4.9 s |
| …its Python `any()` loop over those etimes | **121 s** |
| `stale_book_age_ns` on the full pool | 7.5 s (frame grows to 6.21 GiB) |
| 9 × slice + `apply_admission_policy` | 36 s |
| 5 × `training_rows_for_block` | 0.2 s |
| **peak memory footprint (macOS `time -l`)** | **19.35 GiB**, RSS 9.9 GiB |

Machine: Apple M1 Pro, 8 cores, **32 GiB RAM** — it fits, with roughly 12 GiB of
headroom, and nothing else large may be running. Budget ~5 minutes wall clock.
`_refuse_overlap_with_exhausted_segments` queries MLflow read-only via
`exhausted_segments`, so it needs the canonical tracking root but **spends
nothing**.

Two commits, in this order, for D-07-07's clean `code_hash`:
1. the issuance script (+ anything it imports) — tree now clean;
2. run it; commit the resulting `mvp/data/lake_registry/segments/<id>.json`.

The same two-commit discipline applies to the new `features_norm` partition,
whose manifest also records a `code_hash`.

---

## Q2 — What the accessor hands back, and what the simulator needs

### The frame `materialize` returns

Exactly `features.tier`'s columns, sliced and gated — no column is added and, for
look roles, the internal `stale_book_age_ns` is dropped again before return.
`[VERIFIED: read from a v2 partition]`

| column | dtype | notes |
|--------|-------|-------|
| `etime` | Int64 | ns since epoch. **Globally unique and globally ascending across the whole 7-day pool** (60,926,503 rows / 60,926,503 distinct) |
| `decision_source_rank` | Int8 | 0 = quote, 1 = trade |
| `decision_seq` | Int64 | source arrival seq; **NOT unique** (day 12: 4,193,137 rows, 4,074,012 distinct) and **resets per day** |
| `bid_price`, `ask_price`, `mid` | Float64 | bookkeeping; `mid == (bid+ask)/2` exactly on every v2 row (Phase 6) |
| `imb_top`, `ofi`, `trade_flow` | Float64 | the three model inputs (D-07-09) |
| `ret_10s_mid` | Float64 | target (D-07-10) |
| `ret_1s_mid`, `ret_1min_mid`, `ret_10min_mid` | Float64 | diagnostics |
| `warmup`, `post_gap_warmup` | Boolean | |
| `schema_version` | Int32 | 2 on every v2 partition |

Nullability by role, measured on Option A's `val` (days 17–18 admitted,
16,294,059 rows): `bid_price`/`ask_price`/`mid` carry **no** nulls; the three
feature columns plus `ret_10s_mid` carry **8,614** non-finite rows between them
(on Option B's day-18 `val`: 4,586, all of them `ret_10s_mid`). The `train` role
is never admission-gated, so it retains all feature nulls — 55,843 on 09-12 and
16,001 on 09-17.

### `run_sim_checked`'s exact contract

```python
sim.kernel.run_sim_checked(
    etime:      np.ndarray,      # 1-D, dtype MUST be exactly np.int64      — ns since epoch
    bid_ticks:  np.ndarray,      # 1-D, dtype MUST be exactly np.int64      — integer TICKS
    ask_ticks:  np.ndarray,      # 1-D, dtype MUST be exactly np.int64      — integer TICKS
    pred:       np.ndarray,      # 1-D, dtype MUST be exactly np.float64    — RAW PRICE in USDT
    *,
    x_bps:                int = 0,                    # threshold, basis points; 0 for Phase 7
    max_notional_scaled:  int = MAX_NOTIONAL_SCALED,  # 100 * 1e8 = $100 at PRICE_SCALE
    lot_step_scaled:      int = LOT_STEP_SCALED,      # 100_000 = 0.001 BTC at QTY_SCALE
    fee_bps:              int = 0,                    # accepted, applied nowhere (D-06-16)
    latency_ns:           int = 0,                    # accepted, applied nowhere (D-06-16)
    state: np.ndarray | None = None,                  # None = fresh run
) -> SimResult
```

Dtypes are checked and raise `ValueError`, not coerced. Every array must be 1-D
and the same length; a mismatch returns `STATUS_ARRAY_LENGTH_MISMATCH`.

`SimResult` = `NamedTuple(trade_log: dict[str, np.ndarray], fill_count: int,
equity_scaled: np.ndarray, counters: dict[str, int])`.

**How `pred` gets in:** it is *not* part of `sim_arrays`' contract (D-06-04 —
`sim/arrays.py`'s docstring says so explicitly). `sim_arrays(frame)` returns only
`{"etime", "bid_ticks", "ask_ticks"}`, asserting `null_count() == 0` and the exact
dtype for each of `etime`/`bid_price`/`ask_price` first (a nullable Float64 column
silently becomes a NaN-filled copy at `.to_numpy()`, so the assertion is the
control). `pred` is a separate positional argument the caller supplies.

**Units and where quantisation happens:** `pred` is a raw USDT price.
Quantisation happens **inside the `@njit` kernel**, per row, and deliberately not
via `price_to_ticks`:

```
s                = int64(round(pred[i] * PRICE_SCALE))        # PRICE_SCALE = 1e8
pred_ticks_floor = s // TICK_SIZE_SCALED                      # TICK_SIZE_SCALED = 1e7 ($0.10)
pred_ticks_ceil  = -((-s) // TICK_SIZE_SCALED)
x_ticks          = (bid_ticks[i] + ask_ticks[i]) * x_bps // 20_000
long_trigger     = pred_ticks_floor > ask_ticks[i] + x_ticks
short_trigger    = pred_ticks_ceil  < bid_ticks[i] - x_ticks
```

A negative `s` returns `STATUS_NEGATIVE_PRED` — so `pred` must be a positive
price, never a return. `mid * (1 + ret)` with the measured `|ret| < 0.003` is
always positive.

**The `[:fill_count]` hazard:** `new_trade_log(n)` preallocates at `n =
etime.shape[0]` with `np.empty` — **uninitialised memory**. Every one of the five
columns must be sliced to `[:fill_count]` before being hashed, written, counted or
compared. `SimResult` names `fill_count` as a field precisely so a caller cannot
reach the log without also seeing the number that makes reading it safe. At
Option A's `val` this is a 16.3M-row × 5-column allocation (~0.4 GiB) of which
9,946 rows are real under perfect foresight.

### Provable row alignment for the prediction table

This is the phase's single highest-risk seam, and the risk is documented, not
speculative.

`[CITED: polars 1.41.2 `DataFrame.join` docstring]` `maintain_order` defaults to
`None` → `"none"`: *"No specific ordering is desired. The ordering might differ
across Polars versions or even between different runs."* and *"Do not rely on any
observed ordering without explicitly setting this parameter, as your code may
break in a future release."*

`harness.errata.mask_errata_cells` performs `out.join(hit, on=("etime",
"decision_seq"), how="left")` with **no `maintain_order`**, once per distinct
`label_column` in the errata list — and it runs on **every** `val`/`oof_block`
frame whenever `errata_id` is non-null, even when zero cells match. Phase 7 is the
first phase to feed an accessor frame into a sequential simulator, so this has
never mattered before.

`[VERIFIED: measured on polars 1.41.2]` Order **is** preserved today — both on the
day-18 partition (zero errata hits, join still executes) and on the day-12
partition (all 153 keys present). Which is exactly why the assertion must be
written now.

**The recommended discipline, in order of strength:**

1. **Immediately after `materialize`, assert ascending `etime`** and fail loudly
   otherwise — the cheapest possible statement that the sequential scan is valid:
   ```python
   e = frame["etime"].to_numpy()
   if not np.all(np.diff(e) > 0):
       raise ValueError("materialize returned a frame that is not strictly etime-ascending; "
                        "sim.kernel.run_sim is a sequential scan and would be silently wrong")
   ```
   This also proves `etime` is a unique key on the frame, which the measurement
   above says it is pool-wide.
2. **Emit the prediction table positionally from the very frame the simulator
   consumes** — `pl.DataFrame({"etime": frame["etime"], "decision_seq":
   frame["decision_seq"], "pred": pred_return})`. No join, no sort, nothing to
   misalign.
3. **On read-back, assert key identity before using `pred`, then use it
   positionally:**
   ```python
   tbl = read_prediction_table(...)
   if tbl.height != frame.height: raise ValueError(...)
   if not np.array_equal(tbl["etime"].to_numpy(), frame["etime"].to_numpy()):
       raise ValueError("prediction table etime sequence does not match the frame it claims to score")
   if not np.array_equal(tbl["decision_seq"].to_numpy(), frame["decision_seq"].to_numpy()):
       raise ValueError(...)
   pred = tbl["pred"].to_numpy()          # positional, alignment already proven
   ```
   An O(n) `array_equal` on 16.3M int64s costs milliseconds and is a *stronger*
   statement than any join: it proves the two row sequences are the same sequence,
   not merely that every key found a partner.
4. **If a join is used anyway** (e.g. a Stage-2 reader in Phase 9 that holds only
   the table), pass `maintain_order="left"` and `validate="1:1"` explicitly, and
   still assert the height.

**Correction to D-07-16's stated rationale.** The decision says `(etime,
decision_seq)` is needed "since many rows share an `etime`". Measured: **no row
shares an `etime`** — the features tier emits one decision row per `etime` by
construction (FEAT-01: decisions emit on the last row of each `etime`). `etime`
alone is a unique key on every partition and on the whole pool. The two-part key
is still the right choice — it matches `mask_errata_cells`' own join key and
survives a future invariant change — but a plan that reasons *from* the stated
premise (e.g. "we must not sort by etime, it isn't unique") would reach wrong
conclusions. `decision_seq` alone is **not** unique even within a day and must
never be used as a key by itself.

---

## Q3 — scikit-learn 1.9 determinism and memory at this scale

> **scikit-learn and scipy are NOT installed in this venv.** Every claim in (a)
> and (c) about sklearn's behaviour is read from the scikit-learn 1.9.1 and scipy
> 1.18.1 sources on GitHub, not executed. Claims about numpy/BLAS/memory in (a)
> and (b) were measured in the venv on the real data.

### (a) Which estimators are reproducible, and what actually controls it

| Estimator | Algorithm (1.9.1 source) | Randomness | Needs a seed? |
|-----------|--------------------------|------------|---------------|
| `LinearRegression` | `_preprocess_data` centres, then `scipy.linalg.lstsq` (LAPACK `gelsd`, SVD divide-and-conquer) — `_base.py:752` | none | no (no `random_state` parameter exists) |
| `Ridge` | `solver="auto"` → **`"cholesky"`** for dense, `positive=False`, `return_intercept=False` (`resolve_solver_for_numpy`, `_ridge.py:867`). `_solve_cholesky`: `A = X.T @ X`; `A.flat[::p+1] += alpha`; `scipy.linalg.solve(A, Xy, assume_a="pos")` | none on this path — `random_state` is *"Used when solver == 'sag' or 'saga' to shuffle the data"* | no, but pass one for the MLflow `seed` tag |
| `ElasticNet` | Cython coordinate descent. `rng = check_random_state(random_state)`; `random = selection == "random"` (`_coordinate_descent.py:757–760`) | **only when `selection="random"`** | not strictly with `selection="cyclic"` (D-07-08's default); pass one anyway |
| `Ridge` on `PolynomialFeatures(2)` | pure deterministic column expansion, then the Ridge path above | none | no |

`return_intercept=True` — the one `Ridge` path that routes to `sag` and therefore
consumes `random_state` — is reached only for **sparse X with `fit_intercept=True`**
(`_ridge.py:985`). Dense input never takes it. So with dense float64 arrays, all
four estimators are algorithmically deterministic; no seed changes any result.

**What `OMP_NUM_THREADS` / `threadpoolctl` actually control here — and the finding
that matters.**

`[VERIFIED: `numpy.show_config()` in this venv]` numpy 2.4.6's BLAS and LAPACK are
**`accelerate`** (Apple's), not OpenBLAS. `[VERIFIED: threadpoolctl master
source]` threadpoolctl ships exactly five controllers — `OpenBLASController`,
`BLISController`, `FlexiBLASController`, `MKLController`, `OpenMPController` —
and **no Apple Accelerate controller**. `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`
and `MKL_NUM_THREADS` are all inert against Accelerate; its knob is
`VECLIB_MAXIMUM_THREADS`.

So on the dev Mac, D-07-12's stated mechanism does not do what it says. It is
still worth setting (it *is* the right knob on the Linux CI runner, whose numpy
and scipy wheels link OpenBLAS), but it cannot be the *reason* a determinism test
passes. The reason was measured directly instead:

`[VERIFIED: measured — 7,854,835 × 3 real day-18 rows, the exact Gram+solve
computation `_solve_cholesky` performs]` the Gram matrix bytes, the right-hand
side bytes, the solved coefficients and the full 7.85M-element prediction array
hash **bit-identically** across: two fresh processes with default threading;
`VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1`; and
`VECLIB_MAXIMUM_THREADS=2`. Four configurations, one hash each, all equal.

Practical consequences for the plan:

- **Same-host, cross-process determinism of the prediction table holds** — which
  is exactly the claim D-07-12's subprocess test makes. Keep the test.
- **Set `VECLIB_MAXIMUM_THREADS=1` alongside `OMP_NUM_THREADS=1`**, and state in
  the docstring which platform each one covers. A comment saying
  "`OMP_NUM_THREADS=1` because BLAS thread count changes reduction order" would be
  false on the machine it runs on.
- **Never pin a coefficient or table hash as a literal in a committed test.**
  Accelerate and OpenBLAS will not agree bit-for-bit, and the CI runner is Linux.
  Assert in-process == subprocess on the same host, and `np.allclose` against the
  Gram oracle for numerical agreement.
- ElasticNet's coordinate descent is Cython/nogil, not OpenMP, so no thread knob
  affects it; `selection="cyclic"` is deterministic by iteration order.

### (b) Real memory footprint

Design matrices are **not** the memory problem — the accessor frame is.

| N | p=3 float64 | `PolynomialFeatures(2, include_bias=False)`, p=9 |
|---|-------------|--------------------------------------------------|
| 7,859,419 (Option B `val`) | 0.176 GiB | 0.527 GiB |
| 11,291,363 (the 3-day `val`) | 0.252 GiB | 0.757 GiB |
| 16,294,059 (Option A `val`) | 0.364 GiB | 1.093 GiB |
| 30,000,000 | 0.671 GiB | 2.012 GiB |
| 44,249,548 (Option A `train`) | 0.989 GiB | 2.967 GiB |
| 52,684,188 (Option B `train`) | 1.178 GiB | 3.534 GiB |

`[VERIFIED: measured]` **`PolynomialFeatures(degree=2, include_bias=False)` on 3
inputs produces exactly 9 columns** — 3 linear + 3 squares + 3 cross terms:
`(x0, x1, x2, x0², x0x1, x0x2, x1², x1x2, x2²)`. Not 6, not 10.

The real cost is upstream: the 7-day accessor frame is **101.28 bytes/row**, so
**5.75 GiB** for the full pool, growing to 6.21 GiB when `stale_book_age_ns` is
attached. Peak process footprint for a full issuance pass measured **19.35 GiB**
on a 32 GiB host. `materialize` reloads and re-concatenates that 5.75 GiB on
**every** call — which is D-07-05's cache decision earning its keep for reasons
beyond the look budget.

Mitigation for the fit step: select only the four columns you need
(`imb_top, ofi, trade_flow, ret_10s_mid`) out of the cached Parquet rather than
reading the whole frame — 4 × 8 bytes/row instead of 101.

### (c) The closed-form / streaming alternative, and whether it agrees

Yes, and it agrees to float64 round-off.

`Ridge(fit_intercept=True, solver="auto")` on dense input computes exactly the
centred normal equations: `_preprocess_data` subtracts the column means and the
target mean, `_solve_cholesky` forms `X_c.T @ X_c + αI` and solves it, then
`_set_intercept` recovers `b = ȳ − x̄ᵀw`. Every quantity in that is a function of
the (p+1)×(p+1) augmented Gram matrix `S = [X 1]ᵀ[X 1]` and `b = [X 1]ᵀy`, both
accumulable in one streaming pass:

```python
def ridge_gram_streaming(X, y, alpha, chunk=1_000_000):
    """Sufficient statistics only: 4x4 + 4 for p=3, regardless of n."""
    p = X.shape[1]
    S = np.zeros((p + 1, p + 1)); b = np.zeros(p + 1)
    for s in range(0, X.shape[0], chunk):
        Xb, yb = X[s:s+chunk], y[s:s+chunk]
        A = np.empty((Xb.shape[0], p + 1)); A[:, :p] = Xb; A[:, p] = 1.0
        S += A.T @ A; b += A.T @ yb
    n, sx, sy = S[p, p], S[:p, p], b[p]
    XtX_c = S[:p, :p] - np.outer(sx, sx) / n          # centred Gram
    Xty_c = b[:p]     - sx * sy / n
    w = np.linalg.solve(XtX_c + alpha * np.eye(p), Xty_c)
    return w, sy / n - (sx / n) @ w
```

`[VERIFIED: measured on 7,854,835 real day-18 rows]` against the dense centred
solve, at α ∈ {1e-6, 1, 100, 1e4}: **maximum relative coefficient difference
7.1e-15**, absolute intercept difference 1.3e-19. That is float64 round-off, not a
numerical difference.

**Conditioning makes this safe here, and that was measured too.** Raw `X.T X` on
the three features has condition number **712**; after the FEAT-05 z-score it is
**1.28**. Normal equations square the condition number, so κ(X)² ≈ 1.6 on
normalised inputs — the classic "never use normal equations" objection simply does
not bite. (It would if `mid` were in the matrix, which is one more reason D-07-09
excludes it.)

**Recommended use:** fit with sklearn (FCST-01 says scikit-learn), and use
`ridge_gram_streaming` as the **oracle** a test checks sklearn's coefficients
against with `np.allclose(rtol=1e-9)`. That test is the strongest available
statement that the sklearn fit did what the phase thinks it did, and it costs
nothing. Note honestly: the equivalence is verified against `_solve_cholesky`'s
*textual* math and against numpy's own dense solve — **not** against an executed
sklearn, which is not installed.

### (d) The closure pulls no pandas

Confirmed by reading `requires_dist` for every member — see §Standard Stack. The
only pandas mention anywhere in the closure is `narwhals[pandas]`, an extra that
nothing requests, and `joblib[docs]`, likewise. `tools/check_pin_versions.py`'s
`BANNED_PACKAGES` check turns this from a claim into a commit gate. Nothing was
installed to verify it.

**Two post-lock re-checks D-07-13 asks for, answered in advance:**

- `numpy` stays 2.4.6: sklearn requires `numpy>=1.24.1` (no upper bound) and scipy
  1.18.1 requires `numpy<2.8,>=2.0.0`; `pyproject.toml` pins `numpy==2.4.*` and
  numba 0.65.1 pins `numpy<2.5`. No conflict. `uv lock --check` (pre-commit hook 3)
  plus `check_pin_versions` (hook 4) will catch it if it drifts.
- **Phase 2's pin guardrail needs no new entry.** `PINNED_PREFIXES = {"numba":
  "0.65", "numpy": "2.4", "llvmlite": "0.47"}` — three entries, and adding a
  fourth is optional, not required. If the plan wants `"scikit-learn": "1.9"`
  added for consistency, that is a free choice; omitting it breaks nothing.

---

## Q4 — Scoring a target with a point mass at exactly zero

### The 43.9% figure: verified, and narrower than D-07-18 states

`[VERIFIED: measured]` On the v2 2026-09-13 partition: 3,012,962 of 6,864,853 rows
have `ret_10s_mid == 0.0` exactly = **43.8897%**. The figure is right.

But it is a property of **that day**, not of the target:

| date | rows | exact zeros | fraction |
|------|------|-------------|----------|
| 2026-09-12 | 4,193,137 | 2,905,138 | **69.28%** |
| 2026-09-13 | 6,864,853 | 3,012,962 | **43.89%** |
| 2026-09-14 | 11,323,694 | 1,384,076 | 12.22% |
| 2026-09-15 | 12,203,294 | 1,505,638 | 12.34% |
| 2026-09-16 | 9,872,620 | 1,128,446 | 11.43% |
| 2026-09-17 | 8,482,081 | 1,485,738 | 17.52% |
| 2026-09-18 | 7,986,824 | 767,021 | 9.60% |
| **pool** | **60,926,503** | **12,189,019** | **20.01%** |

On the scorable rows of the two candidate `val` segments: **13.83%** (Option A,
days 17–18) and **9.77%** (Option B, day 18). The tie fraction moves inversely
with activity — quiet days have more unchanged 10-second mids.

**Restate D-07-18 as: the tie fraction is measured per segment and reported beside
the IC.** A plan that hardcodes 43.9% would be describing a day that is inside
`train` under either layout.

### "Beats the zero baseline": the two R² are not the same number, and it matters

```
R²_vs_zero = 1 − SSE / Σyᵢ²              # the constant-zero predictor as the reference
R²_vs_mean = 1 − SSE / Σ(yᵢ − ȳ)²        # the ordinary R², unconditional mean as the reference
```

They differ by exactly `n·ȳ²`: `Σ(y − ȳ)² = Σy² − n·ȳ²`. With `ȳ` tiny but nonzero
and R² itself only a couple of percent, that small absolute difference is a large
*relative* one. Measured in-sample on day 18: `SS_mean / SS_zero = 0.9983534`, and
an OLS scores R²_vs_zero = **+0.022238** against R²_vs_mean = **+0.020625** — a
7.8% relative gap.

**The trap, measured.** A predictor that is the **constant sample mean** — zero
conditional skill, no features used at all — scores:

| predictor (day 18, in-sample) | R²_vs_zero | R²_vs_mean | IC (all rows) | IC (non-tied) |
|-------------------------------|-----------|-----------|---------------|---------------|
| OLS on the 3 features | **+0.022238** | **+0.020625** | +0.207494 | +0.204823 |
| **constant at the sample mean** | **+0.001647** | +0.000000 | **NaN** | **NaN** |
| OLS × 1e-6 (same ranking, no magnitude) | **+0.000000** | −0.001649 | **+0.207494** | +0.204823 |

D-07-18(a) as written — "R² > 0 against the constant-zero predictor" — is
**satisfied by the constant-mean predictor**. And the last row is the mirror
image: a model shrunk into irrelevance keeps its full rank IC while its R² goes
to zero. Two different degenerate models each win one half of the gate.

Out of sample the R² loophole is much weaker (the intercept is the *train* mean,
not the test mean): measured on train-internal splits, the constant-at-train-mean
predictor scores R²_vs_zero of −0.000070, +0.000012, −0.000029, −0.000007 — i.e.
noise around zero. It is real but small; in-sample it is not small.

**Recommended gate (a), stated exactly:**
1. `R²_vs_zero > 0` **and** `R²_vs_mean > 0`, both reported. The second is the one
   that means "has conditional signal"; the first is the one D-07-18 names.
2. `rank_ic_non_tied > 0`, with `tie_fraction` reported beside it as a first-class
   number, and `rank_ic_all_rows` reported too (they answer different questions;
   neither is a correction of the other).
3. **A NaN IC is a FAIL with its own message.** `np.nan > 0` is `False`, so a
   naive comparison fails closed — but it reports "IC not positive" when the truth
   is "the prediction was constant and rankable-ness does not apply". Detect it.
4. The **constant-at-train-mean** predictor is scored as an explicit third column
   in every metrics table, alongside constant-zero. It costs one line and makes
   the loophole visible rather than latent.

### `spearmanr`'s tie behaviour, exactly

`[VERIFIED: scipy 1.18.1 `_stats_py.py` source]` `spearmanr` is
`a_ranked = np.apply_along_axis(rankdata, axisout, a)` followed by
`rs = np.corrcoef(a_ranked, rowvar=axisout)`. That is: **average ranks
(`rankdata`'s default `method="average"`) then ordinary Pearson.** There is no
separate tie-correction term — the average-rank assignment *is* the tie handling,
and Pearson-on-ranks is correct under ties, unlike the `6Σd²/(n(n²−1))` shortcut.

Consequences with a large plateau:

- Every one of the tied rows receives the identical rank `(i+j)/2 + 1`, so they
  contribute zero to the rank covariance *among themselves* but still contribute
  to the covariance with the prediction's ranks.
- The y-rank vector's standard deviation shrinks. Measured: at 43.89% ties the
  ratio to a tie-free uniform rank vector's sd is **0.9568** — only a 4.3%
  attenuation, far less than intuition suggests, because the plateau is one
  contiguous block rather than spread out.
- `spearmanr` emits `scipy.stats.ConstantInputWarning` and returns **NaN** when an
  input is constant. Measured directly (via the equivalent numpy computation): the
  constant predictor yields NaN, not 0.

**Does including the ties flatter the model? Measured: not reliably, and D-07-18's
stated reason is wrong.**

| segment | tie fraction | IC all rows | IC non-tied | ratio |
|---------|-------------|-------------|-------------|-------|
| 2026-09-18 | 9.77% | +0.207494 | +0.204823 | 0.987 (ties **inflate** by 1.3%) |
| 2026-09-13 | 43.89% | +0.363207 | +0.375057 | 1.033 (ties **deflate** by 3.2%) |

The sign of the gap depends on where the model's predictions for the tied rows
happen to fall, not on the tie fraction. So the honest framing for the plan is:
*the two statistics answer different questions* — "can the model order the whole
population?" versus "can it order the rows whose target actually moved?" — and the
second is the one a threshold-crossing policy monetises. Report both; do not
present either as a correction of the other; do not claim ties flatter.

**The honest non-tied statistic**, stated precisely: `spearmanr(pred[y != 0.0],
y[y != 0.0])`, with `tie_fraction = (y == 0.0).mean()` reported next to it, both
computed on the same scorable row set (finite features and finite target) used for
R². The exact-equality test `y == 0.0` is correct here — these are genuine exact
zeros produced by an unchanged mid, not near-zeros. On day 18's scorable rows
there are 65,888 distinct target values among 7.85M rows, so exact ties are
pervasive and intentional in the data, not a float artifact.

### The pitfall, named

**"Scale-free skill versus scale-dependent skill."** Rank IC is invariant to any
monotone rescaling of the prediction; R² is not. A model shrunk toward zero keeps
its full IC and loses all its R² (measured: IC +0.2075 unchanged, R²_vs_zero
0.000000). A model that captures only the unconditional drift has positive
R²_vs_zero and an undefined IC (measured: +0.001647 and NaN). **Neither is
skilful, and each passes one half of D-07-18(a).** The gate is only meaningful
because it requires *both* — which is the decision's real insight, worth stating
in `spec.md` in these terms.

### How much skill is actually there

`[VERIFIED: measured — fits and tests confined to days 12–17, which are inside
`train` under either recommended layout, so this forms no expectation about the
honest `val` look]`

| train days | test day | n(train) | n(test) | ties | R²_vs_zero | R²_vs_mean | IC all | IC non-tied |
|-----------|----------|---------|--------|------|-----------|-----------|--------|-------------|
| 12–14 | 15 | 22,286,604 | 12,137,024 | 12.41% | **+0.013244** | +0.013193 | +0.19796 | +0.19422 |
| 13–15 | 16 | 30,287,004 | 9,806,153 | 11.51% | **+0.016169** | +0.015988 | +0.20723 | +0.20431 |
| 14–16 | 17 | 33,228,305 | 8,430,721 | 17.62% | **+0.047509** | +0.047504 | +0.28921 | +0.29083 |
| 12–16 | 17 | 44,229,781 | 8,430,721 | 17.62% | **+0.047253** | +0.047248 | +0.28889 | +0.29052 |

Three-feature OLS, train-only z-score, no regularisation. Both halves of gate (a)
pass on every split, with margin. `imb_top` carries essentially all of it: on
standardised features the coefficients are ≈ `[4.6e-5, 1.8e-6, 7.4e-6]` against a
target sd of 3.3e-4.

**Disclosure — every research measurement in this document that read the candidate
`val` days (2026-09-17 / 2026-09-18).** All of them read Parquet directly with
polars. **None is a `harness.accessor.materialize` call**, so no look was spent,
no `harness-looks` MLflow run exists, and `budget.look_count` is still 0 for every
segment of every manifest. **None can steer model selection**, which D-07-04
confines to the OOF blocks. Listed in full so this is auditable rather than
asserted:

*Structural — the phase's own decisions require these numbers:*
1. Per-day row counts, null counts and exact-zero counts (§Q4's per-day table).
2. Admission counts for both candidate `val` windows (§Q1's tables) — D-07-01 needs
   them to specify the manifest at all.
3. The perfect-foresight ceiling and the `pred = mid` zero-trade oracle for both
   candidate `val` windows (§Q7) — D-07-19 explicitly requires re-measurement.

*Model fits — these are the ones worth disclosing:*
4. An in-sample 3-feature OLS on day 18 and on days 17+18: R²_vs_zero +0.022238 /
   +0.029105, R²_vs_mean, coefficients, and the raw/standardised Gram condition
   numbers (§Q3b, §Q4).
5. In-sample rank IC on day 18, all-rows and non-tied, plus the constant-at-mean
   and shrunk-by-1e-6 comparison rows (§Q4's pitfall table) — this is the evidence
   for the R²_vs_zero loophole, and day 13 carries the same demonstration.
6. A Gram-vs-dense ridge equivalence check at α ∈ {1e-6, 1, 100, 1e4} on day 18's
   rows, and the cross-process/cross-thread determinism hashes of the resulting
   coefficients and prediction array (§Q3a, §Q3c).
7. The prediction-table parquet byte measurement, which used day 18's `etime`/
   `decision_seq` columns and a day-18-fitted `pred` column (§Q1, D-07-15).

Items 4–7 produced numbers that sit **inside** the range the train-internal splits
(days 12–17) already establish, so nothing in the plan depends on them — they are
reported only so nobody mistakes a passing honest look for a surprise. **The plan
must carry this disclosure into its SUMMARY verbatim.**

---

## Q5 — The Trainer protocol that survives Phase 8

### What would leak if you got it wrong

| Phase 8 need | The naive leak | The containment |
|--------------|---------------|-----------------|
| LightGBM early stopping needs a validation set **during** `fit` | `Trainer.fit(X, y, X_eval, y_eval)` — two extra parameters three of four estimators ignore forever | The trainer receives **one** `FitInputs`, which already carries the OOF block geometry it may split however it likes. A sklearn trainer ignores the split; LightGBM carves its eval set out of its own training rows |
| PyTorch needs epochs, batch size, device, seed | `Trainer.fit(..., epochs=, batch_size=, device=)` | These are **hyperparameters**, not protocol parameters. They live in the `hyperparameters` mapping the trainer receives at construction and reports back for `predictor_id` |
| PyTorch is mini-batched over rows too large for RAM | `Trainer.fit(loader)` — an iterator abstraction sklearn does not want | `FitInputs` carries the **cached Parquet path** plus the column names, not materialised arrays. A sklearn trainer calls `.to_numpy()`; a torch trainer streams row groups. Neither imposes its shape on the other |
| A transformer predicts in batches | `FrozenPredictor.predict(loader)` | `predict(features: np.ndarray) -> np.ndarray` stays pure and pointwise; batching is the *implementation's* business, invisible at the boundary |
| Trees and nets are not linear, so "coefficients" is wrong | `FrozenPredictor` typed around `coef_`/`intercept_` | `to_artifact()/from_artifact()` exchange a plain JSON-able `dict`. A linear artifact holds coefficients; a tree artifact holds a booster dump; a net artifact holds a weights-file digest. `FrozenLinearPredictor` is one *implementation* |

### The proposal

The repo has **no `Protocol` anywhere yet** `[VERIFIED: grepped all production
modules]`; it uses `@dataclass(frozen=True)` (`features/normalize.py`,
`features/api.py`, `data/unit_registry.py`) and `NamedTuple` (`sim/outputs.py`).
So this is the first, and it should look like the repo: frozen dataclasses for the
data, `Protocol` for the two behaviours.

```python
# mvp/models/protocol.py
"""The Trainer/FrozenPredictor boundary (FCST-04).

Phase 8 adds LightGBM (early stopping, needs an eval set during fit) and a
PyTorch transformer (GPU, mini-batched, epochs) WITHOUT editing this file.
That is the design constraint, and it is what every choice below is for:

- fit() takes ONE FitInputs, never an (X_train, y_train, X_eval, y_eval)
  quadruple. Early stopping is a trainer's private business, carved out of the
  rows it was given; a sklearn trainer that will never early-stop is not made to
  carry two dead parameters for the rest of the project.
- FitInputs names a cached PARQUET PATH plus column names, never materialised
  arrays. A sklearn trainer calls .to_numpy(); a torch trainer streams row
  groups. 44,249,548 x 101 bytes is 4.5 GiB, so "just pass the arrays" is not
  neutral at this scale.
- epochs / batch_size / device / n_estimators are HYPERPARAMETERS, not protocol
  parameters -- they already flow through `hyperparameters`, which is also
  exactly what D-07-14 hashes into predictor_id. A protocol that named them
  would have to name every future class's knobs too.
- predict() is pointwise and pure: (n, p) float64 -> (n,) float64 of the TARGET
  unit (a 10s return, D-07-10). Batching, GPU transfer and tree traversal are
  implementation details that never reach this signature.
- to_artifact() returns a JSON-able dict, not a pickle (D-07-17). The
  linear implementation writes coefficients; a booster writes a model dump; a net
  writes a weights digest. Only the dict-ness is protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class FitInputs:
    """Everything a trainer may read, and nothing about HOW to read it."""
    cache_path: Path                 # the D-07-05 scratch Parquet for this segment
    feature_names: tuple[str, ...]   # D-07-09: exactly ("imb_top", "ofi", "trade_flow")
    target_name: str                 # D-07-10: "ret_10s_mid"
    row_mask_path: Path | None       # optional: the OOF training-row selection, if not the whole cache
    normalization_manifest_id: str   # D-07-11: resolved, never refitted
    seed: int


@dataclass(frozen=True)
class PredictInputs:
    """The same shape for the val/OOF read, so predict() never sees a fit-only field."""
    cache_path: Path
    feature_names: tuple[str, ...]
    normalization_manifest_id: str


@runtime_checkable
class FrozenPredictor(Protocol):
    """A fitted model, reduced to what is needed to reproduce its predictions."""

    @property
    def predictor_id(self) -> str:
        """D-07-14's sha256 over (class name, sorted hyperparameters, seed,
        code_hash, normalization_manifest_id)."""

    @property
    def model_class(self) -> str:
        """The MLflow `model_class` tag value, e.g. "sklearn.Ridge"."""

    def predict(self, features: np.ndarray) -> np.ndarray:
        """(n, p) float64 ALREADY-NORMALISED features -> (n,) float64 predictions
        in the TARGET's unit. Pure, pointwise, no I/O, no global state."""

    def to_artifact(self) -> dict[str, Any]:
        """A JSON-serialisable body (D-07-17). Never a pickle."""


@runtime_checkable
class Trainer(Protocol):
    """Fits one configuration. Constructed with its hyperparameters; `fit` takes
    only data."""

    @property
    def model_class(self) -> str: ...

    @property
    def hyperparameters(self) -> Mapping[str, Any]:
        """The full, JSON-able configuration -- what D-07-14 hashes and what
        MLflow logs. A trainer with no knobs returns {}."""

    def fit(self, inputs: FitInputs) -> FrozenPredictor: ...


def predictor_from_artifact(artifact: Mapping[str, Any]) -> FrozenPredictor:
    """Rebuild a FrozenPredictor from `to_artifact()`'s body, dispatching on
    artifact["model_class"]. Deliberately a free function, not a classmethod on
    the Protocol: a Protocol cannot carry one, and the registry of known classes
    is what Phase 8 extends -- by adding an entry, not by editing the Protocol."""
```

**Why `predict` takes already-normalised features.** Normalisation is resolved
from a manifest (D-07-11) and is identical for every model class, so putting it
inside `predict` would duplicate it four times and make the numpy-only
re-evaluation test import the normalisation loader. Keeping it out means the
D-07-17 test is literally `X_norm @ coef + b` with nothing else in scope — which
is the point of that test.

**Why `runtime_checkable`.** `isinstance(obj, Trainer)` then checks method
*presence* at the sweep's registration point, catching a typo'd method name at the
top of the run instead of after the first fold. It does not check signatures —
that is mypy's job, and the repo already runs neither mypy nor pyright in
pre-commit, so the Protocol's value here is documentation plus the presence check.

**The one file that imports sklearn** is `sklearn_track.py`. `frozen.py` imports
numpy only, which is what makes D-07-17's "a test that does not import sklearn at
all" mechanically assertable — e.g. by asserting `"sklearn" not in sys.modules`
after importing `models.frozen` in a fresh subprocess.

---

## Q6 — The prediction-table dataset

### `issue_manifest`'s contract for a new dataset name

`issue_manifest(dataset, symbol, stream, tier, schema_version, inputs,
partitions, code_hash, *, registry_root, dates=None)`. For `BTCUSDT.predictions`:

```python
PREDICTIONS_TIER = "predictions"     # add beside FEATURES_NORM_TIER in data/store.py

issue_manifest(
    dataset="BTCUSDT.predictions",
    symbol="BTCUSDT",
    stream=PREDICTIONS_TIER,
    tier=PREDICTIONS_TIER,
    schema_version=1,
    inputs=[...],                     # provenance chain, see below
    partitions=[partition_entry],     # exactly the write_normalization_artifact shape
    code_hash=compute_code_hash(),
    registry_root=lake_paths.LAKE_REGISTRY_ROOT,
    dates=[],                         # ← REQUIRED. See the trap below.
)
```

**`dates=[]` is not optional, it is load-bearing.** The function computes
`covered_dates = dates if dates is not None else sorted({p["date"] for p in
partitions})` **before** the `if tier not in BY_DATE_INDEXED_TIERS: covered_dates
= []` line. A partition entry without a `date` key therefore raises `KeyError`
even though the tier would never have written a pointer anyway.
`write_normalization_artifact` passes `dates=[]` explicitly and documents exactly
this; mirror it. Do **not** add `predictions` to `BY_DATE_INDEXED_TIERS` — a
prediction table belongs to a `(segment_manifest, segment, predictor)` triple, not
to a date, exactly as the normalisation tier belongs to a fold.

`partitions[0]` must be the same seven keys `write_normalization_artifact` builds:

```python
{"path": str(final_path.relative_to(lake_root)),   # lake-root-RELATIVE, canonical, not absolute
 "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
 "rows": table.height,
 "size_bytes": stat.st_size,
 "mtime_ns": stat.st_mtime_ns,                     # captured AT issuance, never later
 "etime_min": int(table["etime"].min()),
 "etime_max": int(table["etime"].max())}
```

`inputs` is a list of provenance entries with `manifest_id` plus `path`/`sha256`/
`rows`; chain it as `segment manifest → features_norm → predictions` so
`data_hash` in MLflow resolves to something that can be walked back.

**Refusals `issue_manifest` will apply:** empty `partitions` is refused ("a
manifest that names nothing verifies nothing"); a partition path already named by
any manifest of this dataset is refused (write-once — a re-run writes a NEW
`part-<ns>` file); a malformed path (absolute, escaping the lake root, or
non-canonical) is refused; naming one partition twice under any spelling is
refused.

### Partition path convention

`predictions/symbol=BTCUSDT/<discriminator>/part-<time.time_ns()>.parquet`,
following `features_norm`'s shape (`features_norm/symbol=<sym>/train_end=<date>/
part-<ns>.parquet`) rather than the date-partitioned curated/features shape. The
discriminator should carry D-07-14's key, e.g.
`segment=<segment_name>/predictor=<predictor_id[:16]>/`. Whatever is chosen, the
**parent directory must be globbed for `part-*.parquet` and the write refused if
any exists** — `write_normalization_artifact`'s own WR-05 lesson was that testing
`final_path.exists()` on a filename containing `time.time_ns()` is a guard that can
never fire, and two artifacts landed side by side.

`lake/predictions/` must exist as a **real directory** (not a symlink) with the
partition inside it and `st_nlink == 1`, or `_enforce_tier_containment` refuses
every read.

### What the three guardrails require — and the Phase 5 lesson does not apply

| Guardrail | How it enumerates | Does `BTCUSDT.predictions` need an edit? |
|-----------|-------------------|------------------------------------------|
| `check_manifest_id_integrity` | `REGISTRY_DIR_NAMES = ("manifests", "segments", "errata")`, then `registry_dir.glob("**/*.json")` | **No.** A new dataset is a new subdirectory *under* `manifests/`, already covered by the recursive glob. Its whole check is `json.loads` + `compute_manifest_id` + filename comparison, so a `predictions` body is judged identically |
| `check_manifest_append_only` | `REGISTRY_DIR_NAMES = frozenset({"manifests", "segments", "errata"})`, `dir_abs.glob("**/*.json")` | **No**, same reason. Requirement: once committed, the manifest JSON is never deleted, modified or type-changed in git history, and no two manifests may name one partition path with different `sha256` |
| `check_no_manifest_rewrite` | `registry_root / "manifests"` then `glob("**/*.json")`, against the real lake | **No.** Requirement: the parquet must exist at the named path and — for the per-commit `verify_manifest_fast` leg — its `(size_bytes, mtime_ns)` must still match. `--full` (pre-push + CI) recomputes sha256. On a host without `/Volumes/ProjectsSSD` it SKIPs honestly |

**Answering the Phase 5 question directly: no, that lesson does not apply here.**
Phase 5's one-commit requirement existed because `segments/` and `errata/` were
new **sibling registry roots** — peers of `manifests/`, invisible to a scanner
that only knew the one name, so the directory and the `REGISTRY_DIR_NAMES`
extension had to land together or the vacuity guard would pass over unscanned
files. `BTCUSDT.predictions` is a new **dataset subdirectory inside `manifests/`**,
which all three scanners already reach by recursive glob. It can land in whatever
commit suits the plan.

The real constraint is different and sharper: **once the predictions manifest is
committed, the parquet's mtime and bytes are frozen.** The per-commit
`verify_manifest_fast` hook compares `st_mtime_ns` exactly. Any tool that rewrites
or even touches the file — including a well-meaning re-run of the slice — fails
every subsequent commit until a NEW `part-<ns>` file and a NEW manifest are
issued. Write it once, checksum it, commit the manifest, never touch it again.

---

## Q7 — Re-measuring the perfect-foresight ceiling

### How Phase 6 computed 2,192 / 294,554 / $29.46

**There is no committed script.** `[VERIFIED: grepped `mvp/tests/sim/`,
`mvp/scripts/`, `mvp/tools/` — no real-day, perfect-foresight or `ret_10s_mid`
reference anywhere in the sim tests]` 06-07-SUMMARY.md says the measurement came
from "the same script run twice (once before editing the kernel, once after)"; the
script was ad hoc and only its **output** was committed, to
`.planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json`.

Phase 7 therefore has to write this measurement anyway. That is an argument for
putting it in `mvp/models/gates.py` as a real function with a real test, not in
another throwaway script.

### The procedure, reconstructed and re-verified

1. Take the **admitted** segment frame — the exact frame the simulator will walk,
   after admission and errata, in its own row order. Not the raw partition. (Phase
   6 read partitions directly and so never confronted admission; Phase 7 must,
   or the ceiling and the model P&L are measured over different row sets.)
2. `arrays = sim_arrays(frame)` → `etime`, `bid_ticks`, `ask_ticks`.
3. `pred = mid * (1.0 + ret_10s_mid)`, float64 — the perfect predictor is the
   actual future mid, via the identical `spec.md` conversion the model's
   prediction uses.
4. **Where `ret_10s_mid` is null, substitute `pred = mid`.** Phase 6 never needed
   this rule (its day had zero null labels). This phase does. See below.
5. `run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)` — everything else
   at its default ($100 notional, 0.001 BTC lot step, zero fee, zero latency).
6. Slice the trade log to `[:fill_count]`, then report
   `trades`/`flips`/`closed_pnl_ticks`.
7. `closed_pnl_ticks` in the Phase 6 convention is the **trade-log tick-only
   walk**, explicitly not `realized_pnl_scaled`:
   `Σ_{i≥1} side[i−1] · (price_ticks[i] − price_ticks[i−1])`.

### The tick → USD conversion, explicitly

```
1 tick              = TICK_SIZE_SCALED / PRICE_SCALE = 10_000_000 / 100_000_000 = $0.10
lot actually traded = LOT_STEP_SCALED  / QTY_SCALE   =    100_000 / 100_000_000 = 0.001 BTC
```

Phase 6 stated this wrong twice, in both directions, so do not restate it as an
algebraic identity — report two explicitly labelled numbers and one unambiguous
expression, and nothing else:

| convention | formula | day-13 reference |
|-----------|---------|------------------|
| **per 1 BTC** (`ticks × $0.10`) — what 06-RESEARCH.md printed and mislabelled "per one-lot" | `ticks × 0.10` | $29,455.40 |
| **actually realised at the traded 0.001-BTC lot** — the real money, and what Phase 9's "Net P&L > 0" must use | `ticks × 0.10 × 0.001` | **$29.4554** |

The factor between them is exactly 1000. Report both, labelled, every time.
Equivalently and least ambiguously: `equity_scaled[-1] × TICK_SIZE_SCALED /
PRICE_SCALE / QTY_SCALE` gives the realised-USD figure directly from the kernel's
own integer accumulator.

### Measured now, for both candidate `val` segments

`[VERIFIED: measured — the full procedure above, run against the real v2
partitions with `stale_book_age_ns` computed pre-slice exactly as the accessor
does, `NUMBA_CACHE_DIR` exported outside the repo]`

| | Option A `val` (days 17–18) | Option B `val` (day 18) |
|---|---|---|
| admitted rows walked | 16,294,059 | 7,859,419 |
| null `ret_10s_mid` given `pred = mid` | 8,614 | 4,586 |
| **trades** | **9,946** | **5,557** |
| flips | 9,945 | 5,556 |
| rows_in_market | 16,293,901 | 7,859,419 |
| **closed_pnl_ticks** | **1,120,460** | **605,389** |
| closed + unrealised (ticks) | 1,120,530 | 605,459 |
| **realised USD at the 0.001-BTC lot** | **$112.0460** | **$60.5389** |
| per-1-BTC convention | $112,046.00 | $60,538.90 |
| price range | $75,975.00 – $81,341.40 | $76,256.00 – $81,341.40 |
| all fills exactly one lot | yes | yes |
| sim wall clock | 0.8 s | 0.9 s |

**The `pred = mid` null-label convention is provably neutral, not merely
plausible.** `[VERIFIED: measured]` Running the kernel with `pred = mid` on every
row of both segments produces **0 trades**. The algebra: `mid` lies inside the
spread, so `floor(mid_ticks) ≤ ask_ticks` and `ceil(mid_ticks) ≥ bid_ticks`, and
at the minimum one-tick spread `mid` sits exactly on the half-tick where the
symmetric floor/ceil rule triggers neither side. Pin this as a named test — it is
the reason a null label cannot fabricate a trade, and Phase 6 has no such
convention to inherit from.

### Two honesty notes for the plan

1. **Read the ceiling from the CACHED `val` frame, not a second `materialize`.**
   Under D-07-05 the ceiling measurement, the model's prediction and the
   simulation all read one cache written by one look. A ceiling measurement that
   called `materialize` again would spend a second look for a number that is a
   property of the data.
2. **The "ceiling" is a strong sanity bound, not a theorem.** Perfect foresight
   fed to the flip-only rule at `x_bps=0` is a *particular* policy, not the P&L
   maximum over that price path — a coarser policy that declines some 1-tick
   crossings could in principle earn more. So a reported P&L at or above it is
   overwhelmingly a bug and D-07-19's exception is right; the exception message
   should say "investigate", not "impossible".

---

## Q8 — The MLflow run manifest for the slice

### `MANDATORY_TAG_KEYS`

`[VERIFIED: `tracking/mlflow_utils.py`, cross-pinned against `spec.md`'s "MLflow
tag schema" section by `tests/tracking/test_mlflow_utils.py`]` Exactly eight, and
`start_tracked_run` raises `MissingTagError` **before** any MLflow call if one is
absent:

`code_hash`, `data_hash`, `seed`, `env_hash`, `segment_manifest_id`,
`model_class`, `fold_config`, `stage`.

`spec.md` allows the literal `"n/a"` for `segment_manifest_id`/`model_class`/
`fold_config`/`stage` before their owning phase lands — Phase 7 is the phase that
lands all four, so it should pass real values for every one. `code_hash` =
`compute_code_hash()` (raises if git fails, never silently `""`); `env_hash` =
`compute_env_hash(mvp/uv.lock)`; `data_hash` = the manifest id the run consumes,
or the literal `"none"`.

### The blocking constraint: runs cannot nest

`[VERIFIED: measured, mlflow-skinny 3.13.0]` a second `mlflow.start_run()` while
one is active raises:

```
Exception: Run with UUID <id> is already active. To start a new run, first end the
current run with mlflow.end_run(). To start a nested run, call start_run with
nested=True
```

Note the type: a **bare `Exception`**, so a caller cannot catch it narrowly.
`start_tracked_run` calls `mlflow.start_run(experiment_id=..., tags=...)` with no
`nested=True`, and both `budget.record_look` and
`negative_log.record_negative_result` go through `start_tracked_run`. Therefore:

- **`materialize` cannot be called while the slice's own run is open.**
- **`record_negative_result` cannot be called while the slice's own run is open.**

This is not a bug to fix in Phase 2's module — it is a sequencing constraint the
slice must respect.

### Answer: three kinds of runs, sequenced, never nested

| # | Experiment | Created by | When | Count in Phase 7 |
|---|-----------|-----------|------|------------------|
| 1 | `harness-looks` | `budget.record_look` inside `materialize` | one per look, at materialize time | `train` = 0 (not a look) + 5 `oof_block_*` + **1 `val`** |
| 2 | `harness-negative-results` | `negative_log.record_negative_result` | one per config failing a gate (D-07-20) | as many as the counted grid has losers |
| 3 | `stage-1-regression` | the slice, via `start_tracked_run` | **LAST**, after every look and negative is closed | **1** |

Run 1 fills `segment_manifest_id` and `stage="val_look"` itself and requires the
caller to have set `fold_config` (the accessor does, from the manifest's `layout`,
plus an additive `fold_config_reason`). Run 2 fills `stage="negative_result"`,
`outcome`, `reason` and `config_fingerprint`.

**They are not one run, and the reason is mechanical, not stylistic:** the budget
counter *is* `len(search_runs(filter tags.segment_manifest_id = … and
tags.segment_name = …))`. A look must be its own run or it cannot be counted, and
`record_look` holds an `fcntl.flock` across check-then-create for exactly that
reason. Folding the slice's metrics into a look run would make the budget
uncountable; folding a look into the slice run is impossible because the run
cannot nest.

### What run 3 must carry for "the run manifest in MLflow" to be genuinely satisfied

Success criterion 3 is about *provenance closure* — someone holding only the run
must be able to rebuild every byte. Tags (the eight mandatory, plus additive):

- `stage="stage1_regression"`, `model_class` = the winner's class,
  `fold_config="compressed_3seg"`, `segment_manifest_id` = the new manifest's id,
  (`[VERIFIED: spec.md's "MLflow tag schema" section]` the schema constrains only
  that `stage` be PRESENT and permits the literal `"n/a"`; it fixes no vocabulary.
  `"val_look"` and `"negative_result"` are set in `budget.py`/`negative_log.py`,
  not in the spec, so Phase 7 is free to name its own — pick one and add it to
  spec.md's section so the next phase inherits a list rather than a guess);
  `seed`, `code_hash` (clean, no `-dirty`), `env_hash`;
- `data_hash` = the **prediction-table manifest id** — that is what this run
  consumed and produced, and it chains back through `inputs` to `features_norm`
  and to the seven feature manifests;
- additive: `predictor_id` (D-07-14), `normalization_manifest_id`,
  `prediction_table_manifest_id`, `fold_config_reason`, `segment_name="val"`,
  `look_run_ids` (comma-joined; shard via `_shard_values` if it ever exceeds 8000
  chars — MLflow **truncates** rather than raising).

Params: the winning hyperparameters, the grid size actually evaluated (the
"counted" in D-07-08's counted grid), the feature-name tuple, the target name.

Metrics: `r2_vs_zero`, `r2_vs_mean`, `r2_vs_zero_of_constant_train_mean`,
`rank_ic_all`, `rank_ic_non_tied`, `tie_fraction`, `n_scorable`, `n_admitted`,
**`n_pred_missing`** (how many rows got the neutral `mid` fill because the model
produced no prediction — without this, an all-null table reads as "no signal"
rather than "nothing was predicted"); the diagnostic-horizon R²s (D-07-10);
`sim_trades`, `sim_flips`, `sim_closed_pnl_ticks`, `sim_pnl_usd`;
`ceiling_trades`, `ceiling_closed_pnl_ticks`, `ceiling_pnl_usd`, and
`pnl_fraction_of_ceiling` — that last one is the single number a human reads to
know whether D-07-19's guard was close to firing.

`log_data_provenance` should be called on run 3 so `data_manifest_ids`,
`dq_ack_ids` and `dq_ack_sha256` land too; note it uses `set_tags` after creation
deliberately (the atomicity rule covers the eight mandatory keys only).

### Recommended slice sequencing

```
1.  materialize("train")                 → cache     [no look]
2.  fit + write the features_norm partition, issue its manifest
3.  for j in 0..4: materialize(f"oof_block_{j}") → cache   [5 looks, each its own run]
    (steps 1 and 3 may be one pass; the point is that NO run of the slice's own is open)
4.  sweep 4 estimators × grid on the OOF caches → pick the winner
    losers → record_negative_result                       [one run each]
5.  materialize("val")                   → cache     [THE ONE HONEST LOOK, own run]
6.  ── from here on, no materialize and no record_negative_result ──
7.  predict, write + issue the prediction table
8.  measure the ceiling from the cached val frame; run the sim; evaluate the gates
9.  start_tracked_run("stage-1-regression") … log everything … end
```

Steps 4 and 5 are also where D-07-20 bites hardest: a config that fails a gate on
the OOF blocks must be recorded *before* step 5, because after step 5 no new
MLflow run of kind 2 can be opened without closing run 3 — and run 3 should not
exist yet at step 5 anyway.

---

## Q9 — Risks and anti-patterns specific to this phase

Ordered by expected cost.

### R1 — A prediction table misaligned with the decision rows by one row

**What goes wrong:** the simulator walks 16,294,059 rows with a `pred` array
shifted by one, producing a plausible P&L that is pure look-ahead or pure noise,
with nothing failing.
**Why it happens:** `harness.errata.mask_errata_cells` joins with polars' default
`maintain_order=None`, documented as "might differ across Polars versions or even
between different runs", and it runs on every look-role frame.
**How to avoid:** assert strictly-ascending `etime` on the frame the instant
`materialize` returns; emit the table positionally from that frame; on read-back
assert `np.array_equal` on both key columns before touching `pred`; if any join is
used, pass `maintain_order="left", validate="1:1"` explicitly. Consider adding
`maintain_order="left"` to `mask_errata_cells`' own join — a one-line production
fix that removes the class of bug rather than detecting it.
**Warning signs:** a P&L near or above the ceiling; `rank_ic` that flips sign
between the streaming OOF metric and the table-based one; `trades` wildly
different from the ceiling's.

### R2 — Silently spending looks during development

**What goes wrong:** the budget shows 3 spent before the honest evaluation ever
runs, and `issue_segment_manifest` then refuses any overlapping replacement.
**Why it happens:** pre-commit hook 19 is `pytest tests -x -q` — the **whole**
suite, on **every** commit. A single test that reaches `materialize` against the
canonical roots burns a look per commit. Ad-hoc debugging scripts do the same.
**How to avoid:** (i) every `tests/models/` test uses `tests/fixtures/lake` +
`tests/fixtures/lake_registry` and a `tmp_path` tracking root — never
`lake_paths.LAKE_REGISTRY_ROOT` / `mlflow_tracking_root()`; (ii) the slice script
separates a `--segments oof_*` dev mode from an explicit `--spend-val-look` flag
that refuses unless the OOF-winner file already exists on disk; (iii) **key the
frame cache by the tracking root's resolved path** — a `val` frame cached during a
scratch-root dry run is byte-identical to the real one and cost nothing, so
letting it feed the real run is a budget bypass that would read as spent;
(iv) print `budget.look_count` for `val` and all five blocks at the top and bottom
of every slice invocation, so a leak is visible the first time it happens rather
than on the fourth.
**Warning signs:** `look_count("val") > 0` before step 5; a `harness-looks` run
whose `code_hash` carries `-dirty`.

### R3 — A "beats baseline" result that is really an artifact

**What goes wrong:** the gate passes on a model with no conditional skill.
**Why it happens:** measured in §Q4 — a constant predictor at the sample mean
scores R²_vs_zero = +0.001647; a model shrunk by 1e-6 keeps IC = +0.207 with
R²_vs_zero = 0.
**How to avoid:** require R²_vs_mean > 0 as well as R²_vs_zero > 0; score the
constant-at-train-mean predictor as an explicit column in every metrics table;
treat a NaN IC as a distinctly-messaged FAIL; report the tie fraction per segment.
**The leakage variant to check separately:** normalisation parameters. D-07-11
fits one artifact over the **whole** train segment, so an OOF block's metrics are
computed with a mean/std that saw that block's rows. For `LinearRegression` and
any model with an intercept this is exactly zero leakage — an affine rescaling of
the design matrix is absorbed by the coefficients and the intercept. For `Ridge`
and `ElasticNet` the penalty is *not* scale-invariant, so the leak is nonzero but
confined to two scalars per feature estimated from ~44M rows. Document the
magnitude argument in `spec.md`; do not relitigate D-07-11, and do not silently
refit per block (that would violate it).
**Warning signs:** OOF R² above the ~0.013–0.048 band the train-internal splits
measured; an OOF winner whose `val` R² is dramatically higher.

### R4 — float32 / float64 at the tick boundary

**What goes wrong:** a prediction half a tick from a quote flips a trigger, and
the simulated trade count moves by percent.
**Why it happens:** Phase 6 measured that perfect-foresight `pred` sits **exactly**
on a half-tick boundary on 99.96% of real rows (the mid of a one-tick spread always
does), and it spent a whole plan fixing an asymmetric rounding rule that this
exposed. A float32 round-trip perturbs `round(pred * 1e8)` by far more than the
half-tick margin.
**How to avoid:** `pred Float64` in the table (D-07-16) and `np.float64` into the
kernel — which `run_sim_checked` enforces by raising on any other dtype. Do the
`mid * (1.0 + pred)` multiply in float64. Never route a prediction through
`sim.ticks.price_to_ticks` (its round-trip proof is for bid/ask, and it *refuses*
exact half-ticks). Add a test that the stored parquet's `pred` column reads back
with `schema["pred"] == pl.Float64` and that the bytes round-trip exactly.
**Warning signs:** `trades` differing between an in-memory run and a
write-then-read-then-run of the same predictor.

### R5 — A Trainer protocol that hardcodes sklearn

**What goes wrong:** Phase 8 must edit `protocol.py` to add LightGBM, and the
edit ripples into all four Phase 7 implementations plus their tests.
**Why it happens:** early stopping wants `(X_eval, y_eval)` in `fit`; mini-batching
wants a loader; a tree has no `coef_`.
**How to avoid:** §Q5 — one `FitInputs` carrying a path, hyperparameters as a
mapping, `to_artifact() -> dict`, `predict` pointwise on already-normalised
arrays, and `sklearn` imported in exactly one module.
**Warning signs:** any sklearn type in a `protocol.py` annotation; the word
`epochs`, `batch`, `early_stopping` or `device` appearing in a protocol signature.

### R6 — The manifest issuance that cannot be repeated

**What goes wrong:** the manifest is issued with a wrong boundary, and by the time
anyone notices, looks have been spent — so
`_refuse_overlap_with_exhausted_segments` blocks the corrected replacement.
**Why it happens:** it costs ~5 min and ~19 GB, so nobody re-runs it casually; and
its derived fields (`admission.counts`, `oof_training_row_counts`) are only
inspectable *after* issuance.
**How to avoid:** the ns boundaries and every derived count are measured in §Q1 —
assert them against the returned manifest body immediately, in the issuance
script, before any look is spent. A mismatch means the layout is not what was
intended, and it is still free to fix.

### R7 — Memory

**What goes wrong:** issuance or the first `materialize` gets OOM-killed on a
32 GiB host, halfway through.
**Why it happens:** 19.35 GiB measured peak footprint for one issuance pass; every
`materialize` reloads 5.75 GiB and copies it per filter.
**How to avoid:** run these steps with nothing else large resident; read only the
four needed columns out of the cached Parquet for the fits; never hold two
segments' frames live at once.

### R8 — Stray artifacts

`@njit(cache=True)` writes `*.nbi`/`*.nbc` next to the defining source unless
`NUMBA_CACHE_DIR` is set. `tests/conftest.py` pins it for pytest; **any ad-hoc
script must export it itself**, outside the repo. `tests/features/test_time_ns.py`
asserts both that the variable points outside the package tree and that no cache
artifact has landed under `mvp/`.

---

## Code Examples

### Building a prediction table with provable alignment

```python
# Source: this repo -- sim/arrays.py SIM_ARRAY_SCHEMA, sim/kernel.py run_sim_checked,
# spec.md "Decision rule (Stage 2)"
import numpy as np, polars as pl
from features.normalize import apply_normalization
from sim.arrays import sim_arrays
from sim.kernel import run_sim_checked

FEATURES = ("imb_top", "ofi", "trade_flow")          # D-07-09; mid/bid/ask are BOOKKEEPING

def assert_decision_order(frame: pl.DataFrame) -> np.ndarray:
    e = frame["etime"].to_numpy()
    if e.size and not np.all(np.diff(e) > 0):
        raise ValueError(
            "frame is not strictly etime-ascending -- sim.kernel.run_sim is a "
            "sequential scan and would be silently wrong. polars' join order is "
            "unspecified by default (maintain_order=None) and harness.errata."
            "mask_errata_cells joins on every look-role frame."
        )
    return e

def prediction_table(frame, predictor, norm_params) -> pl.DataFrame:
    assert_decision_order(frame)
    cols = [apply_normalization(frame[name].to_numpy(), norm_params[name]) for name in FEATURES]
    X = np.column_stack(cols)                         # (n, 3) float64
    finite = np.isfinite(X).all(axis=1)
    pred_ret = np.zeros(frame.height, dtype=np.float64)
    pred_ret[finite] = predictor.predict(X[finite])    # (n,) float64, a 10s RETURN
    pred_ret[~finite] = np.nan                         # honest: no prediction, not a fabricated 0.0
    return pl.DataFrame({
        "etime":        frame["etime"],                 # positional, same frame, no join
        "decision_seq": frame["decision_seq"],
        "pred":         pl.Series(pred_ret, dtype=pl.Float64),   # D-07-16
    })

def assert_table_aligned(frame: pl.DataFrame, table: pl.DataFrame) -> None:
    e = assert_decision_order(frame)
    if table.height != frame.height:
        raise ValueError(f"prediction table height {table.height} != frame height {frame.height}")
    if not np.array_equal(table["etime"].to_numpy(), e):
        raise ValueError("prediction table etime sequence != the frame it claims to score")
    if not np.array_equal(table["decision_seq"].to_numpy(), frame["decision_seq"].to_numpy()):
        raise ValueError("prediction table decision_seq sequence != the frame's")


def neutral_fill_null_predictions(pred_ret: np.ndarray, mid: np.ndarray) -> tuple[np.ndarray, int]:
    """Convert a 10s RETURN to the simulator's raw-PRICE `pred` (spec.md's
    decision rule, the ONE conversion site), substituting the neutral `mid`
    wherever no prediction exists.

    RETURNS THE COUNT, and the caller MUST log it. `pred = mid` is measured to
    produce 0 trades on both candidate val segments, so a null can never
    fabricate a trade -- which is exactly why an inline `np.where` is the wrong
    shape here: a table that is ENTIRELY NaN simulates cleanly, reports zero
    trades, and looks like a model with no signal rather than a pipeline that
    predicted nothing. Same discipline as `SimResult.fill_count`: the reader sees
    the number before the hazard is hidden.
    """
    missing = ~np.isfinite(pred_ret)
    pred_px = np.where(missing, mid, mid * (1.0 + pred_ret)).astype(np.float64)
    return pred_px, int(missing.sum())


def simulate(frame, table) -> tuple["SimResult", int]:
    assert_table_aligned(frame, table)
    arrays = sim_arrays(frame)                                  # asserts no nulls, exact dtypes
    pred_px, n_pred_missing = neutral_fill_null_predictions(
        table["pred"].to_numpy(), frame["mid"].to_numpy()
    )
    if n_pred_missing == frame.height:
        raise ValueError(
            "every prediction is null -- the simulator would report 0 trades and "
            "that would read as 'no signal' rather than 'nothing was predicted'"
        )
    result = run_sim_checked(arrays["etime"], arrays["bid_ticks"], arrays["ask_ticks"],
                             pred_px, x_bps=0)
    return result, n_pred_missing        # caller logs n_pred_missing as a run-3 metric
```

### The two gates and the ceiling guard

```python
# Source: D-07-18/19; statistics verified against scipy 1.18.1 _stats_py.spearmanr
import warnings

import numpy as np
from scipy.stats import ConstantInputWarning, spearmanr

def forecast_metrics(pred: np.ndarray, y: np.ndarray) -> dict:
    ok = np.isfinite(pred) & np.isfinite(y)
    p, t = pred[ok], y[ok]
    sse = float(((t - p) ** 2).sum())
    ss_zero = float((t ** 2).sum())                  # reference = the constant-ZERO predictor
    ss_mean = float(((t - t.mean()) ** 2).sum())     # reference = the unconditional MEAN
    tie = t == 0.0        # exact zeros, not near-zeros: 65,888 distinct target
                          # values among 7.85M rows, so ties are intentional data
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConstantInputWarning)
        try:   ic_all = float(spearmanr(p, t).statistic)
        except ConstantInputWarning: ic_all = float("nan")
        try:   ic_sub = float(spearmanr(p[~tie], t[~tie]).statistic)
        except ConstantInputWarning: ic_sub = float("nan")
    return {"n_scorable": int(ok.sum()),
            "r2_vs_zero": 1.0 - sse / ss_zero,
            "r2_vs_mean": 1.0 - sse / ss_mean,
            "rank_ic_all": ic_all, "rank_ic_non_tied": ic_sub,
            "tie_fraction": float(tie.mean()),
            # the zero-skill reference that makes the r2_vs_zero loophole visible:
            "r2_vs_zero_of_constant_train_mean": 1.0 - float(((t - p.mean()) ** 2).sum()) / ss_zero}

def gate_forecast(m: dict) -> tuple[bool, str]:
    if not np.isfinite(m["rank_ic_non_tied"]):
        return False, ("rank IC is NaN -- scipy.stats.spearmanr returns NaN for a CONSTANT input. "
                       "The prediction has no ordering at all; this is not a small positive IC.")
    if m["r2_vs_zero"] <= 0:  return False, f"r2_vs_zero={m['r2_vs_zero']:.6g} <= 0"
    if m["r2_vs_mean"] <= 0:  return False, (f"r2_vs_mean={m['r2_vs_mean']:.6g} <= 0 -- beats the "
                                             "constant-zero reference but not the unconditional mean, "
                                             "i.e. it learned the drift and nothing conditional "
                                             "(measured: a constant at the sample mean scores "
                                             "r2_vs_zero=+0.001647 on 2026-09-18)")
    if m["rank_ic_non_tied"] <= 0: return False, f"rank_ic_non_tied={m['rank_ic_non_tied']:.6g} <= 0"
    return True, "ok"

def guard_against_ceiling(pnl_ticks: int, ceiling_ticks: int, segment_name: str) -> None:
    if pnl_ticks >= ceiling_ticks:                   # D-07-19
        raise ValueError(
            f"{segment_name}: reported {pnl_ticks} closed_pnl_ticks at or above the measured "
            f"perfect-foresight ceiling {ceiling_ticks} -- INVESTIGATE. Most likely a prediction "
            "table misaligned with the decision rows, or a label leaking into a feature. "
            "(The ceiling is a strong sanity bound, not a theorem: perfect foresight through the "
            "flip-only rule at x_bps=0 is one policy, not the path's P&L maximum.)"
        )
```

---

## State of the Art

| Old approach | Current approach | When changed | Impact on this phase |
|--------------|------------------|--------------|----------------------|
| Rely on polars join order | `maintain_order` must be set explicitly | polars 1.x; docstring is explicit in 1.41.2 | R1 — the phase's top risk |
| `OMP_NUM_THREADS=1` controls "the BLAS" | macOS arm64 numpy ≥2.0 wheels link **Apple Accelerate**, which honours none of OMP/OPENBLAS/MKL and has no threadpoolctl controller | numpy 2.0 (2024) | D-07-12's mechanism is inert on the dev Mac; determinism had to be measured |
| joblib depends on nothing | joblib 1.6.0 hard-depends on `cloudpickle>=3.0` | joblib 1.6.0 | one extra lock entry D-07-13 did not enumerate |
| `Ridge` dense picks an iterative solver | `solver="auto"` → `cholesky` (explicit `resolve_solver_for_numpy`) | current in 1.9.1 | the Gram oracle in §Q3c is the *same* computation, hence exactly checkable |
| `mlflow` monolith with flavors | `mlflow-skinny` — no `mlflow.sklearn` | this repo's own pin | D-07-21; explicit `log_metric`/`log_param` only |
| Spearman via `6Σd²/(n(n²−1))` | `corrcoef(rankdata(a), rankdata(b))` | long settled; verified in scipy 1.18.1 | correct under this target's 9.8%–69.3% tie mass |

**Deprecated / not applicable here:** `mlflow.sklearn.autolog` (not installed);
`skfolio`/`mlfinlab`/`timeseriescv` for purged CV (rejected in CLAUDE.md — pandas,
unmaintained, paywalled respectively; `harness.kfold` is the project's own and is
already written); `pickle` for model persistence (D-07-17).

---

## Assumptions Log

| # | Claim | Section | Risk if wrong |
|---|-------|---------|---------------|
| A1 | sklearn 1.9.1's executed behaviour matches its 1.9.1 source as read (Ridge `auto`→`cholesky`, ElasticNet `rng` unused under `selection="cyclic"`, LinearRegression→`lstsq`) | Q3a | A seed would matter where this says it does not. Cheap check the plan should include: after `uv sync`, assert `fitted.solver_ == "cholesky"` on a dense fit, and assert two fits with different `random_state` give identical coefficients |
| A2 | `uv lock` resolves to scikit-learn 1.9.1, scipy 1.18.1, joblib 1.6.0, cloudpickle 3.1.2, narwhals 2.26.0, threadpoolctl 3.7.0 | Standard Stack | Only the resolution changes, not the conclusions — the no-pandas result holds for every version in these lines, and `check_pin_versions` gates it mechanically |
| A3 | `scipy.stats.spearmanr(...).statistic` is the accessor name in 1.18.1 (vs the older tuple unpacking) | Code Examples | A trivial `AttributeError` on first run; `SignificanceResult` has exposed `.statistic` since scipy 1.9, so this is low risk but unexecuted |
| A4 | The Linux CI runner never executes a fit on real data (no lake mounted → `check_no_manifest_rewrite` SKIPs, and no test may use canonical roots) | Q3a, Validation Architecture | If a CI job did fit on real data, cross-platform coefficient agreement would become a requirement and Accelerate-vs-OpenBLAS would break any pinned hash |
| A5 | `mid` is excluded from the design matrix even though the `features_norm` artifact carries a `mid` row | Anti-patterns | D-07-09 violated silently. The refusal must be an explicit allow-list assertion, which is why it is called out rather than assumed |

---

## Open Questions

1. **Option A or Option B for the train/val split.**
   - Known: every number for both, measured (§Q1), including both ceilings (§Q7).
   - Unclear: nothing factual. It is a judgement between one-calendar-day OOF
     blocks plus a two-day `val` (A) and a larger train plus a smaller stored table (B).
   - Recommendation: **A.** Measured evidence that B's extra train data buys
     nothing (R² 0.047253 vs 0.047509), and A's 23.99999989 h blocks are one
     calendar day each, which Phase 8's per-fold ranking will want.

2. **Does the plan store the diagnostic-horizon metrics only, or also diagnostic prediction tables?**
   - Known: D-07-10 says the diagnostic horizons are "reported alongside but never
     fitted"; D-07-15 says only the table feeding the simulator is stored.
   - Unclear: whether "reported" means R² of the `ret_10s_mid` model against each
     other horizon (cheap, no extra table) or something more.
   - Recommendation: the cheap reading — four R²/IC pairs from the one `pred`
     array against four target columns, logged as metrics on run 3. No extra bytes.

3. **`errata_id` on the new manifest: keep or null?**
   - Known: the 249-cell list is a measured no-op on v2; `version=1` is required to
     keep it (catalogue version, not feature `schema_version`).
   - Recommendation: keep it with `version=1`, and record the measured no-op. A
     live gate that masks nothing beats a disabled one.

4. **Should `mask_errata_cells` gain `maintain_order="left"`?**
   - Known: order is preserved today (measured) and documented as unspecified.
   - Unclear: whether a one-line change to a Phase 5 harness module belongs in
     Phase 7's scope.
   - Recommendation: yes, as a correctness fix with its own test, because Phase 7
     is the first consumer for which the order is load-bearing. If the plan
     declines, the §Q9-R1 assertion becomes mandatory rather than merely
     recommended.

---

## Environment Availability

| Dependency | Required by | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | everything | ✓ | 3.13 (`requires-python >=3.13,<3.14`) | — |
| polars | frames, parquet | ✓ | 1.41.2 | — |
| numpy | arrays, the Gram oracle | ✓ | 2.4.6 (BLAS/LAPACK = **Apple Accelerate**) | — |
| numba | the sim kernel | ✓ | 0.65.1 | — |
| mlflow-skinny | all tracking | ✓ | 3.13.0 (**no** `mlflow.sklearn`) | none — D-07-21 |
| **scikit-learn** | FCST-01's four estimators | ✗ | — | none. `uv add` + `uv lock` + `uv sync --frozen` is Wave 0 work |
| **scipy** | `spearmanr` (D-07-18) | ✗ | — | arrives with sklearn |
| **joblib / cloudpickle / narwhals / threadpoolctl** | sklearn closure | ✗ | — | arrive with sklearn |
| feature pool (7 v2 partitions) | everything | ✓ | 60,926,503 rows, 2026-09-12..18 | none — capture is STOPPED and the pool cannot grow |
| lake root `/Volumes/ProjectsSSD/aihedgefund/lake` | partitions, new `predictions/` tier | ✓ | 787 GiB free of 1.8 TiB | — |
| MLflow store `/Volumes/ProjectsSSD/aihedgefund/mlflow` | looks, negatives, run 3 | ✓ | canonical root per `lake_paths` | — |
| RAM | issuance (19.35 GiB peak measured) | ✓ | 32 GiB, 8 cores (M1 Pro) | none — run it with nothing else large resident |
| pandas | — | ✗ (banned) | — | polars; CI-enforced by `check_pin_versions` + ruff TID251 |
| statsmodels, matplotlib | Phase 9 (EVAL-05) | ✗ | — | out of scope, deliberately |

**Missing with no fallback:** scikit-learn and its closure. This is Wave 0: one
`uv add`, one `uv lock`, one `uv sync --frozen`, then confirm `numpy` is still
2.4.6 and that `check_pin_versions` still passes (it will — the pandas ban is the
only thing that could fire, and nothing in the closure requires pandas).

---

## Validation Architecture

### Test framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.x + hypothesis 6.x (+ pytest-cov ≥7.1) |
| Config file | `mvp/pyproject.toml` → `[tool.pytest.ini_options]`: `pythonpath=["."]`, `testpaths=["tests"]`, `addopts=["--import-mode=importlib"]` |
| Shared fixtures | `mvp/tests/conftest.py` — pins `NUMBA_CACHE_DIR` before any numba-reaching import |
| Quick run command | `uv run --locked --directory mvp pytest tests/models -x -q` |
| Full suite command | `uv run --locked --directory mvp pytest tests -x -q` (this is pre-commit hook 19 — it already runs on every commit) |
| Guardrails | 19 pre-commit hooks; commands byte-identical to `.github/workflows/ci.yml`, enforced by a parity test |
| Hard rule | **no `mvp/tests/models/__init__.py`** — `mvp/models/` exists; five prior instances of that mistake, all bugs |

### Success criterion → observable → test map

| Criterion | Observable that proves it | Test type | Automated command | Exists? |
|-----------|---------------------------|-----------|-------------------|---------|
| SC1 — regression track trains on harness folds and beats the zero baseline on validation | `r2_vs_zero > 0` **and** `r2_vs_mean > 0` **and** `rank_ic_non_tied > 0` on the `val` frame, with `tie_fraction` reported; plus the constant-at-train-mean column showing the loophole is not what passed | unit (metrics on fixtures) + one real-data slice run | `pytest tests/models/test_metrics.py -x` | ❌ Wave 0 |
| SC1 — the four estimators actually differ and the grid is counted | a metrics table with one row per (class, hyperparams) and `n_configs` logged as a param; every loser present in `harness-negative-results` | unit + integration on the fixture lake | `pytest tests/models/test_sweep.py -x` | ❌ Wave 0 |
| SC1 — `mid`/`bid_price`/`ask_price` never reach an estimator (D-07-09) | a `ValueError` raised when a forbidden name is passed, asserted — not merely the absence of those columns | unit | `pytest tests/models/test_feature_allowlist.py -x` | ❌ Wave 0 |
| SC2 — frozen predictor is deterministic | sha256 of the prediction table from a **fresh subprocess** == the in-process sha256, on the same host; plus a deliberate sentinel mutation observed to break it | integration (subprocess) | `pytest tests/models/test_frozen_determinism.py -x` | ❌ Wave 0 |
| SC2 — frozen predictor is genuinely frozen (D-07-17) | the JSON re-evaluated by a numpy dot product reproduces `predict()` bit-for-bit, in a process where `"sklearn" not in sys.modules` | integration (subprocess) | `pytest tests/models/test_frozen_no_sklearn.py -x` | ❌ Wave 0 |
| SC2 — prediction table keyed by the D-07-14 triple | `predictor_id` changes when any of (class, hyperparams, seed, code_hash, norm manifest id) changes, and is stable otherwise | unit | `pytest tests/models/test_predictor_id.py -x` | ❌ Wave 0 |
| SC3 — the slice runs end to end | one `stage-1-regression` MLflow run carrying all eight mandatory tags plus `predictor_id`, `normalization_manifest_id`, `prediction_table_manifest_id`, `look_run_ids`, and the sim/ceiling metrics | integration on the **fixture** lake + `tmp_path` tracking root | `pytest tests/models/test_slice_end_to_end.py -x` | ❌ Wave 0 |
| SC3 — row alignment is proven, not assumed | `array_equal` on both key columns; and a mutation test that shifts the table by one row and observes the assertion fire | unit + mutation | `pytest tests/models/test_prediction_table.py -x` | ❌ Wave 0 |
| SC3 — the ceiling guard bites | a fabricated P&L at the ceiling raises; at ceiling−1 it does not | unit | `pytest tests/models/test_gates.py -x` | ❌ Wave 0 |
| SC3 — `pred = mid` cannot fabricate a trade | `run_sim_checked` with `pred = mid` on a real-shaped fixture returns `trades == 0`; and a full-tick offset DOES trade (anti-vacuity) | unit | `pytest tests/models/test_neutral_pred.py -x` | ❌ Wave 0 |
| Cross-check — sklearn's Ridge agrees with the Gram oracle | `np.allclose(sklearn_coef, gram_coef, rtol=1e-9)` on a fixture-scale matrix, at three α | unit | `pytest tests/models/test_ridge_oracle.py -x` | ❌ Wave 0 |
| Manual-only — the honest `val` look | one real `materialize("val")`, once, by the slice script with `--spend-val-look`. **Cannot be automated**: repeating it in a test would spend the budget on every commit | manual (recorded in the SUMMARY) | `python -m models.slice --spend-val-look` via `./.venv/bin/python3` | n/a |

### Sampling rate

- **Per task commit:** `pytest tests/models -x -q` (the new tests only) — seconds.
- **Every commit, unavoidably:** `pytest tests -x -q` + `pytest tests/leakage -x -q`
  (hooks 18 and 19). **This is the reason every `tests/models/` test must use the
  fixture lake and a `tmp_path` tracking root** — the full suite runs whether the
  plan wants it to or not.
- **Per wave merge:** all 19 hooks, plus `uv lock --check` and `check_pin_versions`
  (the new dependency's two gates).
- **Pre-push:** `check_no_manifest_rewrite --full` — recomputes sha256 for every
  committed manifest including the new `predictions` one. After that manifest is
  committed, the parquet's bytes **and mtime** are frozen.
- **Phase gate:** full suite green, 19/19 hooks green, CI green, before
  `/gsd-verify-work`.

### Wave 0 gaps

- [ ] `uv add "scikit-learn==1.9.*"` + `uv lock` + `uv sync --frozen`; re-assert `numpy == 2.4.6`
- [ ] `mvp/tests/models/` created **without** `__init__.py`
- [ ] A fixture-lake builder for a `predictions` tier partition (mirror
      `tests/fixtures/harness_span.py`, which must also write an `'ok'` DQ report
      row — `load_features` requires one unconditionally)
- [ ] A fixture-scale `compressed_3seg` segment manifest whose `val` is small
      enough for the full suite to materialize on every commit against a
      `tmp_path` canonical tracking root
- [ ] Each of the eleven test files above
- [ ] A named assertion that `NUMBA_CACHE_DIR` stays outside the package tree for
      the slice script's own invocation path (the existing test covers pytest only)

---

## Security Domain

Applicable ASVS categories for a local, single-user, offline research pipeline with
no network listener, no authentication surface and no untrusted input:

| ASVS category | Applies | Standard control in this phase |
|---------------|---------|-------------------------------|
| V2 Authentication | no | no users, no sessions, no credentials |
| V3 Session Management | no | no sessions |
| V4 Access Control | **partially** | the look budget and the lockbox are access control over *data*, not users. `harness.budget.record_look` holds an `fcntl.flock` across check-then-act; `materialize` refuses `held_out` unconditionally. Phase 7 adds no bypass and must not weaken either |
| V5 Input Validation | **yes** | every boundary already validates: `sim_arrays` asserts dtype and `null_count() == 0`; `run_sim_checked` raises on a wrong dtype; `issue_manifest` refuses malformed/reused partition paths; `read_segment_manifest`/`read_errata_manifest` re-verify their own self-hash on every read; `budget._require_filter_safe` refuses any value outside `[A-Za-z0-9_.\-]` before splicing into an MLflow filter string. New code adds the §Q2 alignment assertions and the §Q9-R5 feature allow-list |
| V6 Cryptography | **yes, and never hand-rolled** | `hashlib.sha256` only, via `data.store.compute_manifest_id` for content addressing and partition integrity. No new hash construction; `predictor_id` (D-07-14) is a sha256 over a canonicalised JSON body, reusing the existing canonicaliser |
| V7 Error Handling / Logging | **yes** | fail-closed everywhere: `read_errata_manifest` raises rather than "mask nothing"; `look_count` lets MLflow exceptions propagate unmodified so `0` never means "could not ask"; `record_negative_result` refuses an over-long `reason` because MLflow truncates instead of raising |

### Threat patterns relevant to this stack

| Pattern | STRIDE | Standard mitigation |
|---------|--------|---------------------|
| MLflow `filter_string` injection via a segment name | Tampering | `_require_filter_safe` — already in `budget` and `negative_log`. Any new filtered query must reuse it |
| Silent artifact substitution (partition rewritten in place) | Tampering | `resolve_manifest` verifies sha256 on every read; `check_no_manifest_rewrite` + `check_manifest_append_only` anchor immutability to git history. The new `predictions` manifest inherits all three |
| Pickle deserialisation of a model file | Tampering / RCE | D-07-17: JSON coefficients, never a pickle. Note `joblib 1.6.0` pulls `cloudpickle` into the environment — nothing in this phase may use it |
| Budget bypass (reading fold rows around the counter) | Repudiation | `check_harness_accessor_only` (hook 17) statically, `record_look` at runtime. §Q9-R2 adds the cache-keyed-by-tracking-root rule, which closes a bypass neither control currently sees |
| Look-ahead leakage presented as skill | Tampering (of the result) | purge/embargo in `harness`, the D-07-19 ceiling guard, the §Q9-R3 double-R² gate, and the §Q2 alignment assertion |

---

## Sources

### Primary (HIGH confidence — measured or read in this session)

- **This repository**, read in full: `harness/segments.py`, `harness/accessor.py`,
  `harness/kfold.py`, `harness/purge_embargo.py`, `harness/row_admission.py`,
  `harness/budget.py`, `harness/errata.py`, `harness/negative_log.py`,
  `sim/kernel.py`, `sim/arrays.py`, `sim/outputs.py`, `sim/ticks.py`,
  `data/store.py`, `data/lake_paths.py`, `features/normalize.py`,
  `tracking/mlflow_utils.py`, `tools/check_harness_accessor_only.py`,
  `tools/check_pin_versions.py`, `tools/check_manifest_id_integrity.py`,
  `tools/check_manifest_append_only.py`, `tools/check_no_manifest_rewrite.py`,
  `spec.md` (Decision rule, Simulator, MLflow tag schema), `pyproject.toml`,
  `.pre-commit-config.yaml`, `tests/conftest.py`
- **Committed registry**, read: the 3-day segment manifest, all 14 feature
  manifests + 7 by-date pointers, the `features_norm` manifest, the 249-cell
  errata manifest
- **The real data pool**, measured read-only with polars: 7 v2 partitions,
  60,926,503 rows — per-day row counts, null counts, zero mass, `etime`
  uniqueness/order, `decision_seq` cardinality, frame bytes/row, conditioning,
  in-sample and out-of-sample R²/IC, admission counts per segment for both
  layouts, OOF training row counts, the Gram-vs-dense equivalence, prediction-table
  parquet bytes, cross-process/cross-thread determinism hashes, and the
  perfect-foresight ceiling for both candidate `val` segments (via `run_sim_checked`
  with `NUMBA_CACHE_DIR` exported outside the repo)
- **scikit-learn 1.9.1 source** — `sklearn/linear_model/_ridge.py`
  (`resolve_solver`, `resolve_solver_for_numpy`, `_solve_cholesky`,
  `Ridge.fit`'s sparse/dense branch at 975–1016), `sklearn/linear_model/_base.py`
  (`LinearRegression` → `linalg.lstsq`), `sklearn/linear_model/_coordinate_descent.py`
  (`random_state` used only when `selection == "random"`, 757–760)
- **scipy 1.18.1 source** — `scipy/stats/_stats_py.py::spearmanr`
  (`rankdata` + `np.corrcoef`; `ConstantInputWarning`)
- **threadpoolctl master source** — the five controllers, no Accelerate
- **polars 1.41.2** — `DataFrame.join` docstring on `maintain_order`, read from the
  installed package
- **mlflow-skinny 3.13.0** — the nested-run refusal, measured in this venv
- **PyPI JSON API**, 2026-09-24 — versions, `requires_dist` and wheel lists for
  scikit-learn, scipy, joblib, cloudpickle, narwhals, threadpoolctl
- **Phase records** — `.planning/phases/06-event-driven-simulator/06-07-SUMMARY.md`,
  `06-06-SUMMARY.md`, `evidence/06-06-real-day-oracle.json`, `.planning/STATE.md`,
  `05-CONTEXT.md` / `06-CONTEXT.md` via the code that implements them

### Secondary (MEDIUM confidence)

- `https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html`
  — solver table and `random_state` wording; confirms but does not state the `auto`
  resolution (the source does)
- `https://scikit-learn.org/stable/computing/parallelism.html` — the
  `*_NUM_THREADS` / `threadpoolctl` / oversubscription text. Notably it does **not**
  discuss determinism under thread-count change, and it never mentions Accelerate

### Tertiary (LOW confidence — flagged, not relied on)

- Nothing. Every claim above traces to a measurement, a read source file, or a
  cited docs page. The unexecuted claims are enumerated in the Assumptions Log.

---

## Metadata

**Confidence breakdown:**

- **Manifest geometry, admission counts, OOF counts, ceilings, memory, timings:
  HIGH** — measured against the real pool with the same functions
  `issue_segment_manifest` calls, in the same order.
- **Accessor/simulator contracts, guardrail requirements, MLflow run structure:
  HIGH** — read from source; the nested-run refusal and the polars ordering
  caveat were executed.
- **Target statistics and the gate design: HIGH** — the 43.9% figure, the
  per-segment tie fractions, the constant-mean R² loophole, the shrunk-model IC
  and the NaN-on-constant behaviour are all measured on real data.
- **scikit-learn runtime behaviour: MEDIUM** — sklearn and scipy are not
  installed. Solver selection, randomness and the algebra are read from the 1.9.1
  source; the Gram equivalence is verified against numpy's own dense solve, not
  against an executed sklearn. A1–A3 in the Assumptions Log name the cheap checks
  that promote these to HIGH in Wave 0.
- **Trainer Protocol design: MEDIUM** — a design proposal, not a measurement. It
  is falsifiable: Phase 8 either adds LightGBM and a transformer without editing
  `protocol.py`, or it does not.

**Research date:** 2026-09-24
**Valid until:** ~2026-10-24 for the repo-internal findings (they change only when
the code does). The sklearn/scipy version numbers are good for ~7 days — both lines
release often, and `uv lock` will resolve whatever is current on the day.

**Nothing was written to the lake, the registry, the MLflow store, or the repo
except this file. No `harness.accessor.materialize` call was made and no
validation look was spent — `budget.look_count` is untouched. The capture daemon
was not started. All measurements read parquet directly with polars, read-only.**
