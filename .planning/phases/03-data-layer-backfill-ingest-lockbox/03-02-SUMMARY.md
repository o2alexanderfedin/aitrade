---
phase: 03-data-layer-backfill-ingest-lockbox
plan: 02
subsystem: data-lake
tags: [trade-side, manifest, immutability, polars, write-once, curated-tier, guardrail]

# Dependency graph
requires:
  - phase: 03-data-layer-backfill-ingest-lockbox
    plan: 01
    provides: "data/lake_paths.py:lake_root()/LAKE_REGISTRY_ROOT, data/backfill/client.py, data/ingest/normalize.py's normalize_archive_trades()/write_raw_partition(), the real raw partition lake/raw/symbol=BTCUSDT/stream=trade/source=archive/date=2026-09-12/ (791,576 rows), data/capture/rotation.py:write_parquet_atomic()"
provides:
  - "data/ingest/trade_side.py -- resolve_side()/nearest_quote_side()/cross_check_agreement(), sign convention pinned against real archive rows"
  - "data/store.py -- issue_manifest()/resolve_manifest()/load_curated(), the manifest-addressed default loader; stored-hash-vs-on-disk-hash verification before any data returns"
  - "tools/check_no_manifest_rewrite.py -- verify_manifest_fast (mtime+size, per-commit) / verify_manifest (sha256, --full, pre-push+CI); both wired into .pre-commit-config.yaml and .github/workflows/ci.yml"
  - "data/ingest/curated_build.py -- materialize_seq()/select_source_for_day()/filter_na_placeholders()/read_capture_partition()/build_curated_day(), the curated-tier orchestrator"
  - "One real curated partition + manifest: BTCUSDT.trade/2026-09-12, manifest_id 1b337fb9efbe5702fa6d16803399a80e8514067e849188459e696af29415036d, 791,576 rows, loadable via store.load_curated()"
  - "RP-1 (immutability) and RP-2 (manifest-hash integrity) observed red-then-green, both against tmp_path fixtures AND against the real committed manifest/partition"
affects: [03-data-layer-backfill-ingest-lockbox/03-03, 03-data-layer-backfill-ingest-lockbox/03-04, 03-data-layer-backfill-ingest-lockbox/03-05, 04-feature-engine]

# Tech tracking
tech-stack:
  added: []  # polars + stdlib only, no new pyproject.toml dependency
  patterns:
    - "join_asof(strategy='backward') for nearest-quote classification -- prevailing quote AT OR BEFORE the trade, never a quote that postdates it (leakage-safe, matches Lee-Ready convention)"
    - "Stored-hash-vs-on-disk-hash manifest verification, never rebuild-vs-compare (03-RESEARCH.md Pattern 3) -- resolve_manifest also re-derives manifest_id from the body before trusting stored partition hashes"
    - "Two-speed guardrail: mtime+size at pre-commit stage, sha256 at pre-push stage, so a per-commit hook never has to hash several GB (which would invite --no-verify)"
    - "Per-day whole-source switch for trades (archive vs capture), never a row-level merge -- trade_id stays unique per curated partition by construction, which is what makes materialize_seq's total order valid"
    - "Schema-tolerant capture-partition read: per-file scan_parquet + concat(how='diagonal_relaxed'), never a bare glob read -- required for a directory straddling Plan 06's schema_version=2 restart"
    - "Write-once curated partitions, same existing-file refusal as Plan 01's raw-tier writer"

