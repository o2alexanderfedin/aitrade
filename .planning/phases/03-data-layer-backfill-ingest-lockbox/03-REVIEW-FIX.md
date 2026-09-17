---
phase: 03-data-layer-backfill-ingest-lockbox
fixed_at: 2026-09-17T02:56:00Z
review_path: .planning/phases/03-data-layer-backfill-ingest-lockbox/03-REVIEW.md
iteration: 1
findings_in_scope: 16
fixed: 16
skipped: 0
status: all_fixed
---

# Phase 3: Code Review Fix Report

**Fixed at:** 2026-09-17T02:56:00Z
**Source review:** `.planning/phases/03-data-layer-backfill-ingest-lockbox/03-REVIEW.md`
**Iteration:** 1
**Branch:** `feature/phase-03-data-layer-backfill-ingest-lockbox`, canonical checkout `/Volumes/ProjectsSSD/aihedgefund/repo`. No worktree, no branch change, every commit went through the live pre-commit hooks (no `--no-verify`).

> **Daemon restart required for CR-01 — not performed.** The capture daemon (Run I, PID 75796) is still running the old `RawArchiveWriter`, so the crash→same-day-restart corruption path is still open in production until a human-approved restart. WR-11 (`power.py`, `watchdog.py`) also only takes effect at that restart. The daemon was not stopped, signalled or relaunched. `/Volumes/ProjectsSSD/aihedgefund/capture/` was only read.

> **Six trade days are now paused, so Phase 4 cannot load them without a human decision.** The new probable_loss check (WR-04) pauses the pre-capture trade days 2026-06-07, 07-06, 07-10, 08-19, 08-22 and 08-25. Each one needs either an acknowledgement or a re-measured `max_na_run_ids`. They were deliberately left unacknowledged. **Interpretation:** CONTEXT's "never hard-fail" was read as "never `failed`". probable_loss reports `degraded`, and under the existing pause rule a `degraded` day still pauses. Override if "never hard-fail" was meant to mean "never pause".

**Summary:**
- Findings in scope: 16 (5 Critical, 11 Warning; Info out of scope)
- Fixed: 16 (none disputed, none skipped). WR-03, WR-04, WR-05 and WR-10 change DQ or classification logic. They are marked **requires human verification** below.
- Commits: 17 (`22654a0`..`4fae59a`). WR-08 took three commits (`ce124aa`, `c386946`, `a503bd6`; the first two are shared with CR-03 and CR-05). WR-04 took two: the code, then the acknowledgement follow-through.
- Tests: **441 passed** (baseline 319). Every fix has a regression test that was seen failing on the unfixed code (transcripts below).
- Real-tree guardrails after the last commit, all exit 0:
  - `check_ms_to_ns_site`: exactly one ms→ns site, `data/capture/parse.py:36`. 13 seconds→ns sites in 4 allowlisted files; the original 8 are unchanged.
  - `check_lockbox_containment`: 382 files scanned (47 Python, 335 other text).
  - `check_manifest_id_integrity`: 111 manifests.
  - `check_manifest_append_only` (new): 111 committed manifests.
  - `check_no_manifest_rewrite --full` on the real lake: 111 manifests, sha256 mode. The fast mode also passes.
  - Also passing: `check_latest_ban`, `check_catalogue_completeness`, `check_numba_globals`, `check_spec_diff`, `check_pin_versions`.
- Phase 2 house style: `02-REVIEW-FIX.md` does not exist anywhere in this repo or its history (`git log --all -- '*REVIEW-FIX*'` is empty). This report follows the gsd-code-fixer template plus the sections the orchestrator asked for.

## Fixed Issues

### CR-01: A crash followed by a same-day restart makes the rest of that day's raw archive unreadable

**Status:** fixed (code). **Not yet live: needs a daemon restart.**
**Commit:** `22654a0`
**Files:** `mvp/data/capture/ws_client.py`, `mvp/tests/capture/test_ws_client_raw_archive.py`, `mvp/tests/capture/test_ws_client_liveness.py`

**Choice: option (a), a new segment per open.**
- Every `_open_for_today` now exclusive-creates `conn_<id>.<open_ns>.ndjson.zst` with `open(path, "xb")`, so appending to a file a previous run left mid-frame can no longer happen.
- Why not (b):
  - It would have to walk a 7 GB file on every start. That file holds tens of millions of `FLUSH_BLOCK` blocks, too slow in Python.
  - It would truncate an irreplaceable file on startup. That is destructive, and it discards the partial frame that still decodes.
  - With (a), a crashed segment stays exactly as recoverable as a truncated file on its own, and nothing is ever deleted.
- `archive_segment_paths(day_dir, conn_id)` returns a day's files in write order: the legacy `conn_<id>.ndjson.zst` first, then segments by `open_ns`. The 19-digit ns keeps lexicographic order equal to write order until 2286.

**Filename consumers checked:**
- Only two tests hard-coded `conn_A.ndjson.zst`; both now use the helper.
- `tools/reframe_raw_archive.py` takes a user-supplied glob, so nothing breaks.
- **Ops note:** after the restart, manual checks (`zstd -t`, the reframe tool) must glob `conn_A*.ndjson.zst`. Each segment is its own zstd stream: decode them one after another, never concatenate the bytes.

