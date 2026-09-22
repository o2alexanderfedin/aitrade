---
phase: 05-fold-harness-overfitting-controls
verified: 2026-09-22T12:30:00Z
status: gaps_found
score: 4/6 must-haves verified
overrides_applied: 0
gaps:
  - truth: "CI is green on the pushed branch (05-VALIDATION.md's own explicit pre-verify-work sign-off gate)"
    status: failed
    reason: "Every GitHub Actions CI run on feature/phase-05-fold-harness-overfitting-controls that contains real Phase-5 code has FAILED (5/5: 05-00, 05-04, 05-06, 05-03, 05-07 commits). Checked both the oldest (35694486557, 05-00) and newest (35722771928, 05-07) failing logs: identical root cause both times -- `data.capture.config.DataRootError: ... has only 12.4{8,9} GiB free, below the required 50.00 GiB free`. `develop` (through Phase 4) is 100% green on the same CI job; Phase 5 is the first phase whose own new tests reach `tracking.mlflow_utils.start_tracked_run` (via `harness.budget.record_look`, called by every val/oof_block materialization) with its default `min_free_gb=DEFAULT_MIN_FREE_GB=50.0` (borrowed from `data.capture.config`, sized for capture's own disk-safety margin, not a tiny sqlite MLflow store). The shared GH Actions runner has ~12.5 GiB free, so this is deterministic and structural, not a flake. The project ALREADY HAS an established convention for this: `tests/lockbox/test_token_one_look.py`, `tests/tracking/test_mlflow_utils.py`, and `tests/dq/test_pause_enforcement.py` all pass `min_free_gb=0.0` explicitly into `start_tracked_run`-family calls to make them CI-safe. `harness/budget.py`'s own docstring says it 'copies data.lockbox's exact shape' but missed this part of the shape: `harness/accessor.py::materialize` has NO `min_free_gb` parameter at all, so even a test that wanted to lower it cannot, without monkeypatching -- the passthrough was never threaded from `materialize` to `budget.record_look`'s own `min_free_gb` keyword. `grep -n min_free_gb mvp/tests/harness/*.py` returns nothing: no Phase-5 test adopted the existing convention."
    artifacts:
      - path: "mvp/harness/accessor.py"
        issue: "materialize() has no min_free_gb parameter to thread through to budget.record_look, unlike data.lockbox's own access functions"
      - path: "mvp/harness/budget.py"
        issue: "record_look(..., min_free_gb: float = DEFAULT_MIN_FREE_GB) inherits capture's 50 GiB threshold with no test-time override path from the accessor"
    missing:
      - "Add a min_free_gb passthrough to materialize() (and thread it through record_look), then have tests/harness's fixtures pass min_free_gb=0.0 -- the same convention tests/lockbox, tests/tracking, and tests/dq already use -- so CI's constrained disk stops tripping every MLflow-tracked-run test"
  - truth: "Errata masking (D-05-20) happens automatically for every val/oof_block look, per spec.md's own 'Fold harness' section: \"the harness masks exactly those cells to null at read time\""
    status: failed
    reason: "harness.accessor.materialize(..., errata_cells: list[dict] | None = None) does NOT read manifest['errata_id'] and resolve it against the errata registry. Gate 6 is `errata.mask_errata_cells(df, errata_cells or [])` -- if a caller omits errata_cells (the documented default), masking silently no-ops even though the manifest correctly names a real errata_id. The real committed manifest's errata_id (22190ad9...) covers 249 cells, all on 2026-09-12/13 (222+27) -- inside the `train` entry's range, which is exactly where the five real `oof_block_0..4` entries live (they partition `train`). A future caller materializing an oof_block with the default signature gets the 249 fabricated-zero label cells UNMASKED, silently. No SUMMARY (05-04, 05-07) discloses this as a deliberate scope choice; spec.md states the opposite (automatic masking) as settled fact. All existing tests pass because every test call site supplies errata_cells explicitly -- none tests the documented default/auto-resolve path, so the gap is real but currently latent: no real look has been recorded yet against the real manifest, confirmed via a live look_count('97964cb2...', 'val'/'oof_block_0', ...) query against the canonical MLflow store returning 0."
    artifacts:
      - path: "mvp/harness/accessor.py"
        issue: "materialize()'s gate 6 never reads manifest['errata_id']; errata_cells is 100% caller-responsibility with a silent empty-list default"
      - path: "mvp/spec.md"
        issue: "\"Fold harness\" section (around line 426-433) states masking happens unconditionally 'at read time', which is not what the code does"
    missing:
      - "materialize() should default to resolving manifest['errata_id'] against the errata registry (hash-verified, same pattern as read_segment_manifest) when the caller doesn't supply errata_cells, OR spec.md/D-05-20 should be corrected to state the caller-supplied contract explicitly, with every real look-issuing caller shown to always supply it"
