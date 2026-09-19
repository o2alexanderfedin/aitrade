---
phase: 04-feature-label-engine
plan: 04
subsystem: feature-engine
tags: [labels, as-of, searchsorted, null-reasons, day-boundary, write-once, hypothesis, catalogue, quantization]

# Dependency graph
requires:
  - phase: 04-feature-label-engine/04-01
    provides: data/time_ns.py's LABEL_HORIZON_NS / NS_PER_SECOND / NS_PER_DAY as pre-multiplied ints; features/event_stream.py's merge_curated_streams / event_arrays (read-only views) / decision_row_index / project_bookticker
  - phase: 04-feature-label-engine/04-02
    provides: features/tier.py's assert_buildable (the holdout gate covering D AND D+1) and the write-once partition rule; data/dq/feature_checks.py's FEATURE_BUILD_STATS_KEYS contract and label_gap/feature_quantization/feature_label_coverage thresholds
  - phase: 04-feature-label-engine/04-03
    provides: features/kernel.py's run_kernel_checked -- the ONE implementation of `mid`, used for both day D's and day D+1's quote series
  - phase: 03-data-layer-backfill-ingest-lockbox
    provides: data/store.py's by_date_index_path (the curated by-date pointer) and load_curated (hash verification + DQ pause); data/holdout.py's assert_not_quarantined
provides:
  - mvp/features/labels.py -- PRIMARY_LABEL, NULL_REASONS, LabelInputError, big_quote_gaps, default_gap_threshold_ns, compute_labels, NextDayUnavailableError, next_utc_date, assert_next_day_available, append_next_day_quotes, next_day_quote_series
  - The label stats dict, emitting the flat keys data/dq/feature_checks.py already declared (null_primary_label_rows, ret_10s_mid_zero_fraction, ret_1s_mid_zero_fraction, asof_convention_disagreement_rows) plus a per-horizon breakdown split by null REASON
  - data/dq/checks.py's LabelGapThresholds.max_quote_gap_ns -- the threshold on the project's clock, converted in the one allowlisted file
  - spec/labels.toml's four information_set/notes values, pinned to the convention, the null rules, the day-boundary rule and the MEASURED quantization on the row set the tier actually writes
affects: [04-feature-label-engine/04-05, 04-feature-label-engine/04-06, 05-fold-harness, 08-stage-1-models]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Two searchsorted calls per horizon over the combined D + D+1 quote array; no Python loop over rows, and the gap test is one more searchsorted over the gap starts rather than a per-row scan"
    - "Null reasons with a stated PRECEDENCE (no_mid -> past_end -> gap) so the three counts partition the NaNs and a test can assert they sum"
    - "The past-end guard is explicit because searchsorted cannot express it: `idx` happily returns the last quote and the arithmetic succeeds, reporting a carried-forward price as a return"
    - "A label's VALUE and its NULL MASK have DIFFERENT information sets, and the catalogue says so: the value reads mids through t+h, the mask additionally reads arrival TIMES through t+h+max_quote_gap -- a direction that can only remove a label, never change a finite one"
    - "The seconds->ns conversion for a CONFIG threshold lives on the threshold dataclass in the one allowlisted file, not as a constant in data/time_ns.py: the TOML is the source of truth and a constant would be a second one"
    - "The day-boundary gate reads the by-date POINTER, never a glob -- a partition file with no manifest is not data this pipeline can read, and a glob would call it available"
    - "Every measured label statistic NAMES ITS ROW SET; per L1 update and per decision row differ by 14 percentage points of zero fraction on the same day"

key-files:
  created:
    - mvp/features/labels.py
    - mvp/tests/features/test_labels.py
    - mvp/tests/features/test_label_day_boundary.py
  modified:
    - mvp/data/dq/checks.py (LabelGapThresholds.max_quote_gap_ns)
    - mvp/data/dq/feature_checks.py (check_feature_asof_convention docstring: row set named)
    - mvp/spec/labels.toml (information_set + notes on all four labels; every `computation` byte-unchanged)
    - mvp/spec/dq_thresholds.toml (feature_quantization, feature_label_coverage, label_gap notes)
    - mvp/spec.md (re-rendered label and DQ-threshold tables)
  deleted: []

