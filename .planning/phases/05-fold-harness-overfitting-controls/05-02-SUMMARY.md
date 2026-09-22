---
phase: 05-fold-harness-overfitting-controls
plan: 02
subsystem: eval-harness
tags: [fold-harness, purge-embargo, kfold, segment-manifest, compressed-3seg, purged-embargoed-oof]

# Dependency graph
requires:
  - phase: 05-fold-harness-overfitting-controls
    provides: "05-01's mvp/harness/ package (purge_embargo.py's shared exclusion math, segments.py's 5seg-only issuer, budget.py, accessor.py) and tests/fixtures/harness_span.py's real-span feature-tier fixture builder"
provides:
  - "mvp/harness/kfold.py: purged_embargoed_blocks (pure geometry, k contiguous blocks partitioning a train segment) and training_rows_for_block (the per-block training-row exclusion, reusing harness.purge_embargo.filter_train_rows textually rather than a third implementation)"
  - "harness/segments.py's issue_segment_manifest now issues BOTH layouts for real: 5seg with real D-05-02 geometric refusals (overlap, order, coverage, held_out's exemption, starvation), and compressed_3seg with real inner oof_block entries computed once at issuance"
  - "D-05-09's derived fields -- effective_intervals, purge_ns, embargo_ns, purged_row_count, embargoed_row_count, and (compressed_3seg only) oof_training_row_counts -- measured against REAL upstream partitions at issuance, never caller-supplied"
  - "A Rule 1 fix in harness/accessor.py: materialize()'s train-role purge/embargo filter no longer treats a train's own nested oof_block children as a purge/embargo source against that same train -- the bug that would have starved materialize(compressed_3seg, 'train') to zero rows the moment a real compressed_3seg manifest existed"
affects: [05-fold-harness-overfitting-controls/05-03, 05-fold-harness-overfitting-controls/05-04, 05-fold-harness-overfitting-controls/05-07, 07-stage-1-regression-vertical-slice]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "issue_segment_manifest now requires a real lake_root keyword-only parameter -- a gap in the plan's own 'interfaces unchanged' note this plan's own action text made unavoidable (derived row counts cannot exist without reading the lake). No optional/opt-out variant: every issued manifest carries real, measured fields, coordinator-confirmed as required per D-05-09."
    - "A train entry's own effective_intervals/purge/embargo geometry is computed against val/held_out roles ONLY -- never a sibling train, and never that train's own nested oof_block children (they partition the very train; counting them as an 'other' self-starves it by construction). This scoping rule is applied identically in three places: harness/segments.py's _train_effective_intervals/_derive_purge_embargo_fields (issuance), harness/accessor.py's materialize (read time), and is documented once, with its D-05-04 citation, in harness/purge_embargo.py's module docstring so a future caller finds the rule at the shared function's own contract rather than rediscovering it in each caller."
    - "harness/kfold.py's training_rows_for_block reuses harness.purge_embargo.filter_train_rows over the WHOLE blocks range as 'train' with the single target block as the sole 'other' entry, rather than pre-filtering to 'candidate = every other block' and reimplementing the exclusion a third time -- the target block's own rows are excluded automatically because they lie inside its own purge band."
    - "_validate_segments treats oof_block entries as CONTAINED WITHIN their parent train entry (not a pairwise-overlap participant against it) -- the overlap/chronological-order pass only ever inspects train/val entries; oof_block entries are checked separately, by containment against some train entry and by pairwise non-overlap against each other."

key-files:
  created:
    - mvp/harness/kfold.py
    - mvp/tests/harness/test_kfold.py
  modified:
    - mvp/harness/segments.py
    - mvp/harness/accessor.py
    - mvp/harness/purge_embargo.py
    - mvp/tests/harness/test_segments.py
    - mvp/tests/harness/test_accessor.py

