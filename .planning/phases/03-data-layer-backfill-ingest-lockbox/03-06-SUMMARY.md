---
phase: 03-data-layer-backfill-ingest-lockbox
plan: 06
subsystem: infra
tags: [zstd, capture, schema-migration, polars, websockets, daemon-restart]

# Dependency graph
requires:
  - phase: 03-01
    provides: "ms_to_ns renamed public, tests/capture suite baseline"
provides:
  - "mvp/data/schema.py: SCHEMA_VERSION=2, TRADE_SCHEMA.exec_type: pl.Utf8"
  - "mvp/data/capture/parse.py: parse_trade populates exec_type from data[\"X\"], required field"
  - "mvp/data/capture/seq.py: resume_seq_assigner projects to seq before concat, tolerates mixed-schema-version date=... directories"
  - "mvp/data/capture/ws_client.py: RawArchiveWriter fixed -- one compressor per file, FLUSH_BLOCK/FLUSH_FRAME cadence"
  - "mvp/tools/reframe_raw_archive.py: streaming offline re-framer with line-sequence-identical assertion and atomic swap"
  - "mvp/data/ingest/normalize.py: archive-sourced rows carry exec_type=null (schema_version=1 literal, unaffected by capture's SCHEMA_VERSION)"
  - "Real re-framing evidence: date=2026-09-12 (both connections) and date=2026-09-13 (both connections) re-framed into a scratch copy, NOT the live archive -- see CHECKPOINT EVIDENCE"
