---
phase: 04-feature-label-engine
plan: 03
subsystem: feature-engine
tags: [numba, streaming-kernel, ofi, ring-buffer, leakage-properties, hypothesis, catalogue, bitwise-equivalence]

# Dependency graph
requires:
  - phase: 04-feature-label-engine/04-01
    provides: features/event_stream.py's EVENT_SCHEMA / SOURCE_RANK_* / event_arrays (read-only zero-copy views) / decision_row_index; data/time_ns.py's TRADE_FLOW_WINDOW_NS and QTY_SCALE as pre-multiplied ints
  - phase: 03-data-layer-backfill-ingest-lockbox
    provides: data/ingest/trade_side.py's SIGN_MAKER_IS_SELLER = +1; data/store.py's load_curated (hash-verified, DQ-paused)
  - phase: 02-stage-0-living-spec-ci-guardrails-tracking
    provides: tools/check_numba_globals.py's UPPER_CASE-globals rule; tools/check_spec_diff.py's definition-change rejection; spec/catalogue.py's get_feature literal-name rule; tests/leakage/ as a collected directory
provides:
  - mvp/features/reference.py -- the readable twin: STATUS_* vocabulary, FeatureStatusError, ReferenceState/new_reference_state, new_outputs, run_reference, run_reference_checked, QTY_SCALE_F
  - mvp/features/kernel.py -- RING_CAPACITY, STATE_I64_SLOTS/STATE_F64_SLOTS, new_state, run_kernel (@njit(cache=True)), run_kernel_checked, KernelStatusError
  - The carry-in/carry-out state contract every later caller conforms to (the trainer's one-shot call and the simulator's row-by-row call are proven byte-identical)
  - mvp/tests/leakage/test_feature_information_set.py -- the first real FEAT-03 contents: future-shuffle and future-delete invariance, anti-vacuity, and declared-vs-measured information-set containment
  - mvp/tests/fixtures/event_streams.py -- quote/trade/build_events/random_events/canonical_streams, the 18 pinned shapes both implementations are compared on
  - spec/features.toml's four information_set values and notes, pinned to what the code computes
affects: [04-feature-label-engine/04-04, 04-feature-label-engine/04-05, 04-feature-label-engine/04-06, 05-fold-harness, 06-simulator]

# Tech tracking
tech-stack:
  added: []   # numba 0.65.1 and hypothesis were already pinned; this is the first @njit function in the repo
  patterns:
    - "A pure-Python twin with the same state contract as the JIT kernel, compared BITWISE (np.array_equal equal_nan=True, never allclose) on every fixture plus hypothesis streams -- the JIT version cannot drift silently"
    - "Never raise from inside @njit: a negative status code plus state_i64['error_row'] is strictly more information than an exception message, and a thin Python wrapper turns it into a named exception"
    - "Status-code vocabulary lives in the numba-free module and the kernel imports it, so the two implementations cannot disagree about what -2 means"
    - "Ring capacity read from ring_t.shape[0], not a module constant, so the overflow guard is testable at cap=8; `& (cap - 1)` still replaces the modulo, and a non-power-of-two ring is refused before the first row"
    - "Anti-vacuity as a first-class property: an invariance suite that never asserts a feature IS sensitive passes on a function returning a constant (demonstrated -- see mutation (e))"
    - "A declared information_set made executable: measure which rows can change a value at t by perturbing them one at a time, and assert the measured set is contained in what the catalogue declares"
    - "Catalogue information_set/notes as the pinning surface: check_spec_diff compares `definition` only, so window lengths, endpoints, units and edge cases go in notes and a redefinition still needs a new name"
    - "numba has no bounds checking, so every array length is checked before the loop -- an output array one row short would be a silent out-of-bounds WRITE"

key-files:
  created:
    - mvp/features/reference.py
    - mvp/features/kernel.py
    - mvp/tests/features/test_reference.py
    - mvp/tests/features/test_kernel.py
    - mvp/tests/leakage/test_feature_information_set.py
    - mvp/tests/fixtures/event_streams.py
  modified:
    - mvp/spec/features.toml (information_set + notes on all four features; every `definition` byte-unchanged)
    - mvp/spec.md (re-rendered feature table)
  deleted: []