key-decisions:
  - "The catalogue's quantization numbers were WRONG for the column the tier writes, and are now corrected: ret_10s_mid is exactly zero on 43.9% of DECISION ROWS, not the 29.7% 04-RESEARCH-NOTES.md measured per L1 UPDATE. Both are real, both are 2026-09-13; the research row set oversamples busy milliseconds at 2.5 quotes per distinct etime. Phase 5/8 pick a loss function off this number."
  - "The seconds->ns conversion for label_gap lives on LabelGapThresholds as a `max_quote_gap_ns` property in data/dq/checks.py (already allowlisted). A `30 * NS_PER_SECOND` in features/labels.py would be a second seconds-to-ns site and check_ms_to_ns_site fails on it; a pre-multiplied constant in data/time_ns.py would be a second source of truth for a value that comes from config."
  - "The gap rule is OVERLAP, and the two comparisons are STRICT. A gap ending exactly at t or starting exactly at t+h touches the window without leaving anything inside it unknown; a gap straddling t leaves the first half unknown and containment would miss it."
  - "The leakage property the plan asked for is literally false as worded, and is split in two. Perturbing the MIDS of quotes after t+h cannot change a label (T-04-15). Perturbing their ETIMES can toggle a label's existence -- the null mask reads arrivals up to t+h+max_quote_gap -- but never turns one finite label into a different finite label. Both are properties; the catalogue's information_set states both directions."
  - "next_day_quote_series loads D+1 through store.load_curated rather than reading parquet, so the holdout refusal, the partition hashes and D+1's own DQ pause all apply; mid comes from features.kernel, never from a second (bid+ask)/2."
  - "The whole D+1 day is loaded, not its first 10 minutes. The slice is a legitimate optimisation and is deliberately not taken: it would need its own proof that sliced and unsliced agree, and it saves ~1.5 s of a build that runs once per day."
  - "PRIMARY_LABEL is get_label(\"ret_10s_mid\").name, not a bare string and not an import from features.tier: the catalogue is the authority, the name is a literal (check_catalogue_completeness's rule), and labels.py owes nothing to the lake tier it feeds."

patterns-established:
  - "A mutation whose WORDING is minimal may not be the mutation that matters: `side=\"right\"` -> `side=\"left\"` keeping the `-1` leaves the plan's named test green, because on that fixture both spellings name the same quote. Run the mutation the SENTENCE means, not only the one the diff suggests."
  - "When a real-data number disagrees with the plan, the first hypothesis to test is 'which rows was the original measured over', before any threshold is touched"

requirements-completed: []
requirements-partial:
  - "FEAT-04: all four catalogued labels are computed by a declared backward as-of rule, are null in exactly the three cases that should be null, and carry their measured quantization into the catalogue. The embargo >= horizon CI assertion and the labels' arrival in a written partition are Plans 05/06."

# Metrics
duration: ~2h
completed: 2026-09-19
---

# Phase 4 Plan 04: Labels Summary

**Every decision row now carries what the mid did over the next second, ten seconds, minute and ten minutes — or an explicit nothing, when the answer would have been the last known price repeated back.**

## Performance

- **Duration:** ~2 h
- **Tasks:** 2, plus one correction commit the real-data run forced
- **Tests:** **817 before → 850 after** (+33: 22 `test_labels.py`, 11 `test_label_day_boundary.py`)
- **Files:** 3 created, 5 modified, 0 deleted

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | Backward as-of labels, and the three ways a label is null | `a216775` | `features/labels.py`, `tests/features/test_labels.py`, `data/dq/checks.py` |
| 2 | The day-boundary rule, and the label catalogue | `473a9d9` | `features/labels.py`, `tests/features/test_label_day_boundary.py`, `spec/labels.toml`, `spec.md` |
| — | Row-set correction forced by the real-data run (Deviation 1) | `f9aaf57` | `spec/labels.toml`, `spec/dq_thresholds.toml`, `data/dq/feature_checks.py`, `features/labels.py`, `spec.md` |
| — | This summary + STATE/ROADMAP | `31d4449` | `.planning/phases/04-feature-label-engine/04-04-SUMMARY.md`, `.planning/STATE.md`, `.planning/ROADMAP.md` |

