# Phase 7: Regression Track & Vertical Slice - Context

**Gathered:** 2026-09-24
**Status:** Ready for planning
**Mode:** Smart discuss (autonomous) — 2 acceptance prompts, both accepted as recommended

<domain>
## Phase Boundary

This phase makes the cheapest possible model class walk the whole Stage-1 tract
end to end, and it makes the walk *provable*: curated store → harness accessor →
fitted regressor → a stored prediction table → the simulator, with one MLflow run
manifest tying the pieces together.

It delivers FCST-01 (a regression track: linear → ridge/elastic-net → small
non-linear, predicting the 10s midprice return) and FCST-04 (a frozen-predictor
interface producing precomputed prediction tables keyed by segment manifest).

IN scope: a `mvp/models/` package holding the Trainer protocol, the four
estimators, the frozen-predictor serialisation, the prediction-table dataset and
the end-to-end slice script; one NEW segment manifest over the full 7-day pool;
the `scikit-learn` dependency; the first real validation looks this project has
ever spent.

OUT of scope, explicitly:
- **Trees and transformer** — Phase 8. Nothing here may special-case a model class
  such that adding LightGBM later means rewriting the Trainer protocol.
- **Declaring or locking the real held-out window** — Phase 8's own success
  criterion 4. This phase's manifest carries the zero-width held-out sentinel.
- **Hyperparameter search via Optuna** — Phase 8. This phase's grid is a fixed,
  hand-written, counted list.
- **Stage 2 monetization / the X threshold sweep** — Phase 9. The simulator runs
  here at the zero-defaulted parameters only, to prove the wiring.
- **Rebuilding features, backfilling data, or restarting capture.** The user's
  standing instruction for this session is explicit: no downloading and no
  production of massive data. The 7 built v2 partitions are the whole pool.

</domain>

<decisions>
## Implementation Decisions

### Validation Window & Look Budget (Area 1 — accepted as recommended)

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

### Model Track (Area 2 — accepted as recommended)

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

### Prediction Tables & The Frozen Predictor (Area 3 — accepted as recommended)

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

### Baseline, Metrics & The Ceiling (Area 4 — accepted as recommended)

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

### Amendment after the pattern map (2026-09-24)

`07-PATTERNS.md` surfaced five open tensions and one genuine gap in the
decisions above. Resolved here, before planning:

- **D-07-22 — The frozen predictor is a committed registry body, carrying two
  hashes.** It lives at `mvp/data/lake_registry/predictors/<manifest_id>.json`,
  self-hashed like every other manifest in this project. It carries BOTH:
  `manifest_id` = sha256 of the canonical body INCLUDING the coefficients (so
  `check_manifest_id_integrity` works on it unchanged), and `predictor_id` =
  sha256 of the RECIPE only (estimator class, sorted hyperparameters, seed,
  code hash, normalization manifest id) per D-07-14. Reading one re-derives
  `predictor_id` from the recipe fields and cross-checks it, failing closed —
  the `harness.errata.read_errata_manifest` pattern.
  The point of carrying both: two runs of the SAME recipe that disagree on
  coefficients yield the same `predictor_id` under different `manifest_id`s.
  That makes D-07-12's determinism claim checkable as DATA, not only as a test.
  An MLflow artifact was rejected: D-07-17 chose JSON over pickle precisely so
  the frozen predictor is diffable and reviewable in git, and an artifact in a
  gitignored tracking root is neither.
  `tools/check_manifest_append_only.py` and
  `tools/check_manifest_id_integrity.py` gain `"predictors"` in their
  per-directory loop. **The new directory and the guardrail extension must land
  in ONE commit** — Phase 5's Rule 5 vacuity refusal, learned the hard way when
  `segments/` and `errata/` were added.

- **D-07-23 — A return is not a price. The conversion is its own function.**
  `run_sim_checked`'s `pred` argument is a raw USD price; the model predicts
  `ret_10s_mid`, which `spec/labels.toml` defines as the SIMPLE return
  `(mid_{t+10s} - mid_t) / mid_t`. So the slice needs
  `pred_price = mid * (1.0 + pred_ret)` in float64, as a named function with its
  own round-trip test, in the register of `sim/ticks.py::price_to_ticks`.
  `mid` is read by that function and by NOTHING else. D-07-09's refusal is
  therefore scoped to the estimator's input columns, not to "the module never
  touches mid", and is asserted three ways because no single assertion sees all
  the ways a price can leak in:
  1. a feature list containing `mid`/`bid_price`/`ask_price` RAISES;
  2. the fitted coefficient vector has exactly 3 entries, in the pinned order
     `("imb_top", "ofi", "trade_flow")`;
  3. the `PolynomialFeatures(degree=2, include_bias=False)` design matrix has
     EXACTLY 9 columns (3 linear + 3 squared + 3 cross) — a count assertion is
     the only one of the three that catches a price column entering through an
     interaction term.
  `mid` must never go through `price_to_ticks`: at a one-tick spread the mid is
  a half tick 98.8% of the time, and `price_to_ticks` refuses an exact half-tick
  round-trip by design. `bid_price`/`ask_price` are on-grid and are the only two
  columns that may pass through it.

