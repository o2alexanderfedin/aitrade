---
phase: 05-fold-harness-overfitting-controls
plan: 06
subsystem: guardrails
tags: [static-tripwire, ast-scan, pre-commit, ci, selection-bias-budget]

# Dependency graph
requires:
  - phase: 05-fold-harness-overfitting-controls
    provides: "05-01's harness/accessor.py (the ordered-gates materialize function and its runtime budget check-and-increment via harness/budget.py:record_look) and 05-02's harness/segments.py, harness/kfold.py, tests/harness/test_kfold.py"
  - phase: 04-feature-label-engine
    provides: "features/tier.py:load_features, the gated tier accessor this scanner watches"
provides:
  - "tools/check_harness_accessor_only.py -- an AST scan (scan_source, is_sanctioned, main) that flags any features.tier.load_features caller outside mvp/harness/ and four named test files; watches the dotted target, not the bare load_features name, so spec/catalogue.py's unrelated function of the same name is never flagged"
  - "the 19th guardrail hook, wired byte-identical between .pre-commit-config.yaml's entry: and .github/workflows/ci.yml's run:"
affects: [05-fold-harness-overfitting-controls/07]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "A scanner watching ONE function (not a whole module) needs a two-pass AST walk: pass 1 collects every local name an Import/ImportFrom node binds to the watched module (import X as t / from PKG import MOD [as t]); pass 2 walks ast.Attribute nodes whose .attr matches the watched name and resolves the value chain (Name/Attribute recursion) against both the literal watched-module spelling and the alias table from pass 1. This closes the module-alias gap check_single_feature_path.py's template left open (04-REVIEW-FIX precedent: a directory sanction around a package is protection pointing at the one place the risk already lives, not a promise nothing dynamic can reach it)."
    - "Mutating WATCHED_TARGET's dotted string to a bare name is not, by itself, a reliable red-proof if the match derives module/name constants from it ONCE at import time (a bare string with no '.' makes rsplit('.', 1) raise ValueError, crashing collection rather than producing the intended false-positive) -- the mutation that actually reproduces the historical bare-name-collision hazard is dropping the module-equality guard in the match condition, which the SUMMARY's mutation-check section transcribes separately from the plan-literal mutation."

key-files:
  created:
    - mvp/tools/check_harness_accessor_only.py
    - mvp/tests/tools/test_check_harness_accessor_only.py
  modified:
    - .pre-commit-config.yaml
    - .github/workflows/ci.yml

key-decisions:
  - "The sanctioned test list has FOUR entries, not the three 05-06-PLAN.md named. The plan's own constraint 3 grepped the repo before 05-01..05-05 landed and found three real importers of features.tier.load_features (test_holdout_refusal.py, test_loader_tier_containment.py, test_features_tier_containment.py). A grep taken at this plan's own execution time found a fourth, real, already-committed caller: tests/harness/test_kfold.py (added by 05-02-PLAN.md's Task 2, which builds a ground-truth feature frame via load_features to compare against harness.accessor.materialize's output for the compressed_3seg/kfold OOF-block tests). D-05-15's own text said four all along; the plan's three-file count was a stale snapshot, not a target. Adding the fourth by NAME (not sanctioning all of tests/harness/ as a directory) keeps the anti-vacuity property the plan's constraint 3 demands: a fifth, unlisted file is still flagged (test_sanctions_the_named_test_files_and_no_others proves it against a synthetic tests/harness/test_rogue.py)."
  - "test_sanctions_the_three_named_test_files_and_no_others, the exact test name 05-06-PLAN.md's <behavior> section specifies, was written as test_sanctions_the_named_test_files_and_no_others instead -- keeping 'three' in the name would misdescribe what the test now asserts (four sanctioned files, a fifth rejected)."
  - "WATCHED_MODULE and WATCHED_NAME are derived ONCE from WATCHED_TARGET via WATCHED_TARGET.rsplit('.', 1) at module-load time, then used as the sole comparison constants in the ImportFrom/Attribute match -- not re-parsed per node. This makes the match itself simpler (module-equality AND name-equality, not one concatenated string compare) but has a side effect documented under Deviations: the plan's literal mutation instruction (set WATCHED_TARGET to the bare string) crashes import rather than producing a false positive, so a second, more diagnostic mutation was also run and is transcribed below."
  - "An ast.Call handling for importlib.import_module/__import__ literal targets was cloned from check_single_feature_path.py's template for structural parity, even though no <behavior> test requires it and grep confirmed no real occurrence in the repo today -- it flags a literal dynamic import of the features.tier MODULE by name (not the function), the same partial coverage the template gives its own watched modules."