**Regression test** (`test_crash_truncate_then_same_day_restart_keeps_every_line_readable`):
1. A subprocess writer appends 1,500 lines, then calls `os._exit(0)` without `close()`.
2. The test truncates 7 bytes into the unterminated frame.
3. It records the lines readable at that point.
4. A new writer opens on the same fixed UTC day, writes 200 lines and closes.
5. Every pre-crash line and all 200 post-restart lines must read back, in order.

**Red** (unfixed `ws_client.py`; failed on the post-restart read):
```
>           all_lines.extend(_read_lines_tolerating_truncated_tail(segment))
E           zstandard.backend_c.ZstdError: zstd decompress error: Data corruption detected
FAILED ...::test_crash_truncate_then_same_day_restart_keeps_every_line_readable
1 failed, 5 deselected in 1.93s
```
**Green:** 6 passed in the file; `tests/capture` + `tests/tools` 106 passed.

### CR-02: The manifest-immutability guardrails never compare against git

**Status:** fixed
**Commit:** `473b75b`
**Files:** `mvp/tools/check_manifest_append_only.py` (new), `mvp/tests/tools/test_check_manifest_append_only.py` (new), `mvp/data/store.py`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`

**What changed.** New guardrail `tools/check_manifest_append_only`. It is wired into both callers with byte-identical commands; the only pre-existing difference between the two callers is the fast variant, IN-07. Immutability is now anchored to git history:
1. A shallow clone is a **FAIL**, never a SKIP. `ci.yml` already uses `fetch-depth: 0`.
2. No manifest may ever have been deleted or modified in `HEAD`'s history (`git log --no-renames --diff-filter=DM`; a rename counts as a delete).
3. Every manifest in `HEAD` must be present and byte-identical in the working tree.
4. No two manifests may name one `partitions[].path` with different sha256.
5. `HEAD` must track at least one manifest.

`by-date/` pointers stay mutable. Every git call uses `env=scrubbed_git_env()`, and the tests use `isolate_config=True`. `issue_manifest` also now refuses a partition path that another manifest already names.

**Red.** This is a new tool, so there is no old version to fail. The red is the reviewer's attack passing both existing checks, asserted inside `test_reviewer_reproduction_rewrite_in_place_and_reissue`:
- `check_manifest_id_integrity.check_manifest_file(new) is None` holds (the gap).
- `check_no_manifest_rewrite.main([--full, ...]) == 0` holds (the gap).
- `check_manifest_append_only.main(...) == 1` holds (the fix).

Each rule was also disabled in turn:
```
history rule off:      FAILED test_reviewer_reproduction_rewrite_in_place_and_reissue
                       FAILED test_committed_modification_anywhere_in_history_is_caught   2 failed, 7 passed
