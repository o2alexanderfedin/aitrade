---
phase: 2
slug: stage-0-living-spec-ci-guardrails-tracking
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-09-14
---

# Phase 2 — Validation Strategy

> Per-phase validation contract. Derived from `02-RESEARCH.md` §Validation Architecture.
> Phase 1 lesson carried in: tests must be hermetic — no dependence on host disk state,
> network, or wall-clock. Every new CI check is proven **red** by a deliberate violation
> before it counts.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x (already installed, Phase 1) |
| **Config file** | `mvp/pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `cd mvp && ./.venv/bin/python3 -m pytest tests/spec tests/tracking -x -q` |
| **Full suite command** | `cd mvp && ./.venv/bin/python3 -m pytest tests -x -q -W error::DeprecationWarning` |
| **Estimated runtime** | ~15 seconds (no network; MLflow smoke uses a `tmp_path` SQLite file) |

---

## Sampling Rate

- **After every task commit:** quick run command
- **After every plan wave:** full suite + `ruff check` + `ruff format --check`
- **Before `/gsd-verify-work`:** full suite green, every CI check observed red-then-green, and the GitHub Actions workflow green on a pushed commit
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 2-01-01 | 01 | 1 | SPEC-01 | — | N/A | unit | `pytest tests/spec/test_catalogue.py -x` | ❌ creates it | ⬜ pending |
| 2-01-02 | 01 | 1 | SPEC-01 | — | Definition change under existing name is rejected | unit | `pytest tests/spec/test_catalogue.py::test_definition_change_rejected -x` | ❌ creates it | ⬜ pending |
| 2-01-03 | 01 | 1 | SPEC-01, SPEC-03 | — | N/A | unit + diff | `pytest tests/spec/test_render.py -x` (spec.md ↔ TOML round-trip is byte-stable) | ❌ creates it | ⬜ pending |
| 2-02-01 | 02 | 2 | SPEC-02 | T-2-01 | Uncatalogued feature name in training code fails CI | unit (red fixture) | `pytest tests/spec/test_catalogue_completeness.py -x` | ❌ creates it | ⬜ pending |
| 2-02-02 | 02 | 2 | SPEC-02 | T-2-02 | `latest` path literal fails; prose mention passes | unit (red fixture) | `pytest tests/spec/test_latest_ban.py -x` | ❌ creates it | ⬜ pending |
| 2-02-03 | 02 | 2 | SPEC-04 | T-2-03 | `@njit` reading a mutable module global fails; UPPER_CASE Final passes | unit (red fixture) | `pytest tests/spec/test_numba_globals.py -x` | ❌ creates it | ⬜ pending |
| 2-02-04 | 02 | 2 | TRACK-02 | — | Lockfile drift and pin mismatch fail | unit + CI | `pytest tests/spec/test_pins.py -x` and `uv lock --check` | ❌ creates it | ⬜ pending |
| 2-03-01 | 03 | 2 | TRACK-01 | T-2-04 | MLflow root refused if cloud-synced/low-space (reuses `validate_data_root`) | unit | `pytest tests/tracking/test_mlflow_utils.py::test_root_guard -x` | ❌ creates it | ⬜ pending |
| 2-03-02 | 03 | 2 | TRACK-01 | T-2-05 | Run with a missing mandatory tag is rejected, not logged | unit | `pytest tests/tracking/test_mlflow_utils.py::test_missing_tag_rejected -x` | ❌ creates it | ⬜ pending |
| 2-03-03 | 03 | 2 | TRACK-01 | — | All 8 mandatory tags round-trip through SQLite, 64-hex values intact | integration (tmp SQLite) | `pytest tests/tracking/test_mlflow_utils.py::test_mandatory_tags_present -x` | ❌ creates it | ⬜ pending |
| 2-03-04 | 03 | 2 | TRACK-01 | T-2-06 | `import mlflow` does not import pandas | unit | `pytest tests/tracking/test_no_pandas_via_mlflow.py -x` | ❌ creates it | ⬜ pending |
| 2-04-01 | 04 | 3 | SPEC-02, TRACK-02 | — | Every check runs identically in pre-commit and Actions | infra | `pre-commit run --all-files` exit 0; `.github/workflows/ci.yml` present | ❌ creates it | ⬜ pending |
| 2-04-02 | 04 | 3 | SPEC-02 | — | Each check observed RED on a deliberate violation | manual-once, recorded | see Manual-Only table | — | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

No separate Wave 0 — pytest is installed; each plan creates its own test files. Test directories `mvp/tests/spec/` and `mvp/tests/tracking/` are created by Plans 01 and 03 respectively.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions | Gating |
|----------|-------------|------------|-------------------|--------|
| Each new CI check seen **red** once | SPEC-02, SPEC-04, TRACK-02 | A green check never seen red is not a guardrail (Phase 1 lesson: TID251 was proven this way) | For each check, commit a throwaway violation on a scratch branch, run pre-commit, observe failure, revert. Record the observed failure text in the plan's SUMMARY. | **Yes** — Plan 04 checkpoint |
| GitHub Actions workflow green on a real push | TRACK-02 | Requires the remote runner; cannot run locally | Push the branch; `gh run watch --exit-status`; record run URL | **Yes** — Plan 04 checkpoint |
| Decision-rule pseudocode correct in spec.md | SPEC-03 | Semantic review, not machine-checkable until Phase 6's oracle tests | Read the corrected block; confirm `pred_mid = mid*(1+pred)` vs `best_ask + X_price` | **Yes** — Plan 01 acceptance |

---

## Validation Sign-Off

- [x] All tasks have automated verify, a recorded manual check, or an explicit checkpoint
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] No MISSING references — every test file is created by the plan that needs it
- [x] No watch-mode flags
- [x] Feedback latency < 15s
- [x] `nyquist_compliant: true`

**Approval:** approved 2026-09-14
