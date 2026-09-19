---
phase: 04-feature-label-engine
plan: 05
subsystem: feature-engine
tags: [build, orchestration, write-once, post-gap-warmup, dq-report, real-data, provenance, resync-warmup]

# Dependency graph
requires:
  - phase: 04-feature-label-engine/04-01
    provides: features/event_stream.py's merge_curated_streams / assert_strict_total_order / event_arrays (read-only views) / decision_row_index
  - phase: 04-feature-label-engine/04-02
    provides: features/tier.py's assert_buildable, write_feature_partition (the one NaN -> null conversion point), issue_feature_manifest, load_features; data/dq/feature_checks.py's FEATURE_BUILD_STATS_KEYS and feature_build_stats_path; data/dq/report.py's build_feature_report_rows_for_date
  - phase: 04-feature-label-engine/04-03
    provides: features/kernel.py's new_state / run_kernel_checked / RING_CAPACITY / STATE_I64_SLOTS
  - phase: 04-feature-label-engine/04-04
    provides: features/labels.py's assert_next_day_available, next_day_quote_series, append_next_day_quotes, compute_labels and its flat stats keys
  - phase: 03-data-layer-backfill-ingest-lockbox
    provides: data/store.py's load_curated (hash verification + DQ pause) and by_date_index_path; data/dq/report.py's dq_resync_windows_path and the regenerable report.parquet; data/ingest/curated_build.py's build_curated_day orchestration shape
provides:
  - mvp/features/build.py -- build_features_day, build_features_range, CuratedInputMissingError, the re-exported FEATURE_BUILD_STATS_KEYS
  - Three real feature partitions on the SSD (2026-09-12, 09-13, 09-14; 22,381,684 decision rows, 287 MiB) with git-committed manifests and by-date pointers
  - Three lake/features_meta/.../build_stats.json files carrying every key the feature-tier DQ checks read, plus provenance and per-horizon label detail
  - stream="features" rows in three report.parquet files, all six checks ok on all three days
affects: [04-feature-label-engine/04-06, 04-feature-label-engine/04-07, 05-fold-harness, 08-stage-1-models]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "The ORDER of the gates relative to the first write is the control, and the test asserts the date directory's ABSENCE after a refusal -- asserting only the exception passes with every gate moved to the end"
    - "post_gap_warmup is a vectorised half-open interval join (one searchsorted over the sidecar's window starts), computed once at build time so no consumer re-derives an outage window"
    - "build_stats.json is written BEFORE the manifest and bound to the partition sha256, then rewritten with the manifest_id -- the exact two-write pattern build_curated_day uses, for the exact same crash window"
    - "FEATURE_BUILD_STATS_KEYS is re-exported, never redefined: the contract has one author (data/dq/feature_checks.py) and the build asserts it produced every key before persisting"
    - "One day per process for the real run, so a failure costs one day and the reported peak RSS is that day's own"
    - "Owned copies of the decision rows and the quote series are taken, then the merged frame and the full-length kernel outputs are deleted, BEFORE day D+1 is loaded"

key-files:
  created:
    - mvp/features/build.py
    - mvp/tests/features/test_build.py
    - mvp/tests/fixtures/feature_build.py
    - mvp/data/lake_registry/manifests/BTCUSDT.features/ (3 manifests + 3 by-date pointers)
  modified:
    - mvp/tests/dq/test_checks.py (test_every_real_manifest_still_has_a_known_source rescoped to curated)
  deleted: []

key-decisions:
  - "build_stats.json lives at lake/features_meta/symbol=/date=/build_stats.json (feature_build_stats_path), NOT the plan's curated_meta/stream=features path. data/dq/report.py:427 reads the former; nothing reads the latter."
  - "FEATURE_BUILD_STATS_KEYS is re-exported from data/dq/feature_checks.py rather than redefined with the plan's 18 names. The committed frozenset is the contract Plan 02's checks already read; the plan's extra names are produced as a documented SUPERSET."
  - "A missing curated partition for day D raises CuratedInputMissingError(FileNotFoundError), never NextDayUnavailableError -- build_features_range turns the latter into a skip, and an ingest hole must not be absorbed as 'the most recent day'."
  - "post_gap_warmup is ledger-derived and therefore INCOMPLETE; the labels' null_gap mask is lake-derived and complete. They answer different questions and only one is self-sufficient. Measured: 09-14's data has four L1 silences over 30 s and the sidecar has three windows."
  - "resync_warmup.seconds = 60 is NOT measurable as a stabilisation period from this data, and was not changed. Reported as a follow-up with the measurement behind it."
  - "No feature day degraded, so no features acknowledgement was written. The acknowledgement machinery was proven by the missing -> DQPauseError refusal observed on all three days before the reports were regenerated."

