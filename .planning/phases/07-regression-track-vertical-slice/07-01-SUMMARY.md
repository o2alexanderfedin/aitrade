---
phase: 07-regression-track-vertical-slice
plan: 01
subsystem: environment
tags: [dependencies, uv-lock, guardrail, pytest-fixtures, test-isolation, scikit-learn, scipy]

# Dependency graph
requires:
  - phase: 02-stage-0-living-spec-ci-guardrails-tracking
    provides: "tools/check_pin_versions.py and its PINNED_PREFIXES/BANNED_PACKAGES enumeration, plus the pre-commit/CI parity that makes a source edit here need no hook-command edit"
  - phase: 05-walk-forward-harness
    provides: "tests/harness/conftest.py's tmp_path lake/registry/tracking fixtures -- the file copied into tests/models/, and the file whose tracking-root defect this plan repairs"
provides:
  - "scikit-learn 1.9.1 + scipy 1.18.1 + joblib 1.6.0 + narwhals 2.26.0 + threadpoolctl 3.7.0 in mvp/uv.lock and the project venv, with numpy/numba/llvmlite unmoved"
  - "tools/check_pin_versions.py: a fourth pinned prefix, scikit-learn 1.9, so a silent bump fails the commit; the PASS line now derives its package list from the dict instead of a hard-coded string"
  - "tests/spec/test_pins.py: scikit-learn in GOOD_LOCK plus two permanent tests (drifted version, absent entry) -- the mutation check in durable form"
  - "tests/models/: the phase's test-directory floor, WITHOUT __init__.py, with a conftest whose autouse fixture genuinely isolates the canonical MLflow tracking root"
  - "tests/models/test_sklearn_environment.py: research assumptions A1 and A3 executed rather than read"
  - "tests/harness/conftest.py: the repaired autouse fixture -- existing-code fix, not a Phase 7 concern"
affects: [07-02, 07-03, 07-04, 07-05, 07-06, 07-07, 07-08, 07-09, 07-10, 07-11]

# Tech tracking
tech-stack:
  added:
    - "scikit-learn 1.9.1 (declared scikit-learn==1.9.*)"
    - "scipy 1.18.1 (transitive)"
    - "joblib 1.6.0 (transitive)"
    - "narwhals 2.26.0 (transitive)"
    - "threadpoolctl 3.7.0 (transitive)"
  patterns:
    - "An autouse test fixture that SETS the canonical-root env var to tmp_path, rather than popping it -- popping is isolation only for tests that also pass their own root, because the resolver's fallback IS the real root and the canonicality guard cannot tell the two apart"
    - "A guardrail's own PASS message derived from the enumeration it checks (`\"/\".join(PINNED_PREFIXES)`), so the message cannot drift from the check -- it had already drifted once, still naming three pins"
    - "Three-hash mutation discipline applied twice: hash, mutate, assert the hash CHANGED, run, restore, assert the hash matches the original. A mutation that never applied is indistinguishable from an uncovered test"

key-files:
  created:
    - mvp/tests/models/conftest.py
    - mvp/tests/models/test_sklearn_environment.py
    - .planning/phases/07-regression-track-vertical-slice/deferred-items.md
  modified:
    - mvp/pyproject.toml
    - mvp/uv.lock
    - mvp/tools/check_pin_versions.py
    - mvp/tests/spec/test_pins.py
    - mvp/tests/harness/conftest.py

key-decisions:
  - "The plan named mvp/tests/tools/test_check_pin_versions.py as the file enumerating PINNED_PREFIXES. That file does not exist, and no test anywhere enumerated the dict. The real tests live in mvp/tests/spec/test_pins.py; that file was edited instead. Adding scikit-learn to PINNED_PREFIXES breaks its GOOD_LOCK fixture (missing pinned package), so the fixture gained a scikit-learn block -- a required edit the plan did not anticipate."
  - "The plan asked for the spearmanr constant-input NaN to be observed under `simplefilter(\"error\", ConstantInputWarning)`. Under that filter the warning RAISES and spearmanr never returns, so the NaN cannot be observed in that call -- verified directly. The test does both halves in two calls: `pytest.raises(ConstantInputWarning)` under \"error\" proves scipy classifies the input as constant rather than silently computing, then \"ignore\" proves the value is NaN and not 0.0."
  - "The two conftest-isolation tests live INSIDE test_sklearn_environment.py rather than in a third file, because the plan's verification pins tests/models/ to exactly two files. Without them, the new tracking_root fixture and its exist_ok=True repair would ship with nothing requesting them -- which is precisely how the same defect survived unnoticed in tests/harness/conftest.py."
  - "PINNED_PREFIXES was NOT widened to scipy/joblib/narwhals/threadpoolctl. CLAUDE.md pins none of them; pinning them would turn a routine `uv lock` refresh into a guardrail failure."
  - "mvp/tests/lockbox/conftest.py carries the identical pop-only defect and was NOT fixed -- out of scope, and unlike the harness copy its docstring describes what its code actually does. Logged in deferred-items.md."

