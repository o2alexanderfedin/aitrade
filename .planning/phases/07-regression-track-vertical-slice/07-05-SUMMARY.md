---
phase: 07-regression-track-vertical-slice
plan: 05
subsystem: models
tags: [prediction-table, row-alignment, return-to-price, join-order, manifest-addressing, holdout-gate]

# Dependency graph
requires:
  - phase: 07-03
    provides: "tests/models/conftest.py's autouse tracking-root isolation and tests/fixtures/model_span.py's learnable rig -- the 98-trade perfect-foresight ceiling this plan's end-to-end test bounds itself against was measured there"
  - phase: 04-feature-catalogue
    provides: "features/normalize.py -- the ONLY existing manifest-addressed Parquet artifact on a non-date-indexed tier; copied function for function, including the write-once PARENT glob guard and the two-gate loader reasoning"
  - phase: 05-walk-forward-harness
    provides: "harness.accessor.materialize as the only door to fold rows, harness.segments.read_segment_manifest's self-hash-on-read, and harness.errata.mask_errata_cells -- the join whose output order this plan makes load-bearing"
  - phase: 06-simulator
    provides: "sim.kernel.run_sim_checked's raw-price `pred` contract and its symmetric floor/ceil quantisation -- the reason a predicted RETURN needs a conversion site at all"
provides:
  - "data.store.PREDICTIONS_TIER -- named beside FEATURES_NORM_TIER, deliberately absent from BY_DATE_INDEXED_TIERS"
  - "models/predictions.py -- PREDICTION_TABLE_SCHEMA, predictions_dataset, prediction_table_path, write_prediction_table, load_prediction_table, scored_segment_input, partition_overlaps_segment, and the two alignment assertions assert_decision_order / assert_table_aligned"
  - "models/conversion.py -- pred_return_to_price (the ONE site) and neutral_fill_null_predictions (returns its substitution count, refuses an all-substituted column)"
  - "harness/errata.py -- maintain_order=\"left\" on the masking join"
  - "tests/fixtures/prediction_span.py -- a tmp_path predictions-tier builder plus prediction_table_from_frame's positional emission"
