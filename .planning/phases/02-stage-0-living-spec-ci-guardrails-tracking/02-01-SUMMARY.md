---
phase: 02-stage-0-living-spec-ci-guardrails-tracking
plan: 01
subsystem: docs
tags: [spec, toml, tomllib, markdown-rendering, ci-guardrails, mlflow, numba, sharpe]

# Dependency graph
requires:
  - phase: 01-capture-daemon-repo-foundation
    provides: mvp/ containment convention, pytest/ruff project config, hermetic-test discipline
provides:
  - mvp/mvp.md + mvp/spec.md as the living spec under containment, with root pointer stubs
  - Corrected Stage-2 decision rule (dimensional bug fixed), Spot-L1 clock exception,
    trades-backfill side-exactness note, Sharpe annualization convention, MLflow
    mandatory tag schema, numba no-globals rule -- all pre-declared in spec.md
  - mvp/spec/{features,labels}.toml as the machine-readable catalogue source of truth
    (4 features, 4 labels, declared not implemented)
  - mvp/spec/catalogue.py typed registry (get_feature/get_label/CatalogueError/
    diff_definition_changes)
  - mvp/spec/render.py (marker-delimited spec.md regeneration, idempotent) and
    mvp/tools/check_spec_diff.py (CI-callable drift + definition-change check)
affects: [02-stage-0-living-spec-ci-guardrails-tracking/02-02, 02-stage-0-living-spec-ci-guardrails-tracking/02-03, 04-feature-engine]

# Tech tracking
tech-stack:
  added: []  # stdlib only (tomllib, ast-adjacent none yet, difflib, subprocess)
  patterns:
    - "Catalogue source of truth in TOML, loaded via tomllib + set-difference required-key validation, no pydantic"
    - "Marker-delimited markdown regeneration: header+separator+rows wrapped together between exact-line HTML-comment markers, never a marker embedded inside an existing table"
    - "One check script (mvp/tools/check_X.py) with a directly-testable main(argv=None) -> int, no subprocess in tests"

key-files:
  created:
    - mvp/spec/features.toml
    - mvp/spec/labels.toml
    - mvp/spec/catalogue.py
    - mvp/spec/render.py
    - mvp/tools/check_spec_diff.py
    - mvp/tests/spec/test_catalogue.py
    - mvp/tests/spec/test_render.py
  modified:
    - mvp/mvp.md
    - mvp/spec.md
    - CLAUDE.md
    - mvp.md (root stub)
    - spec.md (root stub)

key-decisions:
  - "Catalogue markers wrap the entire table (header+separator+rows), not just the rows between the separator and data -- placing a marker line inside an existing GFM table breaks table continuation on GitHub (deviation from the plan's literal marker placement, documented below)."
  - "mvp/tests/spec/__init__.py must NOT exist -- it collides with the real mvp/spec package name under pytest's rootdir package-inference import, silently shadowing spec.catalogue."
  - "diff_definition_changes(old, new, field='definition') is parameterized so callers must pass field='computation' for labels -- the default would KeyError on label dicts, which have no definition key."

patterns-established:
  - "TOML catalogue + tomllib + set-difference validation (no schema library)"
  - "render.py / check_spec_diff.py both anchor every on-disk path off PKG_ROOT = Path(__file__).resolve().parents[N], never a cwd-relative literal"

requirements-completed: [SPEC-01, SPEC-03, SPEC-04]

# Metrics
duration: 10min
completed: 2026-09-13
---

# Phase 2 Plan 01: Living Spec Move + Corrections + TOML Catalogue Summary

**Moved `spec.md`/`mvp.md` under `mvp/` with root stubs, landed all six SPEC-03/SPEC-04 spec corrections into `spec.md`, and built a TOML catalogue source of truth with a typed registry and a marker-delimited renderer + CI diff check.**

## Performance