key-decisions:
  - "Task 3 (the catalogue) was executed FIRST, not last. test_declared_information_set_matches_the_measured_lookback reads the catalogue, and ofi's measured sensitivity set {prev_l1_update, t} exceeds the old declared `t` -- with the plan's task order that test is red until the TOML lands, and pre-commit runs the whole suite, so Task 1 could not have been committed."
  - "STATE_F64_SLOTS gains `last_ofi`, which the plan's slot list does not name. ofi is carried between L1 updates and cannot be recomputed from prev_*; without the slot a chunk boundary between two quotes emits NaN, and on the real day ofi's NaN count would be 329,581 (one per trade decision row) instead of 1."
  - "Ring capacity is `ring_t.shape[0]`, not RING_CAPACITY. The plan asks for `& (RING_CAPACITY - 1)` with a compile-time constant AND for a cap=8 overflow test; those are mutually exclusive. The mask works for any power-of-two runtime cap, so the test exists and the optimisation stays -- and mutation (a) proves the mask is semantics-free."
  - "STATUS_ARRAY_LENGTH_MISMATCH added (Rule 2): numba does not bounds-check, so an output array shorter than the input is a silent out-of-bounds write rather than a crash. Checked once before the loop."
  - "Status codes and QTY_SCALE_F live in reference.py and kernel.py imports them, inverting the intuitive direction. Forced by the task order -- reference.py must be green before kernel.py exists -- and it buys one definition of the division constant, so the two implementations cannot divide by different numbers."
  - "A crossed or locked book is COUNTED, not refused (04-RESEARCH-NOTES.md ambiguity #7 resolved). D-04-16 locked the zero-size case as an assertion; extending assertion-not-branch to crossed books was research prose. mid and imb_top stay arithmetically well-defined, Phase 3's check_crossed_locked_book already treats it as an informational per-day count, and hard-failing here would make the feature tier stricter than the curated tier it reads."
  - "The four OFI indicator additions are ordered, and the order is part of the contract in both implementations. Float addition is not associative -- re-associating to (qb - pqb) - (qa - pqa) changes the last bit, which is why the paper's form and the QuestDB form agree only to 2.84e-14."
  - "tests/fixtures/event_streams.py, not tests/features/__init__.py. Fourth instance of the same lesson: a test directory may be a package only when no source package shares its name."

patterns-established:
  - "canonical_streams() as the single enumeration of pinned shapes, with a guard test asserting the required names are present so a shape cannot be silently dropped from the equivalence sweep"
  - "Cumulative-counter assertions in a resume test carry an explicit non-vacuity assertion (`counter > 0 on this fixture`) -- which immediately caught a fixture with no gap and no locked quote"
  - "Mutation transcripts print the exact block removed and inserted before running the suite, because ruff format rewrapping a target line is indistinguishable from an uncovered test"

requirements-completed: []
requirements-partial:
  - "FEAT-02: the four catalogued features are implemented, proven bit-identical between a JIT and a reference implementation, and verified against the research numbers on the real day. Labels (the other half of the feature/label engine) are Plan 04; the build that writes them into the tier is Plan 05."
  - "FEAT-01: the numba streaming kernel FEAT-01 names now exists and its single-code-path claim is a passing runtime property (batch == chunked == row-by-row). The byte-identical train/infer/sim call sites themselves land with Plan 05/Phase 6."
  - "FEAT-03: the CI leakage suite has real contents -- future-shuffle invariance, future-delete invariance, anti-vacuity, and declared-vs-measured containment. Plan 06 owns the rest and deletes the Phase 2 scaffold."

# Metrics
duration: ~2h
completed: 2026-09-19
---

# Phase 4 Plan 03: The numba Feature Kernel Summary

**Four numbers per event row — the mid, the top-of-book imbalance, the Cont–Kukanov–Stoikov quote delta and the last second of signed trade flow — now come out of one streaming pass that emits the same values whether it is handed a whole day or one row at a time, and a second implementation written to be read proves the fast one has not drifted.**

## Performance

