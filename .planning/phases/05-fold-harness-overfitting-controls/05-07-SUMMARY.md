---
phase: 05-fold-harness-overfitting-controls
plan: 07
subsystem: harness
tags: [mlflow, manifests, guardrails, purge-embargo, selection-bias-budget, errata]

# Dependency graph
requires:
  - phase: 05-fold-harness-overfitting-controls (00-06)
    provides: harness/segments.py, harness/accessor.py, harness/budget.py, harness/row_admission.py, harness/errata.py, harness/kfold.py, harness/negative_log.py, tools/check_harness_accessor_only.py, and the widened 7-day feature pool (05-00)
provides:
  - The first real, git-committed segment manifest (compressed_3seg, 2026-09-12..14) and its errata companion, resolvable on the real lake
  - check_manifest_append_only and check_manifest_id_integrity generalized to cover segments/ and errata/ registries
  - admission.counts derived at issuance (harness/segments.py), closing the gap 05-04-PLAN.md's own SUMMARY named
  - fold_config_reason wired into every look's MLflow tags (EVAL-02's own "reason recorded" clause)
  - mvp/spec.md "## Fold harness" section
  - EVAL-01, EVAL-02, EVAL-03, EVAL-04 marked complete in REQUIREMENTS.md
affects: [phase-06, phase-07, phase-08, phase-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Registry guardrail extension: REGISTRY_DIR_NAMES per-directory loop (Rules 4/5/6 run once per directory that physically exists on disk; a directory not yet created is skipped, not an error; a global fallback refuses if NONE exist at all)"
    - "Same-commit discipline for a newly-protected-but-empty registry directory (guardrail extension + its first manifest, one commit, or Rule 5's vacuity check refuses)"
    - "issue_segment_manifest loads its upstream frame ONCE, shared across purge/embargo, oof-training-count, and admission-count derivations"

key-files:
  created:
    - mvp/data/lake_registry/segments/97964cb27f62aa07201e2e52f788240d4f7801582c7cac25ac21107aa9e330e2.json
    - mvp/data/lake_registry/errata/22190ad925929001855c4cec56c7ddbd00043d4717ec02ac96fc9b71e3e31b58.json
  modified:
    - mvp/harness/segments.py
    - mvp/harness/accessor.py
    - mvp/tools/check_manifest_append_only.py
    - mvp/tools/check_manifest_id_integrity.py
    - mvp/spec.md
    - mvp/tests/harness/test_segments.py
    - mvp/tests/harness/test_accessor.py
    - mvp/tests/harness/test_kfold.py
    - mvp/tests/tools/test_check_manifest_append_only.py
    - mvp/tests/tools/test_check_manifest_id_integrity.py

key-decisions:
  - "admission.counts is DERIVED by issue_segment_manifest itself (harness.row_admission.apply_admission_policy against every segment entry's real upstream rows), never caller-supplied -- an orchestrator absolute_rule that supersedes 05-07-PLAN.md's own Task 2 prose (which still described admission as fully caller-supplied, matching 05-04-PLAN.md's known, explicitly-named gap)"
  - "REGISTRY_DIR_NAMES' Rule 5 vacuity check is scoped PER DIRECTORY (physical existence on disk), with a global fallback error if none of manifests/segments/errata exist at all -- preserves WR-07's 'a path bug must not read as nothing was rewritten' guarantee while letting every existing manifests/-only fixture keep passing unchanged"
  - "The real segment manifest's held_out entry is the documented zero-width sentinel [covered_end_ns, covered_end_ns) -- no held-out day exists among the three original built days (D-05-16/17); Phase 5 ships the declaration tool, it does not declare"
  - "budget_allowance=5 for the real manifest: a conservative round number giving several MLflow-look experiments before a fresh, non-overlapping segment manifest is required; no Optuna sweep sizing informs it since Phase 5 computes no metric (D-05-23)"
  - "train/val boundary is the UTC day boundary between 09-13 and 09-14 (1789344000000000000 ns) -- train = 09-12+09-13, val = 09-14 whole"

requirements-completed: [EVAL-01, EVAL-02, EVAL-04]

# Metrics
duration: 70min
completed: 2026-09-22
---

# Phase 5 Plan 7: Guardrail Extension + First Real Segment/Errata Manifests + Fold Harness Spec Section Summary

**The first real, git-committed segment manifest (`compressed_3seg`, 2026-09-12..14) and its 249-cell errata companion now exist on the real lake, resolvable end to end, in the same commit that extends `check_manifest_append_only`/`check_manifest_id_integrity` to cover `segments/`/`errata/` -- closing the phase's last open gap (`admission.counts` was caller-supplied, now derived at issuance) and adding the `spec.md` "Fold harness" section no CI hook enforces.**

## Performance

- **Duration:** ~70 min
- **Started:** 2026-09-22T03:20:00-07:00 (approx, first file read)
- **Completed:** 2026-09-22T04:29:00-07:00
- **Tasks:** 3 planned + 1 unplanned (Rule 2 deviation: admission.counts derivation)
- **Files modified:** 12 (2 created, 10 modified)

## Accomplishments

- `mvp/data/lake_registry/segments/97964cb2....json` -- the first real segment manifest: `layout="compressed_3seg"`, `train`=[2026-09-12 00:00, 2026-09-14 00:00), `val`=2026-09-14 whole day, `held_out`=the zero-width sentinel (no held-out day exists among the three original days), 5 `oof_block` entries partitioning `train`, real `admission.counts` per entry (train/val/held_out/oof_block_0..4), real derived `purge_ns`/`embargo_ns`/`effective_intervals`/`purged_row_count`/`embargoed_row_count`, `errata_id` naming the committed errata manifest, `fold_config_reason` stated, three real upstream feature-manifest ids.
- `mvp/data/lake_registry/errata/22190ad9....json` -- the first real errata manifest: 249 cells (180 `ret_1s_mid` + 69 `ret_10s_mid`), re-verified BOTH by count AND by exact `(date, etime, decision_seq, label_column)` set equality against `05-04-PLAN.md`'s own evidence file (`evidence/05-04-errata-cells.json`) -- zero discrepancy.
- `check_manifest_append_only.py` and `check_manifest_id_integrity.py` generalized from a single `manifests/` directory to `REGISTRY_DIR_NAMES = {"manifests", "segments", "errata"}`, landing in the SAME commit as the two manifests above (Rule 5's vacuity trap, 05-RESEARCH.md Q1).
- `harness/segments.py:issue_segment_manifest` now DERIVES `admission.counts` at issuance (was caller-supplied, 05-04-PLAN.md's own SUMMARY named this gap explicitly) -- unplanned but required by the orchestrator's absolute_rules.
- `harness/segments.py`/`harness/accessor.py`: `fold_config_reason` is a new required field, stored on every segment manifest and merged additively into every look's MLflow `run_tags` (EVAL-02's "reason recorded" clause).
- `mvp/spec.md` gained a `## Fold harness` section (between `## Sharpe annualization convention` and `## MLflow tag schema`) -- no CI hook enforces its presence (`check_spec_diff` compares only `features.toml`/`labels.toml`).
- `.planning/REQUIREMENTS.md`: EVAL-01, EVAL-02, EVAL-03, EVAL-04 all marked complete, each with a proof line below.

## Task Commits

1. **Task 1: fold_config_reason wiring into MLflow** - `c284627` (feat)
2. **[Rule 2 deviation] admission.counts derived at issuance, never caller-supplied** - `1dc7f06` (feat)
3. **Task 2: guardrail extension + first real segments/errata manifests (ONE commit)** - `255e19d` (feat)
4. **Task 3: spec.md Fold harness section + phase-close verification** - `f80307b` (docs)

_Task 2's commit is the same-commit-mandatory one (Pitfall 3): the guardrail-tool changes and the two new manifest JSON files landed together, as required. The admission.counts derivation (Rule 2) is its OWN commit, landing between Task 1 and Task 2, because it is independently testable harness logic, not part of the guardrail-tool change -- and it had to exist before Task 2's real manifest was issued (the manifest's `code_hash` must point at a commit that actually contains the code that produced its own body)._

## Files Created/Modified

- `mvp/data/lake_registry/segments/97964cb27f62aa07201e2e52f788240d4f7801582c7cac25ac21107aa9e330e2.json` - the first real segment manifest
- `mvp/data/lake_registry/errata/22190ad925929001855c4cec56c7ddbd00043d4717ec02ac96fc9b71e3e31b58.json` - the first real errata manifest
- `mvp/harness/segments.py` - `fold_config_reason` required field; `_load_upstream_frame` (loaded once, shared); `_derive_admission_counts` (new, D-05-21); `_derive_purge_embargo_fields`/`_derive_oof_training_row_counts` refactored to accept the shared frame instead of loading their own
- `mvp/harness/accessor.py` - gate 7 merges `fold_config_reason` into `run_tags` additively
- `mvp/tools/check_manifest_append_only.py` - `REGISTRY_DIR_NAMES` (was `MANIFESTS_DIR_NAME`); `_is_manifest_shaped` generalized; `check_append_only`'s body split into `_check_append_only_detailed` (per-directory loop, real implementation) + a thin `sum()`-wrapping public function (unchanged 2-tuple signature every existing test destructures); `main()` prints a per-directory breakdown line
- `mvp/tools/check_manifest_id_integrity.py` - `_iter_manifest_files` globs across `REGISTRY_DIR_NAMES`
- `mvp/spec.md` - new `## Fold harness` section
- `mvp/tests/harness/test_segments.py` - `fold_config_reason` test + mutation check; `test_issue_segment_manifest_discards_a_hard_coded_admission_counts` + mutation check; existing fixture/assertion updates for the derived `admission.counts` shape
- `mvp/tests/harness/test_accessor.py` - `test_materialize_tags_fold_config_and_reason_on_every_look`; `FOLD_CONFIG_REASON` constant; existing fixture/probe-manifest updates
- `mvp/tests/harness/test_kfold.py` - `fold_config_reason` added to its own `issue_segment_manifest` call site (not in Task 1's `files_modified` list, updated as a Rule 3 blocking fix, same precedent 05-03-PLAN.md set for `tracking_root`)
- `mvp/tests/tools/test_check_manifest_append_only.py` - 6 new tests: manifests-only regression guard, segments+errata coverage/counting, committed delete of a segments manifest, uncommitted delete of an errata manifest, untracked-segments-manifest vacuity (naming only `segments/`), no-known-directory-at-all fallback
- `mvp/tests/tools/test_check_manifest_id_integrity.py` - 3 new tests: segments+errata coverage, hand-edited segments manifest caught, hand-edited errata manifest caught

## Decisions Made

- **`admission.counts` derivation moved into this plan (Rule 2 deviation).** 05-07-PLAN.md's own Task 2 prose still described `admission` as fully caller-supplied (mirroring 05-04-PLAN.md's honest, explicitly-named gap: "the manifest body's `admission.counts` field stays whatever the caller supplies at issuance"). The orchestrator's `<absolute_rules>` explicitly required deriving it at issuance and adding a test that fails when counts are hard-coded or omitted -- this supersedes the plan text. `issue_segment_manifest` now loads the upstream frame ONCE (previously three separate loads across purge/embargo, oof-training-count, and would-be admission derivations) and computes `stale_book_age_ns` on the full, pre-slice frame before any segment time-slice, matching `harness.accessor.materialize`'s own gate-order requirement exactly.
- **`held_out` is the documented zero-width sentinel**, not a real interval: no held-out day exists among the three original built days (D-05-16 says `D_lock` is a FUTURE date declared at Phase 8's v0 gate; D-05-17 says Phase 5 ships the declaration tool, it does not declare). `start_ns == end_ns == covered_end_ns` is accepted by `_validate_segments` per D-05-16's documented sentinel encoding.
- **`train`/`val` boundary is the UTC day boundary** between 2026-09-13 and 2026-09-14 (`1789344000000000000` ns) -- a clean, auditable split (train = the first two built days, val = the third whole day) rather than an arbitrary intra-day cut.
- **`budget_allowance=5`**: a conservative, round number. No Optuna sweep or other quantitative sizing informs it -- Phase 5 computes no metric of any kind (D-05-23), so there is no "expected number of experiments" to size against yet; 5 gives headroom for several MLflow-look experiments in Phase 6/7 before a fresh, non-overlapping segment manifest is required.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] `admission.counts` derived at issuance instead of accepted caller-supplied**
- **Found during:** Task 2 preparation (advisor review, before the same-commit manifest issuance)
- **Issue:** 05-07-PLAN.md's own Task 2 action text still had the real segment manifest's `admission` field fully caller-supplied (`{"policy": "stale_book", "max_age_ns": ..., "counts": <computed by calling apply_admission_policy ... >}`) -- functionally, the CALLER (this plan's own issuance script) would compute and pass the counts, not `issue_segment_manifest` itself. The orchestrator's `<absolute_rules>` require the ISSUER to derive them; a caller-supplied value must be discarded.
- **Fix:** `harness/segments.py:issue_segment_manifest` now derives `admission["counts"]` internally via `harness.row_admission.apply_admission_policy`, against every segment entry's real upstream rows, with stale-book age computed on the full pre-slice frame. Any `counts` value a caller supplies is overwritten, never written to the body. `_load_upstream_frame` was added so the purge/embargo, oof-training-count, and admission-count derivations share ONE frame load instead of three.
- **Files modified:** `mvp/harness/segments.py`, `mvp/tests/harness/test_segments.py`
- **Verification:** `test_issue_segment_manifest_discards_a_hard_coded_admission_counts` (a caller-supplied `{"train_s1": {"admitted": 999}}` is discarded; the real derived value is asserted against the fixture's own known row count); `test_issue_segment_manifest_records_every_d05_09_field` updated to assert derived-not-`{}` counts, with a per-entry row-conservation check. Mutation check: stubbing the derivation to return `{}` made BOTH tests fail (a `KeyError` in the field-set test, a direct assertion failure in the hard-coded-discard test) before the derivation was restored.
- **Committed in:** `1dc7f06` (its own commit, not folded into Task 1 or Task 2 -- see Task Commits note above for why)

---

**Total deviations:** 1 auto-fixed (Rule 2 - missing critical functionality)
**Impact on plan:** Necessary for the real manifest's own correctness (D-05-09/21) and for the orchestrator's explicit same-commit requirement to mean what it says -- a manifest whose `admission.counts` the issuer trusted blindly from its own caller would not actually prove D-05-21 is real. No scope creep beyond what the absolute_rules named.

## Issues Encountered

- **Provenance/code_hash ordering.** The FIRST attempt at the real segment manifest was issued against `HEAD=c284627` (before the admission.counts derivation commit existed) and carried `code_hash="c284627-dirty"`. Re-issuing after `1dc7f06` landed (deleting the stale, untracked JSON files first, so `_discover_existing_manifests` found nothing stale to overlap-check against) produced a NEW manifest id (`97964cb2...`, `code_hash="1dc7f062...-dirty"`) whose provenance is now honest: the code that produced its body is the code actually committed at that `code_hash`. The errata manifest's id (`22190ad9...`) is unchanged across both attempts (it carries no `code_hash`, and its cell content is identical).
- **`-dirty` suffix is unavoidable and expected.** `tracking.mlflow_utils.compute_code_hash()` appends `-dirty` whenever `git status --porcelain` is non-empty at issuance time -- and it always is here, because the manifest JSON files themselves are new, untracked files sitting in the working tree at the moment `compute_code_hash()` runs (they are written by the same script, moments before the commit that finally makes the tree clean again). This is a structural property of write-once, content-addressed issuance immediately followed by commit, not a mistake.
- **`val`'s coverage is 1 row short of 09-14's full row count.** `val`'s admission counts sum to `11,291,363 + 32,330 + 0 = 11,323,693`, one less than 09-14's actual `11,323,694` decision rows. `_validate_segments` caps every top-level entry's `end_ns` at `covered_end_ns` (the exact max `etime` in the upstream partitions); the half-open `< end_ns` slice in both `_derive_admission_counts` and `harness.accessor.materialize` therefore excludes the single row whose `etime` equals `covered_end_ns` exactly. This is an inherent property of the framework's coverage check (any segment ending exactly at the covered range's ceiling loses that one row), not specific to this manifest's own geometry -- flagged here rather than silently accepted.
- **`purged_row_count["train"]=45157`, `embargoed_row_count["train"]=0`.** `train`'s only "other" purge/embargo source is `val` (the zero-width `held_out` sentinel's purge/embargo band sits far past `train`'s own end and never intersects it); `val` immediately follows `train` with no gap, so every excluded row near the boundary falls inside the 600s PURGE band (train's trailing edge), and none falls in the 1s FOLD_EMBARGO_NS band strictly after `val`'s end (there is no train entry after `val` to embargo against). This is expected, not a sign the embargo logic is inert -- P1/P2's own invariant tests (shrinking `FOLD_EMBARGO_NS`/purge to 0 on a fixture with comparable-scale blocks) already prove the exclusion logic does real work; this manifest's particular geometry (train immediately followed by val, nothing after val but a zero-width sentinel) just happens to produce an embargo count of 0.

## User Setup Required

None - no external service configuration required. The real MLflow tracking root (`/Volumes/ProjectsSSD/aihedgefund/mlflow`) and real registry root already existed from earlier phases; this plan's issuance touched neither's structure, only added content.

## D-05-NN Decision / EVAL-0x Requirement Traceability

Grepped across all of `05-00-SUMMARY.md` through this plan's own content; every decision is traceable to a committed artifact or test, though not every one is cited by its literal `D-05-NN` string in an earlier SUMMARY (noted where so).

| ID | Proof | Where |
|----|-------|-------|
| D-05-01 (int64 ns boundaries) | Every segment entry (`start_ns`/`end_ns`) across `harness/segments.py`, the real manifest | `harness/segments.py`, `data/lake_registry/segments/97964cb2....json` |
| D-05-02 (declared-then-validated boundaries) | `_validate_segments`: overlap/order/coverage/held_out-exemption/starvation refusals | `harness/segments.py`, `tests/harness/test_segments.py` (05-01/02-SUMMARY) |
| D-05-03 (rows selected by time) | `materialize`'s `[start_ns, end_ns)` half-open filter | `harness/accessor.py::test_materialize_filters_by_half_open_time_window` |
| D-05-04 (purge/embargo, two mechanisms) | `effective_train_intervals`/`filter_train_rows`, `PURGE_HORIZON_NS`/`FOLD_EMBARGO_NS` | `harness/purge_embargo.py` (05-01-SUMMARY) |
| D-05-05 (catalogue embargo is a label invariant) | `FOLD_EMBARGO_NS != any catalogue embargo`; `tests/leakage/test_embargo.py` (pre-existing, pinned) | `harness/purge_embargo.py`, `mvp/spec.md` "Fold harness" |
| D-05-06 (compressed_3seg default, reason logged) | `fold_config_reason` required field, real manifest's `layout="compressed_3seg"` + stated reason | THIS PLAN: `harness/segments.py`, `data/lake_registry/segments/97964cb2....json` |
| D-05-07 (sibling registry, same content-addressing) | `REGISTRY_DIR_NAMES`, the two real manifests, no `partitions` key in either | THIS PLAN: `tools/check_manifest_append_only.py`, `tools/check_manifest_id_integrity.py`, both real manifests |
| D-05-08 (one manifest per layout, segments named inside) | `COMPRESSED_3SEG_NAMES`, `oof_block_0..4` entries inside the one real manifest | `harness/segments.py`, real manifest's `segments` list |
| D-05-09 (what a segment manifest records) | Every field present and real in the committed manifest (effective_intervals, purge_ns, embargo_ns, purged/embargoed_row_count, admission incl. counts, errata_id, budget_allowance, symbol, version, code_hash) | THIS PLAN: `data/lake_registry/segments/97964cb2....json` |
| D-05-10 (held_out refused unconditionally) | `test_materialize_refuses_held_out_unconditionally` | `tests/harness/test_accessor.py` (05-01-SUMMARY) |
| D-05-11 (a look is materialization) | `_LOOK_ROLES = {"val", "oof_block"}` gate | `harness/accessor.py` (05-04-SUMMARY) |
| D-05-12 (MLflow durable counter, allowance in manifest) | `budget.look_count`/`record_look`, `manifest["budget_allowance"]` | `harness/budget.py` (05-01-SUMMARY) |
| D-05-13 (granularity: manifest_id, segment_name) | `look_count`'s two-tag `filter_string`, never `model_class` | `harness/budget.py` |
| D-05-14 (exhaustion is a hard refusal naming the remedy) | `test_look_count_reaching_allowance_refuses_the_next_look`, `test_issuance_self_discovers_existing_manifests_and_refuses_overlap` | `tests/harness/test_budget.py`, `tests/harness/test_segments.py` (05-03-SUMMARY) |
| D-05-15 (the guarantee, stated honestly) | `tools/check_harness_accessor_only.py`, its own docstring naming the bypass | `tools/check_harness_accessor_only.py` (05-06-SUMMARY); `mvp/spec.md` "Fold harness" |
| D-05-16 (held-out sits forward in time) | Declaration-tool design; THIS PLAN's real manifest uses the zero-width sentinel because no held-out day exists yet | `data/holdout.py` design (05-05-SUMMARY); real manifest's `held_out` entry |
| D-05-17 (Phase 5 builds the tool, does not declare) | `--dry-run` holdout declaration tool; no `holdout.json` written by this plan | 05-05-SUMMARY |
| D-05-18 (declaration moves D_lock AND D_lock-1) | `data.lockbox.quarantine_feature_partition`-shaped orchestration | 05-05-SUMMARY |
| D-05-19 (Plan 0: widen the pool first) | 7 built days (2026-09-12..18) | 05-00-SUMMARY |
| D-05-20 (errata list, not a rebuild) | The real errata manifest, 249 cells, cell-level set equality vs. evidence file | THIS PLAN: `data/lake_registry/errata/22190ad9....json`; `harness/errata.py` (05-04-SUMMARY) |
| D-05-21 (declared row-admission policy) | Real manifest's `admission` field, `STALE_BOOK_MAX_AGE_NS`, per-entry `counts` DERIVED at issuance | THIS PLAN: `harness/segments.py::_derive_admission_counts`, real manifest |
| D-05-22 (negative-result log, fingerprinted) | `harness/negative_log.py`, `compute_manifest_id` reuse | 05-03-SUMMARY; `mvp/spec.md` "Fold harness" |
| D-05-23 (44% zero-mass, handed to Phases 7-9) | Explicit citation in `mvp/spec.md` "Fold harness"'s closing paragraph; Phase 5 computes no metric anywhere | THIS PLAN: `mvp/spec.md` |

| Requirement | Proof |
|-------------|-------|
| EVAL-01 (5-segment split + embargo gaps; manifests as data) | `5seg` layout fully implemented and tested (`tests/harness/test_segments.py`), real, committed, resolvable manifest registry exists (this plan, `compressed_3seg`) |
| EVAL-02 (compressed 3-seg fallback, reason recorded in MLflow) | Real manifest `layout="compressed_3seg"`; `fold_config_reason` required field wired additively into every look's MLflow tags (this plan, `test_materialize_tags_fold_config_and_reason_on_every_look`) |
| EVAL-03 (budget tracked, looks counted, exhaustion forces fresh window) | `test_look_count_reaching_allowance_refuses_the_next_look` (budget.py), `test_issuance_self_discovers_existing_manifests_and_refuses_overlap` (issuance-time refusal, segments.py) |
| EVAL-04 (negative-result log) | `harness/negative_log.py`, query function + CLI (05-03-SUMMARY) |

## Self-Check Note

See the `## Self-Check` section appended below, generated after this SUMMARY was written, verifying every created file and commit hash named above actually exists.

## Next Phase Readiness

- The phase closes with a real, resolvable segment manifest and its errata companion on the real lake; every guardrail that must see them does (`check_manifest_append_only`, `check_manifest_id_integrity`), and the one that must not (`check_no_manifest_rewrite`) still doesn't.
- Phase 6+ can issue additional real segment manifests using the now-complete `issue_segment_manifest` (admission.counts, fold_config_reason both real and derived) without further gap-closing work in `harness/segments.py`.
- **Not done / explicitly out of scope for this plan, named per the plan's own boundaries:** no metric of any kind is computed anywhere in Phase 5 (D-05-23); the held-out window is not declared (D-05-17, Phase 8's job); no training run exists yet; the real manifest's `budget_allowance=5` is a placeholder pending real sizing once actual experiments run against it.

## Self-Check: PASSED

All 8 files named in this SUMMARY (2 created, 6 key modified) verified present via `[ -f ... ]`; all 4 commit hashes (`c284627`, `1dc7f06`, `255e19d`, `f80307b`) verified present via `git log --oneline --all`. No missing items.

---
*Phase: 05-fold-harness-overfitting-controls*
*Completed: 2026-09-22*
