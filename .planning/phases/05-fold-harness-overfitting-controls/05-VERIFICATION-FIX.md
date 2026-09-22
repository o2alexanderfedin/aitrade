---
phase: 05-fold-harness-overfitting-controls
fixed: 2026-09-22T12:52:59Z
status: both_gaps_closed
ci_run: https://github.com/o2alexanderfedin/aitrade/actions/runs/35729636694
ci_conclusion: success
commits:
  - a3711f2: "fix(05-verify-fix): give the MLflow tracking root its own disk-space floor"
  - 6f219ef: "fix(05-verify-fix): materialize auto-resolves and fail-closes on a segment's own errata_id"
  - dafefe1: "test(05-verify-fix): isolate read_errata_manifest's manifest_id-field check"
---

# Phase 5 Verification Fix Report

Closes the two gaps `05-VERIFICATION.md` found: CI had never been green for Phase 5's
real code, and `harness.accessor.materialize` never actually performed the automatic
errata masking D-05-20/spec.md promised.

## Gap 1 — CI disk-space threshold

**What was broken.** Every push containing real Phase 5 code failed CI with
`DataRootError: ... has only 12.4{8,9} GiB free, below the required 50.00 GiB free`.
`harness/budget.py` and `harness/negative_log.py` imported `data.capture.config.
DEFAULT_MIN_FREE_GB` (50 GiB — sized for the capture daemon's continuous multi-GB/day
write workload) as their own default `min_free_gb`, passed straight through to
`start_tracked_run` for the MLflow tracking root: a kilobytes-sized SQLite store. The
shared GitHub Actions runner has ~12.5 GiB free, so every real look-issuing test
refused deterministically.

**The fix.** `tracking/mlflow_utils.py` now declares its own floor,
`MLFLOW_MIN_FREE_GB = 1.0` (a comment in the module states why 1.0 and not something
tied to write volume: an MLflow run is kilobytes; the floor exists only to refuse an
effectively-full volume, not to size against a workload that doesn't scale with
anything this project does at MVP scale). `start_tracked_run`'s default is now
`MLFLOW_MIN_FREE_GB`. `harness/budget.py` and `harness/negative_log.py` stop importing
capture's constant and default to `MLFLOW_MIN_FREE_GB` instead.

**`data/lockbox.py` — decided to fix, not defer.** `open_lockbox`/`_open_locked` took
one `min_free_gb` parameter and used it for TWO different roots: `lake_paths.
lake_root(min_free_gb=...)` (the physical SSD lake — legitimately sized like capture's
own threshold) AND `start_tracked_run(..., min_free_gb=...)` (the tracking root — the
same category error as Gap 1). I grepped every `open_lockbox` call site before
deciding: `tests/lockbox/test_token_one_look.py` has 14 call sites (confirmed by an
`awk` pass over each, not just a line count), `test_containment.py` has 2 more — 16
total. Every single one passes `lake_root=` explicitly AND `min_free_gb=0.0`. Before
the fix, that `0.0` was live-forwarded into `start_tracked_run` and was the ONLY
reason these tests didn't hit capture's 50 GiB threshold on the tracking root — the
identical bug Gap 1 fixed elsewhere, masked here by every test overriding it rather
than the code being correct. Fixed: `min_free_gb` now governs only
`lake_paths.lake_root()`'s physical disk check (never reached in these tests anyway,
since `lake_root=` is always supplied directly); the `start_tracked_run` call no
longer forwards `min_free_gb` at all, so the tracking root gets its own independent
`MLFLOW_MIN_FREE_GB` default. All 16 `min_free_gb=0.0` lockbox call sites become
harmless no-ops post-fix — confirmed by running the full `tests/lockbox/` suite (all
pass unchanged). (The remaining `min_free_gb=0.0` sites found by the broader grep — in
`tests/tracking/test_mlflow_utils.py` and `tests/dq/test_pause_enforcement.py` — call
`start_tracked_run` directly and are unrelated to the lockbox conflation; they stay
valid explicit overrides, now of `MLFLOW_MIN_FREE_GB` rather than
`DEFAULT_MIN_FREE_GB`.)