- **Duration:** ~2 h
- **Tasks:** 3 (one commit each), executed in the order 3 → 1 → 2 (see Deviation 1)
- **Tests:** **748 before → 817 after** (+69: 23 `test_reference.py`, 37 `test_kernel.py`, 9 `test_feature_information_set.py`)
- **Files:** 6 created, 2 modified, 0 deleted

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 3 | Pin the feature catalogue to what the code computes | `76b8104` | `spec/features.toml`, `spec.md` |
| 1 | Leakage properties first, then the pure-Python reference | `9883f68` | `tests/leakage/test_feature_information_set.py`, `features/reference.py`, `tests/features/test_reference.py`, `tests/fixtures/event_streams.py` |
| 2 | The @njit kernel, its state contract, and bit-identical equivalence | `36bddb2` | `features/kernel.py`, `features/reference.py`, `tests/features/test_kernel.py`, `tests/fixtures/event_streams.py` |
| — | This summary + STATE/ROADMAP | `1f0498e` | `.planning/phases/04-feature-label-engine/04-03-SUMMARY.md`, `.planning/STATE.md`, `.planning/ROADMAP.md` |

## Accomplishments

- **One kernel.** `run_kernel` is the repo's first `@njit(cache=True)` function (`check_numba_globals` went from "0 njit functions" to 1). It walks the merged event array once, accumulating on every row and emitting on every row; the caller picks decision rows with `decision_row_index`, which stays a pure function of position so a prefix re-derives the same decision rows.
- **A twin that makes drift non-silent.** `features/reference.py` is a deliberately different implementation — `collections.deque` window, Python `int` accumulator — compared to the kernel bitwise on 18 pinned shapes plus hypothesis-generated streams. Agreement is evidence, not a tautology.
- **The state contract is a passing property, not a comment.** One call over a stream, and 64/7/3/2/1-row chunked calls, emit byte-identical values. That is the difference between the trainer's call shape and the simulator's, and nothing downstream could have caught them disagreeing.
- **The leakage suite has real contents and was red first.** It failed at import before `features.reference` existed (transcript below), and it now holds three kinds of property — invariance, anti-vacuity, containment — where the third is what gives the first two meaning.
- **The catalogue now says what the code does.** `ofi`'s `information_set` was `t`, which hid its dependence on the previous quote; `trade_flow`'s was `t`, which hid a one-second window. Both are declared, and the test measures the code against them instead of against a claim true of any causal function.
- **Every research number reproduced on the real 2026-09-13 day**, including the OFI cross-check against the polars `shift(1)` QuestDB form at max abs diff **2.842e-14** over 17,167,289 rows — the research note's figure to three significant digits.

## RED Transcript (D-04-08: the leakage suite must fail before the kernel exists)

`tests/leakage/test_feature_information_set.py` was written and run before any implementation existed:

```
$ ./.venv/bin/pytest tests/leakage/test_feature_information_set.py -x -q
________ ERROR collecting tests/leakage/test_feature_information_set.py ________
ImportError while importing test module '.../tests/leakage/test_feature_information_set.py'.
Traceback:
tests/leakage/test_feature_information_set.py:41: in <module>
    from features.reference import FEATURE_OUTPUT_NAMES, run_reference_checked
E   ModuleNotFoundError: No module named 'features.reference'
ERROR tests/leakage/test_feature_information_set.py
1 error in 0.25s
```

`tests/features/test_kernel.py` likewise, before `features/kernel.py`:

```
tests/features/test_kernel.py:33: in <module>
    from features.kernel import (
E   ModuleNotFoundError: No module named 'features.kernel'
1 error in 0.22s
```

**A deliberately-failing commit is not landable in this repo** — the pre-commit `pytest (tests, via testpaths)` hook runs the whole suite on every commit, and `--no-verify` is forbidden. So the red is transcribed here rather than preserved as a RED commit, and each task's single commit contains both the failing test and the code that makes it pass. This is the same accounting Plan 04-01 and 04-02 used.

## Mutation Check Results

Every mutation printed the exact block it removed and the exact block it inserted **before** running the suite, and asserted the target text was present — an earlier plan in this phase had a mutation silently not apply because `ruff format` had rewrapped the target lines, which is indistinguishable from an uncovered test. Every file was restored from a byte-identical backup (md5 compared).

### Task 1 — `features/reference.py`

**(a) Four independent indicators → `if/elif` chain** (`b >= pb` / `b <= pb` became `if b > pb ... elif b < pb`, treating an unchanged price as "contribute 0"):

```
FAILED tests/features/test_reference.py::test_ofi_three_cases_per_side[bid resized at same price -> +(qb_n - qb_n-1)-second_quote1-4.0]
FAILED tests/features/test_reference.py::test_ofi_three_cases_per_side[ask resized at same price -> -(qa_n - qa_n-1)-second_quote4--3.0]
E   AssertionError: ask resized at same price -> -(qa_n - qa_n-1)
E   assert np.float64(0.0) == -3.0
2 failed, 31 passed
```