patterns-established:
  - "A mutation can survive because the system is LAYERED, not because the test is weak: moving assert_buildable after the write left two later gates standing. Remove every defence the sentence claims the test covers, or the mutation is measuring the wrong thing."
  - "A whole-day synthetic fixture beats a 40-minute one: a short fixture day hands big_quote_gaps an 84,000-second seam into D+1 and nulls every long-horizon label, which looks exactly like the bug a coverage test exists to catch."

requirements-completed: []
requirements-partial:
  - "FEAT-01: one numba streaming kernel produces every feature on the real lake, state accumulating over every row and decisions emitted on the last row of each etime -- 22.4M decision rows written. The byte-identical-across-training/inference/SIMULATOR claim waits on a simulator (Phase 6/7)."
  - "FEAT-02: all four catalogued L1 microstructure features are computed and written for three real days, each with its catalogue information_set entry. The per-feature leakage proofs that make those entries verified are Plan 06."
  - "FEAT-04: all four labels are written for three real days by the backward as-of rule. The embargo >= horizon CI assertion is Plan 06."
  - "DATA-07: the feature tier now contributes per-filter dropped-event counts, label coverage, quantization, warm-up counts and the as-of delta to report.parquet, and its days pause until acknowledged. Resync warm-up tagging is wired and MEASURED -- and the measurement found the tag's own blind spot (below)."

# Metrics
duration: ~3h
completed: 2026-09-19
---

# Phase 4 Plan 05: The Build Summary

**Three real days of the Binance order book now exist as 22,381,684 decision rows on disk, each carrying four features, four labels and two warm-up flags — and any day that should not have been built is refused before a single byte is written.**

## Performance

- **Duration:** ~3 h
- **Tasks:** 2, plus one small correction commit
- **Tests:** **850 before → 863 after** (+13, all in `tests/features/test_build.py`)
- **Files:** 3 created (+ 6 committed registry JSONs), 1 modified, 0 deleted

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | `build_features_day` — one ordered pipeline, refusals before writes | `12a15cf` | `features/build.py`, `tests/features/test_build.py`, `tests/fixtures/feature_build.py` |
| 2 | The real three-day build, its DQ rows, and the round trip | `938fa19` | `data/lake_registry/manifests/BTCUSDT.features/`, `tests/dq/test_checks.py` |
| — | Docstring named an exception that does not exist | `8e9dde9` | `features/build.py` |

## The three days

| | 2026-09-12 | 2026-09-13 | 2026-09-14 |
|---|---|---|---|
| decision rows | 4,193,137 | **6,864,853** | 11,323,694 |
| merged events | 8,012,065 | **18,576,995** | 42,298,340 |
| L1 rows / trades | 7,220,489 / 791,576 | 17,167,290 / 1,409,705 | 38,609,768 / 3,688,572 |
| partition bytes | 49,504,857 | 89,097,162 | 162,422,157 |
| wall clock | 10.3 s | 23.6 s | 40.0 s |
| peak RSS | 4.27 GB | 4.38 GB | 4.72 GB |
| `max_window_occupancy` | 1,986 | **5,092** | 7,301 |
| `empty_window_rows` | 788,923 | **784,343** | 259,311 |
| `warmup_rows` | 55,844 | 157 | 369 |
| `post_gap_warmup_rows` | 0 | 0 | 20,786 |
| `null_primary_label_rows` | 56,443 (1.346 %) | **0** | 38,565 (0.341 %) |
| `ret_10s_mid` zero fraction | 70.23 % | **43.89 %** | 12.26 % |
| `asof_convention_delta_pct` | 0.128 % | 0.206 % | 0.405 % |
| features DQ verdict | **ok** | **ok** | **ok** |
| manifest id | `1bf9af2e879d…` | `1f10da67ca50…` | `fdbf58ca1def…` |

Total: **22,381,684 decision rows, 287 MiB**. Free space before 845 GiB, after 844 GiB.
Capture daemon PID 72546 before and after: same PID, same `STARTED Wed Sep 16 21:36:29 2026`.

### Every independent cross-check matched, exactly

