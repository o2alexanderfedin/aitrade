---
phase: 05-fold-harness-overfitting-controls
plan: 03
subsystem: ml-pipeline
tags: [mlflow, selection-bias-budget, leakage-guard, harness, sqlite]

# Dependency graph
requires:
  - phase: 05-fold-harness-overfitting-controls (Plan 01)
    provides: harness/budget.py's look_count/record_look, harness/accessor.py's
      ordered-gates materialize()
  - phase: 05-fold-harness-overfitting-controls (Plan 02)
    provides: harness/segments.py's issue_segment_manifest (5seg + compressed_3seg,
      D-05-02 geometric refusals, derived purge/embargo fields)
provides:
  - "harness.budget.record_look: hard refusal (BudgetExhaustedError) once a
    segment's budget_allowance is spent, naming the segment, looks spent,
    allowance, and the remedy"
  - "harness.budget.exhausted_segments: bulk exhaustion query over a list of
    already-issued manifests"
  - "harness.segments.issue_segment_manifest: UNCONDITIONAL issuance-time
    self-discovery + overlap refusal against every exhausted val/oof_block
    window found under registry_root/segments/ (required tracking_root
    keyword, no opt-out parameter)"
  - "harness.negative_log: config_fingerprint/record_negative_result/
    query_negative_results/warn_if_already_negative -- the MLflow-backed
    negative-result log (D-05-22)"
  - "tools/harness_negative_log_cli.py: manual --tracking-root/--fingerprint
    CLI over the negative-result log"
affects: [07-eval-close-out]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "MLflow-first durable counter/log, query FIRST, exception propagates
      unmodified, canonical-root + initialised-store guards duplicated
      per-module (data.lockbox's shape, now in harness.budget AND
      harness.negative_log)"
    - "Fingerprint-not-name: a negative-result config is identified by
      compute_manifest_id(config), the same canonicalizer segment manifests
      use, never a bespoke hash"

key-files:
  created:
    - mvp/harness/negative_log.py
    - mvp/tools/harness_negative_log_cli.py
    - mvp/tests/harness/test_negative_log.py
  modified:
    - mvp/harness/budget.py
    - mvp/harness/segments.py
    - mvp/harness/accessor.py
    - mvp/tests/harness/test_budget.py
    - mvp/tests/harness/test_segments.py
    - mvp/tests/harness/test_accessor.py
    - mvp/tests/harness/test_kfold.py

key-decisions:
  - "record_look's budget_allowance is a required keyword with no default --
    harness.accessor.materialize passes the segment manifest's own
    budget_allowance field, closing the gap between 'a look is counted' (P1)
    and 'a look can be refused' (this plan)"
  - "issue_segment_manifest's overlap refusal is unconditional: no
    existing_manifests parameter exists, tracking_root has no default, and
    self-discovery globs registry_root/segments/ itself -- a caller cannot
    skip the check by omitting an argument (checker iteration 1 blocker 4)"
  - "Zero discovered manifests never touch MLflow: exhausted_segments([])
    iterates nothing, so a tmp_path-fresh registry never has to point at an
    initialised store to issue its first manifest"

requirements-completed: []

# Metrics
duration: ~11min (commit-to-commit span between the two task commits;
  research/reading preceded the first commit and is not included)
completed: 2026-09-22
---

# Phase 5 Plan 3: Budget Exhaustion, Overlap Refusal, Negative-Result Log Summary

**A validation window that has already been looked at the allowed number of times now refuses the next look by name, and a new fold layout that would reuse that same window is refused at the moment someone tries to issue it -- both refusals are unconditional, and a separate MLflow-backed log records every failed configuration by content fingerprint so a later re-run of the same dead end gets a loud warning instead of silence.**

## Performance

- **Duration:** ~11 min between the Task 1 and Task 2 commits (research and file-reading before the first commit not included in this figure)
- **Completed:** 2026-09-22
- **Tasks:** 2/2
- **Files modified:** 10 (3 created, 7 modified)

## Accomplishments
- `harness.budget.record_look` now refuses a look once a segment's `budget_allowance` is spent, and `harness.budget.exhausted_segments` answers the same question in bulk over a list of manifests
- `harness.segments.issue_segment_manifest` self-discovers every existing segment manifest and unconditionally refuses a new `val`/`oof_block` window that overlaps an already-exhausted one -- no opt-out parameter exists
- `harness/negative_log.py` gives every failed configuration a durable, queryable MLflow record keyed by `compute_manifest_id(config)`, with a CLI to list and filter it
- Re-running a known-negative fingerprint warns (logged) and proceeds -- it is never refused, since `code_hash`/`data_hash` on the new run are what distinguish a legitimate re-run from a repeated dead end

## Task Commits

1. **Task 1: budget exhaustion, and the UNCONDITIONAL issuance-time overlap refusal** - `aa2e36f` (feat)
2. **Task 2: negative-result log, query function, and CLI** - `5eb8fc5` (feat)

