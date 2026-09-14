---
phase: 02-stage-0-living-spec-ci-guardrails-tracking
plan: 04
subsystem: infra
tags: [pre-commit, github-actions, ci, setup-uv, red-proof]

# Dependency graph
requires:
  - phase: 02-stage-0-living-spec-ci-guardrails-tracking/02-01
    provides: mvp/tools/check_spec_diff.py, mvp/spec/{catalogue,render}.py
  - phase: 02-stage-0-living-spec-ci-guardrails-tracking/02-02
    provides: "mvp/tools/check_{catalogue_completeness,latest_ban,numba_globals,pin_versions,ms_to_ns_site}.py — the five guardrail scripts wired here, including check_pin_versions.py's --lock-path escape hatch used for scenario 8"
  - phase: 02-stage-0-living-spec-ci-guardrails-tracking/02-03
    provides: "mvp/tracking/ + tests/tracking/ — the tracking test suite added to the CI pytest invocation (smoke_run.py deliberately excluded)"
provides:
  - ".pre-commit-config.yaml — 10 repo-local hooks (ruff check/format, uv lock --check, 6 tools.check_* scripts, pytest over tests/spec+tracking+capture+leakage), installed as the active git hook"
  - ".github/workflows/ci.yml — single ubuntu-latest job, byte-identical run: strings to the pre-commit entry: strings, astral-sh/setup-uv@v10.1.0 pinned to uv 0.11.6, uv sync --locked --directory mvp"
  - "mvp/tests/leakage/ — one-test pytest-collected scaffold for Phase 4's per-feature shuffle-future leakage tests"
  - "Eight mechanically-observed red-then-green transcripts, one per guardrail, none paraphrased"
  - "A confirmed-red GitHub Actions run (ci-red-proof, branch deleted after) and a confirmed-green run (the real feature branch)"
affects: [04-feature-engine]

# Tech tracking
tech-stack:
  added: []  # astral-sh/setup-uv is a GitHub Action reference, not a project dependency
  patterns:
    - "repo: local pre-commit hooks calling `uv run --directory mvp ...` from the repo root, never `cd mvp &&` — the identical string is reused verbatim as a GitHub Actions `run:` step with no working-directory override, mechanically guaranteeing pre-commit and CI run the same command"
    - "Deliberately no astral ruff pre-commit integration hook — a second independently-pinned ruff version is a version-drift class this project avoids entirely by always resolving ruff from mvp/uv.lock"
    - "Red-proof discipline: every guardrail is proven red via `pre-commit run <hook-id> --all-files` in isolation (never the full suite, which could let an unrelated hook's `uv run` auto-repair the very drift under test), immediately reverted, then re-confirmed green with the same isolated hook invocation"
    - "check_pin_versions.py's red-proof runs the venv interpreter directly (`./.venv/bin/python3 -m tools.check_pin_versions --lock-path <scratch>`), never `uv run`, per 02-02-SUMMARY.md's explicit warning about `uv run`'s implicit re-sync racing the live capture daemon's shared venv"

key-files:
  created:
    - .pre-commit-config.yaml
    - .github/workflows/ci.yml
    - mvp/tests/leakage/__init__.py
    - mvp/tests/leakage/test_leakage_scaffold.py
  modified: []

key-decisions:
  - "Comments in .pre-commit-config.yaml/.github/workflows/ci.yml explaining the design choices were reworded to avoid containing the literal substrings the acceptance-criteria greps test for (`working-directory:`, `astral-sh/ruff-pre-commit`) — the rationale is still fully documented, just not in a way that produces a grep false-positive against the file's own explanatory prose."
  - "Scenario 8's tampered lockfile copy lives in the session scratchpad directory, not /tmp — the plan's illustrative path was /tmp/scratch-uv.lock, but this harness's own working-directory rules mandate the scratchpad for temporary files; the substance (a copy never touching the real mvp/uv.lock, read via --lock-path, never through `uv run`) is unchanged."
  - "The ci-red-proof commit was made with `git commit --no-verify` after first attempting a normal `git commit` and capturing its rejection (ruff-check hook fired on the deliberate `import pandas` fixture) as bonus evidence that the installed git hook actually gates real commits, not just `pre-commit run --all-files` invocations."

patterns-established:
  - "Every guardrail's red-proof fixture lives under mvp/data/ (never mvp/tests/, which several check scripts explicitly exclude by path) and is deleted immediately after the failure text is captured — git status --short is empty between every scenario."

requirements-completed: [SPEC-02, TRACK-02]

# Metrics
duration: ~18min
completed: 2026-09-14
---

