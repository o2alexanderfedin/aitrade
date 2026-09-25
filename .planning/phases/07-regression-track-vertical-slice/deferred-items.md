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