**Plan metadata:** (this commit, following this Summary)

_Note: prior 05-* plans in this phase fold RED+GREEN into one `feat(...)` commit per task rather than separate `test(...)`/`feat(...)` commits; this plan matched that established convention -- see "TDD Gate Compliance" below._

## Files Created/Modified
- `mvp/harness/budget.py` - `BudgetExhaustedError`, `exhausted_segments()`, `record_look(..., budget_allowance)` now refuses once spent >= allowance
- `mvp/harness/segments.py` - `issue_segment_manifest(..., tracking_root=<required>)` self-discovers `registry_root/segments/*.json` and refuses an overlapping candidate against any exhausted window
- `mvp/harness/accessor.py` - `materialize()` now passes the segment manifest's own `budget_allowance` to `record_look` (deviation, see below)
- `mvp/harness/negative_log.py` - `config_fingerprint`, `record_negative_result`, `query_negative_results`, `warn_if_already_negative`
- `mvp/tools/harness_negative_log_cli.py` - `--tracking-root`/`--fingerprint` CLI over `query_negative_results`
- `mvp/tests/harness/test_budget.py` - exhaustion + `exhausted_segments` tests; existing `record_look` calls updated with `budget_allowance`
- `mvp/tests/harness/test_segments.py` - self-discovery, overlap refusal, required-keyword, no-opt-out, and no-special-treatment tests; existing `_build_fixture`/direct `issue_segment_manifest` calls updated with `tracking_root`
- `mvp/tests/harness/test_accessor.py`, `mvp/tests/harness/test_kfold.py` - `_build_fixture`/`_build_compressed_3seg_fixture` helpers updated with the new required `tracking_root` parameter (deviation, see below)
- `mvp/tests/harness/test_negative_log.py` - all 6 planned behaviors plus 2 extra tests (over-long `reason` refusal, silent no-op for an unrecorded fingerprint)

## Decisions Made
- `record_look`'s exhaustion check queries `look_count` BEFORE `start_tracked_run`, so a refused look never creates a run -- the count after a refusal stays exactly at the allowance, never one over
- The overlap check runs AFTER `_validate_segments`/`_derive_purge_embargo_fields` (geometry and starvation checks fire first, on the cheapest signal), immediately before the manifest body is written to disk
- `negative_log.py` duplicates `harness.budget`'s canonical-root-pinning and uninitialised-store guard helpers verbatim (same 05-PATTERNS.md norm the rest of this phase follows: small, stable, behaviorally-real helpers are copied, not cross-imported)
- `record_negative_result` refuses a `reason` longer than MLflow's own tag-value cap before writing, rather than letting MLflow silently truncate it (the same WR-01 failure mode `tracking.mlflow_utils` already documents)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `harness/accessor.py` needed to pass `budget_allowance` to `record_look`**
- **Found during:** Task 1, before writing any test
- **Issue:** The plan's action text gives `record_look` a new required `budget_allowance` keyword but names no caller update; `harness/accessor.py` (not in this plan's `files_modified`) is `record_look`'s only real caller and would otherwise raise `TypeError` on every `val`/`oof_block` materialization
- **Fix:** `materialize()`'s look-recording gate now passes `manifest["budget_allowance"]`; one doc line added to `materialize`'s own docstring noting the new refusal
- **Files modified:** `mvp/harness/accessor.py`
- **Verification:** `tests/harness/test_accessor.py`'s full suite (including the gate-order monkeypatch test) still passes
- **Committed in:** `aa2e36f` (Task 1 commit)

**2. [Rule 3 - Blocking] `test_accessor.py`/`test_kfold.py`'s own manifest-issuance helpers needed the new `tracking_root` keyword**
- **Found during:** Task 1, running the full suite before the first commit
- **Issue:** `issue_segment_manifest`'s new required `tracking_root` keyword breaks `test_accessor.py::_build_fixture` and `test_kfold.py::_build_compressed_3seg_fixture`, neither named in this plan's `files_modified` (only `test_segments.py` was)
- **Fix:** Both helpers gained a `tracking_root` parameter, threaded through to `issue_segment_manifest`; every call site (5 in `test_accessor.py`, 3 in `test_kfold.py`) updated to pass the test's own `tracking_root` fixture
- **Files modified:** `mvp/tests/harness/test_accessor.py`, `mvp/tests/harness/test_kfold.py`
- **Verification:** `./.venv/bin/pytest tests/harness/ -q` green (65 tests)
- **Committed in:** `aa2e36f` (Task 1 commit)

**3. [Rule 1 - Bug] `test_materializing_an_oof_block_counts_as_a_look` broke once exhaustion became real**
- **Found during:** Task 1, running the full suite before the first commit
- **Issue:** That test materializes `oof_block_0` TWICE against a fixture whose `budget_allowance` defaulted to 1 -- the second `materialize()` call now legitimately raises `BudgetExhaustedError`, which is correct new behavior, not a bug in the exhaustion logic
- **Fix:** That one test's fixture call now passes `budget_allowance=2`, with a comment explaining why
- **Files modified:** `mvp/tests/harness/test_kfold.py`
- **Verification:** The test still proves what it always proved (look_count reaches 2 after two materializations); a comment ties the allowance choice to the new exhaustion enforcement
- **Committed in:** `aa2e36f` (Task 1 commit)