## The finding that changes what Phase 5/8 will read

`ret_10s_mid` is **exactly zero on 43.9 % of the rows this pipeline writes**, not the 29.7 % the
catalogue said. Both numbers are real, both are 2026-09-13, and neither is a bug:
04-RESEARCH-NOTES.md measured per **L1 update** (17,167,290 rows, 2.5 quotes per distinct `etime`),
and the feature tier writes one row per **decision `etime`** (6,864,853). Per-update sampling
over-weights busy milliseconds, where the price actually moves — so it shows fewer zeros and a
larger std.

Before changing a single number, the research row set was reproduced bit-for-bit with this
implementation, which is what turns "the numbers disagree" into "the numbers describe different
rows":

| Statistic (2026-09-13, h=10 s) | Per L1 update — measured here | 04-RESEARCH-NOTES.md | Per DECISION ROW — what the tier writes |
|---|---|---|---|
| exact-zero fraction | **29.73 %** | 29.7 % | **43.89 %** |
| std | **1.853e-04** | 1.85e-04 | **1.447e-04** |
| rows with no `t+h` in the day | **1,086 (0.0063 %)** | 1,086 (0.0063 %) | **507 (0.0074 %)** |
| prevailing-vs-next disagreement | **0.263 %** | 0.26 % | **0.206 %** |
| `ret_1s_mid` zero fraction | **62.50 %** | 62.5 % | **80.80 %** |
| `ret_10min_mid` rows with no `t+h` | **116,418 (0.678 %)** | 116,418 (0.68 %) | **45,159 (0.658 %)** |

All eight research figures reproduce to their quoted precision. The catalogue, the DQ threshold
notes and the code docstrings now lead with the decision-row number and keep the research one
beside it, labelled — because the research number is quoted in three other documents and a reader
arriving from there was otherwise 14 percentage points out.

## Accomplishments

- **The as-of rule is a declared number, not an emergent property of a join.** `mid_{t+h}` is the
  prevailing mid — `searchsorted(..., side="right") - 1`, exact matches allowed, and on a tie group
  the LAST update of that `etime`. The convention it is not is counted (14,169 decision rows at
  h=10 s) and reported as a DQ number rather than stored as an uncatalogued second column.
- **Three null reasons that partition.** `null_no_mid` → `null_past_end` → `null_gap`, in that
  precedence, so the counts sum to the NaNs and a test asserts they do. The past-end guard exists
  because `searchsorted` cannot express it: the index happily returns the last quote and the
  arithmetic succeeds.
- **The day boundary is a refusal.** `assert_next_day_available` reads the curated by-date pointer
  and refuses day D until D+1 exists. Derived rather than transcribed, the buildable feature days
  are **2026-09-12, 2026-09-13 and 2026-09-14** — never the most recent day.
- **The cross-day read is load-bearing and proven so.** With the D+1 tail, 2026-09-13 has **zero**
  null labels at every horizon; without it, 45,159 rows lose `ret_10min_mid`.
- **The gap rule fired for real, on the day it was written for.** 2026-09-14's four battery-sleep
  outages (2894.1 s, 302.4 s, 160.8 s, 71.5 s) null 38,565 of 11,323,694 primary labels — 0.34 %,
  inside the 2 % coverage threshold, which is the threshold behaving as designed rather than
  needing a tune.
- **`mid` still has one implementation.** Day D+1's quote series runs through the same
  `run_kernel_checked` as day D; nothing in `labels.py` computes `(bid + ask) / 2`.

## RED Transcripts

`tests/features/test_labels.py`, written and run before `features/labels.py` existed:

```
$ ./.venv/bin/pytest tests/features/test_labels.py -x -q
tests/features/test_labels.py:37: in <module>
    from features.labels import (
E   ModuleNotFoundError: No module named 'features.labels'
ERROR tests/features/test_labels.py
1 error in 0.30s
```

`tests/features/test_label_day_boundary.py`, before Task 2's functions existed:

```
tests/features/test_label_day_boundary.py:31: in <module>
    from features.labels import (
E   ImportError: cannot import name 'NextDayUnavailableError' from 'features.labels'
ERROR tests/features/test_label_day_boundary.py
1 error in 0.29s
```

