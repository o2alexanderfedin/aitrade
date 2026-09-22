---
phase: 05-fold-harness-overfitting-controls
plan: 01
subsystem: eval-harness
tags: [fold-harness, purge-embargo, segment-manifest, mlflow-budget, content-addressed-registry]

# Dependency graph
requires:
  - phase: 03-data-layer-backfill-ingest-lockbox
    provides: data.store's canonicalize_manifest/compute_manifest_id content-addressing primitives, data.lockbox's MLflow-first durable-counter shape
  - phase: 04-feature-label-engine
    provides: features.tier.load_features (the four-gate ordered accessor this plan's materialize() extends by one gate), data.time_ns.LABEL_HORIZON_NS/TRADE_FLOW_WINDOW_NS
provides:
  - "mvp/harness/ as a real, importable package: purge_embargo.py (derived purge horizon + pinned fold embargo + the one shared two-sided-purge/one-sided-embargo exclusion math), segments.py (5seg-only segment manifest writer, content-addressed, no partitions key), budget.py (MLflow-first durable look counter), accessor.py (the ordered-gates materialize() entry point)"
  - "A passing end-to-end walking-skeleton test: declare a 5-segment layout on synthetic data, issue its manifest, materialize a purge-excluded train frame and a val frame through the ONE harness accessor, and see the val materialization counted as a look in MLflow"
  - "tests/fixtures/harness_span.py: a real-span features-tier fixture builder through the actual write_feature_partition/issue_feature_manifest writer path (not feature_tier.py's vacuity-trap 3-microsecond span)"
affects: [05-fold-harness-overfitting-controls/05-02, 05-fold-harness-overfitting-controls/05-03, 05-fold-harness-overfitting-controls/05-04, 07-stage-1-regression-vertical-slice]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "purge/embargo exclusion is TWO explicit clauses (two-sided purge (start_ns-purge_ns, end_ns+purge_ns), one-sided trailing embargo [end_ns+purge_ns, end_ns+purge_ns+embargo_ns)) computed as ONE combined half-open excluded band [start_ns-purge_ns, end_ns+purge_ns+embargo_ns) in the implementation -- the boundary at exactly start_ns-purge_ns is EXCLUDED (belongs to the purged zone), not kept, which is what the plan's own exact-tuple test requires and is the concrete, code-level resolution of an ambiguity in the plan's prose (\"t > start_ns - h_max is kept\" vs. the literal expected tuple, which needs t = start_ns - h_max itself excluded)."
    - "harness.accessor.materialize's purge/embargo call filters other_entries to role in (val, held_out, oof_block) before calling filter_train_rows -- another train segment is never a purge/embargo source. The plan's <action> text's literal 'all segments except self' phrasing would have been geometrically equivalent for this plan's own contiguous 900s-wide 5seg fixture (train_s2 was always far enough from train_s1 not to matter either way), but the role-filtered form is what D-05-04 and the must_haves truths actually specify, and is the form that will matter once 05-02 adds oof_block entries and a starved layout."
    - "budget.record_look REQUIRES its caller's run_tags to already carry fold_config (raises BudgetError otherwise) -- budget.py has no segment manifest of its own to derive a layout name from; harness.accessor.materialize is the one that sets tags['fold_config'] = manifest['layout'] before calling record_look. A direct, standalone test of budget.py supplies fold_config itself, simulating what the accessor would have added."
    - "Feature-tier fixtures built for harness tests must also write an 'ok' DQ report row for the features-tier manifest (features.tier.load_features -> store._enforce_dq_pause requires one) -- tests/fixtures/harness_span.py:build_span_partition does this via the existing tests/fixtures/feature_build.py:write_dq_report helper, not a new writer."

key-files:
  created:
    - mvp/harness/__init__.py
    - mvp/harness/purge_embargo.py
    - mvp/harness/segments.py
    - mvp/harness/budget.py
    - mvp/harness/accessor.py
    - mvp/tests/harness/conftest.py
    - mvp/tests/harness/test_purge_embargo.py
    - mvp/tests/harness/test_segments.py
    - mvp/tests/harness/test_accessor.py
    - mvp/tests/harness/test_budget.py
    - mvp/tests/fixtures/harness_span.py
  modified: []

key-decisions:
  - "The purge zone's own start boundary (start_ns - purge_ns) is treated as INCLUSIVE in the excluded band (a row exactly there is excluded), even though D-05-04's prose describes an open interval there -- this is what the plan's own exact-tuple test (effective_train_intervals returning [(0, 3_400_000_000_000), (5_601_000_000_000, 10_000_000_000_000)]) requires, and both readings agree on every row-level assertion the plan states (±1 ns from the boundary)."
  - "harness.accessor.materialize filters purge/embargo 'other_entries' to role in (val, held_out, oof_block), never including another train segment, per D-05-04 and the plan's must_haves truths -- interpreted over the plan's more terse <action> prose ('all segments except self'), which was geometrically indistinguishable for this plan's own fixture but not for 05-02's."
  - "tests/fixtures/harness_span.py:build_span_partition also writes an 'ok' DQ report row for the features-tier manifest it issues (Rule 3 -- blocking issue: features.tier.load_features unconditionally requires one via store._enforce_dq_pause, discovered when Task 2's accessor tests raised DQPauseError on an otherwise-healthy synthetic fixture). Committed as part of Task 1's fixture file, since it completes that file's own contract rather than adding new Task 2 logic."
  - "The 5-segment test/accessor fixture uses five CONTIGUOUS 900-second segments (train_s1|val_s1|train_s2|val_s2|held_out, touching boundaries, no declared gap) rather than segments pre-spaced by purge+embargo width -- contiguous boundaries are what make the purge/embargo exclusion test non-vacuous: it must reach INTO a segment's own declared range and remove rows a naive half-open time filter would have kept, which a pre-gapped layout would not exercise."

patterns-established:
  - "Registry writers that name no partition bytes of their own (a segment manifest) reuse data.store.canonicalize_manifest/compute_manifest_id directly but duplicate (never import) the underscore-private _atomic_write_json, matching data.lockbox's own precedent for crossing that exact boundary."
  - "A durable MLflow counter (budget.py) copies data.lockbox's three-function shape (_require_canonical_tracking_root, _require_initialised_mlflow_store, the search_runs-with-filter_string pattern) verbatim, with its own exception class -- never a second, drifted implementation of the same fail-closed checks."

requirements-completed: []
requirements-partial:
  - "EVAL-01: this plan delivers the 5-segment split, real embargo gaps, and segment manifests stored as content-addressed data -- but D-05-02's overlap/order/coverage validation refusals are 05-02-PLAN.md's job, and no real, git-committed segment manifest exists yet (that lands in 05-07-PLAN.md, the same commit as the manifest-guardrail extension). EVAL-01 stays unchecked, deliberately."
  - "EVAL-02: the compressed_3seg layout is NOT implemented -- issue_segment_manifest(layout='compressed_3seg', ...) raises NotImplementedError by design, naming 05-02-PLAN.md as the plan that adds the purged+embargoed inner k-fold OOF split (harness/kfold.py). EVAL-02 stays unchecked."
  - "EVAL-03: the selection-bias budget's look-accounting (MLflow-first counting, keyed on (segment_manifest_id, segment_name)) is real and tested, but exhaustion refusal (D-05-14, 'budget exhaustion forces a fresh window') is explicitly NOT implemented -- budget.py's own docstring states this is a later plan's job. EVAL-03 stays unchecked."

# Metrics
duration: ~50min
completed: 2026-09-20
---

# Phase 5 Plan 1: Thin Vertical Slice -- Segment Manifest, Purge/Embargo, Budgeted Accessor Summary

**A synthetic 5-segment fold layout, issued as a content-addressed manifest, materializes a purge-excluded train frame and a validation frame through one gated accessor, and the validation pull is durably counted as a look in MLflow -- the first real, non-test code in `mvp/harness/`.**

## Performance

- **Duration:** ~55 min (estimated from the task/fix commit timestamps, 21:56-22:20, plus prior reading/design time; no start-epoch was captured at session start)
- **Tasks:** 2 (both executed, both green, both committed individually), plus 1 follow-up anti-vacuity fix found during advisor review before this SUMMARY was finalized
- **Files modified:** 11 created, 1 further modified (post-review fix)

## Accomplishments

- `mvp/harness/purge_embargo.py`: `PURGE_HORIZON_NS` derived from `data.time_ns.LABEL_HORIZON_NS` (never a literal -- verified by grepping the module's own source for `"600"` and finding none), `FOLD_EMBARGO_NS` pinned to `TRADE_FLOW_WINDOW_NS` and to `ofi`'s catalogue `information_set` text (`"[prev_l1_update, t]"`), `effective_train_intervals()`/`filter_train_rows()` as the one shared two-sided-purge/one-sided-embargo implementation.
- `mvp/harness/segments.py`: `issue_segment_manifest()` for the `5seg` layout only (`compressed_3seg` raises `NotImplementedError` naming `05-02-PLAN.md`), a segment manifest whose body omits `partitions` entirely and whose `purge_ns`/`embargo_ns` are set internally from `harness.purge_embargo`'s own constants, never a caller parameter. `read_segment_manifest()` re-verifies the body's own hash on every read.
- `mvp/harness/budget.py`: `look_count()`/`record_look()`, an MLflow-first durable counter copying `data.lockbox`'s exact shape (query first, refuse an uninitialised/non-canonical store before any client, exceptions propagate unmodified). Granularity is the pair `(segment_manifest_id, segment_name)` -- never `model_class`.
- `mvp/harness/accessor.py`: `materialize()`, the ordered-gates entry point -- self-hash-verified manifest read, unconditional `held_out` refusal before any upstream data is touched, the real `features.tier.load_features` call per upstream manifest, half-open time filtering, REAL purge/embargo exclusion for train rows, and `budget.record_look` for `val`/`oof_block` roles (keyed on role, not layout).
- `mvp/tests/fixtures/harness_span.py`: a real-span, `FEATURE_ROW_SCHEMA`-shaped fixture builder through the actual writer/manifest-issuer path, parameterized `(start_ns, step_ns, rows)` -- explicitly not `feature_tier.py:feature_frame`'s 3-microsecond vacuity trap.
- 21 new tests, all green; full suite 977/977 (956 baseline + 21 new).
- Both plan-mandated mutation checks performed for real: file hash printed and confirmed changed before each mutation, the named test observed failing with the predicted failure mode, then the file restored and hash-verified equal to the original before re-running green.

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | purge/embargo constants + shared exclusion math + segment manifest schema (5seg only) | `93181c0` | `harness/__init__.py`, `harness/purge_embargo.py`, `harness/segments.py`, `tests/harness/conftest.py`, `tests/harness/test_purge_embargo.py`, `tests/harness/test_segments.py`, `tests/fixtures/harness_span.py` |
| 2 | minimal accessor + budget -- real purge/embargo exclusion, held_out refusal, look accounting for val AND oof_block | `74692c5` | `harness/accessor.py`, `harness/budget.py`, `tests/harness/test_accessor.py`, `tests/harness/test_budget.py` |
| fix | assert the accessor fixture actually covers all five segments (anti-vacuity, found during advisor review) | `ee95b63` | `tests/harness/test_accessor.py` |

**Plan metadata:** commit pending (this SUMMARY + STATE.md + ROADMAP.md)

## Files Created/Modified

- `mvp/harness/__init__.py` -- empty, makes `harness` a real package
- `mvp/harness/purge_embargo.py` -- `PURGE_HORIZON_NS`, `FOLD_EMBARGO_NS`, `effective_train_intervals()`, `filter_train_rows()`
- `mvp/harness/segments.py` -- `FIVE_SEG_NAMES`/`FIVE_SEG_ROLES`, `issue_segment_manifest()`, `segment_manifest_path()`, `read_segment_manifest()`
- `mvp/harness/budget.py` -- `BudgetError`, `look_count()`, `record_look()`
- `mvp/harness/accessor.py` -- `materialize()`
- `mvp/tests/harness/conftest.py` -- `lake_root`/`registry_root`/`tracking_root` fixtures, canonical-tracking-root isolation, active-run cleanup
- `mvp/tests/harness/test_purge_embargo.py` -- 6 tests
- `mvp/tests/harness/test_segments.py` -- 5 tests
- `mvp/tests/harness/test_accessor.py` -- 5 tests
- `mvp/tests/harness/test_budget.py` -- 5 tests
- `mvp/tests/fixtures/harness_span.py` -- `build_span_partition()`

## Decisions Made

See `key-decisions` in the frontmatter above for the four substantive ones: the purge-zone left-boundary inclusivity resolution (matching the plan's own exact-tuple test over a stricter reading of its prose), filtering purge/embargo `other_entries` to `val`/`held_out`/`oof_block` roles only, the DQ-report fixture fix, and the contiguous-segment fixture geometry choice.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking issue] `features.tier.load_features` requires a DQ report row that the fixture builder did not write**
- **Found during:** Task 2, first real run of `test_materialize_filters_by_half_open_time_window` (and every other accessor test that calls `materialize()` against real data)
- **Issue:** `harness.accessor.materialize()` calls `features.tier.load_features`, which calls `store._enforce_dq_pause`, which raises `DQPauseError` for any date with no DQ report row at all (`missing`, same as `failed`/`degraded`) -- an otherwise perfectly healthy synthetic fixture built via `write_feature_partition`/`issue_feature_manifest` alone has none.
- **Fix:** `tests/fixtures/harness_span.py:build_span_partition` now also calls the existing `tests.fixtures.feature_build.write_dq_report` helper, writing one `("features", manifest_id, "ok")` row for the date/manifest it just issued.
- **Files modified:** `mvp/tests/fixtures/harness_span.py`
- **Verification:** `test_materialize_filters_by_half_open_time_window` and every other Task 2 accessor test now pass against real, loaded feature rows; full suite 977/977.
- **Committed in:** `93181c0` (Task 1's commit, since this completes `harness_span.py`'s own fixture contract rather than adding Task 2 logic)

**2. [Rule 1 - Bug] `test_accessor.py`'s own fixture never asserted it actually covers all five declared segments**
- **Found during:** advisor review, after both tasks' own automated verification passed
- **Issue:** the plan's absolute rule requires "every fixture must be asserted to have the property it exercises (span, rows per segment) before the behavioural assertion." `test_purge_embargo.py`'s fixture-span test exercises a DIFFERENT `build_span_partition` call (8000 rows, 200s width) than the one `test_accessor.py::_build_fixture` actually uses (4500 rows, 900s contiguous) -- nothing asserted the 4500-row partition's physical span reaches every declared segment's boundary. A too-short `rows` value would still pass every test's own `df.height > 0` check while one or more segments silently received zero rows.
- **Fix:** two assertions added to `_build_fixture` (`span["etime_min"] <= segments[0]["start_ns"]`, `span["etime_max"] >= segments[-1]["end_ns"] - NS_PER_SECOND`), verified non-vacuous directly by calling `_build_fixture(rows=1000)` and confirming it raises `AssertionError` naming exactly which segment boundary the span misses.
- **Files modified:** `mvp/tests/harness/test_accessor.py`
- **Verification:** full suite still 977/977; the vacuity probe above raised as expected.
- **Committed in:** `ee95b63`

---

**Total deviations:** 2 auto-fixed (1 Rule 3 blocking-issue fix, 1 Rule 1 anti-vacuity bug fix found on review). No architectural change, no Rule 4 checkpoint.
**Impact on plan:** Both necessary for the plan's own stated deliverable (a REAL, not deferred, purge/embargo exclusion proven against real, fixture-verified loaded rows) to be genuinely exercised rather than only unit-tested against hand-built frames or a possibly-under-covered fixture. No scope creep -- both fixes are inside the fixture/test modules the plan itself specifies.

## Issues Encountered

None beyond the one documented deviation above. Two genuine ambiguities in the plan's own text were resolved by implementation (documented as key-decisions, not "issues" since no test failed because of them): the purge-zone left-boundary inclusivity question (resolved in favor of the plan's own literal exact-tuple test), and whether `filter_train_rows`'s `other_entries` should include other `train`-role segments (resolved per D-05-04/must_haves, geometrically inconsequential for this plan's own fixture either way).

## Known Stubs

None -- this plan renders no data to a UI and has no downstream consumer yet. `materialize()`'s docstring explicitly names its own two deferred gates (row-admission exclusion, D-05-21; errata null-masking, D-05-20) as a later plan's job, not a stub -- both pass rows through unchanged rather than silently defaulting to an empty/mock value.

## Threat Flags

None. This plan introduces no new network endpoint, auth path, or schema at a trust boundary beyond what the plan's own `<threat_model>` already names (T-05-04 self-hash re-verification, T-05-05 unconditional `held_out` refusal, T-05-06 MLflow-query-failure propagation, T-05-20 the manifest's own `purge_ns`/`embargo_ns` fields being trusted as internally consistent -- all implemented exactly as the threat model's `Mitigation Plan` column specifies).

## Next Phase Readiness

- `mvp/harness/` is a real, importable package with a passing end-to-end walking-skeleton test proving: declare a 5-segment layout -> issue its manifest -> materialize a purge-excluded train frame and a validation frame through the ONE harness accessor -> see the validation pull counted as a look in MLflow.
- The look-accounting gate (`_LOOK_ROLES = {"val", "oof_block"}`) is already correct for `oof_block` roles, proven via a hand-built synthetic manifest entry in `test_materialize_counts_val_and_oof_block_but_not_train_as_a_look` -- `05-02-PLAN.md`'s `compressed_3seg` layout (with real `oof_block` entries from `harness.kfold`) will exercise this same, already-tested code path.
- `harness.purge_embargo.filter_train_rows`/`effective_train_intervals` are the one shared implementation `05-02-PLAN.md`'s issuer must also call (per D-05-04) -- not reimplemented.
- Nothing in this plan wrote to the real, git-committed `mvp/data/lake_registry/segments/` -- confirmed absent on disk. The manifest-guardrail extensions (`check_manifest_append_only`, `check_manifest_id_integrity`) remain untouched, as this plan's own constraints require; they become `05-07-PLAN.md`'s job, landing in the same commit as the first real, committed segment manifest (Pitfall 3 in `05-RESEARCH.md`).
- **Not done in this plan** (by design, per the plan's own scope): the `compressed_3seg` layout, the purged+embargoed inner k-fold OOF split (`harness/kfold.py`), row-admission exclusion (D-05-21), errata null-masking (D-05-20), budget exhaustion refusal (D-05-14), the negative-result log, the holdout-declaration tool, and any real, git-committed segment/errata manifest. All are named explicitly in this plan's own code as later plans' work, not silently deferred.

## Self-Check: PASSED

- `mvp/harness/__init__.py` -- FOUND
- `mvp/harness/purge_embargo.py` -- FOUND, exports `PURGE_HORIZON_NS`, `FOLD_EMBARGO_NS`, `effective_train_intervals`, `filter_train_rows` (verified via `__all__` and direct import in tests)
- `mvp/harness/segments.py` -- FOUND, exports `issue_segment_manifest`, `segment_manifest_path`, `read_segment_manifest`
- `mvp/harness/budget.py` -- FOUND, exports `look_count`, `record_look`
- `mvp/harness/accessor.py` -- FOUND, exports `materialize`
- `mvp/tests/harness/` -- FOUND, no `__init__.py` (`find mvp/tests/harness -name __init__.py` returns nothing)
- `mvp/tests/fixtures/harness_span.py` -- FOUND
- Commit `93181c0` -- FOUND in `git log --oneline`
- Commit `74692c5` -- FOUND in `git log --oneline`
- Commit `ee95b63` -- FOUND in `git log --oneline`
- Full suite: 977 passed, 0 failed (`./.venv/bin/pytest tests -q`)
- `find mvp/features -name '*.nb[ci]'` -- empty
- `mvp/data/lake_registry/segments/` -- does not exist (confirmed via `ls`)
- Both mutation checks (Task 1: hardcoded `PURGE_HORIZON_NS`, deleted embargo clause; Task 2: deleted `filter_train_rows` call, narrowed the look-accounting role check) observed failing their named test with the predicted failure mode, then restored to the exact original file hash before re-running green.
- **Not done / self-check caveat:** `EVAL-01`/`EVAL-02`/`EVAL-03` are NOT marked complete in REQUIREMENTS.md -- an earlier `requirements.mark-complete` run in this session incorrectly checked all three off wholesale (`compressed_3seg` raises `NotImplementedError`; budget exhaustion is not implemented); reverted via `git checkout -- .planning/REQUIREMENTS.md` before anything was committed. See `requirements-partial` in this file's frontmatter for what remains.

---
*Phase: 05-fold-harness-overfitting-controls*
*Completed: 2026-09-20*
