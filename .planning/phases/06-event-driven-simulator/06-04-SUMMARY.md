---
phase: 06-event-driven-simulator
plan: 04
subsystem: data-lake
tags: [parquet, data-lake, schema-migration, regression-proof, real-data, dq-report]

# Dependency graph
requires:
  - phase: 06-event-driven-simulator
    provides: "06-01's FEATURE_SCHEMA_VERSION=2 migration code (bid_price/ask_price bookkeeping columns, version-scoped write-once part-file/build_stats naming, the multi-manifest DQ-report fix) -- this plan is the first real exercise of that code against the real lake"
provides:
  - "All 7 real BTCUSDT feature days (2026-09-12..18, 60,926,503 decision rows) rebuilt at FEATURE_SCHEMA_VERSION=2 with bid_price/ask_price populated, additive-only (7 new manifests, 7 by-date pointers repointed, zero v1 manifest bodies or partition bytes touched)"
  - ".planning/phases/06-event-driven-simulator/evidence/06-04-v1-snapshot.json -- the pre-rebuild v1 manifest-id + build_stats anchor, captured before any by-date pointer moved"
  - ".planning/phases/06-event-driven-simulator/evidence/06-04-v1-v2-label-diff.json -- the measured, committed 249-cell v1-vs-v2 label diff (D-06-19's regression proof)"
  - "mvp/tests/features/test_schema_v2_regression.py -- a permanent, hermetic test tying that evidence to Phase 5's independently-computed errata manifest (22190ad9...), set-equal, exactly 249 cells"
  - "Proof that load_features(v1_manifest_id) still succeeds through the FULL gate chain (not merely resolve_manifest) for all 7 dates after the rebuild, and that segment manifest 97964cb2...'s 3 upstream v1 feature manifest ids still resolve"
affects: [06-05-determinism, 06-06-oracle-suite, 06-07-phase-close, 07-stage-1-regression-vertical-slice]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "A diagnostic build_stats counter that is DERIVED from a null-reason set (asof_convention_disagreement_rows, label_std, both computed from `absent`/`comparable`/`finite` masks in features/labels.py) cascades automatically when the null-reason set gains a member -- verifying such a counter against a strict allowlist requires reading the source's own derivation, not just diffing JSON keys; a bare 'this key changed, STOP' would have false-positived on a correct rebuild"
    - "A per-date build_stats comparison must know which v1 days were built BEFORE a code fix and which were built AFTER it, from `git diff <build-time-code-hash> <current-hash> -- <the changed module>`, not from the errata manifest's date list alone -- 2026-09-16/17/18 were built at 812a264 (post-fix) and already carried non-zero null_stale counts in their OWN v1 build_stats, so the correct comparator is 'v2's null_stale == v1's null_stale + errata_delta', not 'v2's null_stale == errata_delta'"

key-files:
  created:
    - .planning/phases/06-event-driven-simulator/evidence/06-04-v1-snapshot.json
    - .planning/phases/06-event-driven-simulator/evidence/06-04-v1-v2-label-diff.json
    - .planning/phases/06-event-driven-simulator/evidence/06-04-task1-build-results.json
    - .planning/phases/06-event-driven-simulator/evidence/06-04-task2-structural-report.json
    - mvp/tests/features/test_schema_v2_regression.py
  modified:
    - mvp/data/lake_registry/manifests/BTCUSDT.features/by-date/BTCUSDT__features__2026-09-12.json
    - mvp/data/lake_registry/manifests/BTCUSDT.features/by-date/BTCUSDT__features__2026-09-13.json
    - mvp/data/lake_registry/manifests/BTCUSDT.features/by-date/BTCUSDT__features__2026-09-14.json
    - mvp/data/lake_registry/manifests/BTCUSDT.features/by-date/BTCUSDT__features__2026-09-15.json
    - mvp/data/lake_registry/manifests/BTCUSDT.features/by-date/BTCUSDT__features__2026-09-16.json
    - mvp/data/lake_registry/manifests/BTCUSDT.features/by-date/BTCUSDT__features__2026-09-17.json
    - mvp/data/lake_registry/manifests/BTCUSDT.features/by-date/BTCUSDT__features__2026-09-18.json

