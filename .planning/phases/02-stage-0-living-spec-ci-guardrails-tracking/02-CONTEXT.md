# Phase 2: Stage 0 — Living Spec, CI Guardrails & Tracking - Context

**Gathered:** 2026-09-13
**Status:** Ready for planning

<domain>
## Phase Boundary

The living spec, CI enforcement, and experiment-tracking foundation exist so that no untracked or uncatalogued training can ever happen. Concretely: `spec.md` and `mvp.md` move under `mvp/` (containment rule; ARCHITECTURE.md "move inside at Stage 0"); the feature and label catalogues become machine-readable TOML that CI parses and `spec.md` renders; CI (GitHub Actions + identical local pre-commit) fails on any uncatalogued feature/label in training code, any `import pandas`, any `latest` data reference, any lockfile drift, and any `@njit` function reading module globals; the decision-rule dimensional bug is fixed in the spec; the spot-L1 clock exception and trades-backfill side-exactness are written down; the Sharpe annualization convention, MLflow tag schema, and numba no-globals rule are pre-declared before any run exists; MLflow on SQLite records code hash + data hash + seed + env hash for a test run.

In scope: `mvp/spec/{features,labels}.toml` + loader/registry, spec.md regeneration and CI diff check, `.github/workflows/`, `.pre-commit-config.yaml`, ruff banned-api already present + new checks, `mvp/tracking/mlflow_utils.py` with run-manifest hashing, a smoke test run proving the four hashes land as tags, moving the two root docs into `mvp/` with root stubs.

Out of scope: any feature or label *implementation* (Phase 4), the fold harness (Phase 5), backfill (Phase 3), any model code. Catalogue entries in this phase are the seed set already named in spec.md and ARCHITECTURE.md (mid, imb_top, OFI, trade-flow; ret_10s_mid + diagnostic horizons) — declared, not computed.

</domain>

<decisions>
## Implementation Decisions

### Sharpe Annualization (SPEC-04)
- **Headline annualized Sharpe = mean(daily P&L) / std(daily P&L) × √365.** Crypto perps trade 24/7; 365 not 252. Daily P&L is the sum of realized + mark-to-market P&L over each UTC day from the simulator's equity curve.
- **Hourly Sharpe (× √8760) is reported alongside, explicitly labeled "secondary — data-starved diagnostic", and is never the gate.**
- **The MVP gate "Sharpe > 5" is evaluated only with ≥ 30 daily observations** in the held-out window. Fewer → gate result is "insufficient history", not pass/fail.
- Per-trade Sharpe annualized by √(trades/year) is **forbidden** — it inflates HFT Sharpe into meaninglessness. Written into spec.md DON'Ts.
- Rationale: the gate number must mean what practitioners assume "annualized Sharpe" means, or the exit criterion is theatre.

### Catalogue Source of Truth (SPEC-01, SPEC-02)
- **`mvp/spec/features.toml` and `mvp/spec/labels.toml` are the single source of truth.** Human-edited, machine-parsed by CI without executing project code.
- `mvp/spec/catalogue.py` loads them into typed registries; features/labels are reachable in code **only** via the registry by name. This is what makes "uncatalogued feature in training" mechanically detectable.
- `spec.md`'s feature and label tables are **rendered from the TOML between `<!-- catalogue:features:begin/end -->` markers**; a CI check regenerates and fails on diff, so the markdown can never drift from the TOML.
- Each entry carries the columns spec.md already defines: name, definition, information set, lag, normalization, source datasets, notes (features); name, horizon, computation, information set, embargo, notes (labels). Plus a `version` and an `introduced` date. A definition change gets a new name (spec.md rule), enforced by CI refusing a changed `definition` under an existing `name`.
- Seed entries: the examples already in spec.md (`mid`, `imb_top`, `ret_10s_mid`, `ret_1s_mid`) plus the Phase 4 set named in ROADMAP (OFI, trade-flow; `ret_1min_mid`, `ret_10min_mid`). Declared with definitions; implementation is Phase 4.

