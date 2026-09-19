---
phase: 04-feature-label-engine
plan: 07
subsystem: feature-engine
tags: [single-code-path, byte-identity, normalization, welford, manifest-artifact, ast-tripwire, mutation-testing]

# Dependency graph
requires:
  - phase: 04-feature-label-engine/04-01
    provides: features/event_stream.py's EVENT_SCHEMA / event_arrays (read-only views) / decision_row_index (a pure function of position, which is what lets a chunk re-derive its own decision rows); data/time_ns.py's NS_PER_SECOND and TRADE_FLOW_WINDOW_NS
  - phase: 04-feature-label-engine/04-02
    provides: data/store.py's FEATURES_NORM_TIER (already named, so this plan never edited store.py) and its deliberate absence from BY_DATE_INDEXED_TIERS; features/tier.py's write-once partition-entry shape and FEATURE_COLUMNS
  - phase: 04-feature-label-engine/04-03
    provides: features/kernel.py's new_state / run_kernel_checked and the carry-in/carry-out state contract -- the reason chunked and per-row invocation are possible at all; features/reference.py's new_outputs
  - phase: 04-feature-label-engine/04-05
    provides: features/build.py and the three real feature partitions (2026-09-12/13/14) the byte-identity and normalization runs were measured against
  - phase: 03-data-layer-backfill-ingest-lockbox
    provides: data/store.py's issue_manifest / resolve_manifest / read_verified_partitions; data/capture/rotation.py's write_parquet_atomic; tools/check_lockbox_containment.py as the AST-scan shape to mirror
provides:
  - mvp/features/api.py -- THE entry point: compute_decision_rows, for_training, for_inference, for_simulation, chunk_events, event_row_stream, decision_rows_to_frame, FEATURE_PASS_SCHEMA, Z_SUFFIX, NormalizationRequiredError, LabelsNotHereError
  - mvp/features/normalize.py -- fit_normalization, fit_training_segment, expanding_z, apply_normalization, welford_std, write_normalization_artifact, load_normalization, normalization_dataset, ZeroVarianceError, NORMALIZATION_ARTIFACT_SCHEMA
  - mvp/tools/check_single_feature_path.py -- the AST tripwire, wired into both callers as the 18th hook
  - The first real features_norm artifact (manifest d1d35fbf1dbc, fit on 11,057,990 decision rows of 2026-09-12 + 2026-09-13)
affects: [05-fold-harness, 06-simulator, 08-stage-1-models]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Byte-equality across call sites is only evidence when the call sites are proven DIFFERENT: a counting wrapper asserts exact kernel-entry counts (1, n_chunks, n_rows), because a working delegation from the simulator to the batch path leaves every equality test green"
    - "The deferred last-row-of-group emitter is shared by all three call sites; the RULE is not what differs between them, the call GRANULARITY is"
    - "A train/validation boundary passed as an argument (int64 ns, inclusive) so the selection lives in the module under test -- a fit function that only ever receives training rows cannot be tested for train-onlyness"
    - "Welford (count, mean, M2) stored rather than (mean, std): sufficient to freeze, to resume an extended segment exactly, and to make the excluded-null count derivable"
    - "A guardrail whose docstring names the runtime test it defers to, so nobody can quote the scan as the control"
    - "Artifact metadata repeated on every row of a four-row Parquet rather than in a sidecar: a body that cannot answer 'what was I fit on' alone is only auditable while its manifest is at hand"

key-files:
  created:
    - mvp/features/normalize.py
    - mvp/features/api.py
    - mvp/tools/check_single_feature_path.py
    - mvp/tests/features/test_normalize.py
    - mvp/tests/features/test_api_single_path.py
    - mvp/tests/tools/test_check_single_feature_path.py
    - mvp/data/lake_registry/manifests/BTCUSDT.features_norm/d1d35fbf...json
  modified:
    - .pre-commit-config.yaml (single-feature-path hook)
    - .github/workflows/ci.yml (check_single_feature_path step, byte-identical string)
  deleted: []