requirements-completed: []
requirements-partial: []

# Metrics
duration: ~55min
completed: 2026-09-22
---

# Phase 5 Plan 6: The D-05-15 Static Tripwire -- check_harness_accessor_only Summary

**A new pre-commit/CI check reads every `.py` file under `mvp/` and fails the build if anything outside `mvp/harness/` and four named test files imports or attribute-reaches `features.tier.load_features` -- the one tier function the harness's `materialize` accessor wraps to count a validation look before returning rows.** It is accident-proofing, not a security boundary: the scanner's own docstring says so, names the two ways a determined caller still gets through (a dynamically built import string; an alias copied through an intermediate variable it does not trace), and points at the runtime control (`harness.budget.record_look`, invoked from `harness.accessor.materialize`) that actually carries D-05-15's guarantee.

## Performance

- **Duration:** ~55 min (one task, one commit `a794504`)
- **Tasks:** 1/1 executed, committed, green
- **Files:** 2 created (`tools/check_harness_accessor_only.py`, its self-test), 2 modified (`.pre-commit-config.yaml`, `.github/workflows/ci.yml`)

## Accomplishments

- `tools/check_harness_accessor_only.py`: `WATCHED_TARGET = "features.tier.load_features"`, split once into `WATCHED_MODULE`/`WATCHED_NAME`. `SANCTIONED_FILES` = the directory `harness` plus four named test files (see key-decisions for the fourth). `scan_source` clones `check_single_feature_path.py`'s `ImportFrom`/`Call` shape for the direct-import and literal-dynamic-import cases, and adds an `ast.Attribute` walk (via `_collect_module_aliases` + `_dotted_value`) that resolves five real aliasing shapes to the same violation: `from features.tier import load_features` (single- and multi-line), `import features.tier as t; t.load_features(...)`, `from features import tier [as x]; tier.load_features(...)` / `x.load_features(...)`, and the fully-literal `import features.tier; features.tier.load_features(...)`.
- `test_a_real_lookalike_attribute_is_not_flagged` pins the real false-positive risk found while grepping the repo: `tests/harness/test_purge_embargo.py:26` calls `spec_catalogue.load_features()` (the catalogue loader, aliased via `from spec import catalogue as spec_catalogue`) -- the alias table never maps `spec_catalogue` to `features.tier`, so it is correctly silent.
- Scan is green over the real repo today: `scanned 172 python files under .../mvp`, exit 0.
- 19th hook wired: `.pre-commit-config.yaml`'s `entry:` and `.github/workflows/ci.yml`'s `run:` are byte-identical (`uv run --locked --directory mvp python -m tools.check_harness_accessor_only`), placed immediately after `check_manifest_append_only` in both files. `test_ci_pre_commit_parity.py` passes unmodified.
- `pre-commit run --all-files`: 18 of 19 hook ids ran (the 19th, `check-no-manifest-rewrite-full`, is `stages: [pre-push]` by pre-existing config, unrelated to this plan, and correctly does not run on a `pre-commit`-stage invocation) -- all 18 that ran, including the new hook, green. Full suite: **1030 passed** (1021 baseline + 9 new).

## Mutation Checks (transcribed, not paraphrased)