- **D-07-24 — Cite the ceiling from STATE.md, not from the evidence JSON.**
  `2,192 trades / 294,554 ticks / $29.46` is correct. Plan 06-06's
  `evidence/06-06-real-day-oracle.json` and its SUMMARY report
  `2,212 / 293,844`; those are PRE-FIX numbers, superseded by 06-07's symmetric
  floor/ceil quantisation fix, and the evidence file says so in its own
  `SUPERSEDED_BY` key. The ceiling is also a PER-SEGMENT quantity: the new
  `val` segment's own must be re-measured before it bounds anything.

- **D-07-25 — A committed prediction-table manifest makes that table write-once
  forever.** It can never be deleted or rewritten in place, only superseded by a
  new manifest, and the per-commit `stat()` tripwire in
  `check_no_manifest_rewrite` will size/mtime-check it on every commit from then
  on. This is already the status quo for all 9.3 GB the lake's committed
  manifests name — it is not a new dependency — and on a machine without the SSD
  mounted the check SKIPs honestly. The SUMMARY must state the measured bytes
  and this consequence plainly.

- **D-07-26 — `data/store.py` is a MODIFIED file, not merely an import.**
  `PREDICTIONS_TIER` goes beside `FEATURES_NORM_TIER` and deliberately NOT into
  `BY_DATE_INDEXED_TIERS`. `issue_manifest` reads `p["date"]` BEFORE its tier
  check, so a predictions caller must pass `dates=[]` or it raises `KeyError`.

- **D-07-27 — `typing.Protocol` has no precedent in this repo** (zero
  Protocols, zero ABCs; pluggability is done with function injection plus
  `NamedTuple`/frozen dataclass). Introducing one for the Trainer is still the
  right call, because Phase 8 must add LightGBM and a transformer without
  editing the interface — but `mypy` is NOT one of the 19 hooks, so a Protocol
  is unenforced documentation unless a test asserts conformance. Every estimator
  therefore gets an explicit `isinstance(obj, TrainerProtocol)` test
  (`@runtime_checkable`), which is what makes the protocol load-bearing rather
  than decorative.

- **D-07-28 — Dependency resolution is already verified, not assumed.**
  Measured on an isolated copy of `pyproject.toml`/`uv.lock` in a scratch
  directory (the repo was not touched): `scikit-learn==1.9.*` resolves to 1.9.1
  and adds exactly five packages — `joblib` 1.6.0, `narwhals` 2.26.0,
  `scikit-learn` 1.9.1, `scipy` 1.18.1, `threadpoolctl` 3.7.0. Nothing else in
  the lock changed: `numpy` stays 2.4.6, `numba` 0.65.1, `llvmlite` 0.47.0 — the
  load-bearing triple pin holds — and `pandas` is absent from the lock entirely.
  `tools/check_pin_versions.py` PASSES on that lockfile unmodified, since it
  asserts only those three prefixes plus pandas absence. Adding
  `"scikit-learn": "1.9"` to its `PINNED_PREFIXES` is OPTIONAL and recommended:
  the mandated stack names 1.9.0 in CLAUDE.md, and the guardrail is where that
  mandate becomes mechanical.

### Claude's Discretion

- The internal shape of the Trainer protocol, the module split inside
  `mvp/models/`, plan/wave decomposition, and test naming are at Claude's
  discretion, subject to the decisions above and to Phase 8 being able to add
  LightGBM and a transformer WITHOUT editing the protocol.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `harness/accessor.py::materialize(segment_manifest_id, segment_name, *, registry_root, lake_root, tracking_root, run_tags)` — the ONLY sanctioned door to fold rows. It already enforces the held-out refusal, manifest self-hash, purge/embargo, row admission, errata masking and the look count. New code must never import `features.tier.load_features`; `tools/check_harness_accessor_only.py` is the 19th pre-commit hook and will fail the commit.
