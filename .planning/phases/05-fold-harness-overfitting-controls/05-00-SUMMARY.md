---
phase: 05-fold-harness-overfitting-controls
plan: 00
subsystem: data-lake
tags: [curated-ingest, dq-report, capture-redelivery, gap-ledger, real-data, archive-supersede]

# Dependency graph
requires:
  - phase: 03-data-layer-backfill-ingest-lockbox
    provides: data.ingest.curated_build.build_curated_range/build_curated_day, data.backfill.downloader, data.dq.report.write_report, data.store's manifest/DQ-pause machinery
  - phase: 04-feature-label-engine
    provides: features.build.build_features_day/build_features_range, the D+1 label-tail rule, the three existing feature partitions (2026-09-12..14)
provides:
  - Eight new curated-tier manifests (BTCUSDT bookTicker + trade, 2026-09-16..19), all resolving via resolve_manifest, all git-committed with by-date pointers
  - A DQ verdict on record (report.parquet row) for all eight new curated partitions -- every one non-ok
  - A narrow, tested fix in data.ingest.curated_build for a real capture-redelivery duplicate row class, applicable to any future capture-sourced ingest
  - The honest finding that zero of 2026-09-15..18's feature days can build without a human DQ acknowledgement decision
affects: [05-fold-harness-overfitting-controls/05-01, 07-stage-1-regression-vertical-slice, 08-stage-1-trees-transformer]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "A capture-sourced curated day may carry a content-identical redelivered row (BoundedDedup's TTL-eviction tradeoff): materialize_seq's duplicate-id assertion is a real anomaly detector, not a redelivery detector -- the redelivery case must be resolved BEFORE that assertion runs, gated on chosen_source=='capture', keeping the earliest-arrival row and leaving any content-DIFFERING duplicate untouched so the assertion still fires on genuine anomalies."
    - "build_curated_day never reads the network; a day's curated trade source is only as authoritative as whatever raw archive partition already exists on local disk. Getting archive precedence for a newly-published day requires running data.backfill.downloader BEFORE build_curated_range, then re-running build_curated_range to supersede a capture-sourced first pass."
    - "data.ingest.curated_build never writes a DQ report row. A separate data.dq.report.write_report (or its CLI) call is required per date before load_curated/build_features_day can see a non-missing verdict for a newly curated day."

key-files:
  created: []
  modified:
    - mvp/data/ingest/curated_build.py (_drop_capture_redelivery_duplicates, wired into build_curated_day and recompute_build_stats)
    - mvp/tests/ingest/test_curated_build_multi_day.py (3 new tests for the redelivery-dedup fix)
    - mvp/tests/dq/test_checks.py (real curated-manifest count assertion updated 111->119, capture 4->8, archive 107->111)
    - mvp/data/lake_registry/manifests/BTCUSDT.bookTicker/ (4 new manifests + 4 by-date pointers)
    - mvp/data/lake_registry/manifests/BTCUSDT.trade/ (8 new manifests -- 4 capture-sourced now-superseded + 4 archive-sourced current + 4 by-date pointers)

key-decisions:
  - "Content-identical capture-redelivery duplicates (same id, differing only in the daemon's own seq/rtime bookkeeping) are dropped before materialize_seq, keeping the earliest arrival; a duplicate id that disagrees on any real field is left untouched and still raises. Classified Rule 1 (materialize_seq's 'this is impossible' premise was empirically false for capture-sourced data, per STATE.md's own BoundedDedup TTL note) after an advisor consult, not Rule 4 -- the fix is source-gated and narrow, not structural."
  - "Ran data.backfill.downloader for 2026-09-16..19 before the plan's literal build_curated_range(stream='trade') call, then re-ran build_curated_range to supersede all four trade days from capture-sourced to archive-sourced. The plan's <action> text named only the two build_curated_range calls; 05-RESEARCH.md Q4(b) and the project's own archive-is-authoritative-for-trades design make the download step a Rule 3 blocking-issue completion, not scope creep -- omitting it would have left all four trade days on the less-authoritative source with no supersede ever triggered later (nothing re-checks a day once its by-date pointer already resolves)."
  - "No DQ acknowledgement written for any of the eight new curated partitions, all eight are non-ok. Per constraint 11 this is the user's decision; see ACK NEEDED FROM USER below."
  - "Reused the single code_hash (039d7c394720043b382ec15d363f9c1c6386c178, computed once after the redelivery-dedup fix landed at commit 039d7c3) for every build_curated_range and build_features_day call in this plan, per the plan's explicit instruction not to recompute mid-plan. HEAD moved twice more after that (the manifest-commit fdd124c, and this SUMMARY) -- neither touched pipeline code, only registry JSON and a test file, so the hash still accurately names the code that ran."

