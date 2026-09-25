---
phase: 07-regression-track-vertical-slice
plan: 03
subsystem: models
tags: [protocol, runtime-checkable, predictor-id, content-addressing, test-fixture, perfect-foresight, r-squared]

# Dependency graph
requires:
  - phase: 07-01
    provides: "tests/models/ with a conftest whose autouse fixture SETS the tracking-root env var to tmp_path -- every test in this plan depends on that repair to stay off the canonical MLflow store"
  - phase: 05-walk-forward-harness
    provides: "harness.segments.issue_segment_manifest, harness.kfold, harness.accessor.materialize, harness.purge_embargo's two constants -- the fixture manifest is issued and read through the real ones, never a hand-written body"
  - phase: 06-simulator
    provides: "sim.arrays.sim_arrays, sim.kernel.run_sim_checked and the symmetric floor/ceil quantisation the fixture's one-tick spread is built for"
  - phase: 04-feature-tier
    provides: "features.tier's writer (write_feature_partition, issue_feature_manifest) and FEATURE_ROW_SCHEMA"
provides:
  - "models/protocol.py -- FitInputs, PredictInputs, two @runtime_checkable Protocols, and predictor_from_artifact over a registry Phase 8 extends by adding an entry"
  - "models/predictor_id.py -- D-07-14's five-field recipe hash through data.store.compute_manifest_id, with per-type coercion of numpy scalars"
  - "tests/models/test_protocol.py -- the isinstance conformance check that makes the Protocol load-bearing, plus the AST signature-token rule"
  - "tests/fixtures/model_span.py -- a features partition whose labels are DERIVED from its own price path, and a fixture-scale compressed_3seg manifest over it"
  - "tests/models/test_fixture_rig.py -- six measured, printed properties every later Phase-7 test leans on"