key-decisions:
  - "Widened P1's uniform-900s 5seg fixture geometry to 1800s train / 600s val (Option A of a returned checkpoint, coordinator-approved): a uniform 900s width starves train_s2 under the real 600s purge horizon -- verified numerically against the real, unmodified effective_train_intervals BEFORE any code was written, not assumed. This required editing tests/harness/test_accessor.py, nominally owned by 05-04-PLAN.md in a normal parallel wave, but the coordinator confirmed wave 2 is running sequentially with Plan 04 not yet started, so the ownership rule (written for concurrent worktree agents) did not apply at execution time. Non-starvation of the new geometry is asserted directly in both fixtures (test_accessor.py's _build_fixture and test_segments.py's own dedicated test), not merely assumed."
  - "issue_segment_manifest gains a new required keyword-only lake_root parameter -- coordinator-confirmed as required (no optional/opt-out variant), since D-05-09's derived row counts cannot exist without reading real upstream partitions. Documented in the function's own docstring as a deviation from P1's 'interfaces unchanged' note."
  - "A train entry's own purge/embargo 'other' entries are scoped to val/held_out roles only, in issuance (_derive_purge_embargo_fields) as well as at read time (accessor.materialize) -- the plan's literal action-text phrasing ('an oof_block entry is itself an other entry for the train role's own effective_intervals computation') was verified numerically to self-starve every compressed_3seg train by construction (5 blocks of 600s partitioning a 3000s train, checked against each other, returns []). Flagged in a returned checkpoint before writing code; the coordinator confirmed this reading and asked for the corresponding accessor.py fix in the same commit."
  - "_validate_segments's held_out handling takes the coordinator's explicitly stated looser reading: a real, non-sentinel end_ns is accepted (not just the D-05-16 sentinel start_ns==end_ns==covered_end_ns) -- held_out is simply exempt from the upper coverage bound every train/val entry must respect, and must not start before the last train/val entry it follows."
  - "purged_row_count/embargoed_row_count give PURGE PRECEDENCE when a row falls in two different neighbours' bands simultaneously (one neighbour's embargo clause coinciding with a different neighbour's purge clause) -- the two counts are a partition of the excluded rows, never a double-count. Flagged as a real edge case (not hypothetical) before writing the row-counting code, and exercised directly by test_records_real_purged_and_embargoed_row_counts's train_s2 case (two val neighbours, one on each side)."

patterns-established:
  - "A pure-geometry helper (harness.segments._train_effective_intervals) is split out from the data-reading helper (_derive_purge_embargo_fields) specifically so the starvation refusal fires, and is unit-testable, BEFORE any partition is read -- cheap refusal, and no lake fixture needed to test the starvation path itself."
  - "Testing private (underscore-prefixed) module functions directly, by importing them from the test file, is an established project convention (grepped: tests/features/test_normalize.py, tests/dq/test_checks.py, tests/spec/test_render.py all do this) -- used here for _validate_segments, _covered_range, and _train_effective_intervals so pure-geometry refusals can be tested without a real lake fixture, reserving the real-fixture path (_build_fixture/issue_segment_manifest end to end) for the two derived-field tests that genuinely need real partition data."

requirements-completed: []
requirements-partial:
  - "EVAL-01: this plan delivers real D-05-02 geometric validation (overlap, order, coverage, held_out exemption, starvation) and real D-05-09 derived fields for the 5seg layout -- but per this plan's own absolute rules, no EVAL requirement is marked complete here; 05-07-PLAN.md closes the phase (the first real, git-committed segment manifest lands in the same commit as the manifest-guardrail extension)."
  - "EVAL-02: the compressed_3seg layout is now fully issuable, with real, non-starved inner purged+embargoed oof_block entries computed once at issuance and an anti-vacuity-proven training-row exclusion formula -- still deliberately left unchecked in REQUIREMENTS.md per this plan's own scope (05-07-PLAN.md's job)."