patterns-established:
  - "A one-shot ingest script that finds a real data anomaly (a duplicate row, an unexpectedly large outage) should stop, investigate with the tools the anomaly itself provides (read_capture_partition, the gap ledger), and consult before writing a fix -- the row-level dedup fix in this plan came from that sequence, not from guessing."

requirements-completed: [EVAL-01]

# Metrics
duration: ~50min
completed: 2026-09-20
---

# Phase 5 Plan 0: Widen the Curated Pool (2026-09-16..19) Summary

**Ingested four more days of BTCUSDT bookTicker and trade into the curated tier via the existing Phase 3 pipeline (fixing one real capture-redelivery duplicate-row bug along the way and re-running the trade build to supersede onto the archive), then found -- honestly, not by weakening a threshold -- that none of the four candidate feature days can build without a DQ acknowledgement the plan forbids this executor from writing.**

## Performance

- **Duration:** ~50 min (estimated from commit timestamps; no start-epoch was captured at session start)
- **Tasks:** 2 (both executed; Task 1 produced committed artifacts, Task 2 produced a negative, fully-documented result and no artifacts)
- **Files modified:** 2 source files (1 fix, 1 test-count correction), 1 test file (3 new tests), 20 new registry JSON files (curated manifests + by-date pointers)

## Accomplishments

- Curated tier widened from 111 to 119 by-date pointers: 4 new bookTicker days (capture-sourced, its only source) and 4 new trade days (archive-sourced, after downloading the published zips and superseding an initial capture-sourced pass).
- A real production bug found and fixed: one bookTicker row on 2026-09-16, delivered twice 147.3s apart by the capture daemon's redundant connection (a TTL-evicted dedup redelivery, exactly the tradeoff STATE.md's BoundedDedup design note predicts), which `materialize_seq` correctly refused to silently accept and which is now handled by a narrow, tested, source-gated fix.
- DQ reports generated for all four new dates (8 partitions); every one is non-ok, and the numbers are transcribed below, not summarized away.
- Feature-build attempted for 2026-09-15..18, one date at a time so each refusal is individually attributable; all four refused with `DQPauseError`, verbatim messages transcribed below.

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 (fix) | Capture-redelivery duplicate dedup | `039d7c3` | `data/ingest/curated_build.py`, `tests/ingest/test_curated_build_multi_day.py` |
| 1 | Curated ingest 2026-09-16..19 (both streams) + DQ reports + real-count test fix | `fdd124c` | `data/lake_registry/manifests/BTCUSDT.{bookTicker,trade}/*`, `tests/dq/test_checks.py` |
| 2 | Feature build attempted for 2026-09-15..18 -- no artifacts produced (see below) | *(no commit -- nothing changed on disk)* | — |

## The one code_hash

`039d7c394720043b382ec15d363f9c1c6386c178`, computed once via `compute_code_hash()` immediately after the redelivery-dedup fix landed (commit `039d7c3`), and reused unchanged for every `build_curated_range` and `build_features_day` call in this plan, per the plan's own instruction not to recompute mid-plan. It is clean (no `-dirty` suffix) at the moment it was computed. Two more commits landed on HEAD after it (`fdd124c`, and this SUMMARY's own commit) -- both are registry JSON / test / docs changes, not pipeline code, so the hash still names the code that actually ran the builds.