key-files:
  created:
    - mvp/data/ingest/trade_side.py
    - mvp/data/store.py
    - mvp/tools/check_no_manifest_rewrite.py
    - mvp/data/ingest/curated_build.py
    - mvp/tests/fixtures/side_convention_rows.py
    - mvp/tests/ingest/test_trade_side.py
    - mvp/tests/ingest/test_seq_determinism.py
    - mvp/tests/ingest/test_curated_build.py
    - mvp/tests/store/__init__.py
    - mvp/tests/store/test_manifest_determinism.py
    - mvp/tests/store/test_manifest_rewrite_guard.py
    - mvp/tests/store/test_loader.py
    - mvp/data/lake_registry/manifests/BTCUSDT.trade/1b337fb9efbe5702fa6d16803399a80e8514067e849188459e696af29415036d.json
    - mvp/data/lake_registry/manifests/BTCUSDT.trade/by-date/BTCUSDT__trade__2026-09-12.json
  modified:
    - mvp/spec.md (Trades-backfill side-exactness: sign-convention bullet)
    - .pre-commit-config.yaml (default_stages, check-no-manifest-rewrite, check-no-manifest-rewrite-full)
    - .github/workflows/ci.yml (check_no_manifest_rewrite --full)

key-decisions:
  - "resolve_side(df, quotes=None) takes quotes as an optional parameter (not shown in the plan's literal signature) -- this plan's real build has no curated L1 quotes yet, so legacy rows (none exist on real data) stay side_method='unknown' rather than the function requiring a quotes frame that doesn't exist yet."
  - "nearest_quote_side/resolve_side both internally sort by etime for the join_asof, then restore the caller's original row order via a temporary row-index column before returning -- required for cross_check_agreement's elementwise comparison against the caller's own tradeSide_raw column to be valid, and caught by a test failure (ShapeError/misalignment) before shipping."
  - "resolve_manifest re-derives manifest_id from the canonicalized body and compares against the requested id, in addition to checking each partition's on-disk hash -- closes a hole where a hand-edited sha256 field inside an otherwise-untouched manifest JSON would pass the partition-hash check alone."
  - "Added default_stages: [pre-commit] to .pre-commit-config.yaml when introducing the first pre-push-staged hook -- without it, every pre-existing hook (ruff, pytest, the other 6 guardrails) would silently start re-running on push too once pre-commit install --hook-type pre-push registers a pre-push git hook."
  - "Added write-once existing-file refusal to build_curated_day's partition write, matching Plan 01's write_raw_partition -- the plan's Task 3 action text says this explicitly ('write-once, same existing-file refusal as Plan 01's write_raw_partition') but I missed it on first pass; caught before committing by re-reading the plan text, not by a test failure. See Deviations."
  - "select_source_for_day's reconciliation stats are scoped to the trade_id overlap RANGE (max of mins, min of maxes) between archive and capture, matching PROBE-RESULTS.md's own methodology -- a naive full-set diff would count every pre-capture archive id as 'missing from capture', which is not a reconciliation finding on 2026-09-12 (capture starts mid-day), just capture not having started yet."
  - "Committed data/ingest/curated_build.py + its tests BEFORE running the real build, then committed the real manifest as a separate commit -- so the manifest's code_hash points at a commit that actually contains the builder (935b3aa0...), not a '-dirty' working-tree hash. The first real-build attempt (before this ordering) produced a '-dirty' code_hash and was discarded (physical curated/ and curated_meta/ directories + the unregistered manifest JSON were deleted before re-running) rather than committed."

patterns-established:
  - "Two-speed CI guardrail split (fast per-commit tripwire, full pre-push/CI guarantee) as the template for any future guardrail whose full check is expensive at real data scale."
  - "Manifest-hash self-consistency check (re-derive manifest_id from body) as a standard resolve_manifest step, not just a partition-hash check."

requirements-completed: [DATA-03, DATA-05, DATA-06]

# Metrics
duration: ~25min (implementation + real build + red-proofs; commit span 23:44:15 -> 23:54:53 PDT for the 4 commits, plus prior orientation reading and post-commit red-proof transcription)
completed: 2026-09-15
---

# Phase 3 Plan 02: Trade-Side Resolution, Manifest Store, Curated Build Summary