affects: [07-04, 07-05, 07-06, 07-07, 07-08, 07-09, 07-10, 07-11, "Phase 8's LightGBM and transformer tracks"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "typing.Protocol + @runtime_checkable as a NEW project convention (the repo had zero Protocols and zero ABCs), made load-bearing by an isinstance test that also proves the check DISCRIMINATES -- a complete stub passes, the same stub minus one member does not"
    - "A no-library-in-the-interface rule enforced over the AST's parameter and annotation nodes, never over the file's text -- so the docstring stays free to name sklearn/epochs/early_stopping, which it must in order to state the constraint at all"
    - "A fixture built price-path-first, labels derived from it, features derived from the label with CALIBRATED noise: R^2 = A/(A + noise_scale^2) in population, so the knob moves along a known curve instead of by trial and error"
    - "Both bounds on every fixture property that has two. An upper bound on achievable R^2 is what stops a fixture at 0.85 passing every downstream gate while proving nothing"

key-files:
  created:
    - mvp/models/__init__.py
    - mvp/models/protocol.py
    - mvp/models/predictor_id.py
    - mvp/tests/models/test_protocol.py
    - mvp/tests/models/test_predictor_id.py
    - mvp/tests/fixtures/model_span.py
    - mvp/tests/models/test_fixture_rig.py
  modified: []

key-decisions:
  - "A file the plan did not list was added: mvp/tests/models/test_protocol.py. The plan's own objective is that 'a Protocol with no test is decorative', and mypy is not one of the 18 hooks -- without a committed conformance test the plan would ship the anti-pattern it was written to avoid. The plan's verify command checks four signatures once, in a shell one-liner that no commit re-runs."
  - "The signature rule is enforced over ast parameter and annotation nodes, NOT with the _docstring_nodes exemption helper the plan named. Descending into args/returns only never reaches a docstring (the first ast.Expr of a scope body), so there is nothing to exempt; it DOES reach ast.Constant, because a string-quoted forward-reference annotation is one, and the anti-vacuity test depends on that. The counterpart test asserts the docstring DOES contain the forbidden words."
  - "The plan's Task 3 mutation ('shrink rows so one OOF block is empty') cannot reach test 1: the train entry is 18,000 rows wide by construction, so shrinking rows is refused by an earlier gate. Recorded as such, and a second mutation that DOES empty exactly one block was added."
  - "predictor_id COERCES numpy scalars rather than refusing them (the plan offered either, and asked for one behaviour stated in the docstring). Coercion is per-type: np.int64(2) becomes 2 and not 2.0, because a blanket float() would silently give degree: 2 the id of a degree: 2.0 nobody wrote."
  - "The fixture uses a CONSTANT one-tick spread rather than the plan's 'one or two ticks'. At a one-tick spread the mid is always a half tick -- the real pool's own condition 98.8% of the time -- and mid * (1 + ret) reconstructing a future mid then cannot straddle an integer tick boundary under the kernel's floor/ceil rule."
  - "FCST-01 and FCST-04 are NOT marked complete, following 07-01's precedent. Declaring the frozen-predictor interface is FCST-04's floor, not its satisfaction: no estimator, no prediction table and no stored artifact exist yet."

patterns-established:
  - "Pattern: a fixture builder asserts every invariant its callers are entitled to assume ON THE CONSTRUCTED FRAME, before writing -- etime strictly ascending and unique, mid == (bid+ask)/2 exactly, both quotes surviving price_to_ticks, the trailing label rows null with finite features. No caller can obtain an unchecked fixture."
  - "Pattern: a fixture knob documents the MEASURED number it was set by, not its type. flat_fraction=0.3 exists because the 10-step displacement is exactly zero 14.66% of the time, next to correction C3's 13.83%."
  - "Pattern: the anti-vacuity counterpart of 'X does not trade' is 'and here is what does' -- measured on the same frame, in the same test."

requirements-completed: []
# FCST-01 and FCST-04 are this plan's frontmatter tags and are deliberately
# left Pending. See key-decisions.

# Metrics
duration: ~55min
completed: 2026-09-25
---

# Phase 7 Plan 03: The Trainer Boundary and the Learnable Fixture Rig

**An interface three unrelated libraries can implement without editing it, proved
by a runtime check that demonstrably rejects a near-miss, plus the first fixture
in this project on which a model can measurably fail.**

## Performance

- **Duration:** ~55 min
- **Tasks:** 3 of 3
- **Files created:** 7 (no existing file was modified)
- **Tests:** 1139 -> 1166 (+27)
- **`pytest tests/models` wall clock:** 15.7 s for 32 tests -- it runs on every commit

## What Changed

### The interface (Task 1)

`mvp/models/` is the project's first declared interface. The repo had zero
`typing.Protocol` and zero ABCs at HEAD; pluggability was function injection plus
`NamedTuple`/frozen dataclass. Two frozen dataclasses carry the data
(`FitInputs`, `PredictInputs`) and two `@runtime_checkable` Protocols carry the
behaviour (`Trainer`, `FrozenPredictor`), with `predictor_from_artifact` as a free
function dispatching on `model_class` through a `PREDICTOR_BUILDERS` dict that is
empty until 07-04.

Four constraints are written into the module docstring because they are what stops
Phase 8 editing the file: `fit` takes ONE `FitInputs` and never an
`(X_train, y_train, X_eval, y_eval)` quadruple; `FitInputs` names a cached Parquet
path and column names, never materialised arrays; per-class knobs are
hyperparameters, which is also exactly what `predictor_id` hashes; `predict` takes
already-normalised features and returns the target's unit.

`predictor_id` is literally `data.store.compute_manifest_id` over a five-key dict,
for `harness.negative_log.config_fingerprint`'s reason in its own words: two
structurally identical configs must get the same id regardless of key order. That
canonicaliser sorts nested keys, so a hyperparameter mapping built in a different
order hashes identically and no caller has to remember to sort.

### The fixture (Tasks 2-3)

`tests/fixtures/harness_span.py` -- untouched here, and byte-identical to its state
at `9bfa2e4` -- has labels `0.0001 * (i % 97)`, features `float(i % 97)`, and
prices unrelated to either. It is right for what it was built for and useless for
a model: every estimator scores R^2 near 1.0 on it and the perfect-foresight
ceiling is zero trades, so no model gate can fail.

`tests/fixtures/model_span.py` builds in the opposite order: an integer-tick random
walk first, a constant one-tick spread, `mid = (bid + ask) / 2.0` exactly, THEN
`ret_*_mid` derived from that path, THEN features derived from the label with
noise. The walk runs 600 steps past the last written row, so every written row has
a true future mid to build features from while its label is null wherever the
future mid is not itself a written row.

## How the Protocol Is Load-Bearing Rather Than Decorative

`mypy` is in CLAUDE.md's stack but is not one of the 18 pre-commit hooks, so a
Protocol on its own is documentation nothing reads. Three committed tests close
that gap, and each carries a counterpart proving it could fail:

| test | what it proves |
|---|---|
| `test_a_complete_stub_is_a_frozen_predictor_and_one_missing_to_artifact_is_not` | a four-member stub passes `isinstance`; the SAME stub minus `to_artifact` returns `False`. The check discriminates -- it is not `isinstance(x, object)` in a costume |
| `test_a_complete_stub_is_a_trainer_and_one_with_a_typo_in_fit_is_not` | `ft` instead of `fit` is rejected at registration instead of raising `AttributeError` after the first fold -- which, on a validation segment, costs an irreversible look |
| `test_issubclass_is_not_available_on_these_protocols_so_use_isinstance` | both Protocols carry non-method members, so `typing` refuses `issubclass`. Pinned so a later phase does not spend a session on it |

The no-sklearn-no-knobs rule is enforced over the AST, not the file's text, and
the distinction is the one the plan checker caught: `protocol.py`'s docstring must
SAY `sklearn`, `epochs` and `early_stopping` to state the constraint, and the
registry dispatches on keys that are literally `"sklearn.Ridge"`. So
`_signature_tokens` walks every `FunctionDef` and collects parameter names,
unparsed parameter annotations and the unparsed return annotation -- nothing else.
Descending into `args`/`returns` only, it never reaches a docstring -- which is
the first `ast.Expr` of a scope's body -- so **no docstring exemption is needed
and none was imported**; `tools/check_latest_ban.py:56`'s `_docstring_nodes`
helper would have had nothing to do. It DOES reach `ast.Constant`, because a
string-quoted forward-reference annotation is one, and the anti-vacuity test
below depends on exactly that: a string ANNOTATION is what the rule is about, a
docstring is what it must not read. Two counterparts keep the rule from passing vacuously:
the scan is fed a synthetic `fit(..., early_stopping_rounds)` /
`build(estimator: 'sklearn.linear_model.Ridge')` pair and must report exactly
`{early_stopping, sklearn}`; and a separate test asserts the docstring DOES
contain all five words, so stripping the prose to satisfy a naive grep fails.

The per-estimator conformance test D-07-27 asks for belongs to 07-04 onward --
there is no estimator yet. What this plan owed was the mechanism those tests will
rely on, checked.

## The Measured Fixture Numbers

Every one of these is printed by `tests/models/test_fixture_rig.py` and asserted
there. Later plans build on them.

| property | fixture | real pool, for scale |
|---|---|---|
| admitted rows | `val` 1,799; each `oof_block_*` 3,600 | n/a (fixture scale by design) |
| zero point mass of `ret_10s_mid` | **14.6927%** on `val`, **14.6556%** across the five blocks | 20.0% pool, 13.83% Option A `val` (C3) |
| perfect foresight on `val` | **98 trades / +311 closed ticks / +$0.031100** | 9,946 / 1,120,460 / +$112.05 (C4) |
| `pred = mid` | **0 trades** | 0 trades, measured (C4) |
| `pred = mid + ONE tick` | **0 trades** | -- (see below) |
| one tick beyond the QUOTE, alternating side | **1,799 of 1,799 rows**, -1,849 ticks, -$0.184900 | -- |
| 3-feature OLS on 4 blocks, scored on the 5th | **R^2 = +0.027806** | +0.013 to +0.048 train-internal |
| constant at the train mean, same rows | **R^2 = -3.072175e-03** | zero skill by construction (D-07-32) |

### The R^2 band, and why the ceiling is the bound that matters

**Achieved: +0.027806. Plan's band: `0 < r2 < 0.5`. Calibration band asserted:
`0.01 <= r2 <= 0.05`. Real train-internal splits: 0.013 to 0.048.**

The fixture is calibrated, not groped for. With `x_i = a_i z + s e_i` for
independent unit-normal `e_i` and `z` the unit-variance label, the population R^2
of the three-feature linear fit is `A / (A + s^2)` where `A = sum(a_i^2)` -- second
moments only, so it holds whatever the label's distribution, and this label has a
15% point mass at zero. At the default coefficients `A = 1.0^2 + 0.5^2 + 0.3^2 =
1.34` and `s = 6.0`, that is `1.34 / 37.34 = 0.035886`; a held-back 3,600-row block
measures +0.027806. So `noise_scale` moves the fixture along a known curve.

A ceiling of `< 0.9` would have been nearly useless: a fixture sitting at 0.85
passes every downstream gate while looking nothing like the real data, and every
"the model learned something" test built on it would be measuring the fixture.

### The half-tick trap, measured

`pred = mid + ONE tick` trades **zero** times. At a one-tick spread the mid sits
half a tick above the bid, so `mid + 1 tick` is half a tick above the ASK, and the
kernel's `floor(pred_ticks) > ask_ticks` long trigger rounds it back down onto the
ask. "One tick beyond" has to mean one tick beyond the QUOTE, and the test that
proves the fixture can trade uses `ask + 1 tick` / `bid - 1 tick` on alternating
rows -- 1,799 flips in 1,799 rows. It loses $0.18 doing it, which is the right
answer for crossing the spread every row, not a defect.

### Geometry, derived rather than asserted against a magic number

`train = [0, 18,000 s)`, `val = [18,000 s, 19,799 s)`, `held_out` the zero-width
D-05-16 sentinel at 19,799 s, `k = 5`. The builder derives the starvation budget:
a 3,600 s block excludes its own width plus `2 x PURGE_HORIZON_NS` (600 s each
side) plus `FOLD_EMBARGO_NS` (1 s) = 4,801 s, leaving 13,199 s = 13,199 rows. The
real `issue_segment_manifest` derivation then reported per-block training counts of
13,799 / 13,199 / 13,199 / 13,199 / 13,800 -- so `_refuse_starved_oof_blocks`
cannot fire, and the reason is arithmetic in the file rather than a number in a
comment. `budget_allowance` is 50 with its reason stated: a `tmp_path` manifest is
discarded per test, so the only thing a tight allowance could do here is make a
test fail for a budget reason instead of its own.

## Mutation Checks

Three-hash discipline throughout: hash the file, mutate, **assert the hash CHANGED
before running the suite**, run, restore, assert the hash equals the original. The
original hash of `mvp/tests/fixtures/model_span.py` is
`9d232b66cbe46a3664d468ae2ed5f13361a6da57c9a32c82d9c70cd0f2e9c43b` and it was that
value again after each restore.

**M1 -- the plan's literal mutation. `DEFAULT_ROWS: int = 19_800` -> `12_000`.**
- hashes: `9d232b66...` -> `dfebefbec74f63662cff79456966ab8277fdae15ab3ccea31972c83a14f2e78b` -> `9d232b66...`
- `pytest ...::test_every_declared_segment_receives_a_nonzero_number_of_admitted_rows` exit 1
- **but it was NOT test 1 that caught it.** A DIFFERENT, EARLIER GATE fired --
  the builder's own guard: `AssertionError: model_span fixture: rows=12000 must
  exceed train_rows=18000, or the val entry is empty before any test runs`. The
  train entry is 18,000 rows wide by construction, so shrinking `rows` can never
  empty an OOF block; it shortens the covered span, and either the builder's guard
  or `harness.segments._validate_segments`' coverage check refuses the layout
  before any block exists. The plan's stated mutation is therefore not reachable,
  which is why M2 exists.

**M2 -- the mutation that actually exercises test 1: punch out `oof_block_2`'s
whole window, keeping `etime_min`/`etime_max` intact.** The write call was changed
to `write_feature_partition(df.filter(~pl.col("etime").is_between(7_200_000_000_000,
10_800_000_000_000, closed="left")), ...)`.
- hashes: `9d232b66...` -> `6c0a264fae6cfc1ff693c528d50ae4956113735017599fe875d9c6fc1e89c15b` -> `9d232b66...`
- caught by `test_every_declared_segment_receives_a_nonzero_number_of_admitted_rows`,
  **naming the block**: `AssertionError: segment 'oof_block_2' received 0 admitted
  rows -- every later test that reads it would pass vacuously`. The assertion is
  per segment inside the loop, not an `all(...)` over the six, which is why the
  message can name the offender.
- Worth recording: nothing at issuance refused this. `_refuse_starved_oof_blocks`
  looks at each block's TRAINING row count, and block 2's training rows lie outside
  its own window, so they were unaffected. A segment with zero admitted rows is not
  an issuance-time refusal, so test 1 is the only gate that sees it.

**M3 -- `DEFAULT_NOISE_SCALE: float = 6.0` -> `0.05`, i.e. a nearly noiseless,
nearly perfect fixture.**
- hashes: `9d232b66...` -> `6bc5d33b75a86628d303a89677df7b82642e9943aa2fd7c2778cb074c6d376d6` -> `9d232b66...`
- caught by `test_the_fitted_ols_scores_inside_a_stated_band_not_a_near_perfect_r2`
  on the UPPER bound: `AssertionError: outside the plan's band:
  0.9981664971754338`. This is the only mutation that proves the `< 0.5` ceiling
  bites at all; a lower bound alone would have passed at R^2 0.998.

## Deviations from Plan

### [Rule 2 - missing critical functionality] `mvp/tests/models/test_protocol.py` added

- **Found during:** Task 1
- **Issue:** the plan's `files_modified` lists no test for `protocol.py`. Its own
  objective says "a Protocol with no test is decorative", and its verify block
  checks four signatures in a shell one-liner that no commit ever re-runs. Shipping
  that would ship the anti-pattern the plan exists to avoid.
- **Fix:** a committed test file carrying the `isinstance` conformance checks (with
  their discrimination counterparts), the AST signature-token rule (with its
  synthetic-bad-source counterpart), the docstring-presence counterpart, the
  dataclass frozen/field-shape assertions and `predictor_from_artifact`'s two
  refusals. The plan's verify command was also run by hand and reports
  `protocol signatures clean`.
- **Commit:** c3ac1a3

### [Rule 1 - the plan's enforcement mechanism could not do what it said] AST scan without a docstring exemption

- **Found during:** Task 1
- **Issue:** the plan asked for the `_docstring_nodes(tree)` helper to be copied
  from `tools/check_latest_ban.py:56` and used to skip docstrings. A scan that
  collects only `arg.arg`, `arg.annotation` and `node.returns` never reaches an
  `ast.Constant` in the first place, so the helper would be dead code that
  misdescribes the check.
- **Fix:** the narrower scan, with the reason stated in the test module's own
  docstring, plus the two counterparts that make the narrowing checkable.
- **Commit:** c3ac1a3

### [Rule 1 - the specified mutation is unreachable] M1 recorded, M2 added

- **Found during:** Task 3
- **Issue:** see M1 above -- shrinking `rows` cannot empty an OOF block.
- **Fix:** M1 was run anyway and its actual gate recorded (PATTERNS' rule: if a
  mutation does not bite where expected, say which other gate caught it first),
  and M2 punches a hole that does empty exactly one block. M3 was added because
  nothing in the plan's mutation list proves the R^2 CEILING bites.
- **Commit:** 373c93f

### [Rule 2 - a knob narrowed for a measured reason] constant one-tick spread

- **Found during:** Task 2
- **Issue:** the plan allows "a spread of one or two ticks". A two-tick spread puts
  the mid on an integer tick, where `mid * (1 + ret)` reconstructing a future mid
  can straddle a tick boundary by one ulp under the kernel's floor/ceil rule -- and
  it would have made the zero point mass depend on spread CHANGES as well as on the
  walk, muddying the one number the knob is calibrated against.
- **Fix:** a constant one-tick spread, which is also the real pool's condition
  98.8% of the time (D-07-23), with the reason in the module docstring.
- **Commit:** f7db614

### [in-scope addition] a catalogue-drift guard in the builder

`build_model_span_partition` asserts `set(FEATURE_SIGNAL_COEFFICIENTS) | {"mid"}
== set(FEATURE_COLUMNS)` before it builds anything, so a fifth catalogued feature
fails here naming itself rather than as a polars `ColumnNotFound` forty lines down.

## Known Stubs

| stub | file | why, and who resolves it |
|---|---|---|
| `PREDICTOR_BUILDERS` is `{}` | `mvp/models/protocol.py` | Deliberate and tested: this plan declares the registry and its refusal, and `test_predictor_from_artifact_refuses_an_unregistered_model_class_naming_it` asserts the empty registry fails closed rather than rebuilding a body as whatever is registered first. **Plan 07-04 registers the linear implementation.** |

## Threat Register Outcomes

| Threat ID | Disposition | How |
|---|---|---|
| T-07-09 (look budget) | mitigated | every test uses the `tmp_path` lake/registry/tracking roots from `tests/models/conftest.py`; `budget.look_count` re-read after the last commit and still 0 on all twelve segments of both manifests |
| T-07-10 (fixture labels) | mitigated | labels derived from the fixture's own price path, and the R^2 test asserts a LOWER and an UPPER bound, with M3 proving the upper one bites |
| T-07-11 (`predictor_id` spoofing) | mitigated | one canonicaliser; six distinct ids across the base and five single-field variants, so no field is ignored, and no two variants collide with each other either |
| T-07-12 (model artifacts) | mitigated | `to_artifact() -> dict` is the only serialisation in the Protocol. `grep -rn "import.*pickle\|\.dumps\|\.loads\|pickle\." mvp/models/` finds no import and no call -- its only three hits are the word `json.dumps` inside `predictor_id.py`'s prose, and `protocol.py` names `pickle`/`cloudpickle` only in the docstring that forbids them |

## Look Budget

`harness.budget.look_count` read directly against
`/Volumes/ProjectsSSD/aihedgefund/mlflow`, before the first commit and again after
the last:

| manifest | `val` | `oof_block_0..4` |
|---|---|---|
| `97964cb2...` (3-day Phase 5 reference) | 0 | 0, 0, 0, 0, 0 |
| `807125015b...` (the approved 7-day geometry) | 0 | 0, 0, 0, 0, 0 |

`harness.accessor.materialize` was never called against the real lake in this
plan. It is called 16 times inside `tests/models/test_fixture_rig.py`, every one
against a `tmp_path` lake with a `tmp_path` MLflow store.

## Out-of-Scope Honesty

**Not done, deliberately:**

- **No estimator.** No `LinearRegression`, `Ridge`, `ElasticNet` or polynomial
  ridge exists. `PREDICTOR_BUILDERS` is empty and `models/` imports no `sklearn`.
- **No prediction table, no `models/predictions.py`, no `PREDICTIONS_TIER`, no
  `lake_registry/predictors/` directory, and no guardrail extension for one.**
  D-07-22's committed predictor bodies and D-07-38's per-directory scanner loop are
  untouched.
- **No `models/metrics.py` and no `models/gates.py`.** The R^2 and closed-P&L
  helpers in `test_fixture_rig.py` are local to that test file by the plan's
  instruction; 07-07 owns the real ones.
- **No normalization artifact at the new `train_end`** (D-07-11's additive
  partition) and no MLflow run logged through `start_tracked_run`.
- **No look spent, and no segment of either real manifest materialized.**
- **FCST-01 and FCST-04 not marked complete** in `REQUIREMENTS.md`.
- **`tests/fixtures/harness_span.py` not modified** -- `git diff 9bfa2e4..HEAD` on
  it is empty; five harness test modules depend on its exact values.
- **`tests/lockbox/conftest.py`'s pop-only tracking-root defect still stands**,
  as 07-01 logged in `deferred-items.md`. No test in this plan touches it.
- **The rank IC / tie-fraction machinery (D-07-18, C2) is not built.** The fixture
  now HAS a real tie mass to feed it (14.7%), which is the prerequisite; the
  statistic itself is a later plan's.
- **`STATE.md`'s Performance Metrics table still has no row for 07-02** -- that
  plan stopped at its checkpoint before the metric call. Logged in
  `deferred-items.md` rather than filled in from another plan's SUMMARY.

## Self-Check: PASSED

Files (all seven present):
`mvp/models/__init__.py`, `mvp/models/protocol.py`, `mvp/models/predictor_id.py`,
`mvp/tests/models/test_protocol.py`, `mvp/tests/models/test_predictor_id.py`,
`mvp/tests/fixtures/model_span.py`, `mvp/tests/models/test_fixture_rig.py`.

Commits (all three in `git log`): `c3ac1a3`, `f7db614`, `373c93f`.

Suite: 1139 -> 1166 tests, `pytest tests -q` green (247 s); all 18 pre-commit
hooks passed on every commit, none with `--no-verify`.