**`test_ofi_repeated_identical_quote_is_exactly_zero` PASSED under this mutation** — it is 0 either way. Exactly the reason the three-case table is tested row by row instead: the repeated-quote assertion alone is not coverage of the formulation. Only the two *resize* rows die, which is the elif chain's actual defect.

**(b) Window eviction `<=` → `<`** (half-open becomes closed at the old end):

```
E   AssertionError: a trade at exactly t - 1s must be OUTSIDE the window
E   assert np.float64(0.75) == 0.25
FAILED tests/features/test_reference.py::test_trade_flow_window_is_half_open
1 failed, 32 passed
```

**(c) `last_ofi` seeded `0.0` instead of NaN:**

```
FAILED tests/features/test_reference.py::test_ofi_first_quote_of_the_partition_is_nan_not_zero
FAILED tests/features/test_reference.py::test_warmup_is_one_flag_computed_once
2 failed, 31 passed
```

**(d) int64 fixed point → float64 accumulator** (three lines: the slot type, the scaling, the emission):

```
E   AssertionError: 844 of 1390 decision rows' trade_flow depend on the etime tie order
E   assert 844 == 0
FAILED tests/features/test_reference.py::test_trade_flow_is_bit_identical_under_either_tie_order
1 failed, 32 passed
```

**This mutation SURVIVED the first version of the test, and that is the most useful thing in this section.** The fixture drew etimes from a grid so fine that 4,000 rows shared only 3 `etime`s — with no ties, the two "tie orders" are the same stream and the float accumulator is trivially reproducible. The test was passing for the wrong reason and would have passed forever. The fixture is now tie-dense at 2.88 rows per distinct `etime`, matching the real day's 2.71, and the mutation moves 61 % of decision rows (the real day moves 95 %).

**(e) `ofi` stubbed to a constant `0.0`** — the anti-vacuity demonstration the plan's `<done>` asks for:

```
E   AssertionError: ofi is not sensitive to ANY row -- the invariance properties above would pass on a constant
FAILED tests/leakage/test_feature_information_set.py::test_a_feature_IS_sensitive_to_its_own_declared_window
1 failed, 9 passed
```

Both invariance properties **and** the containment test passed on a feature that returns a constant. Only the anti-vacuity assertion noticed. That is the whole argument for its existence, observed rather than asserted.

### Task 2 — `features/kernel.py`

**(a) `& (cap - 1)` → `% cap` — a NON-mutation, recorded to prove the optimisation is semantics-free:**

```
`& (cap-1)` vs `% cap`: bit-identical over 19 streams / 350 rows: True
37 passed in 1.28s
```

(The first comparison harness reported "DIFFERS" on every stream containing a NaN — `float('nan') != float('nan')`. That was the harness, not the kernel; re-run with `np.array_equal(..., equal_nan=True)` it is identical. Recorded because a false positive here is as expensive as a false negative.)

**(b) The `if tail == head: return OVERFLOW` guard removed.** The named test fails:

```
E   assert 0 == -1
FAILED tests/features/test_kernel.py::test_ring_overflow_returns_a_status_not_a_wrap
```

and **what the wrapped ring looks like is the reason the guard exists.** On the 9-trades-in-a-cap-8-ring fixture the emitted column is *indistinguishable from correct* — every value matches the reference — while the state is already corrupt:

```
status returned: 0 (STATUS_OK -- the overflow was NOT reported)
trade_flow with the guard removed: [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5]
trade_flow, correct (reference)  : [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5]
head,tail after the 9th trade: 0 1  -> a full ring reads as EMPTY
max_occupancy reported: 7 (the window actually held 9)
```

Extend the same stream two seconds past the last trade, when the window should be empty, and the corruption surfaces as a plausible number:

```
wrapped ring  trade_flow tail: [4.0, 4.25]
correct       trade_flow tail: [0.0, 0.25]
```

4.0 BTC of signed flow reported one second after nothing traded, status 0, no error — because the wrapped entries can never be evicted. T-04-12 in one line.

**(c) `acc_scaled` reset to 0 per call instead of carried in `state_i64`:**