**Trade-side sign convention pinned against real archive rows, a manifest-addressed `store.py` loader that verifies stored-hash-vs-on-disk-hash before returning any data, and a real curated `BTCUSDT.trade/2026-09-12` partition (791,576 rows) built end-to-end through the actual `curated_build.py` code path and loaded back via `store.load_curated()`.**

## Performance

- **Duration:** ~25 min (commit span 2026-09-15T23:44:15 -> 23:54:53 PDT for the 4 task/artifact commits, plus prior orientation reading and post-commit red-proof transcription against the real data)
- **Started:** 2026-09-15T23:44:15-07:00
- **Completed:** 2026-09-15T23:54:53-07:00
- **Tasks:** 3 (Task 3 split into a code commit + a real-manifest-artifact commit, per the ordering decision below)
- **Files modified:** 22 (17 new, 3 modified, 2 new manifest JSON artifacts)

## Accomplishments

- `data/ingest/trade_side.py`: `resolve_side()` adds `tradeSide_raw`/`tradeSide_corrected`/`side_method`; the sign convention (`is_buyer_maker=true -> tradeSide=-1`) is pinned by a committed test (`tests/ingest/test_trade_side.py`) against real rows from `BTCUSDT-trades-2026-09-12` (`tests/fixtures/side_convention_rows.py`), not a synthetic fixture. `nearest_quote_side()` never fires on an exact-flag row (tested against a fixture engineered so it WOULD disagree if it did). `cross_check_agreement()` exists and is unit-tested; running it over the real day is Plan 03's job per the plan's own text.
- `data/store.py`: `issue_manifest()`/`resolve_manifest()`/`load_curated()` -- manifest_id-addressed, lake-root-relative partition paths, never a glob, never `latest`. `resolve_manifest` verifies BOTH the manifest's own self-consistency (re-derived `manifest_id` matches) and every partition's on-disk sha256 before returning anything.
- `tools/check_no_manifest_rewrite.py`: `verify_manifest_fast` (mtime+size) at pre-commit stage, `verify_manifest` (sha256, `--full`) at pre-push stage and in CI. Both wired into `.pre-commit-config.yaml` and `.github/workflows/ci.yml`.
- `data/ingest/curated_build.py`: `materialize_seq()`, `select_source_for_day()` (per-day whole-source switch with overlap-scoped reconciliation stats), `filter_na_placeholders()` (schema-version-conditional, tolerant of an absent `exec_type` column), `read_capture_partition()` (schema-tolerant `diagonal_relaxed` concat), `build_curated_day()` (full orchestration, write-once).
- The real 2026-09-12 curated build ran through the actual code path (not a shortcut): 791,576 rows, `chosen_source="archive"`, reconciliation numbers matching PROBE-RESULTS.md exactly, `na_placeholder_dropped=0`. Loaded back via `store.load_curated()` and confirmed `tradeSide_corrected`/`side_method` populated correctly.
- RP-1 and RP-2 observed red-then-green both as `tmp_path` pytest fixtures AND live against the real committed manifest/partition (transcripts below).

## Task Commits

1. **Task 1: Trade-side resolution** - `7a4b34a` (feat)
2. **Task 2: Manifest schema, store.py loader, immutability guardrail** - `762aaa3` (feat)
3. **Task 3a: Curated build code (curated_build.py + tests)** - `935b3aa` (feat)
4. **Task 3b: Real BTCUSDT.trade/2026-09-12 manifest artifact** - `97aeeb7` (docs)

**Plan metadata:** this commit (docs: complete plan)

## Files Created/Modified

