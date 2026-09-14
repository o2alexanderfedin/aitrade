# Phase 2: Stage 0 — Living Spec, CI Guardrails & Tracking - Research

**Researched:** 2026-09-13
**Domain:** Machine-readable spec catalogues, CI guardrails (GitHub Actions + pre-commit), MLflow experiment tracking
**Confidence:** HIGH (all load-bearing claims empirically tested in a scratch venv or verified against PyPI/GitHub API; one CRITICAL finding overturns an assumption baked into CONTEXT.md — see Summary)

## Summary

This phase has no novel algorithmic risk — it is glue: TOML catalogues read by `tomllib` (stdlib, zero new dependency), a markdown-render-and-diff CI check, GitHub Actions + pre-commit running the same commands, and an MLflow SQLite tracking wrapper. The mechanical pieces are straightforward and were empirically proven in a scratch directory during this research (tomllib schema round-trip, the numba no-globals AST check prototype, and the MLflow SQLite tag round-trip all work exactly as CONTEXT.md assumed).

**One CRITICAL, empirically-verified finding changes the plan:** the full `mlflow==3.13.0` package **unconditionally imports pandas** — `pip show mlflow` lists `pandas` as a hard `Requires`, and a bare `import mlflow` puts `pandas` in `sys.modules` before any tracking call is made. This directly contradicts CLAUDE.md's "no pandas anywhere in the stack (also transitively)" mandate and STACK.md's stated intent. The fix is also verified: `mlflow-skinny==3.13.0` (the lightweight client/server package) has **no** pandas/numpy/pyarrow/scikit-learn/scipy/matplotlib dependency, and empirically supports the exact call surface this phase needs — `set_tracking_uri`, `start_run(tags=...)`, `log_metric`, `MlflowClient.get_run`, and even the `mlflow ui` / `mlflow server` CLI commands — against a SQLite backend. The plan should install `mlflow-skinny` + `sqlalchemy` + `alembic` (the two packages `mlflow-skinny` needs for the SQL store but doesn't pull automatically) instead of full `mlflow`, and CI's `import pandas` ban should be scoped to project code only (ruff TID251 already only scans project source, so this requires no CI change — just a dependency change and a note in spec.md's DON'Ts explaining why full `mlflow` is banned).

**Primary recommendation:** Use `mlflow-skinny==3.13.0` (not `mlflow`) for TRACK-01; everything else in CONTEXT.md's locked decisions is directly implementable as written — TOML catalogues + `tomllib`, markdown-render-with-CI-diff, GitHub Actions mirroring pre-commit via a shared script, and a small custom AST script for the numba no-globals rule (no third-party AST-checking library needed).

## User Constraints (from CONTEXT.md)

### Locked Decisions

**Phase Boundary:** The living spec, CI enforcement, and experiment-tracking foundation exist so that no untracked or uncatalogued training can ever happen. Concretely: `spec.md` and `mvp.md` move under `mvp/` (containment rule; ARCHITECTURE.md "move inside at Stage 0"); the feature and label catalogues become machine-readable TOML that CI parses and `spec.md` renders; CI (GitHub Actions + identical local pre-commit) fails on any uncatalogued feature/label in training code, any `import pandas`, any `latest` data reference, any lockfile drift, and any `@njit` function reading module globals; the decision-rule dimensional bug is fixed in the spec; the spot-L1 clock exception and trades-backfill side-exactness are written down; the Sharpe annualization convention, MLflow tag schema, and numba no-globals rule are pre-declared before any run exists; MLflow on SQLite records code hash + data hash + seed + env hash for a test run.

In scope: `mvp/spec/{features,labels}.toml` + loader/registry, spec.md regeneration and CI diff check, `.github/workflows/`, `.pre-commit-config.yaml`, ruff banned-api already present + new checks, `mvp/tracking/mlflow_utils.py` with run-manifest hashing, a smoke test run proving the four hashes land as tags, moving the two root docs into `mvp/` with root stubs.

Out of scope: any feature or label *implementation* (Phase 4), the fold harness (Phase 5), backfill (Phase 3), any model code. Catalogue entries in this phase are the seed set already named in spec.md and ARCHITECTURE.md (mid, imb_top, OFI, trade-flow; ret_10s_mid + diagnostic horizons) — declared, not computed.

**Sharpe Annualization (SPEC-04):**
- Headline annualized Sharpe = mean(daily P&L) / std(daily P&L) × √365. Crypto perps trade 24/7; 365 not 252. Daily P&L is the sum of realized + mark-to-market P&L over each UTC day from the simulator's equity curve.
- Hourly Sharpe (× √8760) is reported alongside, explicitly labeled "secondary — data-starved diagnostic", and is never the gate.
- The MVP gate "Sharpe > 5" is evaluated only with ≥ 30 daily observations in the held-out window. Fewer → gate result is "insufficient history", not pass/fail.
- Per-trade Sharpe annualized by √(trades/year) is forbidden — it inflates HFT Sharpe into meaninglessness. Written into spec.md DON'Ts.

**Catalogue Source of Truth (SPEC-01, SPEC-02):**
- `mvp/spec/features.toml` and `mvp/spec/labels.toml` are the single source of truth. Human-edited, machine-parsed by CI without executing project code.
- `mvp/spec/catalogue.py` loads them into typed registries; features/labels are reachable in code only via the registry by name.
- `spec.md`'s feature and label tables are rendered from the TOML between `<!-- catalogue:features:begin/end -->` markers; a CI check regenerates and fails on diff.
- Each entry carries: name, definition, information set, lag, normalization, source datasets, notes (features); name, horizon, computation, information set, embargo, notes (labels). Plus a `version` and an `introduced` date. A definition change gets a new name, enforced by CI refusing a changed `definition` under an existing `name`.
- Seed entries: `mid`, `imb_top`, `ret_10s_mid`, `ret_1s_mid` plus the Phase 4 set (OFI, trade-flow; `ret_1min_mid`, `ret_10min_mid`). Declared with definitions; implementation is Phase 4.

**CI & Tracking Infrastructure (TRACK-01, TRACK-02):**
- GitHub Actions (`.github/workflows/ci.yml`) and local pre-commit run the identical check set. CPU only. Private repo has 2,000 free minutes/month.
- Checks (all must be red-on-violation, proven by a negative test): ruff check + format; `import pandas` (TID251, already present); `latest` data reference (targeted check, not prose grep); catalogue completeness; spec.md ↔ TOML diff; `uv lock --check`; exact-pin assertion test for numba/numpy/llvmlite; numba no-globals lint.
- MLflow tracking root: `/Volumes/ProjectsSSD/aihedgefund/mlflow/` — SQLite backend (`mlflow.db`) + artifact store, outside the OneDrive sync root, validated at startup by `validate_data_root`. Inside the repo, `mvp/mlruns/` must not exist and is `.gitignore`d defensively.
- MLflow tag schema, pre-declared in spec.md: `code_hash` (git HEAD SHA + `-dirty` suffix when tree is not clean), `data_hash` (content hash of the dataset manifest — for the smoke run, hash of `seq_state.json` or explicit "none"), `seed` (int), `env_hash` (SHA-256 of `mvp/uv.lock`), plus `segment_manifest_id`, `model_class`, `fold_config`, `stage` — may be `"n/a"` for the Phase 2 smoke run but keys must exist. A run missing any mandatory tag is rejected by the wrapper, not silently logged.
- One thin wrapper module `mvp/tracking/mlflow_utils.py` is the only place runs are started.

**Spec Corrections (SPEC-03):**
- Decision rule fixed: `pred_mid = mid * (1 + pred_10s_return)`; `X_price = mid * X_bps / 10_000`; long when `pred_mid > best_ask + X_price`; short when `pred_mid < best_bid - X_price`; flip-only when in position. The buggy `pred_10s_return * mid` form is struck in `mvp.md`.
- Spot-L1 clock exception recorded (spot deferred post-MVP; any future spot capture requires resolving the etime approach before any spot row exists).
- Trades-backfill side-exactness note: captured `@trade` frames and `data.binance.vision` futures `trades` dumps both carry exact side; nearest-quote classification applies only to legacy rows with absent/0 side.

**Numba No-Globals Rule (SPEC-04):**
- `@njit` functions take every input as a parameter; module globals limited to `Final`/UPPER_CASE compile-time constants. Enforced by `mvp/tools/check_numba_globals.py`, an AST check that fails on any `Name` load inside an `@njit`-decorated function body resolving to a module-level non-`Final` binding. Must run (against zero functions) in this phase.

### Claude's Discretion
- Exact TOML schema shape, loader implementation, and how the registry exposes entries.
- Whether spec.md regeneration is a script the developer runs (`uv run python -m spec.render`) with CI diffing, or CI-only. Prefer the script + diff.
- pre-commit hook granularity and which checks are `stages: [pre-push]` vs `[commit]`.
- Exact GitHub Actions matrix (single job on `ubuntu-latest`, Python 3.13, `uv sync --frozen`, is sufficient).
- How the "latest" ban is scoped so prose mentions of the word do not trip it.
- Root-level stub content for `mvp.md`/`spec.md` after the move.

### Deferred Ideas (OUT OF SCOPE)
- Per-feature shuffle-future leakage tests — Phase 4 (this phase only scaffolds the CI job).
- Selection-bias budget ledger in MLflow — Phase 5.
- Spot L1 capture approach — post-MVP; the spec section written here is the placeholder that must be filled before any spot row exists.
- GPU CI for the transformer track — Phase 8; blocked on Q3 (GPU spec).
- Migrating the three `ledger_version=1` false-positive gap rows out of the live ledger — Phase 3 DQ report filters on `ledger_version >= 2`; physical cleanup is a Phase 3 concern.

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SPEC-01 | spec.md exists in seed form with feature/label catalogues in machine-readable format CI can parse | TOML schema + `tomllib` verified empirically (see Code Examples); rendering pattern in Architecture Patterns |
| SPEC-02 | CI validates every feature/label used in training has a catalogue entry, rejects `import pandas` and `latest` references | ruff TID251 already proven (Phase 1); catalogue-completeness check design in Architecture Patterns; "latest" ban scoping in Don't Hand-Roll / Common Pitfalls |
| SPEC-03 | Decision-rule bug fixed; spot-L1 clock exception; trades-backfill side-exactness documented in spec.md | Corrected rule text is a CONTEXT.md locked decision; no additional research needed beyond copying the corrected pseudocode into spec.md |
| SPEC-04 | Sharpe annualization, MLflow tag schema, numba no-globals rule pre-declared before any run | Sharpe math validated in Code Examples; MLflow tag schema validated empirically (SQLite round-trip test); numba AST-check prototype validated empirically |
| TRACK-01 | MLflow SQLite backend; run manifest records code/data/seed/env hash | **CRITICAL finding**: use `mlflow-skinny`, not `mlflow` (pandas dependency) — see Summary and Pitfall 1; SQLite URI form verified empirically |
| TRACK-02 | Environment pinned; CI enforces pins | `uv lock --check` / `uv sync --locked` semantics verified via WebSearch + astral docs; exact-pin assertion test pattern in Code Examples |

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Catalogue source of truth (TOML) | Data/Config (`mvp/spec/`) | — | Human-edited, machine-parsed; not code, not docs — a config tier of its own per ARCHITECTURE.md's structure |
| Catalogue registry (`catalogue.py`) | Application logic (`mvp/spec/`) | — | Loads TOML into typed objects; consumed by Phase 4's feature engine later |
| spec.md rendering | Build/tooling script | CI (diff check) | Rendering is a local dev-loop script; CI only verifies no drift, never renders as source of truth |
| CI guardrails (ruff, catalogue, latest-ban, lockfile, numba-globals) | CI / pre-commit (identical command set) | — | Both environments must run the *same* invocation to avoid the "works locally, fails in CI" class of bug |
| MLflow tracking | Cross-cutting infra (`mvp/tracking/`) | External SQLite store (`/Volumes/ProjectsSSD/...`) | Wrapper module owns the contract; actual storage lives outside the repo/OneDrive sync root, same tier discipline as `data_root` |
| Numba no-globals check | CI static-analysis tool (`mvp/tools/`) | — | Pure AST analysis, no numba runtime involved — a lint tool, not a feature-tier component |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| mlflow-skinny | 3.13.0 (verify `uv add` resolves to same 3.13.x line as rest of stack; 3.16.0 is current latest as of this research — CONTEXT.md pins 3.13.0 to match STACK.md, which is fine, `mlflow-skinny` and `mlflow` are versioned in lockstep) | Experiment tracking client, SQLite backend, tag-based run manifest | **[VERIFIED]** Empirically tested in this session: full `mlflow==3.13.0` imports pandas unconditionally (`pip show mlflow` → `Requires: ... pandas ...`; `import mlflow` alone puts `pandas` in `sys.modules`). `mlflow-skinny==3.13.0` has zero pandas/numpy/pyarrow/sklearn/scipy/matplotlib deps and supports the full tracking API this phase needs, verified via a live SQLite round-trip (`start_run(tags=...)`, `log_metric`, `MlflowClient.get_run`), plus `mlflow ui` / `mlflow server` CLI commands |
| sqlalchemy | latest compatible with mlflow 3.13 (verify via `uv add`) | SQL backend engine for MLflow's SQLite store | **[VERIFIED]** `mlflow-skinny` does not vendor SQLAlchemy/alembic; installing them alongside is required and was sufficient in the empirical test — `mlflow.db` initialized correctly ("Creating initial MLflow database tables") |
| alembic | latest compatible | MLflow's DB schema migration tool | **[VERIFIED]** same test; alembic is what actually runs the "Updating database tables" step MLflow logs at first connect |
| tomllib | stdlib (Python 3.11+, project is on 3.13) | Parse `features.toml` / `labels.toml` | **[VERIFIED]** No new dependency. Round-tripped a features.toml fixture with the exact column set CONTEXT.md specifies (definition, information_set, lag, normalization, source_datasets, notes, version, introduced) — parses cleanly, schema-validation-by-set-difference works as shown in Code Examples |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| ruff | 0.15.x (project pin) or 0.16.7 (current PyPI latest, verified via `pip index versions`) | Lint/format, `TID251` banned-import rule (already proven for pandas) | Extend existing `pyproject.toml` config; do not replace. Pin the *same* version in `.pre-commit-config.yaml`'s `astral-sh/ruff-pre-commit` hook `rev` field as in `uv.lock`, or CI and local pre-commit silently drift (a documented, common bug — see Common Pitfalls) |
| astral-sh/ruff-pre-commit | `rev: v0.16.7` **[VERIFIED via GitHub API]** — confirm against whatever ruff version `mvp/pyproject.toml` ends up pinned to | pre-commit hook wrapping ruff | Standard; do not hand-roll a `repo: local` ruff hook — the official hook manages the ruff binary install itself |
| astral-sh/setup-uv | `v10.1.0` **[VERIFIED via GitHub API, `repos/astral-sh/setup-uv/releases/latest`]** | GitHub Actions step to install a pinned uv version | Canonical action for uv-based CI; supports `.python-version` pickup and built-in caching |
| pre-commit | 4.6.2 **[VERIFIED via `pip index versions`]** (CONTEXT.md/STACK.md say "4.x" — locally installed system pre-commit is 4.0.1; either is within the 4.x line) | Git hook runner | Add `repo: local` hooks for project-specific scripts (catalogue check, latest-ban check, numba-globals check) alongside the official ruff hook |
| tomli | not needed | — | tomllib is stdlib on 3.13; do not add `tomli` as a dependency (it exists only as a tomllib backport for <3.11) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `mlflow-skinny` + sqlalchemy + alembic | Full `mlflow` package, accept the pandas dependency as "installed but never imported by our code" | Rejected: CLAUDE.md's ban is explicit about *transitive* pulls ("Beware transitive pulls"); an unused-but-installed pandas in the lockfile is exactly the landmine the project's own STACK.md warns about (skfolio, cryptofeed, binance-public-data are called out by name for this). `mlflow-skinny` costs nothing functionally for this phase's needs |
| Custom markdown-diff render script | A templating library (Jinja2) | Not needed — CONTEXT.md's marker-based (`<!-- catalogue:features:begin/end -->`) approach is simple enough for an f-string/table-builder function; adding Jinja2 is unjustified weight for two tables |
| Custom AST script for numba-globals | `flake8-numba`, third-party numba lint plugins | **[VERIFIED absent]**: no such plugin exists on PyPI as of this research; a WebSearch for numba-specific AST lint tooling surfaced no purpose-built package. A ~60-80 line stdlib `ast` script (prototyped and verified in this session) is the standard approach here — this is exactly the class of custom-but-small tool CONTEXT.md already commits to |
| `repo: local` hooks calling project scripts directly | Duplicating check logic separately for GitHub Actions and pre-commit | Rejected per CONTEXT.md's "identical check set" decision — write each check as a `uv run python -m mvp.tools.X` invocation and call the *same* command from both `.pre-commit-config.yaml` (`repo: local`) and `.github/workflows/ci.yml` |

**Installation:**
```bash
cd mvp
uv add "mlflow-skinny==3.13.*" "sqlalchemy" "alembic"
uv add --dev "pre-commit==4.*"
```

**Version verification performed this session:**
- `mlflow-skinny` PyPI latest: 3.16.0 (2026-09-13, via `pip index versions`); 3.13.0 available and matches STACK.md's pin
- `ruff` PyPI latest: 0.16.7; project's existing pin is `ruff==0.15.*` in `mvp/pyproject.toml` — planner should decide whether to bump to 0.16.x or hold at 0.15.x (STACK.md said "0.15.x" in June; ruff ships frequently, holding the existing pin is lower-risk for this phase since it's infra-only, not a lint-behavior-dependent phase)
- `pre-commit` PyPI latest: 4.6.2; system pre-commit is 4.0.1 — either satisfies CONTEXT.md's "4.x"
- `astral-sh/ruff-pre-commit` latest tag: `v0.16.7` (GitHub API `releases/latest`)
- `astral-sh/setup-uv` latest tag: `v10.1.0` (GitHub API `releases/latest`)
- `uv` PyPI latest: 0.12.13; locally installed `uv` is 0.11.6 (Homebrew) — within the "0.11.x" line CONTEXT.md/STACK.md reference; no action needed for this phase (uv is a dev-machine tool, not a project dependency)