**A deliberately-failing commit is not landable in this repo** — the pre-commit `pytest (tests, via
testpaths)` hook runs the whole suite on every commit and `--no-verify` is forbidden. The red is
transcribed rather than preserved as a RED commit, the same accounting Plans 04-01 … 04-03 used.

## Mutation Check Results

Every mutation printed the exact block removed and the exact block inserted **before** running the
suite, asserted the target text appeared exactly once, and restored from an md5-compared backup.

### Task 1 — `features/labels.py`

**(a) the plan's literal wording — `side="right"` → `side="left"`, keeping the `-1` — SURVIVES its
named test.** On quotes at `t+9s`/`t+11s` both spellings name the 9 s quote, so
`test_label_uses_the_prevailing_quote_not_the_next_one` stays green. What dies is the exact-match
pair:

```
FAILED tests/features/test_labels.py::test_exact_match_at_t_plus_h_is_used
E   assert np.float64(0.0) == 0.02 ± 2.0e-08
FAILED tests/features/test_labels.py::test_exact_match_takes_the_LAST_quote_of_the_tie_group
E   AssertionError: the tie group's LAST mid is 103.0
E   assert np.float64(0.0) == 0.03 ± 3.0e-08
2 failed, 1 passed
```

**(a′) the mutation the sentence MEANS — the real next-quote convention (`side="left"`, no `-1`)** —
kills the named test:

```
FAILED tests/features/test_labels.py::test_label_uses_the_prevailing_quote_not_the_next_one
FAILED tests/features/test_labels.py::test_exact_match_takes_the_LAST_quote_of_the_tie_group
E   assert np.float64(0.01) == 0.03 ± 3.0e-08
2 failed
```

Recorded because it is the second time in this phase a mutation has been weaker than the sentence
describing it. A mutation is a claim about what a test covers; the diff has to match the claim.

**(b) the `t+h > quote_etime[-1]` guard removed — and this is what a carried-forward price looks
like:**

```
FAILED tests/features/test_labels.py::test_label_is_null_past_the_end_of_available_data
E   AssertionError: row 11's horizon ends past the last quote -- a null, never the
    carried-forward last mid 120.0
E   assert False +  where False = isnan(np.float64(0.08108108108108109))

FAILED tests/features/test_labels.py::test_a_label_is_null_even_when_the_last_quote_is_the_prevailing_one
E   AssertionError: without the past-end guard this row reports +50% from a quote 5s old
E   assert False +  where False = isnan(np.float64(0.5))
2 failed
```

`+8.1 %` and `+50 %` are not error values — they are plausible returns, with the right sign and a
believable magnitude, manufactured out of a price that simply stopped being updated. That is
exactly the "carried-forward price" D-04-05 forbids, and nothing downstream could tell.

**(c) overlap → containment (`gap_start >= t and gap_end <= t+h`):**

```
FAILED tests/features/test_labels.py::test_the_gap_rule_is_overlap_not_containment
E   AssertionError: the gap starts before t and ends inside [t, t+60s]; containment
    would not see it
E   assert False +  where False = isnan(np.float64(0.51))
1 failed, 3 passed
```

The three surviving tests are the two endpoint tests and the 31s/29s pair — correct: containment and
overlap agree on a gap strictly inside the window. Only the straddling fixture separates them, which
is why it exists.

**(d) `0.0` instead of null when `mid_t` is NaN:**

```
FAILED tests/features/test_labels.py::test_label_is_never_zero_by_default
E   Failing test case: problem=(array([0]), array([nan]), ...)
FAILED tests/features/test_labels.py::test_label_is_null_when_mid_at_t_is_null
FAILED tests/features/test_labels.py::test_null_reasons_partition_the_nulls
E   assert 2 == 3
3 failed
```

The partition test catching it is the useful part: a reason count that no longer sums to the NaNs is
a second, independent witness that a null turned into a number.

### Task 2

**(a) `assert_next_day_available` accepts a missing pointer:**

```
E   Failed: DID NOT RAISE NextDayUnavailableError
FAILED ...::test_build_refuses_a_day_whose_successor_has_no_curated_manifest
FAILED ...::test_the_buildable_days_are_exactly_the_days_with_a_successor
E   AssertionError: assert ['2026-09-12'... '2026-09-15'] == ['2026-09-12'... '2026-09-14']
E     Left contains one more item: '2026-09-15'
2 failed
```