# Phase 2 Plan 04: Pre-commit + GitHub Actions Guardrail Wiring Summary

**Wired all six Plan 02 check scripts plus ruff/lockfile checks into `.pre-commit-config.yaml` and a byte-identical `.github/workflows/ci.yml`, mechanically proved all eight guardrails red-then-green (including a real GitHub Actions run observed both failing and succeeding), and scaffolded `mvp/tests/leakage/` for Phase 4.**

## Performance

- **Duration:** ~18 min (commit span 19:30:27 PDT wiring commit through GitHub Actions confirmation)
- **Tasks:** 2 automated tasks + 1 pending human-verify checkpoint (this SUMMARY documents evidence for the checkpoint; it does not self-approve it)
- **Files modified:** 4 new files (.pre-commit-config.yaml, .github/workflows/ci.yml, mvp/tests/leakage/__init__.py, mvp/tests/leakage/test_leakage_scaffold.py)

## Accomplishments

- `.pre-commit-config.yaml`: 10 `repo: local` hooks — `ruff-check`, `ruff-format`, `lockfile-check` (`uv --directory mvp lock --check`), `pin-assertion`, `ms-to-ns-site`, `catalogue-completeness`, `latest-ban`, `numba-globals`, `spec-diff` (all six `tools.check_*` scripts), and a final `pytest` hook covering `tests/spec tests/tracking tests/capture tests/leakage`. Installed as the active git hook via `pre-commit install` (`.git/hooks/pre-commit` confirmed present and pre-commit-managed).
- `.github/workflows/ci.yml`: single `ubuntu-latest` job, `permissions: contents: read`, `actions/checkout@v4` with `fetch-depth: 0` (needed for `check_spec_diff`'s `HEAD~1` resolution), `astral-sh/setup-uv@v10.1.0` pinned to `version: "0.11.6"` (matching the locally-installed uv that produced `uv.lock`'s `revision = 3`), `uv sync --locked --directory mvp`, then one `run:` step per pre-commit hook using the **exact same command string** — no `working-directory:` override anywhere.
- `mvp/tests/leakage/{__init__.py,test_leakage_scaffold.py}`: one trivially-true test (`test_leakage_directory_is_collected`), no `__init__.py` collision risk (no top-level `leakage` package exists), collected and green.
- All grep-based acceptance criteria from the plan verified directly (6-check count match in both files, zero `working-directory:` matches, zero `astral-sh/ruff-pre-commit` matches, `permissions:`/`contents: read` present, `setup-uv@v10.1.0`/`version: "0.11.6"` present, `uv sync --locked` present and `--frozen` absent, `fetch-depth: 0` present, `smoke_run` absent from both CI files, `tests/leakage` present in both pytest invocations).
- `pre-commit run --all-files` on the clean, violation-free tree: **10/10 hooks green**, including 147/147 tests (`tests/spec` + `tests/tracking` + `tests/capture` + `tests/leakage`, up from 146 before this plan's scaffold).

## Eight Red-Proof Transcripts (Task 2)

Every scenario was triggered by running **only the single corresponding hook** (`pre-commit run <id> --all-files`), never the full suite, then immediately reverted and re-confirmed green with the same isolated hook. `git status --short` was empty between every scenario. The capture daemon (PID 10771) was confirmed alive before, during, and after every scenario.

**1. ruff-check — `import pandas` fixture (`mvp/tests/fixtures/_red_proof_pandas.py`)**
```
TID251 `pandas` is banned: pandas is banned project-wide per CLAUDE.md; use polars
 --> tests/fixtures/_red_proof_pandas.py:1:8
F401 [*] `pandas` imported but unused
Found 2 errors.
exit=1
```
Reverted → `pre-commit run ruff-check --all-files` → Passed.

**2. spec-diff — one-word edit inside `mvp/spec.md`'s features marker block** (`separately` → `DESYNCED` in the `mid` row's Notes cell, desyncing it from `features.toml`)
```
FAIL: mvp/spec.md's catalogue tables drifted from the TOML source:
--- spec.md (committed)
+++ spec.md (re-rendered from TOML)
-| `mid` | ... | Treat sub-tick stickiness flag DESYNCED |
+| `mid` | ... | Treat sub-tick stickiness flag separately |
exit=1
```
Reverted (`git checkout -- mvp/spec.md`) → `pre-commit run spec-diff --all-files` → Passed.

**3. catalogue-completeness — `mvp/data/_red_proof_catalogue.py`** calling `get_feature("this_name_does_not_exist")`
```
scanned 14 files, 1 call sites
FAIL: uncatalogued or non-literal feature/label name(s):
  data/_red_proof_catalogue.py:3: uncatalogued name 'this_name_does_not_exist'
exit=1
```
Deleted → `pre-commit run catalogue-completeness --all-files` → Passed.