- `harness/segments.py::issue_segment_manifest(...)` — derives `admission.counts`, `effective_intervals`, `purged_row_count`, `embargoed_row_count`, `purge_ns`, `embargo_ns` itself from real partitions; requires `lake_root` and `tracking_root`; self-discovers existing manifests and refuses an overlap with an exhausted window.
- `harness/budget.py` — `look_count`, `record_look` (flock-serialised, `budget_allowance` required), `exhausted_segments`, `BudgetExhaustedError`.
- `harness/negative_log.py` — `record_negative_result`, `config_fingerprint`, `warn_if_already_negative`, `query_negative_results`.
- `sim/kernel.py::run_sim_checked` + `sim/outputs.py::SimResult` — the simulator takes the accessor's frame and a prediction array; `trade_log` columns MUST be sliced to `[:fill_count]` before hashing (the tail is uninitialised `np.empty`).
- `sim/ticks.py` — `TICK_SIZE_SCALED` (0.1 USDT), `LOT_STEP_SCALED` (0.001 BTC), `price_to_ticks`, `position_size_ticks` (raises `ZeroLotError` above ~$100k).
- `tracking/mlflow_utils.py` — `start_tracked_run`, `MANDATORY_TAG_KEYS`, `compute_code_hash`, `compute_env_hash`, `log_data_provenance`, provenance tag sharding.
- `data/store.py` — `issue_manifest`, `resolve_manifest` (verifies partition sha256 on every read), `manifests_for_dataset`.
- `features/normalize.py` — `fit_normalization`, `apply_normalization`, `load_normalization`, `expanding_z`.

### Established Patterns
- Manifest-addressed immutability: `manifest_id = sha256(canonicalised JSON body)`; a wrong build is corrected by a NEW manifest, never by rewriting bytes.
- Integer-exact money: the simulator's P&L is int64 ticks throughout; the int64 overflow bound is **$922.34 notional** (`INT64_MAX // QTY_SCALE`), only 9.22x the $100 default, guarded by `STATUS_NOTIONAL_OVERFLOW`.
- Mutation-checked tests: print the changed block, assert the file hash changed BEFORE running the suite, restore, confirm the hash matches again. A mutation that never applied is indistinguishable from an uncovered test.
- `--import-mode=importlib` repo-wide; NEVER add `mvp/tests/<pkg>/__init__.py` where `mvp/<pkg>/` exists (five instances so far, all bugs).
- Guardrail commands are byte-identical between `.pre-commit-config.yaml` `entry:` and `.github/workflows/ci.yml` `run:` — a parity test enforces it.

### Integration Points
- New package `mvp/models/`; new tests under `mvp/tests/models/` (no `__init__.py`).
- `pyproject.toml` gains `scikit-learn`; `uv.lock` regenerates; Phase 2's pin-assertion guardrail and `compute_env_hash` both read the lock.
- `mvp/spec.md` must gain a section for the regression track, the prediction-table contract and the two baseline gates — PRs touching features/labels/models update the spec by project rule.
- `mvp/data/lake_registry/segments/` gains one new committed manifest body.
- Lake gains a `predictions/` dataset root and one new `features_norm` partition.

</code_context>

<specifics>
## Specific Ideas

- The user's session instruction is a hard boundary: **no downloading, no producing massive data.** Concretely: no feature rebuild, no backfill, no capture restart, and prediction tables limited to the one that feeds the simulator (D-07-15). Report measured bytes.
- Capture has been STOPPED since the 2026-09-19 host reboot and the user declined a relaunch. `/Volumes/ProjectsSSD/aihedgefund/capture/` is read-only to agents. The pool cannot grow during this phase.
- Work ONLY from `/Volumes/ProjectsSSD/aihedgefund/repo`. The OneDrive checkout is a dehydrated stale copy that hangs on read.
- Commit trailer is EXACTLY two lines: `Co-Authored-By: AI Hive(R) <sales@hupyy.com>` and `Claude-Session: <url>`. `noreply@anthropic.com` is forbidden by the user's own CLAUDE.md.
- Any script running a `cache=True` numba kernel outside pytest must export `NUMBA_CACHE_DIR` outside the repo.
- Never launch a long-lived process with `uv run` (it holds `~/.cache/uv/.lock`); use `./.venv/bin/python3` from `mvp/`. `uv lock` / `uv sync` are short-lived and fine.
- Never pass `--no-verify`; all 19 hooks must pass.

</specifics>

<deferred>
## Deferred Ideas

- **Optuna-driven hyperparameter search with a counted trial budget** — Phase 8 (FCST-05 names it).
- **Declaring and locking the real held-out window** — Phase 8 success criterion 4. Note for that phase's discuss: "forward in time" has nowhere to go while capture is stopped.
- **Retiring the v1 feature partitions** — needs a mechanism (a v1 manifest stops resolving once its bytes move); Phase 8's call per Phase 6's decision log.
- **Reporting suite: HAC/Newey-West standard errors, Ljung-Box, block bootstrap, equity-curve figures** — Phase 9 (EVAL-05); `statsmodels` and `matplotlib` are not installed and are not added here.
- **Removing the zero-fee / zero-latency simplifications** — already parameters with zero defaults; a call-site change whenever the project decides to.

</deferred>
