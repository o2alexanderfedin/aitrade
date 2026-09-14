---
phase: 02-stage-0-living-spec-ci-guardrails-tracking
plan: 02
subsystem: infra
tags: [ast, ci-guardrails, tomllib, ruff, numba, uv-lock, ms-to-ns]

# Dependency graph
requires:
  - phase: 02-stage-0-living-spec-ci-guardrails-tracking/02-01
    provides: mvp/spec/catalogue.py typed registry (get_feature/get_label/load_features/load_labels), mvp/tools/ package, mvp/spec.md numba no-globals rule section
provides:
  - mvp/tools/check_catalogue_completeness.py -- AST scan rejecting uncatalogued or non-literal get_feature/get_label call sites
  - mvp/tools/check_latest_ban.py -- AST scan rejecting a literal "latest" path segment in path-construction calls or `/`-joins, scoped to avoid prose false positives
  - mvp/tools/check_numba_globals.py -- AST scan rejecting an @njit function reading a module-level non-constant global (or an explicit `global` statement)
  - mvp/tools/check_pin_versions.py -- tomllib-based assert_pins(lock_path) for numba/numpy/llvmlite pins + pandas absence, with a --lock-path escape hatch for red-proofing without touching the real uv.lock
  - mvp/tools/check_ms_to_ns_site.py -- regex scan enforcing exactly one ms-to-ns conversion site at data/capture/parse.py
affects: [02-stage-0-living-spec-ci-guardrails-tracking/02-04, 04-feature-engine]

# Tech tracking
tech-stack:
  added: []  # stdlib only (ast, re, tomllib, argparse, os)
  patterns:
    - "Every CI check script: PKG_ROOT = Path(__file__).resolve().parents[1], scan_source(source, filename) -> list[Violation] directly testable on in-memory strings, main() prints a visible scanned-count line and returns an exit code, dataclass Violation with filename/lineno/message and a f'{filename}:{lineno}: {message}' __str__"
    - "AST scanners exclude .venv/__pycache__/.pytest_cache/.ruff_cache/tests-path substrings via a shared EXCLUDE_MARKERS tuple + _excluded(path) helper, never a real fixture file on disk"
    - "A regex-based check whose own pattern literal is a syntactic match for itself (check_ms_to_ns_site.py) self-excludes by resolved __file__ path, not a blanket directory exemption"

key-files:
  created:
    - mvp/tools/check_catalogue_completeness.py
    - mvp/tools/check_latest_ban.py
    - mvp/tools/check_numba_globals.py
    - mvp/tools/check_pin_versions.py
    - mvp/tools/check_ms_to_ns_site.py
    - mvp/tests/spec/test_catalogue_completeness.py
    - mvp/tests/spec/test_latest_ban.py
    - mvp/tests/spec/test_numba_globals.py
    - mvp/tests/spec/test_pins.py
  modified: []

key-decisions:
  - "check_catalogue_completeness.py's scan_source(source, filename, feature_names=None, label_names=None) accepts optional explicit name sets for hermetic in-memory testing (defaulting to the real catalogue via load_features()/load_labels() when omitted) -- reconciles the plan's two statements (\"main() loads names\" vs. \"scan_source checks against the real catalogue\") without ever touching disk from a pure-fixture test."
  - "check_latest_ban.py scans ast.BinOp/ast.Div nodes (Path(\"data\") / \"latest\" / \"x\") in addition to ast.Call arguments -- the plan's <behavior> text only specified Call args, but its own objective line explicitly names \"/-joins\" as in-scope; treated as a Rule 2 (missing critical functionality) addition, not a deviation requiring a checkpoint, since the plan's prose already names the requirement."
  - "check_ms_to_ns_site.py excludes its own resolved file path from the scan: the regex pattern 1_000_000([^_0-9]|$), written as source text inside this very file, is itself a syntactic match for the pattern it implements (the '(' following '1_000_000' in the raw-string literal satisfies [^_0-9]). Self-exclusion by exact path equality, not a directory-wide exemption, keeps the check honest everywhere else in mvp/tools/."
  - "check_numba_globals.py's module-level-global candidate set is built strictly from Assign/AnnAssign/AugAssign targets; ast.Import/ast.ImportFrom bindings are explicitly, permanently exempt per the plan's acceptance criteria -- documented as the accepted residual gap (T-2-03) rather than extended to imports, since 02-RESEARCH.md's alternative recommendation conflicts with the plan text and the plan is authoritative."
  - "check_pin_versions.py treats a pinned package (numba/numpy/llvmlite) missing from the lockfile entirely as a failure, not a silent pass -- not explicitly required by the plan's <behavior> prose but consistent with its 'confirms pandas is absent from the lockfile entirely' spirit and the must_haves' 'fails on pin drift' requirement."