key-decisions:
  - "fit_training_segment exists beyond the plan's signature list. The plan's fit_normalization(values) receives only training rows, so `fit over everything` would be a mutation with nowhere in the module to live and test_validation_data_cannot_change_the_parameters would be testing its own slicing. The boundary is now an int64-ns argument and the mutation is a one-line change to this module."
  - "for_training does NOT write the artifact, contrary to the plan's wording. A real training segment spans several days and therefore several for_training calls (the real fit consumed 11.06M rows across two partitions); a per-call writer would either write one artifact per day or recompute. It returns the fitted parameters and the caller persists them."
  - "labels=True raises instead of computing labels. A batch call site here could only produce a null-tailed variant of features/build.py's labels, which reads day D+1's quotes -- a SECOND label convention beside the correct one, which is the drift this module exists to prevent."
  - "The scanner does not sanction tools/. The watched module names appear in it as strings, and a string is not an import, so it scans itself under the same rule as everything else."
  - "The plan's mutation (d) prediction was wrong in KIND and the real outcome is stronger -- see Mutation Check Results."

patterns-established:
  - "When a mutation must be shown NOT to be caught by the obvious test, write the WORKING version of it: the first delegation mutation failed on a type error, which proves nothing about the test suite"
  - "Real chunk boundaries are not a corner case: 45 of 63 boundaries on a real day land inside an etime group"

requirements-completed:
  - "FEAT-01: one feature code path with three genuinely different call sites, byte-identical on a real 18.58M-row day AND byte-identical to the partition features/build.py already wrote, plus a static tripwire in both CI callers."
  - "FEAT-05: expanding normalization fit on the training segment only, stored as a manifest-addressed artifact, loaded and never recomputed -- proven on 22.38M real decision rows with the held-back day present in the input."
requirements-partial: []

# Metrics
duration: ~2h
completed: 2026-09-19
---

# Phase 4 Plan 07: One Entry Point and Train-Only Normalization Summary

**Hand the same day over three ways — whole, in 64 chunks, and one event at a time — and the same 6,864,853 decision rows come back with the same bytes, including against the partition that was already on disk; and the mean and standard deviation a model will divide by are now fixed before the held-back day exists, written down, and looked up.**

## Performance

- **Duration:** ~2 h
- **Tasks:** 2 (one commit each) + one commit for the real artifact + one review fix
- **Tests:** **899 before → 931 after** (+32: 12 `test_normalize.py`, 14 `test_api_single_path.py`, 6 `test_check_single_feature_path.py`)
- **Pre-commit hooks:** 17 → 18
- **Files:** 6 source/test files created, 2 config files modified, 1 manifest committed

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | Train-only expanding normalization as a stored artifact | `1745b64` | `features/normalize.py`, `tests/features/test_normalize.py` |
| 2 | One entry point, three call sites, and the import tripwire | `6a2b4eb` | `features/api.py`, `tools/check_single_feature_path.py`, `tests/features/test_api_single_path.py`, `tests/tools/test_check_single_feature_path.py`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml` |
| — | The first real normalization artifact | `084d94d` | `data/lake_registry/manifests/BTCUSDT.features_norm/d1d35fbf….json` |
| — | Review fix: the emission rule actually observed, plus the empty path | `25b2bcc` | `features/api.py`, `tests/features/test_api_single_path.py` |

## The finding that makes the equality test worth having

A `for_simulation` that collects its rows and takes the batch path produces **identical output from one kernel call instead of 240**. Under that mutation, 12 of the 13 tests in `test_api_single_path.py` stay green — every byte-equality assertion, every chunk size, the emission-rule test, the normalized-column test. The only thing that dies is:

```
E       AssertionError: the simulator entered the kernel 1 time(s) for 240 rows
                        -- it is not driving it row by row