---

**Total deviations:** 3 auto-fixed (2 blocking, 1 bug/test-adaptation)
**Impact on plan:** All three were forced by the signature/behavior change this plan's own two tasks specify; none add scope beyond making the change actually compile and pass. No architectural changes were needed (Rule 4 never triggered).

## Issues Encountered
- The first `config_fingerprint` mutation attempt (re-hashing `json.dumps(config)` with plain sha256 instead of calling `compute_manifest_id`) turned out to be accidentally EQUIVALENT for every test fixture, because none of them carry a `manifest_id` key for `canonicalize_manifest` to strip -- the mutated test passed instead of failing, which would have been a false "mutation caught" claim. Replaced with an md5-based mutation, which genuinely diverges from `compute_manifest_id`'s sha256 output and correctly failed `test_config_fingerprint_matches_compute_manifest_id` before being restored. Recorded here as a near-miss: a mutation that "looks different" in the diff is not the same as one that changes the OBSERVED behavior against the test's own fixtures.
- Two overlap tests in `test_segments.py` initially issued a second manifest against the same `lake_root`/`registry_root` on the same calendar date, hitting `features.tier`'s write-once-per-date refusal (`FileExistsError`) -- unrelated to the segment-overlap logic under test. Fixed by adding a `date` override to `_build_fixture` and using a distinct date (`2026-09-14`) for the second issuance in both tests.

## TDD Gate Compliance
Every prior plan in this phase (05-01 through 05-06) folds RED and GREEN into a single `feat(...)` commit per task rather than separate `test(...)`/`feat(...)` commits (confirmed via `git log --oneline | grep -E "\(05-0[1-6]"` before starting -- no `test(05-...)` commits exist anywhere in this phase's history). This plan matched that established convention: for both tasks, the named tests were written and run RED (confirmed failing with the exact expected error, e.g. `TypeError: record_look() missing 1 required keyword-only argument: 'budget_allowance'` before Task 1's implementation existed) before the implementation was written, then committed together once GREEN. No separate `test(...)` commit exists, matching prior plans' own pattern rather than deviating from it.

## Known Stubs
None. Both tasks' artifacts (`exhausted_segments`, the overlap refusal, and the full negative-result log + CLI) are wired end to end and exercised by real tests against real `tmp_path` MLflow stores -- no hardcoded empty return, no placeholder string, no unwired component.

## Threat Flags
None beyond what this plan's own `<threat_model>` already named (T-05-17, T-05-21, both `accept`-dispositioned with stated rationale in the plan itself). No new network endpoint, auth path, or schema change at a trust boundary was introduced.

## User Setup Required
None - no external service configuration required. Everything in this plan runs against a local SQLite-backed MLflow store; the CLI's default `--tracking-root` is the project's existing canonical store convention (`$AIHF_MLFLOW_TRACKING_ROOT` or the built-in default), unchanged by this plan.

## Not Done / Explicitly Out of Scope
- **No EVAL requirement is marked complete by this plan.** Per this plan's own `<absolute_rules>`, EVAL-02/03/04 stay open in REQUIREMENTS.md; Plan 07 closes the phase and is the one that marks them.
- No real, git-committed segment manifest was written (`mvp/data/lake_registry/segments/` still does not exist after this plan) -- every test uses a `tmp_path` registry and tracking root, per this plan's constraint 3.
- `harness.negative_log`'s guarantee is the same honestly-scoped one `harness.budget`/`data.lockbox` already disclose: a hard delete of the underlying MLflow SQLite row is out of scope (T-05-17, `accept`d in this plan's own threat model, not newly introduced).
- No caller in this codebase yet calls `record_negative_result`/`warn_if_already_negative` from an actual training loop -- Phase 5 has no model-training code yet (that is Phases 7-9's job); this plan ships the log and its CLI, ready to be called once a training loop exists.

## Next Phase Readiness
- `harness.budget` and `harness.segments` now enforce D-05-14 end to end (both look-time and issuance-time refusals), and `harness.negative_log` gives Phase 7-9's model-iteration loop a place to record a failed configuration and check before re-running one -- both are ready to be called by code that does not yet exist.
- No blockers identified for Plan 07 (phase close-out) or for the eventual Phase 7 training loop that will call `record_negative_result`.

---
*Phase: 05-fold-harness-overfitting-controls*
*Completed: 2026-09-22*

## Self-Check: PASSED

All 10 files referenced above (3 created, 7 modified) confirmed present on disk; both task commit hashes (`aa2e36f`, `5eb8fc5`) confirmed present in `git log --oneline --all`. No missing items.
