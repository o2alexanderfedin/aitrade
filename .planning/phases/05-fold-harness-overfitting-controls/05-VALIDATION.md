---
phase: 5
slug: fold-harness-overfitting-controls
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-09-20
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from 05-RESEARCH.md "Validation Architecture"; the planner fills the per-task map.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + hypothesis 6.x (`testpaths = ["tests"]` in `mvp/pyproject.toml`) |
| **Config file** | `mvp/pyproject.toml` `[tool.pytest.ini_options]`; `mvp/tests/conftest.py` pins `NUMBA_CACHE_DIR` |
| **Quick run command** | `cd mvp && ./.venv/bin/pytest tests/harness -x -q` |
| **Full suite command** | `cd mvp && ./.venv/bin/pytest tests -x -q` (the pre-commit `pytest` hook runs this on every commit) |
| **Estimated runtime** | quick ~10 s; full suite ~4 min (953 tests today) |

---

## Sampling Rate

- **After every task commit:** the pre-commit hook already runs the FULL suite plus 17 guardrails — no commit lands red
- **After every plan wave:** `cd mvp && ./.venv/bin/pytest tests -x -q` plus `pre-commit run --all-files`
- **Before `/gsd-verify-work`:** full suite green, all hooks green, CI green on the pushed branch
- **Max feedback latency:** ~4 min (one commit)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| (filled by the planner per task) | | | EVAL-01..04 | | | | | | ⬜ pending |

Requirement → test map from research (the planner maps tasks onto these):

| Req | Behavior | Test | Command |
|---|---|---|---|
| EVAL-01 | 5-segment split with purge + embargo produces a content-addressed segment manifest downstream code resolves | unit + integration | `pytest tests/harness/test_segments.py -x -q` |
| EVAL-01 | Refusals: overlap, out-of-order, out-of-coverage, val-before-train | one test per refusal | `pytest tests/harness/test_segments.py -k refuse -x -q` |
| EVAL-02 | Compressed layout selectable; `fold_config` + reason land in MLflow | MLflow round-trip on tmp root | `pytest tests/harness/test_fold_config.py -x -q` |
| EVAL-02 | Purged + embargoed inner k-fold OOF: no train label window overlaps the OOF block; blocks partition Train; anti-vacuity (purge=0 makes an overlap appear) | property + fixture | `pytest tests/harness/test_kfold.py -x -q` |
| EVAL-03 | Every validation materialization increments the MLflow counter; exhaustion refuses and names the remedy; a manifest overlapping an exhausted window is refused at issuance | unit, tmp MLflow root | `pytest tests/harness/test_budget.py -x -q` |
| EVAL-03 | Static tripwire: a `load_features` caller outside `mvp/harness/` + sanctioned tests fails the scan; red-proof + anti-vacuity | scanner self-test | `pytest tests/tools/test_check_harness_accessor_only.py -x -q` |
| EVAL-04 | Negative result recorded with fingerprint; queryable; re-run warns not refuses | unit, tmp MLflow root | `pytest tests/harness/test_negative_log.py -x -q` |
| D-05-07 | `segments/` and `errata/` covered by `check_manifest_append_only` + `check_manifest_id_integrity`, NOT by `check_no_manifest_rewrite` | guardrail tests | `pytest tests/tools/test_check_manifest_append_only.py tests/tools/test_check_manifest_id_integrity.py -x -q` |
| D-05-17/18 | Declaration `--dry-run` lists exactly what would move and writes nothing; the real run moves `D_lock` and `D_lock−1` and writes `holdout.json` | unit, tmp lake + registry | `pytest tests/harness/test_holdout_declare.py -x -q` |
| D-05-20 | Errata list reproduces 180 + 69 cells from the real partitions; loader masks them | integration (real lake, read-only) + unit | `pytest tests/harness/test_errata.py -x -q` |
| D-05-21 | Stale-book age computed from the partition alone; 09-14 frozen-book count reproduces (29,058) | unit + real-lake check | `pytest tests/harness/test_admission.py -x -q` |

---

## Wave 0 Requirements

- [ ] `mvp/tests/harness/` (NO `__init__.py` — `mvp/harness/` will exist) — new test root
- [ ] `mvp/tests/harness/conftest.py` — tmp lake root, tmp registry root, tmp MLflow tracking root fixtures (pattern: `mvp/tests/fixtures/feature_tier.py`, `mvp/tests/tracking/test_mlflow_utils.py`)
- [ ] Synthetic feature-partition builder with enough SPAN for five segments + four gaps at h_max = 600 s (assert the span before asserting the split) and a second builder for the k-fold layout
- [ ] `mvp/tests/tools/test_check_harness_accessor_only.py` — scanner self-test
- [ ] `segments/` and `errata/` cases added to the two manifest guardrail tests

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Physical lockbox move under `chmod 0000` | D-05-18 | The barrier is lifted/re-applied by a human out-of-band by policy | `--dry-run` only in Phase 5; the real move is Phase 8's act |
| Plan 0 ingest of 2026-09-16..19 and feature build of 09-15..18 on the real lake | D-05-19 | Real data, write-once tiers, DQ verdicts need a human ack if `failed` | Run the documented CLIs; verify manifests resolve; report each day's DQ verdict |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 300s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
