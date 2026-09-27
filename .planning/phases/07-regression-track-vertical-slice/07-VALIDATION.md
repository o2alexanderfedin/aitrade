---
phase: 7
slug: regression-track-vertical-slice
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-09-24
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> The full criterion → observable → test map lives in
> `07-RESEARCH.md` § "Validation Architecture" (line 2052) — twelve rows,
> measured. This file is the contract; that table is the detail. Do not
> duplicate it here; read it.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + hypothesis 6.x (+ pytest-cov ≥ 7.1) — already installed |
| **Config file** | `mvp/pyproject.toml` → `[tool.pytest.ini_options]`: `pythonpath=["."]`, `testpaths=["tests"]`, `addopts=["--import-mode=importlib"]` |
| **Shared fixtures** | `mvp/tests/conftest.py` — pins `NUMBA_CACHE_DIR` before any numba-reaching import |
| **Quick run command** | `uv run --locked --directory mvp pytest tests/models -x -q` |
| **Full suite command** | `uv run --locked --directory mvp pytest tests -x -q` |
| **Hard rule** | **No `mvp/tests/models/__init__.py`.** `mvp/models/` exists; a same-named test package shadows it. Five prior instances, all bugs. |

## Sampling Rate

- **After every task commit:** the quick command (new tests only) — seconds.
- **Every commit, whether the plan wants it or not:** hooks 18 and 19 run the
  **full** suite plus the leakage suite. **This is the load-bearing constraint of
  this phase's test design** (D-07-34): a test that touches the canonical
  tracking root would spend an irreversible validation look on every single
  commit. Every `tests/models/` test therefore uses the fixture lake and a
  `tmp_path` tracking root, without exception.
- **Per wave merge:** all 19 hooks, plus `uv lock --check` and
  `check_pin_versions` — the new dependency's two gates.
- **Pre-push:** `check_no_manifest_rewrite --full` recomputes sha256 for every
  committed manifest, including the new `predictions` one. After that manifest is
  committed, the parquet's bytes **and mtime** are frozen (D-07-25).
- **Phase gate:** full suite green, 19/19 hooks green, CI green, before
  `/gsd-verify-work`.

## Requirement Coverage

| Requirement | Observable that proves it | Automated? |
|---|---|---|
| **FCST-01** — regression track predicting the 10s return | `r2_vs_zero > 0` **and** `r2_vs_mean > 0` **and** non-tied rank IC `> 0` on `val`, with per-segment `tie_fraction` reported; the constant-at-train-mean control present in the same table to show the loophole is not what passed (D-07-32) | unit on fixtures; one manual real-data look |
| **FCST-01** — four distinct estimators, counted grid | one metrics row per (class, hyperparams); `n_configs` logged as a param; every loser recorded in the negative-result log (D-07-20) | yes |
| **FCST-01** — no price column reaches an estimator | a raised `ValueError` on a forbidden feature name, plus the exactly-9-column assertion on the degree-2 design matrix (D-07-23) | yes |
| **FCST-04** — deterministic predictor | fresh-subprocess sha256 == in-process sha256 **on the same host**, with a deliberate sentinel mutation observed to break it. Never against a committed digest — CI is Linux/OpenBLAS, this host is Apple Accelerate (C6) | yes |
| **FCST-04** — genuinely frozen | the coefficient JSON re-evaluated by a numpy dot product reproduces `predict()` bit-for-bit in a process where `"sklearn" not in sys.modules` | yes |
| **FCST-04** — keyed by the D-07-14 triple | `predictor_id` changes when any recipe component changes, is stable otherwise; `manifest_id` differs when coefficients differ at equal `predictor_id` (D-07-22) | yes |
| **SC3** — the slice runs end to end | one MLflow run carrying all mandatory tags plus `predictor_id`, `normalization_manifest_id`, `prediction_table_manifest_id`, `look_run_ids`, and the sim/ceiling metrics — opened **last**, after every look and negative closes (D-07-30) | yes, on the fixture lake |
| **SC3** — row alignment proven, not assumed | `array_equal` on `etime` before and after every join, `etime` strictly ascending, plus a mutation that shifts the table one row and is observed to fire (D-07-31, C1) | yes |
| **SC3** — the ceiling guard bites | a fabricated P&L at the ceiling raises; at ceiling − 1 it does not. Ceiling for the Option A `val` window is **$112.05**, not day 13's $29.46 (C4) | yes |
| **SC3** — the neutral prediction cannot fabricate a trade | `pred = mid` returns `trades == 0`; a full-tick offset DOES trade (anti-vacuity) | yes |

## Manual-Only, and Why

**The honest `val` look.** Exactly one real `materialize("val")`, performed once
by the slice script behind an explicit `--spend-val-look` flag, recorded in the
SUMMARY. This cannot be automated and must not be: repeating it in a test would
spend the budget on every commit, and the budget is 3 (D-07-03).

## Wave 0 Gaps

- [ ] `scikit-learn==1.9.*` added, `uv lock`, `uv sync --frozen`; re-assert `numpy == 2.4.6`
- [ ] `mvp/tests/models/` created **without** `__init__.py`
- [ ] A fixture-lake builder for a `predictions` tier partition, mirroring `tests/fixtures/harness_span.py` — which must also write an `'ok'` DQ report row, because `load_features` requires one unconditionally
- [ ] A fixture-scale `compressed_3seg` segment manifest whose `val` is small enough to materialize on every commit against a `tmp_path` tracking root
- [ ] The eleven test files named in the research table
- [ ] A named assertion that `NUMBA_CACHE_DIR` stays outside the package tree for the **slice script's own** invocation path — the existing test covers pytest only

**Approval:** pending
