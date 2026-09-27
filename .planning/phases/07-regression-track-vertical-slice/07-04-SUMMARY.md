---
phase: 07-regression-track-vertical-slice
plan: 04
subsystem: models
tags: [frozen-predictor, coefficient-json, determinism, subprocess, content-addressing, no-pickle]

# Dependency graph
requires:
  - phase: 07-03
    provides: "models/protocol.py's FrozenPredictor Protocol and its empty PREDICTOR_BUILDERS stub, models/predictor_id.py's five-field recipe hash, and tests/models/conftest.py's autouse tracking-root isolation -- this plan registers the first builder and implements the first Protocol member set"
  - phase: 03-data-lake
    provides: "data.store.compute_manifest_id / canonicalize_manifest -- the ONE canonicaliser; the predictor body's self-hash is literally it, never a second hasher"
  - phase: 05-walk-forward-harness
    provides: "harness.errata.read_errata_manifest's three-refusal read shape and its dedicated-error-class reasoning, copied and extended with a fourth refusal"
  - phase: 06-simulator
    provides: "tests/sim/test_determinism.py's child-script mechanic (inspect.getsource extraction, explicit child env, shape-before-value on the digest) reused in shape, never imported"
provides:
  - "models/frozen.py -- FrozenLinearPredictor (design a DERIVED property), predict() as one matvec, to_artifact()'s JSON body, predictor_registry_path/write_frozen_predictor/read_frozen_predictor with four refusals, poly2_design, FEATURE_NAMES, LINEAR_MODEL_CLASSES"
  - "models/protocol.py -- PREDICTOR_BUILDERS populated lazily by _register_builtin_builders, the import inside the function body"
  - "tests/models/test_frozen_no_sklearn.py -- the sklearn-free re-evaluation (linear and degree-2), the subprocess proof plus its injected-stub counterpart, the AST import ban, and every refusal isolated by re-signing"
  - "tests/models/test_frozen_determinism.py -- in-process vs fresh-subprocess table hash, the one-ULP sentinel, and the registry round trip"
  - "tests/models/test_sklearn_environment.py -- sklearn's degree-2 column order EXECUTED (was read-from-source only)"