| quantity (2026-09-13) | expected | measured |
|---|---|---|
| merged event rows | 18,576,995 | **18,576,995** |
| decision rows | 6,864,853 | **6,864,853** |
| `max_window_occupancy` | 5,092 | **5,092** |
| `empty_window_rows` | 784,343 | **784,343** |
| `ofi` NaN count | 1 | **1** (read back as a NULL from the written Parquet) |
| `ret_10s_mid` exactly-zero | 43.9 % of decision rows | **43.889 %** |

The plan's own verification table still reads 0.297 — written before 04-04 measured the decision-row
figure. That was settled before this plan started; 0.439 is correct.

## The finding: `post_gap_warmup` and `null_gap` do not see the same outages

2026-09-14's written decision rows contain **four** L1 silences longer than 30 s. The
`resync_windows.parquet` sidecar has **three** windows. The missing one is
`00:53:56.345 → 00:56:37.116` (160.8 s), and the gap ledger has **no row for it at all** — not a
`merged-silent`, not a `connection-silent`, nothing.

The mechanism, confirmed on real bytes: **trades are archive-sourced and L1 is capture-only.**

| stream | 09-12 | 09-13 | 09-14 | 09-15 |
|---|---|---|---|---|
| `trade` | archive | archive | archive | archive |
| `bookTicker` | capture | capture | capture | capture |

`data.binance.vision` publishes trades and has never published futures `bookTicker`. So when the
capture host sleeps, the trade tape is refilled from the archive afterwards and the book is not.
Counting decision rows strictly inside each L1 silence makes the asymmetry plain:

| date | silence | decision rows inside | of which trades | tagged `post_gap_warmup` |
|---|---|---|---|---|
| 09-12 | 37.4 s | 46 | **46** | 0 |
| 09-12 | 42.5 s | 71 | **71** | 0 |
| 09-14 | 160.8 s | 499 | **499** | 0 |
| 09-14 | 302.4 s | 2,189 | **2,189** | 54 |
| 09-14 | 2894.1 s | 29,058 | **29,058** | 265 |
| 09-14 | 71.5 s | 699 | **699** | 314 |

Every row inside an L1 outage is a real trade wearing a frozen book: `mid` and `imb_top` are the
last quote before the silence, `ofi` is the last quote's, and **both warm-up flags are false**.
Their labels are correctly null (`null_gap`), so Stage 1 will not train on them as targets — but
a consumer filtering on `warmup | post_gap_warmup` alone will keep them as FEATURES.

**`post_gap_warmup` answers "was the capture process down?"; `null_gap` answers "was the data
silent?".** Only the second is derivable from the lake alone, and 04-04 already made
`big_quote_gaps` read the lake for exactly this reason. Phase 5 needs a lake-derived staleness
flag, not the ledger-derived one, if it wants to exclude these rows.

## The measured resync warm-up, against the 60 s placeholder

Phase 3 left `resync_warmup.seconds = 60` for Phase 4 to measure. It is measured here and **not
changed** — a Phase 4 build is the wrong place to retune a Phase 3 threshold.

**The `_etime_approx` error is real and uneven.** Per 09-14 window, the distance from
`gap_end_etime_approx` to the first genuine quote at or after it:

| window | `gap_end_etime_approx` | first real quote | error | rows tagged |
|---|---|---|---|---|
| 1 | 16:00:14.398 | 16:00:19.228 | **+4.830 s** | 3,010 |
| 2 | 16:48:52.215 | 16:48:52.233 | +0.018 s | 4,680 |
| 3 | 16:50:25.335 | 16:50:25.382 | +0.047 s | 13,096 |

Two of three are good to tens of milliseconds. The first is nearly 5 s early, so it tags 5 s of
nothing and covers only 55 s of data. Window 2 is early by 18 ms, which is enough that the two
quotes at 16:48:52.208 and .210 — genuinely the first after a 48-minute outage — fall *before*
the window and are untagged. 3,010 + 4,680 + 13,096 = **20,786**, the day's `post_gap_warmup_rows`.

**Nothing in the data settles over 60 s.** Quote rate recovers to and past the day's 123/s mean
within 10–30 s of each resumption. One-second-return volatility after resumption, against the
day's own `std(ret_1s_mid)` = 7.905e-05:

| resumption | +1 s | +10 s | +30 s | +60 s | +300 s |
|---|---|---|---|---|---|
| 00:56:37 | 0.00× | 0.30× | 0.34× | 0.36× | 0.73× |
| 16:00:19 | 0.00× | 0.32× | 0.89× | 0.89× | 0.89× |
| 16:48:52 | 0.00× | 0.59× | 0.69× | 0.69× | 0.79× |
| 16:50:25 | 0.00× | 0.60× | 0.55× | 0.49× | 0.90× |