# Metrics
duration: ~65min (two task commits 11 min apart, 23:46:45 and 23:57:34 PDT on 2026-09-21; does not include the earlier design/verification/checkpoint round-trip time before Task 1's commit, which was not separately timestamped -- PLAN_START_TIME was not captured at session start, a process gap this SUMMARY reports rather than papers over)
completed: 2026-09-22
---

# Phase 5 Plan 2: D-05-02 Refusals, Starvation Refusal, and the Purged+Embargoed K-Fold OOF Split Summary

**Both fold layouts (5seg, compressed_3seg) are now fully issuable with real geometric validation, a real starvation refusal, and derived purge/embargo fields measured against actual feature partitions -- `harness/kfold.py`'s purged+embargoed inner k-fold OOF blocks are the one genuinely new algorithm this phase adds, proven anti-vacuous against real 600s/1s purge/embargo magnitudes.**

## Performance

- **Duration:** ~65 min including a returned checkpoint and coordinator round-trip (see `duration` above for the honest caveat on what was/wasn't timestamped)
- **Tasks:** 2 (both executed, both green, both committed individually)
- **Files modified:** 2 created (`harness/kfold.py`, `tests/harness/test_kfold.py`), 5 modified (`harness/segments.py`, `harness/accessor.py`, `harness/purge_embargo.py`, `tests/harness/test_segments.py`, `tests/harness/test_accessor.py`)

## Accomplishments

- `mvp/harness/segments.py`: real D-05-02 validation (`_validate_segments`) for both layouts -- pairwise overlap, chronological order (which also catches a val/held_out entry preceding the train it follows), coverage against the real upstream `etime` range (`_covered_range`, via `data.store.resolve_manifest`'s integrity-verified `partitions[]` metadata), `held_out`'s exemption from the upper coverage bound (D-05-16), and a starvation refusal (`_train_effective_intervals`) that fires on pure geometry before any partition is read.
- D-05-09's derived fields -- `effective_intervals`, `purge_ns`, `embargo_ns`, `purged_row_count`, `embargoed_row_count` (both layouts), plus `oof_training_row_counts` (compressed_3seg only) -- measured against real upstream partitions via `features.tier.load_features` at issuance, never a caller-supplied number. `issue_segment_manifest` gains a new required `lake_root` keyword-only parameter to make this possible.
- `mvp/harness/kfold.py`: `purged_embargoed_blocks` (pure geometry, k contiguous blocks, last absorbs the remainder) and `training_rows_for_block` (reuses `harness.purge_embargo.filter_train_rows` textually -- the identical two-sided-purge/one-sided-embargo formula the segment-level accessor uses).
- `compressed_3seg` is fully wired: `issue_segment_manifest(layout="compressed_3seg", ...)` requires `train`/`val`/`held_out` at the top level, splits `train` into `k` (default 5) named `oof_block_0..k-1` entries via `kfold.purged_embargoed_blocks`, and runs the merged segment list through the same validation/derivation pipeline `5seg` uses.
- Rule 1 bug fix in `harness/accessor.py`: `materialize()`'s train-role purge/embargo filter dropped `oof_block` from its "other entries" role list -- without this fix, `materialize(compressed_3seg_manifest, "train")` would have returned zero rows unconditionally the moment a real `compressed_3seg` manifest existed (every row is inside some block's own purge zone). This bug was latent in P1's own accessor code but never exercised (no real `oof_block` entries existed before this plan).
- 19 new tests (11 in `test_segments.py`, 8 in `test_kfold.py`), all green on first full-suite run after the two commits; full suite 996/996 (977 baseline + 11 + 8).
- Four mandatory mutation checks performed for real across both tasks: file hash printed and confirmed changed before each mutation, the named test(s) observed failing with the predicted failure mode, then the file restored and hash-verified byte-identical to the original before re-running green.

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | D-05-02 geometric refusals, held_out coverage exemption, starvation refusal, derived purge/embargo fields | `bda9f53` | `harness/purge_embargo.py`, `harness/segments.py`, `tests/harness/test_accessor.py`, `tests/harness/test_segments.py` |
| 2 | purged+embargoed inner k-fold OOF blocks, compressed_3seg layout | `2c656c8` | `harness/kfold.py`, `harness/segments.py`, `harness/accessor.py`, `tests/harness/test_kfold.py`, `tests/harness/test_segments.py` |
| fix | correct `accessor.py`'s stale train-purge docstring (found during self-check) | `de7a4bc` | `harness/accessor.py` |

**Plan metadata:** commit pending (this SUMMARY + STATE.md + ROADMAP.md)

## Files Created/Modified

- `mvp/harness/kfold.py` -- `purged_embargoed_blocks()`, `training_rows_for_block()`
- `mvp/harness/segments.py` -- `_validate_segments`, `_covered_range`, `_train_effective_intervals`, `_derive_purge_embargo_fields`, `_validate_compressed_3seg_shape`, `_derive_oof_training_row_counts`; `issue_segment_manifest` now issues both layouts for real, with a new required `lake_root` parameter and an optional `k` (default 5)
- `mvp/harness/accessor.py` -- `materialize()`'s train-role purge/embargo filter narrowed to `("val", "held_out")`
- `mvp/harness/purge_embargo.py` -- `effective_train_intervals`'s docstring gains the D-05-04 oof_block-scoping citation
- `mvp/tests/harness/test_segments.py` -- 11 new tests: 6 geometric refusals (direct against `_validate_segments`, no lake needed), 1 starvation refusal (direct against `_train_effective_intervals`), 2 derived-field tests (real fixture), 1 fixture-integrity test, 1 signature-inspection test; P1's own tests adapted to the widened geometry and new `lake_root` parameter; the obsolete `NotImplementedError` test replaced with a shape-refusal test
- `mvp/tests/harness/test_kfold.py` -- 8 new tests: block-partition geometry (2), the shared exclusion invariant + 2 anti-vacuity counterparts, compressed_3seg issuance, oof_block-materialization-counts-as-a-look (end to end), and the accessor-fix regression test

## Decisions Made

See `key-decisions` in the frontmatter for the five substantive ones: the widened 5seg fixture geometry (and the resulting edit to a file nominally owned by a sibling plan, justified by sequential-wave execution), the new required `lake_root` parameter, the oof_block purge-scoping fix (found via a pre-code numerical check, confirmed by the coordinator via a returned checkpoint), the held_out exemption's looser (non-sentinel-mandatory) reading, and the purge-precedence rule for overlapping purged/embargoed row counts.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking issue] `issue_segment_manifest` needs a `lake_root` parameter the plan's own interfaces note said was unchanged**
- **Found during:** Task 1 design, before writing any code
- **Issue:** the plan's `<interfaces>` block states `issue_segment_manifest`'s signature is "unchanged from P1" except for `purge_ns`/`embargo_ns` staying non-caller-parameters -- but Task 1's own action text requires calling `features.tier.load_features` to derive `purged_row_count`/`embargoed_row_count`, which is impossible without a real `lake_root`.
- **Fix:** added `lake_root: Path` as a new required keyword-only parameter, threaded through every caller (P1's own `test_segments.py`/`test_accessor.py` fixtures).
- **Files modified:** `harness/segments.py`, `tests/harness/test_segments.py`, `tests/harness/test_accessor.py`
- **Verification:** full suite green after the change; documented in the coordinator's checkpoint response as confirmed-required, no optional variant.
- **Committed in:** `bda9f53`

**2. [Rule 4 -> coordinator-resolved architectural conflict] P1's uniform-900s 5seg fixture starves `train_s2` under the real purge math, and the fixture lives in a file this plan was initially told not to edit**
- **Found during:** Task 1 design, before writing any code (verified numerically: `effective_train_intervals` on `train_s2` against `val_s1`/`val_s2`/`held_out` at the real 600s/1s purge/embargo returns `[]`)
- **Issue:** the plan's own must_haves require a starvation refusal; applying it unconditionally would break every test in `tests/harness/test_accessor.py` (nominally owned by 05-04-PLAN.md), which this plan was told not to edit under a rule written for concurrently-running parallel-wave agents.
- **Resolution:** returned a checkpoint before writing any code, laying out the numerically-verified conflict and three options. The coordinator confirmed wave 2 is running sequentially (05-04 has not started) so the ownership rule did not apply here, and approved Option A: widen the geometry to 1800s train / 600s val, assert non-starvation in the fixture itself.
- **Files modified:** `tests/harness/test_accessor.py` (widened `_five_seg_segments`, added `lake_root` kwarg, added an explicit non-starvation assertion), `tests/harness/test_segments.py` (mirrors the same geometry)
- **Verification:** full suite green (996/996); both files' own fixture-integrity tests assert non-starvation directly against the real `effective_train_intervals`.
- **Committed in:** `bda9f53`

**3. [Rule 1 - Bug, coordinator-confirmed] A train entry's own nested `oof_block` children must never be treated as a purge/embargo source against that same train**
- **Found during:** Task 2 design, before writing any code (verified numerically: 5 blocks of 600s partitioning a 3000s train, checked against each other via `effective_train_intervals`, returns `[]` -- every compressed_3seg train would self-starve by construction under the plan's own literal action-text phrasing)
- **Issue:** the plan's action text says "an oof_block entry is itself an 'other' entry for the train role's own effective_intervals computation," which self-starves every compressed_3seg train. The same bug already existed latently in P1's `harness/accessor.py` (`materialize`'s train-purge filter included `role="oof_block"`), just never exercised since no real `oof_block` entries existed before this plan.
- **Fix:** `harness/segments.py`'s `_derive_purge_embargo_fields`/`_train_effective_intervals` scope a train's "other" entries to `val`/`held_out` roles only; `harness/accessor.py`'s `materialize` gets the identical fix in the same commit (coordinator's explicit instruction).
- **Files modified:** `harness/segments.py`, `harness/accessor.py`
- **Verification:** `test_materialize_train_on_compressed_3seg_is_never_empty` (new, `test_kfold.py`) proves `materialize(compressed_3seg, "train").height > 0` and every returned row's `etime` falls inside the manifest's own recorded `effective_intervals["train"]`.
- **Committed in:** `2c656c8`

---

**Total deviations:** 3 auto-fixed (1 Rule 3 blocking-issue fix, 2 architectural/bug fixes surfaced via pre-code numerical verification and resolved through a returned checkpoint rather than silently patched). No scope creep -- all three are necessary for the plan's own must_haves (a starvation refusal that actually fires, a compressed_3seg that can actually issue) to be genuinely true rather than only true on paper.

## Issues Encountered

None beyond the two documented architectural deviations above, both surfaced and resolved BEFORE any code was written (verified numerically first, then checkpointed), so no rework was needed after the fact.

## Known Stubs

None -- this plan renders no data to a UI and has no downstream consumer yet. `oof_training_row_counts` and the row-count derived fields are real, measured numbers against real fixture data in every test that reads them.

## Threat Flags

None beyond what this plan's own `<threat_model>` already names (T-05-07: a hand-built manifest bypassing `_validate_segments` -- accepted, same posture as every other same-uid registry writer in this codebase; T-05-08: `harness.kfold.training_rows_for_block` called directly with `purge_ns=0`/`embargo_ns=0` -- accepted, the function is a pure utility by design, the guarantee lives in `issue_segment_manifest` always deriving the real constants internally).

## Next Phase Readiness

- Both fold layouts are fully issuable on synthetic data with real geometric/starvation refusals and real derived purge/embargo fields.
- `harness/kfold.py`'s two functions are independently tested and anti-vacuity-proven against real 600s/1s purge/embargo magnitudes -- `05-03-PLAN.md`/`05-04-PLAN.md` (row admission, errata) can build on real `oof_block` entries rather than a placeholder.
- `_LOOK_ROLES = {"val", "oof_block"}` (from P1) now has real `oof_block` entries to exercise it, proven end to end in `test_materializing_an_oof_block_counts_as_a_look`.
- Nothing in this plan wrote to the real, git-committed `mvp/data/lake_registry/segments/` -- confirmed absent on disk (this plan's own constraint; the guardrail extension + first real committed manifest land together in `05-07-PLAN.md`).
- **Not done in this plan** (by design, per this plan's own scope): row-admission exclusion (D-05-21), errata null-masking (D-05-20), budget exhaustion refusal (D-05-14), the negative-result log, the holdout-declaration tool, and any real, git-committed segment/errata manifest -- all named explicitly as later plans' work in existing docstrings/comments, not silently deferred. No EVAL requirement is marked complete in REQUIREMENTS.md, per this plan's own absolute rules (05-07-PLAN.md closes the phase).

## Self-Check: PASSED

- `mvp/harness/kfold.py` -- FOUND, exports `purged_embargoed_blocks`, `training_rows_for_block` (verified via `__all__` and direct import in tests)
- `mvp/harness/segments.py` -- FOUND, `issue_segment_manifest` issues both `5seg` and `compressed_3seg` (verified: `layout="compressed_3seg"` no longer raises `NotImplementedError`)
- `mvp/harness/accessor.py` -- FOUND, `materialize`'s train-purge filter is `("val", "held_out")` (verified via `grep -n 'role in (' mvp/harness/accessor.py`)
- `mvp/tests/harness/test_kfold.py` -- FOUND, 8 tests, all pass individually and in the full suite
- `mvp/tests/harness/test_segments.py` -- FOUND, 16 tests (5 P1-era adapted + 11 new), all pass
- `mvp/tests/harness/` -- FOUND, no `__init__.py` (`find mvp/tests/harness -name __init__.py` returns nothing)
- Commit `bda9f53` -- FOUND in `git log --oneline`
- Commit `2c656c8` -- FOUND in `git log --oneline`
- Commit `de7a4bc` -- FOUND in `git log --oneline`
- Full suite: 996 passed, 0 failed (`./.venv/bin/pytest tests -q`)
- `find mvp/features -name '*.nb[ci]'` -- empty
- `mvp/data/lake_registry/segments/` -- does not exist (confirmed via `ls`)
- All four mandatory mutation checks (Task 1: deleted overlap check, deleted starvation raise; Task 2: forced `purge_ns=0` in `training_rows_for_block`, dropped the last block's remainder) observed failing their named test(s) with the predicted failure mode, then restored to the exact original file hash before re-running green.

---
*Phase: 05-fold-harness-overfitting-controls*
*Completed: 2026-09-22*