deferred: []
human_verification:
  - test: "Decide whether the GH Actions disk-space mismatch (Gap 1) blocks proceeding to Phase 6, or is an accepted, tracked infra limitation until the min_free_gb passthrough fix lands."
    expected: "Either the min_free_gb fix lands before Phase 6 starts consuming the accessor for real, or an explicit override entry here accepts the current CI-red state with a remedy plan."
    why_human: "Infra/CI configuration and risk tolerance (how much CI-red is acceptable, and for how long) is a project-level, not code-level, decision."
  - test: "Confirm budget_allowance=5 in the real committed manifest is an accepted placeholder going into Phase 6/7."
    expected: "A sizing rationale before real experiments start against this manifest, or explicit acceptance of the placeholder as-is."
    why_human: "05-07-SUMMARY.md's own words: 'no Optuna sweep or other quantitative sizing informs it' -- an experiment-design call, not a code-correctness question."
  - test: "Confirm the first real segment manifest's 3-of-7-day scope (2026-09-12..14 only, out of the 7 built days already on disk at issuance time) should stand as Phase 5's committed artifact, or whether a second, wider manifest should be issued now."
    expected: "A decision, not a fix -- the current manifest is internally consistent and its reason string traces to 05-07-PLAN.md's own deliberate scope text ('this phase's own deliverable uses the ORIGINAL three days only'), just not restated in the SUMMARY."
    why_human: "A real data/experiment-design tradeoff (train/val window width vs. held-out planning), not a defect."
---

# Phase 5: Fold Harness & Overfitting Controls Verification Report

**Phase Goal:** A fold harness that owns time — every split, look, and failure is a tracked artifact before any model trains
**Verified:** 2026-09-22T12:30:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