requirements-completed: []
# FCST-01 is this plan's frontmatter tag, but it is NOT marked complete here
# and REQUIREMENTS.md is left at Pending. FCST-01 is "Regression track
# (linear -> ridge/elastic-net -> small non-linear) predicting 10s midprice
# return, scikit-learn" -- installing scikit-learn is its floor, not its
# satisfaction, and nine of Phase 7's eleven plans carry the same tag. The
# last of those is what completes it.

# Metrics
duration: ~18min across the three task commits (23:57:23 to 00:15:11); the full session was longer -- the baseline suite alone runs 4m37s and pre-commit re-runs it on every commit
completed: 2026-09-25
---

# Phase 7 Plan 1: Dependency floor, mechanical pin, and a test-isolation repair Summary

**`scikit-learn` 1.9.1 and `scipy` 1.18.1 are now installed and locked without moving the numpy/numba/llvmlite triple pin, the 1.9 line is asserted by a guardrail instead of a comment, and an autouse fixture that claimed for two phases to isolate tests from the real MLflow store — and did not — now actually does.**

## Performance

- **Duration:** ~18 min across the three task commits (23:57:23 → 00:15:11)
- **Tasks completed:** 3/3
- **Commits:** 3

## What Was Built

### Task 1 — the dependency floor (`c21fabc`)

`uv add "scikit-learn==1.9.*"` then `uv lock` then `uv sync --frozen`. The
resolution matched D-07-28's pre-measurement on an isolated lockfile copy
exactly, number for number:

| Measurement | Expected (D-07-28) | Observed |
|---|---|---|
| new `[[package]]` entries | 5 | 5 |
| `joblib` | 1.6.0 | 1.6.0 |
| `narwhals` | 2.26.0 | 2.26.0 |
| `scikit-learn` | 1.9.1 | 1.9.1 |
| `scipy` | 1.18.1 | 1.18.1 |
| `threadpoolctl` | 3.7.0 | 3.7.0 |
| `numpy` | 2.4.6 | 2.4.6 |
| `numba` | 0.65.1 | 0.65.1 |
| `llvmlite` | 0.47.0 | 0.47.0 |
| `pandas` entries, any marker | 0 | 0 |
| total packages | — | 65 → 70 |

Two checks beyond the plan's `^+name = ` count, both clean: `git diff
mvp/uv.lock | grep '^-name = '` is empty (nothing was removed), and
`grep -E '^-version = '` is empty (no already-locked package moved — the
`>=`-ranged ones, alembic/sqlalchemy/pytest-cov, all held). `cloudpickle`
3.1.2 was confirmed present at `HEAD` before the change, so it is not a sixth
addition; `joblib` merely also declares it (correction C7 stands).

Runtime import line: `sklearn 1.9.1 scipy 1.18.1 numpy 2.4.6 numba 0.65.1
llvmlite 0.47.0`.

### Task 2 — the pin made mechanical (`3bfadd5`)

`PINNED_PREFIXES` gains `"scikit-learn": "1.9"`, with a `#:` comment block
stating both why it is there (CLAUDE.md names the 1.9 line; this is where a
mandate becomes mechanical) and why its transitive closure deliberately is
not. Also fixed en route: the PASS line hard-coded
`"pins match numba/numpy/llvmlite"` and would have kept saying three pins
while checking four. It now derives the list from the dict.

**Mutation check, three hashes** — `mvp/tools/check_pin_versions.py`:

```
PINNED_PREFIXES: dict[str, str] = {     PINNED_PREFIXES: dict[str, str] = {
    "numba": "0.65",                        "numba": "0.65",
    "numpy": "2.4",              ---->      "numpy": "2.4",
    "llvmlite": "0.47",                     "llvmlite": "0.47",
    "scikit-learn": "1.9",                  "scikit-learn": "1.8",
}                                       }
```

| Stage | sha256 |
|---|---|
| pre-mutation | `ad0c5a71e599bf68843c3f01d6195a9b1fff27743e819f7650df47da877a405f` |
| mutated | `4a9417a346b0f3633b7eb273311a9e801797860c34939d969394c14d9a502875` |
| restored | `ad0c5a71e599bf68843c3f01d6195a9b1fff27743e819f7650df47da877a405f` |

The mutated hash differs from the pre-mutation hash, so the substitution
genuinely applied. Under it the checker printed
`FAIL: scikit-learn version '1.9.1' does not match pinned prefix '1.8'.*` and
exited 1, and six tests in `test_pins.py` failed. The restored hash equals the
original.

### Task 3 — the models floor, and the repair (`c88935a`)