- **Duration:** ~10 min (commit span 18:51:17 -> 19:00:52 PDT)
- **Started:** 2026-09-13T18:51:17-07:00
- **Completed:** 2026-09-13T19:00:52-07:00
- **Tasks:** 3 (Task 2 and 3 each TDD: RED + GREEN, plus one style-only follow-up commit)
- **Files modified:** 15 (2 renamed, 2 new root stubs, 1 modified CLAUDE.md, 8 new mvp/spec+mvp/tools+mvp/tests/spec files, 2 modified after ruff format)

## Accomplishments

- `mvp.md`/`spec.md` live under `mvp/` (pure `git mv`, history preserved via
  `git log --follow`); root-level 1-line stubs point to the new location;
  `CLAUDE.md`'s "Source documents" line updated
- `mvp/mvp.md`'s dimensionally-buggy decision-logic block struck and replaced with a
  pointer to `mvp/spec.md`'s corrected rule
- `mvp/spec.md` gained: a corrected `## Decision rule (Stage 2)` section
  (`pred_mid = mid * (1 + pred_10s_return)`; `X_price = mid * X_bps / 10_000`; flat vs.
  positioned comparisons), a `### Spot-L1 clock exception` subsection, a
  `### Trades-backfill side-exactness` subsection (plus an amended Side-conventions
  bullet cross-referencing it), a `## Sharpe annualization convention` section
  (`mean(daily_pnl)/std(daily_pnl)*sqrt(365)`, >=30-daily-observation gate, per-trade
  annualization banned), a `## MLflow tag schema` section (8 mandatory tags,
  `mlflow-skinny` requirement, `search_runs()` ban), and a `## Numba no-globals rule`
  section -- five new DONTs bullets and a Change log row accompany these
- Feature catalogue (`mid`, `imb_top`, `ofi`, `trade_flow`) and label catalogue
  (`ret_10s_mid`, `ret_1s_mid`, `ret_1min_mid`, `ret_10min_mid`) wrapped in
  marker-delimited pairs in `spec.md`, backed by `mvp/spec/{features,labels}.toml`
- `mvp/spec/catalogue.py`: `load_features`/`load_labels` (tomllib + required-key
  validation), `get_feature`/`get_label`, `CatalogueError`, `diff_definition_changes`
  (parameterized `field`, defaulting to `"definition"`, with `"computation"` required
  for labels)
- `mvp/spec/render.py`: `render_features_table`/`render_labels_table` (rows only,
  declaration order, pipe-escaping), `render_spec` (exact-line marker anchoring,
  idempotent), `main()` regenerating `mvp/spec.md` in place
- `mvp/tools/check_spec_diff.py`: `git_show_toml` (git-ref TOML fetch, `None` on
  unresolvable ref), `check_drift`, `main(argv=None) -> int` (`--base-ref`, default
  `HEAD~1`; `field="definition"` for features, `field="computation"` for labels;
  unresolvable ref warns and exits 0, never fails)