**Not done, stated plainly:** did not mutation-test the issuance-time overlap-with-an-exhausted-segment refusal (D-05-14's second half) myself — relied on the existing passing test; did not materialize `val` or any `oof_block` from the REAL committed manifest (doing so would spend real, irreversible budget against the real MLflow store, which read-only verification must not do); read 2 of 5 failing CI logs (oldest and newest — both identical root cause, treated as representative of all 5); did not independently re-derive all 23 D-05-01..23 decisions from first principles — spot-checked the higher-risk ones (D-05-04, D-05-14, D-05-15, D-05-20, D-05-21) and cross-read the rest against 05-07-SUMMARY.md's own traceability table.

## Goal Achievement

### Observable Truths

| # | Truth (roadmap Success Criterion) | Status | Evidence |
|---|---|---|---|
| 1 | 5-segment walk-forward split with embargo gaps produces segment manifests stored as data that downstream artifacts reference | ✓ VERIFIED (with an honest scope note) | `layout="5seg"` is fully implemented, validated (`_validate_5seg`, geometric refusals), and issued end-to-end through the real `issue_segment_manifest` path in tests (`tests/harness/test_segments.py::test_default_5seg_fixture_covers_and_is_non_starved`, `test_5seg_refuses_wrong_names_or_roles`, using the production issuer, not a mock). The ONE real, git-committed segment manifest (`97964cb2...`) uses `layout="compressed_3seg"`, per D-05-06's documented default-for-a-thin-pool decision — no real 5seg manifest is committed. This matches D-05-06's explicit text ("The 5-segment layout is implemented, tested on fixtures that have enough span to fill five segments, and selectable by name") and is not a gap; it is disclosed and structurally sound. |
| 2 | Compressed 3-segment fallback selectable per run, with the choice and reason recorded in MLflow | ✓ VERIFIED | Read `harness/accessor.py::materialize`: every `val`/`oof_block` look tags `fold_config` (manifest's `layout`) and `fold_config_reason` (manifest's own reason string) onto the MLflow run BEFORE calling `budget.record_look`, additively, never touching `MANDATORY_TAG_KEYS`. Confirmed by direct source read at `harness/accessor.py:180-193`. `tests/harness/test_accessor.py::test_materialize_tags_fold_config_and_reason_on_every_look` passes. The real manifest's own `fold_config_reason`: `"compressed layout selected: a 3-day pool starves a 5-segment layout (D-05-06)"` — traced to a deliberate 05-07-PLAN.md scope decision to issue the first real manifest against only the "original three days," even though a wider 7-day pool existed on disk by issuance time (see human_verification #3). |
| 3 | Every validation look increments the selection-bias budget in MLflow; budget exhaustion forces a fresh window | ✓ VERIFIED | `harness/budget.py::look_count` queries MLflow first, refuses an uninitialised/non-canonical store before constructing any client, and has no try/except around `search_experiments`/`search_runs` — propagation holds by construction (confirmed by source read; the dedicated exception-propagation test only covers the "no mlflow.db" path, not a live query-raise). Ran a live, read-only, second-process query against the real canonical MLflow store: `look_count('97964cb2...', 'val'/'oof_block_0', tracking_root=<real root>)` both return `0` (no real look yet — correct, since no model has trained). **Mutation-proved myself** (throwaway clone, restored, hash-verified before/after): removing `record_look`'s `if spent >= budget_allowance: raise` makes `test_look_count_reaching_allowance_refuses_the_next_look` fail red exactly as claimed, then passes green restored. Issuance-time overlap-with-an-exhausted-segment refusal (D-05-14's other half) is exercised by a passing test (`test_issuance_self_discovers_existing_manifests_and_refuses_overlap`) but not independently mutation-tested by me — see "Not done" above. |
| 4 | Queryable negative-result log records failed configs | ✓ VERIFIED | `harness/negative_log.py` + `tools/harness_negative_log_cli.py` are real. Ran the CLI (`./.venv/bin/python3 -m tools.harness_negative_log_cli`) against the real canonical MLflow store: exits 0, prints nothing (no negative results recorded yet — correct, no model has run). `pytest tests/harness/test_negative_log.py` (9 tests) passes, including fingerprint-via-`compute_manifest_id`, re-run-warns-not-refuses, and query-propagates-a-failure. 05-03-SUMMARY.md's own honestly-disclosed near-miss (a first mutation attempt was accidentally equivalent, caught and replaced with one that genuinely diverges) increases confidence in this area's mutation discipline. |
| 5 (found, not claimed) | CI is green on the pushed branch (05-VALIDATION.md's own sign-off gate) | ✗ FAILED | `gh run list --branch feature/phase-05-fold-harness-overfitting-controls` shows 5/5 runs containing real Phase-5 code FAILED; `develop` through Phase 4 is 100% green. Root cause read directly from both the oldest and newest failing job logs (identical `DataRootError`, deterministic). See Gaps. |
| 6 (found, not claimed) | Errata masking (D-05-20) is automatic at read time, per spec.md | ✗ FAILED | `harness/accessor.py::materialize`'s `errata_cells` parameter defaults to `None`→`[]`; `manifest["errata_id"]` is never read by the accessor. spec.md's "Fold harness" section states unconditional masking "at read time." See Gaps. |

**Score:** 4/4 roadmap Success Criteria structurally verified; 2 additional gaps surfaced by running the system that no SUMMARY discloses.

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `mvp/harness/segments.py` | Issue/read/validate segment manifests (5seg, compressed_3seg) | ✓ VERIFIED | `issue_segment_manifest`, `read_segment_manifest`, `_validate_segments`, `_derive_purge_embargo_fields`, `_derive_admission_counts` all real, exercised, and independently re-run against the real committed manifest |
| `mvp/harness/accessor.py` | The one `load_features` wrapper; ordered gates 1-7 | ⚠️ PARTIAL | Gates 1-5, 7 verified real and wired (held_out refusal, purge/embargo, budget). Gate 6 (errata) is real code but not self-contained — see Gap 2. No `min_free_gb` passthrough — see Gap 1 |
| `mvp/harness/budget.py` | MLflow-first durable look counter, exhaustion refusal | ✓ VERIFIED | Live-queried against real store; mutation-proved exhaustion path |
| `mvp/harness/negative_log.py` + `tools/harness_negative_log_cli.py` | Fingerprinted negative-result log, queryable | ✓ VERIFIED | CLI run live against real store; 9/9 tests pass |
| `mvp/harness/row_admission.py` | Stale-book admission policy, 5s threshold | ✓ VERIFIED | Independently reproduced the 29,058/40/29,018 gap-window split against the real 09-14 partition — exact match to 05-04-SUMMARY.md's disclosed, honestly-corrected finding |
| `mvp/harness/errata.py` | 249-cell errata computation + masking | ✓ VERIFIED (function itself) | `compute_errata_cells`/`mask_errata_cells` both real and correct in isolation; the wiring gap is in the caller (accessor.py), not this module |
| `mvp/harness/kfold.py` | Purged+embargoed inner k-fold OOF blocks | ✓ VERIFIED | Real manifest's 5 oof_block entries partition `train` exactly (block boundaries chain end-to-start, `block_0.start==train.start`, `block_4.end==train.end`) |
| `mvp/harness/holdout_declare.py` | `--dry-run` declaration tool, never calls chmod | ✓ VERIFIED | Ran `scripts/holdout_declare_dry_run_real_lake.py 2026-09-14` against the REAL lake: reports would-move for both `D` and `D-1` partitions, 701 lake files / 271 registry files unchanged before/after. Source-grepped: no `chmod`/`os.chmod` call anywhere in `harness/holdout_declare.py` or `data/lockbox.py` |
| `mvp/tools/check_harness_accessor_only.py` (D-05-15 tripwire) | Static scan, 4 sanctioned files, 19th hook | ✓ VERIFIED (functionally) — ⚠️ stale docstring | Scanned real repo green; self-tests pass (9/9); `test_kfold.py` confirmed as the disclosed 4th sanctioned file with a real reason. Docstring at line 20 still says budget.py has "NO EXHAUSTION CHECK YET — a later plan's job," which is now false (budget.py's own docstring confirms exhaustion IS implemented, landed in a commit after this file's). Cosmetic doc drift, does not affect scanner behavior. |
| `data/lake_registry/segments/97964cb2....json`, `data/lake_registry/errata/22190ad9....json` | First real, committed manifests | ✓ VERIFIED | Resolved through `read_segment_manifest` (self-hash re-verified); errata cell count independently recomputed as exactly 249 (180 `ret_1s_mid` + 69 `ret_10s_mid`, 0 elsewhere); guardrails (`check_manifest_append_only`, `check_manifest_id_integrity`) both pass against them; `check_no_manifest_rewrite` correctly excludes them (131 of 133 manifests checked, not 133) |

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| Segment manifest `upstream_feature_manifest_ids` | `features.tier.load_features` | `materialize()` step 3 | ✓ WIRED | `resolve_manifest`'s sha256 re-verification runs on every read (confirmed via `features/tier.py` source, calls `store.resolve_manifest`) |
| `manifest["layout"]`/`["fold_config_reason"]` | MLflow run tags | `materialize()` step 7 → `budget.record_look` | ✓ WIRED | Confirmed via source read and passing test |
| Look materialization | MLflow budget counter | `budget.record_look` | ✓ WIRED, mutation-proved | See Truth 3 |
| `manifest["errata_id"]` | `errata.mask_errata_cells` | `materialize()` gate 6 | ✗ NOT WIRED | The manifest names the id; the accessor never resolves it. See Gap 2 |
| `check_harness_accessor_only`/`check_manifest_append_only`/`check_manifest_id_integrity` | pre-commit + CI | byte-identical command strings | ✓ WIRED | Verified byte-identical `entry:`/`run:` strings in `.pre-commit-config.yaml` and `.github/workflows/ci.yml`; `tests/tools/test_ci_pre_commit_parity.py` (3/3) passes. 19 hooks are configured (`grep -c '- id:' .pre-commit-config.yaml`); 18 execute under `pre-commit run --all-files` (all Passed) — the 19th, `check-no-manifest-rewrite-full`, is `stages: [pre-commit-only default]`... correctly `stages: [pre-push]` and does not run at that invocation. This exact 18-vs-19 distinction is independently disclosed and explained in 05-03-SUMMARY.md ("Pre-commit Hook Count") — corroborated, not merely cited. |
| Phase 5 test suite (via `start_tracked_run`) | GitHub Actions `guardrails` job | `git push` | ✗ NOT WIRED (green) | See Gap 1 |

### Data-Flow Trace (Level 4)

Materialized `train` from the real committed manifest directly (read-only, no MLflow writes since `train` is not a look role): returned 11,012,833 real rows with real feature/label columns and a real `etime` range spanning 2026-09-12→2026-09-13. Row-conservation check against the manifest's own recorded fields is exact: `4,193,137 (09-12) + 6,864,853 (09-13) = 11,057,990` raw → admission is computed at issuance over `train`'s own full declared interval (which happens to span both entire days here) → `11,001,926 admitted + 221 stale + 55,843 undefined = 11,057,990` → minus `45,157` purged `= 11,012,833`, matching the live `materialize('train')` output to the row. (The accessor's gate-order note is specifically about *stale-book age* being computed on the full pre-slice frame, not about counts generally — the two numbers coincide here only because `train`'s declared interval already covers both full days.) `val`'s admission-counted total (`11,291,363+32,330+0=11,323,693`) is exactly one less than the real 09-14 partition's own `row_count=11,323,694` — independently confirmed the cause: `val`'s `end_ns` equals the partition's true max `etime` exactly, and the half-open `< end_ns` filter therefore excludes that one boundary row. This is a real, disclosed (05-07-SUMMARY.md "Issues Encountered") design property of "coverage cap = inclusive max etime, slice = exclusive end," not an undisclosed filter bug.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Real committed manifest resolves and self-verifies | `harness.segments.read_segment_manifest(registry_root, mid)` | Returns full body, hash matches | ✓ PASS |
| Real `train` materializes real rows | `harness.accessor.materialize(mid, 'train', ...)` | 11,012,833 rows, real columns | ✓ PASS |
| Negative-result CLI runs against the real store | `./.venv/bin/python3 -m tools.harness_negative_log_cli` | exit 0, no rows (expected — none recorded yet) | ✓ PASS |
| Holdout dry-run against the real lake | `./.venv/bin/python3 -m scripts.holdout_declare_dry_run_real_lake 2026-09-14` | Names correct `D`/`D-1` manifests; 701 lake + 271 registry files unchanged | ✓ PASS |
| Stale-book gap reproduction (D-05-21) | Direct `row_admission.stale_book_age_ns` on the real 09-14 partition | 29,058 gap rows / 40 admitted / 29,018 excluded — exact match to disclosed finding | ✓ PASS |
| Errata cell count (D-05-20) | `Counter` over the real committed errata manifest | 180 `ret_1s_mid` + 69 `ret_10s_mid` = 249, 0 elsewhere | ✓ PASS |
| Live budget query, second process | `harness.budget.look_count(...)` against real MLflow store | 0 looks for `val` and `oof_block_0` | ✓ PASS |
| CI status on the pushed branch | `gh run list --branch feature/phase-05-...` | 5/5 real-code runs FAILED (`DataRootError`) | ✗ FAIL |

### Probe Execution

No `scripts/*/tests/probe-*.sh` convention found in this project; no probes declared in the Phase 5 PLAN/SUMMARY set. Skipped — not applicable to this phase's tooling style (guardrail scripts + pytest are the equivalent mechanism, both exercised above).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|---|---|---|---|---|
| EVAL-01 | 05-01, 05-02, 05-07 | 5-segment split + embargo; manifests as data | ✓ SATISFIED (with disclosed scope note) | See Truth 1 |
| EVAL-02 | 05-06 (default), 05-07 | Compressed fallback selectable, reason in MLflow | ✓ SATISFIED | See Truth 2 |
| EVAL-03 | 05-01, 05-03, 05-06 | Budget tracked, looks counted, exhaustion forces fresh window | ✓ SATISFIED | See Truth 3 |
| EVAL-04 | 05-03 | Negative-result log | ✓ SATISFIED | See Truth 4 |

REQUIREMENTS.md marks all four `Complete` as of Phase 5. This verification confirms the mark is earned for the four literal requirement texts; the two gaps found (CI red, errata auto-resolution) are real but sit slightly outside the four requirements' literal wording (CI-green is a process gate from 05-VALIDATION.md, and D-05-20's automatic-masking claim is a spec.md promise beyond EVAL-01's literal text) — reported as gaps against the phase's OWN stated contract, not as EVAL-0x non-satisfaction.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|---|---|---|---|---|
| `mvp/tools/check_harness_accessor_only.py` | ~20 | Stale docstring claim ("NO EXHAUSTION CHECK YET") contradicted by `harness/budget.py`'s own current docstring | ⚠️ Warning (doc drift) | Cosmetic only — does not affect the scanner's actual behavior or coverage |
| `mvp/harness/accessor.py` | 45, 173 | `errata_cells: list[dict] | None = None` silently no-ops without resolving `manifest["errata_id"]` | ⚠️ Warning (key link not wired; drives `gaps_found`) | A future default-argument call on a val/oof_block segment whose errata cells fall in range returns unmasked fabricated data |
| `mvp/harness/accessor.py`, `mvp/harness/budget.py` | — | No `min_free_gb` passthrough from `materialize()` to `record_look`; no test overrides it | 🛑 Blocker (drives `gaps_found`) | Every real look on CI's shared runner fails deterministically |

No `TBD`/`FIXME`/`XXX` markers found in any Phase-5-modified file (`grep -n -E "TBD|FIXME|XXX" mvp/harness/*.py mvp/tools/check_harness_accessor_only.py mvp/tools/check_manifest_append_only.py mvp/tools/harness_negative_log_cli.py mvp/data/lockbox.py mvp/data/holdout.py mvp/scripts/holdout_declare_dry_run_real_lake.py` returned nothing).

### Human Verification Required

See `human_verification` in the frontmatter for the three items (CI-red acceptance/fix, `budget_allowance=5` sizing, first-manifest 3-of-7-day scope) — each is a project-level tradeoff decision, not a code-correctness question, so none of them alone would flip `status` away from `gaps_found` (the two frontmatter `gaps` already do that).

### Gaps Summary

Two gaps were found by running the system, neither claimed or disclosed by any SUMMARY:

1. **CI has never been green for Phase 5's real code** (5/5 runs failed, oldest and newest logs both show the identical, deterministic `DataRootError`). The shared GH Actions runner's ~12.5 GiB free disk cannot satisfy `DEFAULT_MIN_FREE_GB=50.0`, inherited from `data.capture.config` into every MLflow-tracked-run call `harness.budget.record_look`/`harness.negative_log` makes. The project already has an established fix pattern (`min_free_gb=0.0` in test call sites, used by `tests/lockbox`, `tests/tracking`, `tests/dq`) that Phase 5's own tests never adopted — and `materialize()` doesn't even expose a passthrough to do so without monkeypatching. This directly violates 05-VALIDATION.md's own explicit "CI green on the pushed branch" pre-verify-work gate.
2. **`harness.accessor.materialize` does not auto-resolve a segment manifest's own `errata_id`** — masking is 100% caller-supplied via an optional argument that silently no-ops when omitted (the documented default). spec.md's "Fold harness" section states the opposite ("the harness masks exactly those cells to null at read time") as settled fact. Currently latent (no real look has consumed the real manifest yet, confirmed via a live MLflow query returning 0 looks spent), but load-bearing: the real manifest's 249 errata cells sit inside the real `oof_block_0..4` ranges, exactly where a real k-fold OOF look would read them.

Everything else independently verified strongly: all four roadmap EVAL-0x Success Criteria hold structurally (1059/1059 full local suite, 18/19 pre-commit hooks executed and passed locally — 19th is pre-push-stage by design, matching 05-03-SUMMARY.md's own honest hook-count note — 3/3 self-run mutation spot-checks genuinely red-then-green, 2 real-lake reproductions matching disclosed findings to the row, live queries against the real MLflow store and real lake). The 23 D-05-01..23 decisions are each traceable to a real artifact or test in 05-07-SUMMARY.md's own traceability table; spot-checks against several of the higher-risk ones (D-05-04 purge/embargo, D-05-14 exhaustion, D-05-15 tripwire scope, D-05-20 errata count, D-05-21 admission threshold) independently corroborate the table.

---

_Verified: 2026-09-22T12:30:00Z_
_Verifier: Claude (gsd-verifier)_
