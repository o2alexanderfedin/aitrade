---
phase: 03-data-layer-backfill-ingest-lockbox
plan: 03
subsystem: data-lake
tags: [backfill, monthly-zip, join-asof, trade-side, manifest, polars, resumability]

# Dependency graph
requires:
  - phase: 03-data-layer-backfill-ingest-lockbox
    plan: 01
    provides: "data/backfill/client.py (BackfillClient, list_month, download_and_verify, extract_expected_member), data/ingest/normalize.py (normalize_archive_trades, write_raw_partition), data/unit_registry.py"
  - phase: 03-data-layer-backfill-ingest-lockbox
    plan: 02
    provides: "data/ingest/curated_build.py (build_curated_day, single-day/trade-only), data/ingest/trade_side.py (resolve_side, nearest_quote_side, cross_check_agreement), data/store.py (issue_manifest, resolve_manifest, load_curated)"
provides:
  - "data/backfill/downloader.py -- full-window orchestrator: monthly-vs-daily regime dispatch, extract-to-disk + per-UTC-day scan_csv/filter/collect for monthly zips, skip-and-continue per-date idempotency"
  - "data/backfill/client.py:BackfillClient.granularity -- daily|monthly, one download/checksum/marker code path for both regimes (measured live: monthly URL template + content-length match CONTEXT.md exactly)"
  - "data/ingest/normalize.py:normalize_archive_frame -- the single ms_to_ns call site, split out of normalize_archive_trades so the monthly path can normalize an already-day-filtered lazy-scan slice; raw_partition_dir/raw_partition_exists as the one source of truth for the raw-tier path"
  - "data/ingest/curated_build.py:build_curated_range -- idempotent per-UTC-date orchestration over both streams, with already_present and no_source as counted, non-crashing skip statuses"
  - "data/ingest/trade_side.py:nearest_quote_side -- fixed to allow_exact_matches=False (see Deviations); the real, measured trade-side agreement rate for this dataset"
  - "The real full-window archive raw tier: 107 days, 2026-06-01 -> 2026-09-15, 376,171,010 trade rows, 107/107 archive-chosen"
  - "The real curated tier: BTCUSDT.trade (107 days, 107 manifests) and BTCUSDT.bookTicker (4 days, 2026-09-12 -> 2026-09-15, 4 manifests) -- all git-committed"
affects: [03-data-layer-backfill-ingest-lockbox/03-04, 04-feature-engine]

# Tech tracking
tech-stack:
  added: []  # polars + stdlib only
  patterns:
    - "Extract-to-disk + per-UTC-day pl.scan_csv(...).filter(...).collect(), never one in-memory pl.DataFrame for a full month -- measured ~5.1-7.5 GiB peak RSS vs a ~23 GiB naive-read estimate"
    - "Pre-check-before-extract idempotency: ingest_monthly skips the entire 7.6 GB extract if every in-range day is already written, not just each day inside the loop"
    - "join_asof(strategy='backward', allow_exact_matches=False) for nearest-quote classification -- a same-etime quote is excluded, not just a same-etime-or-later one, because both etime columns are ms-derived and a trade routinely shares a millisecond with the bookTicker update its own execution caused"
    - "_day_has_any_source pre-check before build_curated_day, so a date with neither an archive nor a capture partition is a counted 'no_source' skip, not a crashed ValueError from select_source_for_day"