**4. latest-ban — `mvp/data/_red_proof_latest.py`** with `pathlib.Path("backfill/latest/file.parquet")`
```
scanned 14 files
FAIL: literal 'latest' path segment(s) found:
  data/_red_proof_latest.py:3: literal 'latest' path segment: 'backfill/latest/file.parquet'
exit=1
```
Deleted → `pre-commit run latest-ban --all-files` → Passed.

**5. numba-globals — `mvp/data/_red_proof_numba.py`** (module-level `bad_state = 0`, `@numba.njit def kernel(x): return x + bad_state`)
```
scanned 29 files, 1 njit functions
FAIL: @njit function(s) reading a module-level non-constant global:
  data/_red_proof_numba.py:8: module-level global 'bad_state' read inside @njit function 'kernel'
exit=1
```
Deleted → `pre-commit run numba-globals --all-files` → Passed.

**6. ms-to-ns-site — `mvp/data/_red_proof_ms_to_ns.py`** with a second `x = ms * 1_000_000` line
```
FAIL: expected exactly one ms-to-ns site at 'data/capture/parse.py', found 2:
  data/_red_proof_ms_to_ns.py:2
  data/capture/parse.py:31
exit=1
```
Deleted → `pre-commit run ms-to-ns-site --all-files` → Passed.

**7. lockfile-check — one-line uncommitted addition to `mvp/pyproject.toml`'s dependency list** (`"tomli>=2.0",`), run in isolation, then immediately reverted before any other hook or `uv` command ran
```
Resolved 66 packages in 387ms
The lockfile at `uv.lock` needs to be updated, but `--check` was provided. To update the lockfile, run `uv lock`.
exit=1
```
Immediately `git checkout -- mvp/pyproject.toml` → `git diff --stat mvp/uv.lock` empty (real lockfile never touched) → `ps -p 10771` alive → `pre-commit run lockfile-check --all-files` → Passed.

**8. pin-assertion — scratch copy of `mvp/uv.lock`, `numba` `version` line hand-edited from `"0.65.1"` to `"0.64.0"`**, invoked directly against the venv interpreter (never `uv run`, never the real lockfile) per 02-02-SUMMARY.md's explicit guidance:
```bash
./.venv/bin/python3 -m tools.check_pin_versions --lock-path <scratch>/scratch-uv.lock
```
```
FAIL: numba version '0.64.0' does not match pinned prefix '0.65'.*
exit=1
```
Scratch file deleted → `pre-commit run pin-assertion --all-files` → Passed. `mvp/uv.lock` was never opened for writing at any point in this scenario.

## GitHub Actions: Confirmed Red, Then Confirmed Green

