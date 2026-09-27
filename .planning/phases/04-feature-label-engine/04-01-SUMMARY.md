---
phase: 04-feature-label-engine
plan: 01
subsystem: feature-engine
tags: [event-stream, merge-sorted, decision-rows, total-order, ns-constants, numba-boundary, polars]

# Dependency graph
requires:
  - phase: 02-stage-0-living-spec-ci-guardrails-tracking
    provides: tools/check_ms_to_ns_site.py's ALLOWLISTED_SEC_TO_NS_SITES + cross-module constant folding; tools/check_numba_globals.py's UPPER_CASE-globals rule; spec/catalogue.py's load_labels()
  - phase: 03-data-layer-backfill-ingest-lockbox
    provides: data/store.py's load_curated/by_date_index_path (hash-verified, DQ-paused curated reads); data/ingest/curated_build.py's materialize_seq (fresh per-partition seq) and filter_na_placeholders; data/lake_paths.py's lake_root/LAKE_REGISTRY_ROOT
provides:
  - mvp/data/time_ns.py -- NS_PER_SECOND/NS_PER_DAY, TRADE_FLOW_WINDOW_NS, RET_{1S,10S,1MIN,10MIN}_NS + LABEL_HORIZON_NS, QTY_SCALE; the ONLY allowlisted seconds-to-ns site in feature/label code
  - mvp/features/event_stream.py -- EVENT_SCHEMA, SOURCE_RANK_BOOKTICKER/SOURCE_RANK_TRADE, project_bookticker, project_trade, merge_curated_streams, assert_strict_total_order, event_arrays, decision_row_index
  - The (etime, source_rank, seq) total order and the decision-row rule every later Phase 4 plan computes against
  - The stats dict (n_quote_rows, n_trade_rows, n_events, n_decision_rows, na_placeholder_excluded, unknown_side_rows) Plan 05 persists into build_stats.json and Plan 02 DQ-checks
affects: [04-feature-label-engine/04-02, 04-feature-label-engine/04-03, 04-feature-label-engine/04-05, 04-feature-label-engine/04-06, 05-fold-harness]

# Tech tracking
tech-stack:
  added: []  # polars/numpy/hypothesis already pinned; no new dependency
  patterns:
    - "One allowlisted seconds-to-ns module; every other module imports the PRE-MULTIPLIED int (check_ms_to_ns_site folds constants across modules, so `10 * NS_PER_SECOND` in a new file is a finding there)"
    - "Ordered EVENT_SCHEMA dict as the merge contract: merge_sorted raises SchemaError on any column-order or dtype drift, so projections `select` in schema order rather than `with_columns`"
    - "Runtime order assertion inside the build (assert_strict_total_order), test as tripwire -- STATE.md's guardrails-runtime-first lesson"
    - "NaN (a value), never null and never 0.0, as cross-stream payload filler: a wrong read is visible downstream and the column stays zero-copy"
    - "decision_row_index as a pure function of POSITION (numpy boolean over adjacent etimes), never a groupby, so a prefix re-derives the same decision rows"
    - "A test package must NOT shadow a real top-level package name (tests/features/__init__.py deleted; same reason tests/spec, tests/tools, tests/tracking have none)"

key-files:
  created:
    - mvp/data/time_ns.py
    - mvp/features/__init__.py
    - mvp/features/event_stream.py
    - mvp/tests/features/test_time_ns.py
    - mvp/tests/features/test_event_stream.py
    - mvp/tests/tools/test_check_ms_to_ns_site_allowlist.py
  modified:
    - mvp/data/dq/checks.py (NS_PER_SECOND/NS_PER_DAY imported from data/time_ns.py, still bound at module level and in __all__)
    - mvp/tools/check_ms_to_ns_site.py (ALLOWLISTED_SEC_TO_NS_SITES gains data/time_ns.py, and nothing else)
    - mvp/tests/conftest.py (NUMBA_CACHE_DIR pinned outside the repo before any import can reach numba)
    - mvp/tests/tools/test_check_ms_to_ns_site.py (the exact-set allowlist test grows by data/time_ns.py)
  deleted:
    - mvp/tests/features/__init__.py (created by Task 1 before features/ existed; it shadowed the real package on sys.path)

