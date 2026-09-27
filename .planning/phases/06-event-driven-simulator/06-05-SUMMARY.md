---
phase: 06-event-driven-simulator
plan: 05
subsystem: sim
tags: [numba, njit, determinism, sha256, subprocess, refuse-on-null]

# Dependency graph
requires:
  - phase: 06-event-driven-simulator
    provides: "Plan 06-03's sim/kernel.py (run_sim_checked, @njit(cache=True)), sim/reference.py (run_reference_sim), sim/outputs.py (SimResult, the fill_count-tail hazard), sim/arrays.py (the null_count()==0 boundary) -- this plan proves runtime guarantees ON TOP OF that already-built kernel, adding no new production code"
provides:
  - "mvp/tests/sim/test_determinism.py: two same-process run_sim_checked calls plus one fresh-subprocess spawn, sha256-compared over trade-log columns (sliced [:fill_count]) + equity_scaled + counters, in one fixed declared order (D-06-14, 06-RESEARCH.md Q8's hash recommendation)"
  - "A deterministic sentinel-poked-tail test proving the fill_count-slicing hazard AND its fix in one always-reproducible run, never relying on np.empty's uninitialised memory happening to differ across real runs"
  - "mvp/tests/sim/test_null_refusal.py: a non-finite pred stops run_sim_checked at that exact row, naming the status, row, and values, proven by inspecting the carried-in state array after the raise (D-06-15)"
  - "A dropped-row (never NaN'd) caller-side pre-filter test proving the kernel places no adjacency assumption on etime spacing, resolving 06-RESEARCH.md Q10's forced reading of D-06-09's 'excluded' wording"
affects: [06-06-oracle-suite, 06-07-phase-close]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Subprocess child-script logic extracted via inspect.getsource from the parent test module's own functions (never hand-retyped) -- guarantees the child process runs byte-identical hashing/generator logic to the parent by construction, closing the drift risk a manual duplicate-and-paste would carry, while still not importing across the mvp/tests/sim/ no-__init__.py boundary (D-06-02)"
    - "A deterministic-sentinel mutation-proof pattern for an uninitialised-tail hazard: write two DIFFERENT known values into the tail only, never rely on np.empty's actual garbage bytes (which are frequently zero-filled on a fresh OS page, an outcome this session's own subprocess test--run under the unsliced-hash mutation--reproduced directly: the tail garbage happened to agree across two fresh subprocess allocations, exactly the false-negative absolute rule 3 warns against)"
    - "Reading the carried-in state array after a SimStatusError raise, rather than only asserting on the exception message, to prove a refusal stopped AT the offending row rather than merely 'eventually erred' -- state_i64 is written back on every code path per sim/kernel.py's own STATE CONTRACT, including the error path"

key-files:
  created:
    - mvp/tests/sim/test_determinism.py
    - mvp/tests/sim/test_null_refusal.py
  modified: []

key-decisions:
  - "The child subprocess script's generator + hashing logic is built via `inspect.getsource` over this test module's own top-level functions (_price_at_ticks, _build_fixed_sequence, _hash_arrays, _hash_result), concatenated into a standalone script string, rather than hand-retyped as the plan's action text literally suggested ('duplicated inline in the spawned string'). This satisfies the same intent (the child cannot import the test module, mvp/tests/sim/ has no __init__.py) with a stronger guarantee: the two copies cannot silently drift apart, because there is only ever one copy of the source text. `_hash_result`'s five-column tuple is hardcoded inside its own body (not read from the module-level `_TRADE_LOG_COLUMNS` constant) specifically so the extracted source is self-contained and does not reference a name the child script never defines."
  - "The fixed 500-row (determinism) and 20-row (null-refusal drop test) synthetic sequences use a closed-form deterministic formula (t += 100 + i%7, bid oscillating via (i*37)%401, etc.) rather than a seeded RNG (numpy's default_rng or Python's random). A seeded RNG's bit-for-bit output across a fresh interpreter process is an assumption this plan does not need to make when a plain formula makes the question moot -- the two same-process runs and the subprocess run all compute the identical array from the identical arithmetic, not from 'the same seed happens to reproduce.'"
  - "The refuse-on-null row-5 test passes `state=new_state()` explicitly (rather than letting `run_sim_checked` default one) specifically so the state array can be inspected AFTER the SimStatusError is raised -- proving fill_count==2/flip_count==1/error_row==5 (only rows 0-4's two trades were ever written) rather than merely asserting the exception fired somewhere."
  - "No production code changes were made for Task 2, as the plan itself anticipated: `STATUS_NON_FINITE_PRED` (sim/kernel.py) and the null_count()==0 assertion (sim/arrays.py, already tested by Plan 06-03's test_arrays_refuse_a_nullable_bid_or_ask_column) already implement both halves of D-06-15. The mutation check (disabling the isfinite guard) proved the refusal path is load-bearing rather than merely present."