```
FAILED tests/features/test_kernel.py::test_batch_and_chunked_resume_are_bit_identical[1]
FAILED tests/features/test_kernel.py::test_batch_and_chunked_resume_are_bit_identical[2]
FAILED tests/features/test_kernel.py::test_batch_and_chunked_resume_are_bit_identical[3]
FAILED tests/features/test_kernel.py::test_batch_and_chunked_resume_are_bit_identical[7]
FAILED tests/features/test_kernel.py::test_batch_and_chunked_resume_are_bit_identical[64]
FAILED tests/features/test_kernel.py::test_a_single_row_at_a_time_is_the_simulator_call_shape
6 failed, 31 passed
```

**(d) `cache=True` dropped.** Correctness does not depend on the cache — the full suite is green — and the cost is measured:

```
$ ./.venv/bin/pytest tests -q          # with @njit, no cache
817 passed in 132.40s

compile time, cache=True DROPPED (every process pays it):  0.41 s / 0.37 s
compile time, cache=True RESTORED:                         0.43 s cold, 0.09 s warm
```

4.5× on a warm process, and on the real day the cache load is inside the 1.088 s first call against 0.090 s warm.

### Deferred from Plan 04-01 — `NUMBA_CACHE_DIR`

Plan 04-01 deferred this mutation because it could not fire without a `cache=True` kernel. It now fires. The conftest pin was deleted and the suite run:

```
### DEFERRED MUTATION: delete the NUMBA_CACHE_DIR pin from tests/conftest.py
--- removed ---
os.environ.setdefault(
    "NUMBA_CACHE_DIR", str(Path(tempfile.gettempdir()) / "aihf-numba-cache")
)
--- inserted ---
(nothing)

FAILED tests/features/test_time_ns.py::test_numba_cache_dir_is_pinned_outside_the_repo
FAILED tests/features/test_time_ns.py::test_no_numba_cache_artifacts_under_mvp
2 failed, 815 passed in 120.64s

$ find mvp -name '*.nbc' -o -name '*.nbi'
  mvp/features/__pycache__/kernel.run_kernel-199.py313.nbi
  mvp/features/__pycache__/kernel.run_kernel-199.py313.1.nbc
```

Restored, artifacts deleted, `tests/features/test_time_ns.py` → 4 passed, `find` → nothing.

**And the threat fired for real during this plan, by accident.** The mutation-(a)/(b) probe scripts were bare `./.venv/bin/python3 -c` runs — `conftest.py` only pins the variable under pytest — so they compiled `run_kernel` with `cache=True` and no cache dir, and dropped `*.nbc`/`*.nbi` into `mvp/features/__pycache__/`. **`git status` stayed clean the whole time**, because `__pycache__` is gitignored: the repo-walk assertion in `test_no_numba_cache_artifacts_under_mvp` is the only thing that sees this, and it is what caught it. T-04-14's "accept" disposition rests entirely on that test.

## Real-Data Verification (read-only, 2026-09-13)

Run once as a script under `./.venv/bin/python3` with `NUMBA_CACHE_DIR` exported outside the repo, through the real `load_curated` path with hash verification and the DQ pause active. Never a collected test. The capture daemon (PID 72546) was live throughout, so timings are best-of-3 and are not a regression baseline.

