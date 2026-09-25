---
phase: 07-regression-track-vertical-slice
plan: 02
subsystem: harness
tags: [segment-manifest, memory, column-projection, walk-forward, budget]
requires:
  - "harness.segments.issue_segment_manifest (Phase 5)"
  - "the seven v2 BTCUSDT.features manifests for 2026-09-12..18"
  - "harness.errata manifest 22190ad9 (the live, no-op errata gate)"
provides:
  - "segment manifest 807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548 -- the fold geometry every later Phase 7 plan trains and evaluates against"
  - "features.tier.load_features(columns=...) and data.store.read_verified_partitions(columns=...) -- an opt-in column projection with an unchanged default"
  - "harness.segments.UPSTREAM_DERIVATION_COLUMNS -- the named, justified column union issuance reads"
affects:
  - "Phase 8's held-out declaration, which issues more manifests over the same pool and now clears the same memory wall"
tech-stack:
  added: []
  patterns:
    - "a projection narrows the PARSE, never the bytes that are hashed -- so it cannot weaken an integrity gate"
    - "prove a memory claim with /usr/bin/time -l on a write-suppressed dry run BEFORE the irreversible run"
    - "an anti-vacuity width assertion is what makes a bit-identity test mean anything"
key-files:
  created:
    - "mvp/data/lake_registry/segments/807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548.json"
  modified:
    - "mvp/data/store.py"
    - "mvp/features/tier.py"
    - "mvp/harness/segments.py"
    - "mvp/scripts/issue_phase7_segment_manifest.py"
    - "mvp/tests/fixtures/harness_span.py"
    - "mvp/tests/harness/test_segments.py"
    - "mvp/tests/harness/test_kfold.py"
decisions:
  - "The issuance footprint was fixed by reading fewer columns, not by rewriting the derivations -- 2 of 16 columns, output bit-identical including the manifest_id"
  - "_derive_purge_embargo_fields' ['etime'].to_list() was deliberately left un-vectorised: a ~1.7 GB transient that peaks before df_with_age exists, so it never stacks with the frames that caused the problem"
  - "The memory bar is 1.5x the re-measured peak (6.5 GiB), calibrated from a measurement rather than guessed, so a machine in daily use clears it"
metrics:
  duration: "~1h20m"
  completed: "2026-09-25"
---

# Phase 7 Plan 02: The 7-Day Segment Manifest Summary

Issuing a fold geometry over seven days of order-book data used to need more
memory than a laptop somebody is working on can spare; now it needs 4 GiB and
half a minute, and the geometry it produces is the same to the byte.

## What Changed

The issuance derivations read **2 of the feature row's 16 columns**. They were
being handed all 16. Nothing else about them changed.

| derivation | columns it actually reads |
|---|---|
| `_derive_purge_embargo_fields` | `etime` — filters on it, then `["etime"].to_list()` |
| `_derive_oof_training_row_counts` → `kfold.training_rows_for_block` → `purge_embargo.filter_train_rows` | `etime` — filters on it, returns `.height` |
| `_derive_admission_counts` | `etime` — the per-entry slice |
| `row_admission.stale_book_age_ns` | `decision_source_rank` and `etime` |
| `row_admission.apply_admission_policy` | the pre-computed `stale_book_age_ns` column, plus `.height` |

Re-derived from the five functions directly, and it agrees with the plan:
`("etime", "decision_source_rank")`, 9 bytes per row instead of 101.28.
Everything else those functions do is `etime` filtering and row counting, and
`.height` is column-independent — which is exactly why the output cannot move.

`columns` threads through three call sites and defaults to `None` (all columns)
at every one, so `harness.accessor.materialize` and every other reader are
unchanged. The union is a module constant with a docstring naming which
derivation needs each column and the rule for adding a third; `_load_upstream_frame`
takes `columns` as a **required** keyword, so no future caller inherits a
projection it never reasoned about, and it asserts the loaded schema is exactly
what was requested — name, order and dtype.

### Where the 19.35 GiB actually came from

Not the concat: `pl.concat`'s `rechunk` already defaults to `False` in polars
1.41.2 (checked against the installed version — `rechunk: 'bool' = False`), so
there was no copy to remove there. The amplifiers were all downstream and all
proportional to row *width*: seven parsed full-width frames, a 17-column
`df_with_age` alongside the 16-column `df`, and then nine slices of `df_with_age`
of which `train` alone is 44.5M of the pool's 60.9M rows. A projection cuts all
three at the root.

## The Memory Story