`mvp/tests/models/` exists with exactly `conftest.py` and
`test_sklearn_environment.py`, and **no `__init__.py`**.

**Research assumption A1, executed.** `Ridge(alpha=1.0)` fitted on a dense
float64 `(40, 3)` design has `solver_ == "cholesky"` — the observed value. Two
fits differing only in `random_state` (0 vs 12345) give `coef_` equal under
`np.array_equal` and identical `intercept_`; both report `solver_ ==
"cholesky"`. The phase's "no seed changes any Stage-1 result" conclusion now
rests on an assertion rather than a source reading. No coefficient value is
pinned as a literal (correction C6 — this host is Apple Accelerate, CI is
Linux/OpenBLAS).

**Research assumption A3, executed.** `spearmanr` returns a
`SignificanceResult` exposing `.statistic` (observed type `numpy.float64`,
which is a `float` subclass). On a constant first argument it emits
`scipy.stats.ConstantInputWarning` and the observed statistic is `nan`, not
`0.0` — which is what plan 07-07's gate (D-07-32) depends on.

## The Defect Repaired

`tests/harness/conftest.py`'s autouse `isolated_canonical_tracking_root` said
it "points the canonical MLflow store at each test's own `tmp_path`". Its body
only popped `AIHF_MLFLOW_TRACKING_ROOT`. With the variable absent,
`lake_paths.mlflow_tracking_root(None)` falls through to
`DEFAULT_MLFLOW_TRACKING_ROOT` — the real store — and
`_require_canonical_tracking_root` cannot catch it, because the real root *is*
the canonical one. Only a test that separately requested the `tracking_root`
fixture was ever isolated.

**Probe, a throwaway test in `tests/harness/` requesting no fixture beyond the
autouse ones, deleted before staging:**

| | resolved canonical root |
|---|---|
| before | `/Volumes/ProjectsSSD/aihedgefund/mlflow` — the real store |
| after | `/private/var/folders/.../pytest-341/test_probe_resolved_canonical_0/mlflow_root` |

Both conftests now SET the variable, `mkdir(parents=True, exist_ok=True)`, and
restore the previous value (or its absence). The `tracking_root` fixture drops
its bare `root.mkdir()` for `exist_ok=True`: the autouse fixture runs first and
has already created that directory, so a bare `mkdir` would now raise
`FileExistsError` in every test that asks for `tracking_root`. The two fixtures
share one path that either may create and neither owns; both docstrings say so,
and both docstrings now describe what their code does.

The probe's permanent form is
`test_canonical_tracking_root_resolves_inside_tmp_path_with_no_fixture_asked`
in the models suite.

**Mutation check, three hashes** — `mvp/tests/models/conftest.py`, reverting
the `SET` back to the old `pop`:

| Stage | sha256 |
|---|---|
| pre-mutation | `d1ecf59eb73aa01fd07629d00b52e955125974f79ea5e7708efd299c58ffabbd` |
| mutated | `bdeb9f838ad2c6734ebb3dde36f244614aa23c21160b041d33b1fa00662a2bbe` |
| restored | `d1ecf59eb73aa01fd07629d00b52e955125974f79ea5e7708efd299c58ffabbd` |

Under the mutation both isolation tests failed and the three A1/A3 tests
correctly did not — they are pure numpy and have nothing to do with the
tracking root.

## Test Counts

| Point | Tests |
|---|---|
| baseline, before any change | 1130 passed (4m37s) |
| after all three commits | 1137 passed (5m05s) |

+7: two permanent pin tests (drifted scikit-learn, absent scikit-learn) and
five models tests (three A1/A3, two isolation). Zero pre-existing tests
changed behaviour — notably, no harness test depended on the pop.

## Budget Integrity

`budget.look_count` against the real tracking root and the issued segment
manifest `97964cb2…e330e2`, read-only, measured before the first change and
again after the last commit:

| Segment | before | after |
|---|---|---|
| `val` | 0 | 0 |
| `oof_block_0` … `oof_block_4` | 0 | 0 |

`harness.accessor.materialize` was never called.

## Deviations from Plan

### 1. [Rule 3 — Blocking] The plan named a test file that does not exist

- **Found during:** Task 2
- **Issue:** `files_modified` and Task 2's `<verify>` both name
  `mvp/tests/tools/test_check_pin_versions.py`. It does not exist, and
  `grep -rn PINNED_PREFIXES mvp/tests/` found no test enumerating the dict at
  all — the plan's "it very likely pins the `PINNED_PREFIXES` dict contents"
  was wrong in both particulars.
