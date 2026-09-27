---
phase: 07-regression-track-vertical-slice
plan: 06
subsystem: models
tags: [estimators, counted-grid, feature-allowlist, gram-oracle, coefficient-fidelity, protocol-conformance]

# Dependency graph
requires:
  - phase: 07-01
    provides: "scikit-learn==1.9.1 in the lock with the numpy 2.4.6 / numba 0.65.1 / llvmlite 0.47.0 triple pin intact, and tests/models/'s autouse tracking-root isolation"
  - phase: 07-03
    provides: "models/protocol.py's two @runtime_checkable Protocols, proven to DISCRIMINATE, and tests/fixtures/model_span.py's learnable rig -- whose R^2 law this plan's synthetic cache reuses"
  - phase: 07-04
    provides: "models/frozen.py -- FrozenLinearPredictor, the design property derived from hyperparameters['degree'], expected_coefficient_count, and poly2_design (sklearn's degree-2 column order, asserted bit-for-bit)"
  - phase: 04-feature-catalogue
    provides: "features/normalize.py -- load_normalization/apply_normalization as the frozen transform (D-07-11), and fit_normalization's exclusion-not-imputation of non-finite rows"
provides:
  - "models/regression.py -- the four Trainer implementations (LinearRegressionTrainer, RidgeTrainer, ElasticNetTrainer, Poly2RidgeTrainer), GRID/GRID_SIZE, build_grid, FitContext, validate_feature_names, validate_target_name, FeatureAllowlistError, FitDataError, POLY2_COLUMN_COUNT, ROW_MASK_COLUMN; the ONLY module in models/ that imports sklearn"
  - "tests/fixtures/model_fit.py -- the tmp_path cache + features_norm artifact pair (make_context, write_normalization, identity_params, measured_params, learnable_columns, write_cache, write_row_mask, make_fit_inputs, learnable_fit_inputs)"
  - "tests/models/test_protocol_conformance.py -- 17 isinstance(Trainer) cases, four fitted-predictor cases with a predictor_from_artifact round-trip, the 17-distinct-id test with the measured collapse to 14, and the fit-signature scan at the implementation site"
  - "tests/models/test_feature_allowlist.py -- D-07-23's three refusals plus the fit boundary's own (row-mask height, all-non-finite, dropped-count accounting)"
  - "tests/models/test_ridge_oracle.py -- ridge_gram_streaming and five agreement/fidelity tests"