| | full-width load | projected load |
|---|---|---|
| peak memory footprint | **19.35 GiB** | **4.04 GiB** |
| maximum resident set size | 9.9 GiB | 4.07 GiB |
| wall clock | ~5 min | **29.6 s** |

Measured with `/usr/bin/time -l` on the real pool both times. Reclaimable memory
at run time was **15.96 GiB**.

The wall clock fell 10× as well, which was not the goal and is worth naming: the
121 s that 07-RESEARCH.md attributed to `_derive_purge_embargo_fields`' Python
`any()` loop was substantially the cost of that loop running under memory
pressure. The loop is unchanged.

**The peak was measured before anything was issued, not after.** A scratchpad
probe monkeypatched `harness.segments._atomic_write_json` to a no-op and ran the
committed script end to end against the real pool: same load, same derivations,
same 99 assertions, zero bytes written. It reported 4.03 GiB, which is what the
bar was then calibrated from — 1.5× rounded up to 6.5 GiB. Had it come back above
8 GiB the plan's instruction was to stop, and this is the measurement that would
have said so, at a cost of 30 seconds and no irreversible action.

The pre-flight's measure is `vm_stat`'s free + inactive + speculative + purgeable
pages: classes a new allocation can take over without anything being paged out,
excluding `active` and `wired`, so it reads as a floor rather than as total RAM.
This is a **reconstruction** — `6351293` recorded the first attempt's numbers
(11.48–13.40 GiB over twelve readings) but not the formula, and this definition
reproduces the same magnitude on the same host. It is written into the constant so
the next reader does not have to guess either.

Both refusal branches were exercised rather than assumed:

- bar raised to 999 GiB → refuses, message naming both `12.05 GiB reclaimable`
  and the `4.03 GiB` peak it is 1.5× of;
- `vm_stat` output garbled → `RuntimeError`, never a silent pass.

## Why The Integrity Gate Is Untouched

Quoted from `mvp/data/store.py:1073-1078`, unchanged by this plan:

```python
        path = Path(lake_root) / part["path"]
        buffer = path.read_bytes()
        digest = hashlib.sha256(buffer).hexdigest()
        if digest != part["sha256"]:
            raise ManifestHashMismatch(str(path), part["sha256"], digest)
```

`read_bytes()` still reads the whole file and `sha256` still hashes all of it
**before** polars is handed the buffer. A projection narrows only what is parsed
out of already-verified bytes, so IN-10's same-buffer guarantee holds identically
at 2 columns or 16. `resolve_manifest` hashes the same way one gate earlier
(`data/store.py:530`, also untouched). `load_features`' four gates —
resolve/integrity, the holdout refusal, the DQ pause, provenance logging — all
run before the read and are unmodified.

## The Bit-Identity Test

`test_a_projected_upstream_load_derives_every_field_identically_to_a_full_column_load`
issues the same `compressed_3seg` layout twice over one fixture partition — once
as shipped, once with `_load_upstream_frame` forced back to `columns=None` — and
compares `admission["counts"]` for all eight entries, `effective_intervals`,
`purged_row_count`, `embargoed_row_count`, `purge_ns`, `embargo_ns`,
`oof_training_row_counts`, then the whole body, then the `manifest_id`. Equal ids
are the strongest single assertion available: the id is a sha256 over the
canonicalised body, so it cannot agree while any derived byte disagrees.

**The anti-vacuity half is what makes that mean anything.** A spy records the
width of the frame each issuance actually loaded and requires `[2, 16]`. Without
it the test passes by comparing a thing to itself — which is what a projection
that silently did nothing, or a monkeypatch that silently failed to take, both
look like. It also pins `len(FEATURE_ROW_SCHEMA) == 16` and the union's contents,
and asserts the admission counts under comparison are non-zero: the fixture now
takes an optional `decision_source_ranks`, because an all-quote span gives every
row age 0 and would have compared two zeros in the one dimension the second
projected column controls. With leading trade rows and a quote every tenth row,
`excluded_undefined` and `excluded_stale` are real, and — because
`stale_book_age_ns` forward-fills — they depend on row **order**, not only on row
count.

### Four mutations, each caught

Green tests claim nothing until something they should catch is broken in front of
them:

| mutation | result |
|---|---|
| issuance passes `columns=None` (projection silently disabled) | widths `[16, 16]`, anti-vacuity assertion fires |
| `read_verified_partitions` ignores `columns` | both new tests fail |
| union narrowed to `("etime",)` | `polars.exceptions.ColumnNotFoundError` — loud, never a silently wrong count |
| `load_features` defaults to the projection | 3 failures, 2 of them in `tests/harness/test_accessor.py` — the accessor's full-width contract is pinned independently of anything added here |