**Mutation 1 -- comment out the entire `ast.Attribute` walk block.**
- Pre-mutation hash: `35790783a483dfcdb1a80668dc726bc12dc2bd16892d33ce73bb050bc7cab2b9`
- Post-mutation hash: `0455d3be176eea1870d34e68c6f1b544032d4516ba0614f1a1a18c78b15c9442` (differs, confirmed before running)
- `pytest tests/tools/test_check_harness_accessor_only.py::test_flags_an_aliased_module_attribute_call -v`:
  ```
  FAILED ... AssertionError: import features.tier as t; t.load_features(...) went unreported
  assert []
  ```
- Restored; hash verified back to `35790783...`; full self-test file re-run green (9 passed).

**Mutation 2 -- the plan's literal instruction: set `WATCHED_TARGET = "load_features"` (bare string).**
- Post-mutation hash: `7397e5e9cf6a1987b169a5d36c43246ee60ab8937cb10f04a80b0e3db9ea2f05` (differs)
- Because `WATCHED_MODULE, WATCHED_NAME = WATCHED_TARGET.rsplit(".", 1)` runs once at import time, a bare string with no `.` makes `rsplit` return a 1-element list, and the unpack raises at collection:
  ```
  ERROR tests/tools/test_check_harness_accessor_only.py - ValueError: not enough
  values to unpack (expected 2, got 1)
  tools/check_harness_accessor_only.py:90: in <module>
      WATCHED_MODULE, WATCHED_NAME = WATCHED_TARGET.rsplit(".", 1)
  ```
  This IS a failure of `test_does_not_flag_the_catalogue_load_features` (collection error), satisfying the plan's letter, but it demonstrates an import-time crash, not the bare-name false-positive the mutation is meant to exercise.
- Restored; hash verified back to `35790783...`.

**Mutation 2b (supplemental, not in the plan, added because 2 was a crash not a false positive) -- drop the `node.module == WATCHED_MODULE` guard in the `ImportFrom` check** so matching falls back to name-only:
- Post-mutation hash: `06d55208610e0e6a34aa36d9822e13012c848155c6d5531f099e65f8abe1db7f` (differs)
- `pytest tests/tools/test_check_harness_accessor_only.py::test_does_not_flag_the_catalogue_load_features -v`:
  ```
  FAILED ... AssertionError: spec.catalogue.load_features is the CATALOGUE
  loader, unrelated to D-05-15 -- the bare-name collision this scanner must
  not fall into
  assert not ['models/train.py:1: imports features.tier.load_features
  directly -- reach features through harness.accessor.materialize so the
  look is counted']
  ```
  This is the real bare-name-collision hazard reproduced: with the module guard dropped, `from spec.catalogue import load_features` is misreported as the tier's function.