affects: [07-06, 07-07, 07-08, 07-09, 07-10, 07-11, "Phase 8's LightGBM and transformer tracks", "Phase 9's Stage 2 sweep"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "A holdout gate whose dates are derived TWO HOPS and then NARROWED: scored segment manifest -> its upstream feature manifests -> the partition dates whose etime range overlaps THIS segment. Deriving without narrowing is a false refusal, and a false refusal teaches the reader to route around the gate"
    - "np.array_equal over a whole key column as a STRONGER statement than a join: it proves two row sequences are the same sequence, where a join proves only that every key found a partner -- which a permutation also satisfies"
    - "A substitution spelled as a neutral INPUT (a zero return) rather than as a neutral OUTPUT (writing mid), so the conversion stays at exactly one call site and the round-trip test covers the substituted rows too"
    - "A regression guard that says so in its own docstring: the errata order test passed before maintain_order=\"left\" as well as after, and claims only to be what fails if a future polars default changes"
    - "An exact count assertion whose number only the intended mechanism can produce, with the mechanism named: a one-sided simulator signal gives exactly ONE trade because the kernel is flip-only, so the alternating case (1,799 of 1,799 flips, sides asserted) is what proves both triggers fire"

key-files:
  created:
    - mvp/models/predictions.py
    - mvp/models/conversion.py
    - mvp/tests/fixtures/prediction_span.py
    - mvp/tests/models/test_prediction_store.py
    - mvp/tests/models/test_prediction_table.py
    - mvp/tests/models/test_neutral_pred.py
  modified:
    - mvp/data/store.py
    - mvp/harness/errata.py
    - mvp/tests/harness/test_errata.py

key-decisions:
  - "`schema_version` is asserted from the MANIFEST, not from a body column, which is where `features/normalize.py` keeps its own. D-07-16 pins this tier's body to exactly three columns and the plan's own acceptance criterion asks for `body.schema == dict(PREDICTION_TABLE_SCHEMA)` as a single dict comparison -- a fourth column repeating a constant over 16M rows would contradict both."
  - "The loader's date derivation is NARROWED to the partitions overlapping the scored segment, which the plan did not ask for. Without it a `val` table's scored dates are every day the POOL spans, so declaring the held-out window (Phase 8 success criterion 4) would make every already-stored val table unreadable -- a refusal that is wrong in the safe-looking direction."
  - "`inputs` gained ONE entry the writer builds itself (`role: \"scored_segment\"`), carrying the segment manifest's absolute path, its file sha256, the segment name and the FULL 64-hex predictor_id. The path truncates the predictor id to 16 characters and cannot be inverted, and the three-column body has nowhere to keep it -- so without that entry a stored table cannot say whose predictions it holds."
  - "ONE unlisted test file: `tests/models/test_prediction_store.py`. Task 1's five acceptance criteria all demand tests and the plan named no file for them; putting them in Task 3's `test_prediction_table.py` would have mixed the storage contract with the alignment proof in a file whose five test names the plan specifies exactly. 07-04 set the precedent for reporting an unlisted file rather than silently adding one."
  - "`partition_overlaps_segment` needed a THIRD clause the obvious two-comparison form does not have: a zero-width segment (the held_out sentinel) is satisfied by any partition straddling it, so `empty` read as `matches everything`. Caught by this plan's own boundary-case test before any integration test could have."
  - "FCST-04 is NOT marked complete, following 07-01, 07-03 and 07-04. A table that can round-trip is FCST-04's mechanism, not its satisfaction: no estimator has been fitted, no real table stored, and nothing committed to the lake."

patterns-established:
  - "Pattern: state which observable a mutation should move BEFORE trusting a green result, and when a mutation is expected to survive, say so in the plan and then report that it did. `maintain_order=\"left\"` survived its removal with 17 tests passing, exactly as 07-05-PLAN predicted -- the order holds today, which is why the assertion was written while it passes."
  - "Pattern: a mutation that survives is a test gap report, not a formality. Replacing the narrowing's CALL with `if True` passed the whole suite because the fixture writes ONE feature partition; the predicate had a unit test and its use did not. Closed in its own commit with a re-signed two-upstream segment manifest, then the mutation was re-run and caught."
  - "Pattern: when the hazard cannot be caught, prove which witness DOES see it. A `pred` array rolled by one row leaves fill_count identical (23 -> 23) and moves only the trade-log digest and the closed P&L -- so the test hashes the SLICED trade log rather than watching the trade count."
  - "Anti-pattern recorded: deriving a gate's input from caller-supplied provenance. `features/normalize.py`'s `_train_dates` unions `inputs[].dates`, so a caller that omits an input narrows the gate. The predictions loader instead derives everything from the ONE entry the writer builds itself, and fails closed on none, on two, and on bytes that moved."

requirements-completed: []
# FCST-04 is this plan's frontmatter requirement and is deliberately left
# Pending. See key-decisions.

# Metrics
duration: ~1h15m
completed: 2026-09-25
---

# Phase 7 Plan 05: A Prediction Table That Cannot Quietly Shift

**A stored prediction column proven to be the same row sequence as the frame it
scores — and one named multiplication where a predicted return becomes the price
the simulator compares against the book.**

## Performance

- **Duration:** ~1h15m (17:20 to 18:35)
- **Tasks:** 3 of 3, plus one gap a mutation check found and a fourth commit closed
- **Files:** 6 created, 3 modified
- **Tests:** 1198 → **1227** (+29)
- **`pytest tests/models` wall clock:** 17.5 s for 92 tests — it runs on every commit
- **Full suite:** 1227 passed in 3m50s

## Commits

| Commit | Subject |
|---|---|
| `17ee83b` | `feat(07-05)` a prediction table whose provenance says which rows it scored |
| `0538083` | `feat(07-05)` one site where a predicted return becomes a price |
| `e79c6f6` | `test(07-05)` a one-row shift refused, and the same shift nothing can catch |
| `158db4b` | `test(07-05)` the date narrowing a mutation check found untested |

## The Alignment Proof

Three claims, each measured on the fixture's 1,799-row `val` frame.

**A table written, manifest-addressed and read back is still the same rows.**
The emission is one `pl.DataFrame({...})` taken positionally off the scored
frame — no join, no sort, no group_by, nothing that *could* misalign — and
`assert_table_aligned` on the read-back table compares:

| check | statement |
|---|---|
| `table.height == frame.height` | same number of rows |
| `np.array_equal(table["etime"], etime)` | the same key SEQUENCE, not merely the same key SET |
| `np.array_equal(table["decision_seq"], frame["decision_seq"])` | the cross-check column agrees too |

`etime` is asserted strictly ascending first, which is what makes the second
row of that table a comparison of distinct keys rather than of a multiset
(correction C1: 60,926,503 distinct etimes in 60,926,503 rows).

**The one-row shift, observed firing.** Rolling `etime` and `decision_seq` by
one row raises, naming the etime sequence. Rolling `decision_seq` alone raises
on the cross-check — the half the etime assertion cannot see.

**And the shift nothing can catch, with the witness that does see it.** Rolling
`pred` alone leaves every key correct, so `assert_table_aligned` passes, and it
must. What moves:

```
pred rolled by one row: trades 23 -> 23, closed ticks 4 -> 6,
                        digest 2dda15737764 -> 858387555fd0
```

**The trade count does not change.** A test watching `fill_count` would have
called the two runs identical. This is why the table is emitted positionally
from the scored frame in the first place, and why the test hashes the trade log
sliced to `[:fill_count]` (the tail past it is uninitialised `np.empty` memory).

## The Conversion, and Where `mid` Goes

`pred_return_to_price(mid, pred_return)` is `mid * (1.0 + pred_return)` in
float64 and nothing else. Its round trip, measured across five midprices
($0.10 to $70,000.05) and twelve signed return magnitudes from 1e-9 to 1e-1:

```
(price / mid) - 1.0  recovers pred_return with error <= 4 * spacing(|r| + 1.0)
```

on every one of the 60 pairs — the tolerance stated in ULPs of the operands
rather than as a round number. A zero return converts to the mid **bit for
bit** (`np.array_equal`, not `allclose`), which is what makes the neutral fill
a statement about the conversion rather than about a value that rounds nicely.

**`mid` reaches nothing but that function.** Asserted three ways rather than
claimed:

| check | result |
|---|---|
| `mid * (1.0 + ...)` sites at CODE level in `models/conversion.py`, by AST | **1** (line 124). The module contains exactly one multiplication in total |
| `price_to_ticks` CALLS in `models/`, by AST | **0**. Its three textual hits in `conversion.py` are docstring prose (lines 18, 35, 37) explaining why the mid must never go through it |
| reads of the `mid` COLUMN anywhere in `models/` or `tests/models/` | **5**, all in tests: 2 pre-existing in `test_fixture_rig.py`, and 3 new ones that pass it straight into `pred_return_to_price` / `neutral_fill_null_predictions`. No production module outside `models/conversion.py` reads it |

The design matrix is `("imb_top", "ofi", "trade_flow")`, three columns, and the
end-to-end test asserts `X.shape == (height, 3)`.

**Why the substitution is a zero RETURN and not a write of `mid`.** Both give
the same bits; only one keeps the conversion at a single site. The research
sketch's `np.where(missing, mid, mid * (1.0 + pred_ret))` is a second copy of
the rule that no round-trip test covers, and it would have broken the plan's
own acceptance criterion of exactly one such expression.

**The neutrality it rests on, measured here:**

```
pred = mid * (1 + 0.0) on 1799 val rows: 0 trades
one tick beyond the quote:  long-only=1  short-only=1  alternating=1799
```

The one-sided counts are exactly 1 because the kernel is **flip-only** — after
the row-0 entry the same-direction trigger is never evaluated again — and both
the count and the `side` are asserted, because 07-03 nearly accepted such a
single trade as proof that a signal worked. The alternating case is the number
that proves both triggers fire repeatedly: 1,799 flips on 1,799 rows, sides
asserted to alternate `+1, -1, +1, ...`.

## The End-to-End Slice

No estimator, no `models.frozen`, no sklearn: hand-picked coefficients, one
numpy dot product, through the stored table and back.

```
perfect foresight on val (1799 rows): trades=98 neutral-filled=9 closed=(311 ticks, $0.0311)
hand model on val: intercept=3.00e-07 coef=(1.5e-07, 7.5e-08, 4.5e-08)
                   trades=23 closed_ticks=4 usd=+0.000400
```

The ceiling is **re-measured, not cited**: 98 trades, matching 07-03's
measurement on the same fixture at the same knobs, and computed through the
SAME conversion site as the model's prediction (D-07-33), so the bound and the
thing it bounds can never be converted by two different rules. The coefficient
scale is derived rather than groped for — one tick at a $70,000 mid is 1.43e-6
in return units and the long trigger needs `mid + 1.5 ticks`, so a return must
exceed ~2.1e-6 to cross; at feature std ~6 a coefficient of 1.5e-7 puts ~4% of
rows past it. The test also asserts that **dropping the intercept changes the
run**, so a model that happened to be intercept-free could not pass with
`intercept +` deleted.

`0 < trades < ceiling` is stated in the test's own docstring as a tuning
constraint on this fixture and **not a law** — a noisy model can trade more
often than perfect foresight does; crossing the spread every row is a way to
lose money quickly, not a way to be right.

## Stored Size, Measured

Through the real `write_prediction_table`, zstd, against `tmp_path`:

| rows | etime spacing | pred | bytes | B/row | projected for the real `val` |
|---|---|---|---|---|---|
| 1,799 | arithmetic | smooth | 26,522 | 14.74 | 229.1 MiB |
| 100,000 | arithmetic | smooth | 1,243,256 | 12.43 | 193.2 MiB |
| 100,000 | irregular (ms grid) | noisy | 1,305,957 | 13.06 | 202.9 MiB |
| 2,000,000 | arithmetic | noisy | 24,260,037 | 12.13 | 188.5 MiB |
| 2,000,000 | irregular (ms grid) | noisy | 25,410,762 | 12.71 | 197.4 MiB |

**Against D-07-15/C5's expectation: the bytes per row reproduce exactly, the
row count does not.** C5's 91.2 MiB at 7.86M rows is **12.167 B/row**, and the
measurement above lands at 12.1–12.7 B/row once the per-file overhead stops
dominating (the 1,799-row fixture table is 14.7 B/row — a floor artefact, not a
rate). But 7.86M rows is one day: the approved manifest `807125015b...` admits
**16,294,059** rows to `val`, two days under Option A, so the one table plan
07-11 stores will be about **190 MiB**, not 91.2. D-07-15's decision — store
only the winner — is unaffected and is if anything better supported. Logged to
`deferred-items.md` as an input to 07-11, because D-07-25 makes that manifest's
bytes and mtime `stat()`-checked on every commit from then on.

`pred` being random rather than smooth costs 0.5%; float64 noise does not
compress either way. Irregular etime gaps cost ~0.6 B/row.

## Mutation Checks

Ten, each with its three hashes, the test that caught it, and — stated before
the run, because 07-04 found a mutation that changed the file and no observable
— what the edit was supposed to move.

| # | Mutation | before → mutated → restored | observable it should move | caught by |
|---|---|---|---|---|
| M1 | `dates=[]` removed from `write_prediction_table`'s `issue_manifest` call | `3dc395574c121fba` → `5c4ecddaf45b9ad1` → `3dc395574c121fba` | the writer raises instead of returning a manifest | **9 tests**, all with `KeyError: 'date'` at `data/store.py:441` |
| M2 | the `etime` `array_equal` check in `assert_table_aligned` | `3dc39557…` → `2f925300127fa677` → `3dc39557…` | the rolled-keys `pytest.raises` finds nothing to catch | `test_a_table_shifted_by_one_row_is_refused_naming_the_etime_sequence` |
| M3 | `mid * (1.0 + pred_return)` → `mid * pred_return` | `dbe2a21bec21000c` → `7f4aa475805fd5a5` → `dbe2a21b…` | the recovered return, and the bit-identity of a zero return | 4 tests, incl. `test_the_return_to_price_conversion_round_trips_to_float64_roundoff` |
| M4 | the all-substituted refusal | `dbe2a21b…` → `2a5b94318bc523b7` → `dbe2a21b…` | an all-NaN column returns instead of raising | `test_every_prediction_null_raises_rather_than_reporting_a_clean_zero_trade_run` |
| M5 | `return price, count` → `return price, 0` | `dbe2a21b…` → `9838db3b2b7c31d7` → `dbe2a21b…` | the reported substitution count (4 injected; 9 in the ceiling run) | 3 tests, incl. the end-to-end `ceiling_fills > 0` |
| M6 | `maintain_order="left"` removed from the errata join | `590e6e726ab787de` → `0d73e0f31a8d2688` → `590e6e72…` | **nothing, expected** — polars preserves this order today | **SURVIVED: 17 passed.** See below |
| M7 | `steps > 0` → `steps >= 0` in `assert_decision_order` | `3dc39557…` → `e34c379144a958db` → `3dc39557…` | a repeated etime stops being refused | `test_a_frame_that_is_not_strictly_etime_ascending_is_refused_before_any_simulation` |
| M8 | the narrowing at its CALL SITE → `if True` | `3dc39557…` → `6c4396f7332bb5c0` → `3dc39557…` | the derived date set widens to every upstream day | **SURVIVED: 21 passed.** Gap closed, then caught — see below |
| M9 | the write-once guard back to WR-05's unreachable `final_path.exists()` | `3dc39557…` → `21aa2723ea17f2f6` → `3dc39557…` | a second table for one triple lands beside the first | `test_a_second_table_for_the_same_triple_is_refused_naming_the_existing_part_file` |
| M10 | `assert_not_quarantined` skipped in the loader | `3dc39557…` → `ef927ba766b44b3b` → `3dc39557…` | a quarantined scored day stops refusing the read | 2 tests, incl. `test_load_prediction_table_refuses_a_table_whose_scored_day_is_held_out` |

**M6 survived by design, and that is the plan's own stated expectation.** The
errata order test passed before `maintain_order="left"` was added as well as
after; it is a regression guard against a future polars default change, not a
demonstration that the one-liner works, and its docstring says exactly that
rather than implying otherwise.

**M8 survived and was a real gap.** `partition_overlaps_segment` had a
boundary-case unit test; its CALL SITE did not, because
`build_model_span_fixture` writes exactly ONE feature partition and one
partition cannot tell narrowing from no narrowing. Closed in commit `158db4b`
with a re-signed segment manifest naming a second upstream feature manifest ten
days past the covered end, and both directions of the gate asserted:
quarantining the day the table never read does not refuse it, quarantining the
day it did still does. Re-run afterwards, M8 now fails
`test_the_scored_dates_exclude_an_upstream_day_the_segment_never_covered`.

## Deviations from Plan

### 1. One unlisted file: `mvp/tests/models/test_prediction_store.py`

Task 1's acceptance criteria demand five tests ("each pinned by its own test")
and the plan's `files_modified` names no file for them. Task 3's
`test_prediction_table.py` is specified down to its five test names and is
about alignment, not bytes. A separate file keeps the storage contract — the
tier, the path, the write-once refusal, the manifest's seven keys, the loader's
two gates — where a reviewer would look for it. 17 tests. 07-04 set the
precedent for reporting an unlisted file rather than adding one quietly.

### 2. `schema_version` is asserted from the manifest, not from a body column

The plan says "assert the `schema_version` set equals
`{PREDICTIONS_SCHEMA_VERSION}`", which is `features/normalize.py`'s wording and
reads a per-row column there. This tier cannot: D-07-16 pins the body to three
columns, and the same acceptance criterion asks for
`body.schema == dict(PREDICTION_TABLE_SCHEMA)` as a single dict comparison. A
fourth column repeating `1` over 16M rows would contradict both. The manifest's
own top-level `schema_version` is checked instead, and the constant's docstring
records why it lives there.

### 3. The derived dates are NARROWED, which the plan did not ask for

The plan says to derive the dates "the way `_train_dates` does". Copied
literally that yields every day the pool spans. Declaring the real held-out
window is Phase 8's success criterion 4, and on the day it happens every
already-stored `val` table would become unreadable for a train day it never
scored. A false refusal is not a safe failure — it is the thing that teaches
the next reader to route around the gate. So the derivation keeps only the
partitions whose etime range overlaps the scored segment, `partition_overlaps_
segment` is a named predicate with its boundary cases tested, and M8 above is
the reason its call site is tested too.

### 4. `inputs` carries one entry the writer builds itself

`_train_dates`' shape cannot resolve a segment manifest (it lives in
`registry_root/segments/`, not `manifests/<dataset>/`, and has no partitions),
and a `features_norm` input resolves but carries no dates. So
`write_prediction_table` always prepends a `role: "scored_segment"` entry with
the segment manifest's path, file sha256, segment name and full 64-hex
`predictor_id`; the caller's own `inputs` stay pure provenance and are never a
gate input. Everything the loader needs hangs off that one entry, and it fails
closed on none, on two, on a missing file, on moved bytes, and on a segment
name the manifest does not declare.