- **Fix:** Edited the real file, `mvp/tests/spec/test_pins.py`. Adding a fourth
  prefix breaks its `GOOD_LOCK` fixture ("scikit-learn is missing from the
  lockfile entirely"), so the fixture gained a `scikit-learn 1.9.1` block —
  a required edit the plan did not anticipate. Two permanent tests were added
  in place of the enumeration assertion the plan expected to update.
- **Files:** `mvp/tests/spec/test_pins.py`
- **Commit:** `3bfadd5`

### 2. [Rule 1 — Bug] The plan's spearmanr instruction contradicts itself

- **Found during:** Task 3
- **Issue:** It asks for the constant-input NaN to be asserted while catching
  the warning with `simplefilter("error", ConstantInputWarning)`. Verified
  directly: under that filter `warnings.warn` raises, `spearmanr` never
  returns, and there is no value whose NaN-ness could be checked.
- **Fix:** Two calls, both halves kept. `pytest.raises(ConstantInputWarning)`
  under `"error"` proves scipy classifies the input as constant rather than
  silently computing a number; a second call under `"ignore"` proves the value
  it would have returned is `nan`, not `0.0`. The test docstring says why it is
  two calls.
- **Files:** `mvp/tests/models/test_sklearn_environment.py`
- **Commit:** `c88935a`

### 3. [Rule 2 — Missing coverage] The new conftest would have shipped untested

- **Found during:** Task 3
- **Issue:** The plan's verification pins `tests/models/` to exactly two files,
  and neither of the three A1/A3 tests requests `tracking_root` — so the new
  fixture, the `exist_ok=True` repair, and the isolation guarantee would all
  have had zero tests exercising them. That is exactly how the defect survived
  in `tests/harness/conftest.py`.
- **Fix:** Two isolation tests added inside `test_sklearn_environment.py`
  rather than in a third file, keeping the directory listing the plan verifies.
  The module docstring states why they live there.
- **Files:** `mvp/tests/models/test_sklearn_environment.py`
- **Commit:** `c88935a`

### 4. [Rule 1 — Bug] The guardrail's PASS message named three pins while checking four

- **Found during:** Task 2
- **Issue:** `print(f"PASS: ... pins match numba/numpy/llvmlite; pandas absent")`
  is a hard-coded string beside an enumeration it does not read.
- **Fix:** Derived from `PINNED_PREFIXES`. The message cannot drift from the
  check again.
- **Files:** `mvp/tools/check_pin_versions.py`
- **Commit:** `3bfadd5`

## Deferred

`mvp/tests/lockbox/conftest.py` carries the identical pop-only defect and was
deliberately left alone — see `deferred-items.md` D1. Two reasons: its
docstring is honest about what it does (it says "cleared", not "pointed at
`tmp_path`"), and no validation look flows through lockbox tests, since
`harness.budget.record_look` is the only thing that spends one.

## Threat Flags

None. The plan's register (T-07-01…04) is unchanged by what was executed:
T-07-01 and T-07-02 are strengthened as planned, T-07-03's mitigation is the
conftest this plan wrote, and T-07-04 (`cloudpickle` in the environment via
`joblib`) remains accepted for this plan — nothing added here imports it.

## What Was NOT Done

- **`harness.accessor.materialize` was never called, and no validation look was
  spent.** All six `look_count` values are 0, verified before and after.
- **`mvp/tests/lockbox/conftest.py` was not repaired**, despite carrying the
  same defect. Logged, not fixed.
- **`PINNED_PREFIXES` was not widened** to `scipy`, `joblib`, `narwhals` or
  `threadpoolctl`.
- **No `mvp/models/` production code exists yet.** This plan built the test
  directory floor only; `models/` itself is later plans' work.
- **FCST-01 was NOT marked complete in `REQUIREMENTS.md`**, despite being this
  plan's frontmatter tag. Nine of Phase 7's eleven plans carry it; installing
  the library is the requirement's floor, not its satisfaction. Checking the
  box here would have made the traceability table claim a regression track
  that does not exist yet.
- **Nothing was pushed.** The three commits are local to
  `feature/phase-07-regression-track-vertical-slice`.
- **CI was not run.** Every check was run locally via the 18 pre-commit hooks;
  the pre-push `check_no_manifest_rewrite --full` leg (hook 19) has not run,
  and GitHub Actions has not seen these commits.
- **The A1 assumption's other two claims were not executed.** A1 also asserts
  `ElasticNet`'s `rng` is unused under `selection="cyclic"` and that
  `LinearRegression` resolves to `lstsq`. The plan named only the `Ridge` half;
  those two remain read-but-not-run.

## Self-Check: PASSED

- `mvp/tests/models/conftest.py` — FOUND
- `mvp/tests/models/test_sklearn_environment.py` — FOUND
- `mvp/tests/models/__init__.py` — correctly ABSENT
- `.planning/phases/07-regression-track-vertical-slice/deferred-items.md` — FOUND
- commits `c21fabc`, `3bfadd5`, `c88935a` — all FOUND in `git log`
- working tree clean after the third commit