Post-gap rows are **quieter** than the day, at every horizon, and creep toward the baseline over
minutes without ever exceeding it. There is no settling transient to wait out.

What the kernel's own state argues for is much smaller: `mid` and `imb_top` are correct at the
first post-gap quote; `ofi` is stale for exactly one quote (it is carried forward by design);
`trade_flow`'s trailing window is fully refreshed after `TRADE_FLOW_WINDOW_NS` = **1 s**.

**Follow-up for Phase 5, not a change here:** 60 s is conservative in the right direction and
arbitrary in magnitude — roughly 60× the only bound this system can justify. Either restate it as
an economic claim about price discovery after a multi-minute silence (and measure that), or
reduce it to the state-machine bound. Whichever, fix the window's LEFT edge first: it should be
the first curated quote at or after `gap_end_etime_approx`, not the approximation itself.

## 2026-09-12 is a partial L1 day, and that is not a defect

Capture's first L1 message is 2026-09-12T06:37:10.882Z; the archive-sourced trade day starts at
00:00:00.002. So the first **55,843 decision rows of 09-12 are trades with no book at all** —
`mid`, `imb_top` and `ofi` NULL, `warmup=true`, labels null (`null_no_mid`). That is the two-regime
dataset behaving exactly as designed, and it is 1.346 % of the day's primary labels, comfortably
inside the 2 % coverage threshold.

Worth naming precisely: on 09-12 `warmup=true` covers **6 h 37 m**, not the kernel's usual first
second. The flag means "the kernel has not seen two quotes yet", and on this day that condition
lasts until the L1 regime starts.

## RED Transcript

`tests/features/test_build.py`, written and run before `features/build.py` existed:

```
$ ./.venv/bin/pytest tests/features/test_build.py -x -q
tests/features/test_build.py:40: in <module>
    from features.build import (
E   ModuleNotFoundError: No module named 'features.build'
ERROR tests/features/test_build.py
1 error in 0.22s
```

**A deliberately-failing commit is not landable in this repo** — the pre-commit
`pytest (tests, via testpaths)` hook runs the whole suite on every commit and `--no-verify` is
forbidden. The red is transcribed, the same accounting Plans 04-01 … 04-04 used.

## Mutation Check Results

Every mutation printed the exact removed and inserted block before running, asserted the target
text appeared exactly once, and restored from an md5-compared backup.

### (a) The plan's literal wording — `assert_buildable` moved to AFTER `write_feature_partition` — **SURVIVES**

```
--- features/build.py: removed ---
    # (2) the holdout refusal, covering D AND D+1, still before any read.
    assert_buildable(symbol, date, next_date, registry_root=registry_root)
--- inserted --- (nothing)
--- features/build.py: removed ---     del frame
--- inserted ---                       del frame
                                       assert_buildable(...)
--- pytest ---
1 passed in 2.13s
exit=0
```

Not because the test is weak — because the system is **layered**. Two later gates still stand:
`write_feature_partition`'s own `assert_not_quarantined([date])` catches the held-out-D case, and
`next_day_quote_series`'s internal `assert_buildable` (04-04's Deviation 5) catches the held-out-D+1
case. Both still fire before any write.

### (a′) The mutation the sentence MEANS — that gate the ONLY one, and after the write

Same move, plus removing the two layered defences:

```
FAILED tests/features/test_build.py::test_pipeline_order_refusals_come_before_any_write
E   AssertionError: quarantined_d: the refusal left a features date directory behind --
    the gate ran after the write, not before it
E   assert not True
E    +  where True = PosixPath('.../lake/features/symbol=BTCUSDT/date=2026-09-13').exists()
```

The exception is still raised. The day is on disk anyway. That is precisely what asserting the
directory's absence buys, and asserting only `pytest.raises` would have missed it.

This is the **fourth** time this phase a mutation has been weaker than the sentence describing it.

### (b) `assert_strict_total_order` dropped at BOTH call sites — and what the silent build looked like

The build calls it explicitly *and* `merge_curated_streams` calls it on its own result; removing
only one changes nothing. Removing both, against two individually-sorted curated partitions whose
merge is ambiguous (a duplicated `(etime, seq)` pair inside the L1 stream):