### 5. One bug this plan's own test caught before anything integrated

`partition_overlaps_segment`'s obvious two-comparison form returns True for a
ZERO-WIDTH segment — the `held_out` sentinel — because any partition straddling
a zero-width range satisfies both halves. "Empty" read as "matches
everything". Fixed with an explicit `start_ns >= end_ns` clause, and the
boundary-case test that caught it is committed.

## The `dates=[]` Transcript

Required by the plan's acceptance criteria, and pinned two ways.

**As a source mutation (M1 above).** With `dates=[]` deleted from the
`issue_manifest` call, nine tests fail identically:

```
E       KeyError: 'date'
data/store.py:441: KeyError
```

`covered_dates = dates if dates is not None else sorted({p["date"] for p in
partitions})` is computed BEFORE `if tier not in BY_DATE_INDEXED_TIERS`, and a
predictions partition entry has no `date` key.

**As a committed test.** `test_no_by_date_pointer_is_written_and_omitting_
dates_raises_a_bare_key_error` asserts both halves: after a real write the
dataset has no `by-date/` directory at all, and the same partition entry
re-issued with `dates` omitted raises `KeyError: 'date'`. The explicit argument
is what makes the writer work, not a tidiness choice — and that is the
assertion that fails if someone "cleans it up".

## Look Budget