key-decisions:
  - "No cached merge tier. End-to-end read_parquet -> merge_sorted -> to_numpy is ~3.5 s for the real day here (2.70 s of it hash-verified loading), so a cached merged tier would buy under a second and cost a second write-once tier to keep immutable forever. This answers the first open question in 04-CONTEXT.md's <research_done>."
  - "The tie order is PRODUCED by argument position (merge_sorted takes from the left frame) but CHECKED from the materialized source_rank column. Swapping the two arguments therefore raises rather than silently reversing the order -- observed: mutation (a) failed with the runtime ValueError, not with a test-only assertion."
  - "project_trade returns (frame, counts) rather than the bare frame its signature line in the plan shows -- the plan's body requires the excluded count be returned alongside; the header line was abbreviated."
  - "event_arrays requires EXACT schema equality (names, order, dtypes) rather than a superset. The merged frame is exactly EVENT_SCHEMA; a later plan that carries extra columns must `.select(EVENT_SCHEMA)` before the numba boundary, which is the point at which it should be thinking about what the kernel reads anyway."
  - "trade_side is forced to 0 where side_method == 'unknown' even though trade_side.py already sets tradeSide_corrected = 0 there -- one rule stated at the boundary that consumes it, not an inherited invariant that a future change to trade_side.py could quietly break."

patterns-established:
  - "Curated-shaped hermetic fixture factories (curated_bookticker/curated_trade with the FULL curated column set) so a test exercises the same filter code path the build runs"
  - "hypothesis strategy that respects merge_sorted's precondition: a sorted etime list per stream with seq as the row index, mirroring materialize_seq"

requirements-completed: []   # FEAT-01 is PARTIAL, deliberately left unchecked
requirements-partial:
  - "FEAT-01: the decision-row half is delivered and proven on real data (state accumulates on every row, decisions emit on the last row of each etime). The numba streaming kernel and the byte-identical train/infer/sim sharing FEAT-01 also names are Plans 03 and 05; REQUIREMENTS.md stays Pending until those land."


# Metrics
duration: ~35min (this session; Task 1 was committed in a prior session that died mid-Task-2)
completed: 2026-09-19
---

# Phase 4 Plan 01: ns Constants + Merged Event Stream Summary

**Two curated partitions now become one chronological array whose order is pinned, materialized and checked at runtime — `(etime, source_rank, seq)` with bookTicker winning a tie — and exactly one module in the codebase is allowed to turn seconds into nanoseconds, so no later Phase 4 file can grow a second convention without failing CI.**

## Performance