I also verified `harness.budget.look_count`'s other MLflow root call
(`lake_paths.mlflow_tracking_root(...)`) does **not** call `validate_data_root` at
all — its own docstring states this deliberately ("Deliberately does NOT `mkdir` or
`validate_data_root`") — so there was no second disk-check site on the query path to
fix.

**Proof (a) — regression test, independence.**
`tests/harness/test_budget.py::test_record_look_default_min_free_gb_is_independent_of_captures_threshold`
fakes `shutil.disk_usage` to report ~12.48 GiB free (the exact number the failing CI
logs measured) and asserts, on the SAME faked disk: `record_look` called with **no**
`min_free_gb` kwarg (its default is the property under test) succeeds, while
`data.capture.config.validate_data_root` at its own unchanged 50 GiB default still
raises `DataRootError` naming "50.00 GiB". Two independent thresholds, proven
independent on one faked reading.

**Proof (b) — mutation check, with hash proof.**
```
BEFORE: harness/budget.py sha256 = 6e048429a26b6a5893d536792a5ecc2b2d76c7467aad5a033dc795d6f2db3565
  min_free_gb: float = MLFLOW_MIN_FREE_GB
MUTATED: restored `from data.capture.config import DEFAULT_MIN_FREE_GB` and
  `min_free_gb: float = DEFAULT_MIN_FREE_GB`
AFTER (mutated): harness/budget.py sha256 = 081fe6eebe773a04398123b4798a1f47ded3a492cfad866ce89f9ae0cacd3a9c
  (hash changed, confirming the mutation actually landed)
RESULT: tests/harness/test_budget.py -x -q → 1 failed, 7 passed
  FAILED test_record_look_default_min_free_gb_is_independent_of_captures_threshold
  data.capture.config.DataRootError: data_root '.../mlflow_root' has only 12.48 GiB
  free, below the required 50.00 GiB free  ← the EXACT original CI failure message
RESTORED: harness/budget.py sha256 = 6e048429a26b6a5893d536792a5ecc2b2d76c7467aad5a033dc795d6f2db3565 (matches BEFORE)
RE-RUN: tests/harness/test_budget.py -x -q → 8 passed
```

**Proof (c) — CI actually green.** Pushed both fix commits; watched
`gh run watch 35729636694 --exit-status`:
`https://github.com/o2alexanderfedin/aitrade/actions/runs/35729636694` — conclusion
`success`, all 19 guardrail steps (`ruff check`, `ruff format --check`, `uv lock
--check`, all 10 `check_*` tools, `check_no_manifest_rewrite --full` twice,
`check_manifest_id_integrity`, `check_manifest_append_only`,
`check_harness_accessor_only`, `pytest (leakage suite)`, `pytest (tests, via
testpaths)`) passed, 2m16s total. This is the FIRST green CI run on this branch
containing real Phase 5 code (every prior run — 35694486557 through 35722771928 — had
failed with the identical `DataRootError`).

## Gap 2 — errata masking not read at materialize time

**What was broken.** `harness.accessor.materialize(..., errata_cells: list[dict] |
None = None)` never read `manifest["errata_id"]`; masking was 100% caller-supplied via
an optional argument that silently no-opped on the documented default. D-05-20 and
spec.md's "Fold harness" section both stated masking happens automatically at read
time. Latent (no real look has consumed the real committed manifest — confirmed at
verification time via a live `look_count` query returning 0), but load-bearing: the
real manifest's 249 errata cells sit inside the real `oof_block_0..4` ranges, exactly
where a real k-fold OOF look would read them.

**The fix.** `harness/errata.py` gains `errata_manifest_path` and
`read_errata_manifest`, mirroring `harness.segments.read_segment_manifest`'s
self-hash-on-read shape, plus two checks a segment manifest's own reader does not need:

1. Missing file → raises `ErrataManifestError` (a dedicated class, not a bare
   `ValueError`, so the fail-closed contract is assertable precisely — a bare
   `ValueError` would also match `_find_entry`'s or `read_segment_manifest`'s own
   unrelated raises).
2. Self-hash re-verified with `compute_manifest_id(manifest)` against BOTH the
   filename-stem-derived id the caller passed AND the body's own `manifest_id` field —
   catching a hand-edited body whose `manifest_id` field was kept superficially in
   sync, not just one whose hash disagrees outright.
3. `symbol`/`version` cross-checked against the segment manifest naming it.

Any failure raises — never degrades to "mask nothing" (the exact failure direction
`data.holdout`'s own fail-closed doctrine forbids). `materialize`'s `errata_cells`
parameter is removed entirely (not kept as an assertion-only override — no test or
caller needed it retained); `errata_id: null` in the segment manifest is now the ONLY
way to apply no errata. `spec.md`'s "Fold harness" section gained one sentence stating
this contract explicitly (self-hash re-verified, fail-closed, `null` is the only
no-op) — the existing sentence about automatic masking was already accurate wording,
just previously untrue of the code.

**Proof (a) — regression test, real manifest, nothing caller-supplied.**
`tests/harness/test_accessor.py::test_accessor_applies_admission_and_errata_gates`
now writes a REAL `registry_root/errata/<id>.json` (via a new `_write_errata_manifest`
test helper, content-addressed the same way `issue_segment_manifest` is) naming 3
cells across BOTH `ret_1s_mid` and `ret_10s_mid`, on rows admission does not exclude,
and calls `materialize(...)` with no `errata_cells` kwarg (the parameter no longer
exists). Asserts, counted BOTH directions: `null_count == 3` (exactly the named
cells), `non_null_count == df.height * 2 - 3` (every other cell untouched), plus
per-cell spot checks that the masked column is `None` while the row's OTHER label
column and an untouched row's both columns survive.

**Proof (b) — anti-vacuity.**
- `test_materialize_errata_id_null_masks_nothing`: `errata_id=None` → `null_count ==
  0` across both label columns.
- `test_materialize_refuses_missing_errata_manifest`: a syntactically plausible but
  nonexistent errata id → raises `ErrataManifestError` matching "missing"; asserts
  `look_count(...) == 0` afterward (the refused read never spends a look — fail-closed
  extends to the budget, not just the data).
- `test_materialize_refuses_tampered_errata_manifest`: writes a real errata manifest,
  then mutates its `cells` list in place after computing its id → raises
  `ErrataManifestError` matching "hash mismatch" (the `recomputed != errata_id` half
  of the check).
- `test_materialize_refuses_errata_manifest_whose_manifest_id_field_disagrees`:
  isolates the OTHER half — rewrites only the body's own `manifest_id` field (every
  other key, including `cells`, untouched), so `recomputed` alone still equals
  `errata_id` (`compute_manifest_id` excludes the `manifest_id` key from what it
  hashes) and only `body_manifest_id != errata_id` fires. Added after review found the
  tampered-manifest test above trips `recomputed` first and never isolates this
  branch; sanity-checked by dropping the `body_manifest_id` clause locally (this test
  alone goes red: "DID NOT RAISE"), then restoring.
- `test_materialize_refuses_errata_manifest_with_wrong_symbol`: errata manifest
  written for `"ETHUSDT"`, segment manifest for `"BTCUSDT"` → raises matching
  "symbol".

**Proof (c) — mutation checks, with hash proof, TWO mutations (per reviewer
guidance, to prove the regression test in (a) catches the actual gap, not only the
fail-closed path):**

Mutation 1 — `read_errata_manifest` degrades to "mask nothing" on a missing file:
```
BEFORE: harness/errata.py sha256 = 186430d68066b50c27ca112b9bba4e3101c41da7a057fdc4604302e0530ec5bf
MUTATED: `if not path.exists(): raise ErrataManifestError(...)` replaced with
  `if not path.exists(): return []`
AFTER (mutated): harness/errata.py sha256 = a14687c77948eb23b488e76476d64021a71e4798bbfd48a3db4732fcb172bd17
RESULT: pytest tests/harness/test_accessor.py -q -k errata →
  1 failed, 6 passed, 6 deselected: test_materialize_refuses_missing_errata_manifest
  "Failed: DID NOT RAISE ErrataManifestError"
RESTORED: hash back to 186430d68066b50c27ca112b9bba4e3101c41da7a057fdc4604302e0530ec5bf
RE-RUN: 7 passed, 6 deselected (all 7 errata-matching tests in this file)
```

Mutation 2 — `materialize` never resolves `errata_id` at all (the ORIGINAL bug,
reproduced directly in the accessor):
```
BEFORE: harness/accessor.py sha256 = 7e87bf7bb9f18506251888e61859d7bb4c529c48f64ed41b6b9b83eb5043e790
MUTATED: the errata_id-resolution block replaced with `resolved_errata_cells = []`
  unconditionally (never calls read_errata_manifest, regardless of errata_id)
AFTER (mutated): harness/accessor.py sha256 = efb9f1b065566d178c416cc52bddfab0d28f39b2304f6afdd7d8fca93be59104
RESULT: pytest tests/harness/test_accessor.py -q -k errata →
  5 failed, 2 passed, 6 deselected:
  test_accessor_applies_admission_and_errata_gates            ← proof (a)'s own test
  test_materialize_refuses_missing_errata_manifest
  test_materialize_refuses_tampered_errata_manifest
  test_materialize_refuses_errata_manifest_whose_manifest_id_field_disagrees
  test_materialize_refuses_errata_manifest_with_wrong_symbol
RESTORED: hash back to 7e87bf7bb9f18506251888e61859d7bb4c529c48f64ed41b6b9b83eb5043e790
RE-RUN: tests/harness/ (full directory) → 84 passed
```
Mutation 2 confirms `test_accessor_applies_admission_and_errata_gates` — proof (a) — is
itself a genuine regression test for the gap the verifier found (unmasked rows on the
documented default), not merely exercising the fail-closed path.

## Full suite, hooks, and CI

- `./.venv/bin/pytest tests -x -q` (mvp/): **1065 passed** (1059 pre-existing + 6 new:
  1 in `test_budget.py`, 5 in `test_accessor.py` — the 5th isolates
  `read_errata_manifest`'s `body_manifest_id != errata_id` check specifically, added
  after review; sanity-checked by dropping that clause locally and confirming the new
  test alone goes red, then restoring — same hash-proof discipline as the two mutation
  checks below, hash `186430d6...` unchanged after restore).
- Both commits' pre-commit hooks: **18 of the 19 configured hooks** ran and passed at
  `git commit` (`ruff check`, `ruff format --check`, `uv lock --check`, 10 `check_*`
  tools, `check_no_manifest_rewrite --full` ×2, `check_manifest_id_integrity`,
  `check_manifest_append_only`, `check_harness_accessor_only`, both `pytest` steps).
  The 19th, `check-no-manifest-rewrite-full`, is `stages: [pre-push]` by design (same
  distinction 05-03-SUMMARY.md and 05-VERIFICATION.md both note) — it ran, and passed,
  at `git push` instead (visible in the push output above each push).
- `find mvp/features -name '*.nb[ci]'` — empty before each commit (no numba cache
  stragglers).
- CI: `https://github.com/o2alexanderfedin/aitrade/actions/runs/35729636694` —
  **success**, 2m16s, all steps green. First green run on this branch containing real
  Phase 5 code.

## Commits

| Commit | Message |
|---|---|
| `a3711f2` | fix(05-verify-fix): give the MLflow tracking root its own disk-space floor |
| `6f219ef` | fix(05-verify-fix): materialize auto-resolves and fail-closes on a segment's own errata_id |
| `dafefe1` | test(05-verify-fix): isolate read_errata_manifest's manifest_id-field check |

## What was NOT done

- **`read_segment_manifest`'s own latent gap, noted but not fixed.** While building
  `read_errata_manifest`'s stricter self-hash check (both the filename stem AND the
  body's own `manifest_id` field), I noticed `harness.segments.read_segment_manifest`
  only checks the recomputed hash against the filename-stem-derived id, never against
  the body's own `manifest_id` field. `compute_manifest_id` excludes the
  `manifest_id` key from what it hashes, so a segment manifest whose stored
  `manifest_id` field disagrees with its own filename would currently still be
  accepted as long as the REST of the body hashes correctly. This is a real, adjacent
  gap, but it is in `segments.py`, not `errata.py`/`accessor.py`, and neither gap
  the verifier found names it — out of scope for this fix, not touched.
- **Issuance-time `errata_id` validation.** `harness.segments.issue_segment_manifest`
  still accepts a caller-supplied `errata_id` at issuance with no check that the named
  errata manifest exists, hashes correctly, or matches symbol/version — only
  `materialize` (read time) validates it now. Issuance-time validation would be
  belt-and-suspenders (a bad `errata_id` would still be caught the first time anything
  reads that segment), and neither gap named it — not added.
- **`tools/check_harness_accessor_only.py`'s stale docstring** ("NO EXHAUSTION CHECK
  YET"), already flagged as cosmetic doc drift in `05-VERIFICATION.md`'s own
  Anti-Patterns table — not touched; out of scope for these two gaps.
- **The three `human_verification` items** in `05-VERIFICATION.md`'s frontmatter
  (`budget_allowance=5` sizing rationale, the first manifest's 3-of-7-day scope
  decision) are project-level tradeoffs, not code defects — not addressed here, and
  Gap 1's own third `human_verification` item (whether CI-red blocks Phase 6) is now
  moot: CI is green.
- **`05-VERIFICATION.md` itself** was untracked when this fix started (a
  verifier-authored artifact never committed) — included in this same final commit
  alongside this report, so both land together rather than leaving a report that
  cites an uncommitted document.