affects: [07-05, 07-06, 07-07, 07-08, 07-09, 07-10, 07-11, "Phase 8's LightGBM and transformer tracks"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "A load-bearing string DERIVED from the hashed recipe instead of stored beside it -- `design` is a @property over hyperparameters['degree'], so it cannot disagree with what predictor_id hashes, and the emitted copy is cross-checked on every read rather than trusted"
    - "Body-vs-recipe as an explicit, documented split: three fields (train_target_mean, n_rows_fitted, n_rows_dropped) live on the dataclass and in to_artifact() but are EXCLUDED from the hash, so two runs of one recipe share a predictor_id under different manifest_ids"
    - "A per-module import ban asserted over ast.Import/ast.ImportFrom nodes, with a counterpart fed both spellings (`import sklearn.x as y`, `from sklearn import Y`) -- the same signatures-not-text distinction 07-03 drew, one level out"
    - "Refusal tests that RE-SIGN the tampered body, so each trips exactly one check and cannot pass because an earlier one fired"
    - "A determinism claim asserted only BETWEEN two artefacts of one run on one host, never against a committed digest (correction C6)"

key-files:
  created:
    - mvp/models/frozen.py
    - mvp/tests/models/test_frozen_no_sklearn.py
    - mvp/tests/models/test_frozen_determinism.py
  modified:
    - mvp/models/protocol.py
    - mvp/tests/models/test_sklearn_environment.py

key-decisions:
  - "`design` derives from the PRESENCE of a `degree` key, not from `degree == 2` as the plan wrote it, and a `degree` present but not equal to 2 is REFUSED. The plan's literal spelling reads `degree: 3` as `\"linear\"`: three coefficients, every check passed, a cubic model predicted as a linear one. Presence plus an explicit supported-degree refusal keeps the cannot-disagree property identical and closes that hole; the mutation that removes the refusal is caught."
  - "ONE file the plan did not list was touched, and one check the plan ran only as a shell one-liner became a committed test. The unlisted file is `tests/models/test_sklearn_environment.py`: the must_have 'in exactly sklearn's column order, and that is asserted, not assumed' had no owner in any Phase 7 plan, and that file exists to execute research assumptions read from source but never run -- it is also the only place in tests/models/ that may import sklearn. The committed check is `models/frozen.py`'s import ban, which lives in `test_frozen_no_sklearn.py` (a file the plan DOES list) rather than only in the verify line nothing re-runs (07-03's precedent)."
  - "`hyperparameters` is stored as a MappingProxyType over `models.predictor_id._canonical_value`'s output. A sibling module's private helper imported rather than copied: it is a twenty-line recursive per-type coercion in the SAME package, and copying it would create exactly the second canonicaliser 07-PATTERNS §C forbids -- the body's bytes and the hashed recipe's must be the same, or a reader diffing the file is reading something the id does not cover."
  - "The dataclass is `kw_only=True` and coerces its own containers in __post_init__ (tuple of float, tuple of str, int). sklearn's `coef_` is a numpy array of np.float64; a to_artifact() that inherited them would raise TypeError from json.dumps hours into a 17-config sweep."
  - "NO body was written under mvp/data/lake_registry/predictors/ and NO guardrail was edited, per hard constraint 3 -- the directory, both checker edits and the first REAL coefficients belong to one commit in 07-10. Tests write only to tmp_path registries."
  - "FCST-04 is NOT marked complete, following 07-01 and 07-03. A frozen predictor that can round-trip is FCST-04's mechanism, not its satisfaction: no estimator has been fitted, no prediction table stored, no artifact committed."

patterns-established:
  - "Pattern: when a refusal is an `or` of two comparisons, each half gets a test that ONLY it can catch. Here the body's own manifest_id field is checked beside the caller's id, and the case that isolates it is a body where that ONE field was edited -- canonicalize_manifest drops the key before encoding, so the recomputed hash still equals the filename and the first half is silent."
  - "Pattern: a one-ULP sentinel needs a fixture where the perturbed coefficient DOMINATES the sum. At 4.0 against features in [-0.75, 0.75] one ULP of coefficient is of the order of one ULP of result and moved 346 of 400 rows; on a small coefficient it would have rounded away in the final addition and the sentinel would have looked flaky instead of informative."
  - "Pattern: a subprocess guard prints its evidence as well as asserting it. The child prints its sklearn-rooted sys.modules (`[]`) on line 1 and the digest on line 2, and the parent asserts both -- so 'sklearn never arrived' is a value in the transcript, not only an assertion that did not fire."
  - "Anti-pattern recorded: a mutation that swaps the OPERANDS of a multiplication is not a column-order mutation. Float multiplication is commutative, so `features[:, j] * features[:, i]` changed the file's hash and nothing else; no test failed, correctly. The real permutation mutation reverses the inner sweep."

requirements-completed: []
# FCST-04 is this plan's frontmatter requirement and is deliberately left
# Pending. See key-decisions.

# Metrics
duration: ~40min
completed: 2026-09-25
---

# Phase 7 Plan 04: The Frozen Predictor as Coefficient JSON

**A fitted model stored as four numbers a process that has never imported
sklearn can re-evaluate to the same bits — and a design string that cannot
lie about which nine terms those numbers multiply.**

## Performance

- **Duration:** ~40 min (16:38 to 16:57)
- **Tasks:** 3 of 3, plus one added test owner
- **Files:** 3 created, 2 modified
- **Tests:** 1166 → **1198** (+32)
- **`pytest tests/models` wall clock:** 8.96 s for 64 tests — it runs on every commit

## Commits

| Commit | Subject |
|---|---|
| `afd64c2` | `feat(07-04)` a frozen predictor whose design cannot disagree with its recipe |
| `e83498b` | `test(07-04)` the coefficient JSON re-evaluated where sklearn never was |
| `549260b` | `test(07-04)` cross-process table determinism, with a one-ULP sentinel |
| `1cd474a` | `test(07-04)` sklearn's degree-2 column order, executed instead of read |

## What "Frozen" Now Means, Checkably

Three claims, each measured rather than argued.

**The body is re-evaluable without the library that made it.** A child process
spawned with an explicit environment reads a coefficient JSON off a `tmp_path`
registry, predicts, and prints two lines: the list of `sklearn`-rooted names in
its own `sys.modules`, and the sha256 of its prediction table's raw bytes.

```
child stdout:  ['[]', '8b142053bedde68425cbb42e38c08fc20e8877c6dacf622d27a66e5d9b78c0ee']
parent hash :   8b142053bedde68425cbb42e38c08fc20e8877c6dacf622d27a66e5d9b78c0ee
```

The guard is not decorative. With a stub module stuffed into the child's
`sys.modules` (`--inject-sklearn`), the same child exits 1 with
`AssertionError: sklearn reached this process: ['sklearn']`. A stub rather than
a real import, so the suite that runs on every commit does not pay a second to
learn the same thing.

**The table is bit-identical across processes on this host.** In-process and
fresh-subprocess hashes, the child extracted with `inspect.getsource` so it runs
byte-identical logic by construction:

```
in-process hash : 70690565c04cfe13870d6d617f5cc8dbfcb5bbc8b562df534747c1073bea92c9
subprocess hash : 70690565c04cfe13870d6d617f5cc8dbfcb5bbc8b562df534747c1073bea92c9
```

**And the sentinel that makes those equalities mean something.** One ULP on the
last coefficient (`np.nextafter(4.0, inf)`):

```
unperturbed : 70690565c04cfe13870d6d617f5cc8dbfcb5bbc8b562df534747c1073bea92c9
one-ULP     : 719292835782cc7bd1a5c2b42ea7560519934161b7794425d5741f84d4245f5d
rows whose prediction bits moved: 346 / 400
```

346, not 1. That is the count the mechanism predicts and the reason the fixture's
largest coefficient is the one perturbed: on a small coefficient the one-ULP step
rounds away in the final addition, `!=` still passes on a handful of rows, and the
test would look flaky while proving nothing. **No digest above is pinned as a
literal anywhere in the repo** — `grep -cE "[0-9a-f]{64}"` returns 0 on all three
new files. This host's numpy links Apple Accelerate; CI is Linux/OpenBLAS; a
committed constant would be a guaranteed CI failure that says nothing about
determinism (correction C6).

## Why `design` Cannot Disagree With `degree`

`design` is a `@property`, asserted to be absent from
`dataclasses.fields(FrozenLinearPredictor)` and to have `fset is None`. It is
computed from `hyperparameters`, which is what `predictor_id` hashes — so there
is no second place for it to be wrong. Four mechanisms hold that:

| Where | What it refuses |
|---|---|
| `__post_init__` | a coefficient count that disagrees with the derived design, naming both numbers and the `degree` it derived from |
| `__post_init__` | a `degree` present and not 2 — refused, never read as `"linear"` |
| `_require_design_agrees` | an on-disk body whose EMITTED `design` differs from the derived one |
| both doors | `read_frozen_predictor` and `frozen_linear_from_artifact` share one checker, so neither can be the lenient one |

The collision this closes is concrete. 07-06 pins the degree-2 Ridge to the same
`model_class` string as plain Ridge, and the two share three alphas. A `design`
FIELD saying `"poly2"` beside a recipe with no `degree` key would hash
byte-identically to plain Ridge at that alpha: three pairs of configs collapsing
onto one `predictor_id`, `harness.negative_log` deduplicating away up to three
distinct ineligible configs, and a stored table's `predictor=<first 16 chars>`
directory naming a config that never produced it.

**Deviation from the plan's literal spelling, and why.** The plan wrote
`design = "poly2" if hyperparameters.get("degree") == 2 else "linear"`. That
reads `degree: 3` as `"linear"` — three coefficients, every check satisfied, a
cubic model predicted as a linear one. Presence selects the design and the VALUE
is separately pinned to `SUPPORTED_DEGREE`; removing that pin is one of the seven
mutations below, and it is caught.

## The Column Order, Executed Instead of Read

The nine-column order came from sklearn 1.9.1's source
(`combinations_with_replacement`) and had never been run anywhere in this repo.
It is now asserted two ways in `tests/models/test_sklearn_environment.py` — the
file whose stated purpose is executing exactly such assumptions, and the only
place in `tests/models/` that may import sklearn:

```
get_feature_names_out -> ['x0', 'x1', 'x2', 'x0^2', 'x0 x1', 'x0 x2', 'x1^2', 'x1 x2', 'x2^2']
poly2_design(X) == PolynomialFeatures(degree=2, include_bias=False).fit_transform(X)   # array_equal, max abs diff 0.0
```

Nine columns for three inputs — not six, which would drop the squares, and not
ten, which would add a bias column `include_bias=False` never emits. The count is
the only one of D-07-23's three refusals that can catch a price column arriving
through an interaction term.

## Mutation Checks

Seven mutations, each with its before / mutated / restored hash and the test that
caught it. `models/frozen.py` restored to `a18e39cd…` every time.

| Mutation | mutated hash | Caught by |
|---|---|---|
| reverse the inner upper-triangular sweep (same six terms, permuted) | `49e6ca0c…` | the hand-built 9-column re-evaluation, its permutation counterpart, AND the sklearn comparison (3 tests) |
| drop the emitted-vs-derived `design` refusal | `bb586c97…` | `…poly2_with_no_degree_key_is_refused`, `…linear_while_carrying_a_degree…`, and the `predictor_from_artifact` door (3 tests) |
| compare the recomputed hash against ONLY the caller's id | `6a150fe6…` | exactly one test — `…own_manifest_id_field_was_edited…_although_it_hashes_to_its_filename` |
| drop the supported-degree refusal | `60ce0ef1…` | exactly one — `…unimplemented_degree_is_refused_rather_than_read_as_linear` |
| drop the re-derived `predictor_id` cross-check | `37d2b26c…` | exactly one — `…recipe_was_edited_is_refused_by_the_re_derived_predictor_id` |
| drop the coefficient-count refusal | `3a34d589…` | both count tests (degree-2-with-3, linear-with-9) |
| drop the intercept from `predict` | `136b40ee…` | both re-evaluation tests plus the permutation counterpart (3 tests) |

**The near-miss worth keeping.** The first attempt at the column-order mutation
swapped the multiplication's operands (`features[:, j] * features[:, i]`). The
file hash changed, the suite stayed green, and that was correct: float
multiplication is commutative, so the mutation was a no-op the hash could not
distinguish from a real edit. Asserting the file hash changed is necessary and
not sufficient — the edit also has to mean something. The real mutation reverses
the sweep, keeping the same six terms in a different order.

## The Four Refusals, Each Isolated

Every tampered body below is RE-SIGNED at its own recomputed `manifest_id`, so it
trips exactly one check and passes the rest. Without that, a body edited to break
the `predictor_id` cross-check would be caught by the self-hash first and its
test would pass while the check it names went unexercised.

1. **Named-but-missing fails closed** — never a default, never `None`.
2. **The self-hash, against BOTH ids.** Three tests: coefficients edited with
   everything left stale; coefficients edited with the `manifest_id` field
   "helpfully" kept in sync; and — the only case the caller's-id half cannot see
   — the `manifest_id` field edited ALONE, where `canonicalize_manifest` drops
   that key before encoding so the recomputed hash still equals the filename.
3. **`predictor_id` re-derived** from the five recipe fields and cross-checked.
4. **Emitted `design` vs derived**, in both directions.

Plus: an incomplete body refused BY NAME, a foreign `schema_version` refused, and
a second write at the same id refused rather than overwriting.

## The Body/Recipe Split, as Data

`test_the_body_carries_both_ids_and_the_three_non_recipe_fields` writes two bodies
that differ only in `coef`, `train_target_mean` and `n_rows_fitted`:

```
predictor_id : equal
manifest_id  : different
```

That is D-07-22's whole point made checkable by looking at the registry rather
than by re-running a test. It only works because the three fields are in the body
and not in the hash — as hyperparameters they would give every OOF block's fit of
one config its own `predictor_id`, and as a second return value they would break
`Trainer.fit(...) -> FrozenPredictor` for Phase 8.

## Validation-Budget Safety

`harness.accessor.materialize` was never called. `budget.look_count` read
read-only against `/Volumes/ProjectsSSD/aihedgefund/mlflow`, before the first
commit and again after the last:

| Manifest | allowance | `val` | `oof_block_0..4` |
|---|---|---|---|
| `807125015b252014…` (7-day, 07-02) | 3 | 0 → 0 | 0 → 0 (all five) |
| `97964cb27f62aa07…` (3-day, Phase 5 reference) | 5 | 0 → 0 | 0 → 0 (all five) |

Twelve counters, zero before, zero after. Every test in this plan uses a
`tmp_path` registry and the directory's autouse tracking-root isolation; none
touches the real lake, the real registry or the real MLflow store. `git status` is
clean of any new `mvp/data/lake_registry/` path.

## What Was NOT Done

- **No `mvp/data/lake_registry/predictors/` directory, no body in it, and no
  guardrail edit.** `tools/check_manifest_append_only.py` and
  `tools/check_manifest_id_integrity.py` are byte-unchanged. The directory, both
  checker edits and the first REAL coefficients land together in **07-10**
  (D-07-22 / D-07-38); a fixture-derived body committed into an append-only
  registry would be permanent junk.
- **No estimator was fitted.** `models/regression.py` does not exist. Nothing in
  this plan calls `Ridge`, `LinearRegression` or `ElasticNet` to produce a
  coefficient — every coefficient here is hand-chosen. That is 07-06.
- **No prediction table, no `models/predictions.py`, no `models/conversion.py`**
  (07-05), **no `models/metrics.py`, no `models/gates.py`** (07-07 onward).
- **No MLflow run was started**, no normalization artifact was written at the new
  `train_end`, and no `val` or OOF frame was materialised.
- **FCST-04 is left Pending.** A predictor that round-trips is the requirement's
  mechanism, not its satisfaction.
- **Determinism is NOT claimed across platforms.** Every equality asserted here
  is between two artefacts of one run on this host. Apple Accelerate versus
  Linux OpenBLAS is untested and deliberately unasserted.
- **`predict` is not proven deterministic at production scale.** 400 rows × 3 (and
  × 9) columns, not the 7.86M-row `val` frame research measured separately.

## Self-Check: PASSED

- `mvp/models/frozen.py` — FOUND
- `mvp/tests/models/test_frozen_no_sklearn.py` — FOUND
- `mvp/tests/models/test_frozen_determinism.py` — FOUND
- `afd64c2`, `e83498b`, `549260b`, `1cd474a` — all FOUND in `git log`
- `pytest tests` — 1198 collected, green (all four commits passed hooks 18 and 19)