```
--- what the mis-ordered build produced ---
WROTE a partition: 2 rows, manifest 5c5ba0abdabe
┌─────────────────────┬──────────────────────┬──────────────┬───────┬───────────┬─────┐
│ etime               ┆ decision_source_rank ┆ decision_seq ┆ mid   ┆ imb_top   ┆ ofi │
╞═════════════════════╪══════════════════════╪══════════════╪═══════╪═══════════╪═════╡
│ 1789257600000000000 ┆ 1                    ┆ 0            ┆ 100.1 ┆ -0.333333 ┆ 3.0 │
│ 1789257601000000000 ┆ 0                    ┆ 1            ┆ 100.2 ┆ -0.333333 ┆ 3.0 │
└─────────────────────┴──────────────────────┴──────────────┴───────┴───────────┴─────┘

FAILED ...::test_the_total_order_gate_is_a_runtime_refusal_not_only_a_test
E   Failed: DID NOT RAISE ValueError
1 failed, 9 passed
```

A well-formed, manifest-addressed, hash-verified partition. `ofi` is 3.0 on both rows — the
duplicated quote was differenced against itself and then against its twin, so the second reading
overwrote the first and the carried-forward value is wrong for the rest of the stream. The
decision row at the shared `etime` reports `decision_source_rank=1` (a trade) while carrying the
second quote's `mid`, so the `(etime, source_rank, seq)` tie pin recorded in the artifact is a
lie about which event was the decision. Nothing downstream can tell.

**All nine `tests/features/test_event_stream.py` tests stayed green** with the merge-internal gate
removed — they exercise `assert_strict_total_order` as a function, not as a gate. Only the
build-level test catches its absence. The runtime gate, not the test, is the control.

### (c) One key removed from the persisted stats — `empty_window_rows`

```
FAILED ...::test_build_stats_carry_every_key_the_dq_checks_read
E   AssertionError: build_stats.json is missing ['empty_window_rows'] -- the checks that
    read them would report `failed`, and the day would pause for a reason nobody could
    see from the report
```

### (d) `post_gap_warmup` DERIVED from the row instead of JOINED from the sidecar

`post_gap = decision["warmup"].copy()` instead of the sidecar join:

```
FAILED ...::test_post_gap_warmup_is_tagged_from_the_sidecar
FAILED ...::test_the_tag_follows_the_sidecar_rather_than_the_row
E   Extra items in the right set: 1789258600000000000, 1789258601000000000...
FAILED ...::test_a_date_with_no_sidecar_still_builds_with_every_tag_false
E   assert not True  +  where True = written["post_gap_warmup"].any()
```

The third failure is the useful one: a date with no sidecar at all must have every tag false, and
a derived tag cannot know that.

## Real-Data Verification

Run as `./.venv/bin/python3` scripts with `NUMBA_CACHE_DIR` exported outside the repo, one day per
process, never as a collected test. The capture daemon was live throughout and was never signalled.

**Refusals observed on the real lake:**

```
REFUSED 2026-09-15: NextDayUnavailableError: feature/label build of 2026-09-15 refused: day
2026-09-16 has no curated bookTicker manifest (.../BTCUSDT__bookTicker__2026-09-16.json does not
exist). Day 2026-09-15's long-horizon labels are computed from the prevailing mids of 2026-09-16,
and a feature partition is write-once -- so 2026-09-15 is built once 2026-09-16 has been ingested.

REFUSED 2026-09-13: FileExistsError: feature partition .../features/symbol=BTCUSDT/date=2026-09-13
already has a written part file: part-1789812323488894000.parquet -- partitions are write-once
```

**The DQ pause, observed before the reports carried a features row** (all three days, verbatim
for 09-13):

```
DQPauseError: DQ pause: BTCUSDT.features has unacknowledged day(s): 2026-09-13: missing
(no DQ report generated for this date; findings dq_report=missing; no acknowledgement file).
Add a valid, git-committed acknowledgement JSON (date, symbol, stream, reason, who, when,
acknowledged; e.g. .../dq_acknowledgements/BTCUSDT__features__2026-09-13.json) to proceed.
```

A features manifest with no report row of its own is `missing`, which pauses — so the feature-tier
DQ rows are load-bearing, not decoration. **No acknowledgement was written, because no features day
degraded**: coverage came in at 1.346 % / 0.000 % / 0.341 % against a 2 % threshold, and the other
five checks are informational by design. No threshold was touched.

**Report regeneration did not disturb the curated verdicts** (T-04-24): for all three dates the
non-features rows are `DataFrame.equals()`-identical before and after, 13 rows each, and the
`resync_windows.parquet` sidecars are identical too. Each date gained exactly six `stream="features"`
rows, all `ok`.