key-decisions:
  - "The plan's own step-1 instruction to read v1's build_stats.json with no explicit schema_version would have read the WRONG file: features.tier.FEATURE_SCHEMA_VERSION is already 2 on disk (06-01 landed it), so an implicit read resolves build_stats-v2.json, which does not exist before this plan's own builds run. Fixed by passing schema_version=1 explicitly at snapshot time -- caught in advisor review before the first build call, not after."
  - "A self-imposed build_stats diff allowlist (added as an extra safety net beyond the plan's literal acceptance criteria, which are about label CELLS via Task 2, not every build_stats diagnostic) was initially too narrow and stopped the script twice on FALSE positives: once for asof_convention_disagreement_rows/label_std (a verified cascade of the SAME null_stale fix through features/labels.py's `absent`/`comparable`/`finite` masks -- confirmed by `git diff 12a15cff 6b49378 -- labels.py` showing the null_stale term as the ONLY functional change), and once for label_null_counts's null_stale key on 2026-09-16/17/18 (built at 812a264, already post-fix, so v1's OWN build_stats already carried non-zero null_stale counts -- the comparator needed 'v2 == v1 + errata_delta', not 'v2 == errata_delta'). Both were root-caused via source reading before the corresponding day's build_stats was accepted, not explained away without evidence. Neither run left any WRONG data in the lake -- the offending days' v2 partitions were already correctly built; only my own verification script's assertion was wrong."
  - "09-12 was built in an aborted first script run (before the allowlist bug above was found) and left with its report/dual-load steps incomplete; rather than delete and rebuild it (which write-once semantics would refuse anyway -- a second build of an already-v2 date raises FileExistsError), three resume scripts completed the remaining per-date steps in place, reusing the already-written v2 manifest ids and NEVER re-deriving code_hash from `git status` (the tree was dirty by then) or re-snapshotting v1 (09-12's by-date pointer already named v2 by that point)."
  - "No DQ acknowledgement was needed on any of the 7 days: check_feature_label_coverage's 2% degrade threshold is never approached (the largest single-day label-null increase is 153 cells against 4.19M rows, 0.0036%), and every other FEATURE_CHECKS entry is either always-ok or informational. Per this plan's absolute rules, an acknowledgement would have been a STOP-and-report situation, not something to write -- moot here because none was triggered."

patterns-established:
  - "A rebuild's own extra verification script (build_stats diffing beyond what the plan literally requires) should be validated against the SAME rigor as the acceptance criteria it's meant to protect: read the actual source diff between build-time and current code before writing an allowlist, not just the docstring's claim about what changed."

requirements-completed: []