**(b) the D+1 quote series comes back empty (the cross-day read dropped):**

```
FAILED ...::test_next_day_quote_series_comes_back_as_etime_and_mid
E   assert [] == [1789344000000000000, 1789344001000000000]
1 failed
```

**(c) the seam check removed from `append_next_day_quotes`:**

```
E   Failed: DID NOT RAISE ValueError
FAILED ...::test_append_next_day_quotes_refuses_an_out_of_order_seam
1 failed
```

**(d) D+1 appended to the DECISION rows as well as the quote series — applied to the TEST, not to
production code**, because no production function assembles decision rows until Plan 05; the
protection today is the shape of the API (`append_next_day_quotes` takes and returns quote series
and has no way to say otherwise):

```
FAILED ...::test_next_day_quotes_are_appended_not_merged_as_decision_rows
E   AssertionError: one label per decision row of day D -- appending D+1 to the
    DECISION rows would make this 5
E   assert 5 == 3
1 failed
```

### The `check_spec_diff` guard was observed biting

```
### MUTATION: one space appended to ret_10s_mid's `computation`
--- removed ---
computation = "(mid_{t+10s} - mid_t) / mid_t"
--- inserted ---
computation = "(mid_{t+10s} - mid_t) / mid_t "

FAIL: mvp/spec.md's catalogue tables drifted from the TOML source: ...
FAIL: definition/computation changed under an existing name -- give it a new name instead:
  labels (computation changed): ['ret_10s_mid']
exit=1
# reverted -> exit=0, md5 identical
```

Every `computation`, `horizon`, `embargo`, `version` and `introduced` field was additionally verified
byte-unchanged against `git show 96dd371:mvp/spec/labels.toml`, field by field. Only
`information_set` and `notes` moved on all four labels.

## Real-Data Verification (read-only)

Run once as a script under `./.venv/bin/python3` with `NUMBA_CACHE_DIR` exported outside the repo,
through the real `load_curated` path with hash verification and the DQ pause active. Never a
collected test. The capture daemon (PID 72546) was live throughout.

### 2026-09-13 — 6,864,853 decision rows, 17,167,290 L1 rows, 1,409,705 trades

| | labelled | null | no_mid | past_end | gap | zero frac | std | as-of disagree |
|---|---|---|---|---|---|---|---|---|
| **WITHOUT the D+1 tail** |
| `ret_10s_mid` | 6,864,346 | 507 | 0 | 507 | 0 | 43.89 % | 1.447e-04 | 14,169 (0.206 %) |
| `ret_1s_mid` | 6,864,828 | 25 | 0 | 25 | 0 | 80.80 % | 4.761e-05 | 13,797 (0.201 %) |
| `ret_1min_mid` | 6,861,512 | 3,341 | 0 | 3,341 | 0 | 13.58 % | 3.300e-04 | 14,012 (0.204 %) |
| `ret_10min_mid` | 6,819,694 | 45,159 | 0 | 45,159 | 0 | 0.29 % | 8.695e-04 | 11,238 (0.165 %) |
| **WITH the D+1 tail (38,609,768 more L1 rows)** |
| `ret_10s_mid` | 6,864,853 | **0** | 0 | 0 | 0 | 43.89 % | 1.447e-04 | 14,173 (0.206 %) |
| `ret_1s_mid` | 6,864,853 | **0** | 0 | 0 | 0 | 80.80 % | 4.761e-05 | 13,797 (0.201 %) |
| `ret_1min_mid` | 6,864,853 | **0** | 0 | 0 | 0 | 13.58 % | 3.299e-04 | 14,018 (0.204 %) |
| `ret_10min_mid` | 6,864,853 | **0** | 0 | 0 | 0 | 0.29 % | 8.724e-04 | 11,385 (0.166 %) |