E       assert 1 == 240
```

That is the whole reason `test_the_three_call_sites_drive_the_kernel_differently` exists, and it is the difference between a test that proves the code path is shared and a test that proves nothing. The counts are asserted EXACTLY (1, `n_chunks`, `n_rows`) rather than as "the three differ", because at chunk size 1 inference and the simulator legitimately enter the kernel the same number of times.

## RED transcripts

Both tasks were written test-first. A genuinely red COMMIT is impossible here — pre-commit runs the whole suite and `--no-verify` is forbidden — so the red state is transcribed rather than committed.

Task 1:
```
$ ./.venv/bin/pytest tests/features/test_normalize.py -x -q
tests/features/test_normalize.py:29: in <module>
    from features.normalize import (
E   ModuleNotFoundError: No module named 'features.normalize'
```

Task 2:
```
$ ./.venv/bin/pytest tests/features/test_api_single_path.py -x -q
tests/features/test_api_single_path.py:30: in <module>
    from features import api
E   ImportError: cannot import name 'api' from 'features'
$ ./.venv/bin/pytest tests/tools/test_check_single_feature_path.py -x -q
E   ModuleNotFoundError: No module named 'tools.check_single_feature_path'
```

## Mutation Check Results

### Task 1 — normalization

**(a) Fit over the whole array instead of the training slice.**

```python
-    in_train = etime <= np.int64(train_end_etime)
+    in_train = np.ones(etime.shape[0], dtype=bool)  # MUTATION (a)
```
```
FAILED test_validation_data_cannot_change_the_parameters
E  AssertionError: validation rows changed the fitted parameters -- the fit is not train-only
E  {'mid': (400, 99.72103127239643, 5.497585310182093)}
E   != {'mid': (399, 2405404.7399933897, 4.974774770095927e+19)}
FAILED test_artifact_records_its_training_segment (train_row_count 400 != 250)
```
Two tests, not one: the artifact also records the segment it was fit on, so a widened fit is visible in the metadata as well as in the numbers. Restored.

**(b) Two-pass, whole-segment centring inside `expanding_z`.** The removed block (the running Welford update) and the inserted block (one `_welford` pass over the finite rows, then a constant `(value - mean) / std`) were printed in full before running.
```
FAILED test_expanding_z_at_t_uses_only_rows_up_to_t
E  a row after t changed the normalized value at t
E  base[:61] = [nan, -0.979363…, -0.979363…, …]   (mutant, constant-ish)
E  vs           [nan,  2.453090…,  2.401354…, …]  (causal)
```
Only that test failed. `test_expanding_z_ends_at_the_same_statistics_the_fit_stores` correctly stayed green — a whole-segment centring agrees with the fit at the LAST row by construction, which is exactly why that test is not a causality test. Restored.

**(c) `apply_normalization` refits from its input.**

```python
-    count, mean, m2 = params
+    count, mean, m2 = _welford(np.asarray(values, dtype=np.float64))  # MUTATION (c)
```
```
FAILED test_inference_loads_parameters_and_does_not_refit
```
Restored.

**(d) `_enforce_dq_pause` added to `load_normalization` — the plan predicted `DQPauseError` and that prediction is wrong in kind.**

```
FAILED test_load_normalization_does_not_enforce_a_dq_pause
FAILED test_inference_loads_parameters_and_does_not_refit
FAILED test_artifact_round_trips_through_the_manifest
FAILED test_artifact_records_its_training_segment
E  dates = sorted({part["date"] for part in manifest["partitions"]})
E  KeyError: 'date'      (data/store.py:909)
```

The gate is not merely unnecessary for this tier — it is **inapplicable**. It asks each partition which DATE it covers, and an artifact's partition covers a fold. The test asserts this directly (`pytest.raises(KeyError)`) so the omission is pinned as deliberate rather than incidental.

**(d2) the counterfactual, to show the plan's prediction was right about the mechanism:** adding `"date": train_end_date` to the partition entry ON TOP of (d) produces exactly what the plan expected —

```
E  data.store.DQPauseError: DQ pause: BTCUSDT.features_norm has unacknowledged
   day(s): 2026-09-13: missing (no DQ report generated for this date …)
```

So both halves of the reasoning hold: a dated artifact partition would pause forever, and a dateless one cannot even be asked. Restored.

### Task 2 — the single path

**(a) `for_simulation` delegates to `for_training`.** See "The finding" above. The FIRST attempt at this mutation raised `ValueError` on the event-array contract (a list of row mappings is not an arrays dict) — a failure that proves nothing about the tests — so it was rewritten as a WORKING delegation that builds the arrays first. Only then is the survival of the 12 equality tests a real measurement. Restored.

**(b) `for_inference` resets the ring/accumulator state at each chunk** (`state = new_state()` at the top of the chunk loop). Five tests failed; chunk size 1000 correctly stayed green (the 240-row fixture is one chunk at that size). Measured divergence against the batch pass, 64-row chunks over the 240-row fixture, 138 decision rows:

| column | rows differing | first differing decision row | max abs diff |
|---|---|---|---|
| `etime` | 0 | — | — |
| `mid` | 1 | 78 | NaN (a chunk opening on a trade row has no prevailing book) |
| `imb_top` | 1 | 78 | NaN |
| `ofi` | 7 | 39 | NaN (`last_ofi` and `n_quotes_seen` reset) |
| `trade_flow` | **99 of 138** | 39 | 25.641 |
| `warmup` | 72 | 66 | 1 (each chunk re-enters warm-up) |

`trade_flow` is the column that diverges most widely, as the plan expected; `ofi` and `trade_flow` tie for the FIRST differing row (39) rather than `trade_flow` leading, because the reset clears the quote counter and the ring in the same instant. Restored.

**(c) `for_inference` fits its own parameters when none are given.**
```
FAILED test_inference_and_simulation_require_a_normalization_artifact
E  Failed: DID NOT RAISE NormalizationRequiredError
```
Restored.

**(d) a `features.kernel` import added to `tools/git_env.py`.**
```
scanned 148 python files under /Volumes/ProjectsSSD/aihedgefund/repo/mvp
FAIL: the feature kernel is imported outside features/ -- there is supposed to be ONE call path (features.api):
  tools/git_env.py:26: imports from features.kernel -- reach the features through features.api
exit=1
```
Reverted; the scan is green at exit 0 over all 148 files.

### Review fix — the test whose name was ahead of its assertions

`test_simulation_emits_one_group_late_and_flushes_at_the_end` drove the generator to exhaustion with `list(stream)` and then checked the first and last rows. Both are true of a batch pass, so the test asserted nothing about WHEN a decision row appears — while the SUMMARY claimed the emission rule was pinned and Phase 6 was told to build against it. Replaced by `test_simulation_emits_a_group_only_once_a_larger_etime_arrives`, which counts what the simulator has pulled from its source:

**(e) `for_simulation` consumes its source eagerly** (same rows, same bytes, same kernel-call count — only the laziness is gone):
```
FAILED test_simulation_emits_a_group_only_once_a_larger_etime_arrives
E  AssertionError: the decision row for the first etime group appeared after 60
   source rows; the rule says it appears on row 1 -- the first row with a larger etime
E  assert 60 == (1 + 1)
```
Every other test, including all the byte-equality ones, stayed green. Restored.

**(f) the empty-frame branch's dtype patches are dead code — the mutation SURVIVED.** Deleting `columns["warmup"] = np.empty(0, dtype=np.bool_)` changed nothing: `pl.Series(np.empty(0, float64), dtype=pl.Boolean)` casts an EMPTY series to any declared dtype without complaint. Rather than leave untested branching in place, the branch was simplified to one empty float array per column with the measurement recorded in a comment.

**(f2) the mutation that does bite:** returning `pl.DataFrame()` from the empty path fails `test_an_empty_stream_is_an_empty_frame_not_a_crash` on the schema assertion — so the new empty-path test is not vacuous, it is just testing the frame's shape rather than per-column dtype patches that polars never needed.

Also noted in `for_training`'s docstring: its `fit_normalization: bool` parameter SHADOWS the imported `features.normalize.fit_normalization` inside that scope. The fit routes through `fit_normalization_from_frame`, which reads the module global from its own scope; a future edit calling `fit_normalization(...)` directly inside `for_training` would call `True`.

## Real-Data Verification

Run once from `mvp/` with `./.venv/bin/python3` and `NUMBA_CACHE_DIR` outside the repo, against Plan 05's real partitions. Full transcript below is quoted verbatim from the run.

### 1. Three call sites on the real 2026-09-13 day

```
loaded + merged 18,576,995 event rows in 1.8 s
  (17,167,290 quotes + 1,409,705 trades -> 6,864,853 decision rows)
for_training : 1 batch  -> 6,864,853 decision rows in 0.8 s
64 chunks of 290,266 rows; 45 of 63 boundaries land INSIDE an etime group
for_inference: 64 calls -> 6,864,853 decision rows in 0.2 s
  training vs inference etime/decision_source_rank/decision_seq  identical
  training vs inference mid/imb_top/ofi/trade_flow/warmup        identical  (6,864,853 rows each)
for_simulation: 200,000 calls (one per row) -> 127,606 decision rows in 2.2 s
comparing the 127,605 decision rows with etime < 1789259978529000000
  training vs sim  every column identical (127,605 rows each)
```

**Which three call sites, and on what data:** one `for_training(events)` over the whole merged `(etime, source_rank, seq)`-ordered array; one `for_inference(chunk_events(events, 290_266))` whose 63 interior boundaries include 45 that fall between two rows of the same `etime`; and one `for_simulation(event_row_stream(prefix))` over the first 200,000 rows, one kernel call per row.

**Why a 200,000-row prefix and not the day:** the full 18.58M rows row-by-row is ~3 minutes of wall clock at the measured 2.2 s / 200k and proves nothing the prefix does not. It is a PREFIX from row 0, not a middle slice, because the carried state (warm-up, ring occupancy, `last_ofi`) is only correct from the partition's first row. The prefix's final `etime` group is cut in half by the slice, so the simulator's end-of-stream flush emits a row the whole-day pass would never emit there; that one group (`etime = 1789259978529000000`) is dropped from the comparison and the remaining 127,605 rows are compared.

**The three call sites are byte-identical to what is already on disk.** `for_training`'s output was compared against the 2026-09-13 partition `features/build.py` wrote in Plan 05, loaded through `load_features` (hash-verified, DQ-gated):

```
stored partition: 6,864,853 rows
  api vs stored etime / decision_source_rank / decision_seq  identical
  api vs stored mid / imb_top / ofi / trade_flow / warmup     identical
```

Without this comparison "the training path" would be two things — `build.py` driving the kernel directly, and `api.for_training` — and FEAT-01 would still be a claim about three new functions. It is one thing. `build.py` was deliberately NOT rewired to call `api` (it is not in this plan's `files_modified`, and it is the code that produced write-once artifacts); see Deferred below.

### 2. A real normalization artifact

```
  2026-09-12:  4,193,137 decision rows (manifest 1bf9af2e879d)
  2026-09-13:  6,864,853 decision rows (manifest 1f10da67ca50)
  2026-09-14: 11,323,694 decision rows (manifest fdbf58ca1def)
all three days: 22,381,684 decision rows; training boundary etime <= 1789343999976000000
fit over all three days, boundary applied: 9.1 s
fit over the training days alone:          9.5 s
parameters bit-identical with and without 2026-09-14 present: True
```

The stronger form of the FEAT-05 test: the fit was handed **all three days including the held-back one** and a boundary, and produced parameters bit-identical to a fit that never saw 2026-09-14 at all.

```
artifact manifest id : d1d35fbf1dbcbe910745fac6f0a8ab0b7192599e34c577c0b7e74381fa669e74
artifact path        : features_norm/symbol=BTCUSDT/train_end=2026-09-13/part-1789817895150639000.parquet
artifact size        : 5,568 bytes (4 rows x 13 columns)
train rows           : 11,057,990
train etime range    : (1789171200002000000, 1789343999976000000)
code_hash            : 6a2b4ebaaa5d9bed9cf500d9fba70140262a2455   (clean, no -dirty)

  feature      count        mean            std        excluded_nulls
  imb_top      11,002,147  4.61350650e-02  6.19870198e-01   55,843
  mid          11,002,147  7.70962990e+04  2.45346842e+02   55,843
  ofi          11,002,145 -2.53554375e-04  5.23639834e-01   55,845
  trade_flow   11,057,990 -1.55070986e-01  8.91707223e+00        0
```

**The excluded-null counts are a cross-check, not bookkeeping.** `mid` and `imb_top` exclude exactly **55,843** rows — precisely the count 04-05 measured for 2026-09-12's rows with no book at all. `ofi` excludes **two more**, one per day: D-04-04's "the first L1 update of a day is null, not zero", visible in an artifact fit two days later. `trade_flow` excludes none, because it is defined from the first row.

**Applied to 2026-09-14 (the held-back segment for this exercise only):**

```
  imb_top      scored 11,323,694 rows; z mean -0.067947 std 1.132787
  mid          scored 11,323,694 rows; z mean  4.313302 std 2.849635
  ofi          scored 11,323,694 rows; z mean  0.003476 std 0.924009
  trade_flow   scored 11,323,694 rows; z mean  0.034186 std 1.378877

artifact sha256 before: d0aff38813423618cf4b3409592eda68ff2d56829cea73596b0536db5989f73e
artifact sha256 after : d0aff38813423618cf4b3409592eda68ff2d56829cea73596b0536db5989f73e
unchanged: True
```

**`mid` lands four training-sigmas out, and that is the transform working, not failing.** Scoring the next day with frozen parameters puts the price level at z ≈ +4.3 with 2.85× the training dispersion, because `mid` is a LEVEL and the level moved. A model whose inputs are recomputed per-window would never see this — it would see a tidy z ≈ 0, which is precisely the leak. Phase 5/8 should read this as evidence that `mid` wants a differenced or ratio form as a model input (a NEW catalogue name, not an edit), not as a defect in the normalization.

**2026-09-14 is NOT a lockbox date and this is not a held-out look.** The holdout registry is still empty (`holdout registry: None`) and Phase 5 has not chosen a range — verified in Plan 02 and re-verified here. The day was held back by this script only, to have something to score.

### 3. No side effects

```
[capture daemon BEFORE] 72546 Wed Sep 16 21:36:29 2026
[capture daemon AFTER ] 72546 Wed Sep 16 21:36:29 2026
numba cache artifacts under mvp/: none
```

One near-miss worth recording: an earlier mutation-diagnostic script was run WITHOUT `NUMBA_CACHE_DIR` exported and wrote `features/__pycache__/kernel.run_kernel-199.py313.{nbi,nbc}` into the package. `test_no_numba_cache_artifacts_under_mvp` (Plan 01) caught it on the next full-suite run, naming both files. They were deleted individually — never `git clean` — and every subsequent script run exported the variable. The handoff note from 04-03 said exactly this would happen.

## Verification Transcript

```
$ ./.venv/bin/pytest tests -q
931 passed in 107.79s (0:01:47)

$ pre-commit run --all-files          (via the commit hook, all 18 hooks)
ruff check / ruff format --check / uv lock --check / check_pin_versions /
check_ms_to_ns_site / check_catalogue_completeness / check_latest_ban /
check_lockbox_containment / check_single_feature_path / check_numba_globals /
check_spec_diff / check_no_manifest_rewrite (x3) / check_manifest_id_integrity /
check_manifest_append_only / pytest (leakage suite) / pytest (tests)  -- all Passed

$ ./.venv/bin/python3 -m tools.check_single_feature_path
scanned 148 python files under /Volumes/ProjectsSSD/aihedgefund/repo/mvp   exit 0

$ diff <(grep 'entry: .*check_single_feature_path' .pre-commit-config.yaml | sed 's/.*entry: //') \
       <(grep 'run: .*check_single_feature_path' .github/workflows/ci.yml | sed 's/.*run: //')
(no output -- byte-identical)
```

`tests/tools/test_ci_pre_commit_parity.py` (04-06's runtime guard) passes with the new hook, so the byte-identity of the two command strings is now also asserted by a test rather than only by this `diff`.

## TDD Gate Compliance

Both tasks were written test-first and the import-time RED was transcribed above. Neither RED state is a commit: `.pre-commit-config.yaml` runs the entire suite on every commit and `--no-verify` is forbidden by this project's rules, so a red commit cannot exist here. The gate is therefore satisfied in sequence (test written → observed red → implementation → green → one commit), not in commit history.

## Deviations from Plan

**1. [Rule 2 — missing critical functionality] `fit_training_segment` added beyond the plan's function list.**
The plan specifies `fit_normalization(values: dict[str, np.ndarray])`, which receives only training rows. With that signature alone, the train/validation boundary lives in the CALLER, mutation (a) ("fit on the whole array") has nowhere in the module to live, and `test_validation_data_cannot_change_the_parameters` would be testing its own slicing. `fit_training_segment(values, etime, *, train_end_etime)` owns the boundary as an inclusive int64-ns comparison; `fit_normalization` is unchanged and is what it calls. The real-data run exercises exactly this: all three days in, one day's worth of rows out.

**2. `for_training` returns the fitted parameters; it does NOT write the artifact.**
The plan says for_training "writes them as an artifact … it does not hold them in memory". A real training segment spans several days and therefore several `for_training` calls — the real fit consumed 11,057,990 rows across two partitions — so a per-call writer would issue one artifact per day or recompute what it already had. `FeaturePass.normalization` carries the parameters out and `write_normalization_artifact` persists them, which is what the real-data run does.

**3. `labels=True` raises instead of computing labels.**
The plan's `compute_decision_rows(..., labels=False)` signature is kept as a signpost, and setting it raises `LabelsNotHereError` naming `features.build.build_features_day`. A label reads data AFTER its decision row (day D's long horizons read D+1's quotes): a streaming call site cannot see it, and a batch call site could only produce a null-tailed variant — a SECOND label convention beside the correct one, which is the exact drift this module exists to prevent.

**4. A `normalize` flag was added to all three call sites.**
The plan's test asks that inference and the simulator raise when called "with `normalization=None` while asking for normalized outputs", which requires a way to ASK. `normalize=None` (the default) means "normalize iff parameters were given"; `normalize=True` with no parameters raises `NormalizationRequiredError`. Neither streaming call site has any argument that would let it fit — `fit_normalization=True` is a `TypeError` there, and that is asserted.

**5. Mutation (d)'s outcome differs from the plan's prediction.** `KeyError: 'date'`, not `DQPauseError` — documented above with the counterfactual that produces the predicted error. The plan's reasoning was right; its predicted exception was for a partition entry shape this plan does not write.

**6. `tools/` is not sanctioned by the scanner.** The plan says "any file outside `mvp/features/` and `mvp/tests/`", and that is what shipped. An early draft sanctioned `tools/` so the scanner could name the watched modules in its own docstring; strings are not imports, so the sanction was unnecessary and was removed — the scanner scans itself under the same rule.

## Threat Model Disposition

| Threat ID | Disposition | Evidence |
|---|---|---|
| T-04-30 (silent train/infer drift) | mitigated | Byte-identity on 6,864,853 real decision rows across three call sites AND against the stored partition; exact kernel-entry counts asserted (1 / 64 / 200,000) |
| T-04-31 (normalization leakage) | mitigated | Train-only fit proven by mutating validation rows and by the real three-days-in/two-days-out run; causal transform proven by mutating future rows, with anti-vacuity counterparts for both |
| T-04-32 (artifact tampering) | mitigated | `resolve_manifest(expected_tier=FEATURES_NORM_TIER)` + per-partition sha256; a one-byte flip raises `ManifestHashMismatch` |
| T-04-33 (second call convention) | mitigated | `check_single_feature_path` in both callers, mutation-verified; 148 files scanned |
| T-04-34 (dynamic kernel import) | accepted | Not detected by design; stated in the scanner's docstring and asserted by `test_static_scan_states_what_it_cannot_see` |

## Known Stubs

None. Every function this plan created is wired and exercised on real data.

## Threat Flags

None. `lake/features_norm/` is new surface and was already in the plan's threat register (T-04-32); nothing else in this plan opens a network, auth or file-access path.

## Surprises

- **45 of 63 real chunk boundaries fall inside an `etime` group.** The "hard case" the deferred emitter exists for is the common case on a real day, not a corner — 6.86M distinct `etime`s across 18.58M rows means most boundaries land mid-group.
- **The chunked pass is FASTER than the batch pass** (0.2 s vs 0.8 s over the same 18.58M rows). The batch pass allocates full-length output arrays once (five arrays × 18.58M × 8 B ≈ 740 MB); 64 chunks allocate 64 small ones that stay in cache. The single-code-path rule costs nothing here.
- **The Welford fit over 11M rows takes 9 s in pure Python**, not the minutes a per-element Python loop suggests. No second `@njit` was added; a JIT copy of the accumulation would be a second implementation of the statistic the artifact stores.
- **`mid`'s held-back z-score is +4.3σ** — see the real-data section. This is the single most consequential number this plan produced for Phase 8.

## Issues Encountered

- The first `for_simulation`-delegates-to-`for_training` mutation failed on a type error rather than on a test assertion. A mutation that crashes proves nothing about the suite; it was rewritten to work before its survival was measured.
- A diagnostic script run without `NUMBA_CACHE_DIR` wrote two numba cache files into `features/__pycache__/`. Caught by the existing test, deleted individually.
- The background-run wrapper does not inherit `cwd` on `sys.path`; the real-data script needed `PYTHONPATH=…/mvp` to import the package. Recorded for the next plan that runs a script outside pytest.

## Next Phase Readiness — handoff notes

- **`features.build.build_features_day` still drives the kernel directly rather than calling `features.api`.** It is proven byte-identical to `api.for_training` on the real day, so nothing is wrong today — but it is a SECOND caller of `run_kernel_checked`, and the tripwire sanctions `features/` wholesale, so it cannot catch a future divergence between them. Rewiring `build.py` onto `api.compute_decision_rows` is a small, testable follow-up (the byte-identity comparison above is the acceptance test) and was left out because `build.py` is not in this plan's `files_modified` and it produced write-once artifacts.
- **Phase 6 (the simulator) conforms to `for_simulation`'s emission rule**: the decision row for `etime = t` is emitted when the first row with `etime > t` arrives, plus an end-of-stream flush. It is ONE GROUP LATE by construction. Any comparison against a batch pass must align on `etime`, never on emission index, and must drop the final partial group when the streams are cut at different points.
- **Phase 5 sizes the fold boundary with `fit_training_segment(..., train_end_etime=…)`**, an inclusive int64-ns comparison — not a date string, and not a row count. `parse_embargo` (04-06) gives the gap; this gives the fit.
- **The artifact stores Welford `(count, mean, M2)`, so a fold that EXTENDS the training segment can resume exactly** rather than refitting from `(mean, std)`. Nothing uses that yet; the state is there because re-deriving it later is impossible.
- **`features_norm` has no DQ report rows and its loader does not call the pause gate.** If a future phase wants DQ coverage for this tier it must add both a check that emits rows AND a `date` field on the partition entry — adding only the gate call pauses it forever, and adding only the date gives it a `missing` verdict.
- **Normalized columns are `<feature>_z` and are never stored.** `spec/features.toml` still declares `normalization = "none"` for all four features, and that is correct: it describes the STORED column, which is raw, which is what makes one partition reusable across folds. A stored normalized column would need a NEW catalogue name.
- **`mid` should probably not be z-scored as a model input at all** (+4.3σ on the very next day). That is a Phase 8 catalogue decision — a new name like `mid_ret_1s`, never an edit to `mid`.
- **FEAT-02 is still marked `Pending` in REQUIREMENTS.md** and looks complete on the evidence: all four catalogued features are implemented, bit-identical between the kernel and its reference twin, each carries an `information_set` entry checked against measured behaviour, and all four are written into three real partitions. This plan only owned FEAT-01 and FEAT-05, so it did not flip a requirement it was not asked about -- the phase's verification step should look at it.
- **The scan is a tripwire and the equality test is the control.** If `check_single_feature_path` is ever quoted as the reason the single-path claim holds, it is being misquoted; its own docstring says so.

## Self-Check: PASSED

- `mvp/features/normalize.py`, `mvp/features/api.py`, `mvp/tools/check_single_feature_path.py`, `mvp/tests/features/test_normalize.py`, `mvp/tests/features/test_api_single_path.py`, `mvp/tests/tools/test_check_single_feature_path.py` — all present on disk.
- `mvp/tests/features/__init__.py` — confirmed absent.
- Commits `1745b64`, `6a2b4eb`, `084d94d` all present in `git log` on `feature/phase-04-feature-label-engine`.
- Artifact `lake/features_norm/symbol=BTCUSDT/train_end=2026-09-13/part-1789817895150639000.parquet` present, 5,568 bytes, sha256 `d0aff388…f73e` matching its committed manifest `d1d35fbf…`.
- The `single-feature-path` entry and the `check_single_feature_path` run step verified byte-identical by `diff` of the extracted command strings, and by `test_ci_pre_commit_parity.py`.
- `find mvp -name '*.nbc' -o -name '*.nbi'` returns nothing.
- Capture daemon PID 72546 unchanged, same start time, never signalled; nothing written to `/Volumes/ProjectsSSD/aihedgefund/capture/`, and no existing lake partition or manifest body modified.