- Restored; hash verified back to `35790783...`; full self-test file re-run green (9 passed); `pytest tests -q` re-run green (1030 passed) before the task commit.

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | the scanner, its self-test, and the 19th hook | `a794504` | `tools/check_harness_accessor_only.py`, `tests/tools/test_check_harness_accessor_only.py`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml` |

**Plan metadata:** commit pending (this SUMMARY + STATE.md + ROADMAP.md)

## TDD Gate Compliance

No standalone `test(05-06): ...` RED commit exists, matching every prior Phase 5 plan's precedent (`git log --oneline` shows no `test(05-...)` commits across 05-00..05-05 either) -- the `pytest tests -x -q` hook runs the FULL suite on every commit, so a test-only commit referencing a not-yet-created module fails collection and the hook blocks it; `--no-verify` is forbidden. RED was demonstrated without committing it: `tools/check_harness_accessor_only.py` was moved aside, `pytest tests/tools/test_check_harness_accessor_only.py -x -q` was run and produced `ModuleNotFoundError: No module named 'tools.check_harness_accessor_only'` across all 9 (then-uncollectable) tests, the file was restored, and the suite went green before the single `feat(05-06)` commit that carries tests + implementation + hook wiring together.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - stale plan premise] Sanctioned test list is four files, not the three 05-06-PLAN.md named**
- **Found during:** Task 1, `read_first` grep-confirmation step (before writing `SANCTIONED_FILES`)
- **Issue:** 05-06-PLAN.md's constraint 3 named exactly three sanctioned test files, grepped at plan-authoring time before 05-01..05-05 had landed. Grepping the actual repo state at this plan's execution time found a fourth real importer, `tests/harness/test_kfold.py` (added by 05-02-PLAN.md's Task 2). The plan's own acceptance criterion ("the scan must be GREEN over the real repo today") could not hold with only three names, since `test_kfold.py` genuinely calls `load_features` directly and is not under the sanctioned `harness/` directory (it lives at `tests/harness/`, whose `parts[0]` is `tests`, not `harness`).
- **Fix:** Added `tests/harness/test_kfold.py` as a fourth named entry in `SANCTIONED_FILES`, with a reason string grepped from the file itself (it compares `load_features`'s raw output against `harness.accessor.materialize`'s output, the same "must call the tier directly to check what wraps it" justification as the other three). Did not sanction `tests/harness/` as a directory -- that would silently exempt any future file under it, which is exactly the hole `test_sanctions_the_named_test_files_and_no_others`'s fifth-file proof exists to catch. Documented the discrepancy explicitly in the scanner's own module docstring.
- **Files modified:** `mvp/tools/check_harness_accessor_only.py`, `mvp/tests/tools/test_check_harness_accessor_only.py`
- **Commit:** `a794504`

### Auto-fixed Issues (minor)

**2. [Rule 1 - bug] `ruff format` reflowed one long line in the new self-test file**
- **Found during:** the pre-commit `ruff-format` hook, first `pre-commit run --all-files` pass
- **Issue:** `test_flags_an_aliased_module_attribute_call`'s literal-import assertion message exceeded the formatter's line-wrap width.
- **Fix:** Ran `ruff format` on the two new files; the only change was wrapping that one assertion's message onto its own line.
- **Files modified:** `mvp/tests/tools/test_check_harness_accessor_only.py`
- **Commit:** `a794504`

## What Was NOT Done

- **EVAL-03 is NOT marked complete.** 05-06-PLAN.md's frontmatter lists `requirements: [EVAL-03]`, but the phase's own absolute rules state Plan 07 closes the phase -- `requirements.mark-complete` was deliberately skipped for this plan.
- **D-05-14's exhaustion refusal is not implemented anywhere this plan touches.** `harness/budget.py`'s own docstring says so ("NO EXHAUSTION CHECK YET"); this scanner's docstring names the runtime control it defers to (`materialize`'s look-count, not an exhaustion check) rather than overclaiming a refusal path that does not exist yet.
- **The `ast.Call` (dynamic `importlib.import_module`) branch is untested by a dedicated behavior test** -- it was cloned from the template for structural parity and confirmed by grep not to false-positive against anything in the real repo today, but no synthetic fixture exercises it the way the five `ast.Attribute` shapes are exercised. If a future plan adds a real dynamic import of `features.tier`, this branch is the only thing standing between it and silence, and it has never been red-proofed.
- **The scanner cannot see, by design (disclosed in its own docstring):** a dynamically constructed import string (`importlib.import_module("features." + "tier")`, `getattr(mod, "load_" + "features")`), and an alias rebound through an intermediate variable (`m = t` where `t` was bound to `features.tier`, then `m.load_features(...)`). Both are D-05-15's own stated limits, not new gaps this plan introduced.
- **No production code outside the scanner and hook wiring changed.** `harness/accessor.py`, `harness/budget.py`, and every other Phase 5 module are untouched by this plan.

## Self-Check: PASSED

- `mvp/tools/check_harness_accessor_only.py` -- FOUND
- `mvp/tests/tools/test_check_harness_accessor_only.py` -- FOUND
- Commit `a794504` -- FOUND in `git log --oneline --all`
- `.pre-commit-config.yaml` contains `check-harness-accessor-only` -- FOUND
- `.github/workflows/ci.yml` contains `check_harness_accessor_only` -- FOUND