## Task 1: Curated ingest, both streams, 2026-09-16..19

### The capture-redelivery duplicate (found before any curated day built)

The first `build_curated_range(stream="bookTicker", ...)` attempt raised on 2026-09-16:

```
ValueError: materialize_seq: 'update_id' has 1 duplicate value(s) within this partition --
the per-day source switch should make this impossible; investigate before trusting (etime, seq).
```

Investigated on the real partition (`read_capture_partition`): `update_id=11569010063437` appears twice, identical on every exchange field (`etime`, `bid_price`, `bid_qty`, `ask_price`, `ask_qty`), differing only in the daemon's own arrival bookkeeping: `seq` 108084687 vs 108084688 (consecutive), `rtime` 1789528170166506000 vs 1789528317474976000 (147.3s apart). This is STATE.md's own documented BoundedDedup tradeoff made real: "a key the redundant connection delivers late is never wrongly dropped as a duplicate" -- a redelivery arriving *later than the dedup TTL* is not deduped by the daemon and lands in the raw stream as a genuine second row.

Consulted `advisor()` before writing any fix (transcript in the conversation, not reproduced here). Verdict: Rule 1 bug (the assertion's premise was empirically false for capture-sourced data), fix narrowly in `build_curated_day`, gated on `chosen_source == "capture"`, drop rows that are duplicates on every column except `seq`/`rtime`, keep the lowest `seq` (earliest arrival); a duplicate id disagreeing on any other field stays untouched and still raises. Added `_drop_capture_redelivery_duplicates` (`data/ingest/curated_build.py`), wired into both `build_curated_day` (before `materialize_seq`) and `recompute_build_stats` (so a recompute never produces a smaller key set than a fresh build), and recorded the count as `capture_redelivery_rows_dropped` in `build_stats.json`.

Three new tests in `tests/ingest/test_curated_build_multi_day.py`:
1. `test_capture_redelivery_duplicate_is_dropped_keeping_lowest_seq` -- the real case, reproduced synthetically: dedup drops one row, keeps the earliest `rtime`.
2. `test_non_identical_duplicate_update_id_still_raises` -- a duplicate id disagreeing on `bid_price` still raises via `materialize_seq`.
3. `test_archive_sourced_duplicate_trade_id_still_raises` -- the dedup gate never fires for `chosen_source == "archive"`.

Full suite: **956 passed** (953 baseline + 3 new), `ruff check`/`ruff format --check` clean, all 17 pre-commit hooks green. Committed `039d7c3`.

Re-run against the real 2026-09-16 partition: `bookTicker 2026-09-16: rows=36234202` (36,234,203 raw rows minus the 1 dropped redelivery, exact match).

### The trade archive step the plan's literal action text omitted

`build_curated_range(stream="trade", ...)` for 2026-09-16..19, run as the plan's `<action>` literally specifies, returned `chosen_source: "capture"` for all four dates -- there was no local `lake/raw/.../source=archive/date=2026-09-1{6,7,8,9}/` partition on disk yet, even though 05-RESEARCH.md Q4(b) had already confirmed all four archive zips are published (HTTP 200). `build_curated_day` never fetches the network itself; it only reads what is already staged locally. Ran `python -m data.backfill.downloader --symbol BTCUSDT --start 2026-09-16 --end 2026-09-19 -v` (real network op): `written=4 already_present=0 no_rows=0 not_published=0 not_found=0 total_rows_written=13180397`. Re-ran `build_curated_range(stream="trade", ...)`: all four dates now `status="superseded"`, `chosen_source="archive"`, each naming its `superseded_manifest_id` (the original capture-sourced manifest, which keeps resolving, write-once).

Treated as a Rule 3 blocking-issue completion (not scope creep): the project's own precedence rule ("the archive is authoritative for trades on every published day") and 05-RESEARCH.md's own Q4(b) section describe this exact download step as part of "Plan 0 Mechanics" -- the plan's condensed `<action>` prose simply didn't restate it. Nothing re-checks a day's source once its by-date pointer resolves, so skipping this step would have left all four trade days silently less authoritative than the project's own design, forever.

### Curated build results (verbatim status dicts)

**bookTicker:**

| date | status | chosen_source | manifest_id (first 16) |
|---|---|---|---|
| 2026-09-16 | written | capture | `154bf197810a9c51` |
| 2026-09-17 | written | capture | `bc11a5cfda37af10` |
| 2026-09-18 | written | capture | `1ef62c97bb7e732f` |
| 2026-09-19 | written | capture | `41ae94a9014fbce1` |

**trade** (after the archive-supersede pass):

| date | status | chosen_source | manifest_id (first 16) | superseded_manifest_id (first 16) |
|---|---|---|---|---|
| 2026-09-16 | superseded | archive | `0e10b09a770be58f` | `c38cd6875458addc` |
| 2026-09-17 | superseded | archive | `70894b02d9b0f98d` | `577e4606aecc07c7` |
| 2026-09-18 | superseded | archive | `fd18654c54bea884` | `8b86068f7ed6e9ad` |
| 2026-09-19 | superseded | archive | `9c71fc2ae2d8748f` | `b72f2a05c62987ea` |

Row counts / bytes, from `resolve_manifest` on each by-date pointer (the actual written row set, not the raw pre-dedup count):

| date | bookTicker rows | bookTicker bytes | trade rows | trade bytes |
|---|---|---|---|---|
| 2026-09-16 | 36,234,202 | 463,492,530 | 4,124,224 | 28,449,081 |
| 2026-09-17 | 28,170,617 | 367,562,681 | 2,761,932 | 19,583,917 |
| 2026-09-18 | 31,460,152 | 401,869,464 | 4,486,145 | 32,176,202 |
| 2026-09-19 | 13,913,226 | 183,172,575 | 1,808,096 | 13,692,716 |

Automated verify (plan's own script): `8/8 new curated by-date pointers present` -- passed.

### DQ report generation and verdicts (data.dq.report.write_report)

`build_curated_day`/`build_curated_range` never write a DQ report row themselves (confirmed by reading the source before assuming otherwise, per the plan's `<read_first>` instruction) -- ran `python -m data.dq.report --symbol BTCUSDT --range 2026-09-16 2026-09-19` afterward. All eight new curated partitions now have a report row; **every one is non-ok**, transcribed exactly as measured:

| date | stream | check | dq_status | value | threshold |
|---|---|---|---|---|---|
| 2026-09-16 | bookTicker | gap_coverage | **failed** | 9333.449716 s | failed_seconds=900 |
| 2026-09-16 | bookTicker | l1_sparsity | **degraded** | 3075.739 s (interior) | degraded_seconds=30 |
| 2026-09-16 | trade | reconciliation | **degraded** | 6.757898 % missing_from_capture | degraded_pct=0.5 |
| 2026-09-17 | bookTicker | gap_coverage | **failed** | 6136.80316 s | failed_seconds=900 |
| 2026-09-17 | bookTicker | l1_sparsity | **degraded** | 3173.948 s (interior) | degraded_seconds=30 |
| 2026-09-17 | trade | reconciliation | **degraded** | 6.444351 % missing_from_capture | degraded_pct=0.5 |
| 2026-09-18 | bookTicker | gap_coverage | **failed** | 17733.028922 s | failed_seconds=900 |
| 2026-09-18 | bookTicker | l1_sparsity | **degraded** | 9460.449 s (interior) | degraded_seconds=30 |
| 2026-09-18 | trade | reconciliation | **degraded** | 15.543947 % missing_from_capture | degraded_pct=0.5 |
| 2026-09-19 | bookTicker | gap_coverage | **failed** | 17816.669779 s | failed_seconds=900 |
| 2026-09-19 | bookTicker | l1_sparsity | **degraded** | 17636.996 s (interior) | degraded_seconds=30 |
| 2026-09-19 | trade | reconciliation | **degraded** | 15.769019 % missing_from_capture | degraded_pct=0.5 |

Every other check on these eight partitions is `ok` or `n/a` (etime/event_time/rtime plausibility all `ok`; `na_placeholder` `ok` at 0.32-0.52%; `crossed_locked_book`, `probable_loss` and trade `gap_coverage` all `n/a`/informational). Not summarized away -- the full 13-row-per-date report tables were inspected directly for all four dates.

**Cross-checked against the raw gap ledger** (`ledger_version >= 2`, `__connection__`/`__power__` rows, `capture/gap_ledger/ledger.parquet`) to rule out these numbers being an artifact of this run rather than real outages:

| date | `__power__` events | `__connection__` rows | max single connection-gap |
|---|---|---|---|
| 2026-09-16 | 2 | 25 | 4761.57 s *(spans the 09-16/09-17 boundary)* |
| 2026-09-17 | 2 | 20 | 4761.57 s *(same outage, counted on both adjacent days)* |
| 2026-09-18 | 4 | 30 | 9522.76 s |
| 2026-09-19 | 1 | 6 | 17637.22 s |

These are real, multi-hour outages consistent with STATE.md's ongoing battery-sleep problem (the `__power__` events), not a defect in this plan's ingest.

### 2026-09-19's gap picture (D-05-19's own acceptance line)

- L1 part-file count: **2,784**, against 7,248 / 5,636 / 6,293 for 09-16/17/18 -- matches 05-RESEARCH.md's measured baseline exactly. **Safety valve not triggered** (2,784 is nowhere near the "under 500" anomaly threshold; this is the known partial-gap case, not an unexplained one).
- The low file count is fully explained by the DQ numbers above: a single 17,637 s (4.9 h) connection-silent outage that UTC day, `l1_sparsity` interior gap measured at 17,636.996 s (essentially the same event, measured two different ways), `gap_coverage` at 17,816.67 s total silence -- roughly half the day's 86,400 s had no L1 at all.

No acknowledgement was written for any of the eight partitions (constraint 11).

## Task 2: Feature build attempted for 2026-09-15..18

`build_features_range` does not catch `DQPauseError` by design (it propagates uncaught, aborting the whole range call at the first refusal) -- so each date was built individually via `build_features_day`, catching `NextDayUnavailableError`/`QuarantinedDateError`/`DQPauseError`/`CuratedInputMissingError`/`FileExistsError` per call, so every date's own refusal is attributable rather than the range silently truncating after the first failure. This is a real gap between the plan's literal `<action>` text (which calls `build_features_range` as one call) and the module's actual, documented behavior -- read and confirmed in `features/build.py` before running anything, per the plan's own `<read_first>` instruction.

**All four dates refused, verbatim:**

```
REFUSED (DQ pause) 2026-09-15: DQ pause: BTCUSDT.bookTicker has unacknowledged day(s):
2026-09-16: failed (findings gap_coverage=failed, l1_sparsity=degraded; no acknowledgement
file). Add a valid, git-committed acknowledgement JSON (date, symbol, stream, reason, who,
when, acknowledged; e.g. .../dq_acknowledgements/BTCUSDT__bookTicker__2026-09-16.json) to
proceed.

REFUSED (DQ pause) 2026-09-16: DQ pause: BTCUSDT.bookTicker has unacknowledged day(s):
2026-09-16: failed (findings gap_coverage=failed, l1_sparsity=degraded; no acknowledgement
file). ...

REFUSED (DQ pause) 2026-09-17: DQ pause: BTCUSDT.bookTicker has unacknowledged day(s):
2026-09-17: failed (findings gap_coverage=failed, l1_sparsity=degraded; no acknowledgement
file). ...

REFUSED (DQ pause) 2026-09-18: DQ pause: BTCUSDT.bookTicker has unacknowledged day(s):
2026-09-18: failed (findings gap_coverage=failed, l1_sparsity=degraded; no acknowledgement
file). ...
```

**Why 2026-09-15 fails on 2026-09-16 rather than on itself:** 2026-09-15's own bookTicker and trade DQ verdicts are ALSO non-ok (pre-existing from Phase 3/4: `gap_coverage=failed` at 9642.13s, `l1_sparsity=degraded` at 6052.63s, `reconciliation=degraded` at 6.36%) -- but 2026-09-15 already has committed, git-tracked acknowledgements (`BTCUSDT__bookTicker__2026-09-15.json`, `BTCUSDT__trade__2026-09-15.json`) from a prior phase. So `build_features_day`'s own-day load (step 3) succeeds for 2026-09-15, and it fails one step later, at the D+1 label-tail load (`next_day_quote_series` -> `load_curated` on 2026-09-16), which this plan's own new, unacknowledged verdict now pauses. 2026-09-16/17/18 fail immediately at their own-day load, since none of them has any acknowledgement.

**Zero new feature days built.** No `lake/features/date=2026-09-1{5,6,7,8}/` partition exists, no partial/staged file was left behind (the refusal fires before step 8's first write in every case), `find features -name '*.nb[ci]'` prints nothing, and `git status --short` shows no changes from this task -- there is nothing to commit for Task 2.

### The plan's own automated verify script, run and reported honestly

```
3/7 features manifests resolve, total rows=22381684; built=['2026-09-12', '2026-09-13', '2026-09-14']; unbuilt=['2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18']
PLAN VERIFY ASSERTION: FAILED -- expected at least one new day to build, built=['2026-09-12', '2026-09-13', '2026-09-14']
```

The plan's own embedded `assert len(built) >= 4` fails, because zero new days built. This is the honest, correct outcome given constraint 11 (this executor never writes an acknowledgement) and the real DQ picture measured above -- **not** a defect papered over. The plan's `<acceptance_criteria>` for Task 2 explicitly anticipates "fewer only because a verdict paused a day, with each unbuilt day named and its cause stated" -- satisfied, at the boundary case of "fewer" being zero. The three pre-existing days (2026-09-12/13/14, 22,381,684 rows total) are unchanged and still resolve.

## ACK NEEDED FROM USER

Eight curated partitions across four dates carry a non-ok DQ verdict. No acknowledgement was written by this plan (constraint 11 -- a DQ acknowledgement is a human decision). Until at least 2026-09-16's bookTicker and trade (and, separately, 2026-09-19's, since 2026-09-18's own build also needs 2026-09-19 as its D+1) are acknowledged, none of 2026-09-15..18 can build as feature days.

| date | stream | verdict | findings (check=status) | key numbers |
|---|---|---|---|---|
| 2026-09-16 | bookTicker | failed/degraded | gap_coverage=failed, l1_sparsity=degraded | 9333.45s outage; 3075.74s max interior gap; 2 `__power__` events, 25 `__connection__` ledger rows |
| 2026-09-16 | trade | degraded | reconciliation=degraded | 6.76% missing_from_capture |
| 2026-09-17 | bookTicker | failed/degraded | gap_coverage=failed, l1_sparsity=degraded | 6136.80s outage; 3173.95s max interior gap; 2 `__power__` events, 20 `__connection__` ledger rows |
| 2026-09-17 | trade | degraded | reconciliation=degraded | 6.44% missing_from_capture |
| 2026-09-18 | bookTicker | failed/degraded | gap_coverage=failed, l1_sparsity=degraded | 17733.03s outage; 9460.45s max interior gap; 4 `__power__` events, 30 `__connection__` ledger rows |
| 2026-09-18 | trade | degraded | reconciliation=degraded | 15.54% missing_from_capture |
| 2026-09-19 | bookTicker | failed/degraded | gap_coverage=failed, l1_sparsity=degraded | 17816.67s outage (~half the UTC day); 17636.996s max interior gap; 1 `__power__` event, 6 `__connection__` ledger rows; 2,784 L1 part files vs 5,636-7,248 for neighbours |
| 2026-09-19 | trade | degraded | reconciliation=degraded | 15.77% missing_from_capture |

The decision (whether/how to acknowledge each day, citing these measured numbers, never a threshold edit) belongs to the user, per the orchestrator's normal flow.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `materialize_seq` raised on a real capture-redelivery duplicate row**
- **Found during:** Task 1, first `build_curated_range(stream="bookTicker")` call, before any curated day was written.
- **Issue:** `materialize_seq`'s "duplicate id is impossible" premise is empirically false for capture-sourced data -- STATE.md's own BoundedDedup design note predicts a TTL-lapsed redelivery will not be deduped by the daemon. One real occurrence on 2026-09-16 (`update_id=11569010063437`, identical content, `seq` 687/688, `rtime` 147.3s apart).
- **Fix:** `_drop_capture_redelivery_duplicates` in `data/ingest/curated_build.py`, gated on `chosen_source == "capture"`, drops rows duplicated on every column except `seq`/`rtime`, keeping the lowest `seq`; a content-differing duplicate id is left untouched and still raises via `materialize_seq`. Wired into `build_curated_day` and `recompute_build_stats`; the dropped count is recorded as `capture_redelivery_rows_dropped` in `build_stats.json`.
- **Files modified:** `mvp/data/ingest/curated_build.py`, `mvp/tests/ingest/test_curated_build_multi_day.py` (3 new tests).
- **Verification:** Full suite 956/956 passed (+3 from baseline 953); real re-run against 2026-09-16 dropped exactly 1 row (36,234,203 raw -> 36,234,202 written).
- **Committed in:** `039d7c3` (advisor-consulted before writing the fix).

**2. [Rule 3 - Blocking issue] Trade archive not downloaded before the plan's literal `build_curated_range(stream="trade")` call**
- **Found during:** Task 1, after the first trade `build_curated_range` call returned `chosen_source="capture"` for all four dates despite 05-RESEARCH.md's own HTTP-200 probes.
- **Issue:** The plan's condensed `<action>` text names only the two `build_curated_range` calls; it omits 05-RESEARCH.md Q4(b)'s explicit downloader step. `build_curated_day` never reaches the network -- it reads only a local `raw/.../source=archive/` partition, which did not exist yet for these four dates.
- **Fix:** Ran `python -m data.backfill.downloader --symbol BTCUSDT --start 2026-09-16 --end 2026-09-19 -v` (real network download, 13,180,397 rows written, 4/4 dates), then re-ran `build_curated_range(stream="trade", ...)`, which superseded all four days from capture-sourced to archive-sourced.
- **Files modified:** none (data-only; raw archive partitions live under `lake_root()/raw/`, not git-tracked).
- **Verification:** All four trade days report `status="superseded"`, `chosen_source="archive"`, each naming its `superseded_manifest_id` (the prior capture-sourced manifest, which still resolves, write-once).
- **Committed in:** `fdd124c` (the resulting archive-sourced manifests).

**3. [Rule 1 - Bug] `tests/dq/test_checks.py`'s hardcoded real curated-manifest count**
- **Found during:** Task 1, first commit attempt (pre-commit `pytest` hook).
- **Issue:** `test_every_real_curated_manifest_still_has_a_known_source` asserted `len(curated) == 111` and `counts == {"capture": 4, "archive": 107}` -- stale now that 8 new curated by-date pointers exist.
- **Fix:** Updated to `119` / `{"capture": 8, "archive": 111}`, matching this plan's real additions exactly (4 new bookTicker capture days + 4 new trade days, now archive-sourced after the supersede).
- **Files modified:** `mvp/tests/dq/test_checks.py`.
- **Verification:** Full suite 956/956 passed; same class of fix as 04-05-SUMMARY's Deviation 4 for the features tier.
- **Committed in:** `fdd124c`.

---

**Total deviations:** 3 auto-fixed (1 Rule 1 bug in production code, 1 Rule 3 blocking-issue completion of the plan's own stated intent, 1 Rule 1 test-count correction). No architectural change, no Rule 4 checkpoint. All three were necessary for the plan's stated deliverables (a genuinely archive-sourced trade tier; a green, honest test suite) to be real rather than nominal.

## Issues Encountered

None blocking beyond the three documented deviations above. Task 2 produced a fully negative result (zero feature days built) -- this is the correct, honest outcome given the real DQ picture and constraint 11, not an issue to resolve within this plan.

## Known Stubs

None. This plan writes no code path that renders or serves data; it only ingests and reports.

## Threat Flags

None. `data.backfill.downloader` against `data.binance.vision` is the project's existing, already-assessed archive ingestion path (T-05-03 in this plan's own threat model, disposition `accept`); no new endpoint, auth path, or schema at a trust boundary was introduced. The one code change (`_drop_capture_redelivery_duplicates`) narrows an existing write-once guarantee's scope (still write-once; still raises on any genuine anomaly) rather than widening any trust boundary.

## Next Phase Readiness

- The curated tier now covers `2026-09-12 -> 2026-09-19` for bookTicker (8 days) and the full archive range plus these 4 days for trade (111 archive + a handful of remaining capture-only days). This is real widening, on the real lake, git-committed.
- **The feature-tier pool is UNCHANGED at 3 days (2026-09-12..14, 22,381,684 rows)** -- this plan's stated objective ("give Phase 7+ a 7-day training pool") is not yet achieved; it is blocked on the ACK NEEDED FROM USER decision above, not on any remaining engineering work in this plan.
- Per the plan's own objective, no other Phase 5 plan reads this plan's output -- P1-P7 use either the original three built days or `tmp_path` synthetic fixtures. This is confirmed unaffected by Task 2's zero-new-days outcome.
- The `_drop_capture_redelivery_duplicates` fix is now load-bearing for any FUTURE capture-sourced curated build (this project's capture-sourced ingest has run for months and this is the first time this exact redelivery pattern was hit; it will very likely recur, since the BoundedDedup TTL tradeoff is structural, not a one-off).
- If the user acknowledges 2026-09-16's and 2026-09-19's DQ findings (the two dates whose own-day or D+1-tail status blocks the chain), a follow-up run of Task 2's per-date `build_features_day` loop (same code, same code_hash if HEAD hasn't moved on pipeline files) would very likely build 2026-09-15..18 in one pass -- no re-investigation needed, just re-running the already-written loop after the acknowledgement files land.

## Self-Check: PASSED

- `mvp/data/ingest/curated_build.py` -- modified, `_drop_capture_redelivery_duplicates` present (`grep -n "_drop_capture_redelivery_duplicates" mvp/data/ingest/curated_build.py` finds 4 matches: 1 def, 2 call sites, 1 explanatory comment).
- `mvp/tests/ingest/test_curated_build_multi_day.py` -- 3 new tests present and passing.
- `mvp/tests/dq/test_checks.py` -- count assertions read `119` / `{"capture": 8, "archive": 111}`.
- 8 new curated manifests + 8 by-date pointers under `mvp/data/lake_registry/manifests/BTCUSDT.{bookTicker,trade}/` -- confirmed present via the plan's own verify script (`8/8 new curated by-date pointers present`).
- Commits `039d7c3` and `fdd124c` present in `git log --oneline` on `feature/phase-05-fold-harness-overfitting-controls`.
- `find mvp/features -name '*.nb[ci]'` -- empty, confirmed after every features-importing Bash call in this plan.
- No file exists under `mvp/data/lake_registry/dq_acknowledgements/` for any of the eight new dates -- confirmed via `ls`.
- No `lake/features/date=2026-09-1{5,6,7,8}/` partition exists -- confirmed via `find`.
- Full suite: 956 passed (953 baseline + 3 new), 0 failed, both times it was run in this plan.