The third is the one that matters most: it proves `decision_source_rank` is
genuinely in the union rather than padding, and that a union too narrow fails
loudly rather than producing a plausible wrong number.

`test_load_features_without_a_columns_argument_still_returns_the_whole_feature_row_schema`
pins the unchanged default by name, order and dtype against `FEATURE_ROW_SCHEMA`
itself. It lives in `tests/harness/test_kfold.py`, not in `test_segments.py`
where the plan put it: `test_segments.py` is not one of hook 17's six sanctioned
`load_features` callers, so writing it there would have failed
`check_harness_accessor_only`. `test_kfold.py` is sanctioned and already imports
the tier for exactly this "must call it directly to check what wraps it" reason.

## The Issued Manifest

```
807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548
```

`code_hash 912afeff6d06b2557a14478a9d7be2adf61f6444` — **clean, no `-dirty`**
(D-07-07), and it names the tree that already contains the projected loader the
body was produced with, which is why the code landed in the two commits before
the body. It differs from anything measured previously because the code changed;
the body has never been issued before, so there is nothing for it to disagree
with.

`layout compressed_3seg` (D-07-02), `budget_allowance 3` (D-07-03), `errata_id
22190ad9` live and masking nothing, `held_out` the zero-width sentinel at
`covered_end_ns` (D-07-06).

**99 assertions passed, 0 failed. Every derived count equals the value
07-RESEARCH.md Q1 pre-measured, and not one expected value was edited to fit.**

| entry | `[start_ns, end_ns)` | raw | stale / undefined / admitted |
|---|---|---|---|
| `train` | `[1789171200002000000, 1789603200000000000)` | 44457598 | 152207 / 55843 / 44249548 |
| `val` | `[1789603200000000000, 1789775999957000000)` | 16468904 | 174845 / 0 / 16294059 |
| `held_out` | `[1789775999957000000, 1789775999957000000)` | 0 | 0 / 0 / 0 |
| `oof_block_0` | `[1789171200002000000, 1789257600001600000)` | 4193137 | 221 / 55843 / 4137073 |
| `oof_block_1` | `[1789257600001600000, 1789344000001200000)` | 6864853 | 0 / 0 / 6864853 |
| `oof_block_2` | `[1789344000001200000, 1789430400000800000)` | 11323695 | 32330 / 0 / 11291365 |
| `oof_block_3` | `[1789430400000800000, 1789516800000400000)` | 12203294 | 61148 / 0 / 12142146 |
| `oof_block_4` | `[1789516800000400000, 1789603200000000000)` | 9872619 | 58508 / 0 / 9814111 |

`effective_intervals {"train": [[1789171200002000000, 1789602600000000000]]}` —
the last 600 s of 09-16 purged by `val`'s leading edge.
`purged_row_count {"train": 3115}`, `embargoed_row_count {"train": 0}`,
`purge_ns 600000000000`, `embargo_ns 1000000000` (both read from
`harness.purge_embargo`'s live constants, not from literals, so a constant drift
fails here).
`oof_training_row_counts` 40242038 / 37488354 / 32997430 / 32094158 / 34530706.
Pool `row_count` sum 60926503; block width 86399999600000 ns, uniform, with
`(train_end − train_start) % 5 == 0` so the last block absorbs no remainder.

## No Look Was Spent

`budget.look_count` reads **0** for `val` and all five `oof_block_*`, on both the
new manifest and the Phase 5 one, before the run and after it — read-only MLflow
queries, which spend nothing. The run wrote 1 registry file, 0 lake files, and
changed no pre-existing registry file. `97964cb2` is byte-identical to its
committed form (D-07-01), confirmed by `git diff` against HEAD as well as by the
snapshot comparison inside the script.

`harness.accessor.materialize` was never called.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] The revised plan file had to be committed before any code**

- **Found during:** Task 1 setup.
- **Issue:** `compute_code_hash()` shells `git status --porcelain` over the whole
  tree, so the uncommitted `07-02-PLAN.md` revision alone would have stamped the
  manifest `-dirty` and the script's own D-07-07 gate would have refused. Task
  2(b) also requires empty porcelain.
- **Fix:** committed the plan revision as `4040660` before touching code.
  Committed rather than stashed — stashing would have hidden the authority file
  for the rest of the run.
- **Commit:** `4040660`

**2. [Rule 3 — Blocking] The default-schema test moved to a hook-17-sanctioned file**