**`load_features` round trip, after the manifests were committed:**

```
2026-09-12: LOADED 4,193,137 rows   nulls={mid:55843, imb_top:55843, ofi:55844, trade_flow:0,
                                           ret_10s:56443, ret_1s:56011, ret_1min:58903, ret_10min:87753}
2026-09-13: LOADED 6,864,853 rows   nulls={mid:0, imb_top:0, ofi:1, trade_flow:0, all labels:0}
2026-09-14: LOADED 11,323,694 rows  nulls={mid:0, imb_top:0, ofi:1, trade_flow:0,
                                           ret_10s:38565, ret_1s:33274, ret_1min:54451, ret_10min:221904}
```

**`build_features_range` on the real lake is idempotent:**

```
{'date': '2026-09-12', 'status': 'already_present', 'manifest_id': '1bf9af2e879d'}
{'date': '2026-09-13', 'status': 'already_present', 'manifest_id': '1f10da67ca50'}
{'date': '2026-09-14', 'status': 'already_present', 'manifest_id': 'fdbf58ca1def'}
{'date': '2026-09-15', 'status': 'skipped', 'manifest_id': None, 'reason': 'no next day'}
```

**The immutability guards pass with the new tier present:**

```
$ ./.venv/bin/python3 -m tools.check_no_manifest_rewrite --full
checked 114 manifest(s), mode=full (sha256)                                  exit 0
$ ./.venv/bin/python3 -m tools.check_manifest_id_integrity
checked 114 manifest(s) for id/filename self-consistency                     exit 0
$ ./.venv/bin/python3 -m tools.check_manifest_append_only
PASS: 114 committed manifest(s) append-only against HEAD history             exit 0
```

## Verification Transcript

```
$ ./.venv/bin/pytest tests -q
863 passed in 118.40s                   (850 before this plan)

$ ./.venv/bin/ruff check .                              All checks passed!
$ ./.venv/bin/ruff format --check .                     136 files already formatted
$ ./.venv/bin/python3 -m tools.check_ms_to_ns_site      exit 0
    PASS: exactly one ms-to-ns site at data/capture/parse.py:36
    PASS: 22 seconds-to-ns site(s) in 5 file(s), all allowlisted (59 files scanned)
$ ./.venv/bin/python3 -m tools.check_spec_diff          exit 0
$ ./.venv/bin/python3 -m tools.check_numba_globals      scanned 59 files, 1 njit functions   exit 0
$ ./.venv/bin/python3 -m tools.check_catalogue_completeness  scanned 59 files, 10 call sites exit 0
$ ./.venv/bin/python3 -m tools.check_lockbox_containment exit 0

# every commit: all 15 pre-commit hooks, never --no-verify
```

## TDD Gate Compliance

Task 1's tests were written and run first (transcript above), then `features/build.py`, then the
mutation checks. There is no `test(...)` RED commit and there cannot be one in this repo.

## Deviations from Plan

### 1. [Rule 1 — Bug] `build_stats.json` goes to `features_meta`, not `curated_meta/stream=features`

The plan names `build_stats_path(lake_root, symbol, "features", date)` three times, which resolves
to `lake/curated_meta/symbol=/stream=features/date=/build_stats.json`. **Nothing reads that path.**
`data/dq/report.py:427` reads `feature_build_stats_path(lake_root, symbol, date)` →
`lake/features_meta/symbol=/date=/build_stats.json`, which Plan 02 committed and documented as
"a sibling metadata tier of `features/`, mirroring `curated_meta/`'s relationship to `curated/`".
The plan's own carry-in note agrees (`lake/features_meta/`). Writing to the plan's path would have
produced three `feature_build_stats` `failed` rows and three paused days.

### 2. [Rule 1 — Bug] `FEATURE_BUILD_STATS_KEYS` is re-exported, not redefined

The plan lists 18 key names. The frozenset Plan 02 committed and `tests/dq/test_feature_checks.py`
asserts against has 14, and only 8 names are shared: the plan says `crossed_locked_rows`,
`label_null_counts`, `label_zero_fraction`, `label_std`, `asof_convention_delta_pct`,
`curated_manifest_ids`, `next_day_manifest_id`, `n_events`, `n_quote_rows`; the contract says
`null_primary_label_rows`, `ret_10s_mid_zero_fraction`, `ret_1s_mid_zero_fraction`,
`window_capacity`, `asof_convention_disagreement_rows`.