## Architecture Patterns

### System Architecture Diagram

```
                    ┌─────────────────────────────┐
                    │  mvp/spec/features.toml     │
                    │  mvp/spec/labels.toml       │  ← human-edited source of truth
                    └──────────────┬──────────────┘
                                   │ tomllib.load()
                                   ▼
                    ┌─────────────────────────────┐
                    │  mvp/spec/catalogue.py       │  ← typed registry
                    │  (FeatureEntry, LabelEntry)  │
                    └──────┬──────────────┬────────┘
                           │              │
              consumed by  │              │  read by
              Phase 4 code │              │  render script
                           │              ▼
                           │   ┌─────────────────────────┐
                           │   │ mvp/spec/render.py       │  → writes markdown tables
                           │   │ (dev-run script)         │     between markers in
                           │   └──────────┬───────────────┘     mvp/spec.md
                           │              │
                           │              ▼
                           │   ┌─────────────────────────┐
                           │   │ mvp/spec.md              │
                           │   │ <!-- catalogue:features  │
                           │   │      :begin/end -->      │
                           │   └──────────┬───────────────┘
                           │              │ CI: re-render, diff, fail on mismatch
                           ▼              ▼
              ┌─────────────────────────────────────────┐
              │        CI CHECK SET (identical)          │
              │  ┌─────────────┐  ┌────────────────────┐ │
              │  │ pre-commit  │  │ GitHub Actions      │ │
              │  │ (local, git │  │ (.github/workflows/ │ │
              │  │  hook)      │  │  ci.yml)             │ │
              │  └──────┬──────┘  └──────────┬───────────┘ │
              │         └────────┬───────────┘             │
              │                  ▼                          │
              │   uv run python -m mvp.tools.<check>        │
              │   - ruff check / format (TID251 pandas ban) │
              │   - catalogue_completeness (feature/label   │
              │     names used in code ⊆ registry)          │
              │   - spec_diff (render.py output == spec.md) │
              │   - latest_ban (targeted AST/regex scan)    │
              │   - lockfile_check (uv lock --check)         │
              │   - pin_assertion (numba/numpy/llvmlite)     │
              │   - numba_globals (AST scan of @njit bodies) │
              └──────────────────────────────────────────────┘

                    ┌─────────────────────────────┐
                    │  mvp/tracking/mlflow_utils.py│  ← ONLY entrypoint for runs
                    │  - computes code_hash (git)  │
                    │  - computes env_hash (uv.lock│
                    │    sha256)                    │
                    │  - requires all 8 mandatory   │
                    │    tags before start_run()    │
                    └──────────────┬────────────────┘
                                   │ mlflow.set_tracking_uri(
                                   │   "sqlite:////Volumes/ProjectsSSD/
                                   │    aihedgefund/mlflow/mlflow.db")
                                   ▼
                    ┌─────────────────────────────┐
                    │ /Volumes/ProjectsSSD/        │  ← validated by
                    │   aihedgefund/mlflow/        │     validate_data_root()
                    │   ├── mlflow.db  (SQLite)    │     reused, not duplicated
                    │   └── artifacts/              │
                    └─────────────────────────────┘
```