- **Found during:** Task 1.
- **Issue:** the plan places
  `test_load_features_without_a_columns_argument_...` in
  `tests/harness/test_segments.py`, which is **not** in
  `check_harness_accessor_only`'s `SANCTIONED_FILES`. Importing `load_features`
  there would have failed hook 17 on commit.
- **Fix:** the test lives in `tests/harness/test_kfold.py`, which is sanctioned
  and already imports the tier for the same reason. The bit-identity test stayed
  in `test_segments.py` and names no `features.tier` symbol at all — it spies on
  `_load_upstream_frame` instead, which also gives it the width readings the
  anti-vacuity assertion needs. `check_harness_accessor_only` exits 0.
- **Commit:** `1ce8ee8`

**3. [Rule 2 — Missing critical functionality] The fixture could not produce a non-zero admission count**

- **Found during:** Task 1.
- **Issue:** `build_span_partition` hardcoded `decision_source_rank = 0` on every
  row, so `stale_book_age_ns` returned a column of zeros and both
  `excluded_stale` and `excluded_undefined` came out 0. A bit-identity test
  comparing those would have been comparing two zeros — vacuous in the one
  dimension the second projected column controls.
- **Fix:** optional `decision_source_ranks` parameter; `None` reproduces the old
  all-quote column exactly, so every existing caller is unchanged (the full
  suite, unmodified apart from the two added tests, is the proof).
- **Commit:** `1ce8ee8`

**4. [Rule 2 — Missing critical functionality] The bar needed a measurement the plan had no way to take**

- **Found during:** Task 2(a).
- **Issue:** the plan requires the bar be calibrated from the re-measured peak,
  but the only way to measure the peak was to run the issuance — which is
  irreversible and which the bar is supposed to gate.
- **Fix:** a write-suppressed dry run in the scratchpad (`_atomic_write_json`
  monkeypatched to a no-op), measured under `/usr/bin/time -l`. 4.03 GiB, and it
  incidentally verified all 99 assertions before anything was written. The probe
  lives outside the repo and is not committed.
- **Commit:** `912afef`

### Not a deviation, recorded because a reader will wonder

`_derive_purge_embargo_fields`' `["etime"].to_list()` is untouched. It is the
largest remaining single term (~1.7 GB of Python ints for a 44.5M-row train
entry), but it is a *transient* that peaks before `df_with_age` exists, so it
never stacks with the wide frames that caused the problem — and vectorising it
would mean rewriting working, purge-precedence-correct code for no measured gain
at a 4 GiB peak. The reasoning is recorded at the line, not only here.

## Authentication Gates

None.

## Known Stubs

None.

## Verification

- Full suite **1139 passed** (1137 before, + the 2 added tests), no existing test
  modified.
- `check_harness_accessor_only`, `check_single_feature_path`,
  `check_manifest_id_integrity`, `check_manifest_append_only`,
  `check_no_manifest_rewrite --full` all green; all 18 pre-commit hooks passed on
  each of the four commits, none with `--no-verify`.
- `mvp/data/lake_registry/segments/` holds exactly two bodies.
- `git status --porcelain` empty between and after every commit.

## Commits

| hash | subject |
|---|---|
| `4040660` | `docs(07-02)`: revise the plan to cut the issuance footprint before issuing |
| `1ce8ee8` | `feat(07-02)`: read 2 of 16 columns at segment-manifest issuance |
| `912afef` | `feat(07-02)`: refuse the issuance on a host that cannot spare its measured peak |
| `0b8243b` | `feat(07-02)`: issue the 7-day compressed_3seg segment manifest |

Code before body, so the manifest's `code_hash` names a tree that already
contains the loader it was produced with.

## What Is NOT Done

- **Task 3, the human-verify checkpoint, is NOT approved.** The geometry is
  presented and the plan halts here. No later plan may materialize a segment of
  this manifest until the developer responds — once a look is spent, re-issuing
  this geometry is effectively impossible.
- **No look has been spent and no model has been fitted.** Days 17–18 (`val`)
  have never been read as a validation look and are seen exactly once, in plan
  07-11.
- **`tests/lockbox/conftest.py`'s pop-only tracking-root defect is still
  deferred** (07-01 D1), untouched by this plan.
- **The `.to_list()` loop is not vectorised**, by decision, above.
- **`STATE.md` was not updated by this executor.** Plan hard constraint 8 forbids
  the `gsd-sdk state.*` verbs here — `state.add-blocker` was observed silently
  overwriting `stopped_at` and destroying 07-01's handoff note. Left to the
  orchestrator, or to a hand edit.

## Self-Check: PASSED

Every file this SUMMARY claims was created exists on disk; all four commit
hashes resolve in `git log`.