affects: [07-07, 07-08, 07-09, 07-10, 07-11, "Phase 8's LightGBM and transformer tracks"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "A refusal asserted with a cache path that DOES NOT EXIST: if the check ever drifts from 'the names' to 'the fitted coefficients', the test fails with a file-not-found instead of passing, and the price would already have been in the matrix"
    - "A COUNT assertion as the only witness a name check cannot be: the degree-2 expansion's width sees a fourth column that leaves all three allowed names in place"
    - "An oracle fixture with deliberately NONZERO column means, because the centring terms are the part a mutation deletes silently -- measured: deleting them moves the answer 1.3e+00 relative with the means in place and 9.3e-04 without"
    - "A constructed-truth recovery test as the only thing that sees a permuted coefficient vector: reversing coef_ leaves every length, name and id assertion in the phase green (measured, M5)"
    - "An identity normalisation artifact (mean 0, M2 = rows - 1 so welford_std is exactly 1.0) as the device that makes a fit's design matrix equal to the values written into the cache -- so a coefficient can be checked against arithmetic instead of against a BLAS-dependent literal"
    - "Diagnostic-only state on the trainer (last_fit_seconds), never in hyperparameters: a recipe whose id moved with the machine's load would deduplicate nothing"

key-files:
  created:
    - mvp/models/regression.py
    - mvp/tests/fixtures/model_fit.py
    - mvp/tests/models/test_protocol_conformance.py
    - mvp/tests/models/test_feature_allowlist.py
    - mvp/tests/models/test_ridge_oracle.py
  modified: []

key-decisions:
  - "FitContext was ADDED because FitInputs structurally cannot resolve a normalisation manifest: load_normalization needs a dataset (hence a symbol), a registry root and a lake root, and FrozenLinearPredictor needs a code hash -- none of which is data and every one of which differs between a tmp_path test and a real run. protocol.py was NOT edited. The roots are kept out of hyperparameters (a root in the hashed recipe makes predictor_id machine-dependent) and there is NO canonical-root fallback, so no test can silently reach the real registry under hooks 18/19."
  - "The plan's 'assert null_count() == 0 per column AFTER the polars-to-numpy boundary' contradicts 'MASK non-finite rows, do not refuse them' if read literally -- the tier's own frames carry null labels. Resolved as a four-step accounting: count the nulls, fill_null(nan) EXPLICITLY, assert no nulls remain, then cross-check on the numpy side that every null resurfaced as a non-finite value. That last check is the one with teeth: a null that arrives as a NUMBER is an invented observation no dropped-row count would reveal."
  - "Two dedicated error classes (FeatureAllowlistError, FitDataError) rather than bare ValueError, for models.frozen.FrozenPredictorError's stated reason: pytest.raises(ValueError) meaning 'the allow-list refused a price column' would otherwise be satisfied by 'polars refused a ragged frame'. Both are ValueError subclasses, so the plan's wording holds."
  - "validate_target_name enforces D-07-10 as a refusal, not only as a constant: a fit on ret_1min_mid scored against the 10-second gate would look like a modelling result and be a units mistake. The three diagnostic horizons are named in the message via literal get_label lookups."
  - "Three tests the plan did not ask for -- RidgeTrainer.fit against the oracle, and two constructed-truth recoveries (OLS exact, poly2 nine-term). Nothing in the plan's test set could see a fit that reversed sklearn's coef_; mutation M5 proves it, failing exactly those three and nothing else."
  - "Task 2's test_no_protocol_signature_names_a_model_class_knob and test_a_class_missing_fit_is_not_a_trainer already exist, delivered by 07-03 as tests/models/test_protocol.py:107-139 and :215. Not duplicated -- hook 19 runs the full suite on every commit, so a second scan of the same file is a second thing to maintain. The signature rule is instead asserted where a knob would actually sprout: each concrete trainer's own fit."
  - "FCST-01 and FCST-04 left Pending, following 07-01/03/04/05. Four estimators that fit a fixture are FCST-01's mechanism, not its satisfaction: no real segment has been fitted, no model selected, nothing logged to MLflow."

patterns-established:
  - "Pattern: when a plan's test list predates a sibling plan's delivery, say which tests already exist and where, and spend the budget on what is missing instead."
  - "Pattern: state the observable BEFORE the mutation run, then report the count. Seven mutations, seven predictions, seven exact matches (16/1/2/5/3/4/2 failures) -- a prediction that misses is itself the finding."
  - "Anti-pattern recorded: an oracle whose fixture is zero-mean. Deleting the centring is then a 9.3e-04 relative move instead of 1.3e+00 -- still caught by rtol=1e-9 here, but three orders quieter, and on a smaller matrix it would hide."

# Metrics
duration: "~2.4 h"
completed: 2026-09-25
---

# Phase 7 Plan 06: The Four Estimators and the Price-Column Refusal Summary

Four scikit-learn estimators now fit behind one protocol on a grid of 17 hand-counted configurations, and a raw price level can no longer reach a design matrix: naming `mid` raises before a byte is read, the coefficient vector is pinned at three entries in order, and the degree-2 expansion is pinned at nine columns -- the only assertion of the three that sees a price arriving through an interaction term.

## Performance

| | |
|---|---|
| Suite | 1,227 -> 1,293 tests (66 added: 27 allow-list, 31 conformance, 8 oracle) |
| `pytest tests/models` | 92 -> 158 tests, 14.2 s (runs on every commit, hook 19) |
| Full suite | 261 s on a cold run before, 175 s on a warm run after -- the 66 new tests add about 6 s, and the rest of that gap is numba/parquet cache warmth, not a speedup |
| All 17 configs, fixture scale (17,400 rows) | 0.075 s end-to-end, 0.018 s of estimator time |
| All 17 configs, 7,859,419 synthetic rows | 10.2 s end-to-end; slowest single config 1.05 s |
| Looks spent | ZERO. `budget.look_count` is 0 on `val` and all five `oof_block_*` of BOTH manifests, before the first commit and after the last |

## Commits

| Hash | Subject |
|---|---|
| `9ee2443` | feat(07-06): four sklearn estimators behind the Trainer protocol, on a counted grid |
| `c20e796` | test(07-06): the protocol made load-bearing on 17 real configs, the refusal made three-way |
| `d22c1f6` | test(07-06): sklearn's Ridge against an independent streaming Gram oracle |

## The Grid, Counted and Timed

Measured through the REAL path -- `build_model_span_fixture` -> `harness.accessor.materialize("train")` -> a four-column cache Parquet -> `fit_normalization` over all four catalogue features (`mid` included) -> each trainer's `fit`. 17,400 train rows, 1,799 `val` rows, all `tmp_path`. `R^2 val` is scored against the val block's own mean.

| # | `model_class` | `hyperparameters` | fit (s) | max abs coef | R^2 val |
|---|---|---|---|---|---|
| 1 | `sklearn.LinearRegression` | `{}` | 0.0077 | 6.110e-07 | +0.03046 |
| 2 | `sklearn.Ridge` | `alpha 1e-6` | 0.0047 | 6.110e-07 | +0.03046 |
| 3 | `sklearn.Ridge` | `alpha 1e-3` | 0.0038 | 6.110e-07 | +0.03046 |
| 4 | `sklearn.Ridge` | `alpha 1.0` | 0.0049 | 6.109e-07 | +0.03046 |
| 5 | `sklearn.Ridge` | `alpha 100.0` | 0.0039 | 6.075e-07 | +0.03047 |
| 6 | `sklearn.ElasticNet` | `alpha 1e-6, l1_ratio 0.15` | 0.0045 | 4.609e-07 | +0.02617 |
| 7 | `sklearn.ElasticNet` | `alpha 1e-6, l1_ratio 0.5` | 0.0036 | 1.107e-07 | +0.00339 |
| 8 | `sklearn.ElasticNet` | `alpha 1e-6, l1_ratio 0.85` | 0.0034 | **0.0 exactly** | -0.00592 |
| 9 | `sklearn.ElasticNet` | `alpha 1e-4, l1_ratio 0.15` | 0.0037 | **0.0 exactly** | -0.00592 |
| 10 | `sklearn.ElasticNet` | `alpha 1e-4, l1_ratio 0.5` | 0.0038 | **0.0 exactly** | -0.00592 |
| 11 | `sklearn.ElasticNet` | `alpha 1e-4, l1_ratio 0.85` | 0.0035 | **0.0 exactly** | -0.00592 |
| 12 | `sklearn.ElasticNet` | `alpha 1e-2, l1_ratio 0.15` | 0.0040 | **0.0 exactly** | -0.00592 |
| 13 | `sklearn.ElasticNet` | `alpha 1e-2, l1_ratio 0.5` | 0.0043 | **0.0 exactly** | -0.00592 |
| 14 | `sklearn.ElasticNet` | `alpha 1e-2, l1_ratio 0.85` | 0.0045 | **0.0 exactly** | -0.00592 |
| 15 | `sklearn.Ridge` | `alpha 1e-3, degree 2` | 0.0051 | 6.120e-07 | +0.03066 |
| 16 | `sklearn.Ridge` | `alpha 1.0, degree 2` | 0.0049 | 6.119e-07 | +0.03066 |
| 17 | `sklearn.Ridge` | `alpha 100.0, degree 2` | 0.0044 | 6.085e-07 | +0.03067 |

Every ElasticNet row also carries `max_iter 1000`, `tol 1e-4`, `selection "cyclic"` in its recipe. No `ConvergenceWarning` was raised at any scale.

**SEVEN OF THE NINE ELASTICNET CONFIGS RETURN A COEFFICIENT VECTOR OF EXACTLY ZERO, and that is a real property of the grid rather than a defect.** sklearn's objective carries `1 / (2 n)` on the squared-error term, so `alpha * l1_ratio` is a soft threshold in per-sample gradient units -- and this target is a 10-second return of order 1e-4, whose OLS coefficients are order 1e-7. Every alpha at or above 1e-4 therefore thresholds them all to zero, and those configs ARE the zero-skill control: their `R^2 val` of -0.00592 is bit-identical to the constant-at-train-mean control measured beside them. Nothing refuses them here: a zero vector is a valid `FrozenLinearPredictor` (only a non-finite one is refused), 07-07's gates own eligibility, and D-07-20 logs each as a negative result. **The input for 07-07 is that the grid's LIVE membership is about 10 configs while the selection-bias denominator stays 17.**

The OLS `R^2` of +0.03046 sits inside the 0.013..0.048 the real train-internal splits measured, next to 07-03's +0.0278 on a held-back block -- so the rig is still learnable and still not solved. The degree-2 members score marginally higher (+0.03066).

Cost at segment scale, on SYNTHETIC rows (no real lake, no look):

| rows | all 17 configs | slowest config |
|---|---|---|
| 1,000,000 | 1.22 s | 0.132 s (poly2, alpha 1e-3) |
| 7,859,419 (Option B `val`'s height) | 10.2 s | 1.05 s (poly2, alpha 1e-3) |

Linear in rows, so the real 44,249,548-row `train` extrapolates to roughly 57 s for the whole grid -- one OOF block, one sweep. **No config came near a budget worth stating, so there is nothing here that argues for shrinking the grid.**

## The 17 Distinct Recipes, and the Collapse

At a fixed seed, code hash and normalisation manifest id -- so the only things that can differ are `model_class` and `hyperparameters`:

| | distinct `predictor_id`s |
|---|---|
| The grid as written | **17** |
| `degree` stripped from the poly2 recipes | **14** |

Both numbers are asserted in `test_all_17_grid_entries_have_distinct_predictor_ids`, and the test also asserts `Poly2RidgeTrainer.MODEL_CLASS == RidgeTrainer.MODEL_CLASS == "sklearn.Ridge"` -- because the collapse only happens while that equality holds. Mutation M7 shows what a plausible-looking rename costs: with `"sklearn.Ridge+poly2"` the stripped set stays at 17, the collapse assertion never even runs, and the test fails on the literal line `assert 'sklearn.Ridge+poly2' == 'sklearn.Ridge'` -- naming the cause instead of leaving "17 distinct" looking correct.

## The Three Refusals

| D-07-23 | The assertion that proves it | What it alone can see |
|---|---|---|
| 1. a forbidden name RAISES | `test_a_feature_list_naming_mid_or_bid_price_or_ask_price_raises`, 12 cases (3 names x 4 trainers), each with a cache path that **does not exist** -- so the raise can only come from the names, before any read | `mid`, `bid_price`, `ask_price` named directly |
| 2. exactly three coefficients, in order | `test_the_fitted_linear_coefficient_vector_has_exactly_three_entries_in_the_pinned_order` on a real fit, three classes: `len(coef) == 3` and `feature_names == ("imb_top", "ofi", "trade_flow")` | a fourth column that got past the name check |
| 3. exactly nine columns | `test_the_degree_two_design_matrix_has_exactly_nine_columns` (`len(coef) == 9` on a fit) plus `test_the_nine_column_count_fires_when_a_fourth_column_reaches_the_expansion` (a four-column matrix raises, naming 14 and 9) | a price entering through an INTERACTION term -- the three input NAMES stay legal and the coefficient count moves to a number nobody watches |

Plus the fourth case the plan asked for: `test_the_normalization_artifact_may_carry_mid_and_the_fit_still_refuses_to_use_it` builds a FOUR-row artifact, asserts by read-back that its params are `["imb_top", "mid", "ofi", "trade_flow"]`, fits, and gets three coefficients. Research assumption A5 mechanised: provenance through the normalisation artifact is not a filter.

A fifth, unasked-for member of the same family: `("ofi", "imb_top", "trade_flow")` -- every name legal, the count right -- also raises. `models.frozen` rebuilds the design matrix positionally, so a permutation pairs every coefficient with the wrong input and still predicts plausible numbers.

## The Oracle

`ridge_gram_streaming` is implemented in `tests/models/test_ridge_oracle.py` and imports nothing from `models/regression.py`: a `(p+1) x (p+1)` augmented Gram plus a `(p+1)` right-hand side accumulated in chunks, then the centred solve and `b = y_mean - x_mean @ w`.

| comparison | max relative coefficient difference | intercept |
|---|---|---|
| sklearn Ridge vs oracle, alpha 1e-6 | **4.829e-14** | 9.592e-14 absolute |
| sklearn Ridge vs oracle, alpha 1.0 | **4.874e-14** | 9.415e-14 absolute |
| sklearn Ridge vs oracle, alpha 100.0 | **4.941e-14** | 9.148e-14 absolute |
| `RidgeTrainer.fit` vs oracle, alpha 1.0 | **4.843e-14** | agrees to rel 1e-9 |
| oracle chunk 1,000 and 997 vs one whole pass | **2.340e-14** | agrees to rel 1e-12 |
| **anti-vacuity:** sklearn alpha 1.0 vs oracle alpha 100.0 | **2.287e-02, NOT close** | not close |

The assertions are `rtol=1e-9`, five orders looser than the worst number above. Research's 7.1e-15 was measured against numpy's dense solve on 7.85M real rows; this is the first time an executed sklearn is on one side of the comparison, and 4.9e-14 on a 5,000-row matrix with means of order 1 is the same statement -- float64 round-off. The anti-vacuity case is what stops an "oracle" that merely echoed sklearn: it must disagree when alpha disagrees, and the same-alpha comparison in that test still agrees, so the disagreement is about alpha and not about a broken comparison.

## Coefficient Fidelity, Which the Plan's Test Set Could Not See

Three tests beyond the plan, because every assertion the plan specified is blind to a fit that mangles what sklearn returned:

| test | statement | observed |
|---|---|---|
| `test_the_ols_trainer_recovers_a_constructed_truth_exactly` | a noiseless `y = 7.25 + 2 x0 - x1 + 0.5 x2` comes back as `(2, -1, 0.5)` and 7.25 | max absolute error **8.882e-16**; the frozen body's `predict` reproduces the target to 1e-9 |
| `test_the_poly2_trainer_recovers_a_nine_term_truth_in_sklearns_column_order` | nine distinct coefficients recovered in sklearn's own expansion order | max relative error **1.495e-05** at alpha 1e-3 -- ridge shrinkage, not a defect (a permutation is an O(1) error) |
| `test_the_ridge_trainers_own_coefficients_agree_with_the_oracle` | the production `fit` path, not just sklearn | 4.843e-14 |

No coefficient literal is pinned anywhere (correction C6): the truths are constructed in the test, and the tolerance for the poly2 case is a function of the data and alpha, not of the BLAS.

## Mutation Checks

Each one: state the observable first, print the changed block, assert the file hash CHANGED, run `pytest tests/models` (158 tests, 14 s), restore, confirm the hash matches again. `models/regression.py` at `9a43624bd043496e`, `tests/models/test_ridge_oracle.py` at `dbc4b678f87413ed`. **All seven predictions matched the observed failure count exactly, and every file was restored.**

| # | Mutation | Observable stated BEFORE the run | Result | Hashes (orig -> mut -> restored) |
|---|---|---|---|---|
| M1 | `validate_feature_names(...)` call deleted from `fit` | the 12 forbidden-name cases and the 4 not-exactly cases can no longer raise `FeatureAllowlistError` -- polars fails on the nonexistent cache instead; the diagnostic-target test survives because `validate_target_name` is a separate call | **16 failed**, 142 passed -- exactly the predicted set | `9a43624b` -> `3630468a` -> `9a43624b` MATCH |
| M2 | the nine-column check weakened from `!=` to `<` | a 14-column expansion now passes, so only the four-column anti-vacuity test fails; every three-input fit is untouched | **1 failed**, 157 passed | `9a43624b` -> `61e145e8` -> `9a43624b` MATCH |
| M3 | the finite mask replaced by an all-true mask | sklearn's own `check_array` refuses NaN, so this surfaces as a bare `ValueError`, NOT as a masked fit -- the dropped-count test errors and the all-non-finite test fails because a bare `ValueError` is not a `FitDataError` | **2 failed**, 156 passed; message confirmed `ValueError: Input X contains NaN.` | `9a43624b` -> `6bfb2c4f` -> `9a43624b` MATCH |
| M4 | the oracle's centring terms deleted (test file) | the fixture's means are (3, -2, 0.5) with a 7.25 offset, so the uncentred system differs by O(1): three alpha cases, the mismatch test's same-alpha assertion, and trainer-vs-oracle fail; chunk invariance compares the oracle with itself and survives | **5 failed**, 153 passed -- exactly that set | `dbc4b678` -> `ed49dbe3` -> `dbc4b678` MATCH |
| M5 | `coef_` reversed in `fit` | the length is unchanged, so all three price-column refusals, every `isinstance` case and all 17 ids still pass; only the three fidelity tests fail | **3 failed**, 155 passed, all in `test_ridge_oracle.py` | `9a43624b` -> `ba75512f` -> `9a43624b` MATCH |
| M6 | `degree: 2` removed from the poly2 recipe | `design` derives from the PRESENCE of `degree`, so it reads `"linear"` and `FrozenLinearPredictor` refuses 9 coefficients for 3 inputs -- every poly2 fit raises, and the id set drops to 14 | **4 failed**, 154 passed; `FrozenPredictorError: design 'linear' over 3 inputs needs exactly 3 coefficients, got 9`; distinct ids **14** | `9a43624b` -> `21a3bdc7` -> `9a43624b` MATCH |
| M7 | poly2 renamed `"sklearn.Ridge+poly2"` | the 17 ids stay distinct because the class string now separates them, so the test fails at the class-EQUALITY assertion (not the collapse), and `predictor_from_artifact` has no builder for the invented class | **2 failed**, 156 passed; failing line `assert 'sklearn.Ridge+poly2' == 'sklearn.Ridge'` | `9a43624b` -> `1b4ad406` -> `9a43624b` MATCH |

**M4's side measurement, which is the reusable finding.** The same deletion on a matrix whose columns were NOT shifted moves the coefficients only **9.336e-04** relative, against **1.337e+00** with the fixture's means in place. Still caught at `rtol=1e-9`, so the trap did not spring -- but three orders quieter, and on a smaller or better-conditioned matrix it would hide. 07-04's inert-mutation lesson, paid forward: the fixture's nonzero means are load-bearing, and the module docstring says so.

**M5 is the gap this plan closed.** Every assertion the plan itself specified -- the three refusals, the 17 `isinstance` cases, the 17 distinct ids, the sklearn-vs-oracle comparison -- survives a `fit` that hands back sklearn's coefficients in reverse. Only the three unasked-for fidelity tests fail. Had they not been written, "the estimators conform and sklearn agrees with an oracle" would have been true of an implementation whose every prediction was wrong.

## Deviations from Plan

### 1. `FitContext` added; `protocol.py` NOT edited

**Found during:** Task 1, reading `load_normalization`'s signature.

`FitInputs` carries a normalisation manifest ID and nothing that can resolve it: `load_normalization` needs the dataset (hence the symbol), a registry root and a lake root, and `FrozenLinearPredictor` needs a code hash. Adding them to `FitInputs` would have edited the one file Phase 8 must not have to edit; defaulting them to `data.lake_paths`' canonical roots would have pointed the suite at the real registry under hooks 18/19. So they are a small frozen `FitContext` passed to every trainer's constructor, deliberately absent from `hyperparameters` (a root in the hashed recipe makes `predictor_id` machine-dependent) and with no fallback at all.

### 2. One unlisted file: `tests/fixtures/model_fit.py`

`tests/models/` has no `__init__.py` by design and runs under `--import-mode=importlib`, so its modules cannot import each other; three test files need the same cache-plus-artifact pair. The alternative was the same forty lines three times. It writes a plain four-column Parquet and a real `features_norm` artifact, never through the accessor. 07-04 and 07-05 set the precedent of reporting an unlisted test file rather than adding one silently.

### 3. Two of Task 2's five tests already existed

`test_no_protocol_signature_names_a_model_class_knob` and `test_a_class_missing_fit_is_not_a_trainer` were delivered by 07-03 (`tests/models/test_protocol.py:107`, `:127`, `:215`), whose own docstring says it "proves the mechanism those tests will rely on". Not duplicated. The signature rule is asserted instead at the implementation site -- each concrete trainer's `fit` must take exactly `(self, inputs)` and name none of seven banned tokens, with an anti-vacuity case showing the scanner flags a widened signature. The stub case is re-anchored to a real trainer (`RidgeTrainer` with `fit` shadowed), which is new: it proves `isinstance` rejects something shaped exactly like a grid entry.

### 4. The null accounting, reconciled

The plan asks for `null_count() == 0` per column AFTER the numpy boundary AND for non-finite rows to be masked rather than refused. Read literally those contradict, because the tier's frames carry null labels by construction. Implemented as: record each column's null count, `fill_null(nan)` EXPLICITLY, assert no nulls survive that fill, then assert on the numpy side that at least as many non-finite values appeared as there were nulls. The last one is the check with teeth -- a null that arrives as a number is an invented observation, and no dropped-row count would show it.

### 5. Additions under Rule 2

`validate_target_name` (D-07-10 as a refusal, naming the three diagnostic horizons); two dedicated error classes so `pytest.raises` cannot pass for the wrong reason; row-mask dtype and null checks beside the height check (a numeric mask is truthy on every count; a null is neither kept nor dropped); `selection` declared in ElasticNet's recipe alongside `max_iter` and `tol`, since it is what makes the fit deterministic; `last_fit_seconds` as diagnostic trainer state, because "record each config's wall clock" needs somewhere to be recorded and `hyperparameters` is the one place it must not be.

### 6. Import-time cross-checks

`FEATURE_NAMES` (three literal catalogue lookups) is asserted equal to `models.frozen.FEATURE_NAMES`, `POLY2_COLUMN_COUNT` to `expected_coefficient_count(3, "poly2")`, and `len(GRID)` to `GRID_SIZE`. A catalogue edit that would silently repair one side and break the other fails at import instead.

## Look Budget

`harness.budget.look_count` read directly against `/Volumes/ProjectsSSD/aihedgefund/mlflow` (a read-only `search_runs`), before the first commit and again after the last:

| manifest | `val` | `oof_block_0..4` |
|---|---|---|
| `97964cb2...` (3-day Phase 5 reference) | 0 | 0, 0, 0, 0, 0 |
| `807125015b...` (the approved 7-day geometry) | 0 | 0, 0, 0, 0, 0 |

Twelve counters, unchanged. **`harness.accessor.materialize` was never called against the real lake in this plan.** It is called twice, in one throwaway measurement script, against a scratch lake with a scratch MLflow store -- and not at all from any committed test: the three new test files build their caches directly.

## Threat Register Outcomes

| Threat ID | Disposition | How |
|---|---|---|
| T-07-21 (a price in the design matrix) | mitigated | three independent refusals, each with the assertion that proves it and the nine-column one with an anti-vacuity case; M1 and M2 prove two of the three bite, and the artifact-carries-`mid` case closes research assumption A5 |
| T-07-22 (normalisation leakage) | accepted, unchanged | `load_normalization` only; no refit path exists in the module. Documenting it in `spec.md` remains 07-07's |
| T-07-23 (the polars-to-numpy boundary) | mitigated | per-column dtype check, explicit `fill_null(nan)`, the post-fill null assertion and the nulls-must-resurface cross-check; M3 proves the mask is what keeps sklearn from refusing the frame outright |
| T-07-24 (sklearn's own solve) | mitigated | an independent streaming oracle at three alphas (4.8e-14), chunk-invariance at three chunk sizes, an alpha-mismatch anti-vacuity case (2.3e-02), and the same oracle run through the production `fit`; M4 proves the comparison can fail |

## What Was NOT Done

- **No real data was fitted.** Every number above comes from the `tmp_path` fixture rig or from synthetic rows. `materialize` was never pointed at `/Volumes/ProjectsSSD/aihedgefund/lake`, no look was spent, and the 7-day manifest's twelve counters are still 0.
- **No normalisation artifact exists for the 7-day train window.** D-07-11 says the new window needs its own `train_end` partition, written additively; this plan only READS artifacts, and the one it reads it writes into `tmp_path`.
- **Nothing was logged to MLflow and no run was started.** No `start_tracked_run`, no `design` tag, no `n_configs`, no negative-result log -- 07-07 owns the sweep, the tag schema and D-07-20's log.
- **No eligibility gate, no metric, no `models/metrics.py`.** The R^2 numbers in this SUMMARY were computed in a measurement script, not by committed code; the zero-skill control column D-07-32 asks for does not exist yet, and `train_target_mean` is captured on every predictor waiting for it.
- **No committed wall-clock test.** The 17 timings come from a scratch script that was not committed, to keep 14 s of `tests/models` from becoming 20. They are reproducible from the rig but are not re-measured on every commit.
- **`spec.md` untouched** (07-07 owns the Stage 1 section), **no guardrail or tool edited**, **no manifest issued or committed**, and `mvp/data/lake_registry/` is byte-unchanged.
- **`FCST-01` and `FCST-04` left Pending** in REQUIREMENTS.md, following every earlier plan in this phase.
- **mypy was not run.** It is not one of the 19 hooks; the annotations in `regression.py` are documentation, and conformance is asserted at runtime instead, which is the whole of D-07-27.
- **The root-level untracked `.gitignore`** (`timesfm/`, `hf-cache/`, model weights) is pre-existing, unrelated to this plan, and was left exactly as found -- not staged, not modified.
- **No `PREDICTOR_BUILDERS` entry was added.** Both Ridge trainers report `"sklearn.Ridge"`, which `models.frozen.LINEAR_MODEL_CLASSES` already registers -- which is why M7's rename broke the round-trip.

## Self-Check: PASSED

- `mvp/models/regression.py`, `mvp/tests/fixtures/model_fit.py`,
  `mvp/tests/models/test_feature_allowlist.py`,
  `mvp/tests/models/test_protocol_conformance.py`,
  `mvp/tests/models/test_ridge_oracle.py` -- all five FOUND on disk.
- Commits `9ee2443`, `c20e796`, `d22c1f6` -- all three FOUND in `git log --all`.
- `git status --short` names nothing of this plan's: every mutation was
  restored and the three working-tree files are committed. The only untracked
  entry is the pre-existing root `.gitignore`, left as found.
- `grep -rn "import sklearn\|from sklearn" mvp/models/` names `regression.py`
  and nothing else; `grep -rn optuna mvp/models/ mvp/tests/models/` finds
  nothing; `len(models.regression.GRID)` prints 17.
- Full suite 1,293 passed. `budget.look_count` re-read after the last code
  commit: 0 on all twelve counters.