### Recommended Project Structure
```
mvp/
├── spec.md                        # moved from root; markers for rendered tables
├── mvp.md                         # moved from root
├── spec/
│   ├── __init__.py
│   ├── features.toml              # source of truth
│   ├── labels.toml                # source of truth
│   ├── catalogue.py                # tomllib loader + typed registry (FeatureEntry/LabelEntry)
│   └── render.py                   # renders TOML -> markdown tables, writes between markers
├── tools/                          # CI/pre-commit check scripts (importable, testable)
│   ├── __init__.py
│   ├── check_catalogue_completeness.py
│   ├── check_spec_diff.py
│   ├── check_latest_ban.py
│   ├── check_pin_versions.py
│   └── check_numba_globals.py
├── tracking/
│   ├── __init__.py
│   └── mlflow_utils.py             # the only place mlflow.start_run() is called
└── tests/
    ├── test_catalogue.py           # schema validation, definition-change-needs-new-name rule
    ├── test_spec_render.py         # render output matches fixture; diff-check red/green
    ├── test_latest_ban.py          # red-on-violation fixture
    ├── test_numba_globals.py       # red-on-violation fixture (from prototype in this research)
    └── test_mlflow_utils.py        # tag round-trip against a temp SQLite file

.github/workflows/
└── ci.yml                          # single ubuntu-latest job, Python 3.13, uv sync --locked, runs same tools.* scripts

.pre-commit-config.yaml             # ruff (official hook) + repo:local hooks calling the same tools.* scripts

mvp.md                              # root stub: "moved to mvp/mvp.md" + link
spec.md                             # root stub: "moved to mvp/spec.md" + link
```