### CI & Tracking Infrastructure (TRACK-01, TRACK-02)
- **GitHub Actions** (`.github/workflows/ci.yml`) **and local pre-commit** run the identical check set. CPU only. Private repo has 2,000 free minutes/month — lint + unit tests fit easily.
- Checks (all must be red-on-violation, proven by a negative test in this phase): ruff check + format; `import pandas` (TID251, already present); `latest` data reference (a targeted check on path-constructing code, not a prose grep); catalogue completeness; spec.md ↔ TOML diff; `uv lock --check`; exact-pin assertion test for numba/numpy/llvmlite; numba no-globals lint.
- **MLflow tracking root: `/Volumes/ProjectsSSD/aihedgefund/mlflow/`** — SQLite backend (`mlflow.db`) + artifact store, outside the OneDrive sync root, validated at startup by the same guard as `data_root` (reuse `validate_data_root`; do not write a second guard). Inside the repo, `mvp/mlruns/` must not exist and is `.gitignore`d defensively.
- **MLflow tag schema, pre-declared in spec.md:** `code_hash` (git HEAD SHA + `-dirty` suffix when the tree is not clean), `data_hash` (content hash of the dataset manifest consumed — for the smoke run, the hash of `seq_state.json` or an explicit "none"), `seed` (int), `env_hash` (SHA-256 of `mvp/uv.lock`), plus `segment_manifest_id`, `model_class`, `fold_config`, `stage` — the last four may be `"n/a"` for the Phase 2 smoke run but the keys must exist. A run missing any mandatory tag is rejected by the wrapper, not silently logged.
- One thin wrapper module `mvp/tracking/mlflow_utils.py` is the **only** place runs are started; every future entrypoint goes through it (ARCHITECTURE.md's "uniform, un-skippable" logging).

### Spec Corrections (SPEC-03)
- **Decision rule fixed.** `pred_mid = mid * (1 + pred_10s_return)`; `X_price = mid * X_bps / 10_000`; long when `pred_mid > best_ask + X_price`; short when `pred_mid < best_bid - X_price`; flip-only when in position. The buggy `pred_10s_return * mid` form (a price *change* compared to a price *level*) is struck in `mvp.md` and the corrected pseudocode is authoritative in `spec.md`. Phase 6's oracle tests will re-derive P&L from this exact rule.
- **Spot-L1 clock exception recorded:** Spot L1 is deferred post-MVP (Phase 1 decision). The spec states that spot `bookTicker` carries no exchange timestamp and that, if spot is ever captured, the approach (`@depth@100ms` with `E`, SBE streams, or a documented local-clock exception) must be chosen and written here first — no spot row may exist with a null or locally-sourced `etime` without this section being updated.
- **Trades-backfill side-exactness note:** captured `@trade` frames carry `m` (buyer-is-maker) — side is exact; `data.binance.vision` futures `trades` dumps carry `is_buyer_maker` — side is exact; nearest-quote classification (`tradeSide_corrected`) applies **only** to legacy rows where the side field is absent or 0, and the spec says so explicitly so nobody "corrects" exact sides.

### Numba No-Globals Rule (SPEC-04)
- A `@njit` function that reads a module-level global freezes its value at first compile — later edits are silently ignored. Rule: `@njit` functions take every input as a parameter; module globals are limited to compile-time constants declared `Final` and named in UPPER_CASE. Enforced by a small AST check in CI (`mvp/tools/check_numba_globals.py`) that fails on any `Name` load inside an `@njit`-decorated function body that resolves to a module-level non-`Final` binding. Phase 1 has no `@njit` code yet; the check must still run (against zero functions) so it is proven wired before Phase 4 adds the kernel.

### Claude's Discretion
- Exact TOML schema shape, loader implementation, and how the registry exposes entries.
- Whether spec.md regeneration is a script the developer runs (`uv run python -m spec.render`) with CI diffing, or CI-only. Prefer the script + diff.
- pre-commit hook granularity and which checks are `stages: [pre-push]` vs `[commit]`.
- Exact GitHub Actions matrix (single job on `ubuntu-latest`, Python 3.13, `uv sync --frozen`, is sufficient).
- How the "latest" ban is scoped so prose mentions of the word do not trip it.
- Root-level stub content for `mvp.md`/`spec.md` after the move.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `mvp/data/capture/config.py::validate_data_root()` — the cloud-sync / free-space / writability guard. Reuse for the MLflow root; do not duplicate.
- `mvp/pyproject.toml` — ruff config with `flake8-tidy-imports.banned-api` for pandas already present and proven (TID251 fires). Extend, do not replace.
- `mvp/uv.lock` — the pinned env; `uv lock --check` is the drift gate.
- `mvp/tests/conftest.py` + fixture conventions from Phase 1 (hermetic; no host-state dependence — see the watchdog `disk_usage` lesson).
- Phase 1 established `print(..., flush=True)` as the logging convention and `./.venv/bin/python3` as the long-lived-process invocation (never `uv run` for daemons).

### Established Patterns
- Everything under `mvp/`; the only files outside are `.planning/`, `CLAUDE.md`, `README.md`, and the two docs this phase moves inside.
- TDD with RED commit → GREEN commit visible in history.
- Commits end `Co-Authored-By: AI Hive(R) <sales@hupyy.com>`.
- Empirical over documentary: when a claim is load-bearing, test it (Phase 1 disproved research twice by probing).

### Integration Points
- The catalogue registry is what Phase 4's feature engine registers into and what Phase 7+ trainers read names from.
- `mlflow_utils.py` is what every pipeline entrypoint from Phase 5 onward calls; the selection-bias ledger (Phase 5) hangs off the same run manifest.
- The corrected decision rule is what Phase 6's simulator implements and its oracle tests verify.
- The CI leakage suite scaffolding (job + directory) is created here; Phase 4 fills it with per-feature shuffle-future tests.
- A capture daemon (Run F → Run G) is running throughout; nothing in this phase touches it or `data_root`.

</code_context>

<specifics>
## Specific Ideas

- Every CI check must be proven **red** by a deliberate violation in this phase (a throwaway file with `import pandas`, a fake `latest` path, an uncatalogued feature name, a `@njit` reading a global) — a green check that has never been seen red is not a guardrail. Phase 1 proved TID251 this way; extend the practice to every new check.
- The Sharpe convention and the MLflow tag schema go into `spec.md` **before** `mlflow_utils.py` is written, and the smoke run's tags are asserted against the spec's list — the spec is the contract, the code conforms.
- Phase 2 must not start any model training (CLAUDE.md process constraint). The MLflow smoke run logs a dummy metric, not a model.
- `1_000_000_000` (seconds→ns) already appears in `dedup.py` and `rotation.py`; the single-ms→ns-site CI rule must use `1_000_000([^_0-9]|$)`, not the bare substring (noted in Phase 1's 01-03 checkpoint).

</specifics>

<deferred>
## Deferred Ideas

- Per-feature shuffle-future leakage tests — Phase 4 (this phase only scaffolds the CI job).
- Selection-bias budget ledger in MLflow — Phase 5.
- Spot L1 capture approach — post-MVP; the spec section written here is the placeholder that must be filled before any spot row exists.
- GPU CI for the transformer track — Phase 8; blocked on Q3 (GPU spec).
- Migrating the three `ledger_version=1` false-positive gap rows out of the live ledger — Phase 3 DQ report filters on `ledger_version >= 2`; physical cleanup is a Phase 3 concern.

</deferred>