`harness.budget.look_count` read directly against
`/Volumes/ProjectsSSD/aihedgefund/mlflow`, before the first commit and again
after the last:

| manifest | `val` | `oof_block_0..4` |
|---|---|---|
| `97964cb2...` (3-day Phase 5 reference) | 0 | 0, 0, 0, 0, 0 |
| `807125015b...` (the approved 7-day geometry) | 0 | 0, 0, 0, 0, 0 |

**`harness.accessor.materialize` was never called against the real lake in this
plan.** It is called 6 times inside the new tests (2 in `test_neutral_pred.py`,
4 in `test_prediction_table.py`, none in `test_prediction_store.py`, which does
not need a real frame), every one against a
`tmp_path` lake with a `tmp_path` MLflow store created by
`tests/models/conftest.py`'s autouse fixture. Nothing was written to
`/Volumes/ProjectsSSD/aihedgefund/lake`, no `predictions` manifest was
committed, and `mvp/data/lake_registry/` is byte-unchanged.

## Threat Register Outcomes

| Threat ID | Disposition | How |
|---|---|---|
| T-07-17 (post-join row order) | mitigated | `assert_decision_order` at every entry and on every read-back; `assert_table_aligned`'s two `array_equal` checks; positional emission from the scored frame; `maintain_order="left"` on the errata join. M2 and M7 prove the assertions bite; M6 documents that the join argument's removal is invisible today, which is why the assertions exist rather than the argument alone |
| T-07-18 (a partition rewritten in place) | mitigated | manifest-addressed with sha256 re-verified on every read (twice, by `resolve_manifest` then `read_verified_partitions`); the write-once PARENT glob refuses a second `part-*.parquet` in one triple's directory. M9 proves the guard is reachable, which the WR-05 form it replaces was not |
| T-07-19 (the quarantined tier) | mitigated | `load_prediction_table` keeps `assert_not_quarantined` with dates derived two hops and narrowed to the scored segment; M10 proves it is live, and the narrowing test proves it is live in both directions |
| T-07-20 (a null-filled prediction column) | mitigated | `neutral_fill_null_predictions` returns its count (M5 proves the count is read) and refuses an all-substituted column outright (M4). `pred = mid` measured at 0 trades, with the alternating counterpart at 1,799 proving the fixture could have traded |

