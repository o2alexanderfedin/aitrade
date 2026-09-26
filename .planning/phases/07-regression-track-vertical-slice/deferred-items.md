# Deferred items — Phase 7

Out-of-scope discoveries logged during execution, not fixed. Each names the
plan that found it and why it was left alone.

## D1. `mvp/tests/lockbox/conftest.py` has the same pop-only tracking-root defect

**Found during:** 07-01 Task 3.
**Status:** NOT FIXED. Deliberately out of scope.

Its autouse `isolated_canonical_tracking_root` is the fixture
`tests/harness/conftest.py` was copied from, and it has the identical body:
`os.environ.pop(MLFLOW_TRACKING_ROOT_ENV, None)`. With the variable absent,
`lake_paths.mlflow_tracking_root(None)` resolves to the REAL store at
`/Volumes/ProjectsSSD/aihedgefund/mlflow`, so a lockbox test that resolves a
canonical root without passing its own gets the real one.

Two differences from the harness case, which is why it was left:

1. **Its docstring is honest.** It says "cleared before every test, and
   restored afterwards" — which is exactly what the code does. The harness
   copy claimed to point the store at `tmp_path` and did not; that gap between
   claim and behaviour is what made it a defect rather than a design.
2. **No look flows through it.** `harness.budget.record_look` is the only
   thing that spends a validation look, and no lockbox test calls it. The
   lockbox's own counter is the consumed-token record, which every lockbox
   test already seeds into its own `tmp_path` store explicitly.

07-01's scope was the two conftests the plan named. Fixing a third shared
fixture in the same commit would have put an unrelated blast radius behind
one full-suite green. The repair is mechanical — the same four lines — and
should be taken by whichever later plan first needs lockbox tests to resolve
a canonical root implicitly.

---

## `STATE.md`'s Performance Metrics table has no row for 07-02

Found while 07-03 was recording its own row. The table runs
`... | Phase 06 P07 | ... | Phase 07 P01 | ... | Phase 07 P03 |` -- **07-02 is
absent**, because that plan stopped at its human-verify checkpoint and the
`state.record-metric` call lives after the checkpoint in the executor flow.

NOT fixed here, deliberately. `07-02-SUMMARY.md` carries `duration: ~1h20m`, so
the number is recoverable, but it is a number 07-03 did not measure and
transcribing someone else's into a metrics table is how a table stops meaning
what it says. Whichever plan next runs a `state.*` verb can add it from the
SUMMARY, with a note that it came from there.

---

## Correction C5's 91.2 MiB is ONE DAY, not the `val` segment

**Found during:** 07-05, measuring the real writer's bytes per row.

C5 corrected D-07-15's "~11.3M rows ≈ 270 MB" to "91.2 MiB zstd at 7.86M
rows". The bytes per row is right and reproduced exactly -- 12.17 B/row there,
12.1 to 12.7 B/row measured here through `write_prediction_table` at 100k and
2M rows -- but the row count is a single day. The approved manifest
`807125015b...` admits **16,294,059** rows to `val` (two days, Option A), so
the one table plan 07-11 stores will be **about 190 MiB**, not 91.2 MiB.

NOT a defect and nothing to fix here: D-07-15's decision (store only the
winner) stands, and 07-05 stores nothing. It is an input to 07-11, because
D-07-25 makes a committed prediction-table manifest `stat()`-checked on every
commit from then on, and the number that consequence attaches to is 190 MiB.

---

## The `predictions` tier has no `verify_manifest --full` coverage of its own

**Found during:** 07-05 Task 1.

`tools/check_no_manifest_rewrite` globs `manifests/**`, so a committed
predictions manifest is covered the moment one exists (D-07-38, confirmed --
no guardrail edit was needed). What does NOT exist yet is a predictions
partition inside `tests/fixtures/lake`, so the CI-fixture leg of that hook
exercises the scan over curated/features bytes only. NOT added here: the
fixture lake is committed bytes, and adding a partition to it for coverage of
a tier whose first real table lands in 07-11 is a commit that freezes bytes
nothing reads.