- 24/24 tests green in `mvp/tests/spec`; `ruff check` and `ruff format --check` clean
  on all new modules; `python -m spec.render` run twice against the real `mvp/spec.md`
  produces zero diff on both runs (Task 1's hand-written rows already match the
  renderer's canonical formatting byte-for-byte); `python -m tools.check_spec_diff`
  exits 0 against the real repo

## Task Commits

1. **Task 1a: pure rename** - `ae5c90f` (docs) -- `git mv spec.md mvp/spec.md`, `git mv mvp.md mvp/mvp.md`, nothing else
2. **Task 1b: root stubs + spec corrections** - `759f8b7` (docs)
3. **Task 2 RED: failing catalogue tests** - `4b1850a` (test)
4. **Task 2 GREEN: catalogue.py + TOML** - `515393e` (feat) -- includes the `tests/spec/__init__.py` deletion (Rule 1 fix, see Deviations)
5. **Task 3 RED: failing render/check_spec_diff tests** - `be7aaaa` (test)
6. **Task 3 GREEN: render.py + check_spec_diff.py** - `8434fa0` (feat)
7. **Style follow-up: ruff format** - `62c884b` (style)

**Plan metadata:** (this commit, following SUMMARY/STATE/ROADMAP updates)

## Files Created/Modified

- `mvp/mvp.md` - struck buggy decision-logic block, points to `spec.md`
- `mvp/spec.md` - six SPEC-03/SPEC-04 corrections, two marker-delimited catalogue tables, updated TOC, Change log row
- `CLAUDE.md` - "Source documents" line points at `mvp/mvp.md`/`mvp/spec.md`
- `mvp.md`, `spec.md` (repo root) - 1-line pointer stubs
- `mvp/spec/__init__.py` - package docstring
- `mvp/spec/features.toml` - 4 seed feature entries (mid, imb_top, ofi, trade_flow)
- `mvp/spec/labels.toml` - 4 seed label entries (ret_10s_mid, ret_1s_mid, ret_1min_mid, ret_10min_mid)
- `mvp/spec/catalogue.py` - typed registry, `diff_definition_changes`
- `mvp/spec/render.py` - marker-delimited regeneration
- `mvp/tools/__init__.py` - package docstring
- `mvp/tools/check_spec_diff.py` - CI-callable drift/definition-change check
- `mvp/tests/spec/test_catalogue.py` - 13 tests
- `mvp/tests/spec/test_render.py` - 11 tests

## Decisions Made

- Catalogue markers wrap the whole table (header + separator + rows), not just the
  rows between an existing header and `**Rules:**` -- see Deviations below.
- `mvp/tests/spec/__init__.py` was not created (deleted after the RED commit) --
  its presence collides with the real `mvp/spec` package under pytest's rootdir
  package-inference, silently shadowing `spec.catalogue`. See Deviations.
- `diff_definition_changes`'s `field` parameter defaults to `"definition"`
  (features' comparison key); every call site touching labels passes
  `field="computation"` explicitly, both in `check_spec_diff.py` and in tests --
  labels have no `definition` key, so the default against a label dict is a
  deliberate `KeyError`, not a silent wrong comparison.
- Only Name cells are backtick-wrapped by the renderer; all other cells render the
  raw TOML string value (pipe-escaped). Chosen for determinism: `ofi`'s definition
  is prose, so wrapping definitions in backticks would look inconsistent across rows.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Catalogue markers wrap the full table, not just the rows**
- **Found during:** Task 1 (writing the marker-delimited catalogue tables)
- **Issue:** The plan's literal instruction places `<!-- catalogue:features:begin -->`
  immediately after the table's header-separator row and `:end` immediately before
  `**Rules:**` -- i.e. the marker line sits *inside* the table, between the
  separator row and the first data row. Per GFM (GitHub Flavored Markdown) table
  continuation rules, an HTML-comment line is a block-level structure that
  terminates table parsing; a marker placed there would render as a header-only
  table on GitHub with the data rows falling out as literal `| mid | ... |` text.
- **Fix:** Placed `:begin` immediately before the header row and `:end` immediately
  after the last data row, so the markers wrap the complete, valid table (header +
  separator + rows). `render.py`'s `render_features_table`/`render_labels_table`
  still return rows-only per the plan's behavior spec; `render_spec`/`_replace_block`
  glue a `FEATURES_HEADER`/`LABELS_HEADER` module constant on top when substituting
  between the markers, so the replacement text is always a complete table.
- **Files modified:** `mvp/spec.md` (marker placement), `mvp/spec/render.py`
  (`FEATURES_HEADER`/`LABELS_HEADER` constants, `_replace_block`)
- **Verification:** All grep-based acceptance criteria (marker-count = 2 per pair)
  still pass; `render.py` run twice against the real `spec.md` is idempotent with
  zero diff on both runs.
- **Committed in:** `759f8b7` (spec.md), `8434fa0` (render.py)

**2. [Rule 1 - Bug] Removed `mvp/tests/spec/__init__.py`**
- **Found during:** Task 2 GREEN phase, verifying `test_catalogue.py` actually
  passes after restoring `catalogue.py`
- **Issue:** With `mvp/tests/spec/__init__.py` present (as the plan's file list
  specifies), pytest's rootdir-based package inference walks up from
  `tests/spec/test_catalogue.py` through the first `__init__.py`-having ancestor
  chain. Since `mvp/tests/` itself intentionally has no `__init__.py` (Phase 1's
  `conftest.py` docstring: "this file exists so mvp/tests is a discoverable
  rootdir-relative package"), pytest inserts `mvp/tests/` into `sys.path` and
  imports the test module as the top-level dotted name `spec.test_catalogue`. This
  registers `sys.modules["spec"]` as the *test* package (empty, no `catalogue`
  submodule), silently shadowing the real `mvp/spec` package of the identical name
  already reachable via `pythonpath = ["."]`. Every `from spec.catalogue import
  ...` in the test file then raised `ModuleNotFoundError: No module named
  'spec.catalogue'` -- reproduced empirically with the real `catalogue.py` present
  on disk, ruling out a simple "file doesn't exist yet" RED false-positive.
- **Fix:** Deleted `mvp/tests/spec/__init__.py`. Without it, pytest imports
  `test_catalogue.py`/`test_render.py` as plain top-level modules
  (`test_catalogue`, `test_render`) with no package identity, eliminating the name
  collision with the real `spec` package. `mvp/tools/` has no equivalent collision
  (no `mvp/tests/tools/` directory was created).
- **Files modified:** `mvp/tests/spec/__init__.py` (deleted)
- **Verification:** `./.venv/bin/python3 -m pytest tests/spec -x -q` -- 24/24 pass,
  both test files collect and import correctly.
- **Committed in:** `515393e` (same commit as the GREEN implementation, since the
  fix was discovered while verifying that commit's own tests)

---

**Total deviations:** 2 auto-fixed (both Rule 1 - bug fixes required for the plan's
own acceptance criteria to be achievable: a broken-on-GitHub table and a hard
`ModuleNotFoundError` are not stylistic choices)
**Impact on plan:** Both fixes were necessary for the plan's stated `must_haves` and
`done` criteria to actually hold (byte-identical, GFM-valid rendering; a working,
directly-testable registry). No scope creep -- no new files beyond what the plan's
`files_modified` list already named (one file from that list, `tests/spec/__init__.py`,
was *not* created; a fully justified subtraction).

## Issues Encountered

- Ruff's initial header constant for the label table (`LABELS_HEADER`) had only 5
  `---` separator cells for a 6-column table; caught by the plan's own idempotence
  acceptance check (first `python -m spec.render` run produced a 1-line diff on the
  separator row). Fixed before committing Task 3's GREEN commit; the corrected
  constant now produces zero diff on the very first run against the real `spec.md`.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `mvp/spec/catalogue.py`'s registry (`get_feature`/`get_label`) and
  `diff_definition_changes` are ready for Plan 02's `check_catalogue_completeness`
  and `check_spec_diff.py`'s definition-change enforcement to build on.
- `mvp/spec.md`'s MLflow tag schema (8 mandatory keys) and Sharpe annualization
  convention are now committed and ready as the contract Plan 03's
  `mvp/tracking/mlflow_utils.py` must conform to.
- `mvp/tools/` now exists as a package; Plan 02's `check_numba_globals.py`,
  `check_catalogue_completeness.py`, etc. can land alongside `check_spec_diff.py`
  without further scaffolding.
- No blockers. The capture daemon (Run G, PID 10771) was confirmed alive and
  untouched throughout (`ps -p 10771` succeeded before, during, and after this
  plan's execution; no `uv sync`, no writes under `/Volumes/ProjectsSSD/aihedgefund/capture`).

---
*Phase: 02-stage-0-living-spec-ci-guardrails-tracking*
*Completed: 2026-09-13*

## Self-Check: PASSED

All 14 files claimed as created/modified verified present on disk (including
confirming `mvp/tests/spec/__init__.py` is absent, as intended by the Rule 1 fix).
All 7 commit hashes (`ae5c90f`, `759f8b7`, `4b1850a`, `515393e`, `be7aaaa`, `8434fa0`,
`62c884b`) verified present in git history. No missing items.