## What Was NOT Done

- **Nothing was written to the real lake and no `predictions` manifest was
  committed.** The first real table is plan 07-11's, and D-07-25 is why: a
  committed prediction-table manifest makes those bytes and that mtime
  `stat()`-checked on every commit forever after.
- **No estimator was fitted and `models.frozen` was never imported.** The
  end-to-end test's model is three hand-written coefficients and a numpy dot
  product. Plan 07-06 brings sklearn's four estimators; hard constraint 8
  forbade the import here because 07-04 lands in the same wave.
- **No normalization artifact is loaded.** The end-to-end test uses RAW
  features, stated in the helper's own docstring: the FEAT-05 artifact is
  resolved by manifest at fit time (D-07-11), this fixture has none, and a
  linear prediction is invariant under the per-feature rescaling the
  coefficients absorb. Nothing this plan asserts can tell the difference.
- **FCST-04 is not marked complete.** A table that round-trips is its
  mechanism, not its satisfaction.
- **`mvp/spec.md` was not touched.** Plan 07-07 owns the
  `## Stage 1 — Regression track` section, including the prediction-table
  contract; `check_spec_diff` watches only `spec/features.toml` and
  `spec/labels.toml` and passed on every commit.
- **`validate="1:1"` was NOT added to the errata join**, per the plan: that
  changes a Phase 5 module's refusal semantics and is a different decision
  from asking for an order.
- **No guardrail was edited.** D-07-38 confirmed: all three manifest scanners
  glob `manifests/**`, so the new `predictions` dataset is covered the moment a
  manifest exists. `lake_registry/predictors/` is the one that needs the
  per-directory loop extension, and it lands with the directory in 07-10.
- **`tests/fixtures/lake` gained no predictions partition**, so the
  CI-fixture leg of `check_no_manifest_rewrite --full` still scans
  curated/features bytes only. Logged to `deferred-items.md`.
- **`BY_DATE_INDEXED_TIERS` is byte-unchanged**, and a test now asserts the
  whole frozenset rather than only this tier's absence from it.

## Self-Check: PASSED

All six created files exist on disk; all four commit hashes resolve in
`git log --all`; `git diff HEAD~4 HEAD -- mvp/data/lake_registry/` is empty, so
the committed registry is byte-unchanged and no manifest was added, deleted or
rewritten.