affects: ["03-04", "04"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "RawArchiveWriter: one long-lived ZstdCompressor().stream_writer per open file (not per message). flush(FLUSH_BLOCK) after every message for durability without a hard frame boundary; flush(FLUSH_FRAME) every 500 messages or 5s (whichever first, both configurable) to bound how much of the tail a crash can leave undecoded by readers that need a real frame boundary. Rotation (UTC date change) and close() both force a final FLUSH_FRAME."
    - "tools/reframe_raw_archive.py: streaming throughout -- iter_lines() is a bounded-chunk generator (never .read()s a whole file), the identity check streams both original and reframed output in lockstep (_first_line_mismatch), so memory use stays bounded (~45MB RSS observed) regardless of file size (tens of GB)."
    - "Schema-version migration pattern (reaffirms 03-01/03-02's precedent): readers spanning both TRADE_SCHEMA v1 (no exec_type) and v2 (exec_type present) project to only the columns they need BEFORE any concat/union, rather than reaching for diagonal_relaxed -- used here in resume_seq_assigner (.select(\"seq\")), matching curated_build.py's existing schema_version-conditional NA-filter logic."
    - "Empty zstd frames at a graceful-shutdown boundary (RawArchiveWriter._close_writer()'s final FLUSH_FRAME + writer.close(), when 0 messages have accumulated since the last flush) are transparently skipped by read_across_frames=True -- verified synthetically (cctx.compress(b\"a\\n\") + cctx.compress(b\"\") + cctx.compress(b\"b\\n\") decodes to b\"a\\nb\\n\" across 3 frames). Post-Task-3-restart files will contain these mid-stream, not just at EOF -- expected and harmless."

key-files:
  created:
    - mvp/tools/reframe_raw_archive.py
    - mvp/tests/capture/test_ws_client_raw_archive.py
    - mvp/tests/tools/test_reframe_raw_archive.py
    - .planning/phases/03-data-layer-backfill-ingest-lockbox/deferred-items.md
  modified:
    - mvp/data/schema.py
    - mvp/data/capture/parse.py
    - mvp/data/capture/seq.py
    - mvp/data/capture/ws_client.py
    - mvp/data/ingest/normalize.py
    - mvp/tests/capture/test_parse.py
    - mvp/tests/capture/test_seq_resume.py

key-decisions:
  - "Real re-framing run operated on a COPY under /Volumes/ProjectsSSD/aihedgefund/reframe_work/, never in place on the live capture/raw/ tree -- overrides 03-06-PLAN.md's literal 'run the tool for real against the existing archive' text, per the executor's explicit safety instruction that capture/ is read-only until a human approves the Task 3 restart. The production swap is a follow-up, human-directed action, not automated here."
  - "mvp/tests/tools/__init__.py (listed in the plan's files_modified) was NOT created -- would collide with the real top-level tools package under pytest's import-mode resolution (tests/ has no __init__.py, so tests/tools/__init__.py would make the test module resolve as top-level 'tools', not 'tests.tools'). Reproduced the ModuleNotFoundError before removing it."
  - "Fixed a pre-existing check_no_manifest_rewrite fast-mode (mtime+size) false positive on the 03-02 curated partition, caused by the prior wave's own RP-2 red-proof restore (byte-identical content, new mtime) -- reset the partition's mtime to the manifest's recorded value via os.utime, verified both fast and --full (sha256) guard modes pass before and after. Logged as a deferred-items.md follow-up (the guard's fast mode has no tolerance for a legitimate identical-content restore)."
  - "normalize.py's archive-CSV writer (write_raw_partition's input) gained exec_type=null -- archive CSVs have no execution-type field at all, and TRADE_SCHEMA's v2 column set is now selected unconditionally by that path; archive rows keep the literal schema_version=1 regardless of live capture's SCHEMA_VERSION (curated_build.py already reads exec_type conditionally on schema_version==2)."

requirements-completed: []
# DATA-02, DATA-07 intentionally NOT marked complete here. Task 3's checkpoint
# (the daemon restart shipping schema v2 + the fixed writer) is pending human
# approval, per the same pattern as every prior live-daemon checkpoint in this
# project (01-02, 01-03, 01-04). Tasks 1-2 are done, committed, and verified.

duration: ~2h (tool-call wall time; includes reading 8 source files + 5 planning
  docs, one advisor consultation before implementation, a zstandard FLUSH_BLOCK/
  FLUSH_FRAME prototype, two TDD-style RED/GREEN cycles, a pre-commit-hook-driven
  manifest-guard investigation + second advisor consultation, real re-framing
  runs against ~5.5GB of copied archive data (one ~97s foreground + one ~5min
  background), and this SUMMARY's own real-data verification queries)
completed: 2026-09-16
---

# Phase 3 Plan 06: Capture Schema v2 + RawArchiveWriter Zstd Framing Fix Summary

**`TRADE_SCHEMA` gains an explicit `exec_type` column (schema_version 1->2) replacing the ambiguous `price==0 AND qty==0` NA-placeholder heuristic; `RawArchiveWriter` moves from one zstd compressor per message (~1.52x measured) to one compressor per file with `FLUSH_BLOCK`/`FLUSH_FRAME` cadence (~8.9x measured on real data, ~5.85-6.04x improvement over the old per-message framing); a new streaming offline re-framer tool re-frames existing archive files with a proven line-sequence-identical guarantee. Tasks 1-2 are complete, committed, and fully verified against real captured data. Task 3 (the live daemon restart that ships both changes) is a checkpoint awaiting human approval -- the daemon is untouched, still running as PID 57329.**

## Performance

- **Duration:** ~2h (tool-call wall time, see frontmatter)
- **Completed:** 2026-09-16 (Tasks 1-2; Task 3 pending human approval)
- **Tasks:** 2/2 automated tasks completed and committed; Task 3 (checkpoint) evidence gathered below, daemon left running untouched
- **Files modified:** 11 (4 created, 7 modified) plus this SUMMARY and deferred-items.md

## Accomplishments

- `schema.py`/`parse.py`: `TRADE_SCHEMA` gains `exec_type: pl.Utf8` at `SCHEMA_VERSION=2`, populated by `parse_trade` from the live frame's `X` field (required, not optional -- a frame missing it raises `FrameParseError` like every other required field). Documented that the shared `SCHEMA_VERSION` constant means `BOOKTICKER_SCHEMA` rows also report `schema_version=2` after the restart, per the existing locked contract.
- `seq.py`: `resume_seq_assigner` projects to `.select("seq")` before concatenating candidate partition files -- belt-and-braces so a `date=...` directory straddling the Task 3 restart (mixing pre-restart files with no `exec_type` and post-restart files with it) resolves seq resume without hitting `SchemaError`/`ShapeError`.
- Rule 1 fix found in scope: `normalize.py`'s archive-CSV writer selects `TRADE_SCHEMA`'s full column set, which now includes `exec_type` -- archive CSVs have no execution-type field, so this broke `tests/ingest/test_normalize.py` the moment `SCHEMA_VERSION` bumped. Fixed with an explicit `exec_type=null` literal (archive rows keep `schema_version=1` regardless of live capture's version).
- `ws_client.py`'s `RawArchiveWriter`: one long-lived `ZstdCompressor().stream_writer` per open file (not per message). `flush(FLUSH_BLOCK)` after every message; `flush(FLUSH_FRAME)` every 500 messages or 5s, whichever first (both configurable, `DEFAULT_FLUSH_FRAME_EVERY_MESSAGES`/`DEFAULT_FLUSH_FRAME_EVERY_SECONDS`). Rotation (UTC date change) and `close()` both force a final `FLUSH_FRAME`.
- New `tools/reframe_raw_archive.py`: streaming (bounded-chunk generator, never loads a whole file) offline re-framer. Decompresses via `read_across_frames=True` (works for both the old per-message-frame format and the new shared-context format), re-writes through the new framing, asserts the decompressed line sequence is identical via a **lockstep streamed comparison** (not two full loads), then does an atomic `.tmp`+`Path.replace()` swap. Raises and leaves the original completely untouched on any mismatch. `--skip-active` (default on) excludes today's UTC `date=...` directory and anything modified in the last 5 minutes.
- **Real re-framing run**, on a COPY (see Deviations -- this overrides the plan's literal "run against the existing archive" instruction per the executor's explicit safety rule): `date=2026-09-12` (both `conn_A` and `conn_B`, 1.78GB total) and `date=2026-09-13` (both connections, 6.94GB total) re-framed under `/Volumes/ProjectsSSD/aihedgefund/reframe_work/raw/`, never touching `/Volumes/ProjectsSSD/aihedgefund/capture/`. Measured compression and an **independent** (not just the tool's internal assertion) line-identity proof below.

## Task Commits

1. **Task 1: TRADE_SCHEMA v2 (exec_type) + resume_seq_assigner projection fix** - `220cf10` (feat)
2. **Task 2: RawArchiveWriter zstd framing fix + offline re-framer tool** - `c19f12c` (feat)

_TDD gate compliance note: see below -- both commits are single `feat` commits containing both tests and implementation, not separate RED/GREEN commits, because this repo's pre-commit hooks run the full test suite and block any commit where it fails. A literal failing-test RED commit is structurally impossible here._

## Files Created/Modified

- `mvp/data/schema.py` - `SCHEMA_VERSION=2`, `TRADE_SCHEMA.exec_type: pl.Utf8`, docstring update
- `mvp/data/capture/parse.py` - `parse_trade` populates `exec_type` from `data["X"]`
- `mvp/data/capture/seq.py` - `resume_seq_assigner`'s candidate scan projects to `seq` before concat
- `mvp/data/capture/ws_client.py` - `RawArchiveWriter` rewritten: one compressor per file, `FLUSH_BLOCK`/`FLUSH_FRAME` cadence, configurable defaults
- `mvp/data/ingest/normalize.py` - archive rows get `exec_type=null` (Rule 1 fix)
- `mvp/tools/reframe_raw_archive.py` - new: `iter_lines`, `write_reframed`, `reframe_file`, `is_active_file`, `main`
- `mvp/tests/capture/test_parse.py` - +4 tests (exec_type population, NA placeholder, missing-X error, DataFrame construction regression guard)
- `mvp/tests/capture/test_seq_resume.py` - +1 test (mixed schema-version directory)
- `mvp/tests/capture/test_ws_client_raw_archive.py` - new, 5 tests (round-trip, compression ratio floor, frame cadence, crash-truncation recovery)
- `mvp/tests/tools/test_reframe_raw_archive.py` - new, 6 tests (old-format read, identity round-trip, mismatch-leaves-original-untouched, active-file exclusion x2, `main()` integration)

## Decisions Made

See frontmatter `key-decisions`. In short: the real re-framing run targeted a scratch copy, never the live `capture/` tree (safety override); `tests/tools/__init__.py` was dropped to avoid a real package-name collision; a pre-existing, out-of-plan manifest-guard false positive was fixed with evidence (both guard modes verified) rather than deferred, because it blocked every commit in the repo; `normalize.py` needed a one-line `exec_type=null` fix to keep reading across the v1/v2 schema boundary.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug, found in scope] `normalize.py`'s archive writer broke on the schema bump**
- **Found during:** Task 1, running the full suite after the schema change (not just `tests/capture`)
- **Issue:** `normalize_archive_trades` does `df.select(list(TRADE_SCHEMA.keys())).cast(TRADE_SCHEMA)` — selecting `TRADE_SCHEMA`'s full column set. The moment `TRADE_SCHEMA` gained `exec_type`, this raised `polars.exceptions.ColumnNotFoundError: unable to find column "exec_type"` because archive CSVs have no execution-type field at all. `tests/ingest/test_normalize.py::test_normalize_archive_trades_shapes_rows_like_trade_schema` failed.
- **Fix:** Added `pl.lit(None, dtype=pl.Utf8).alias("exec_type")` to the row-construction `select(...)`. Archive rows keep the literal `schema_version=1` (independent of live capture's `SCHEMA_VERSION`, by design per the existing code) with `exec_type=null` — `curated_build.py` already only reads `exec_type` conditionally on `schema_version==2`, so this is a correct null, not a fabricated value.
- **Files modified:** `mvp/data/ingest/normalize.py`
- **Verification:** `tests/ingest` 22/22 passing (was failing 1/9 at first check); full suite 246 passed after this fix.
- **Committed in:** `220cf10`

**2. [Rule 1 - Bug, out-of-plan-scope blocker] `check_no_manifest_rewrite` fast-mode false positive from a prior wave's own red-proof**
- **Found during:** Task 1's first commit attempt -- pre-commit hook blocked before any code was staged, unrelated to this plan's files
- **Issue:** `check_no_manifest_rewrite` (fast mode, mtime+size) failed on `data/lake_registry/manifests/BTCUSDT.trade/...json` -> `curated/symbol=BTCUSDT/stream=trade/date=2026-09-12/part-...parquet`. Investigated: `--full` (sha256) mode passed cleanly -- content byte-identical. Root cause (confirmed via advisor consultation): the 03-01/03-02 orchestrator's own RP-2 red-proof (append one byte to the curated partition, confirm `store.load_curated` raises `ManifestHashMismatch`, then restore) left the restored file's mtime ~5 minutes newer than the manifest's recorded `mtime_ns`, despite identical content. This blocked EVERY commit in the repo, not just this plan's.
- **Fix:** `os.utime()` reset the one affected partition file's mtime to the manifest's recorded `mtime_ns` (content untouched). Verified BOTH guard modes pass before proceeding:
  ```
  $ ./.venv/bin/python3 -m tools.check_no_manifest_rewrite
  checked 1 manifest(s), mode=fast (mtime+size)
  $ ./.venv/bin/python3 -m tools.check_no_manifest_rewrite --full
  checked 1 manifest(s), mode=full (sha256)
  ```
- **Files modified:** none (filesystem metadata only, outside git); logged the guard's structural fragility to `deferred-items.md` (fast mode has no tolerance for a legitimate identical-content restore -- a real fix would fall back to sha256 on mtime mismatch before failing).
- **Committed in:** N/A (no git-tracked change; verified live before `220cf10`)

**3. [Rule 1 - hard_constraints violation avoided] `mvp/tests/tools/__init__.py` not created**
- **Found during:** Task 2, first `pytest tests/tools/test_reframe_raw_archive.py` run
- **Issue:** 03-06-PLAN.md's `files_modified` literally lists `mvp/tests/tools/__init__.py`. Creating it (matching every other `tests/<x>/__init__.py` in this repo) caused `ModuleNotFoundError: No module named 'tools.reframe_raw_archive'` under pytest collection: `tests/` has no `__init__.py`, so pytest's import-mode resolves the topmost package boundary at `tests/tools/`, making the test module import as top-level `tools.test_reframe_raw_archive` -- colliding with the real `mvp/tools` package (`hard_constraints`: "No `__init__.py` in a test dir colliding with a real package name"). Reproduced with `python -c "import tools.reframe_raw_archive"` (succeeds standalone) vs. the pytest failure, confirming it was pytest's import-mode resolution, not a missing module.
- **Fix:** Deleted `mvp/tests/tools/__init__.py`. `tests/tools/test_reframe_raw_archive.py` now resolves as a rootless top-level test module (same mechanism `pythonpath = ["."]` already provides), no collision.
- **Files modified:** `mvp/tests/tools/` (no `__init__.py` present, unlike `tests/capture`, `tests/ingest`, `tests/store` etc. -- deliberately, not an oversight)
- **Verification:** `pytest tests/tools/test_reframe_raw_archive.py` -- 6/6 passing after removal.
- **Committed in:** `c19f12c` (the `__init__.py` was never added to git)

**4. [Safety override, not a Rule 1-3 auto-fix -- explicit instruction] Real re-framing run targeted a copy, not the live archive**
- **Found during:** Before starting Task 2's "real run" against the archive
- **Issue:** 03-06-PLAN.md's Task 2 literally says: "Run the tool for real against the existing ~21 GB raw archive (excluding today's active file)" -- implying an in-place swap on the live `capture/raw/` tree. The executor's own system instructions explicitly and unconditionally forbid this: "`Do NOT write, move, delete or rename anything under /Volumes/ProjectsSSD/aihedgefund/capture/. That whole tree is READ-ONLY to you`" and "`The offline re-framing work in Task 2 must operate on a copy written somewhere else... never in place on the live archive.`"
- **Fix:** Copied `date=2026-09-12` and `date=2026-09-13` (both connections each) to `/Volumes/ProjectsSSD/aihedgefund/reframe_work/raw/`, ran the real tool there, never against `capture/`. The production swap onto the live tree is deferred to a human-directed follow-up action (not part of this plan's automated scope under the safety override), most naturally as part of or after Task 3's approved restart.
- **Files modified:** none under `capture/` (confirmed read-only, untouched throughout). New scratch files under `/Volumes/ProjectsSSD/aihedgefund/reframe_work/` (outside the repo, outside `capture/`).
- **Verification:** see "Real Re-Framing Run" section below -- measured before/after sizes, independent line-identity proof, and a real-data crash-truncation-recovery proof.

---

**Total deviations:** 4 (1 Rule-1 bug in scope, 1 Rule-1 bug found blocking-but-out-of-plan-scope, 1 hard-constraint-driven omission, 1 explicit safety override). **Impact on plan:** All four are necessary for correctness, for unblocking every commit in the repo, or for honoring an explicit, non-negotiable safety instruction that takes precedence over the plan's literal text. No scope creep beyond what each required.

## TDD Gate Compliance

Plan frontmatter marks Task 1 `tdd="true"`. RED was genuinely run and genuinely failed before any implementation:

```
$ uv run pytest tests/capture/test_parse.py -x -q
.....F
tests/capture/test_parse.py:58: KeyError: 'exec_type'
1 failed, 4 passed in 0.20s
```

However, **a separate RED commit was not made**, and this is structurally impossible in this repo, not a shortcut: pre-commit's `pytest (tests, via testpaths)` hook runs the FULL suite and blocks any commit where it fails (confirmed live -- attempting the RED commit produced `pytest ... Failed` and the hook rejected it, per the transcript). 01-04-SUMMARY.md documents the identical situation and the same resolution: implement GREEN first, then make one commit containing both the test and the implementation, disclosing that RED was verified but not separately committed. Both `220cf10` and `c19f12c` follow this pattern; both commit messages state the RED evidence inline.

Task 2 has no `tdd="true"` marker in the plan frontmatter (plain `type="auto"`), so no RED/GREEN gate applies there -- tests were still written and run before considering the task done.

## Real Re-Framing Run (Task 2, against a COPY -- see Deviation 4)

**Setup:** `/Volumes/ProjectsSSD/aihedgefund/reframe_work/` created outside `capture/`. `cp -R` (APFS clonefile, near-instant) of `date=2026-09-12` and `date=2026-09-13` from the live archive (read-only source access, zero writes to `capture/`). `--no-skip-active` used deliberately on the copies: `cp` resets mtime to "now," so the default 5-minute-recency guard would have skipped everything in the copy -- the guard's default-on behavior (and that it *would* have blocked these copies) is separately exercised by `test_main_skips_active_and_reframes_the_rest` and `test_is_active_file_belt_and_braces_recent_mtime_outside_today_dir`.

### Measured compression, real data

| File | Lines | Raw NDJSON | Old (per-msg-frame) | New (shared-context) | raw->old | raw->new | old->new reduction |
|---|---|---|---|---|---|---|---|
| `date=2026-09-12/conn_B` | 1,975,792 | 588,186,652 B | 386,166,396 B | 65,957,125 B | **1.52x** | **8.92x** | 5.85x |
| `date=2026-09-12/conn_A` | 7,787,895 | (not separately measured) | 1,526,377,176 B | 262,682,989 B | -- | -- | 5.81x |
| `date=2026-09-13/conn_A` | 18,583,821 | -- | 3,644,749,506 B | 603,715,477 B | -- | -- | 6.04x |
| `date=2026-09-13/conn_B` | 18,583,822 | -- | 3,635,740,673 B | 603,105,917 B | -- | -- | 6.03x |
| **date=13 combined** | 37,167,643 | -- | 7,280,490,179 B | 1,206,821,394 B | -- | -- | **6.03x** |

`conn_B`'s raw->old (1.52x) and raw->new (8.92x) ratios match PROBE-RESULTS.md section 4's predictions almost exactly ("~1.5x instead of the ~10x zstd gives"). The old->new size-reduction factor (what a human directly compares against current disk usage) is consistently **5.8-6.0x** across all four files measured.

**Disk-fill horizon, recomputed with this plan's fix:** current growth ~15 GB/day (old framing) -> ~15/5.85 ~= **2.56 GB/day** (new framing). At 832 GiB currently free: **~325 days** before the volume fills, comfortably past the 3-month backfill-history target this plan exists to protect (PROBE-RESULTS.md's ~2-month old-framing horizon).

### Independent line-sequence-identical proof (not just the tool's internal assertion)

Before running the tool against `date=2026-09-12/conn_B`, a **second, separate copy** of the original was preserved (`reframe_work/orig_backup/conn_B_original.ndjson.zst`). After the tool ran (and swapped the reframed result into `reframe_work/raw/date=2026-09-12/conn_B.ndjson.zst`), an independent script -- not `reframe_file`'s own internal `_first_line_mismatch` check -- streamed both files' decompressed lines in lockstep:

```
total lines compared: 1,975,792
mismatches: 0
original size: 386,166,396   reframed size: 65,957,125
```

Zero mismatches across the full 1,975,792-line real trade/bookTicker stream, independently confirming what the tool's own internal assertion (which must pass or it raises) already required.

### Crash-mid-write recovery proof, on real data (not just the synthetic unit test)

Using the already-reframed real `conn_B` file: located all 3,953 zstd frame boundaries (via `decompressobj()`/`unused_data` walk), then truncated the file at a byte offset strictly inside a frame roughly halfway through the file (not the tiny empty closing frame at EOF):

```
frame boundary before cut:  33,322,222
frame boundary after cut:   33,339,732
cut applied at:              33,330,977  (inside that frame)

lines recoverable via decompression up to the LAST CLEAN frame boundary before the cut: 988,500
lines actually recovered from the truncated file:                                        988,756
prefix match (first 988,500 recovered == the clean-boundary prefix):                     True
original total lines: 1,975,792  ->  987,036 lines lost (everything after the cut point)
```

The recovered count (988,756) exceeds the last-clean-FLUSH_FRAME-boundary count (988,500) because `FLUSH_BLOCK` (called after every single message) provides decodability finer than "one whole frame" -- the reader recovers everything actually written up to the truncation point, not merely up to the last `FLUSH_FRAME`. This is a stronger guarantee than the plan's own stated minimum ("truncates at most the tail of one frame"). No corruption, no fabricated data, no reordering in the recovered prefix.

### Empty-frame-at-shutdown-boundary verification

`_close_writer()`'s final `flush(FLUSH_FRAME)` + `writer.close()`, when zero messages have accumulated since the last flush (the common case at a graceful `close()` right after a periodic `FLUSH_FRAME` already fired), writes a small empty frame (9 bytes observed on the real `conn_B` reframe, the final frame at EOF). Verified synthetically that `read_across_frames=True` transparently skips an empty frame **mid-stream**, not just at EOF (`cctx.compress(b"a\n") + cctx.compress(b"") + cctx.compress(b"b\n")` decodes to exactly `b"a\nb\n"` across 3 frames) -- relevant because after Task 3's restart, every graceful shutdown-then-restart of `RawArchiveWriter` against an existing file will leave one of these mid-stream, not just at end-of-file. Expected and harmless.

### What was NOT re-framed

`date=2026-09-14` (~15 GB) and `date=2026-09-15` (~17 GB), plus today's active `date=2026-09-16` files, were **not** copied or re-framed -- per the plan's own escape valve ("a representative sample if re-running the full assertion against all files is impractically slow -- state which"). The two days processed (1.78 GB + 6.94 GB = 8.72 GB across 4 files) are a representative real-data sample spanning both the smallest and one of the largest days in the current archive, at consistent 5.8-6.0x reduction. Re-framing the remaining ~32 GB is mechanically identical (same tool, same guarantees) and is deferred to whoever performs the actual production swap after Task 3's human-approved restart -- not because of any unresolved technical question.

**Swap-ready deliverable, kept on disk for human inspection before any production swap (1.4 GB total, vs. 8.72 GB before):**

| File | Bytes | sha256 |
|---|---|---|
| `reframe_work/raw/date=2026-09-12/conn_A.ndjson.zst` | 262,682,989 | `934b4714da187c720ff8ed485a6a394070d4d6afb7fea39ff918e7171b92568d` |
| `reframe_work/raw/date=2026-09-12/conn_B.ndjson.zst` | 65,957,125 | `877417e41d65084aed4692ed60af09884b159e42e8c7229a681422f45f112b8e` |
| `reframe_work/raw/date=2026-09-13/conn_A.ndjson.zst` | 603,715,477 | `e03b1072fabefd1b639102d30348a8555a574fc5d0348c99a576895bb4b133c8` |
| `reframe_work/raw/date=2026-09-13/conn_B.ndjson.zst` | 603,105,917 | `0fe23bd967351bc0ac5a8f81de7c078a0702e3cfd2946c5ea3b2e08889522512` |

`reframe_work/orig_backup/` (the independent-verification backup copy) was deleted after use -- it served only to prove the identity check above, not as a deliverable.

## Issues Encountered

Beyond the deviations documented above: the real re-framing run's throughput on this machine is ~10-16 MB/s of compressed input (Python-level per-message `write`+`flush(FLUSH_BLOCK)` loop overhead, not a zstd algorithmic limit) -- the `date=2026-09-13` run (7.3 GB compressed across 2 files) took several minutes and was run via `run_in_background` rather than blocking. Not a blocker, just a throughput note for whoever runs the full-archive swap later: budget roughly 1-1.5 minutes per GB of old-format compressed input.

Empty `/tmp/reframe_date13.log` while the background job was still running is Python's default block-buffering on redirected (non-tty) stdout, not a hang -- output landed all at once when the process exited. Confirmed via `ps`/file-size polling rather than trusting the empty log file.

Peak RSS observed on the 3.6 GB `conn_A` re-frame: ~46-48 MB (`ps aux` `RSS` column) -- confirms the streaming design keeps memory bounded independent of file size, as intended.

## Known Stubs

None.

## Threat Flags

None new. This plan's threat register (T-03-10, T-03-11) is implemented exactly as specified: the re-framer's atomic `.tmp`+`replace` write with a pre-replacement identity assertion (T-03-10), and Task 3's human-gated, graceful-SIGTERM restart with sidecar seq-resume (T-03-11, pending human approval).

## CHECKPOINT EVIDENCE -- Task 3 (AWAITING HUMAN APPROVAL)

**This plan is NOT complete. Tasks 1-2 are done, committed (`220cf10`, `c19f12c`), and verified above. Task 3 -- the live daemon restart that actually ships schema v2 and the fixed `RawArchiveWriter` -- has not happened. The daemon is untouched: PID 57329, uptime 2h18m+ at last check, unchanged since this session started.**

### 1. Evidence already established (Tasks 1-2, not re-explained here)

- `tests/capture` 92 passed, `tests/tools` 6 passed, full `mvp` suite 257 passed, `ruff check`/`ruff format --check` clean, all 10 pre-commit guardrails green on both commits.
- Real-data compression: **5.8-6.0x** reduction vs. current per-message framing (raw->old 1.52x, raw->new 8.92x on the measured file), disk-fill horizon extends from ~2 months to **~325 days** at current free space.
- Independent line-sequence-identical proof: 1,975,792 lines, 0 mismatches.
- Real-data crash-truncation recovery proof: no corruption, recovers everything up to the truncation point.

### 2. What the restart will do

```
1. SIGTERM the current daemon (PID 57329) -- graceful shutdown, NOT SIGKILL.
   Confirm the pidfile is removed and the process exits (matches 01-04's
   ~1s graceful-shutdown precedent).
2. Relaunch from the SAME working tree (now at commit c19f12c, HEAD of
   feature/phase-03-data-layer-backfill-ingest-lockbox), same command line:
     cd /Volumes/ProjectsSSD/aihedgefund/repo/mvp && nohup ./.venv/bin/python3 -m data.capture.daemon \
       --data-root /Volumes/ProjectsSSD/aihedgefund/capture \
       --symbol BTCUSDT --stagger-seconds 45 \
       > /tmp/capture-daemon-runI.log 2>&1 &
   (never `uv run` -- STATE.md's standing operational rule; log file name
   follows the existing runA..runH sequence, next is runI)
3. Confirm via the new run's own log line the format Run H already shows:
     seq resume source: symbol=BTCUSDT stream=trade source=sidecar
     seq resume source: symbol=BTCUSDT stream=bookTicker source=sidecar
     resumed seq: symbol=BTCUSDT stream=trade next=<N>
     resumed seq: symbol=BTCUSDT stream=bookTicker next=<N>
```

**Pre-restart baseline (captured this session, read-only):**
- `seq_state.json`: `{"BTCUSDT": {"bookTicker": {"seq": 112179678, ...}, "trade": {"seq": 10834305, ...}}}` (values still advancing -- daemon is live)
- Gap ledger: 51 rows total (3 `ledger_version=1` legacy, 48 `ledger_version=2`)
- Expected downtime: **~3-4 seconds**, per 01-04-SUMMARY.md's measured Run E->Run F precedent (SIGTERM exit <1s + process launch ~2s, independent of partition-file count thanks to the sidecar).

### 3. IMPORTANT correction to this plan's own text: do not expect a gap-ledger row for the restart

03-06-PLAN.md's Task 3 action item (b) says "confirm the gap ledger recorded a short outage for the restart window." **This will not happen, and that is expected, not a failure.** `rotation.consume()`'s `last_seen_state` and `last_trade` are both fresh empty dicts at every daemon startup -- the first frame received after ANY restart has no `prior_merged`/`prior_conn`/`prior_id` to compare against, so none of the three gap-detection signals (`merged-silent`, `connection-silent`, `trade-id-skip`) can fire on it. 01-04-SUMMARY.md section 6 already observed this live ("the restart itself produces no reactive false gap") -- its inverse (a REAL restart outage is *also* invisible to the ledger) is the same structural fact, just not previously called out. Logged to `deferred-items.md` as a DATA-07 follow-up.

**What to check instead, to confirm the restart's outage window and duration:**
```python
# (a) trade_id discontinuity between the last pre-restart row and the first post-restart row
import polars as pl
df = pl.scan_parquet(
    "/Volumes/ProjectsSSD/aihedgefund/capture/parsed/symbol=BTCUSDT/stream=trade/date=2026-09-16/*.parquet"
).sort("seq").collect()
df.filter(pl.col("trade_id").diff() > 1)  # any post-restart row where the exchange's own id skipped

# (b) rtime discontinuity in today's raw/ files (both conn_A and conn_B)
# decode today's date=... files with tools.reframe_raw_archive.iter_lines (read-only,
# works on either framing) and diff consecutive rtime_ns values around the restart window
```

### 4. Post-restart confirmations required (per the plan's own acceptance criteria)

- (a) `seq` resumed from `seq_state.json` sidecar, not a full partition rescan -- confirm via the `source=sidecar...` log line, not inferred.
- (b) ~~Gap ledger short-outage row~~ -- superseded by section 3 above; use the `trade_id`/`rtime` discontinuity checks instead.
- (c) A fresh post-restart trade row carries `schema_version=2` and a non-null `exec_type` -- `pl.scan_parquet(...).filter(pl.col("schema_version") == 2).head(1)` against the newest `date=2026-09-16` partition written after the restart.
- (d) New raw archive file(s) opened after restart use the fixed framing -- spot-check file size growth rate over a few minutes; should track ~2.5-2.6 GB/day rather than ~15 GB/day (or simply: growth per message should look like the ~5.8-6x-smaller ratio measured above, not the pre-fix rate).

### 5. Sharp edge worth flagging before approval

`X` is now a **required** field on every trade frame (schema_version=2) -- if Binance ever omits it, `parse_trade` raises `FrameParseError` and `rotation.consume()`'s post-dedup handler (see `ingest()`'s `except FrameParseError` branch) drops that single row with a `WARNING` log line, while `seq` still increments for it (pre-existing behavior for any missing required field, unchanged by this plan). Both `SAMPLE_TRADE_FRAME` fixtures (captured live, 01-04) and every trade frame observed in this session's real-data work carry `X`, so this is a theoretical edge, not an observed one.

### 6. Rollback plan if the restart misbehaves

The daemon runs directly from this working tree (no separate deploy step for the local run) -- the running process already has Tasks 1-2's code loaded in memory regardless of any later `git` operation, so rollback only matters for the *next* restart:
```
git revert --no-commit c19f12c 220cf10 && git commit -m "revert(03-06): roll back schema v2 + RawArchiveWriter fix"
```
(newest commit first, per standard `git revert` ordering) then relaunch with the same command as section 2. `seq_state.json`/gap ledger/parsed partitions are unaffected either way -- this plan makes no destructive schema change to already-written Parquet (v1 and v2 rows coexist by design, per `resume_seq_assigner`'s and `curated_build.py`'s existing tolerance).

### 7. What was deliberately NOT done as part of this checkpoint

- The re-framed `date=2026-09-12`/`date=2026-09-13` files under `reframe_work/` were **not** swapped into `capture/raw/` -- per the safety override (Deviation 4), that swap is a human-directed follow-up, not automated here, and does not itself require the daemon to be stopped (`RawArchiveWriter` only ever holds *today's* file open; historical dates are never touched by the running process).
- `date=2026-09-14`/`date=2026-09-15` (~32 GB) were not copied or re-framed -- representative-sample escape valve, see "What was NOT re-framed" above.

### Resume signal expected from human

Reply "approved" to authorize the restart (section 2's exact commands will be run), or describe what needs to change first. Until then, this plan is not marked done, DATA-02/DATA-07 stay incomplete in REQUIREMENTS.md, and the daemon keeps running on its current (pre-03-06) code.

## Self-Check

Verified file existence:
```
FOUND: mvp/data/schema.py
FOUND: mvp/data/capture/parse.py
FOUND: mvp/data/capture/seq.py
FOUND: mvp/data/capture/ws_client.py
FOUND: mvp/data/ingest/normalize.py
FOUND: mvp/tools/reframe_raw_archive.py
FOUND: mvp/tests/capture/test_ws_client_raw_archive.py
FOUND: mvp/tests/tools/test_reframe_raw_archive.py
FOUND: .planning/phases/03-data-layer-backfill-ingest-lockbox/deferred-items.md
```

Verified commits exist in `git log --oneline`:
```
FOUND: 220cf10 feat(03-06): TRADE_SCHEMA v2 (exec_type) + resume_seq_assigner seq-projection fix
FOUND: c19f12c feat(03-06): fix RawArchiveWriter per-message zstd framing + offline re-framer
```

Daemon PID 57329 confirmed alive and unchanged throughout this session (`ps -p 57329`).

## Self-Check: PASSED (pending human approval of the Task 3 checkpoint)

---
*Phase: 03-data-layer-backfill-ingest-lockbox*
*Completed: 2026-09-16 (Tasks 1-2; Task 3 checkpoint pending human approval)*