patterns-established:
  - "inspect.getsource-based script extraction for subprocess-determinism tests: any future cross-process proof in this codebase can reuse this shape instead of hand-duplicating logic into a spawned script string"

requirements-completed: []

# Metrics
duration: ~35min (commit timestamps 23:54:26 to 23:58:43 PDT for the two task commits; total session time including required-reading of 06-CONTEXT.md/06-RESEARCH.md Q8/Q10/the four sim/ source files/the subprocess precedents before any test code was written was longer, not separately timestamped, matching the same honest process-gap note 06-03/06-04-SUMMARY.md both record)
completed: 2026-09-24
---

# Phase 6 Plan 5: Cross-process determinism and refuse-on-null Summary

**Two sha256-hashed same-process `run_sim_checked` runs plus one fresh-subprocess spawn (a distinct `NUMBA_CACHE_DIR`, `inspect.getsource`-extracted child logic) prove D-06-14's bit-identical guarantee, while a non-finite `pred` is proven to stop the kernel at the exact offending row (via post-raise state inspection, not just the exception message) and a caller-side drop of 5 non-adjacent rows is proven to run correctly with real `etime` gaps, closing D-06-15 and 06-RESEARCH.md Q10's "dropped, never NaN'd" reading -- with zero production code changes, since Plan 06-03's kernel already implemented both guarantees correctly.**

## Performance

- **Duration:** ~35 min from Task 1 commit to Task 2 commit (git commit timestamps: 23:54:26 to 23:58:43 PDT); required-reading time before the first test line was written is not separately isolated, per the same honest caveat prior plan summaries in this phase record
- **Started:** 2026-09-23 (session start, required-reading phase)
- **Completed:** 2026-09-23T23:58:43-07:00 (Task 2 commit)
- **Tasks:** 2
- **Files modified:** 2 (both created, no existing files touched)

## Accomplishments