### Pattern 1: TOML catalogue with tomllib schema validation (no pydantic needed)

**What:** Read `features.toml`/`labels.toml` with stdlib `tomllib.load()`, then validate the required key set per entry with a plain set-difference check (no schema library needed at this scale — 8 fixed columns per feature, ~6 per label).

**When to use:** Always, here — CONTEXT.md wants "machine-parsed by CI without executing project code," and `tomllib` on a static file satisfies that trivially, whereas a pydantic model would add a real dependency for a validation need that's 10 lines of set arithmetic.

**Example (verified working in this session — see Code Examples for full transcript):**
```python
# Source: empirically verified in this research session (Python 3.12/3.13 tomllib)
import tomllib

REQUIRED_FEATURE_KEYS = {
    "definition", "information_set", "lag", "normalization",
    "source_datasets", "notes", "version", "introduced",
}

def load_features(path: str) -> dict[str, dict]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    for name, entry in data.items():
        missing = REQUIRED_FEATURE_KEYS - entry.keys()
        if missing:
            raise ValueError(f"feature {name!r} missing keys: {missing}")
    return data
```

### Pattern 2: Marker-delimited markdown regeneration + CI diff

**What:** `render.py` reads the TOML, builds a markdown table, and replaces only the text between `<!-- catalogue:features:begin -->` and `<!-- catalogue:features:end -->` in `spec.md`, leaving hand-written prose untouched. CI runs the same render into a temp buffer and diffs it against the committed `spec.md`; any difference fails the build.

**When to use:** Always, per CONTEXT.md's locked decision. Keep column order and cell escaping (`|` → `\|`, newlines stripped or `<br>`) deterministic so re-runs produce byte-identical output — this is what makes the CI diff meaningful rather than flaky.

**Trade-offs:** A naive `str.replace` on markers is fragile if the marker text itself appears in a table cell; anchor on exact-line matches (`spec.md` line equals the marker string) rather than substring search.

### Pattern 3: One check script, two callers (pre-commit + GitHub Actions)

**What:** Every guardrail is a `mvp/tools/check_X.py` module with a `main()` and a non-zero exit on violation, unit-tested directly (fast, no subprocess), and *also* invoked identically from `.pre-commit-config.yaml` (`repo: local`, `entry: uv run python -m mvp.tools.check_X`) and `.github/workflows/ci.yml` (`- run: uv run python -m mvp.tools.check_X`).

**When to use:** Always — this is the mechanical guarantee behind CONTEXT.md's "GitHub Actions and local pre-commit run the identical check set." Avoid writing the logic once in a shell script and once in a GitHub Actions YAML step; one Python entrypoint, two callers.

### Pattern 4: The numba no-globals AST check

**What:** Walk the module AST once to collect module-level `Assign`/`AnnAssign` target names (the candidate "globals"). Walk again to find `FunctionDef`/`AsyncFunctionDef` nodes decorated with something whose `ast.dump()` contains `njit` or (`jit` and `nopython`). Inside each such function's body, find `ast.Name` nodes with `Load` context whose `id` is in the module-level name set, is not a local parameter, and is not `UPPER_CASE` (the `Final`/constant escape hatch).

**When to use:** Exactly as CONTEXT.md specifies. **[VERIFIED]** — prototyped and run in this session; correctly flagged a lowercase mutable module global referenced inside an `@njit` function while allowing an uppercase constant (`MAX_POS`) referenced the same way. See Code Examples for the full working prototype and its output.

**Known edge cases to handle in the real implementation (not yet covered by the prototype, flag for the plan):**
- **Closures / nested functions:** a `@njit` function containing a nested `def` — names free in the nested function that resolve to the *outer* njit function's parameters are fine; only module-level resolution should trigger a violation. Walking `ast.walk(node)` over the whole `FunctionDef` subtree (as the prototype does) will also descend into nested defs; nested-def parameters need to be added to the local-names exclusion set per nesting level, or the check will misclassify a nested function's own parameter as a global.
- **`global` statements:** an explicit `global x` inside the njit body is an unambiguous violation and should be flagged with a clearer message than the generic Name-load case.
- **Imports used as names:** a module-level `import numba` (or `from mvp.constants import MAX_POS`) creates a module-level binding via `ast.Import`/`ast.ImportFrom`, not `Assign`. The prototype's `module_level_names` collection (only `Assign`/`AnnAssign`) correctly does *not* flag `numba.njit` usage — but if the project later does `from mvp.config import SOME_TABLE` and reads `SOME_TABLE` inside a kernel, the current prototype would miss it (it's not an `Assign` target). Decide explicitly whether imported names should also be subject to the UPPER_CASE rule (recommended: yes — extend collection to `ImportFrom` aliases).

### Anti-Patterns to Avoid
- **Full `mlflow` package for a "just tracking" need:** pulls pandas/pyarrow/scikit-learn/scipy/matplotlib transitively — see Summary/Pitfall 1. Use `mlflow-skinny`.
- **Rendering spec.md tables at CI-time only (no local script):** CONTEXT.md prefers the script-first approach; CI-only rendering means a developer's local `spec.md` is silently stale until they push, defeating "living spec."
- **Grepping the whole repo for the literal string `latest`** to enforce the "no latest data reference" rule: this trips on prose ("the latest research shows...", changelog entries, this very RESEARCH.md). Scope the check to string literals inside path-construction call sites in `mvp/data/` and `mvp/pipelines/` (see Common Pitfalls).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|--------------|-----|
| Reading/validating TOML | A custom TOML parser | `tomllib` (stdlib) | Zero-dependency, spec-compliant, already on Python 3.13 |
| ruff↔pre-commit version sync | Manual copy-paste of version strings | `astral-sh/ruff-pre-commit`'s `rev:` pinned to the exact same version as `mvp/pyproject.toml`'s ruff pin (a pre-commit CI job that asserts the two match is cheap insurance — see Common Pitfalls) | The single most common pre-commit drift bug in ruff projects (verified via WebSearch: multiple GitHub issues show exactly this failure mode) |
| uv install in GitHub Actions | Manual curl/install script | `astral-sh/setup-uv@v10.1.0` | Canonical, maintained, handles caching and `.python-version` pickup |
| MLflow SQL backend schema management | Hand-rolled SQL migrations | MLflow's own `alembic`-based migration (`mlflow db upgrade`, or automatic on first `mlflow-skinny` connect — verified in this session's test: "Creating initial MLflow database tables" ran automatically) | MLflow owns its schema; do not touch `mlflow.db` structure directly |
| Numba static analysis | A general-purpose Python AST-linting framework (e.g., wrapping `flake8` plugin machinery) for one narrow rule | A standalone ~80-line `ast`-module script, unit-tested directly | No existing PyPI package does this numba-specific check (verified: no hits for a numba-globals lint plugin); a `flake8` plugin adds packaging overhead for one rule used by one project |