- `decision_mid` NaN count: **0** (the day's first merged row is a quote).
- L1 inter-quote gaps **> 1 s: 27**; **> 30 s: 0** — so on this day the gap rule correctly never
  fires, and `null_gap` is 0 at every horizon. The rule has **no real-data evidence on 09-13**;
  09-14 below is where it is exercised.
- Label ranges: `ret_10s_mid` ∈ [−0.00243, +0.00286], `ret_10min_mid` ∈ [−0.00631, +0.00328]. No
  infinities at any horizon.
- Mid price level p50 **76,975.05 USDT** over 21,306 distinct `mid` values — the half-tick lattice
  D-04-17 describes.
- Wall clock: 23.0 s end to end including both `load_curated` calls and the 38.6M-row D+1 load.

### 2026-09-14 — the outage day, 11,323,694 decision rows

| | labelled | null | past_end | **gap** | zero frac | std |
|---|---|---|---|---|---|---|
| `ret_10s_mid` (with tail) | 11,285,129 | 38,565 | 0 | **38,565** | 12.26 % | 2.490e-04 |
| `ret_1s_mid` | 11,290,420 | 33,274 | 0 | **33,274** | 59.57 % | 7.905e-05 |
| `ret_1min_mid` | 11,269,243 | 54,451 | 0 | **54,451** | 0.78 % | 6.249e-04 |
| `ret_10min_mid` | 11,101,790 | 221,904 | 0 | **221,904** | 0.04 % | 1.632e-03 |

Four L1 gaps over 30 s — **2894.1 s, 302.4 s, 160.8 s, 71.5 s** — the battery-sleep outages
`data/capture/power.py` documents. They null **0.34 %** of the day's primary labels and **1.96 %** of
its 10-minute ones. That is under `feature_label_coverage.degraded_missing_pct = 2.0` for the primary
label: the day is usable and degraded, not unusable, and no threshold needed touching. 09-14's own
DQ acknowledgement (already committed) is what let `load_curated` read it at all.

## Verification Transcript

```
$ ./.venv/bin/pytest tests -q
850 passed in 115.14s                   (817 before this plan)

$ ./.venv/bin/ruff check .                              All checks passed!
$ ./.venv/bin/ruff format --check .                     133 files already formatted
$ ./.venv/bin/python3 -m tools.check_ms_to_ns_site      exit 0
    PASS: exactly one ms-to-ns site at data/capture/parse.py:36
    PASS: 22 seconds-to-ns site(s) in 5 file(s), all allowlisted (58 files scanned)
$ ./.venv/bin/python3 -m tools.check_spec_diff          exit 0
$ ./.venv/bin/python3 -m tools.check_numba_globals      scanned 58 files, 1 njit functions   exit 0
$ ./.venv/bin/python3 -m tools.check_catalogue_completeness  scanned 58 files, 10 call sites  exit 0
$ ./.venv/bin/python3 -m tools.check_lockbox_containment exit 0

$ find mvp -name '*.nbc' -o -name '*.nbi'
(nothing)

# every commit: all 15 hooks, never --no-verify
```

## TDD Gate Compliance

Each task's tests were written and run first (transcripts above), then the implementation, then the
mutation checks. There is no `test(...)` RED commit and there cannot be one in this repo; the red is
evidenced by transcript.

## Deviations from Plan

### 1. [Rule 1 — Bug] The catalogue's measured numbers described a row set the tier does not write

The plan's `<real_data_verification>` block predicts zero fractions of 62.5 / 29.7 / 8.2 / 0.2 % and
stds of 6.88e-05 / 1.85e-04 / 3.83e-04 / 9.53e-04 "over the 6,864,853 decision rows", and calls a
material disagreement a STOP. The measured decision-row figures are 80.8 / 43.9 / 13.6 / 0.29 % and
4.761e-05 / 1.447e-04 / 3.299e-04 / 8.724e-04.

It is a STOP, and the stop resolved into an attribution error rather than a defect: rerunning the
same code over the **L1-update** row set reproduces all eight research figures exactly (table above).
Task 2 had already committed the research numbers into `spec/labels.toml` as properties of the
label column, so commit `f9aaf57` corrects them there and in three other places that would have
contradicted the first real DQ row: `dq_thresholds.toml`'s `feature_quantization`,
`feature_label_coverage` and `label_gap` notes (which describe `build_stats.json` values this plan's
code now produces), `data/dq/feature_checks.py`'s `check_feature_asof_convention` docstring, and
`features/labels.py`'s own. `04-CONTEXT.md` D-04-17 is left as written — it is the record of what was
decided — and the correction is carried into STATE.md instead.

Files outside the plan's `files_modified`: `spec/dq_thresholds.toml` and `data/dq/feature_checks.py`
(Rule 2 — a threshold note that contradicts the number the check will read is a correctness problem,
not a documentation one).

### 2. [Rule 3 — Blocking] `LabelGapThresholds.max_quote_gap_ns`

`dq_thresholds.toml` carries `max_quote_gap_seconds = 30`, and the label rule needs nanoseconds.
Writing `* NS_PER_SECOND` in `features/labels.py` is a seconds-to-ns site in a file that is not on
`ALLOWLISTED_SEC_TO_NS_SITES`, and `check_ms_to_ns_site` resolves values across modules, so binding
the literal to a name would not hide it either. `data/time_ns.py`'s pattern — a pre-multiplied
constant — does not fit a value that comes from config: the TOML is the source of truth and the
constant would be a second one. So the conversion went onto the threshold dataclass in
`data/dq/checks.py`, which is already allowlisted and already converts the rtime-skew bounds the
same way.

### 3. [Rule 1] The plan's leakage property is false as worded, and is split in two

"Perturbing any quote with `etime > t+h` cannot change the label at `t`" does not hold, and should
not: a gap that BEGINS at `t+h` and runs longer than `max_quote_gap` is only knowable from the next
arrival, which is up to 30 s past `t+h`. Two properties replace it —
`test_perturbing_a_quote_after_t_plus_h_cannot_change_a_label` (mid VALUES; this is T-04-15) and
`test_perturbing_an_etime_after_t_plus_h_only_toggles_nullness` (arrival TIMES may add or remove a
label, but never turn one finite label into a different finite label). The asymmetry is now stated
in the catalogue's `information_set` for all four labels rather than left as an unrecorded
exception.

### 4. [Rule 2 — Missing critical functionality] Input guards and an anti-vacuity property

`compute_labels` refuses an unsorted or empty quote series and a non-positive `mid_t`
(`searchsorted` over an unsorted array is silently wrong; an empty series would surface as an
`IndexError` from the middle of the arithmetic; a `mid_t` of 0 would divide to an `inf` that reads
as a return). `append_next_day_quotes` refuses an out-of-order seam — the one join here where two
individually-sorted arrays can produce an unsorted one.
`test_perturbing_the_prevailing_quote_DOES_change_the_label` is the anti-vacuity counterpart the two
invariance properties need, following 04-03's lesson.

### 5. [Rule 1] `next_day_quote_series` calls `tier.assert_buildable` itself

T-04-18 says "this plan's build path calls it before any load". The load is inside
`next_day_quote_series`, so the gate is too — before the pointer is even dereferenced. A caller that
forgot would otherwise read a held-out D+1's prices into D's labels.

### 6. Two extra tests beyond the plan's eight + four

`test_the_buildable_days_are_exactly_the_days_with_a_successor` derives the 09-12/09-13/09-14 list
rather than restating it, and `test_the_tail_is_needed_for_every_horizon_in_the_catalogue` shows the
tail matters for the primary 10 s label too, not just the 10-minute diagnostic.

**Total deviations:** 6 — one real correction with evidence, one conversion-site placement, one
property restated to be true, two guard additions, one gate moved earlier. No architectural change,
no Rule 4 checkpoint.

## Threat Flags

None. This plan reads arrays, reads one extra curated manifest through the existing loader, and
writes nothing to the lake.

## Known Stubs

None. All four labels are computed from real inputs on real data; the anti-vacuity property would
fail if any returned a constant.

## Surprises

- **The plan's own expected numbers were measured on different rows than the plan's own row count.**
  The `<real_data_verification>` block names 6,864,853 decision rows and then lists statistics from
  a 17.2M-row set. Reproducing the research set exactly was the only way to tell the difference
  between "my code is wrong" and "these describe different rows" — and the reproduction agreed to
  four significant figures on all eight figures, which is stronger evidence than the disagreement
  was.
- **The minimal mutation is not always the meaningful one.** `side="right"` → `side="left"` (keeping
  the `-1`) is what the plan literally asks for, and the test it names stays green: on quotes at
  `t+9s`/`t+11s` both spellings pick the 9 s quote. Only dropping the `-1` — the real next-quote
  convention — kills it. A mutation check is a claim about coverage, and the claim has to be the one
  the diff makes.
- **The past-end mutation produced believable returns, not obvious garbage.** `+8.1 %` and `+50 %`,
  right sign, plausible size, from a price that had simply stopped updating.
- **`ret_1s_mid` is 80.8 % zeros on the rows that get written.** The catalogue already said "barely
  a continuous variable" at 62.5 %; at four fifths it is a classification target wearing a
  regression's clothes.
- **The gap rule has no evidence on the day the plan verifies.** 2026-09-13 has zero gaps over 30 s,
  so `null_gap` is 0 everywhere on it. Running 2026-09-14 was not in the plan's verification block
  and is where the rule's only real measurement comes from.

## Issues Encountered

None blocking. The row-set correction (Deviation 1) required a third commit after Task 2 was already
green; nothing had to be reverted.

## Next Phase Readiness — handoff notes

- **Plan 05 (the build)** calls, in order: `assert_next_day_available` → `assert_buildable` →
  `merge_curated_streams` → `run_kernel_checked` → `decision_row_index` →
  `next_day_quote_series` → `append_next_day_quotes` → `compute_labels`. The stats dict already
  carries four of `FEATURE_BUILD_STATS_KEYS` (`null_primary_label_rows`,
  `ret_10s_mid_zero_fraction`, `ret_1s_mid_zero_fraction`, `asof_convention_disagreement_rows`) plus
  `n_decision_rows`; the rest come from the kernel state and the merge stats.
- **Buildable days are 2026-09-12, 2026-09-13 and 2026-09-14.** 09-15 is refused by
  `assert_next_day_available` until 09-16 is ingested, and that refusal is a passing test, not a
  convention.
- **Labels are NaN here and NULL in the partition.** `features/tier.py:write_feature_partition` is
  the single conversion point and already asserts nothing survives it.
- **Phase 5/8: the primary target has a ~44 % point mass at exactly zero** on the rows that will be
  trained on, with std 1.447e-04. Every IC, every Sharpe and the loss function itself have to be
  chosen knowing that. `ret_1s_mid` at 80.8 % zeros should not be trained on at all.
- **The null mask is not a pure function of `[t, t+h]`** — it reads arrival times up to
  `t + h + max_quote_gap`. Plan 06's leakage suite should encode the correct, split property rather
  than the plan's original wording.
- **`04-05-PLAN.md`'s verification table (line 171) still expects `ret_10s_mid` exactly-zero
  0.297, and `04-CONTEXT.md` D-04-17 still says 29.7 % without naming a row set.** Both were
  written before this plan measured the decision-row figure. The build will report **0.439**, and
  that is correct — check the row-set table above before treating the difference as a STOP.
- **Plan 06's leakage suite must learn the two-part information set.** The four labels'
  `information_set` strings now say "data through t+h for the VALUE; quote ARRIVAL TIMES through
  t+h+max_quote_gap decide whether it exists". `tests/leakage/test_feature_information_set.py`'s
  `_allowed_sources` raises on a string it cannot read, and the plan's original one-line property
  ("nothing after t+h can change the label") is FALSE against correct code — see Deviation 3.
- **Loading a real day pair costs ~23 s and peaks around 1.5 GB** (17.2M + 38.6M quotes plus the
  concatenation copy). The D+1 slice optimisation is available and deliberately untaken.

## Self-Check: PASSED

- `mvp/features/labels.py`, `mvp/tests/features/test_labels.py`,
  `mvp/tests/features/test_label_day_boundary.py` — all present on disk.
- `mvp/tests/features/__init__.py` — confirmed absent.
- Commits `a216775`, `473a9d9`, `f9aaf57` all present in `git log` on
  `feature/phase-04-feature-label-engine`.
- `spec/labels.toml`'s four `computation` strings verified byte-identical against
  `git show 96dd371:mvp/spec/labels.toml`; only `information_set` and `notes` differ.
- `find mvp -name '*.nbc' -o -name '*.nbi'` returns nothing.