Redefining the set in `build.py` would have created a second contract that drifts on the first
added key. `build.py` re-exports the committed one, asserts every member is present before
persisting, and produces **every plan-named key as well**, as a documented superset — so both
readings are satisfied and there is still one author.

### 3. [Rule 2 — Missing critical functionality] `CuratedInputMissingError`

A missing by-date pointer for day D's own bookTicker or trade stream originally raised
`NextDayUnavailableError`, which `build_features_range` catches and turns into
`status="skipped", reason="no next day"`. An ingest hole would have been reported as "this is the
most recent day", and a range of days would quietly have become a range of days minus the broken
ones. Now `CuratedInputMissingError(FileNotFoundError)`, which propagates.

### 4. [Rule 3 — Blocking] `test_every_real_manifest_still_has_a_known_source` rescoped to curated

Phase 3's test asserts `len(pointers) == 111` over `rglob("by-date/*.json")` and
`counts == {"capture": 4, "archive": 107}`. Three committed features pointers break both, and
`manifest_source` returns `"unknown"` for every features manifest — a features manifest's
`inputs[]` are manifest JSON paths, not `/source=.../` partition paths, exactly as
`features/tier.py`'s docstring predicted.

Bumping 111 → 114 would have been wrong: the test's claim is about the fail-closed branch of
`check_rtime_plausibility`, which only ever sees curated manifests. The test now counts curated
pointers (still 111, still 4/107), asserts the features pointers ARE `unknown`, and asserts
`"features" not in data.dq.report.STREAMS` — the one loop that reaches the rtime check. A decision
row has no receive clock.

### 5. [Rule 1] `build_features_day`'s docstring named `HoldoutError`, which does not exist

`assert_buildable` raises `QuarantinedDateError`. A caller writing an `except` clause off that
sentence would have caught nothing. Fixed in `8e9dde9`, along with dropping the unused
`features_partition_dir` helper.

### 6. Six extra tests beyond the plan's seven

`test_the_total_order_gate_is_a_runtime_refusal_not_only_a_test` (mutation (b) needs a fixture that
survives `merge_sorted`), `test_the_tag_follows_the_sidecar_rather_than_the_row` and
`test_a_date_with_no_sidecar_still_builds_with_every_tag_false` (the plan's third behaviour split
into its two halves), `test_range_skips_a_built_day_and_a_day_with_no_successor`,
`test_range_propagates_a_refusal_that_is_not_a_missing_successor`, and
`test_a_days_manifest_names_the_curated_manifests_it_was_built_from`.

**Total deviations:** 6 — two plan-vs-code path/name corrections with evidence, one error-type
addition, one blocked Phase 3 test rescoped, one docstring fix, one test expansion. No
architectural change, no Rule 4 checkpoint.

## Threat Flags

None. This plan writes only to the tiers the plan declares writable (`lake/features/`,
`lake/features_meta/`, `lake/dq/`), never opens `capture/` except through the existing read-only
gap-ledger reader inside `data.dq.report`, and adds no network surface, no auth path and no
schema at a trust boundary.

## Known Stubs

**One, and it is not a stub.** `build_stats["window_overflow"]` is `False` in every persisted
build and cannot be otherwise: `run_kernel_checked` RAISES `KernelStatusError` on a ring overflow,
so a build that reached the stats assembly did not have one. The key exists so
`check_feature_window` finds the number it fails on rather than reporting `failed` for a missing
key, and so the claim is recorded per day rather than inferred from the absence of a crash. This
is stated in `_build_stats`'s docstring.

## Surprises

- **Trades survive an outage and the book does not.** The 2894-second "battery sleep" gap contains
  29,058 trade decision rows, because `data.binance.vision` backfills trades and has never
  published futures `bookTicker`. Every one of those rows carries a 48-minute-old `mid` with both
  warm-up flags false. Their labels are null, so Stage 1's targets are safe — their FEATURES are
  not, and no flag in the partition says so.
- **The gap ledger missed a 160.8 s L1 silence entirely.** No `merged-silent` row, no
  `connection-silent` row. The connection was not silent — trades were arriving — so the watchdog
  structurally could not fire, and the sidecar has three windows against the data's four.
- **A layered system makes a mutation survive for the right reason.** The plan's mutation (a)
  passes, and the reason is that two more gates stand behind the one removed. The lesson from
  04-04 generalises: a mutation is a claim about what a test covers, and in a system with defence
  in depth the claim has to name every defence.