key-files:
  created:
    - mvp/data/backfill/downloader.py
    - mvp/tests/ingest/test_curated_build_multi_day.py
  modified:
    - mvp/data/backfill/client.py (BackfillClient.granularity field)
    - mvp/data/ingest/normalize.py (normalize_archive_frame split out; raw_partition_dir/raw_partition_exists added)
    - mvp/data/ingest/curated_build.py (build_curated_range, _day_has_any_source, bookTicker branch documented)
    - mvp/data/ingest/trade_side.py (nearest_quote_side: allow_exact_matches=False)
    - mvp/tests/backfill/test_downloader.py (10 new tests)
    - mvp/tests/ingest/test_trade_side.py (2 new regression tests)
    - mvp/data/lake_registry/manifests/BTCUSDT.trade/*.json (107 manifests + by-date index)
    - mvp/data/lake_registry/manifests/BTCUSDT.bookTicker/*.json (4 manifests + by-date index)

key-decisions:
  - "BackfillClient (Plan 01) gained a granularity field rather than a parallel monthly-only client -- one download/checksum/idempotent-marker code path for both regimes, not a duplicate. Not in the plan's files_modified list; added as a Rule 3 blocking-issue fix (the client hardcoded the daily URL template only)."
  - "curated_build.py imports daterange from data.backfill.downloader (not duplicated) -- a minor layering choice (curated/ingest depending on backfill/) accepted because the alternative was re-implementing a 5-line pure date-math function."
  - "Monthly ingestion re-scans the extracted CSV once per UTC day (pl.scan_csv(...).filter(...).collect() per date) rather than a single hive-partitioned sink_parquet pass -- ~24s/day measured, ~12-15 min/month, well within budget, and it is the literal alternative 03-RESEARCH.md's Pitfall 3 sanctions; a single-pass sink was not attempted given the measured approach's RSS was already far below the 23GB threat threshold."
  - "bookTicker's curated range was run as 2026-09-12 -> 2026-09-15 (the live-confirmed real start date), not the full 2026-06-01 window -- build_curated_range's no_source skip is exercised and unit-tested (test_curated_build_multi_day.py) but not run against 92 additional trivially-empty real dates, which would have added no new information."

patterns-established:
  - "Real-scale write_parquet determinism check: build the same day into two independent tmp lake roots from a copy of the same raw input, compare curated part-file sha256 (not manifest_id, which legitimately differs via built_at) -- confirmed identical on a 1.4M-row day."
  - "Real backfill/curated builds are re-runnable and were proven so live, not just unit-tested: an accidental second invocation of the full 107-day trade curated build returned 107/107 already_present with zero new manifests/partitions."

requirements-completed: [DATA-02, DATA-03, DATA-05, DATA-06]

# Metrics
duration: ~3h10m (orientation + implementation + three real background runs + investigation/fix + real curated builds + cross-check)
completed: 2026-09-16
---

# Phase 3 Plan 03: Full-Window Backfill, Curated Build At Scale, Trade-Side Cross-Check Summary

**Widened Plan 01/02's single-day slice to the real `2026-06-01 -> 2026-09-15` window (107 archive days, 376,171,010 trade rows, 107/107 archive-chosen), built the curated tier for both streams (trade: 107 days; bookTicker: the real capture-confirmed 4-day range), and measured the trade-side nearest-quote cross-check for real -- which came back at 57.5% pre-fix, was root-caused to a millisecond-granularity join_asof bug (not a data problem), fixed, and re-measured at 99.854%.**

## Performance

- **Duration:** ~3h10m wall-clock across three background real-network runs (June: 925s; interrupted July-Sept attempt: ~4min before deliberate kill; resumed July-Sept: 183s) plus curated builds, investigation, and the fix
- **Started:** 2026-09-16T14:00 PDT (session start)
- **Completed:** 2026-09-16T15:10 PDT (approx, at this SUMMARY's commit)
- **Tasks:** 3 (Task 1, Task 2, Task 3 -- each with a code commit; Task 2 also has a separate real-manifest-artifact commit, matching Plan 02's own ordering so `code_hash` is never `-dirty`)
- **Files modified:** ~230 (2 new code files, 5 modified code files, 2 new test files, ~220 real manifest JSON files)

## Accomplishments

- `data/backfill/downloader.py`: monthly-vs-daily regime dispatch across the real `2026-06-01 -> 2026-09-15` window. Monthly zips extracted to disk once, normalized via an explicit per-UTC-day `pl.scan_csv(...).filter(...).collect()` loop -- never a full in-memory `pl.DataFrame` for a month. Measured peak RSS: **8,052,850,688 B (7.50 GiB)** for June (`/usr/bin/time -l`, alongside the live capture daemon), far below the ~23 GiB naive-read estimate 03-RESEARCH.md's Pitfall 3 warns against.
- Real full-window backfill run, completed with a **genuine interruption and resume** (see below) -- 107 archive-day raw partitions, `2026-06-01T00:00:00.093Z -> 2026-09-15T23:59:59.986Z`, **376,171,010 total trade rows** (verified via parquet metadata scan across all 107 files, not just summed print output).
- `data/ingest/curated_build.py:build_curated_range` -- ran for real: `BTCUSDT.trade` across the full 107-day window (all archive-chosen, 0 NA-placeholder rows dropped anywhere) and `BTCUSDT.bookTicker` across the real, live-confirmed capture range `2026-09-12 -> 2026-09-15` (4 days, 104,868,413 rows, all capture-sourced -- no L1 backfill source exists at all).
- The trade-side cross-check ran for real over 2026-09-13 and **found a genuine bug**, not a data anomaly: `nearest_quote_side`'s `join_asof(strategy="backward")` was allowing same-millisecond ("exact") matches, so it frequently picked the bookTicker update a trade's OWN execution had just caused. Root-caused, fixed (`allow_exact_matches=False`), and re-measured: **99.854% agreement (1,407,647/1,409,705)**.
- The real-scale `write_parquet` determinism check passed: two independent `build_curated_day` runs against the same 1.4M-row raw input produced byte-identical curated part files (`sha256` matched exactly; only `manifest_id`/`built_at` differed, as expected).

## Task Commits

1. **Task 1: Downloader at scale** - `5b37c66` (feat)
2. **Task 2: Curated build generalized (code)** - `9ba1654` (feat)
3. **Task 3: Trade-side cross-check fix** - `2a4d93b` (fix)
4. **Task 2: Real curated manifests (artifact)** - `91e91ea` (docs)

**Plan metadata:** this commit (docs: complete plan)

## Files Created/Modified

- `mvp/data/backfill/downloader.py` - `daterange`, `month_dates`, `day_bounds_ms`, `published_daily_dates`, `ingest_daily_date`, `ingest_monthly`, `run_backfill`, CLI `main`
- `mvp/data/backfill/client.py` - `BackfillClient.granularity` (`"daily"|"monthly"`), URL template now derived from it
- `mvp/data/ingest/normalize.py` - `normalize_archive_frame` (core, the `ms_to_ns` site) split from `normalize_archive_trades` (thin `csv_path` wrapper); `raw_partition_dir`/`raw_partition_exists`
- `mvp/data/ingest/curated_build.py` - `build_curated_range`, `_day_has_any_source`, `_curated_build_stats_path`; explicit bookTicker-branch documentation in `build_curated_day`
- `mvp/data/ingest/trade_side.py` - `nearest_quote_side`'s `join_asof(..., allow_exact_matches=False)` fix, with the measured root-cause writeup in the module docstring
- `mvp/tests/backfill/test_downloader.py` - 10 new tests (regime dispatch, resume idempotency, the `FileExistsError` race path, pure date-math helpers)
- `mvp/tests/ingest/test_curated_build_multi_day.py` - 4 new tests (multi-day dispatch, idempotent re-run, bookTicker no-source skip, cross-function manifest reuse)
- `mvp/tests/ingest/test_trade_side.py` - 2 new regression tests pinning the `allow_exact_matches=False` fix
- `mvp/data/lake_registry/manifests/BTCUSDT.trade/*.json` (107 manifests + 106 new by-date index files -- 2026-09-12 reused Plan 02's own manifest unchanged)
- `mvp/data/lake_registry/manifests/BTCUSDT.bookTicker/*.json` (4 manifests + 4 by-date index files)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `BackfillClient` had no monthly-zip URL support**
- **Found during:** Task 1, before writing `downloader.py`
- **Issue:** Plan 01's `BackfillClient` hardcoded `.../daily/...` in its URL construction; the plan's interfaces section lists `BackfillClient` as reused "without duplicating any of their logic," but the class as written could not download a monthly zip at all.
- **Fix:** Added a `granularity: Literal["daily", "monthly"]` field (default `"daily"`, matching Plan 01's original behavior exactly). `ensure_downloaded`'s URL now interpolates `self.granularity` into the path. Verified live before writing any code: `curl -I https://data.binance.vision/data/futures/um/monthly/trades/BTCUSDT/BTCUSDT-trades-2026-06.zip` returned `content-length: 1085937447` -- an exact match to 03-CONTEXT.md's measured size.
- **Files modified:** `mvp/data/backfill/client.py` (not in the plan's `files_modified` list for this plan)
- **Verification:** `test_backfill_client_monthly_granularity_hits_monthly_url_path`, `test_backfill_client_rejects_unknown_granularity`; the real June download succeeded end-to-end through this path.
- **Committed in:** `5b37c66`

**2. [Rule 1 - Bug] `nearest_quote_side` allowed same-millisecond quote matches -- the real cross-check's headline finding**
- **Found during:** Task 3, the real 2026-09-13 cross-check
- **Issue:** `join_asof(strategy="backward")` defaults to `allow_exact_matches=True`, so a quote sharing the trade's exact `etime` was a valid match. Both `etime` columns are ms-derived (Binance's own `E`/`T` fields, one `ms_to_ns` site), so their nanosecond "precision" is trailing zeros -- a trade and the bookTicker update its OWN execution caused routinely land in the same millisecond in a fast market. The classifier frequently picked the post-trade quote: **exactly** the price-impact leakage the module's docstring already warned against in principle, just not enforced in the join's own parameters. Measured: **57.5%** overall agreement (811,088/1,409,705); trades landing on an unambiguous (single-update) millisecond agreed 88.1% of the time; trades landing on a millisecond with >1 quote update (62% of the day, 877,254/1,409,705) agreed only 39.0% of the time.
- **Fix:** `join_asof(..., allow_exact_matches=False)` -- strictly-earlier quotes only. Confirmed via `pl.DataFrame.join_asof`'s own docstring that the parameter exists in polars 1.41.2 before using it.
- **Files modified:** `mvp/data/ingest/trade_side.py`, `mvp/tests/ingest/test_trade_side.py`
- **Verification:** Re-ran the real 2026-09-13 cross-check post-fix: **99.854% agreement (1,407,647/1,409,705)**. Two new regression tests pin the fix directly. No existing test's fixtures had a same-etime quote/trade pair, so all 6 pre-existing `test_trade_side.py` tests pass unchanged.
- **Committed in:** `2a4d93b`
- **Note on scope:** this is the exact scenario the plan's own Task 3 text pre-authorized ("if the join reveals a genuine bug in `nearest_quote_side` (e.g. an off-by-one in 'nearest in time'), fix it here and re-run") -- treated as Rule 1, not a Rule 4 architectural question, because the fix is a one-parameter join semantics correction with zero effect on any other code path (no curated rebuild needed: every real row's `side_method` is `"exact_flag"`, so `nearest_quote_side` never actually fires on this phase's committed curated data).

**Total deviations:** 2 auto-fixed (1 Rule 3 blocking-issue, 1 Rule 1 bug -- the bug being the single most consequential finding of this plan). Both were necessary for the plan's own stated deliverables to be real, not shortcuts.

## The Real Backfill Numbers

### Downloader (Task 1)

| Metric | Value |
|---|---|
| Window requested | `2026-06-01 -> 2026-09-15` (the real "yesterday" relative to 2026-09-16, today's date -- today is deliberately never built: partial day, daemon actively writing, schema-v2 switch lives there) |
| Archive days present (measured) | 107 / 107 requested -- every day published by run time (0 `not_published`, 0 `not_found`) |
| Zip files checksum-verified this session | 17 (3 monthly + 14 daily; `2026-09-12`'s zip was Plan 01's own pre-existing cached artifact, never re-touched this session since its raw partition already existed) |
| Total bytes downloaded+verified this session | 2,778,514,793 B (~2.78 GB) |
| Checksum verdicts | **17/17 PASS, 0 FAIL** (a single mismatch would have raised `ChecksumError` and aborted the run; both real runs exited 0) |
| Monthly zip sizes (measured, exact match to 03-CONTEXT.md) | 2026-06: 1,085,937,447 B; 2026-07: 676,850,419 B; 2026-08: 691,176,258 B |
| Total trade rows (raw tier, verified via parquet metadata scan) | **376,171,010** |
| Date range covered | `2026-06-01T00:00:00.093Z -> 2026-09-15T23:59:59.986Z` |
| Days from archive vs capture | 107 archive-sourced days (every day requested); 0 days fell back to capture in the raw tier (not_published never fired) |
| Wall-clock, June (fresh, includes 30-day monthly processing) | 925.04s real (15m 25s) |
| Wall-clock, resumed July-Sept run (July instant-skip + Aug remainder + Sept daily) | 183.05s real (3m 3s) |
| Peak RSS, June (`/usr/bin/time -l`, `maximum resident set size`) | 8,052,850,688 B = **7.50 GiB** |
| Peak RSS, resumed July-Sept run | 7,817,740,288 B = **7.28 GiB** |
| Naive full-month in-memory-read estimate this design avoids (03-RESEARCH.md) | ~23 GiB |

### Disk headroom (self-measured, `df -h /Volumes/ProjectsSSD`)

| Checkpoint | Free |
|---|---|
| Session start (before any downloads) | 810 GiB |
| Before starting the real June run | 825 GiB |
| After June completed (extracted CSV auto-deleted) | 856 GiB |
| Mid-August, at the moment of deliberate `kill -9` (4.9 GB extracted August CSV orphaned by the hard kill) | 849 GiB |
| After the resumed run completed (0 CSVs left in staging) | 853 GiB |
| After both curated builds + the determinism check | 849 GiB (current, final) |

Never came close to the 100 GiB floor. Note: a concurrent, unrelated background job (the coordinator's raw-archive re-framing, `capture/raw` 43 GB -> 20 GB) also freed space on this same volume during this session -- the numbers above are the real `df -h` readings I took myself, not adjusted for that.

### Curated build (Task 2)

| Stream | Days | Rows | Source split | NA-placeholder rows dropped |
|---|---|---|---|---|
| `BTCUSDT.trade` | 107 (`2026-06-01 .. 2026-09-15`) | 376,171,010 (same as raw -- archive-chosen everywhere, nothing filtered) | 107 archive, 0 capture | 0 across all 107 days (archive never has `X="NA"` placeholder rows) |
| `BTCUSDT.bookTicker` | 4 (`2026-09-12 .. 2026-09-15`, the real live-confirmed capture range) | 104,868,413 (09-12: 7,220,489 partial day from 06:37:10Z; 09-13: 17,167,290; 09-14: 38,609,768; 09-15: 41,870,866) | 4 capture, 0 archive (no L1 backfill source exists) | n/a (no NA-placeholder concept for this schema) |

**bookTicker before 2026-09-12 is deliberately unbuilt, not an empty partition.** `build_curated_range`'s `no_source` skip status (unit-tested in `test_curated_build_multi_day.py`) exists exactly for this case; it was not exercised against the 92 additional June-through-mid-September dates in the real run because doing so would have added no information beyond what the unit test already proves -- the range argument passed was `2026-09-12 -> 2026-09-15`, the real confirmed start.

**Manifests:** 107 `BTCUSDT.trade` manifests (106 newly issued + 2026-09-12 reusing Plan 02's own manifest unchanged) and 4 `BTCUSDT.bookTicker` manifests, all git-committed in `91e91ea`.

**Real-scale determinism check (Manual-Only Verification):** built `2026-09-13` (1,409,705 rows, 9,034,707 B raw input) into two independent tmp lake roots from a copy of the same raw partition. `sha256` of the resulting curated part files: **identical** (`c4d9c85a9354125cdf245c65c93a1fc01179edfe5725d86bbce941abaa88cef3` both times); `manifest_id` differed only because `built_at` (a wall-clock timestamp) differed between the two runs -- exactly the expected behavior.

**Idempotency at real scale, observed live (not just unit-tested):** an accidental second full-window trade curated build (I re-ran the driver script to redirect output to a log file, not realizing the first run had already completed) returned `already_present` for all 107/107 dates, with the by-date index reporting the SAME manifest_id as the first run, and zero new files written -- a stronger, real-data proof than the unit test alone.

## The Trade-Side Agreement Cross-Check (Task 3)

**Measured on 2026-09-13** (a full day with both streams fully covered -- avoids 2026-09-12's partial-day skew, where trades before 06:37:10Z have no prevailing quote at all).

- **Pre-fix:** 57.5% (811,088 / 1,409,705) -- see Deviations #2 for the root cause.
- **Post-fix:** **99.854% (1,407,647 / 1,409,705)**.
- Residual disagreement breakdown, post-fix: 64 rows `"unknown"` (tie or no strictly-prior quote), 1,994 rows genuine opposite-sign disagreement -- both plausible residual noise (thin-book edge cases, not a further systematic bug).
- Loaded both curated partitions via `store.load_curated` (the real, hash-verified reading path) -- `trade` manifest `2a7b4c89cf3661420b5ad9ad469e5a6ffc3dd096f2e3730f77cea2e201504099`, `bookTicker` manifest `223b0f2ae928f1b8695814f67f9bbb2626eb92dbefc51c58bf6e1c6b4080d498`.

This is the honest, measured resolution of Open Question 2 (03-CONTEXT.md): a one-time mechanism-validation number, not a recurring DQ check. The pre-fix 57.5% is not rounded away -- it is the reason the fix exists, and it is reported here in full alongside the post-fix number.

## The Interruption/Resume Test (real, not simulated)

1. Started the real `2026-07-01 -> 2026-09-15` backfill run in the background.
2. Waited (via an `until`-loop condition check, not a blind sleep) for August to reach `date=2026-08-06` (6 of 31 days written).
3. **`kill -9`** both the wrapping `/usr/bin/time` process and the Python downloader process -- a genuine hard kill, exit code 137, NOT a graceful shutdown. Confirmed no `.tmp` files anywhere under `lake/raw` (atomic `.tmp`+rename held even under `SIGKILL`), but a 4.9 GB extracted August CSV was orphaned in staging (the `finally: csv_path.unlink()` cleanup never got a chance to run -- this is a known, accepted gap under a hard kill; see Surprises).
4. Restarted the **identical command** (`--start 2026-07-01 --end 2026-09-15`).
5. **Observed live:** `monthly 2026-07: all 31 in-range day(s) already present, skipping download+extract entirely` -- zero network calls, zero re-download, for the fully-completed month.
6. August resumed correctly: the 6 already-written days were skipped silently inside the loop, the remaining 25 were re-extracted-and-written (the orphaned CSV from step 3 was simply overwritten by the fresh extract, not reused -- also documented in Surprises).
7. September proceeded normally; `2026-09-12` (from Plan 01) was skipped as `already_present`.
8. Final summary: `written=39 already_present=38`, exit code 0.

This is the real-data version of `test_ingest_monthly_skips_already_written_day_and_still_processes_adjacent_day` and `test_ingest_daily_date_second_call_is_noop_skip_no_network` -- both passed as unit tests AND observed against the live 107-day window with an actual `SIGKILL` in the middle.

## Surprises

- **The cross-check's pre-fix 57.5% was the single biggest finding of this plan** -- see Deviations #2. Investigated to a definitive, measured root cause (millisecond-granularity `etime` collisions between a trade and its own resulting quote update, in a market fast enough that 62% of trades share a millisecond with more than one quote update) before touching any code, per the plan's own explicit pre-authorization to fix exactly this class of bug.
- **`select_source_for_day`'s reconciliation stats show a growing capture-side gap on the most recent days**, not present on 09-13: `reconciliation_missing_from_capture` is 0 on 2026-09-13, 176,454 on 2026-09-14, and 317,032 on 2026-09-15 (ids in the archive-capture overlap range that archive has but capture doesn't). Archive was chosen every day, so the curated trade tier is unaffected -- but this is exactly the DQ-report input Plan 04 is meant to read, and the growing gap on the two most recent days is worth Plan 04 flagging, not smoothing over. Not investigated further here -- out of this plan's scope (Plan 04 owns DQ interpretation).
- **A hard `kill -9` orphans the extracted monthly CSV** (the `finally: csv_path.unlink(missing_ok=True)` block never runs under `SIGKILL`, only under a normal exception path). The next `ingest_monthly` call for that same month re-extracts fresh and silently overwrites the orphaned file (same filename, `zipfile.ZipFile.extract` truncates-and-rewrites) -- so correctness is unaffected, but a repeatedly-interrupted run could in principle leave a stale, unused multi-GB file in staging until the month is next touched. Documented here as a known gap, not fixed: fixing it would mean either a `try/except (BaseException)` around the extract-and-process block (broader than the `FileExistsError`-only catch the plan asks for) or a startup sweep of `backfill_staging_root()` for orphaned `.csv` files with no matching `.verified` marker's month fully complete -- both are Rule 4-scale design choices, not something to bolt on unannounced.
- **The monthly CSV's header row is present**, confirmed by direct inspection of the downloaded 2026-06 zip before writing any monthly-path code (`id,price,qty,quote_qty,time,is_buyer_maker`) -- matches `unit_registry.toml`'s "all dates" claim, which had only been measured against a daily file. No surprise here, but worth recording as a confirmed-not-assumed fact per the plan's own instruction.
- Two files not in this plan's declared `files_modified` list were touched: `mvp/data/backfill/client.py` (Deviation #1) and `mvp/data/ingest/trade_side.py` (Deviation #2, `files_modified` only for Task 3). Both are documented above as auto-fixes required for the plan's own stated deliverables to hold.

## Verification Transcript

```
$ uv run --locked --directory mvp pytest tests/backfill/test_downloader.py -x -q
20 passed in 2.06s   # (10 pre-existing client tests + 10 new downloader/regime tests)

$ uv run --locked --directory mvp pytest tests/ingest/test_curated_build_multi_day.py -x -q
4 passed in 0.24s

$ uv run --locked --directory mvp pytest tests/ingest/test_trade_side.py -x -q
8 passed in 0.15s

$ uv run --locked --directory mvp pytest tests -x -q   # full suite, after Task 1
278 passed

$ uv run --locked --directory mvp pytest tests -x -q   # full suite, after Task 2
282 passed

$ uv run --locked --directory mvp pytest tests -x -q   # full suite, after Task 3's fix
284 passed

$ uv run --directory mvp python -m tools.check_ms_to_ns_site
PASS: exactly one ms-to-ns site at data/capture/parse.py:36
PASS: 8 seconds-to-ns site(s), all allowlisted
```

Every pre-commit hook (ruff check, ruff format --check, uv lock --check, 8 guardrails incl. `check_no_manifest_rewrite` now checking 111 real manifests, pytest) passed on all four commits -- transcripts shown inline at each `git commit` invocation, captured live.

## Issues Encountered

None blocking beyond the two documented deviations. The capture daemon (Run I, PID 75796) was read-only to this plan throughout and was never touched, restarted, or signaled -- confirmed still running normally (`ps -p 75796`) at the end of this session.

## Next Phase Readiness

- Plan 04 (DQ report) can read `build_stats.json`'s precomputed `reconciliation_missing_from_capture`/`reconciliation_missing_from_archive`/`na_placeholder_dropped`/`na_placeholder_rate` fields directly for all 107 real trade days -- including the 09-14/09-15 capture-gap surprise flagged above, which Plan 04's threshold logic should surface.
- Phase 4 (feature engine) has both curated streams to read via `store.load_curated`: `BTCUSDT.trade` (107 days) and `BTCUSDT.bookTicker` (4 days, `2026-09-12 -> 2026-09-15`) -- any feature needing L1 is structurally limited to that 4-day window until more capture history accrues; this is the two-regime boundary working as designed, not a gap to fill.
- `data/ingest/trade_side.py`'s `allow_exact_matches=False` fix is load-bearing for ANY future legacy-side backfill that actually needs `nearest_quote_side` to fire (unlike this phase's real data, where `side_method` is `"exact_flag"` everywhere) -- the 99.854% post-fix rate is the number future work should cite, not the pre-fix 57.5%.
- `mvp/data/backfill/downloader.py`'s CLI (`python -m data.backfill.downloader --start ... --end ...`) is the re-runnable entry point for extending the window forward as more days publish -- idempotent by construction, proven live under an actual `SIGKILL`.

## Self-Check: PASSED

- `mvp/data/backfill/downloader.py` -- FOUND on disk
- `mvp/tests/ingest/test_curated_build_multi_day.py` -- FOUND on disk
- Commits `5b37c66`, `9ba1654`, `2a4d93b`, `91e91ea` -- all FOUND in `git log`
- 107 `lake/raw/.../source=archive/date=*` partitions -- FOUND on disk (verified via `find | wc -l` = 108 including the `source=archive` directory itself)
- 107 `BTCUSDT.trade` + 4 `BTCUSDT.bookTicker` manifest JSON files -- FOUND in `mvp/data/lake_registry/manifests/` and in `git log` (commit `91e91ea`)
- Real curated partitions for `2026-09-13` (trade + bookTicker) -- FOUND on disk, loaded successfully via `store.load_curated` during the cross-check
- Capture daemon PID 75796 -- confirmed still running, untouched

No missing items.