**Real branch, green run** (pushed after Task 1's commit, no violations present):
- Commit: `08ebf9d3f316d27bccbc3faad00b0ed8d611c3ef`
- Run: **https://github.com/o2alexanderfedin/aitrade/actions/runs/34799740762**
- Conclusion: `success`, 37s total, all 15 steps (checkout, setup-uv, uv sync, ruff check, ruff format, uv lock --check, all 6 `tools.check_*`, pytest, both post-steps) green.
- Confirmed via `gh run list --branch feature/phase-02-stage-0-living-spec-ci-guardrails-tracking --limit 1 --json conclusion` → `"success"` (re-checked after the ci-red-proof branch was deleted, to rule out any residual effect).

**`ci-red-proof` scratch branch, red run** (deliberate `import pandas` fixture, pushed specifically to observe the *runner's* wiring fail, not just local pre-commit):
- A plain `git commit` (hooks active) was attempted first and **rejected locally** by the `ruff-check` hook — captured as bonus evidence the installed git hook gates real commits, not only `pre-commit run --all-files`. The commit was then made with `--no-verify` (documented here as the one sanctioned use, per the plan's own T-2-14 threat-register disposition) so the violation could reach GitHub.
- Commit: `d6b77222a77f2392a14d2219de921c6b46db88ed`
- Run: **https://github.com/o2alexanderfedin/aitrade/actions/runs/34799815465**
- Conclusion: `failure`, failed at the `ruff check` step (14s to failure), exactly the expected failure point.
- Cleanup: `git checkout feature/... && git push origin --delete ci-red-proof && git branch -D ci-red-proof`. Confirmed absent: `git branch -a | grep ci-red-proof` → no match; `gh api repos/o2alexanderfedin/aitrade/branches/ci-red-proof` → `404 Branch not found`. The `mvp/tests/fixtures/_red_proof_pandas.py` fixture does not exist on the feature branch.

## Task Commits

1. **Task 1: Write .pre-commit-config.yaml and .github/workflows/ci.yml** - `08ebf9d` (feat)
2. **Task 2: Prove every check red, then push and confirm GitHub Actions** - no permanent files (scratch violations created and reverted per the plan's own file list for this task); evidence recorded above. `git status --short` is empty.

**Plan metadata:** (this commit, SUMMARY.md + STATE.md + ROADMAP.md)

## Files Created/Modified

- `.pre-commit-config.yaml` - 10 repo-local hooks, installed as the active git hook
- `.github/workflows/ci.yml` - single ubuntu-latest job mirroring pre-commit byte-for-byte
- `mvp/tests/leakage/__init__.py` - empty package marker
- `mvp/tests/leakage/test_leakage_scaffold.py` - one-test placeholder scaffold for Phase 4

## Decisions Made

See `key-decisions` in frontmatter: (1) explanatory comments reworded to avoid tripping the acceptance-criteria greps on their own prose; (2) scenario 8's scratch lockfile placed under the session scratchpad, not `/tmp`, to comply with this harness's working-directory rules while preserving the plan's substantive intent; (3) the `ci-red-proof` commit used `--no-verify` after first capturing the local hook's rejection as evidence, exactly as the plan's T-2-14 threat-register entry anticipates.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Explanatory comments in .pre-commit-config.yaml/.github/workflows/ci.yml initially self-tripped their own acceptance-criteria greps**
- **Found during:** Task 1, running the plan's own acceptance-criteria grep commands after first draft
- **Issue:** The plan's acceptance criteria assert `grep -c "working-directory:" .github/workflows/ci.yml` equals 0 and `grep -c "astral-sh/ruff-pre-commit" .pre-commit-config.yaml` equals 0. The first-draft explanatory comments in both files quoted these exact strings (in backticks, as documentation) to explain *why* they weren't used — which is itself a literal match for the naive grep, producing counts of 1 instead of 0.
- **Fix:** Reworded both comment blocks to convey the identical rationale without embedding the literal grepped substrings (e.g. "no per-step working directory override key" instead of quoting `working-directory:`; "the official astral ruff pre-commit integration hook" instead of the exact `astral-sh/ruff-pre-commit` string).
- **Files modified:** `.pre-commit-config.yaml`, `.github/workflows/ci.yml`
- **Verification:** Both greps re-run, now return 0 as required; `pre-commit run --all-files` still 10/10 green afterward.
- **Committed in:** `08ebf9d` (Task 1, same commit — caught before commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 — a documentation-comment wording bug that would have failed the plan's own stated acceptance criteria). No scope creep; no architectural change.

## Issues Encountered

None beyond the one deviation above. All eight red-proof scenarios behaved exactly as the plan and the 02-02 SUMMARY's guidance predicted, including the pin-assertion scenario's explicit avoidance of `uv run` and the lockfile-check scenario's immediate-revert discipline. GitHub Actions surfaced one unrelated informational annotation (`Node.js 20 is deprecated ... actions/checkout@v4 ... forced to run on Node.js 24`) on both runs — a GitHub-runner-side deprecation notice about the `actions/checkout@v4` action's own Node runtime, not a failure, not caused by anything in this plan's wiring, and out of this plan's scope to fix (would require bumping `actions/checkout` to a newer major version, a separate decision).

## User Setup Required

None — no external service configuration required. `gh` was already authenticated against `o2alexanderfedin/aitrade`.

## CHECKPOINT EVIDENCE — awaiting human confirmation

Per this plan's Task 3 (`checkpoint:human-verify`, `gate="blocking"`), the evidence above is gathered and presented for review; **it is not self-approved**. Mapping onto ROADMAP.md Phase 2's five success criteria:

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | spec.md exists in seed form under mvp/ with feature/label catalogues in machine-readable format CI parses | Delivered in Plan 01 | `mvp/spec.md` lines 93 (`## Feature catalogue`) and 112 (`## Label catalogue`); `mvp/spec/{features,labels}.toml`; this plan's `check_spec_diff` hook (scenario 2 above) mechanically proves CI parses and enforces it |
| 2 | CI fails any training code using a feature/label without a catalogue entry, any `import pandas`, and any `latest` data reference | Delivered by this plan | Scenarios 1, 3, 4 above — all three mechanically observed red-then-green, wired identically into `.pre-commit-config.yaml` and `.github/workflows/ci.yml` |
| 3 | spec.md contains the corrected decision-rule pseudocode, spot-L1 clock exception, trades-backfill side-exactness note | Delivered in Plan 01 (unchanged by this plan) | `mvp/spec.md` line 165 (`## Decision rule (Stage 2)`), line 72 (`### Spot-L1 clock exception`), line 82 (`### Trades-backfill side-exactness`) — see `02-01-SUMMARY.md` |
| 4 | Sharpe annualization convention, MLflow tag schema, numba no-globals lint rule pre-declared in spec.md before any run exists | Delivered in Plan 01 (spec text) + this plan (numba-globals CI enforcement, scenario 5) | `mvp/spec.md` line 317 (`## Sharpe annualization convention`), line 334 (`## MLflow tag schema`), line 351 (`## Numba no-globals rule`); scenario 5 above proves the lint rule is CI-enforced |
| 5 | MLflow on SQLite records code/data/seed/env hash for a test run; environment pins are CI-enforced | Smoke run delivered in Plan 03; pin enforcement delivered by this plan | Real smoke-run evidence in `02-03-SUMMARY.md` ("Real Smoke Run Evidence" section, run_id `234d6b58621f435680b6201deb50ba0e`); scenarios 7 (lockfile-check) and 8 (pin-assertion) above mechanically prove CI enforcement of both `uv.lock` staleness and numba/numpy/llvmlite pin drift |

**Phase-completion statement:** All four of Phase 2's plans (01–04) have now executed. Every guardrail this plan wires has been mechanically observed both red and green, and a real GitHub Actions run has been observed both failing (deliberate violation, `ci-red-proof`, deleted) and succeeding (real branch, current HEAD). This SUMMARY does **not** itself mark Phase 2 or this plan's Task 3 checkpoint as approved — that requires human review of the evidence above per the plan's `gate="blocking"` designation. STATE.md/ROADMAP.md are updated to reflect Plan 04's tasks 1–2 as executed and the checkpoint as pending.

## Next Phase Readiness

- The full guardrail set (ruff/pandas ban, catalogue completeness, latest-ban, numba-globals, lockfile check, pin assertion, spec-diff) is live in both local pre-commit and GitHub Actions for every subsequent phase's commits.
- `mvp/tests/leakage/` is ready for Phase 4 to fill with real per-feature shuffle-future leakage tests — already wired into both CI callers' pytest invocation, so Phase 4 adds tests to an already-exercised directory rather than bolting on new CI wiring.
- **Blocker:** Task 3 (human-verify checkpoint) is pending. Phase 2 should not be marked fully complete in ROADMAP.md until a human reviews this evidence and responds "approved" (or requests changes) per the plan's `resume-signal`.
- No other blockers. The capture daemon (Run G, PID 10771) was confirmed alive via `ps -p 10771` before, during, and after every operation in this plan — no `uv sync`/`uv add`/`uv lock` mutation was ever run against the real lockfile, and nothing under `/Volumes/ProjectsSSD/aihedgefund/capture` was touched.

---
*Phase: 02-stage-0-living-spec-ci-guardrails-tracking*
*Completed: 2026-09-14*

## Self-Check: PASSED

All 4 files claimed as created verified present on disk (`.pre-commit-config.yaml`,
`.github/workflows/ci.yml`, `mvp/tests/leakage/__init__.py`,
`mvp/tests/leakage/test_leakage_scaffold.py`). Commit hash `08ebf9d` verified
present in git history (`git log --oneline --all | grep 08ebf9d`). GitHub Actions
run URLs verified live via `gh run view` at write time (success:
34799740762, failure: 34799815465, since deleted branch but run record persists
per GitHub's retention). `ci-red-proof` branch verified absent both locally
(`git branch -a`) and remotely (`gh api .../branches/ci-red-proof` → 404). No
missing items.

---

## Checkpoint resolution — 2026-09-14 02:55 UTC

**Status: APPROVED — Phase 2 complete.** All five ROADMAP criteria verified by the orchestrator independently: Actions green run 34799740762 (17/17), red run 34799815465 (failed at `ruff check` on deliberate `import pandas`), scratch branch deleted; `pre-commit run --all-files` 10/10; ten check commands byte-identical across pre-commit and `ci.yml` (Actions adds only the `uv sync --locked` setup step); `permissions: contents: read`; 147 tests; three guardrails additionally driven red by the orchestrator on scratch violations; `mlflow-skinny` with pandas absent from the venv; daemon Run G alive throughout. User chose to end the autonomous run after Phase 2's verify/review/merge — Phase 3 starts in a fresh session.