patterns-established:
  - "AST guardrail scripts are proven red exclusively via in-memory scan_source(source, filename) fixture strings -- never a stray real .py file under a scanned directory -- so a fixture can never accidentally trip the same script's own repo-wide main() scan."
  - "Every check script's main() prints a 'scanned {N} files...' (or equivalent) visibility line even on a legitimately-zero-violation, zero-match run, so a silently-empty scan is distinguishable from a passing guardrail in CI logs."

requirements-completed: [SPEC-02, SPEC-04, TRACK-02]

# Metrics
duration: 4min
completed: 2026-09-13
---

# Phase 2 Plan 02: CI Guardrail Scripts Summary

**Five independently-testable AST/regex CI guardrail scripts (catalogue completeness, "latest"-path ban, numba no-globals, pin-version assertion, ms-to-ns single-site) enforcing SPEC-02/SPEC-04/TRACK-02, each with a red-fixture-proven test suite that never touches disk with a real violation.**

## Performance

- **Duration:** ~4 min (commit span 19:20:29 -> 19:23:57 PDT)
- **Started:** 2026-09-13T19:20:29-07:00
- **Completed:** 2026-09-13T19:23:57-07:00
- **Tasks:** 3 (all TDD: RED commit + GREEN commit per task)
- **Files modified:** 9 new files (5 tools, 4 test files)

## Accomplishments

- `mvp/tools/check_catalogue_completeness.py`: `scan_source` walks `ast.Call` nodes
  matching `get_feature`/`get_label` (both `Name`- and `Attribute`-call styles),
  flags a non-literal first argument or a literal name absent from its own
  namespace (feature names and label names tracked as separate sets so
  `get_feature("ret_10s_mid")` -- a valid label, not a feature -- is correctly
  flagged); `main()` walks `data/features/labels/pipelines/forecast` (existing
  dirs only), prints `scanned {N} files, {M} call sites`, exits 0/1
- `mvp/tools/check_latest_ban.py`: flags a literal `(^|/)latest(/|$)` path
  segment in string/f-string constants passed as call arguments (positional or
  keyword) or as an operand of a `/`-join (`ast.BinOp`/`ast.Div`); prose never
  matches since the regex requires the word to be its own path segment; scoped
  to `mvp/data/` and `mvp/pipelines/`
- `mvp/tools/check_numba_globals.py`: `is_njit_decorated` detects `@njit` and
  `@jit(nopython=True)` spellings; locals are the flat union of every
  `ast.arg` and every Store-context `ast.Name` anywhere in the function's
  subtree (covers nested defs/comprehensions at any depth); a Name load
  resolving to a module-level `Assign`/`AnnAssign`/`AugAssign` target, not in
  locals, not UPPER_CASE, is a violation; an explicit `global` statement is an
  immediate violation regardless of case; `ast.Import`/`ast.ImportFrom`
  bindings are exempt
- `mvp/tools/check_pin_versions.py`: `assert_pins(lock_path)` parses a
  `uv.lock`-shaped TOML with `tomllib`, asserts `numba==0.65.*`,
  `numpy==2.4.*`, `llvmlite==0.47.*` (a pinned package missing entirely is
  also a failure) and pandas absence at any version; `main()`'s `--lock-path`
  CLI arg is used verbatim, letting Plan 04's red-proof point at a tampered
  temporary copy without ever touching the real `mvp/uv.lock`
- `mvp/tools/check_ms_to_ns_site.py`: `find_ms_to_ns_sites(root)` regex-scans
  every `*.py` file for `1_000_000([^_0-9]|$)`, correctly distinguishing the
  one legitimate ms-to-ns site (`data/capture/parse.py:31`) from the existing
  `1_000_000_000` seconds-to-ns sites in `dedup.py`/`rotation.py`; excludes
  its own source file (see Deviations) so the check does not trip on its own
  regex-pattern literal
