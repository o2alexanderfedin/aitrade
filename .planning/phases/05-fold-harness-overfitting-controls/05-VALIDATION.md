---
phase: 5
slug: fold-harness-overfitting-controls
status: approved
nyquist_compliant: true
wave_0_complete: false
created: 2026-09-20
revised: 2026-09-20 (checker iteration 1 blocker 7 -- per-task map populated, sign-off completed)
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from 05-RESEARCH.md "Validation Architecture"; populated per task after plan-checker
> iteration 1 (blocker 7).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + hypothesis 6.x (`testpaths = ["tests"]` in `mvp/pyproject.toml`) |
| **Config file** | `mvp/pyproject.toml` `[tool.pytest.ini_options]`; `mvp/tests/conftest.py` pins `NUMBA_CACHE_DIR` |
| **Quick run command** | `cd mvp && ./.venv/bin/pytest tests/harness -x -q` |
| **Full suite command** | `cd mvp && ./.venv/bin/pytest tests -x -q` (the pre-commit `pytest` hook runs this on every commit) |
| **Estimated runtime** | quick ~10 s; full suite ~4 min (953+ tests as of Phase 4's close, growing through this phase) |

---

## Sampling Rate

- **After every task commit:** the pre-commit hook already runs the FULL suite plus 18-19 guardrails -- no commit lands red
- **After every plan wave:** `cd mvp && ./.venv/bin/pytest tests -x -q` plus `pre-commit run --all-files`
- **Before `/gsd-verify-work`:** full suite green, all hooks green, CI green on the pushed branch
- **Max feedback latency:** ~4 min (one commit)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---|---|---|---|---|---|---|---|---|---|
| 05-00-T1 | P0 | 1 | EVAL-01 | T-05-01, T-05-02 | Committed DQ acknowledgement, byte-identical to its git blob, reason citing measured numbers | integration, real lake | `cd mvp && ./.venv/bin/python3 -c "... assert 8/8 new curated by-date pointers present"` | ❌ Wave 0 | ⬜ pending |
| 05-00-T2 | P0 | 1 | EVAL-01 | T-05-03 | Seven features manifests resolve; row counts and 09-19 gap picture reported | integration, real lake | `cd mvp && ./.venv/bin/python3 -c "... assert 7/7 features manifests resolve"` | ❌ Wave 0 | ⬜ pending |
| 05-01-T1 | P1 | 1 | EVAL-01, EVAL-02, EVAL-03 | T-05-04 | `PURGE_HORIZON_NS`/`FOLD_EMBARGO_NS` derived not hardcoded; `effective_train_intervals` two-sided-purge math; 5seg manifest schema complete | unit, tmp_path | `pytest tests/harness/test_purge_embargo.py tests/harness/test_segments.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 05-01-T2 | P1 | 1 | EVAL-01, EVAL-02, EVAL-03 | T-05-05, T-05-06, T-05-20 | held_out refused unconditionally; real purge/embargo row exclusion; look counted for val/oof_block only; walking-skeleton end to end | unit + integration, tmp_path MLflow | `pytest tests/harness/test_accessor.py tests/harness/test_budget.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 05-02-T1 | P2 | 2 | EVAL-01, EVAL-02 | T-05-07 | D-05-02 geometric refusals; held_out coverage exemption; starvation refusal; derived purge/embargo fields with real row counts | unit, tmp_path (real partitions via write_feature_partition) | `pytest tests/harness/test_segments.py -k "refuse or starved or records" -x -q` | ❌ Wave 0 | ⬜ pending |
| 05-02-T2 | P2 | 2 | EVAL-01, EVAL-02 | T-05-08 | kfold two-sided-purge/one-sided-embargo formula; anti-vacuity on both purge=0 and embargo=0; oof_block materialization counts as a look end to end | property + fixture + integration | `pytest tests/harness/test_kfold.py "tests/harness/test_segments.py::test_compressed_3seg_issues_with_oof_blocks" "tests/harness/test_accessor.py::test_materializing_an_oof_block_counts_as_a_look" -x -q` | ❌ Wave 0 | ⬜ pending |
| 05-03-T1 | P3 | 3 | EVAL-02, EVAL-03, EVAL-04 | T-05-16 (removed), T-05-21 | Budget exhaustion hard refusal naming remedy; issuance-time overlap refusal UNCONDITIONAL (tracking_root required, self-discovery, no opt-out) | unit, tmp_path MLflow | `pytest tests/harness/test_budget.py tests/harness/test_segments.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 05-03-T2 | P3 | 3 | EVAL-02, EVAL-03, EVAL-04 | T-05-17 | Negative result fingerprinted via `compute_manifest_id`; queryable; re-run warns not refuses | unit, tmp_path MLflow | `pytest tests/harness/test_negative_log.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 05-04-T1 | P4 | 2 | EVAL-01 | T-05-10 | `STALE_BOOK_MAX_AGE_NS` decided constant (5s); undefined-age sentinel excluded, never zero; 29,058-row real-lake reproduction | unit + real-lake script | `pytest tests/harness/test_admission.py -x -q` (+ standalone real-lake script, transcript in SUMMARY) | ❌ Wave 0 | ⬜ pending |
| 05-04-T2 | P4 | 2 | EVAL-01 | T-05-09 | Errata 249-cell reproduction via `features.api.for_build`; accessor gate order admission→errata→budget; no write to real lake during recompute | unit + real-lake script | `pytest tests/harness/test_errata.py tests/harness/test_accessor.py -x -q` (+ standalone real-lake script) | ❌ Wave 0 | ⬜ pending |
| 05-05-T1 | P5 | 2 | EVAL-01 | T-05-11, T-05-13 | `quarantine_feature_partition` never calls chmod (red-proof); genuine move not copy; refuses an unbuilt date | unit, tmp_path | `pytest tests/lockbox/test_quarantine_feature_partition.py -x -q && ./.venv/bin/python3 -m tools.check_lockbox_containment` | ❌ Wave 0 | ⬜ pending |
| 05-05-T2 | P5 | 2 | EVAL-01 | T-05-12 | `dry_run` needs no write access; `declare` refuses re-declaration; move-before-declare ordering; never calls chmod | unit, tmp_path + standalone real-lake script (`mvp/scripts/holdout_declare_dry_run_real_lake.py`, warning 1) | `pytest tests/harness/test_holdout_declare.py -x -q` (+ standalone script, transcript in SUMMARY) | ❌ Wave 0 | ⬜ pending |
| 05-06-T1 | P6 | 2 | EVAL-03 | T-05-14, T-05-15 | Watches dotted `features.tier.load_features`, not the colliding bare name; `ast.Attribute` alias walk; three real sanctioned test files enumerated, not wildcarded; 19th hook byte-identical | unit (scanner self-test) + parity | `pytest tests/tools/test_check_harness_accessor_only.py tests/tools/test_ci_pre_commit_parity.py -x -q && ./.venv/bin/python3 -m tools.check_harness_accessor_only` | ❌ Wave 0 | ⬜ pending |
| 05-07-T1 | P7 | 4 | EVAL-01, EVAL-02, EVAL-04 | — | `fold_config_reason` required on issuance; tagged additively on every look | unit, tmp_path | `pytest tests/harness/test_segments.py tests/harness/test_accessor.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 05-07-T2 | P7 | 4 | EVAL-01, EVAL-02, EVAL-04 | T-05-18, T-05-19 | `segments/`/`errata/` guardrail extension + first real committed manifests in ONE commit; vacuity mutation observed red-then-green | guardrail unit + real-lake integration | `./.venv/bin/python3 -m tools.check_manifest_append_only && ./.venv/bin/python3 -m tools.check_manifest_id_integrity && pytest tests/tools/test_check_manifest_append_only.py tests/tools/test_check_manifest_id_integrity.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 05-07-T3 | P7 | 4 | EVAL-01, EVAL-02, EVAL-04 | — | `## Fold harness` spec.md section; D-05-23 recorded as handed forward; full phase-level suite + hook + traceability closure | integration + doc | `grep -n "^## Fold harness" spec.md && ./.venv/bin/python3 -m tools.check_spec_diff && pytest tests -x -q` | ❌ Wave 0 | ⬜ pending |

Requirement → test map (unchanged from research, retained for cross-reference):

| Req | Behavior | Test | Command |
|---|---|---|---|
| EVAL-01 | 5-segment split with purge + embargo produces a content-addressed segment manifest downstream code resolves | unit + integration | `pytest tests/harness/test_segments.py -x -q` |
| EVAL-01 | Refusals: overlap, out-of-order, out-of-coverage, val-before-train, starved | one test per refusal | `pytest tests/harness/test_segments.py -k refuse -x -q` |
| EVAL-02 | Compressed layout selectable; `fold_config` + reason land in MLflow | MLflow round-trip on tmp root | `pytest tests/harness/test_segments.py tests/harness/test_accessor.py -x -q` |
| EVAL-02 | Purged + embargoed inner k-fold OOF: no label-window overlap; blocks partition Train; anti-vacuity on purge AND embargo | property + fixture | `pytest tests/harness/test_kfold.py -x -q` |
| EVAL-03 | Every validation/oof_block materialization increments the MLflow budget counter; exhaustion refuses unconditionally and names the remedy; overlap refused at issuance | unit, tmp MLflow root | `pytest tests/harness/test_budget.py -x -q` |
| EVAL-03 | Static tripwire: a `load_features` caller outside `mvp/harness/` + 3 sanctioned tests fails the scan | unit (scanner self-test) | `pytest tests/tools/test_check_harness_accessor_only.py -x -q` |
| EVAL-04 | Negative result recorded with fingerprint; queryable; re-run warns not refuses | unit, tmp MLflow root | `pytest tests/harness/test_negative_log.py -x -q` |
| D-05-07 | `segments/` and `errata/` covered by `check_manifest_append_only` + `check_manifest_id_integrity`, NOT `check_no_manifest_rewrite` | guardrail tests | `pytest tests/tools/test_check_manifest_append_only.py tests/tools/test_check_manifest_id_integrity.py -x -q` |
| D-05-17/18 | Declaration `--dry-run` lists exactly what would move and writes nothing (pytest, tmp_path); real-lake safety proof is a standalone script (warning 1) | unit + script | `pytest tests/harness/test_holdout_declare.py -x -q` |
| D-05-20 | Errata list reproduces 180 + 69 cells from the real partitions; loader masks them | integration (real lake, read-only) + unit | `pytest tests/harness/test_errata.py -x -q` |
| D-05-21 | Stale-book age computed from the partition alone; decided threshold (5s); 09-14 frozen-book count reproduces (29,058) | unit + real-lake script | `pytest tests/harness/test_admission.py -x -q` |

---

## Wave 0 Requirements

- [x] `mvp/tests/harness/` directory (no `__init__.py`) — created by P1 Task 1
- [x] `mvp/tests/harness/conftest.py` — created by P1 Task 1
- [x] Synthetic feature-partition builder with enough SPAN for five segments + four gaps at h_max = 600 s — `mvp/tests/fixtures/harness_span.py`, created by P1 Task 1
- [x] A second, smaller-span builder for the k-fold layout — built inline in P2 Task 2's fixtures
- [x] `mvp/tests/tools/test_check_harness_accessor_only.py` — created by P6 Task 1
- [x] `segments/` and `errata/` cases added to the two manifest guardrail tests — P7 Task 2

All Wave 0 gaps are closed by named tasks in the plan set above; none remain unassigned.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Physical lockbox move under `chmod 0000` | D-05-18 | The barrier is lifted/re-applied by a human out-of-band by policy | `--dry-run` only in Phase 5 (P5); the real move is Phase 8's act |
| Plan 0 ingest of 2026-09-16..19 and feature build of 09-15..18 on the real lake | D-05-19 | Real data, write-once tiers, DQ verdicts need a human-legible report even though the ack itself is written autonomously | P0's two tasks are fully autonomous; each transcribes its DQ verdicts into the SUMMARY for post-hoc human review |
| The first real segment/errata manifest commit | D-05-07, D-05-09 | Write-once real registry artifact; the same-commit discipline is safety-critical | P7 Task 2 is autonomous but its mutation-check transcript (vacuity red-then-green) is the artifact a human reviews before merge |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify (every task above has one)
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 300s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-09-20 (planner)