| Quantity | Measured | Plan expected |
|---|---|---|
| bookTicker / trade / merged rows | **17,167,290 / 1,409,705 / 18,576,995** | ✓ |
| decision rows | **6,864,853** | ✓ |
| `ofi` NaN count over L1 rows | **1** (merged row 0, the first L1 update) | 1 ✓ |
| `ofi` NaN count at **decision rows** | **1** | 1 ✓ |
| `ofi` exact zeros after row 0 | **798** | 798 ✓ |
| `ofi` mean / std | **−0.000066 / 0.497476** | ≈ −0.0001 / ≈ 0.4975 ✓ |
| `ofi` p1 / p99 | **−1.3380 / 1.3310** | ≈ −1.338 / ≈ 1.331 ✓ |
| `ofi` vs polars `shift(1)` QuestDB CASE form | **2.842e-14 max abs diff over 17,167,289 rows** | ≤ 1e-13 ✓ (research: 2.84e-14) |
| `trade_flow` max ring occupancy | **5,092** | 5,092 ✓ |
| ring overflow status returned | **never** (12.9× headroom) | never ✓ |
| empty-window rows | **784,343 (4.2 %)** | 784,343 ✓ |
| `trade_flow` differing decision rows, both tie orders | **0 of 6,864,853** | 0 ✓ |
| `mid`/`imb_top`/`ofi` at decision rows, both tie orders | **bit-identical** | ✓ |
| `imb_top` zero-size assertion | **never fired**; `imb_top` ∈ [−1, 1] everywhere | ✓ |
| crossed/locked L1 updates | **0** | 0 ✓ |
| `mid` NaN count | **0** (the day's first merged row is a quote) | — |
| kernel wall-clock, best of 3 | **0.090 s** (0.090 / 0.125 / 0.132), **206 M rows/s** | 0.05–0.15 s ✓ |
| first call (JIT cache load) | 1.088 s | — |
| `load_curated`, both streams, hash-verified | 1.484 s | — |
| warmup rows | **344** (0.0019 %), spanning 990,000,000 ns | — |
| `*.nbc`/`*.nbi` under `mvp/` afterwards | **none** | none ✓ |

**One number needed explaining, and the explanation is the decision-row rule doing its job.** `ofi` is NaN on **3** rows of the merged stream, not 1 — and that is correct. Rows 0, 1 and 2 all carry `etime = 1789257600002000000`: a quote followed by two trades at the same millisecond, with the second L1 update at merged position 3. `ofi` is undefined over `[first L1 update, second L1 update)`, which is those three rows. Only row 2 is the decision row for that `etime`, so **at the decision rows — the rows the feature tier actually writes — the NaN count is exactly 1**, which is the research note's number. The plan's "NaN count exactly 1 (row 0 of the L1 stream)" is right about the L1 stream and right about decision rows; it is the raw merged stream that has three, for a reason that is the merge working as designed.

## Verification Transcript

```
$ ./.venv/bin/pytest tests -q
817 passed in 109.04s                   (748 before this plan)

$ ./.venv/bin/ruff check .                              All checks passed!
$ ./.venv/bin/ruff format --check .                     130 files already formatted
$ ./.venv/bin/python3 -m tools.check_numba_globals      scanned 57 files, 1 njit functions   exit 0
$ ./.venv/bin/python3 -m tools.check_ms_to_ns_site      exit 0
$ ./.venv/bin/python3 -m tools.check_spec_diff          exit 0
$ ./.venv/bin/python3 -m tools.check_catalogue_completeness   scanned 57 files, 8 call sites   exit 0
$ ./.venv/bin/python3 -m tools.check_lockbox_containment exit 0

$ find mvp -name '*.nbc' -o -name '*.nbi'
(nothing)

# every commit: all 15 hooks, never --no-verify
ruff check ............ Passed        check_lockbox_containment ...... Passed
ruff format --check ... Passed        check_numba_globals ............ Passed
uv lock --check ....... Passed        check_spec_diff ................ Passed
check_pin_versions .... Passed        check_no_manifest_rewrite ...... Passed
check_ms_to_ns_site ... Passed        check_no_manifest_rewrite --full  Passed
check_catalogue_completeness  Passed  check_manifest_id_integrity .... Passed
check_latest_ban ...... Passed        check_manifest_append_only ..... Passed
                                      pytest (tests, via testpaths) .. Passed
```

### The `check_spec_diff` guard was observed biting (Task 3's proof it stayed on the allowed side)

```
### MUTATION: one character appended to ofi's `definition`
--- removed ---
definition = "Order-flow imbalance: top-of-book quote-and-size delta between consecutive updates (Cont-Kukanov-Stoikov convention)"
--- inserted ---
definition = "Order-flow imbalance: top-of-book quote-and-size delta between consecutive updates (Cont-Kukanov-Stoikov convention)."

FAIL: definition/computation changed under an existing name -- give it a new name instead:
  features (definition changed): ['ofi']
exit=1
# reverted -> exit=0
```

Every `definition` string was additionally verified byte-unchanged by parsing `git show HEAD:mvp/spec/features.toml` and comparing field by field: `lag`, `normalization`, `source_datasets`, `version` and `introduced` are unchanged too. Only `information_set` and `notes` moved.

## TDD Gate Compliance

Each task's failing test was written and run first (transcripts above), then the implementation, then the mutation checks. There is **no `test(...)` RED commit**, and there cannot be one: the pre-commit `pytest` hook runs the whole suite on every commit and `--no-verify` is forbidden, so a deliberately-failing commit is unlandable in this repo. The red is evidenced by transcript rather than by commit, consistent with Plans 04-01 and 04-02.

## Deviations from Plan

### 1. [Rule 3 — Blocking] Task 3 executed FIRST, not last

`test_declared_information_set_matches_the_measured_lookback` reads `get_feature(...).information_set` and asserts the measured sensitivity set is contained in it. `ofi` genuinely depends on the previous L1 update, so against the old declared `t` that test is red — and pre-commit runs the whole suite, so Task 1 could not have been committed with the catalogue still saying `t`. The catalogue edit is pure declaration (every semantic in it was already locked in 04-CONTEXT.md), so moving it ahead of the implementation changes nothing except making each commit landable. Order executed: 3 → 1 → 2.

### 2. [Rule 2 — Missing critical functionality] `STATE_F64_SLOTS` gains `last_ofi`

The plan's slot list names only `prev_bid_price`, `prev_bid_qty`, `prev_ask_price`, `prev_ask_qty`. `ofi` is carried forward between L1 updates and, unlike `mid`/`imb_top`, cannot be reconstructed from `prev_*` — it is the *delta* between the two most recent quotes. Without the slot, `test_batch_and_chunked_resume_are_bit_identical` fails at any chunk boundary between two quotes, and on the real day `ofi`'s NaN count would be 329,581 (one per trade decision row) instead of 1. Committed in `36bddb2`.

### 3. [Rule 3 — Blocking] Ring capacity is `ring_t.shape[0]`, not `RING_CAPACITY`

The plan asks for `& (RING_CAPACITY - 1)` with `RING_CAPACITY` a module-level compile-time constant **and** for `test_ring_overflow_returns_a_status_not_a_wrap` at `cap=8`. Those cannot both hold. The mask works for any power-of-two cap, runtime value or not, so the kernel reads the length from the array: the optimisation survives (mutation (a) proves it is semantics-free), the overflow test exists, and `new_state()` still allocates `RING_CAPACITY`. A cap that is not a power of two is refused with `STATUS_RING_NOT_POWER_OF_TWO` before the first row — `& (cap - 1)` would otherwise corrupt the window silently rather than wrap it cleanly, which is the failure mode T-04-12 names.

### 4. [Rule 2 — Missing critical functionality] `STATUS_ARRAY_LENGTH_MISMATCH`

numba does not bounds-check. An output array one row shorter than the input is a silent out-of-bounds **write**, not a crash. All thirteen array lengths are checked once before the loop.

### 5. [Rule 1] Status codes and `QTY_SCALE_F` live in `reference.py`, and `kernel.py` imports them

The intuitive direction is the reverse. It is forced by the task order — `reference.py` must be green before `kernel.py` exists — and it buys something real: one definition of the float divisor, so the two implementations cannot divide by different constants. `KernelStatusError` subclasses `FeatureStatusError`, which is defined alongside the codes.

### 6. [Rule 1] `tests/fixtures/event_streams.py`, not fixtures inside `tests/features/`

Fourth instance of the phase's recurring lesson. `tests/features/` must not be a package (it would shadow `mvp/features/`), so shared builders go in `tests/fixtures/`, the pattern Plan 04-02 established.

### 7. Crossed/locked books are counted, not refused — 04-RESEARCH-NOTES.md ambiguity #7 resolved

D-04-16 LOCKED the zero-size case as an assertion. The research note's prose extended "assertion, not branch" to crossed books as well, but that was never a decision. Resolved toward Phase 3's existing convention: `mid` and `imb_top` stay arithmetically well-defined on a crossed book, `check_crossed_locked_book` already treats it as an informational per-day count, and hard-failing a feature build on it would make this tier stricter than the curated tier it reads. Counted into `state_i64["crossed_locked_rows"]` for Plan 05 to report. Recorded in the module docstring and the catalogue note as the plan requires.

**Total deviations:** 7 — two blocking-order fixes, two missing-correctness additions, three placements/readings. No architectural change, no Rule 4 checkpoint.

## Threat Flags

None. This plan adds no network endpoint, no auth path, no file access pattern and no schema at a trust boundary; it reads arrays and writes arrays.

## Known Stubs

None. All four catalogued features are computed from real inputs; nothing returns a placeholder, and the anti-vacuity property would fail if one did.

## Surprises

- **The float64 mutation survived the first version of its own test.** The tie-order fixture had 4,000 rows over 3,997 distinct `etime`s — 3 ties in total — so the two orders were the same stream and the test was passing for a reason unrelated to the accumulator. Nothing except the mutation check could have found this: the test was green, the code was right, and the assertion was empty. This is the strongest argument in the plan for mutation-checking a test that already passes.
- **The ring-overflow mutation produced a *correct-looking* column.** On the cap-8 fixture every emitted `trade_flow` value matched the reference exactly while `head == tail` had already destroyed the window. The corruption only became visible one second later, as 4.0 BTC of flow where the truth was 0.0. A test that compared only the values at the overflow row would have passed.
- **The NUMBA_CACHE_DIR threat fired by accident, during the mutation checks, and `git status` never noticed.** Bare `python3 -c` probes are outside pytest, so the conftest pin does not apply; `*.nbc` landed in `mvp/features/__pycache__/`, which is gitignored. The repo-walk assertion was the only observer. Worth carrying: any Phase 4+ script run outside pytest must export `NUMBA_CACHE_DIR` itself.
- **My own vacuity guard caught my own fixture.** `assert whole_state[0][index] > 0` in the resume test failed immediately, because the 300-row random stream had no gap (so no empty window) and no locked quote. The counters were being compared, correctly, against zero. The fixture now has a 16-second gap and a locked quote.
- **`ofi` NaN = 3 over the merged stream, 1 at decision rows.** Two trades share the day's first `etime` with the first quote. Both numbers are right; only one of them is the one the research note measured, and the difference is exactly what the decision-row rule is for.
- **The QuestDB cross-check reproduced to three significant digits** — 2.842e-14 here against 2.84e-14 in the research note, on the same day, from an implementation written months apart from the benchmark script.

## Issues Encountered

None blocking. The plan's task order had to be inverted (Deviation 1) and three of its interface details had to be widened (Deviations 2–4); all four are recorded above with the evidence that forced them.

## Next Phase Readiness — handoff notes

- **Plan 04 (labels)** gets `mid` at every row, the `(t-1s, t]` endpoint convention to mirror for its own windows, and `data/time_ns.py`'s `LABEL_HORIZON_NS`. The as-of rule (D-04-05) is its own to implement; nothing here presumes it.
- **Plan 05 (the build)** calls `merge_curated_streams` → `event_arrays` → `run_kernel_checked(events, state=..., out=...)` and selects `decision_row_index`. Four state slots are meant for its `build_stats.json`: `empty_window_rows`, `crossed_locked_rows`, `max_occupancy`, `n_quotes_seen`. `warmup` is already computed and travels with the row; `post_gap_warmup` is Plan 05's to join from Phase 3's `resync_windows` sidecar.
- **Plan 06 (leakage)** inherits a leakage suite with real contents. The Phase 2 scaffold `tests/leakage/test_leakage_scaffold.py` is deliberately left in place for Plan 06 to delete. `_allowed_sources` in the property file is where a fifth feature's `information_set` string needs an executable reading — an unknown string raises rather than passing.
- **Phase 6 (the simulator)** is the row-by-row caller. `test_a_single_row_at_a_time_is_the_simulator_call_shape` already asserts that shape is byte-identical to the batch one; the simulator conforms to `STATE_I64_SLOTS`/`STATE_F64_SLOTS` rather than re-deriving anything.
- **Any script run outside pytest must export `NUMBA_CACHE_DIR`** — the conftest pin does not reach it, and the artifacts land in a gitignored directory where only the repo-walk test can see them.
- **`run_kernel` is the only `@njit` function in the repo.** `check_numba_globals` now reports `1 njit functions` instead of `0`; `test_check_numba_globals_is_green_over_the_whole_package` asserts that string is no longer `0`, so the guardrail cannot go back to passing vacuously.

## Self-Check: PASSED

- `mvp/features/reference.py`, `mvp/features/kernel.py`, `mvp/tests/features/test_reference.py`, `mvp/tests/features/test_kernel.py`, `mvp/tests/leakage/test_feature_information_set.py`, `mvp/tests/fixtures/event_streams.py` — all present on disk.
- `mvp/tests/features/__init__.py` — confirmed absent.
- Commits `76b8104`, `9883f68`, `36bddb2` all present in `git log` on `feature/phase-04-feature-label-engine`.
- `mvp/spec/features.toml`'s four `information_set` values are `t`, `t`, `[prev_l1_update, t]`, `[t-1s, t]`; every `definition` byte-unchanged against `git show 6eeab5f:mvp/spec/features.toml` (the pre-plan ref).
- `find mvp -name '*.nbc' -o -name '*.nbi'` returns nothing.