- **D-06-14 proven, not assumed.** `test_two_same_process_runs_hash_identical` runs the same 500-row fixed (non-RNG) input through `run_sim_checked` twice with fresh state, hashes the sliced trade log + equity curve + counters via `hashlib.sha256(dtype.str + shape-bytes + tobytes())` per array in one fixed declared order (06-RESEARCH.md Q8's own recommendation), and asserts equality after confirming `fill_count > 0` (anti-vacuity: an inert run would hash two empty logs identically no matter what).
- **The subprocess half -- the one D-06-14's own text calls "the hard half" -- is real.** `test_subprocess_run_hashes_identical_to_the_parent_process` spawns `sys.executable -c <script>` with a FRESH, distinct `NUMBA_CACHE_DIR` (a pytest `tmp_path` subdirectory) and `PYTHONPATH` set explicitly, mirroring `tests/capture/test_ws_client_raw_archive.py`'s exact subprocess shape. The child script's own generator/hashing logic is extracted via `inspect.getsource` from this test file's own functions -- never hand-retyped -- so parent and child are provably running the identical source text, not two copies that happened to agree today. The child's stdout is validated as a well-formed 64-hex-char digest BEFORE being compared to the parent's hash (a crashed child with empty stdout cannot vacuously "match").
- **The fill_count-tail hazard is proven AND fixed in one deterministic test, never relying on real-run flakiness.** `test_unsliced_hash_is_sensitive_to_tail_garbage_sliced_hash_is_not` writes two DIFFERENT literal sentinels (`111_111`/`222_222`) into two copies' unfilled tails only, proves the unsliced hash diverges and the sliced hash (via `_hash_result`'s `[:fill_count]`) agrees -- deterministically, every run.
- **The mutation check caught a real cross-process hazard this plan's own absolute rules warned about.** Removing the `[:fill_count]` slice from `_hash_result` made `test_unsliced_hash_is_sensitive_to_tail_garbage_sliced_hash_is_not` fail exactly as predicted, on its own sliced-identical assertion -- but it ALSO made `test_two_same_process_runs_hash_identical` fail (two same-process `np.empty` tail allocations differed), while `test_subprocess_run_hashes_identical_to_the_parent_process` happened to still PASS under the same mutation, because that run's fresh-page tail garbage was zero-filled identically in both processes. This is a live demonstration of this plan's own absolute rule 2 ("never rely on `np.empty` garbage being non-deterministic... it is often zeroed on a fresh page") -- the sentinel-based test is the one immune to it; the other two are incidental collateral of the same mutation, not separately engineered proofs of the hazard.
- **D-06-15 proven with row-level precision, not just "an exception happened somewhere."** `test_non_finite_pred_stops_the_run_at_that_row_not_after` builds a 10-row sequence with real trades before AND after the NaN (rows 0/2 trade, rows 6-9 would each independently trigger a flip if ever reached), passes its own `state` array explicitly, and after the raise asserts `error_row==5`, `fill_count==2`, `flip_count==1` directly from that carried-in state -- proving the loop `break`s at row 5 rather than merely eventually raising.
- **The dropped-row convention is pinned against the reference twin, on a genuinely non-contiguous sequence.** `test_dropped_null_rows_produce_a_correct_non_contiguous_run` removes 5 non-adjacent rows (indices 2/5/9/13/17) from a 20-row sequence entirely (never zeroed, never NaN'd), asserts the resulting `etime` has real, non-uniform gaps (`len(set(gaps)) > 1`, ruling out an accidental constant stride), and proves `run_sim_checked` and `run_reference_sim` agree bitwise on the trade log, equity curve, and counters over the same filtered 15-row sequence.
- **Both mutation checks observed genuinely failing, then restored to the exact pre-mutation file hash, then re-verified green.** Task 1: removing `_hash_result`'s slice -- `shasum` before `f8367451...`, after `f7bdd4a4...`, restored to `f8367451...` exactly. Task 2: disabling `sim/kernel.py`'s `if not np.isfinite(p):` guard -- `shasum` before `2824e56f...`, after `2fe0004368...`, restored to `2824e56f...` exactly; the failure mode observed was `Failed: DID NOT RAISE SimStatusError` (on this machine's platform, `np.int64(round(nan))` does not itself trap -- it silently produces a value the kernel then keeps running on, which is precisely the silent-poison failure D-06-15 exists to prevent, not a crash that would have been caught some other way).
- Full `mvp/tests/sim` + `mvp/tests/features` suite green (213 tests). `ruff check .`/`ruff format --check .` both exit 0. `find features sim -name '*.nb[ci]'` (run from `mvp/`) empty before both commits. `mvp/tests/sim/` confirmed to still have no `__init__.py` (D-06-02) before each commit.

## Task Commits

1. **Task 1: Bit-identical across two same-process runs and one fresh subprocess** - `43e499b` (test)
2. **Task 2: Refuse-on-null and the dropped-row convention** - `d8c24d4` (test)

**Plan metadata:** pending (this commit)

_Note: both tasks are `type="auto" tdd="true"`. RED was genuinely observed for both in this session's own tool transcript (see "TDD Gate Compliance" below), but per the same hook-forced deviation `06-01`/`06-03`/`06-04-SUMMARY.md` all record, it could not be its own commit -- the pre-commit hook runs the full suite with no `--no-verify` escape hatch, and a RED commit is by definition a failing-suite commit._

## Files Created/Modified

- `mvp/tests/sim/test_determinism.py` - 3 tests: two-same-process hash equality, subprocess hash equality (child logic via `inspect.getsource`), and the deterministic sentinel-tail hazard/fix proof; plus the shared `_hash_arrays`/`_hash_result`/`_build_fixed_sequence`/`_child_script` helpers
- `mvp/tests/sim/test_null_refusal.py` - 2 tests: the row-5 non-finite-pred refusal (state-inspected, not just exception-message-inspected) and the 5-non-adjacent-rows-dropped, gap-proven, reference-twin-matched run

## Decisions Made

See `key-decisions` in the frontmatter for the four substantive ones: the `inspect.getsource` extraction mechanism for the subprocess child script (and why `_hash_result`'s column tuple is hardcoded rather than referencing a module constant), the closed-form-deterministic (non-RNG) synthetic sequence generator, passing `state=new_state()` explicitly to inspect post-raise state, and why no production code changed for Task 2.

## TDD Gate Compliance

Both tasks are `tdd="true"`. RED was genuinely observed for both, in this session's own tool transcript, not merely asserted after the fact:

- **Task 1:** with `_hash_result`'s body temporarily replaced by `raise NotImplementedError(...)`, all three tests failed (`3 failed`) -- confirmed via `shasum` that the file hash changed from the eventual committed hash (`f8367451...` -> `3ac434f9...`) before this RED run, then restored to `f8367451...` exactly before re-running green.
- **Task 2:** both tests passed on the first run against the ALREADY-COMMITTED Plan 06-03 implementation (no RED from a missing feature -- the plan's own action text anticipated this: "No production code changes are expected"). Genuine RED was instead produced by the plan's own MUTATION CHECK step (disabling `sim/kernel.py`'s `if not np.isfinite(p):` guard), which is the mechanism this plan uses to prove the refusal test is not vacuous -- `test_non_finite_pred_stops_the_run_at_that_row_not_after` failed with `Failed: DID NOT RAISE SimStatusError` under that mutation, confirmed via `shasum` (`2824e56f...` -> `2fe0004368...`, restored to `2824e56f...` exactly).

Neither RED could be its own commit: the pre-commit hook runs the full suite with no `--no-verify` escape hatch, and a RED commit is by definition a failing-suite commit -- the same hook-forced deviation `06-01`/`06-03`/`06-04-SUMMARY.md` all record and recover from the same way. Each task therefore has one `test(...)` commit covering RED-observed-then-GREEN.

## Deviations from Plan

None - plan executed exactly as written. Both tasks' `<action>` sections anticipated their own likely path (Task 1: build the hashing helper and the subprocess mechanism; Task 2: "no production code changes are expected") and both were followed exactly, including both plan-specified mutation checks.

## Issues Encountered

- **The subprocess test's own hash happened to survive the Task 1 mutation, while the two other tests did not.** Not a bug in the test -- documented above under "Accomplishments" and matches this plan's own absolute rule 2's warning almost exactly (a fresh-page `np.empty` tail is often zero-filled, hence often coincidentally equal across two independent allocations). The plan-mandated sentinel test is the one immune to this by construction; this observation is transcribed here as evidence the plan's own warning was correct on this machine, not merely theoretical.
- **The plan's Task 2 mutation instruction (`if pred[i] != pred[i]` -> `if False`) named a self-NaN-comparison idiom that is not the line actually in `sim/kernel.py`** (the real guard is `if not np.isfinite(p):`, D-06-15/Plan 06-03's own implementation choice). Adapted to disable the ACTUAL guard line rather than a line that does not exist in this codebase, preserving the mutation check's intent (disable the non-finite refusal) exactly. Observed failure mode: `DID NOT RAISE SimStatusError` rather than a downstream crash -- `np.int64(round(nan))` on this platform silently produces a value instead of trapping, so the run simply continues past row 5 undetected until the loop ends normally, exactly the silent-poison scenario D-06-15 is written to prevent.

## User Setup Required

None - no external service configuration required.

## Known Stubs

None. Every array in every test is built from either real `run_sim_checked`/`run_reference_sim` output or a hand-constructed, fully-specified synthetic fixture; no hardcoded empty/placeholder value flows into any assertion.

## Threat Flags

None. Both trust boundaries this plan's `<threat_model>` names (T-06-14: cache poisoning/module-level mutation across processes; T-06-15: a NaN silently poisoning the P&L) are exactly what this plan's two test files address, with real mutations proving each mitigation fires. No new, unlisted security-relevant surface was introduced -- this plan adds tests only, no production code.

## What Was NOT Done

- **No SIM requirement was marked complete.** This plan's frontmatter names `SIM-03`, but per this plan's own absolute rules, `requirements.mark-complete` was NOT run -- Plan 06-07 closes the phase and is the correct place for that.
- **D-06-15's "naming the column and the row count" wording is satisfied by TWO separate, already-existing sites, not one new mechanism -- documented here rather than built, per this plan's own explicit prohibition on a second refusal mechanism.** The COLUMN-naming half (which column is null) is `sim/arrays.py:sim_arrays`'s `null_count() == 0` assertion, already tested by Plan 06-03's `test_arrays_refuse_a_nullable_bid_or_ask_column` (unchanged, untouched by this plan). The ROW-naming half (which row's `pred` is non-finite, and that row's own values) is `sim/kernel.py`'s `STATUS_NON_FINITE_PRED`/`_status_detail`, proven by this plan's Task 2 with a real mutation. Neither this plan's task 2 action text nor its own absolute rules called for unifying these into one new function, and doing so would have duplicated `arrays.py`'s existing, already-tested assertion.
- **No new production code was written or modified.** Both tasks' own action text anticipated this as the likely (and, for Task 2, expected-by-default) outcome; both mutation checks confirm the existing guards are load-bearing rather than merely present, which is the strongest form of "no code needed" this plan could offer.
- **No perfect-foresight or zero-prediction oracle was run against a real lake day.** This plan's fixtures are all hand-built or closed-form-deterministic in-memory arrays (`tmp_path`-free, no polars frame, no accessor call) -- wiring a real, accessor-gated frame through `sim_arrays` with a real label-derived `pred` (and exercising the drop-vs-refuse convention against REAL null labels, e.g. 2026-09-14's measured 38,565-null day) is Plan 06-06's job (D-06-09's oracle suite), not this plan's.
- **`mvp/spec.md` was not touched.** No new rule or convention needed documenting there; the "Simulator" section 06-CONTEXT.md flags as a possible future addition remains Plan 06-07's call.
- **The `NUMBA_CACHE_DIR`s this plan's subprocess test creates are pytest `tmp_path` subdirectories, cleaned up by pytest's own retention policy, not manually swept.** No stray cache artifact was observed under `mvp/features` or `mvp/sim` at any point (checked before both commits), so no manual cleanup step was needed.

## Next Phase Readiness

- Both of D-06-14's and D-06-15's runtime guarantees are now proven with real mutations, not merely asserted to exist -- Plan 06-06's oracle suite can build on `run_sim_checked`/`run_reference_sim` without re-deriving either proof.
- The `inspect.getsource`-based subprocess-determinism pattern (extract the parent test's own functions into a spawned child script, rather than hand-duplicating them) is available for reuse by any future cross-process proof in this codebase.
- The "excluded means dropped, never NaN'd" convention (06-RESEARCH.md Q10) is now pinned by a passing test against the reference twin, ready for Plan 06-06's perfect-foresight oracle to apply against a REAL day with real null labels (2026-09-14, per Q10's own measurement).
- No blockers identified for 06-06 or 06-07.

---
*Phase: 06-event-driven-simulator*
*Completed: 2026-09-24*

## Self-Check: PASSED

- `mvp/tests/sim/test_determinism.py` -- FOUND on disk, 3 tests, all pass (`./.venv/bin/pytest tests/sim/test_determinism.py -q` -> `3 passed`)
- `mvp/tests/sim/test_null_refusal.py` -- FOUND on disk, 2 tests, all pass (`./.venv/bin/pytest tests/sim/test_null_refusal.py -q` -> `2 passed`)
- Commit `43e499b` -- FOUND in `git log --oneline`
- Commit `d8c24d4` -- FOUND in `git log --oneline`
- `mvp/tests/sim/` has no `__init__.py` -- confirmed via `ls`
- `find features sim -name '*.nb[ci]'` (from `mvp/`) -- empty
- Full `mvp/tests/sim` + `mvp/tests/features` suite -- 213 passed
- `ruff check .` / `ruff format --check .` -- both exit 0