- **Duration:** ~35 min this session (Task 2 + real-data run + summary); Task 1 landed 2026-09-17 in a session that died to an API error partway through Task 2 and left nothing behind
- **Tasks:** 2 (one commit each)
- **Tests:** 693 before this session → **702 after** (+9 in `tests/features/test_event_stream.py`; Task 1's 4 `test_time_ns.py` tests were already in the 693)
- **Files:** 6 created, 4 modified, 1 deleted

## Accomplishments

- `data/time_ns.py` holds every Phase 4 window and horizon **pre-multiplied into int64 ns** — `TRADE_FLOW_WINDOW_NS`, the four `RET_*_NS`, `LABEL_HORIZON_NS` (keys must equal the catalogue's label names), `QTY_SCALE`. Every name UPPER_CASE and every value an `int`, so an `@njit` kernel may read them under `check_numba_globals`.
- `ALLOWLISTED_SEC_TO_NS_SITES` gained exactly one entry. `features/*.py` stays forbidden by construction: a later plan that writes `10 * NS_PER_SECOND` fails CI instead of quietly establishing a second convention. `data/dq/checks.py` now imports the two constants instead of defining them, and every existing `from data.dq.checks import NS_PER_SECOND` importer resolves to the same objects.
- `features/event_stream.py` turns the two curated frames into one `EVENT_SCHEMA` frame via `bookticker.merge_sorted(trade, key="etime")` and **asserts the strict total order inside the merge**, not only in tests. The assertion reads the materialized `source_rank` column, so it catches an argument swap that argument-position-only reasoning could not.
- The numba boundary refuses a degraded array. `polars`' `.to_numpy()` on a `Float64`-with-null returns a **NaN-filled copy and does not raise** (re-measured in this venv), so `event_arrays` checks dtype and null count per column and names the offender.
- Cross-stream payload filler is **NaN, never 0.0**: a kernel that wrongly reads a trade row's `bid_price` produces a visible NaN, not a plausible size.
- D-04-12's two row classes come back as counts (`na_placeholder_excluded`, `unknown_side_rows`) rather than disappearing — and the NA-placeholder signature has one definition in the codebase, reused from `curated_build.filter_na_placeholders`.
- The whole thing was run once over the real 2026-09-13 day: **every expected number in the plan matched exactly.**

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | One ns constant module, one allowlisted conversion site, one numba cache dir | `4ec7943` (prior session) | `data/time_ns.py`, `data/dq/checks.py`, `tools/check_ms_to_ns_site.py`, `tests/conftest.py`, `tests/features/test_time_ns.py`, `tests/tools/test_check_ms_to_ns_site_allowlist.py` |
| 2 | The merged event stream and the decision-row rule | `fd90e9e` | `features/__init__.py`, `features/event_stream.py`, `tests/features/test_event_stream.py`, (deletes `tests/features/__init__.py`) |

## Mutation Check Results

Each mutation was applied to the real source, the suite was run, the named test was observed failing, and the file was restored from a byte-identical backup (`git status` clean afterwards).

**Task 1 — allowlist entry (run this session; it was not recorded when Task 1 was committed):**

```
# entry for data/time_ns.py commented out of ALLOWLISTED_SEC_TO_NS_SITES
$ ./.venv/bin/python3 -m tools.check_ms_to_ns_site
PASS: exactly one ms-to-ns site at data/capture/parse.py:36
FAIL: seconds-to-ns conversion site(s) outside the allowlist (add to
      ALLOWLISTED_SEC_TO_NS_SITES if intentional): ['data/time_ns.py']
  data/time_ns.py:58
  data/time_ns.py:72
  data/time_ns.py:73
  data/time_ns.py:74
exit=1
# restored
$ ./.venv/bin/python3 -m tools.check_ms_to_ns_site
PASS: exactly one ms-to-ns site at data/capture/parse.py:36
PASS: 21 seconds-to-ns site(s) in 5 file(s), all allowlisted (50 files scanned)
exit=0
```

**Task 1 — `NUMBA_CACHE_DIR`: DEFERRED, not observed.** `./.venv/bin/python3 -m tools.check_numba_globals` reports `scanned 50 files, 0 njit functions` — there is no `@njit(cache=True)` kernel in the tree yet, so deleting the conftest line cannot produce a `*.nbc`/`*.nbi` file and the mutation would pass vacuously. **This mutation is deferred to Plan 03's Task 2**, which lands the first cached kernel. `test_no_numba_cache_artifacts_under_mvp` is in place and currently passes over an empty set; `find mvp -name '*.nbc' -o -name '*.nbi'` returns nothing.

**Task 2 (a) — swap the merge receiver (`trades.merge_sorted(quotes, ...)`):**

```
E  ValueError: assert_strict_total_order: row 1 does not advance on
   (etime, source_rank, seq): row 0 is (etime=1, source_rank=1, seq=0) and
   row 1 is (etime=1, source_rank=0, seq=0); 1 violation(s) total
FAILED tests/features/test_event_stream.py::test_bookticker_wins_an_etime_tie
FAILED tests/features/test_event_stream.py::test_merged_order_is_a_strict_total_order
FAILED tests/features/test_event_stream.py::test_one_decision_row_per_distinct_etime
FAILED tests/features/test_event_stream.py::test_hypothesis_merge_is_order_of_construction_independent
FAILED tests/features/test_event_stream.py::test_na_placeholder_trades_are_excluded_and_counted
5 failed, 4 passed
# restored -> 9 passed
```

Worth noting what killed it: the **runtime assertion**, not a test-only comparison. The test is the tripwire; the control is in the build.

**Task 2 (b) — `decision_row_index` takes the FIRST row of each etime run:**

```
FAILED tests/features/test_event_stream.py::test_bookticker_wins_an_etime_tie
FAILED tests/features/test_event_stream.py::test_one_decision_row_per_distinct_etime
FAILED tests/features/test_event_stream.py::test_hypothesis_merge_is_order_of_construction_independent
3 failed, 6 passed
# restored -> 9 passed
```

The fixture deliberately contains an etime run of length 3 (`etime=2`: quote, quote, trade) so first-position and last-position give different answers; the test asserts the exact positions `[0, 3, 4, 6]`, not just the count — a count-only assertion would have survived this mutation.

**Task 2 (c) — delete the `null_count` assertion in `event_arrays`:**

```
>   with pytest.raises(ValueError, match="bid_price"):
E   Failed: DID NOT RAISE ValueError
FAILED tests/features/test_event_stream.py::test_event_arrays_refuse_a_nullable_hot_path_column
1 failed, 8 passed
# restored -> 9 passed
```

This is the mutation that matters most: without the assertion `.to_numpy()` returns `[1.0, nan, 3.0]` — a silent copy — so nothing else in the chain would have failed.

## Measured Numbers (real 2026-09-13 day)

Run once as a script under `./.venv/bin/python3` (never a collected test — pre-commit runs `pytest tests -x -q` on every commit and the lake is not on every machine), through the real `load_curated` path with hash verification and the DQ pause active. **The capture daemon (PID 72546) was running throughout, so timings are best-of-3 and are not a regression baseline.**

| Quantity | Measured | Plan expected |
|---|---|---|
| bookTicker rows | **17,167,290** | 17,167,290 ✓ |
| trade rows | **1,409,705** | 1,409,705 ✓ |
| merged rows (`n_events`) | **18,576,995** | 18,576,995 ✓ |
| distinct `etime` (`n_unique`) | **6,864,853** | 6,864,853 ✓ |
| decision rows (`decision_row_index`) | **6,864,853** | 6,864,853 ✓ |
| `assert_strict_total_order` over 18,576,994 adjacent pairs | **PASS, 0.125 s** | pass ✓ (plan estimated ~0.05 s) |
| decision rows that are TRADE rows | **329,580** | 329,580 ✓ |
| distinct trade `etime`s | **329,580** (equal, as it must be under quotes-first) | 329,580 ✓ |
| `merge_sorted` wall-clock | **0.785 s** (best of 3: 0.785/0.810/0.820) | ~0.6 s (research spread 0.610–0.819) |
| `event_arrays` wall-clock | **0.000 s** (2.2e-05 s best) | ~0.00 s ✓ |
| `load_curated` both streams, hash-verified | 2.699 s | not predicted |
| `na_placeholder_excluded` | **0** | — |
| `unknown_side_rows` | **0** | — |

Two numbers worth keeping:

- **328,313 real `etime`s carry both a quote and a trade.** `329,580 − 328,313 = 1,267` — exactly the research note's count of decision rows that would be trade rows under a *trades-first* rank. So the two independently-measured numbers close on each other, and the rank pin is doing precisely what the research said it does: it moves 328,313 decision rows from a quote row to a trade row, and changes nothing else.
- **`na_placeholder_excluded = 0` and `unknown_side_rows = 0`** on this day. Phase 3's curated build already filtered the placeholders, so the filter here is the uniform no-op its docstring predicts on an already-cleaned day — present so archive-sourced and capture-sourced days stay on one code path, not dead code.

## Verification Transcript

```
$ ./.venv/bin/pytest tests/features/test_event_stream.py -q
9 passed in 1.22s

$ ./.venv/bin/pytest tests -x -q          # full suite
702 passed in 139.34s          (693 before this session)

$ ./.venv/bin/ruff check .                 All checks passed!
$ ./.venv/bin/ruff format --check .        116 files already formatted
$ ./.venv/bin/python3 -m tools.check_ms_to_ns_site        exit 0
$ ./.venv/bin/python3 -m tools.check_numba_globals        exit 0 (0 njit functions)
$ ./.venv/bin/python3 -m tools.check_lockbox_containment  exit 0

$ git commit    # Task 2 -- all 15 hooks, no --no-verify
ruff check ............ Passed        check_lockbox_containment ...... Passed
ruff format --check ... Passed        check_numba_globals ............ Passed
uv lock --check ....... Passed        check_spec_diff ................ Passed
check_pin_versions .... Passed        check_no_manifest_rewrite ...... Passed
check_ms_to_ns_site ... Passed        check_no_manifest_rewrite --full  Passed
check_catalogue_completeness  Passed  check_manifest_id_integrity .... Passed
check_latest_ban ...... Passed        check_manifest_append_only ..... Passed
                                      pytest (tests, via testpaths) .. Passed

$ find mvp -name '*.nbc' -o -name '*.nbi'     # no numba cache artifacts
(nothing)
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `mvp/tests/features/__init__.py` shadowed the real `features` package**

- **Found during:** Task 2, first test run
- **Issue:** `from features.event_stream import ...` raised `ModuleNotFoundError: No module named 'features.event_stream'` even though `python -c "import features.event_stream"` worked. `mvp/tests/` has no `__init__.py`, so pytest inserts `tests/` at the front of `sys.path` — making `tests/features/` (a package, created by Task 1 when `mvp/features/` did not yet exist) resolve as `features` and shadow the real one.
- **Fix:** `git rm mvp/tests/features/__init__.py`. This is the repo's existing convention, arrived at for the same reason: `tests/spec`, `tests/tools` and `tests/tracking` have no `__init__.py` precisely because `spec/`, `tools/` and `tracking/` are real top-level packages, while `tests/backfill`, `tests/capture`, `tests/dq`, `tests/ingest`, `tests/lockbox`, `tests/store` (no name collision) do have one.
- **Verification:** `pytest tests/features -q` → 13 passed (both test modules still collected); full suite 702 passed.
- **Committed in:** `fd90e9e`

**2. [Rule 2 - Missing critical functionality] `project_trade` returns counts alongside the frame**

The plan's signature line shows `project_trade(df) -> pl.DataFrame` while its body requires "the excluded count is RETURNED alongside the frame, never dropped silently". Implemented as `-> tuple[pl.DataFrame, dict[str, int]]`. Not a divergence from intent — the header line was abbreviated — recorded because the type differs from what a reader of the plan's signature line would expect.

**Total deviations:** 2 (1 blocking-fix, 1 signature clarification). No architectural change, no Rule 4 checkpoint.

## Surprises

- **The plan's own Task 1 artifact was the thing that broke Task 2.** `tests/features/__init__.py` was correct when it was written (there was no `mvp/features/`) and became a shadowing bug the moment Task 2 created the package it named. Worth carrying: in this layout, a test directory may only be a package when no source package shares its name.
- **`assert_strict_total_order` measured 0.125 s, not the plan's estimated ~0.05 s** — 2.5× the estimate but still 16 % of the merge, comfortably inside "cheap enough to run on every build". Three `diff()`s over 18.6M rows plus a boolean combine; the estimate came from a differently-shaped benchmark.
- **`merge_sorted` measured 0.785 s vs the plan's quoted 0.610 s.** Same code, same day; the research note's own spread was 0.610–0.819 s with the capture daemon running. Not a regression, and not a baseline either — recorded so a later measurement is not read as a change.
- **This is the third instance of one lesson, and the second time it cost a debugging cycle.** STATE.md already records it from Phase 2 (`mvp/tests/spec/__init__.py must not exist -- it collides with the real mvp/spec package`), yet the plan specified `tests/features/__init__.py` and Task 1 wrote it. The rule generalizes: a test directory may be a package only when no source package shares its name. Added to STATE.md's decisions in that general form.
- **The two independently-derived tie counts agree exactly** (329,580 − 328,313 = 1,267). Neither number was computed from the other, and 1,267 was measured a session earlier by a different script.

## Issues Encountered

None blocking. The previous session's death during Task 2 left no partial work (`git status` clean at `4ec7943`), so Task 2 started from a known state.

## Next Phase Readiness

- **Plan 02/03/05** call `merge_curated_streams` → `event_arrays` → `decision_row_index` and get, respectively: an order they do not have to re-establish, a refusal at the numba boundary instead of a silent NaN copy, and a decision-row index that is a pure function of position.
- **Plan 03** (the first `@njit(cache=True)` kernel) owes the deferred `NUMBA_CACHE_DIR` mutation check: delete the `tests/conftest.py` line, run the suite, confirm `*.nbc` appears under `mvp/`, restore.
- **Plan 05** (the build) persists the stats dict verbatim into `build_stats.json`; `n_decision_rows` is already cross-checked against `etime.n_unique()` on the real day.
- **Plan 06** (leakage properties) can rely on `decision_row_index` being position-pure: truncating the merged frame after `t` re-derives the same decision rows for the prefix by construction.
- **`event_arrays` hands back READ-ONLY views** — measured just now: `pl.Series(...).to_numpy().flags.writeable` is `False`, `owndata` `False`. A kernel that only reads is fine (and gets zero-copy); a kernel that tries to write into an `event_arrays` output will fail at numba compile time and must allocate its own output arrays. Plan 03 should assume read-only inputs.
- `EVENT_SCHEMA` is the exact contract at the numba boundary. A later plan that carries extra columns must `.select(EVENT_SCHEMA)` before calling `event_arrays`.

## Self-Check: PASSED

- `mvp/data/time_ns.py`, `mvp/features/__init__.py`, `mvp/features/event_stream.py`, `mvp/tests/features/test_time_ns.py`, `mvp/tests/features/test_event_stream.py`, `mvp/tests/tools/test_check_ms_to_ns_site_allowlist.py` — all present on disk.
- `mvp/tests/features/__init__.py` — confirmed deleted and its deletion committed in `fd90e9e`.
- Commits `4ec7943` and `fd90e9e` both present in `git log` on `feature/phase-04-feature-label-engine`.
- `grep -rn "NS_PER_SECOND" mvp --include=*.py` shows the constant DEFINED in exactly one file (`data/time_ns.py`); every other occurrence is an import or a use.