- 36/36 new tests green; 60/60 across all of `mvp/tests/spec`; `ruff check` +
  `ruff format --check` clean on every new module; all five scripts' `main()`
  run clean (exit 0) against the real repo:
  `check_catalogue_completeness` -> `scanned 13 files, 0 call sites`;
  `check_latest_ban` -> `scanned 13 files`;
  `check_numba_globals` -> `scanned 28 files, 0 njit functions`;
  `check_pin_versions` -> `PASS: ... pins match numba/numpy/llvmlite; pandas absent`;
  `check_ms_to_ns_site` -> `PASS: exactly one ms-to-ns site at data/capture/parse.py:31`

## Task Commits

1. **Task 1 RED: failing catalogue-completeness test** - `dc5fc67` (test)
2. **Task 1 GREEN: check_catalogue_completeness.py** - `14f9590` (feat)
3. **Task 2 RED: failing latest-ban/numba-globals tests** - `0a29c37` (test)
4. **Task 2 GREEN: check_latest_ban.py + check_numba_globals.py** - `2559940` (feat)
5. **Task 3 RED: failing pin-assertion/ms-to-ns tests** - `8d6da04` (test)
6. **Task 3 GREEN: check_pin_versions.py + check_ms_to_ns_site.py** - `5773b49` (feat)

**Plan metadata:** (this commit, SUMMARY.md only -- STATE.md/ROADMAP.md are owned by the orchestrator per the parallel-execution note)

## Files Created/Modified

- `mvp/tools/check_catalogue_completeness.py` - AST scan: get_feature/get_label call sites must use a literal, catalogued name
- `mvp/tools/check_latest_ban.py` - AST scan: string literals in path-construction calls/`/`-joins must not contain a "latest" path segment
- `mvp/tools/check_numba_globals.py` - AST scan: @njit function bodies must not read a module-level non-UPPER_CASE name
- `mvp/tools/check_pin_versions.py` - Reads a uv.lock-shaped TOML, asserts numba/numpy/llvmlite pins and pandas absence
- `mvp/tools/check_ms_to_ns_site.py` - Regex scan: exactly one ms-to-ns conversion site, in data/capture/parse.py
- `mvp/tests/spec/test_catalogue_completeness.py` - 9 tests
- `mvp/tests/spec/test_latest_ban.py` - 7 tests
- `mvp/tests/spec/test_numba_globals.py` - 7 tests
- `mvp/tests/spec/test_pins.py` - 13 tests

## Decisions Made