working-tree rule off: FAILED test_uncommitted_delete_and_edit_are_caught                1 failed, 8 passed
path/sha rule off:     FAILED test_reissue_keeping_the_old_manifest_is_caught_by_partition_path_rule  1 failed, 8 passed
shallow check off:     FAILED test_shallow_clone_is_a_failure_not_a_skip                1 failed, 8 passed
```
**Green:** 9 passed. Real tree: `PASS: 111 committed manifest(s) append-only against HEAD history`.

### CR-03: The lockbox containment check misses attribute access on the imported module, and never scans non-`.py` files

**Status:** fixed, with accepted gaps listed below
**Commit:** `ce124aa`, which also covers WR-08 for this tool
**Files:** `mvp/tools/check_lockbox_containment.py`, `mvp/tests/lockbox/test_containment_scan.py` (new)

**What changed.** The check now resolves names instead of grepping. Every name bound to `data.lockbox` or to one of its members is tracked, iterated to a fixed point. That covers:
- `import data.lockbox [as x]` and `from data import lockbox [as x]`;
- relative imports, resolved against the file's own package;
- `importlib.import_module`, `__import__` and `sys.modules["data.lockbox"]`;
- re-binding (`y = x`).

Flagged:
- a private attribute access on a bound name;
- **any** attribute assignment or deletion, including public ones (a monkeypatch);
- `setattr`, `delattr` and `vars` on a bound name;
- `getattr` or `hasattr` with a private or non-constant name;
- a private `mock.patch("data.lockbox._x")` target string.

Notebook code cells are parsed and scanned (magic lines are text-scanned; a cell that does not parse is a violation). Every other text file under `mvp/` is line-scanned for a separator-bounded lockbox path. Exemptions are by exact path: `data/lockbox_POLICY.md` and `spec.md`.

**Red** (unfixed tool): 22 failed, 4 passed.
- All 13 resolved-access bypasses.
- Relative import.
- Notebook with a literal path; notebook monkeypatch behind a magic line.
- `.sh`, `.toml` and `.md` files.
- A nested `scripts/tests/` directory.
- A checkout under `tests/`; zero files scanned.

**Green:** 26 passed. Real tree: scanned 382 files, exit 0.

**Accepted gaps.** Static analysis cannot close these without executing code; the list is also in the module docstring:
- names or paths built at runtime from pieces (inside f-strings, reversed literals, values read from data);
- `importlib.import_module(<non-constant>)`;
- `eval`/`exec` of a constructed string;
- reflection that never names the module (`gc.get_referrers`, walking `sys.modules.values()`);
- a subprocess running code the scanner never sees.

The `chmod 0000` barrier, CR-04's runtime refusal and the durable MLflow one-look record are the layers behind these.

### CR-04: The default loader enforces neither the curated tier nor path containment

**Status:** fixed
**Commit:** `f2fa50e`
**Files:** `mvp/data/store.py`, `mvp/data/dq/report.py`, `mvp/data/lockbox.py`, `mvp/tests/store/test_loader_tier_containment.py` (new), `mvp/tests/lockbox/test_containment.py`, `mvp/tests/store/test_manifest_rewrite_guard.py`

**What changed.**
- `resolve_manifest` now requires a keyword-only `expected_tier`. Before any partition byte is read, it raises `ManifestTierError` when:
  - the manifest's tier does not match; or
  - a partition path resolves outside `lake_root/<tier>/` after `..` normalisation and following symlinks; absolute paths are included.
- `load_curated` and the DQ report pass `CURATED_TIER`. `open_lockbox` passes its own `LOCKBOX_TIER`; `store.py` still never spells out the lockbox name.
- Manifests of any tier other than curated never write the by-date index, so a lockbox manifest cannot re-point a curated date.
- `test_default_loader_cannot_reach_lockbox` never called the loader. It is renamed `test_chmod_0000_blocks_listing_the_quarantined_dir`.
- New tests call `load_curated` itself, with a read spy asserting that no partition was opened. `test_load_curated_refuses_a_lockbox_manifest_before_any_read` shows the loader refuses before it ever reaches the chmod barrier. A separate test shows chmod alone still blocks a direct read.

**Red** (unfixed store; the new exception class replaced by `Exception` so the failure is behavioural, not an `AttributeError`):
```
E  Failed: DID NOT RAISE Exception   x6 (lockbox tier; lockbox/ path; curated/../lockbox; curated/../../raw; absolute; symlink)
E  AssertionError: assert '8f1b5c770578...' == 'feef9e0c42f2...'   (lockbox manifest re-pointed the curated by-date index)
7 failed, 1 passed
```
**Green:** 8 passed. All 111 real manifests pass the tier/containment check (read-only path arithmetic).

### CR-05: The ms→ns single-site check matches literals rather than resolved values

**Status:** fixed
**Commit:** `c386946`, which also covers WR-08 for this tool
**Files:** `mvp/tools/check_ms_to_ns_site.py`, `mvp/tests/tools/test_check_ms_to_ns_site.py` (new), `mvp/data/dq/checks.py`

**What changed.** For each module the check builds a table of constant values, iterated to a fixed point across modules. It covers:
- names, attribute and class bindings;
- parameter defaults;
- cross-module `from`/`import` bindings;
- values folded through arithmetic.

It then flags:
- multiplication chains, flattened, so `t * 1000 * 1000` counts;
- augmented assignment;
- `mul`/`__mul__`/`multiply` calls (plus the division family for seconds→ns);
- millisecond unit conversions: `Datetime("ms")`, `time_unit="ms"`, `from_epoch`, `duration(milliseconds=)`, and `datetime64[ms]` dtype strings passed to a call.

`data/dq/checks.py`'s `NS_PER_SECOND` arithmetic now shows up as a seconds→ns site. It is **allowlisted explicitly**, and the docstring advice to hide it behind a name is removed. The original 8 allowlisted sites in 3 files are still found, pinned by `test_real_tree_seconds_to_ns_allowlist_is_preserved`, and `parse.py:36` gives no false positive.

**Red** (unfixed tool): 18 failed, 5 passed. Failures:
- named, derived, function-local and default-argument constants;
- augassign and factor chain;
- polars `.mul`, `__mul__`, `operator.mul`;
- class attribute;
- 5 unit-cast forms;
- a cross-module import;
- a named seconds→ns constant;
- a relative `tests/` exclusion.

The 5 passes are the literal forms, non-conversions and the real tree.

**Green:** 24 passed. Real tree: `PASS: exactly one ms-to-ns site at data/capture/parse.py:36`.

**Accepted gaps (docstring):**
- a value reaching the multiplication only through a function return or a parameter without a default;
- container lookups;
- `getattr`/`eval`;
- two partial scalings in sequence (`t *= 1000` twice).

### WR-01: A DQ acknowledgement is only checked for existence

**Status:** fixed to the orchestrator's specification; one residual, noted below
**Commit:** `cb3ee5c`
**Files:** `mvp/data/store.py`, `mvp/tracking/mlflow_utils.py`, `mvp/tests/dq/test_pause_enforcement.py`, `mvp/tests/tracking/test_mlflow_utils.py`

**What changed.**
- `validate_dq_acknowledgement` requires a JSON object that meets all of these:
  - `date`, `symbol`, `stream`, `reason`, `who` and `when` are non-blank strings;
  - `date`, `symbol` and `stream` match the day being loaded;
  - `when` parses as ISO 8601.
- An invalid acknowledgement leaves the day paused, and the error says why.
- `dq_acknowledgement_ids()` returns the acknowledgement ids a load relied on.
- `start_tracked_run(..., dq_ack_ids=...)` logs them as the `dq_ack_ids` tag inside the same atomic `start_run(tags=...)` call, so there is still only one MLflow entry point.
- All 6 committed acknowledgements validate; a test pins this.

**Red:** 12 failed.
- `DID NOT RAISE DQPauseError` for all 9 invalid forms: zero-byte, not JSON, not an object, missing reason, blank reason, empty `who`, bad `when`, copied from another date, copied from another stream.
- The helper and the `dq_ack_ids` parameter were missing.

**Green:** 33 passed in those two files.

**Residual:**
- The acknowledgement is not bound to the status, check or `manifest_id` it acknowledged. The reviewer suggested this; the orchestrator scoped it out because it would invalidate all 6 committed acknowledgements.
- The MLflow tag is only written if the caller passes `dq_ack_ids`; nothing forces a training script to do so. Phase 4+ training code should call `dq_acknowledgement_ids` and pass the result.

### WR-02: A missing `build_stats.json` silently removes the reconciliation and NA checks

**Status:** fixed
**Commit:** `4ce831a`
**Files:** `mvp/data/ingest/curated_build.py`, `mvp/data/dq/report.py`, `mvp/tests/dq/test_report.py`

**What changed.**
- `build_stats.json` is now written atomically **before** `issue_manifest`, bound to the partition's sha256, and updated with `manifest_id` after issuance.
- The report emits a failed `build_stats` row when a trade manifest has no stats, or has stats bound to a different build (neither the manifest id nor the partition sha256 matches). The failure is closed, matching the missing-report fix.

**Red:**
- The missing-stats case gave `assert None == 'failed'`: only gap_coverage and etime rows were emitted.
- The stale-stats case gave `assert None == 'failed'`.
- Crash inside `issue_manifest` gave `FileNotFoundError ... build_stats.json`. This was confirmed by restoring the pre-fix `curated_build.py`.

**Green:** full suite 426 at that commit.

### WR-03: `build_curated_range` freezes partial or capture-sourced days forever and hides crash orphans as `already_present`

**Status:** fixed — **requires human verification** (rebuild policy)
**Commit:** `75efedf`
**Files:** `mvp/data/ingest/curated_build.py`, `mvp/tests/ingest/test_curated_build_multi_day.py`

**What changed.**
- **Superseding a capture-sourced trade day.** A trade day whose current manifest is capture-sourced (read from the manifest's own inputs) is rebuilt once the archive publishes:
  - a new `part-<ns>` file and a new manifest are written, and the by-date pointer moves;
  - the result is status `superseded` with `superseded_manifest_id`;
  - the old manifest and partition stay untouched and resolvable, via `build_curated_day(supersede=True)`.
- **Orphans.** A part file that no manifest names, or a day with no by-date pointer, gets status `orphan` with `orphan_paths` and is not built over.
- **In-progress days.** `date >= today` (UTC) gets status `not_final`.

**Red** (old code; the new `today=` argument stripped from a temp copy):
```
assert 'already_present' == 'superseded'   ([..., 'chosen_source': 'capture'])
assert 'already_present' == 'orphan'       ([..., 'manifest_id': None, 'chosen_source': None])
assert ['written', 'written'] == ['not_final', 'not_final']
```
**Green:** full suite 429. Real lake, checked read-only:
- trade: 107 part files, all manifested, 0 orphans, all archive-sourced;
- bookTicker: 4/4, 0 orphans. L1 has no archive, so bookTicker days are never superseded.

**Residual:**
- A bookTicker day built for yesterday before the daemon's final partition flush can still be frozen short.
- The reviewer's `--allow-capture-final` publication-horizon flag was not built; superseding covers trades.

### WR-04: DQ checks measure the wrong source on archive days; `probable-loss` is not implemented

**Status:** fixed — **requires human verification** (new check threshold, 6 newly paused days)
**Commits:** `7900d4b` (code), `4fae59a` (acknowledgement follow-through)
**Files:** `mvp/data/ingest/curated_build.py`, `mvp/data/dq/checks.py`, `mvp/data/dq/report.py`, `mvp/spec/dq_thresholds.toml`, `mvp/spec.md` (re-rendered), `mvp/tests/ingest/test_curated_build.py`, `mvp/tests/dq/test_checks.py`, `mvp/tests/dq/test_report.py`, `mvp/data/lake_registry/dq_acknowledgements/*`

**What changed.**
- **(b) Reconciliation.** X="NA" placeholder rows are dropped from both id sets before diffing.
- **(c) NA rate.** `_select_with_na_stats` reports the rate from the capture side whenever capture exists (`na_placeholder_rate_source`).
- **(a) gap_coverage.** It is `n/a` on archive-sourced trade days.
- **probable-loss.** This decision was locked in CONTEXT but never built.
  - Scope: pre-capture archive days only.
  - Rule: a `diff(trade_id) - 1` run longer than `max_na_run_ids` is flagged `degraded`, never `failed`.
  - Days with a capture overlap report `n/a`, because reconciliation is the loss detector there.
- **`recompute_build_stats()`.** It re-derives stats for an already-built day without writing partitions or manifests. It refuses if any manifest input's sha256 changed, or if the recomputed source differs.

**`max_na_run_ids = 5`, measured** read-only over every captured trade day. These are the longest runs of consecutive placeholder ids per day, over 55,905 placeholder rows:

| Day | 09-12 | 09-13 | 09-14 | 09-15 | 09-16 | 09-17 (partial) |
|---|---|---|---|---|---|---|
| Longest run (ids) | 5 | 5 | 3 | 3 | 4 | 2 |

The placeholder counts on 09-12..09-15 (4,270 / 6,862 / 15,558 / 15,171) equal the old `missing_from_archive` values exactly.

**Red:** 6 failed.
- `assert 2 == 0`: placeholder ids counted as missing from the archive.
- `assert 0.0 == 0.25`: the NA rate came from the chosen archive source.
- `check_probable_loss` did not exist, and `DQThresholds` had no `probable_loss`.
- `assert 'failed' == 'n/a'`: a capture outage failed an archive-sourced trade day.
- `assert None == 'degraded'`: a pre-capture day had no loss detector at all.

**Green:** full suite 441.

**Residual:** 2026-09-12 before 06:37Z (the pre-capture part of a partial-overlap day) has no loss detector.

### WR-05: `l1_sparsity` ignores the start and end of the day

**Status:** fixed — **requires human verification**
**Commit:** `c8c3c5c`
**Files:** `mvp/data/dq/checks.py`, `mvp/data/dq/report.py`, `mvp/spec/dq_thresholds.toml`, `mvp/spec.md`, `mvp/tests/dq/test_checks.py`

**What changed.**
- The check now takes `date` and measures `max(leading, interior, trailing)`.
- **Regime boundary, handled explicitly.** On the regime-start date the leading gap is measured from `regime_start_utc = "2026-09-12T06:37:10.882Z"`, now declared in the TOML and rendered into the spec, not from midnight.
- The trailing edge of an in-progress day (`now_ns` before the next midnight) is not measured.
- A single-row partition is now measured by its edges; it used to be `n/a`. This deliberately revises the 03-gaps closure `d4ea874` ("n/a, not a false ok, on <2 rows"). That closure was about a false `ok` from a single interior measurement. With edges measured, one row in a day yields near-24 h leading/trailing gaps and a `failed` result, so it is not a false `ok` and not a regression. Zero rows stays `n/a`. The existing single-row test was rewritten accordingly.

**Red** (old check; the new arguments stripped from a temp copy): 4 tests failed.
- trailing gap after dying at 12:00Z: old result `ok`;
- leading gap after a restart at 04:00Z: `ok`;
- leading gap measured from the regime start: `ok`;
- single row: `n/a`.

Two expectation slips in my own fixtures (40 vs 45 s, 43200 vs 43205 s) were corrected in the test file. The first was caught in the red run before the fix was written; the second in the first green run, where the check correctly reported 43,205 s from the last row at 43,195 s.

**Green:** `tests/dq` 44 passed. The 4 real bookTicker days give identical values (every maximum is interior), so the 3 bookTicker acknowledgements stay accurate.

### WR-06: The "durable" MLflow one-look check misses soft-deleted runs and experiments and accepts any tracking root

**Status:** fixed
**Commit:** `32a82fb`
**Files:** `mvp/data/lockbox.py`, `mvp/data/lockbox_POLICY.md`, `mvp/tests/lockbox/test_token_one_look.py`, `mvp/tests/lockbox/test_containment.py`

**What changed.**
- `search_experiments(view_type=ViewType.ALL)` and `search_runs(..., run_view_type=ViewType.ALL)`.
- A tracking root without an existing `mlflow.db` raises `LockboxTokenError` before any MLflow object is constructed, so no store is created. The real root, `/Volumes/ProjectsSSD/aihedgefund/mlflow/mlflow.db`, exists.
- `open_lockbox` holds an `O_EXCL` `<token>.lock` across check, stamp and read. A stale lock fails closed.
- Test fixtures pre-create the store. POLICY updated.

**Red:** 4 failed, 38 passed.
- `DID NOT RAISE LockboxTokenError` for a soft-deleted run, a soft-deleted experiment, and a tracking root with no store.
- The concurrent-open lock was not honoured.

**Green:** 42 passed.

### WR-07: A typo in `--registry-root` turns the CI fixture leg into a silent pass

**Status:** fixed
**Commit:** `186e71a`
**Files:** `mvp/tools/check_no_manifest_rewrite.py`, `mvp/tools/check_manifest_id_integrity.py`, `mvp/tests/store/test_manifest_rewrite_guard.py`, `mvp/tests/tools/test_check_manifest_id_integrity.py`

**What changed.**
- A missing registry root, or zero manifests found, is a FAIL in both tools.
- The unmounted-lake SKIP test now uses a non-empty registry, so it still tests only the SKIP.
- The command strings in both callers are unchanged.

**Red:** 3 failed: typo'd root, empty root, zero manifests in id-integrity.

**Green:** 58 passed. Real reproduction:
```
$ python -m tools.check_no_manifest_rewrite --full --lake-root tests/fixtures/lake --registry-root tests/fixtures/lake_regsitry
FAIL: registry root tests/fixtures/lake_regsitry does not exist      exit=1
```

### WR-08: A `/tests/` substring anywhere in the absolute path excludes files from the lockbox scan

**Status:** fixed
**Commits:** `ce124aa` (lockbox containment), `c386946` (ms→ns), `a503bd6` (latest-ban, catalogue-completeness, numba-globals)
**Files:** the five tools above, plus `mvp/tests/tools/test_scan_scope_exclusion.py` (new)

**What changed.** Exclusion is decided on the path **relative to the scan root**: only a top-level `tests/` directory and cache/venv directories are skipped. A scan of zero files fails. Three Phase 2 tools had the identical defect and were fixed too.

**Red:** the lockbox and ms→ns reds are in their sections above. The three Phase 2 tools: 6 failed.

**Green:** 6 passed; each real-tree tool scans 49 files.

### WR-09: `reframe_file` replaces irreplaceable raw files without fsync, and leaves multi-GB `.reframe.tmp` files on failure

**Status:** fixed
**Commit:** `b3a5158`
**Files:** `mvp/tools/reframe_raw_archive.py`, `mvp/tests/tools/test_reframe_raw_archive.py`

**What changed.**
- `write_reframed` flushes the tmp file and calls `F_FULLFSYNC` (falling back to `os.fsync`) before returning.
- `reframe_file` wraps the write and the identity check in `try/except BaseException` that unlinks the tmp file.
- It then calls `os.replace` and fsyncs the parent directory.

**Red:**
- A corrupt-source reframe left `conn_A.ndjson.zst.reframe.tmp` behind (`assert not True`).
- The fsync-order spy had nothing to attach to (`no attribute '_fsync_fd'`): the old tool never synced anything.

**Green:** 8 passed. The spy sees the order `fsync_file`, `replace`, `fsync_dir`.

### WR-10: The nearest-quote classifier depends on input row order when quotes tie on etime

**Status:** fixed — **requires human verification**
**Commit:** `3602597`
**Files:** `mvp/data/ingest/trade_side.py`, `mvp/tests/ingest/test_trade_side.py`

**What changed.**
- Quotes are sorted by `(etime, update_id)`, or `(etime, seq)` on curated frames, and reduced to the last update per `etime` before the asof join.
- Tied etimes with no tiebreak column raise instead of picking arbitrarily.

**Red:** all 6 input orders gave `{(-1,), (1,)} == {(1,)}` for both tiebreak columns, and the no-tiebreak case did not raise.

**Green:** 31 passed in `tests/ingest`.

**Not re-measured:** the published 99.854 % cross-check agreement came from the non-deterministic join. It was not recomputed here.

### WR-11: `sleep_risk()` stays silent when the power source cannot be determined

**Status:** fixed (code). **Not yet live: needs a daemon restart.**
**Commit:** `e7dc574`
**Files:** `mvp/data/capture/power.py`, `mvp/data/capture/watchdog.py`, `mvp/tests/capture/test_power.py`

**What changed.** On macOS, `sleep_risk()` now stays silent only in three cases:
- the host is on AC;
- it is on battery with `disablesleep` set;
- the power source is unreadable but `pmset -g batt` answered and lists no internal battery.

Every other case returns an "UNDETERMINABLE" description, which the watchdog records in the ledger. Off macOS the check is documented as not applicable. Code and docstrings now agree.

**Red:** 3 failed (`assert None is not None`): `ps` failing on a battery host, every `pmset` call failing, and an unrecognised "UPS Power" source.

**Green:** 98 passed in `tests/capture`. The real host (on AC) returns `None`.

**Not addressed:** the review's secondary note. The watchdog still runs `pmset` synchronously on the event loop.

## Regenerated derived artifacts

Only derived, re-computable files were written. No partition was modified, rewritten or deleted. No manifest body was edited. The capture tree was only read.

| Artifact | Dates | Why |
|---|---|---|
| `lake/curated_meta/symbol=BTCUSDT/stream=trade/date={2026-09-12,13,14,15}/build_stats.json` | 4 | WR-04 reconciliation and NA-rate fix, via `recompute_build_stats` |
| `lake/dq/date=*/report.parquet`, `resync_windows.parquet`, `report.md` | 107 (2026-06-01..2026-09-15) | New `probable_loss`, `build_stats` and `gap_coverage` n/a rows, the WR-05 check, and the stats above. Command: `.venv/bin/python3 -m data.dq.report --symbol BTCUSDT --range 2026-06-01 2026-09-15`, 30 s. |

**Faithfulness check before regenerating.** Running the unfixed `select_source_for_day` over today's real sources reproduced every committed reconciliation field for 09-12..09-15 exactly, so the capture set is unchanged since the build.

**What changed in the regenerated stats:**
- `missing_from_archive`: 4,270 / 6,862 / 15,558 / 15,171 → 0 on all four days.
- `na_placeholder_rate`: 0.0 → 0.7526 % / 0.4844 % / 0.4410 % / 0.3238 %, now taken from the capture side.
- `missing_from_capture` and overlap rows are unchanged.

Pre-fix copies of the reports and `curated_meta` were kept in the session scratchpad (session-scoped, not durable).

**Worst-status changes after regeneration:**

| Stream | Days | Before | After | Cause |
|---|---|---|---|---|
| trade | 2026-06-07, 07-06, 07-10, 08-19, 08-22, 08-25 | ok | **degraded** | probable_loss: max skip 7/10/15/16/12/6 ids; runs over 5: 4/9/5/74/7/1 |
| trade | 2026-09-12 | degraded | **ok** | reconciliation 0.169 %, missing_from_archive 0 |
| trade | 2026-09-14, 2026-09-15 | failed | **degraded** | gap_coverage n/a; reconciliation still 4.78 % / 6.36 % missing_from_capture |
| bookTicker | all 4 days | unchanged | unchanged | |

## Acknowledgement decisions

- **`BTCUSDT__trade__2026-09-12.json`: removed.** Its reason was "a structural artifact" of counting X="NA" placeholders in reconciliation. It was acknowledging the bug, not the data. With placeholders excluded:
  - reconciliation is ok (missing_from_archive 0, missing_from_capture 0.169 %);
  - the NA rate is 0.75 %, below the 2 % threshold;
  - the day scores `ok` and `load_curated` returns 791,576 rows with no acknowledgement (verified against the real lake).
- **`BTCUSDT__trade__2026-09-14.json` and `...__2026-09-15.json`: reason corrected.** Both still need an acknowledgement for real degradation: reconciliation shows 4.78 % / 6.36 % `missing_from_capture` (176,454 / 317,032 ids) from the battery-sleep outages. Their old reasons also cited "gap_coverage FAILED" on the trade stream, which is no longer true after WR-04. The reasons were rewritten to the true remaining cause and marked as corrected; `who`/`when` record this pass and the original author.
- **bookTicker 09-12 / 09-14 / 09-15: unchanged.** Every value they cite (42.53 s, 2,894.05 s, 6,052.63 s, 3,269.5 s, 9,642.1 s) is unchanged.
- **Newly paused, deliberately not acknowledged:** trade 2026-06-07, 07-06, 07-10, 08-19, 08-22, 08-25 (probable_loss). Each needs a human decision: genuine loss, or evidence that the 5-id placeholder maximum measured over 6 capture days is too tight. After the change, the real pause state across all 111 curated day-manifests is exactly these 6 paused; trade 09-14/09-15 and bookTicker 09-12/14/15 are unpaused by valid acknowledgements.

## Accepted gaps (stated, not claimed closed)

- **CR-03:** runtime-constructed names and paths, dynamic `import_module`, `eval`/`exec`, reflection that never names the module, subprocess code.
- **CR-05:** values that flow only through function returns, parameters without defaults, or containers; partial scalings applied in sequence.
- **CR-01:** until the daemon restarts, production still appends. The first hard crash plus same-day restart under the old code would still corrupt that day's raw archive.
- **WR-01:** acknowledgements are not bound to the status, check or manifest they cover; `dq_ack_ids` tagging relies on the caller passing it.
- **WR-03:** bookTicker "yesterday" built before the final flush; no publication-horizon flag.
- **WR-04:** no loss detector for the pre-capture part of a partial-overlap day (09-12 before 06:37Z); `max_na_run_ids` should be re-measured as capture history grows.
- **WR-10:** the 99.854 % cross-check agreement was not re-measured.
- **CR-03 scan scope:** the gitignored `mvp/.hypothesis/` example database (94 of the 382 files) is text-scanned, so the file count varies between runs. This is harmless: no lockbox path matches, and binary files are sniffed out. Pruning `.hypothesis` is a possible follow-up.
- **WR-06:** a crash while holding the lock leaves a `<token>.lock` in the git-tracked `lockbox_tokens/`. It must be removed by hand, and `git add -A` would pick it up.
- **CR-02 rule 3:** an unstaged edit to a manifest fails the hook even when the commit being made does not touch manifests. This is a conservative false positive, not a fail-open.
- **WR-11:** `pmset` still runs synchronously on the daemon's event loop.

---

_Fixed: 2026-09-17T02:56:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_

---

## Follow-up (2026-09-17): `probable_loss` re-measured and made informational

**Supersedes:** the "Six trade days are now paused" banner at the top of this report, the probable_loss row in *Worst-status changes after regeneration*, the "Newly paused, deliberately not acknowledged" bullet, and the `max_na_run_ids` bullet under *Accepted gaps* (WR-04). Those passages describe the first version and were left unedited.

**User decision.** The six pauses (2026-06-07, 07-06, 07-10, 08-19, 08-22, 08-25) were false positives. Re-measure the threshold over all 107 archive days, add an etime-span discriminator, and make the check informational so it never pauses. This matches CONTEXT's locked wording "flagged `probable-loss` in the DQ report and never hard-fail". On archive data, trade ids alone cannot tell an X="NA" placeholder burst from a genuine loss.

**Commit:** `66acc2e` fix(03): WR-04 probable_loss is informational, flagged on run size AND etime span.

### Measurement

The measurement was read-only over all 107 curated archive trade days, `lake/curated/symbol=BTCUSDT/stream=trade/date=2026-06-01..2026-09-15`, 857,144 skip runs. Method: sort by `trade_id`; run = `diff(trade_id) - 1 > 0`; span = the etime step across the skip. There were 0 non-monotone etime steps.

| run size (ids) | runs | max etime span |
|---|---|---|
| 1 | 837,907 | 10.886 s |
| 2–5 | 19,137 | 10.049 s |
| 6–20 | 100 | **0.021 s** |
| 21–100 | 0 | – |
| > 100 | 0 | – |

- **Run-size ceiling:** 16 ids (2026-08-19).
- **Span ceiling of runs above 5 ids:** 0.021 s, over 100 runs.
- **The "0.00–0.02 s" premise is right for the flagged bursts but does not generalise.** 87,733 skip runs span more than 1 s. All of them are 1–5 ids, reaching 10.886 s: a lone placeholder in a quiet market. Consecutive trades with no skip at all are up to 15.378 s apart. Span alone would create tens of thousands of false positives.
- **Size alone was not chosen either.** Over three months no run exceeded 16 ids. Still, a >100-id placeholder storm inside a few ms cannot be ruled out, and it would look exactly like a size-only loss.

### Discriminator and thresholds (`mvp/spec/dq_thresholds.toml` `[probable_loss]`, spec.md re-rendered)

- A run is flagged only when **`run_ids > flag_run_ids_over = 100` AND `span > flag_span_seconds_over = 1` s**. Both comparisons are strict.
- Margin: 100 ids is 6× the 16-id ceiling. 1 s is about 48× the 0.021 s span ceiling of runs above 5 ids.
- Real runs meeting both conditions: **0 of 857,144**.
- `max_na_run_ids` was removed.

### Behaviour

- `check_probable_loss(trades: DataFrame[trade_id, etime])` always returns `dq_status="ok"`, or `"n/a"` with fewer than 2 trades. It follows `check_crossed_locked_book`, so it never contributes `degraded` or `failed` to the pause decision.
- `count` is the number of flagged runs.
- The detail reports `skip_runs`, `max_run_ids`, `max_run_span_s` and the flagged-run count. When a run is flagged, it also reports the largest flagged run (ids, span, etime before the skip) and `ids_in_flagged_runs`.
- `report.py` now reads `trade_id` + `etime` for pre-capture archive days. Days with a capture overlap still report `n/a`, because reconciliation is the loss detector there.
- The change adds three seconds↔ns uses of `NS_PER_SECOND` in `data/dq/checks.py`, an allowlisted file: one threshold multiplication and two display divisions. `check_ms_to_ns_site` now counts 16 seconds→ns sites, up from 13. The single ms→ns site is still `data/capture/parse.py:36`.

### Tests (448 passed, up from 441)

**`tests/dq/test_checks.py`:**
- Both thresholds are pinned (100, 1).
- NA-shaped bursts (16/15/6 ids in ≤ 21 ms) and quiet-market skips (1 id over 10.886 s, 5 ids over 0.9 s) give 0 flags and `ok`.
- A 5,000-id / 30 s loss gives count 1 and `ok`, with the reason carrying `run_ids=5000 span_s=30.000`.
- **Pinned:** 500 ids in 5 ms is not flagged, and 2 ids over 10 s is not flagged.
- Boundaries: 100 ids / exactly 1 s is not flagged; 101 ids / 1 s + 1 ns is flagged.
- Unsorted input is handled, and fewer than 2 trades gives `n/a`.

**`tests/dq/test_report.py`:** both tests go through the real pause path, `write_report` → `_dq_status_for_date` → `load_curated` with no acknowledgement directory.
- An NA-shaped day has count 0, status `ok`, and loads.
- A 5,000-id / 30 s loss day has count 1, the size and span in the detail, status `ok`, and loads.
- The existing pre-capture test now expects `ok`.

**Red-proof.** `"dq_status": "ok"` was temporarily changed to `"degraded" if flagged.height else "ok"`, then restored from a copy:

```
=== RED: never-pauses reverted ===
+        "dq_status": "degraded" if flagged.height else "ok",  # RED-PROOF
E           data.store.DQPauseError: DQ pause: BTCUSDT.trade has unacknowledged day(s): 2026-09-12: degraded (no acknowledgement file). Add a valid git-committed acknowledgement JSON (date, symbol, stream, reason, who, when; e.g. .../registry/dq_acknowledgements/BTCUSDT__trade__2026-09-12.json) to proceed.
data/store.py:479: DQPauseError
FAILED tests/dq/test_report.py::test_genuine_loss_is_reported_but_never_pauses_the_day
1 failed in 0.75s
=== GREEN: restored ===
0
1 passed in 0.72s
```

### Regenerated DQ reports (derived; `lake/dq/` only)

- Command: `.venv/bin/python3 -m data.dq.report --symbol BTCUSDT --range 2026-06-01 2026-09-15`, 27 s, 107 dates, 551 rows before and after.
- Compared row by row against a pre-change copy, the only changes are in `probable_loss` rows:
  - `dq_status` degraded → ok on the six days;
  - detail text on 103 pre-capture days, which is the new format.
- Every other check's status, value, count and detail is unchanged.
- `probable_loss` after regeneration: 103 `ok`, 4 `n/a` (09-12..09-15, capture overlap), **0 flagged runs in total**.
- New details for the six days (max run ids / max skip span): 06-07 7 / 4.160 s; 07-06 10 / 3.896 s; 07-10 15 / 4.827 s; 08-19 16 / 5.499 s; 08-22 12 / 7.520 s; 08-25 6 / 4.412 s. The large spans come from 1–5 id skips, not from the bursts.
- Worst-status changes: only trade on those six days, degraded → ok.
- Remaining non-ok days: trade 09-14/09-15 (degraded), bookTicker 09-12 (degraded), bookTicker 09-14/09-15 (failed). All are unchanged and acknowledged.
- No Parquet partition, manifest body or `curated_meta` was touched. `check_no_manifest_rewrite --full` (real lake), `check_manifest_id_integrity` and `check_manifest_append_only` all pass on 111 manifests.

### The six days load without acknowledgement (real lake, `load_curated`)

| day | rows | ack file |
|---|---|---|
| 2026-06-07 | 5,625,457 | none |
| 2026-07-06 | 5,073,937 | none |
| 2026-07-10 | 2,973,359 | none |
| 2026-08-19 | 5,388,815 | none |
| 2026-08-22 | 3,803,207 | none |
| 2026-08-25 | 5,743,532 | none |

- **Pause state across all 111 curated day-manifests:** 0 paused.
- **Acknowledgements relied on:** exactly the 5 committed ones (trade 09-14/09-15, bookTicker 09-12/09-14/09-15).
- **`dq_acknowledgements/`:** no files added or removed; `git status` is clean.

**Remaining gap:** the two-axis rule does not flag a large id skip packed into ≤ 1 s. That shape cannot be told apart from a placeholder storm on archive data. The report's `max_run_ids` still shows it. Re-measure both ceilings as capture history grows.