**Key insight:** Every guardrail in this phase is either (a) already solved by a stdlib module (`tomllib`, `ast`) or (b) a well-maintained official GitHub Action/pre-commit hook (`setup-uv`, `ruff-pre-commit`). The only genuinely custom code is the catalogue registry, the markdown renderer, and the numba-globals AST walk — all three are small, narrow, and match CONTEXT.md's own scoping.

## Common Pitfalls

### Pitfall 1: Full `mlflow` package silently installs pandas [VERIFIED — CRITICAL]
**What goes wrong:** `uv add "mlflow==3.13.*"` (as literally written in STACK.md's installation block) pulls pandas, pyarrow, scikit-learn, scipy, and matplotlib into the lockfile as hard dependencies of `mlflow` itself — none of which are "pandas anywhere in the stack" per CLAUDE.md. Ruff's `TID251` banned-import rule does not catch this because it only scans project source files, not the dependency's internals — the ban would appear to pass while pandas sits in the venv, importable and imported the instant `mlflow` loads.
**Why it happens:** The full `mlflow` package bundles model-flavor support (e.g., `mlflow.sklearn`, `mlflow.pyfunc` dataframe schemas) that pull in pandas/pyarrow at the top-level `__init__`, not lazily.
**How to avoid:** Depend on `mlflow-skinny` instead of `mlflow`. Add `sqlalchemy` and `alembic` explicitly (verified necessary for the SQLite backend). If `mlflow ui`/`mlflow server` need to run in an environment where the plan wants nicer artifact previews, note that `mlflow-skinny` still exposes both commands (verified) — the tradeoff is only in artifact-viewer richness, not core tracking.
**Warning signs:** `uv.lock` after `uv add mlflow` contains a `pandas` entry; a fresh `pip show pandas` in the project venv succeeds.
**Verification performed:** `pip show mlflow` → `Requires: ... pandas ...`; live Python session confirmed `'pandas' in sys.modules` becomes `True` immediately after `import mlflow`, before any tracking call; the identical tracking/tag workflow was reproduced with `mlflow-skinny` and confirmed `'pandas' in sys.modules` stays `False` throughout.

### Pitfall 2: ruff version drift between `pyproject.toml`, `uv.lock`, and `.pre-commit-config.yaml`
**What goes wrong:** The `astral-sh/ruff-pre-commit` hook's `rev:` field is an independent pin from the project's own `ruff` dependency; if they diverge, pre-commit (local) and CI (running `uv run ruff` from the lockfile) can pass/fail differently on the same diff — precisely the "identical check set" guarantee CONTEXT.md requires.
**Why it happens:** Two separate places declare a ruff version; nothing enforces they match unless someone writes that check.
**How to avoid:** Either (a) don't use the official `ruff-pre-commit` hook at all — run ruff via a `repo: local` hook that calls `uv run ruff check`/`uv run ruff format --check`, guaranteeing the lockfile's ruff is what runs in both places, or (b) if using the official hook for speed, add a cheap CI/pre-commit assertion that `rev:` in `.pre-commit-config.yaml` matches the `ruff==X.Y.*` pin in `pyproject.toml`. Option (a) is simpler and removes an entire class of drift; recommend it for this project given the existing "prove every CI check red once" discipline.
**Warning signs:** ruff fails locally via pre-commit but passes in CI, or vice versa, on an unchanged ruleset.

### Pitfall 3: The "latest" ban trips on prose or is too narrow to catch real violations
**What goes wrong:** A repo-wide grep for the string `latest` fails two ways at once: false positives on prose (commit messages, this RESEARCH.md, docstrings explaining why `latest` is banned) and false negatives on the actual risk (a variable holding a computed "most recent" path that never contains the literal word `latest`).
**Why it happens:** "Ban a string" is not the same shape of problem as "ban a data-versioning anti-pattern."
**How to avoid:** Per CONTEXT.md's discretion note, scope the check narrowly: an AST/regex scan restricted to string literals that are *arguments to path-construction calls* (`pathlib.Path(...)`, `open(...)`, `str.format`/f-strings building a path) inside `mvp/data/` and `mvp/pipelines/` only — not a repo-wide grep. Flag literal `"latest"` segments in those specific call sites. This mirrors the numba-globals check's own scoping discipline (narrow AST target, not broad text search).
**Warning signs:** CI check fires on a comment or this document; or a real `latest`-symlink-based data loader ships without being caught because it never spells the word.

### Pitfall 4: MLflow tag values and mandatory-tag enforcement
**What goes wrong:** If the wrapper logs tags via `mlflow.set_tags()` calls scattered after `start_run()`, a crash between `start_run()` and the last `set_tag()` call leaves a run in the store with an incomplete tag set — exactly what CONTEXT.md says the wrapper must prevent ("A run missing any mandatory tag is rejected by the wrapper, not silently logged.")
**Why it happens:** `mlflow.start_run(tags={...})` and `mlflow.set_tags({...})` look interchangeable but have different failure windows — passing tags directly to `start_run` sets them atomically as part of run creation.
**How to avoid:** **[VERIFIED]** the wrapper should validate the full 8-key mandatory tag dict *before* calling `start_run`, then pass it as `mlflow.start_run(tags=mandatory_tags)` in one call (confirmed working in the empirical test — all 8 tags, including the 64-character hex-string `code_hash`/`env_hash` values, round-tripped correctly through `MlflowClient.get_run().data.tags` with no truncation). Do not use `set_tags` as a follow-up call for mandatory fields.
**Warning signs:** A run exists in the MLflow store with some but not all of `code_hash/data_hash/seed/env_hash/segment_manifest_id/model_class/fold_config/stage`.

### Pitfall 5: `uv lock --check` vs `uv sync --frozen` vs `uv sync --locked` confusion
**What goes wrong:** These three do different things and picking the wrong one either silently updates the lockfile (defeating the drift gate) or fails for the wrong reason.
**Why it happens:** `--frozen` means "use the lockfile as-is, don't even check if it matches pyproject.toml"; `--locked` means "fail if the lockfile doesn't match pyproject.toml, otherwise install exactly what's locked." [CITED: astral-sh/uv docs + GitHub issue discussion, cross-verified via WebSearch]
**How to avoid:** In CI, use `uv sync --locked` (astral's own documented recommendation for CI), not `--frozen` — `--locked` is what actually enforces "the lockfile matches pyproject.toml," which is the drift gate CONTEXT.md wants. Reserve `uv lock --check` (a lighter-weight lock-only check with no full sync) for a fast pre-commit hook if syncing every commit is too slow locally.
**Warning signs:** A `pyproject.toml` dependency edit lands without a `uv.lock` update and CI still passes — means `--frozen` was used where `--locked` was needed.

### Pitfall 6: `git mv` "preserves history" is a display nuance, not a guarantee
**What goes wrong:** After `git mv spec.md mvp/spec.md`, a plain `git log mvp/spec.md` only shows history from the move commit forward; the pre-move history is not "lost" but is invisible without `--follow`.
**Why it happens:** Git doesn't store renames explicitly — it detects them heuristically at `log`/`diff` time based on content similarity, and `--follow` is required to walk across that boundary for a single-file history query. [CITED: general git rename-detection behavior, cross-verified via WebSearch]
**How to avoid:** Just use `git mv spec.md mvp/spec.md` and `git mv mvp.md mvp/mvp.md` — this is sufficient (git's similarity-based rename detection will find it in `git log --follow mvp/spec.md` or in any `git blame`/`git log -p` that crosses the boundary). No special tooling needed; do not reach for `git filter-repo` for a simple two-file move — that's for full-history rewrites, disproportionate here.
**Warning signs:** None expected — flagging only because CONTEXT.md explicitly asked about history preservation; the standard `git mv` behavior is adequate for this phase's two files.

## Code Examples

### TOML catalogue load + schema validation (verified working, Python 3.13 tomllib)
```python
# Source: verified in this research session's scratch venv
import tomllib

REQUIRED_FEATURE_KEYS = {
    "definition", "information_set", "lag", "normalization",
    "source_datasets", "notes", "version", "introduced",
}

def load_features(path: str) -> dict[str, dict]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    for name, entry in data.items():
        missing = REQUIRED_FEATURE_KEYS - entry.keys()
        if missing:
            raise ValueError(f"feature {name!r} missing keys: {missing}")
    return data

# Verified fixture (features.toml):
#   [mid]
#   definition = "(best_bid + best_ask) / 2"
#   information_set = "t (most recent BBO at or before t)"
#   lag = 0
#   normalization = "none"
#   source_datasets = ["swap L1 BBO"]
#   notes = "Treat sub-tick stickiness flag separately"
#   version = 1
#   introduced = "2026-09-13"
# -> loads cleanly, zero missing keys, no dependency beyond stdlib
```

### Numba no-globals AST check (prototype verified working)
```python
# Source: verified in this research session — correctly flags `some_mutable_global`
# (lowercase, module-level, referenced inside @njit body) while allowing
# `MAX_POS` (uppercase constant) referenced the same way.
import ast

def is_njit_decorated(fn: ast.FunctionDef) -> bool:
    for dec in fn.decorator_list:
        dumped = ast.dump(dec)
        if "njit" in dumped or ("jit" in dumped and "nopython" in dumped):
            return True
    return False

def find_module_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            names.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
    return names

def check_no_globals(source: str) -> list[tuple[str, str, int]]:
    """Return (function_name, offending_name, lineno) for every violation."""
    tree = ast.parse(source)
    module_names = find_module_level_names(tree)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and is_njit_decorated(node):
            local_names = {a.arg for a in node.args.args}
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Name)
                    and isinstance(inner.ctx, ast.Load)
                    and inner.id in module_names
                    and inner.id not in local_names
                    and not inner.id.isupper()
                ):
                    violations.append((node.name, inner.id, inner.lineno))
    return violations

# Verified output against a fixture with `MAX_POS: int = 100` (allowed, uppercase)
# and `some_mutable_global = 5` (flagged): violations == [('bad_kernel', 'some_mutable_global', 8)]
```
**Remaining work for the real implementation (see Architecture Patterns > Pattern 4 for details):** handle nested-function parameter shadowing, explicit `global` statements, and imported names (`ImportFrom` targets) — the prototype above covers the core case CONTEXT.md describes but not every edge case named in the phase's research questions.

### MLflow SQLite tracking with mandatory tags (verified working end-to-end)
```python
# Source: verified in this research session (mlflow-skinny 3.13.0 + sqlalchemy + alembic)
import mlflow
from mlflow.tracking import MlflowClient

MANDATORY_TAG_KEYS = {
    "code_hash", "data_hash", "seed", "env_hash",
    "segment_manifest_id", "model_class", "fold_config", "stage",
}

def start_tracked_run(tracking_uri: str, tags: dict[str, str], experiment_name: str):
    missing = MANDATORY_TAG_KEYS - tags.keys()
    if missing:
        raise ValueError(f"missing mandatory tags: {missing}")
    mlflow.set_tracking_uri(tracking_uri)  # e.g. "sqlite:////Volumes/ProjectsSSD/aihedgefund/mlflow/mlflow.db"
    exp = mlflow.get_experiment_by_name(experiment_name)
    exp_id = exp.experiment_id if exp else mlflow.create_experiment(experiment_name)
    return mlflow.start_run(experiment_id=exp_id, tags=tags)  # atomic: tags set at run creation

# Verified: a 64-char hex code_hash and env_hash round-trip through
# MlflowClient().get_run(run_id).data.tags with no truncation (len == 64 both directions).
```

### SQLite tracking URI form (verified)
```python
# Source: verified in this research session
import os
MLFLOW_ROOT = "/Volumes/ProjectsSSD/aihedgefund/mlflow"
tracking_uri = f"sqlite:///{os.path.abspath(MLFLOW_ROOT)}/mlflow.db"
# On an absolute MLFLOW_ROOT (starts with "/"), this yields exactly 4 leading slashes
# after "sqlite:", e.g. "sqlite:////Volumes/ProjectsSSD/aihedgefund/mlflow/mlflow.db"
# — matches CONTEXT.md's expected form and matches SQLAlchemy's documented
# absolute-path URI convention.
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Full `mlflow` for any tracking need | `mlflow-skinny` for client-only tracking (no model registry flavors needed) | `mlflow-skinny` has existed since ~MLflow 2.x; still current at 3.13.0/3.16.0 | Avoids the pandas/pyarrow/sklearn/scipy/matplotlib dependency bloat this project cannot tolerate |
| `uv sync` with implicit re-lock | `uv sync --locked` in CI (explicit drift-fail) | uv's CI-in-GitHub-Actions guide currently recommends `--locked`; `--frozen` remains for "trust the lock blindly" use cases | Prevents CI silently accepting a stale lockfile |
| Manual ruff install in CI | `astral-sh/setup-uv` + `uv run ruff` (lockfile-pinned) OR `astral-sh/ruff-pre-commit` (independently pinned) | Both are current; the project should pick lockfile-pinned-everywhere to avoid the two-pin drift class (Pitfall 2) | Simpler mental model: one ruff version, one place it's declared |

**Deprecated/outdated:** None identified specific to this phase's stack — all recommended tools (`tomllib`, `mlflow-skinny`, `astral-sh/setup-uv`, `astral-sh/ruff-pre-commit`) are current, actively maintained choices as of 2026-09-13.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|----------------|
| A1 | `mlflow-skinny`'s SQL-store code path (beyond the basic run/tag/metric operations tested) never imports pandas even for more advanced query operations (e.g., `search_runs` returning a DataFrame-shaped result) that later phases might use | Standard Stack, Pitfall 1 | If a later phase calls `mlflow.search_runs()` (the pandas-DataFrame-returning convenience API) instead of `MlflowClient().search_runs()` (list-of-Run-objects API), pandas could still get imported lazily even under `mlflow-skinny` if that function exists there. Not tested in this session — only `start_run`, `log_metric`, `get_run` were verified. Mitigation: mandate `MlflowClient` object-based APIs project-wide in spec.md, never the `mlflow.search_runs()` pandas-returning convenience function |
| A2 | The exact ruff version to pin (`0.15.*` per existing `mvp/pyproject.toml` vs `0.16.7` current PyPI latest) is Claude's/planner's discretion, not a blocking decision | Standard Stack | Low risk either way — this phase's checks don't depend on ruff-version-specific rule behavior; picking wrong just means an extra `uv lock` bump later |
| A3 | `mlflow-skinny`'s `mlflow ui`/`mlflow server` commands, while present, may have visually degraded artifact-browsing (e.g., no DataFrame-table rendering for logged CSV artifacts) compared to full `mlflow` — not verified in this session beyond confirming the CLI commands exist and `--help` responds | Standard Stack, Don't Hand-Roll | Low risk for this phase (no artifacts logged yet, dummy metric only); worth a spot-check in Phase 5+ when real reporting artifacts start landing |

**If this table is empty:** N/A — see above.

## Open Questions

1. **Should the ruff pin move to 0.16.x now or stay at 0.15.x?**
   - What we know: `mvp/pyproject.toml` currently pins `ruff==0.15.*`; PyPI's current latest is `0.16.7`; `astral-sh/ruff-pre-commit`'s latest tag is also `v0.16.7`.
   - What's unclear: whether ruff 0.16 introduces any rule-behavior change that affects the existing `TID251` banned-pandas config from Phase 1.
   - Recommendation: hold at `0.15.*` for this infra-only phase to minimize unrelated diff noise; bump ruff as a dedicated, separately-reviewed change if desired later. Either choice is compatible with everything else in this research.

2. **Does `mlflow-skinny` fully cover any MLflow feature later phases (5, 9) will need (e.g., nested runs for the selection-bias ledger, `MlflowCallback` from `optuna-integration`)?**
   - What we know: `mlflow-skinny` covers `start_run`, nested runs (untested here but documented as a core tracking-client feature, not a full-mlflow-only feature), tags, metrics, `MlflowClient`.
   - What's unclear: `optuna-integration`'s `MLflowCallback` (Phase 9) internals were not inspected this session — need to verify it doesn't hard-import full `mlflow` (defeating the point of switching to `mlflow-skinny` now).
   - Recommendation: re-verify `optuna-integration[mlflow]`'s dependency graph specifically in Phase 9's research, before that phase adds the dependency; not a Phase 2 blocker since Phase 2 has no Optuna usage.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.13 | Project runtime | ✓ | 3.13.3 | — |
| uv | Dependency management | ✓ | 0.11.6 (Homebrew) | PyPI latest is 0.12.13; current install is functional, no action needed this phase |
| ruff | Lint/format | ✓ | 0.14.6 (global), project pins 0.15.* in venv | — |
| pre-commit | Git hooks | ✓ | 4.0.1 (global) | Project should `uv add --dev pre-commit` to pin in-repo |
| gh (GitHub CLI) | Verifying Action tags, general repo ops | ✓ | 2.89.0 | — |
| GitHub Actions | CI runner | ✓ (assumed — repo is on GitHub per CLAUDE.md conventions) | — | Private repo, 2,000 free Linux minutes/month confirmed current for 2026 |
| MLflow (full) | — | N/A — deliberately not used | — | Use `mlflow-skinny` instead (see Pitfall 1) |
| `/Volumes/ProjectsSSD` | MLflow tracking root | ✓ | 885 GiB free of 1.8Ti, 53% used | Same volume Phase 1 uses for `data_root`; `validate_data_root()` reuse is directly viable |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** None — all required tools are present or a straightforward `uv add`.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.x (project pin, `mvp/pyproject.toml`) |
| Config file | `mvp/pyproject.toml` (`[tool.pytest.ini_options]`, `testpaths = ["tests"]`) |
| Quick run command | `uv run pytest tests/test_catalogue.py tests/test_numba_globals.py tests/test_latest_ban.py -x` |
| Full suite command | `uv run pytest` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|---------------------|-------------|
| SPEC-01 | TOML catalogue parses and validates schema | unit | `uv run pytest tests/test_catalogue.py::test_schema_validation -x` | ❌ Wave 0 |
| SPEC-01 | A definition change under an existing name is rejected | unit | `uv run pytest tests/test_catalogue.py::test_definition_change_rejected -x` | ❌ Wave 0 |
| SPEC-02 | catalogue-completeness check fails on uncatalogued feature use | unit (red-on-violation fixture) | `uv run pytest tests/test_catalogue_completeness.py::test_uncatalogued_feature_fails -x` | ❌ Wave 0 |
| SPEC-02 | `import pandas` fails ruff TID251 | already covered | `uv run ruff check` against a fixture file with `import pandas` | ✅ (Phase 1 proved TID251 red) |
| SPEC-02 | "latest" path-construction reference fails the targeted check | unit (red-on-violation fixture) | `uv run pytest tests/test_latest_ban.py::test_latest_path_literal_fails -x` | ❌ Wave 0 |
| SPEC-03 | Decision-rule pseudocode in spec.md matches corrected form | doc/manual | N/A — reviewed via spec.md diff in PR | N/A |
| SPEC-04 | Sharpe convention function computes correctly on golden fixture | unit | `uv run pytest tests/test_sharpe.py::test_daily_sharpe_golden_fixture -x` | ❌ Wave 0 (Sharpe function itself may be a stub in this phase — see note below) |
| SPEC-04 | numba-globals check fails on a fixture `@njit` reading a mutable global | unit (red-on-violation fixture) | `uv run pytest tests/test_numba_globals.py::test_global_read_fails -x` | ❌ Wave 0 |
| TRACK-01 | MLflow smoke run records all 8 mandatory tags | integration (temp SQLite file) | `uv run pytest tests/test_mlflow_utils.py::test_mandatory_tags_present -x` | ❌ Wave 0 |
| TRACK-01 | Wrapper rejects `start_run` with a missing mandatory tag | unit | `uv run pytest tests/test_mlflow_utils.py::test_missing_tag_rejected -x` | ❌ Wave 0 |
| TRACK-02 | `uv lock --check` / pin-assertion test catches drift | unit + CI | `uv run pytest tests/test_pins.py::test_numba_numpy_llvmlite_pins -x` | ❌ Wave 0 |

**Note on SPEC-04/Sharpe:** the phase's scope is to *pre-declare* the Sharpe convention in spec.md, not to build the evaluation harness (that's Phase 5/9). If the plan includes a golden-fixture unit test for the Sharpe formula itself, it should be a small standalone function (e.g., `mvp/eval/sharpe.py::annualized_sharpe(daily_pnl, floor_obs=30)`) scoped narrowly enough not to overlap Phase 5's fold harness or Phase 9's full reporting suite — confirm this scoping with the planner rather than assuming it's in-phase.

### Sampling Rate
- **Per task commit:** quick run command above (catalogue + numba-globals + latest-ban tests — the three new custom-AST checks are the highest-risk-of-silent-bug code in this phase)
- **Per wave merge:** full suite command (`uv run pytest`)
- **Phase gate:** Full suite green, plus a manual verification that every new CI check has been observed red-then-green once (per CONTEXT.md's "every CI check must be proven red" rule) before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `mvp/tests/test_catalogue.py` — covers SPEC-01
- [ ] `mvp/tests/test_catalogue_completeness.py` — covers SPEC-02
- [ ] `mvp/tests/test_latest_ban.py` — covers SPEC-02
- [ ] `mvp/tests/test_numba_globals.py` — covers SPEC-04 (prototype logic in this RESEARCH.md's Code Examples is a strong starting point)
- [ ] `mvp/tests/test_mlflow_utils.py` — covers TRACK-01 (use a `tmp_path` fixture SQLite file, per the pattern verified in this research's scratch test)
- [ ] `mvp/tests/test_pins.py` — covers TRACK-02
- [ ] Framework install: none — pytest already in `mvp/pyproject.toml` dev group

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-------------------|
| V2 Authentication | No | No auth surface in this phase (no service, no API) |
| V3 Session Management | No | N/A |
| V4 Access Control | Marginal | MLflow tracking root path validated by `validate_data_root()` (reused, not duplicated) — prevents writing tracking data into a cloud-synced path, which is a data-integrity control, not strictly an access-control one |
| V5 Input Validation | Yes | TOML catalogue entries validated for required keys (Pattern 1); mandatory MLflow tags validated before `start_run` (Pitfall 4) |
| V6 Cryptography | Marginal | `code_hash`/`env_hash` use SHA-256 (git's own SHA-1/SHA-256 for commit hash; explicit SHA-256 for `uv.lock` hash per CONTEXT.md) — standard library `hashlib.sha256`, never hand-rolled |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|-----------------------|
| Secrets accidentally logged as MLflow tags/params (e.g., an API key pasted into a config that gets hashed/logged) | Information Disclosure | This phase's tag schema is a small, fixed, reviewed set (code_hash/data_hash/seed/env_hash/segment_manifest_id/model_class/fold_config/stage) — no free-form config dumping. Keep it that way; free-form param logging is deferred to later phases and should go through an explicit allowlist, not raw dict dumps |
| CI running arbitrary code from an untrusted PR (fork) with access to MLflow tracking root credentials | Elevation of Privilege | Not applicable yet — no secrets are used in this phase's CI (all checks are static analysis + a local SQLite file); flag for later phases once CI needs any credential |
| A committed `.pre-commit-config.yaml` pinning an unpinned/mutable Git ref for a third-party hook (supply-chain risk) | Tampering | Pin `astral-sh/ruff-pre-commit` by tag/SHA (`rev: v0.16.7`), not a branch name — standard pre-commit hygiene, already implied by CONTEXT.md's "identical check set" requirement |

## Sources

### Primary (HIGH confidence — empirically verified in this session)
- Scratch venv test: `pip show mlflow` → `Requires: aiohttp, alembic, cryptography, docker, Flask, Flask-CORS, graphene, gunicorn, huey, matplotlib, mlflow-skinny, mlflow-tracing, numpy, pandas, pyarrow, scikit-learn, scipy, skops, sqlalchemy` — pandas confirmed as a hard dependency of full `mlflow==3.13.0`
- Scratch venv test: `import mlflow` alone puts `pandas` in `sys.modules` (`True`) before any tracking call
- Scratch venv test: `mlflow-skinny==3.13.0` + `sqlalchemy` + `alembic` — full tag/run/get_run round-trip against a SQLite backend, `pandas` never in `sys.modules`
- Scratch venv test: `mlflow ui --help` and `mlflow server` both present under `mlflow-skinny`
- Scratch test: SQLite tracking URI form `sqlite:////absolute/path/mlflow.db` (four slashes) confirmed by constructing the string from an absolute path and initializing a real MLflow SQLite store
- Scratch test: 64-character hex tag values (`code_hash`, `env_hash`) round-trip through `MlflowClient.get_run().data.tags` with no truncation
- Scratch test: `tomllib` (stdlib) parses a features.toml fixture matching CONTEXT.md's exact required-column list with zero missing keys
- Scratch test: numba no-globals AST-check prototype correctly distinguishes an uppercase module constant (allowed) from a lowercase mutable module global (flagged) referenced inside an `@njit`-decorated function
- `pip index versions` (PyPI, 2026-09-13): `ruff` 0.16.7 latest / project pins 0.15.*; `pre-commit` 4.6.2 latest; `mlflow-skinny` 3.16.0 latest (3.13.0 available, matches STACK.md pin); `uv` 0.12.13 latest (0.11.6 installed locally)
- `gh api repos/astral-sh/ruff-pre-commit/releases/latest` → `v0.16.7`
- `gh api repos/astral-sh/setup-uv/releases/latest` → `v10.1.0`
- Local environment probe: Python 3.13.3, uv 0.11.6, ruff 0.14.6 (global), pre-commit 4.0.1 (global), gh 2.89.0, `/Volumes/ProjectsSSD` has 885 GiB free — same volume Phase 1 validated for `data_root`
- `mvp/data/capture/config.py` — read directly, confirms `validate_data_root()` signature (`data_root: str, min_free_gb: float = 50.0`) is reusable as-is for the MLflow tracking root guard

### Secondary (MEDIUM confidence — WebSearch, cross-checked against multiple results)
- [astral-sh/uv GitHub Actions integration guide](https://docs.astral.sh/uv/guides/integration/github/) — `uv sync --locked` recommended for CI; `--frozen` vs `--locked` semantics
- [uv docs: Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/) — `--frozen` skips the up-to-date check entirely; `--locked` fails if out of date
- [astral-sh/ruff-pre-commit repo](https://github.com/astral-sh/ruff-pre-commit) and multiple GitHub issues (e.g., a project's CI red because pre-commit ruff rev and pyproject.toml ruff version diverged) — cross-verified the version-drift failure mode described in Pitfall 2
- [Numba FAQ: globals as compile-time constants](https://numba.readthedocs.io/en/0.51.1/user/faq.html) and [numba #9664](https://github.com/numba/numba/issues/9664) — confirms the frozen-global behavior this phase's lint rule defends against (already in PITFALLS.md, re-confirmed here)
- General git-rename-detection behavior (`git mv` + `git log --follow`) — cross-checked across multiple independent explainer sources, consistent description

### Tertiary (LOW confidence — single source, not independently re-verified)
- GitHub Actions private-repo free-minute figure (2,000/month) — sourced from third-party pricing-aggregator sites (cicdcalculator.com, warpbuild.com), not GitHub's own pricing page directly; consistent across three independent sites, and matches the figure already asserted in CONTEXT.md, so treated as reliable but flagged as not primary-sourced this session

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every version-critical claim (mlflow-skinny vs mlflow, ruff/pre-commit/setup-uv versions) was verified via `pip index versions`, `gh api`, or a live install-and-run test, not training-data recall
- Architecture: HIGH — patterns are directly derived from CONTEXT.md's locked decisions plus empirically-proven mechanics (tomllib schema check, AST check, MLflow tag round-trip)
- Pitfalls: HIGH for Pitfall 1 (empirically proven, the most consequential finding in this research) and Pitfall 4 (empirically proven); MEDIUM for Pitfalls 2/3/5/6 (WebSearch-verified against official docs/multiple sources but not independently re-executed against this exact repo's CI)

**Research date:** 2026-09-13
**Valid until:** 30 days (infra/tooling versions move fast — re-verify ruff/mlflow-skinny/setup-uv pins if planning is delayed past early October 2026)