- **Removing the total-order assertion left all nine `test_event_stream.py` tests green.** They
  test the function; only the build tests the gate.
- **The 60 s resync warm-up has no support in the data and I left it alone.** Post-gap returns are
  *quieter* than the day at every horizon out to 5 minutes. The only defensible bound from the
  kernel's state is 1 second.
- **Two of the three manifests carry a `-dirty` code hash.** 09-12's is clean; 09-13's and 09-14's
  are `12a15cff…-dirty` because each build process recomputed the hash after the previous day's
  manifest JSON had landed untracked in the repo. `build_curated_range` never had this problem
  because it takes ONE `code_hash` computed before the loop — and `build_features_range` has the
  same signature. It is a calling convention, not a code defect, and the partitions are write-once
  so these two stay as they are.

## Issues Encountered

None blocking. Three test-level corrections during Task 1 (the holdout exception type, a fixture
whose four-combination claim was unreachable, and a `curated_manifest_ids` shape) and one Phase 3
test rescoped during Task 2.

## Next Phase Readiness — handoff notes

- **T-04-09, carried forward:** a features partition written before its date is declared
  held out **stays readable on disk**. `load_features` refuses it, but the refusal covers the code
  path, not the bytes. Moving or deleting the partition is part of Phase 5's declaration step;
  no read-time gate can do it.
  **CORRECTED 2026-09-19 (04-REVIEW.md CR-01):** the partition Phase 5 must move when it declares
  day `X` held out is **`features/date=X-1`**, not (only) `features/date=X`. Day `D`'s label tail
  stores day `D+1`'s prevailing mids, recoverable as `mid_t * (1 + ret_10min_mid)` to 1.5e-11 USDT
  — 77,962 such rows measured in `date=2026-09-14` for 2026-09-15. `load_features` now refuses on
  the partition dates **and** their `D+1` tail days (`features.tier.refused_dates_for`).
- **Phase 5 must not use `post_gap_warmup` as its staleness filter.** It tags capture-process
  outages the ledger saw. The rows that matter — trades carrying a frozen book through an L1
  silence — are invisible to it and visible to the lake-derived gap rule. A lake-derived
  `stale_book` flag (or simply "the primary label is null") is the correct filter.
- **The feature tier grows one day at a time.** 2026-09-15 becomes buildable the moment a curated
  2026-09-16 exists; the refusal is a passing test and a real observed refusal, not a convention.
  `build_features_range` is idempotent and safe to re-run daily.
- **Give `build_features_range` one `code_hash` per RUN, not per day** — or commit each manifest
  before building the next — or every day after the first gets a `-dirty` provenance tag.
- **The three days at a glance for fold design:** 22,381,684 decision rows total; 09-13 is the
  clean day (zero null labels), 09-14 is the outage day (0.34 % of primary labels null, 20,786
  post-gap rows), 09-12 is the partial-L1 day (1.35 % null, 55,843 rows with no book at all and
  `warmup=true` for 6 h 37 m). All three are `ok` on every feature-tier DQ check.
- **`ret_10s_mid`'s point mass at zero is not stable across days:** 70.2 % / 43.9 % / 12.3 % on
  09-12 / 09-13 / 09-14. It tracks L1 density, not the target's nature. A loss function tuned on
  one day's quantisation will be wrong on the next.
- **Plan 06's leakage suite** inherits 04-04's split information set unchanged; nothing here
  narrows or widens it.
- **Follow-up filed above:** `resync_warmup.seconds = 60` measured and left as-is, with its left
  edge (`gap_end_etime_approx`, up to 4.8 s early) flagged as the part to fix first.

## Self-Check: PASSED

- `mvp/features/build.py`, `mvp/tests/features/test_build.py`, `mvp/tests/fixtures/feature_build.py`
  — all present on disk.
- `mvp/tests/features/__init__.py` — confirmed absent.
- Three feature partitions present on the SSD at the paths their manifests name, with the recorded
  sizes (49,504,857 / 89,097,162 / 162,422,157 bytes).
- Three manifests + three by-date pointers present under
  `mvp/data/lake_registry/manifests/BTCUSDT.features/` and committed.
- Three `build_stats.json` files present under `lake/features_meta/symbol=BTCUSDT/date=*/`.
- Commits `12a15cf`, `938fa19`, `8e9dde9` all present in `git log` on
  `feature/phase-04-feature-label-engine`.
- `find mvp -name '*.nbc' -o -name '*.nbi'` returns nothing.
- Capture daemon PID 72546 unchanged, same start time, never signalled.