# Metrics
duration: ~30min (git commit timestamps: 23:04:22 PDT, prior plan's close, to 23:34:23 PDT, this plan's Task 2 commit -- not separately isolated from the required-reading time before the first tool call, matching prior plans' honest process-gap note)
completed: 2026-09-24
---

# Phase 6 Plan 4: Real schema-v2 feature rebuild and the 249-cell regression proof Summary

**All 7 real BTCUSDT feature days (2026-09-12..18) now exist a second time at `FEATURE_SCHEMA_VERSION=2` with `bid_price`/`ask_price` populated, written additively beside the untouched v1 partitions, and the measured v1-vs-v2 label diff is exactly the 249 cells Phase 5's independently-computed errata manifest already named -- zero more, zero fewer, confined to 2026-09-12/13 as required.**

## Performance

- **Duration:** ~30 min (see `duration` above for the honest caveat)
- **Tasks:** 2 (both executed, both green, both committed individually)
- **Files modified:** 5 created (2 plan-mandated evidence files + 1 hermetic test + 2 supplementary evidence files), 7 modified (the 7 by-date pointers)
- **Real lake bytes written:** 7 new `part-v2-<ns>.parquet` partitions + 7 new features manifests + 7 regenerated `report.parquet`/`report.md` + 7 `build_stats-v2.json` files (all under `/Volumes/ProjectsSSD/aihedgefund/lake/`, not git-tracked; only the registry JSON and evidence files are committed)

## Accomplishments

- **All 7 days built at schema v2.** `build_features_day` called directly (never `build_features_range`, per the plan's own warning about `_already_built` skipping every one of these dates) with one clean `code_hash` (`6b4937838b71d76382e379b9c7b2d676ef5d3a7f`, HEAD at the time, confirmed non-`-dirty` on every one of the 7 issued manifests).
- **Dual-resolve proven for all 7 dates, after the rebuild.** `load_features(v1_manifest_id, ...)` -- the full gate chain (integrity hash, holdout refusal, DQ pause, verified read), not merely `resolve_manifest` -- succeeds for every v1 id, and `load_features(v2_manifest_id, ...)` succeeds for every v2 id.
- **The real, committed segment manifest re-verified.** `harness.segments.read_segment_manifest(registry_root, "97964cb2...")`'s 3 `upstream_feature_manifest_ids` (the v1 ids for 09-12/13/14) all still resolve through `load_features` after the rebuild.
- **Zero v1 bytes touched.** `check_no_manifest_rewrite --full` (138 manifests, sha256 mode) and `check_manifest_append_only` (140 registry entries) both green after both commits; `git diff` on every pre-existing manifest body is empty -- only the 7 mutable by-date pointer JSONs (documented as NOT the immutability boundary, per `data/store.py`'s own docstring) were modified, repointing to the new v2 ids.
- **The 249-cell regression proof, measured twice from two different angles.** Task 1's per-day `build_stats` diff (against the v1 snapshot) and Task 2's per-day, per-label-column cell diff (against the live partition data via `load_features`) both independently confirm: 09-12 (69 `ret_10s_mid` + 153 `ret_1s_mid`), 09-13 (27 `ret_1s_mid`), and 09-14 through 09-18 bit-identical across all 4 label columns, all 4 catalogued features, both warmup flags, `etime`, and `decision_seq`.
- **The hermetic cross-check test passes and was mutation-proven.** `tests/features/test_schema_v2_regression.py` asserts the committed diff evidence's cell set equals Phase 5's independently-computed errata manifest's cell set (`22190ad9...`) exactly; removing one cell from the evidence file (hash `b58adf14...` -> `f14d774f...`) failed the test with the predicted 248-vs-249 mismatch naming the exact removed cell, then the file was regenerated by re-running the same read-only script and its hash confirmed back to `b58adf14...` exactly before the final green run.

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | Snapshot v1, rebuild all 7 days at schema v2, prove dual-resolve | `1dc0f34` | `06-04-v1-snapshot.json`, `06-04-task1-build-results.json`, 7 new manifest JSONs, 7 by-date pointer updates |
| 2 | v1-vs-v2 label diff, cross-checked against the errata manifest | `14f71fe` | `06-04-v1-v2-label-diff.json`, `06-04-task2-structural-report.json`, `tests/features/test_schema_v2_regression.py` |

**Plan metadata:** pending (this commit)

_Note: Task 2 is `tdd="true"`. RED was genuinely observed (`FileNotFoundError` against the not-yet-existing evidence file, transcribed below) before implementation, but per the same hook-forced deviation `06-01`/`06-03-SUMMARY.md` both record, it could not be its own commit -- this repo's pre-commit hook runs the full suite with no `--no-verify` escape hatch, and a RED commit is by definition a failing-suite commit._

## Per-Day Table

| Date | v1 manifest id | v2 manifest id | rows | `ret_10s_mid` diff | `ret_1s_mid` diff | `ret_1min_mid` diff | `ret_10min_mid` diff | build elapsed |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 2026-09-12 | `1bf9af2e879d...` | `91154024ba40...` | 4,193,137 | 69 | 153 | 0 | 0 | 10.9s |
| 2026-09-13 | `1f10da67ca50...` | `5e4ce973b196...` | 6,864,853 | 0 | 27 | 0 | 0 | 20.3s |
| 2026-09-14 | `fdbf58ca1def...` | `1863b8d255d1...` | 11,323,694 | 0 | 0 | 0 | 0 | 35.3s |
| 2026-09-15 | `781750a9df69...` | `2da1b5b97f97...` | 12,203,294 | 0 | 0 | 0 | 0 | 35.8s |
| 2026-09-16 | `4613e9e8771d...` | `0bfa8d286d90...` | 9,872,620 | 0 | 0 | 0 | 0 | 27.0s |
| 2026-09-17 | `4d0cd973ed75...` | `dcf3b39dad19...` | 8,482,081 | 0 | 0 | 0 | 0 | 23.6s |
| 2026-09-18 | `d8dfb322914e...` | `d21771bda036...` | 7,986,824 | 0 | 0 | 0 | 0 | 21.3s |
| **Total** | | | **60,926,503** | **69** | **180** | **0** | **0** | ~174s |

Row set for every count above: `load_features(v2_manifest_id)`'s full frame, joined 1:1 against `load_features(v1_manifest_id)`'s full frame on `(etime, decision_seq)` (sorted, then positionally compared -- proven valid only after `etime`/`decision_seq` were themselves proven bitwise identical per day, i.e. the join key IS the position). Total decision rows (60,926,503) matches `06-CONTEXT.md`'s independently-stated figure exactly. Total differing cells: 249 (69 + 153 + 27), matching D-06-19 and the committed errata manifest exactly.

## Files Created/Modified

- `.planning/phases/06-event-driven-simulator/evidence/06-04-v1-snapshot.json` -- the 7 v1 `(manifest_id, build_stats)` pairs, captured before any by-date pointer moved
- `.planning/phases/06-event-driven-simulator/evidence/06-04-v1-v2-label-diff.json` -- the 249-cell measured diff, in the errata manifest's exact `{date, etime, decision_seq, label_column}` cell shape, plus a `computed_from` field naming both manifest ids per date
- `.planning/phases/06-event-driven-simulator/evidence/06-04-task1-build-results.json` -- supplementary: per-day v1/v2 manifest ids, elapsed seconds, build_stats diff keys (not plan-mandated, kept as a durable trace of Task 1's own script output rather than left as an untracked scratch file)
- `.planning/phases/06-event-driven-simulator/evidence/06-04-task2-structural-report.json` -- supplementary: per-day structural equality proof (row counts, etime/decision_seq/feature-column/warmup-flag bitwise-identity booleans)
- `mvp/tests/features/test_schema_v2_regression.py` -- 2 tests: the plan-named cell-set-equality test, plus an anti-vacuity companion asserting every diff cell is confined to the 2 known errata dates and 2 known label columns
- 7 `mvp/data/lake_registry/manifests/BTCUSDT.features/by-date/*.json` -- repointed from each date's v1 manifest id to its new v2 manifest id

## Decisions Made

See `key-decisions` in the frontmatter for the four substantive ones: the step-1 `schema_version` bug caught pre-write via advisor review, the two false-positive build_stats-allowlist stops (both root-caused via source diffing, neither caused by wrong data), the resume-script discipline (never re-derive `code_hash` from a now-dirty tree, never re-snapshot a by-date pointer that already moved), and why no DQ acknowledgement was written.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug, caught before any write] The plan's own step-1 instruction would have read the wrong v1 build_stats file**
- **Found during:** advisor review, before the first `build_features_day` call
- **Issue:** `feature_build_stats_path(lake_root, "BTCUSDT", date)` with no `schema_version` argument reads `features.tier.FEATURE_SCHEMA_VERSION` from the CURRENT code, which is already 2 (06-01 landed it). The plan's step-1 text ("at this point in the sequence `FEATURE_SCHEMA_VERSION` on disk is still 1 for every date... no explicit `schema_version=1` argument is needed here") confuses "the physical v1 partitions on disk" with "the global version constant" -- the constant moved to 2 the moment 06-01 was committed, well before this plan runs.
- **Fix:** Snapshot script passes `schema_version=1` explicitly and prints the resolved path with an `exists()` assertion before reading, so the snapshot provably captured the bare `build_stats.json`, not a not-yet-existing `build_stats-v2.json`.
- **Files modified:** none (caught in the one-off script before it ran, not in committed code)
- **Verification:** `06-04-v1-snapshot.json`'s 7 entries all have non-empty, plausible v1 `build_stats` bodies (visible in the committed file)
- **Committed in:** `1dc0f34` (Task 1)

**2. [Rule 1 - Bug, self-caused] A self-imposed build_stats safety-net comparator was wrong twice, stopping the script on correct data**
- **Found during:** Task 1, building 2026-09-12 (first stop) and 2026-09-16 (second stop)
- **Issue:** Beyond the plan's own acceptance criteria (which are about label CELLS via Task 2), I added an extra build_stats diff check as a safety net. Its allowlist was wrong: (a) it did not anticipate that `asof_convention_disagreement_rows`/`label_std` are DERIVED from the same `absent` mask the null_stale fix extends (cascading automatically, not a second bug); (b) it assumed every non-errata day's `null_stale` count must be 0, when 2026-09-16/17/18 were actually built at `812a264` -- already carrying the null_stale fix -- so their OWN v1 build_stats already had non-zero `null_stale` counts, and the correct comparator is `v2_null_stale == v1_null_stale + errata_delta`, not `v2_null_stale == errata_delta`.
- **Fix:** Root-caused both via direct source reading (`git diff 12a15cff 6b4937838 -- features/labels.py`, confirming the null_stale term as the ONLY functional change; `git diff 812a264 6b4937838 -- features/labels.py`, confirming it is EMPTY) before accepting either day, then corrected the comparator function rather than loosening it blindly.
- **Files modified:** none (a one-off script's own bug; no committed code was wrong)
- **Verification:** the corrected comparator was applied to all 7 days including a re-check of 09-12/13/14/15 (already built) via a fallback results dict transcribed from the earlier runs' own stdout
- **Committed in:** `1dc0f34` (Task 1) -- the script itself was never committed (one-off, per the plan's own convention); only its verified output (the snapshot, the per-day results) is

---

**Total deviations:** 2, both self-contained to this plan's own one-off verification scripts (neither touched committed code, neither wrote incorrect data to the lake -- every day's v2 partition was correct on first build; only my own extra safety-net assertions needed two corrections before they matched the code they were checking).
**Impact on plan:** Zero scope creep. Both deviations were caught and resolved via source-level verification (not "explained away" on faith) before the corresponding day's build was accepted as correct, consistent with this plan's own absolute rule against adjusting an expected set without evidence.

## Issues Encountered

None beyond the two self-caused verification-script bugs documented above as deviations. No DQPauseError, no unexpected `FileExistsError`, no `ManifestHashMismatch` at any point across 7 builds, 7 report regenerations, 14 `load_features` calls (7 v1 + 7 v2), and 3 segment-upstream `load_features` calls.

## User Setup Required

None -- no external service configuration required.

## Known Stubs

None. Every partition, manifest, and evidence file written by this plan carries real, measured data from the real lake; no placeholder or hardcoded-empty value was introduced anywhere.

## Threat Flags

None beyond what this plan's own `<threat_model>` already names and mitigates (T-06-11: no v1 overwrite, confirmed via `git status`/`check_no_manifest_rewrite --full`; T-06-12: the diff is measured and independently cross-checked, not asserted; T-06-13: no DQ finding occurred, so the stop-and-report protocol was never exercised, but was ready). No new, unlisted security-relevant surface was introduced -- this plan writes data, not code.

## What Was NOT Done

- **No SIM requirement is marked complete.** This plan's frontmatter names `SIM-02`, but per this plan's own absolute rules, `requirements.mark-complete` was NOT run -- Plan 06-07 closes the phase and is the correct place for that.
- **No new segment manifest over v2 was issued.** Phase 5's committed segment manifest (`97964cb2...`) still names the v1 feature manifest ids and remains valid and readable (re-verified above); a v2-based segment manifest is explicitly Phase 7's call per D-06-18, not this plan's.
- **`mvp/spec.md` was not touched.** No catalogue or spec change was needed or made; `bid_price`/`ask_price` were already declared bookkeeping in 06-01.
- **No DQ acknowledgement was written, anywhere.** Every one of the 7 rebuilt days stayed `"ok"` on every `FEATURE_CHECKS` entry -- the null_stale fix's label-coverage impact (max 153 cells on the largest affected day) is roughly two orders of magnitude under the 2% degrade threshold. The plan's stop-and-report protocol for an unexplained DQ finding was never exercised because no finding occurred.
- **The one-off scripts themselves (`task1_rebuild.py`, `task1_resume*.py`, `task2_label_diff.py`) are not committed anywhere** -- they lived in the session scratchpad only, per the plan's own convention that real-lake reads outside `harness/`/the four sanctioned test files happen in an uncommitted one-off script, never a fifth sanctioned file under `mvp/`.
- **The physical v2 partition bytes, `build_stats-v2.json` files, and regenerated `report.parquet`/`report.md` files are not git-tracked** -- they live under `/Volumes/ProjectsSSD/aihedgefund/lake/`, per this project's standing convention (`LAKE_REGISTRY_ROOT` is the git-tracked audit trail; the physical lake is not). Only the registry JSON (manifests, by-date pointers) and this plan's evidence files are committed.

## Next Phase Readiness

- All 7 real feature days now exist at schema v2 with real `bid_price`/`ask_price`, ready for Plan 06-06's real-day perfect-foresight oracle to read.
- The migration mechanism (version-scoped write-once naming, multi-manifest DQ report) from `06-01` is now proven against the real lake, not only synthetic fixtures -- `T-06-03` (accepted-but-unexercised in `06-01-SUMMARY.md`) is now closed.
- The 249-cell regression proof is permanent and hermetic (`tests/features/test_schema_v2_regression.py` runs in ordinary CI, no lake mount needed) and will catch any future accidental change to either the errata manifest or a repeat rebuild's diff.
- No blockers identified for `06-05` (determinism) or `06-06` (oracle suite) or `06-07` (phase close).

---
*Phase: 06-event-driven-simulator*
*Completed: 2026-09-24*

## Self-Check: PASSED

- `.planning/phases/06-event-driven-simulator/evidence/06-04-v1-snapshot.json` -- FOUND, 7 entries
- `.planning/phases/06-event-driven-simulator/evidence/06-04-v1-v2-label-diff.json` -- FOUND, 249 cells
- `mvp/tests/features/test_schema_v2_regression.py` -- FOUND, 2 tests, both pass (`./.venv/bin/pytest tests/features/test_schema_v2_regression.py -x -q` -> `2 passed`)
- Commit `1dc0f34` -- FOUND in `git log --oneline`
- Commit `14f71fe` -- FOUND in `git log --oneline`
- `check_no_manifest_rewrite --full` -- exits 0 (138 manifests, sha256 mode)
- `check_manifest_append_only` -- exits 0 (140 registry entries)
- `check_manifest_id_integrity` -- exits 0 (140 manifests)
- `find mvp/features mvp/sim -name '*.nb[ci]'` -- empty, confirmed before both commits