- `mvp/data/ingest/trade_side.py` - `resolve_side()`, `nearest_quote_side()`, `cross_check_agreement()`
- `mvp/tests/fixtures/side_convention_rows.py` - 10 real rows (first/last 5) from `BTCUSDT-trades-2026-09-12.csv`
- `mvp/tests/ingest/test_trade_side.py` - 6 tests
- `mvp/data/store.py` - `issue_manifest()`, `resolve_manifest()`, `load_curated()`, `ManifestHashMismatch`
- `mvp/tools/check_no_manifest_rewrite.py` - `verify_manifest_fast()`, `verify_manifest()`, `main(--full)`
- `mvp/tests/store/{__init__.py,test_manifest_determinism.py,test_manifest_rewrite_guard.py,test_loader.py}` - 10 tests total (incl. RP-1/RP-2 as tmp_path fixtures, and main()'s SKIP/fail-on-finding behavior via monkeypatch)
- `.pre-commit-config.yaml` - `default_stages: [pre-commit]`, two new hooks
- `.github/workflows/ci.yml` - one new step (`--full`)
- `mvp/data/ingest/curated_build.py` - `materialize_seq()`, `select_source_for_day()`, `filter_na_placeholders()`, `read_capture_partition()`, `build_curated_day()`
- `mvp/tests/ingest/test_seq_determinism.py` - hypothesis property test + 3 unit tests
- `mvp/tests/ingest/test_curated_build.py` - 10 tests (incl. write-once refusal)
- `mvp/data/lake_registry/manifests/BTCUSDT.trade/1b337fb9....json` + `by-date/BTCUSDT__trade__2026-09-12.json` - the real committed manifest

## Decisions Made

See `key-decisions` in frontmatter. Most consequential for later plans:
1. The overlap-scoped reconciliation methodology in `select_source_for_day` (matches PROBE-RESULTS.md exactly) -- Plan 04's DQ check (2) should read these precomputed `build_stats.json` fields rather than re-deriving reconciliation from raw data.
2. `resolve_manifest`'s manifest-self-consistency check (re-derive `manifest_id` from body) -- a real hardening beyond the plan's literal text, closing a hole the plan didn't explicitly call out.
3. Committing the builder code before running the real build, so `code_hash` in the real manifest is a clean commit SHA, not `-dirty`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Curated partition writer needed the same write-once refusal as the raw tier**
- **Found during:** Task 3, re-reading the plan's own action text before running the real build
- **Issue:** My first draft of `build_curated_day` wrote the curated partition via `write_parquet_atomic` with no existing-file check -- a second call for an already-built day would silently write a second, different-timestamped part file and issue a second manifest, never erroring. The plan's Task 3 action text explicitly says "write-once, same existing-file refusal as Plan 01's `write_raw_partition`", which I had missed on first implementation.
- **Fix:** Added the same `sorted(curated_date_dir.glob("part-*.parquet"))` check + `FileExistsError` raise used by Plan 01's `write_raw_partition`, before the write.
- **Files modified:** `mvp/data/ingest/curated_build.py`
- **Verification:** New unit test `test_build_curated_day_refuses_second_write_same_day`; also observed live against the real 2026-09-12 partition (see transcript below).
- **Committed in:** `935b3aa` (Task 3a commit)

**2. [Rule 1 - Bug] `nearest_quote_side`/`resolve_side` lost row order across the internal `join_asof` sort**
- **Found during:** Task 1, first test run (`test_legacy_row_classified_by_nearest_quote_when_present` and the `_resolve_side_order` reconciliation path)
- **Issue:** `nearest_quote_side` sorted its input by `etime` for `join_asof` (a real requirement of that API) but returned the sorted-order frame, not the caller's original order. `resolve_side`'s legacy-row reattachment then misaligned, and `cross_check_agreement`'s elementwise comparison against the caller's own `tradeSide_raw` column would silently compare misaligned rows on any non-trivially-ordered input. Caught by a `ShapeError` on first test run (column-order mismatch in the `pl.concat`), traced to the deeper row-order bug during investigation.
- **Fix:** Added an internal `_nqs_order` row-index column in `nearest_quote_side`, restored via `.sort("_nqs_order").drop(...)` before returning, so output row N always corresponds to input row N regardless of the internal sort.
- **Files modified:** `mvp/data/ingest/trade_side.py`
- **Verification:** All 6 `tests/ingest/test_trade_side.py` tests pass, including `test_cross_check_agreement_full_agreement_fixture` which depends on row-order correctness to compare correctly.
- **Committed in:** `7a4b34a` (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (1 Rule 2 missing-critical, 1 Rule 1 bug). Both caught and fixed before any commit landed with the defect; neither required a plan revision or user decision.
**Impact on plan:** No scope creep. Both fixes are required for the plan's own stated guarantees (write-once immutability, correct row-order dependent classification) to actually hold.

## Issues Encountered

None blocking. The one operational hiccup (real build's first run producing a `-dirty` code_hash because the builder code wasn't committed yet) was resolved by committing the code first and re-running -- documented as a `key-decision`, not a bug, since nothing was wrong with the code itself.

## Measured Numbers (real 2026-09-12 curated build)

Run via `mvp/.venv/bin/python3` (never `uv run` for anything beyond one-shot pytest/tooling, per environment rules), calling `data.ingest.curated_build.build_curated_day` directly against the real raw partition (Plan 01's output) and the real capture `parsed/` root:

| Field | Value |
|---|---|
| `manifest_id` | `1b337fb9efbe5702fa6d16803399a80e8514067e849188459e696af29415036d` |
| `code_hash` | `935b3aa0facb1ae23bd540ef78438805757a9658` (clean -- built after committing the builder) |
| `chosen_source` | `archive` (published: `raw/.../source=archive/date=2026-09-12/` has a part file) |
| `row_count` | **791,576** (matches PROBE-RESULTS.md and Plan 01's own raw-partition count exactly) |
| `na_placeholder_dropped` | 0 |
| `na_placeholder_rate` | 0.0 |
| `reconciliation_missing_from_capture` | **955** (archive ids absent from capture -- matches PROBE-RESULTS.md section 2 exactly) |
| `reconciliation_missing_from_archive` | **4,270** (capture ids absent from archive, all `X="NA"` rows -- matches PROBE-RESULTS.md exactly) |
| `reconciliation_overlap_rows` | 563,092 (sanity: 564,047 − 955 = 567,362 − 4,270 = 563,092 ✓) |
| `reconciliation_overlap_id_min` / `_max` | 8072574559 / 8073142879 (matches PROBE-RESULTS.md's overlap `trade_id` range exactly) |
| `etime_range` | `[1789171200002000000, 1789257599477000000]` (exactly one UTC day, matches Plan 01's raw-partition min/max) |
| Curated partition | `lake/curated/symbol=BTCUSDT/stream=trade/date=2026-09-12/part-1789541613202010000.parquet`, 6,193,423 B |
| Curated partition sha256 | `ac533224af805fff7a58aad3834afff8830094258b279710eb4fad4a19525d5d` |
| `store.load_curated(manifest_id, "BTCUSDT.trade")` | Returns 791,576 rows; `tradeSide_raw`/`tradeSide_corrected`/`side_method` populated; `side_method` distinct values = `{"exact_flag"}` (as expected -- 0/564,047 mismatches per PROBE-RESULTS.md, no legacy rows on real data) |

## RP-1 Transcript (immutability -- observed against the real committed manifest/partition)

```
$ uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite
checked 1 manifest(s), mode=fast (mtime+size)
$ uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite --full
checked 1 manifest(s), mode=full (sha256)

# RED: append one byte to the committed curated partition
$ printf '\x00' >> ".../lake/curated/symbol=BTCUSDT/stream=trade/date=2026-09-12/part-1789541613202010000.parquet"

$ uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite
checked 1 manifest(s), mode=fast (mtime+size)
FAIL: partition(s) diverged from their manifest:
  data/lake_registry/manifests/BTCUSDT.trade/1b337fb9....json -> curated/symbol=BTCUSDT/stream=trade/date=2026-09-12/part-1789541613202010000.parquet
exit=1

$ uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite --full
checked 1 manifest(s), mode=full (sha256)
FAIL: partition(s) diverged from their manifest:
  data/lake_registry/manifests/BTCUSDT.trade/1b337fb9....json -> curated/symbol=BTCUSDT/stream=trade/date=2026-09-12/part-1789541613202010000.parquet
exit=1

# Restore: truncate back to the original size AND os.utime back to the original mtime_ns
$ uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite
checked 1 manifest(s), mode=fast (mtime+size)
exit=0
$ uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite --full
checked 1 manifest(s), mode=full (sha256)
exit=0

# sha256 of the restored file matches the manifest's stored value exactly:
$ shasum -a 256 ".../part-1789541613202010000.parquet"
ac533224af805fff7a58aad3834afff8830094258b279710eb4fad4a19525d5d  .../part-1789541613202010000.parquet
```

Also observed live: `pre-commit run check-no-manifest-rewrite --all-files` and `pre-commit run check-no-manifest-rewrite-full --all-files --hook-stage pre-push` both `Passed` after restoration, and both ran the real check against this machine's mounted lake (NOT `SKIP` -- the lake IS mounted here; `SKIP` is exercised in CI, where it never is, and separately in `test_main_skips_honestly_when_lake_root_unmounted` via `monkeypatch`).

## RP-2 Transcript (manifest-hash integrity -- live scratchpad script, two synthetic fixture manifests)

```
Before swap: resolve_manifest(a) succeeds, resolve_manifest(b) succeeds
  OK both resolved cleanly

Swapped on-disk bytes at part-a.parquet <-> part-b.parquet (manifests unchanged)

Attempting resolve_manifest(manifest_a) after swap:
  RAISED ManifestHashMismatch: manifest hash mismatch for .../lake/curated/part-a.parquet: expected aea0c6580286e23c2b3d6d2cad4dd26402eef176f6958b2ecccb9c9129f4218d, got 0e24c6e2679b842a8f9a0a79802f49b310222b163bed4c4a306b70a2efaf34fe

Attempting load_curated(manifest_a) after swap (the actual reading path):
  RAISED ManifestHashMismatch: manifest hash mismatch for .../lake/curated/part-a.parquet: expected aea0c6580286e23c2b3d6d2cad4dd26402eef176f6958b2ecccb9c9129f4218d, got 0e24c6e2679b842a8f9a0a79802f49b310222b163bed4c4a306b70a2efaf34fe

Attempting resolve_manifest(manifest_b) after swap:
  RAISED ManifestHashMismatch: manifest hash mismatch for .../lake/curated/part-b.parquet: expected 0e24c6e2679b842a8f9a0a79802f49b310222b163bed4c4a306b70a2efaf34fe, got aea0c6580286e23c2b3d6d2cad4dd26402eef176f6958b2ecccb9c9129f4218d
```

This script ran against a `tempfile.mkdtemp()` fixture, never touching the real lake -- the real 2026-09-12 partition was untouched by RP-2.

## Bonus: write-once red-proof (curated tier, live against the real day)

```
$ ./.venv/bin/python3 -c "build_curated_day('BTCUSDT', 'trade', '2026-09-12', ...)"
RAISED FileExistsError: curated partition .../lake/curated/symbol=BTCUSDT/stream=trade/date=2026-09-12 already
has a written part file: .../part-1789541613202010000.parquet
```

## Verification Transcript

```
$ uv run --locked --directory mvp pytest tests/ingest/test_trade_side.py -x -q
6 passed in 0.13s

$ uv run --locked --directory mvp pytest tests/store/test_manifest_determinism.py tests/store/test_manifest_rewrite_guard.py tests/store/test_loader.py -x -q
10 passed in 0.20-0.26s

$ uv run --locked --directory mvp pytest tests/ingest/test_seq_determinism.py tests/ingest/test_curated_build.py -x -q
12 passed in 0.23-0.76s

$ uv run --locked --directory mvp pytest tests -x -q   # full suite (after each task)
229 passed (after Task 2), 241 passed (after Task 3)

$ uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite
checked 1 manifest(s), mode=fast (mtime+size)
$ uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite --full
checked 1 manifest(s), mode=full (sha256)
```

Every pre-commit hook (ruff check, ruff format --check, uv lock --check, 8 guardrails incl. the two new ones, pytest) passed on all four commits -- transcripts shown inline at each `git commit` invocation (captured live, not simulated).

## Surprises

- The plan's Task 3 `<done>` text mentions write-once for the curated tier in passing ("write the curated partition via write_parquet_atomic ... (write-once, same existing-file refusal as Plan 01's write_raw_partition)") inside the longer `build_curated_day` action paragraph -- easy to miss on a first pass since it reads as parenthetical rather than a separate requirement. Caught and fixed before the real build ran a second time, but worth flagging for future plan-writing: write-once requirements buried inside a dense action paragraph are easy to skip.
- RP-1's fast check (`verify_manifest_fast`) caught the byte-append mutation too, not just the full sha256 check -- `printf '\x00' >>` changes both `st_size` and `st_mtime_ns` simultaneously, so both speeds fire red together on this specific mutation. A mutation that preserved size+mtime (extremely contrived, e.g. a direct `mmap` overwrite of existing bytes with `os.utime` reset) would be the only way to demonstrate the fast check's actual blind spot; not attempted here since it isn't a red-proof this plan asks for.
- `select_source_for_day`'s overlap-scoped reconciliation numbers matched PROBE-RESULTS.md's 955/4,270 exactly on the first real run, with no tuning -- the probe's own methodology (restrict to the id range where both sources have data) turned out to be exactly "max of mins, min of maxes" on `trade_id`, which is what the implementation does.

## Next Plan Readiness

- Plan 03 (full-range backfill + real day's `cross_check_agreement` measurement) can call `data.ingest.curated_build.build_curated_day` directly for each day in the 2026-06-01→yesterday window; `select_source_for_day`'s per-day whole-source switch and `materialize_seq`'s duplicate-id assertion will surface any day where the precedence rule's assumptions don't hold.
- Plan 04 (DQ report) should read `build_stats.json`'s precomputed `reconciliation_missing_from_capture`/`reconciliation_missing_from_archive`/`na_placeholder_dropped`/`na_placeholder_rate` fields directly rather than re-deriving reconciliation from raw data -- this is exactly the "precomputed input" the plan's action text calls for, and it already reproduces PROBE-RESULTS.md's numbers.
- `store.py`'s `load_curated` is the pattern Plan 05 (lockbox) extends: `registry_root`/`lake_root` are both required keyword-only parameters (no defaults), so a lockbox variant cannot accidentally fall back to the curated-tier registry root.
- `data/lake_registry/manifests/BTCUSDT.trade/` now has its first real manifest + by-date index, both git-committed -- Plan 03's additional days will add sibling manifest files under the same `BTCUSDT.trade` dataset directory.
- `check_no_manifest_rewrite`'s `main()` now has real, non-zero manifest coverage on this machine (1 manifest) -- future commits on this machine will exercise the real guardrail path (not `SKIP`) going forward.

## Self-Check: PASSED

All 14 created files verified present on disk; all 4 commits (`7a4b34a`, `762aaa3`, `935b3aa`, `97aeeb7`) verified present in `git log`; the real curated partition
(`lake/curated/symbol=BTCUSDT/stream=trade/date=2026-09-12/part-1789541613202010000.parquet`) and manifest
(`mvp/data/lake_registry/manifests/BTCUSDT.trade/1b337fb9efbe5702fa6d16803399a80e8514067e849188459e696af29415036d.json`)
verified present on disk and in git respectively. No missing items.