See `key-decisions` in frontmatter. In summary: `scan_source`'s optional
`feature_names`/`label_names` params reconcile the plan's dual description of
name-loading; `check_latest_ban.py` additionally scans `/`-join `BinOp` nodes
per the plan's objective line even though `<behavior>` only spelled out
`Call` args; `check_ms_to_ns_site.py` self-excludes its own file by resolved
path since its regex pattern is self-matching; imports remain exempt from the
numba-globals candidate set per the plan text (not the research's alternative
suggestion); a pinned package missing entirely from `uv.lock` is treated as a
pin-check failure.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical functionality] check_latest_ban.py scans `/`-join BinOp nodes, not just Call arguments**
- **Found during:** Task 2, writing `check_latest_ban.py`
- **Issue:** The plan's `<behavior>` section describes scanning "`ast.Call` nodes whose function resolves to a path-construction call... f-string / `str.format` building a path... detect via `ast.JoinedStr` and `ast.Constant` string arguments to ANY call" -- strictly Call-node-scoped. But the plan's `<objective>`/context section (and the orchestrator's `what_correct_looks_like`) explicitly names "`/`-joins" (`Path("data") / "latest" / "file.parquet"`) as a case the check must catch. A `/`-join is an `ast.BinOp`/`ast.Div`, not a `Call`, so the literal `<behavior>` text alone would silently miss the most idiomatic pathlib pattern.
- **Fix:** Extended `scan_source` to also walk `ast.BinOp` nodes with `ast.Div` and check both operands for a "latest" segment, alongside the Call-argument scan.
- **Files modified:** `mvp/tools/check_latest_ban.py`
- **Verification:** `test_latest_path_literal_in_slash_join_is_flagged` (new test) passes; all 7 tests in `test_latest_ban.py` green.
- **Committed in:** `2559940` (Task 2 GREEN)

**2. [Rule 1 - Bug] check_ms_to_ns_site.py must self-exclude its own source file**
- **Found during:** Task 3, first `main()` run against the real repo
- **Issue:** The mandated regex `1_000_000([^_0-9]|$)`, written as a raw-string literal inside `check_ms_to_ns_site.py` itself (`re.compile(r"1_000_000([^_0-9]|$)")`), is a syntactic match for its own pattern -- the `(` immediately following `1_000_000` in that source line is a non-digit, non-underscore character. Without exclusion, `main()` against the real repo reports two sites (the legitimate one plus its own source line) and exits 1, permanently red.
- **Fix:** Added a narrow self-exclusion in `_excluded()`: `path.resolve() == _SELF_PATH` (where `_SELF_PATH = Path(__file__).resolve()`), checked before the general marker-substring exclusion. This excludes only this one file, not a blanket `tools/`-directory exemption -- a second real ms-to-ns site elsewhere in `tools/` would still be caught.
- **Files modified:** `mvp/tools/check_ms_to_ns_site.py`
- **Verification:** `uv run --directory mvp python -m tools.check_ms_to_ns_site` (via `./.venv/bin/python3 -m tools.check_ms_to_ns_site`) exits 0, reporting exactly the one legitimate site; `test_find_ms_to_ns_sites_real_repo_has_exactly_one_at_parse_py` passes.
- **Committed in:** `5773b49` (Task 3 GREEN)

---

**Total deviations:** 2 auto-fixed (1 Rule 2 - missing critical functionality per the plan's own stated objective; 1 Rule 1 - bug fix required for the plan's own acceptance criteria, "each script... exits 0" against the real repo, to hold at all)
**Impact on plan:** Both fixes were necessary for the plan's stated `must_haves`/`done` criteria to be achievable as written. No scope creep beyond the plan's five named files plus their four test files (Task 2 covers two tool files under one task, per the plan's own task boundary).

## Issues Encountered

None beyond the two deviations above -- both were the expected outcome of following the plan's own advisory notes (`02-CONTEXT.md`/`02-RESEARCH.md` Pitfall 3 for the `/`-join case; the plan's own interface note calling out `PKG_ROOT`-relative path resolution discipline made the self-exclusion fix mechanical once the trap surfaced empirically on the first real-repo run).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All five check scripts are importable, directly-testable modules
  (`scan_source`/`assert_pins`/`find_ms_to_ns_sites` + `main() -> int`) ready
  for Plan 04 to wire into `.pre-commit-config.yaml` and
  `.github/workflows/ci.yml` with the identical invocation string in both
  places, per this plan's stated purpose.
- Plan 04's red-proof of `check_pin_versions.py` should use `--lock-path`
  against a tampered temporary copy, never the real `mvp/uv.lock` and never
  `uv run` (which could trigger an implicit re-sync of the shared venv under
  the running capture daemon, PID 10771, or under Plan 03's concurrent
  `mlflow-skinny` dependency addition).
- File naming note for Plan 04: the plan's own frontmatter/tasks consistently
  name this file `check_ms_to_ns_site.py` (singular); an earlier orchestrator
  prompt referenced a plural `check_ms_to_ns_sites.py` -- the singular,
  plan-authoritative name is what was built and is what Plan 04 must wire.
- `uv lock --check` (part of Task 3's plan `<verify>` block) was intentionally
  **not run** by this executor: Plan 03 was concurrently mid-`uv add
  mlflow-skinny` on the same checkout, and running `uv lock`/`uv sync` risked
  racing that operation or resyncing the shared venv under the live capture
  daemon. `mvp/uv.lock`'s numba/numpy/llvmlite pins and pandas-absence were
  verified directly via `assert_pins()` against the real, on-disk
  `mvp/uv.lock` instead (`test_assert_pins_passes_against_real_uv_lock`,
  `test_pins_main_default_lock_path_is_real_repo` -- both green). Plan 04
  should run `uv lock --check` as its own standalone CI/pre-commit step once
  no concurrent `uv` operation is in flight.
- No blockers. The capture daemon (Run G, PID 10771) was confirmed alive
  before, during, and after this plan's execution (`ps -p 10771` succeeded
  throughout); no `uv sync`/`uv add`/`uv lock` was run, and no file under
  `/Volumes/ProjectsSSD/aihedgefund/capture` was touched.

---
*Phase: 02-stage-0-living-spec-ci-guardrails-tracking*
*Completed: 2026-09-13*

## Self-Check: PASSED

All 9 files claimed as created verified present on disk (5 `mvp/tools/check_*.py`
scripts, 4 `mvp/tests/spec/test_*.py` test files). All 6 commit hashes (`dc5fc67`,
`14f9590`, `0a29c37`, `2559940`, `8d6da04`, `5773b49`) verified present in git
history. No missing items.
