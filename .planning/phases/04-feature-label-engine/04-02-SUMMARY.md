---
phase: 04-feature-label-engine
plan: 02
subsystem: feature-engine
tags: [lake-tier, manifest, provenance, holdout, dq-report, containment, write-once]

# Dependency graph
requires:
  - phase: 03-data-layer-backfill-ingest-lockbox
    provides: data/store.py's issue_manifest/resolve_manifest/_enforce_dq_pause/read_verified_partitions (content hashes, tier containment, the DQ pause gate); data/dq/report.py's per-date report.parquet and normalize_row; data/dq/checks.py's precomputed-build_stats check shape; data/lake_paths.py's LAKE_REGISTRY_ROOT
  - phase: 02-stage-0-living-spec-ci-guardrails-tracking
    provides: spec/catalogue.py's get_feature/get_label + tools/check_catalogue_completeness.py's literal-name rule; spec/render.py + tools/check_spec_diff.py; tools/check_lockbox_containment.py
provides:
  - mvp/features/tier.py -- FEATURE_ROW_SCHEMA/BOOKKEEPING_COLUMNS, feature_partition_path, write_feature_partition (the single NaN->null site), curated_manifest_input, issue_feature_manifest, assert_buildable, load_features
  - mvp/data/holdout.py -- QuarantinedDates/quarantined_dates/assert_not_quarantined over a git-committed, dependency-free holdout registry
  - mvp/data/dq/feature_checks.py -- six feature-tier checks + FEATURE_BUILD_STATS_KEYS (the build<->DQ contract) + feature_build_stats_path
  - data/store.py's FEATURES_TIER/FEATURES_NORM_TIER/BY_DATE_INDEXED_TIERS
  - data/dq/report.py's build_feature_report_rows_for_date, assembled into the same date's report.parquet
affects: [04-feature-label-engine/04-05, 04-feature-label-engine/04-07, 05-fold-harness, 09-lockbox-gate]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "A new lake tier is a tier NAME plus its own loader, never a flag on the existing one: load_features mirrors load_curated one tier over and passes its own expected_tier"
    - "Tier allowlist as an explicit frozenset (BY_DATE_INDEXED_TIERS) with the excluded side asserted directly, rather than an inequality against one tier name"
    - "One NaN->null conversion point at the WRITER, never per-producer: three modules legitimately produce NaN, and a fourth would ship it unnoticed"
    - "A refusal-carrying set subclass (QuarantinedDates with .declared) so 'not armed yet' and 'armed and empty' are different values, not the same empty set"
    - "Gate ORDER asserted with monkeypatched sentinels, not assumed: integrity, then holdout, then DQ pause, then read"
    - "Lockbox-literal tests go in the file check_lockbox_containment already sanctions; a new test file proves the same code path against a non-quarantined escape target (raw/) so no new blanket sanction is needed"

key-files:
  created:
    - mvp/features/tier.py
    - mvp/data/holdout.py
    - mvp/data/dq/feature_checks.py
    - mvp/tests/features/test_tier.py
    - mvp/tests/features/test_holdout_refusal.py
    - mvp/tests/store/test_features_tier_containment.py
    - mvp/tests/dq/test_feature_checks.py
    - mvp/tests/fixtures/feature_tier.py
  modified:
    - mvp/data/store.py (FEATURES_TIER, FEATURES_NORM_TIER, BY_DATE_INDEXED_TIERS; issue_manifest tests set membership)
    - mvp/data/dq/report.py (build_feature_report_rows_for_date + FEATURE_CHECKS, called from build_report_rows_for_date)
    - mvp/data/dq/checks.py (four new threshold dataclasses on DQThresholds; one TOML reader, not two)
    - mvp/spec/dq_thresholds.toml (label_gap, feature_label_coverage, feature_row_filters, feature_quantization)
    - mvp/spec.md (re-rendered dq-thresholds table)
    - mvp/tests/store/test_loader_tier_containment.py (two tests: the tier allowlist's excluded half, and load_features refusing a quarantined-tier manifest)
  deleted: []

key-decisions:
  - "No tests/features/__init__.py, contradicting the plan's <interfaces> note: mvp/features/ is a real package and a same-named test package shadows it on sys.path (04-01's Rule 3 fix, and Phase 2's before that). Shared fixtures live in tests/fixtures/feature_tier.py instead -- tests/fixtures/ is an existing package whose name collides with nothing."
  - "The lockbox-literal tests live in tests/store/test_loader_tier_containment.py, the ONE test file check_lockbox_containment already sanctions, rather than extending SANCTIONED_TEST_FILES to two new files. A blanket sanction disables every rule for a file; the alternative cost nothing, because _enforce_tier_containment cannot tell what is on the other side of an escape -- tests/store/test_features_tier_containment.py exercises the identical code path with raw/ as the escape target."
  - "build_stats is NOT written by issue_feature_manifest. Phase 3's build_curated_day writes its own stats file and issue_manifest knows nothing about it; the features tier mirrors that exactly. feature_build_stats_path lives in data/dq/feature_checks.py so data/dq/report.py never imports upward from features/."
  - "Feature build stats live in lake/features_meta/symbol=<S>/date=<D>/build_stats.json -- a sibling metadata tier of features/, mirroring curated_meta/'s relationship to curated/, so the write-once partition tier holds nothing but decision rows."
  - "DQThresholds gained four fields rather than feature_checks.py growing a second TOML loader. load_dq_thresholds stays the single reader; a second one is drift waiting to happen. (data/dq/checks.py was not in the plan's files_modified -- recorded as a deviation.)"
  - "write_feature_partition takes registry_root=None (not in the plan's signature line) purely so the holdout refusal can run BEFORE the first mkdir. Without it, RP-4's 'writes nothing' assertion would have been vacuous."
  - "At READ time the holdout refusal runs AFTER resolve_manifest, so the partition bytes have already been hashed when it fires. That is the price of integrity-first ordering, which the plan mandates; the guarantee is 'no rows are returned, no DQ verdict formed, no provenance logged', and it is asserted with monkeypatched sentinels."

patterns-established:
  - "FEATURE_BUILD_STATS_KEYS as a named frozenset contract between the build that writes build_stats.json and the checks that read it, with a test that drops each key in turn and requires some check to go failed"
  - "A fixture module under tests/fixtures/ (never tests/<real-package-name>/) shared by three test packages"

requirements-completed: []
requirements-partial:
  - "FEAT-01: the tier the decision-row matrix will live in exists, is addressable and is proven; the matrix itself is Plan 05."
  - "DATA-07: the feature tier's dropped-event counts per filter are delivered as report.parquet rows. Still Pending overall until Plan 05 produces real ones."
  - "DATA-08: the feature tier's half of the holdout is delivered and proven (build and read, D and D+1). The holdout window itself is Phase 5's to declare."

# Metrics
duration: ~2h
completed: 2026-09-19
---

# Phase 4 Plan 02: The Features Tier and Its Holdout Summary

**Decision rows now have an address: a `lake/features/` tier that inherits every Phase 3 guarantee unchanged, reports on itself in the same `report.parquet` as the curated streams, and refuses a held-out date at both the write and the read — including refusing day D because day D+1 is held out, which is how a holdout gets laundered through a neighbour's label tail if nobody checks.**

## Performance

- **Duration:** ~2 h
- **Tasks:** 3 (one commit each)
- **Tests:** **702 before → 748 after** (+46: 6 `test_tier.py`, 16 `test_holdout_refusal.py`, 8 `test_features_tier_containment.py`, 14 `test_feature_checks.py`, 2 added to `test_loader_tier_containment.py`)
- **Files:** 8 created, 6 modified, 0 deleted

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | The features tier — schema, write-once partition, provenance chain | `722352d` | `data/store.py`, `features/tier.py`, `tests/features/test_tier.py`, `tests/fixtures/feature_tier.py`, `tests/store/test_loader_tier_containment.py` |
| 2 | `load_features`, and the holdout refusal covering D and D+1 (RP-4) | `cd8b436` | `data/holdout.py`, `features/tier.py`, `tests/features/test_holdout_refusal.py`, `tests/store/test_features_tier_containment.py`, `tests/store/test_loader_tier_containment.py` |
| 3 | Feature-tier DQ checks and their report rows | `19aea31` | `data/dq/feature_checks.py`, `data/dq/report.py`, `data/dq/checks.py`, `spec/dq_thresholds.toml`, `spec.md`, `tests/dq/test_feature_checks.py` |

## Accomplishments

- **`lake/features/` is a tier, not a directory.** Content-hashed write-once partitions, a git-committed manifest body, a by-date pointer, and an `inputs[]` chain naming every curated manifest the day was built from — each with its absolute path, that file's sha256, its `row_count`, and the **role it played** (`l1_day`, `trade_day`, `l1_label_tail`). Day D+1's contribution to D's long-horizon labels is therefore recorded in the artifact, not only in the build script.
- **Nothing was re-implemented.** No second hasher, no second containment rule, no second write-once check. `issue_feature_manifest` is 20 lines of delegation; `load_features` is four calls in a fixed order.
- **`FEATURE_ROW_SCHEMA` cannot drift from the catalogue.** Its feature/label names come from LITERAL `get_feature`/`get_label` calls (which `check_catalogue_completeness` requires), and every other column is named in `BOOKKEEPING_COLUMNS`. A test asserts the partition into those two sets is exact, so an uncatalogued feature column cannot be slipped in.
- **One NaN→null site.** `write_feature_partition` converts and then asserts none survived, so the rule holds for the kernel, the labels, the event stream's filler, and whatever fourth producer Plan 05 invents.
- **The holdout is armed before the first date is declared.** `data/holdout.py` reads a git-committed date list using `json`/`pathlib`/`re`/`logging` and nothing else — in particular no `mlflow` import, which is what importing the quarantined tier's own module would have cost. An absent registry returns an empty set that *says* it is undeclared (`QuarantinedDates.declared`); a malformed one raises.
- **`report.parquet` carries all three streams from one pass.** Not appended — recomputed, because `write_report` rebuilds the file wholesale and an appended row is a row the next curated regen deletes.
- **`spec.md` re-rendered** with four new threshold tables carrying the measured 2026-09-13 numbers in their notes.

## RP-4 Transcript

Run as a `./.venv/bin/python3` script against a `tmp_path` lake, with a
holdout registry declaring one date. The refusal happens **before the first
`mkdir`**, so `lake/features/` is not merely empty afterwards — it was never
created.

```
holdout.json declares: [2026-09-13]

write_feature_partition -> QuarantinedDateError:
  feature partition write of 2026-09-13: date(s) ['2026-09-13'] are held out
  for BTCUSDT (declared in .../registry/holdout/holdout.json). The held-out
  window is reachable only through the Phase 9/10 gate protocol, never
  through this path.

lake/features exists                       : False
lake/features/symbol=.../date=2026-09-13/  : False
```

And the D+1 half, with D itself perfectly clean:

```
holdout.json now declares: [2026-09-14] only

assert_buildable(2026-09-13, 2026-09-14) -> QuarantinedDateError:
  feature build of 2026-09-13: its long-horizon label tail reads 2026-09-14:
  date(s) ['2026-09-14'] are held out for BTCUSDT (declared in
  .../registry/holdout/holdout.json). ...
```

The message names **which day was refused and why that day was being read at
all** — `2026-09-13` is being built, `2026-09-14` is what stopped it.

## Mutation Check Results

Each mutation was applied to the real source, the suite was run, the named test was observed failing, and the file was restored from a byte-identical backup (`git status` clean after each).

### Task 1

**(a) `BY_DATE_INDEXED_TIERS` gains the quarantined tier** — 2 tests red:

```
FAILED tests/store/test_loader_tier_containment.py::test_lockbox_manifest_never_repoints_the_curated_by_date_index
FAILED tests/store/test_loader_tier_containment.py::test_a_quarantined_tier_manifest_still_gets_no_by_date_pointer
E  AssertionError: the quarantined tier must never be date-addressable
2 failed, 60 passed
```

**(b) `date` dropped from the partition entry** — observed in BOTH layers, because the question the plan asked ("does the DQ gate get skipped or does it raise?") is only visible with the guard removed:

```
# with issue_feature_manifest's explicit 'date' guard in place:
4 failed, 2 passed      (every test that issues a manifest)

# with the guard ALSO removed, to see where it surfaces downstream:
partition entry keys: ['etime_max','etime_min','mtime_ns','path','rows','sha256','size_bytes']
manifest issued: 460d2f9defbc
_enforce_dq_pause -> KeyError: 'date'
```

**Finding: it RAISES, it does not silently skip the gate.** The plan's contingency ("if it skips instead of raising, that is a finding to record and fix by asserting the key") therefore does not apply. The assertion in `issue_feature_manifest` was added anyway — a `KeyError` two layers down names neither the caller nor the reason.

### Task 2

**(a) `quarantined_dates` degrades to an empty set on a JSON decode error:**

```
E  Failed: DID NOT RAISE ValueError
FAILED tests/features/test_holdout_refusal.py::test_holdout_registry_malformed_fails_closed[{not json at all]
1 failed, 15 passed
```

**(b) the holdout check moved AFTER `read_verified_partitions`** — exactly the split the plan predicted:

```
FAILED tests/features/test_holdout_refusal.py::test_the_quarantine_refusal_precedes_the_dq_gate_and_the_read
E  AssertionError: reached past the quarantine refusal
1 failed, 15 passed
```

`test_load_features_refuses_a_quarantined_date_already_on_disk` **stayed green** under this mutation, as the plan said it would — it asserts the refusal, not its position. The ordering test (monkeypatched sentinels on `read_verified_partitions`, `_enforce_dq_pause` and `_log_provenance`) is what actually covers it.

**(c) `assert_buildable` forgets `next_date`:**

```
E  Failed: DID NOT RAISE QuarantinedDateError
FAILED tests/features/test_holdout_refusal.py::test_build_refuses_when_only_the_NEXT_day_is_quarantined
1 failed, 15 passed
```

Worth keeping: **the first attempt at this mutation was a no-op and passed 16/16.** `ruff format` had joined the two f-string lines after the code was written, so the text substitution silently matched nothing. A mutation that "passes" is indistinguishable from a mutation that was never applied — the second run printed the exact block it removed before running the suite.

### Task 3

**(a) `check_feature_row_filters` returns `ok` on a missing key** — 4 red:

```
FAILED ...::test_row_filters_fail_closed_on_a_missing_key[na_placeholder_excluded]
FAILED ...::test_row_filters_fail_closed_on_a_missing_key[unknown_side_rows]
FAILED ...::test_row_filters_fail_closed_on_a_missing_key[n_trade_rows]
FAILED ...::test_every_check_names_the_stats_keys_it_needs
4 failed, 10 passed
```

**(b) the feature branch left defined but uncalled inside the report assembler** — the careless-refactor mutation, and the one worth transcribing in full:

```
FAILED tests/dq/test_feature_checks.py::test_features_rows_survive_a_report_regeneration
E  AssertionError: a features manifest with no rows of its own is `missing`, which pauses load_features permanently
E  assert 0 >= 6

# and what the resulting artifact looks like:
report.parquet verdicts: ['degraded', 'failed', 'n/a', 'ok']
rows with stream=features: 0
load_features -> DQPauseError: DQ pause: BTCUSDT.features has unacknowledged day(s):
  2026-09-13: missing (no DQ report generated for this date; findings dq_report=missing;
  no acknowledgement file).
```

**The report reads as an ordinary, healthy day while the tier is permanently unloadable.** Nothing about the file says the features rows are absent — only the loader knows, and only when someone tries.

**(c) `[feature_label_coverage] degraded_missing_pct = 100.0`:**

```
E  - degraded
E  + ok
FAILED tests/dq/test_feature_checks.py::test_label_coverage_degrades_past_the_threshold
1 failed, 13 passed
```

The threshold is read from the TOML, not hardcoded.

### Scope proof for `check_lockbox_containment`

Both new modules are genuinely in the scanner's scope (not merely passing because they were never looked at):

```
$ ./.venv/bin/python3 -m tools.check_lockbox_containment
scanned 361 files (116 python, 245 other text)     exit=0

# with a tier-path literal planted in each:
features/tier.py: FAIL: ... features/tier.py:390: literal 'lockbox' path segment: 'lake/lockbox/x'
data/holdout.py:  FAIL: ... data/holdout.py:199: literal 'lockbox' path segment: 'lake/lockbox/x'
# restored -> exit=0
```

## Real-Data Verification (read-only)

Run as a `./.venv/bin/python3` script from `mvp/`, never as a collected test. Nothing was written to the lake; `lake/features/` still does not exist.

**1. The holdout is still undeclared, so every L1 day is still buildable:**

```
$ stat -f "%Sp %N" /Volumes/ProjectsSSD/aihedgefund/lake/lockbox
d--------- /Volumes/ProjectsSSD/aihedgefund/lake/lockbox

holdout registry path  : mvp/data/lake_registry/holdout/holdout.json
holdout registry exists: False
quarantined_dates      : set() declared = False
```

The refusal path is therefore built **ahead of** the first declaration, as D-04-11 requires — and `declared = False` is the loader-visible way of saying "not armed yet", which a bare empty set could not.

**2. Which curated by-date pointers resolve today** (git-committed registry, no lake read needed):

| stream | 09-11 | 09-12 | 09-13 | 09-14 | 09-15 | 09-16 |
|---|---|---|---|---|---|---|
| bookTicker | — | `f4d8d81d298d` | `223b0f2ae928` | `456cf9fe7572` | `50ff26dae810` | — |
| trade | `5685bec3e9c6` | `1b337fb9efbe` | `2a7b4c89cf36` | `1aa00f7c822b` | `68ccb9a367f7` | — |

**Buildable feature days for Plan 05: `2026-09-12`, `2026-09-13`, `2026-09-14`** — exactly the plan's expectation. 09-15 has L1 and trades but no D+1 L1, so its long-horizon label tail has nowhere to read from; under D-04-05's write-once rule it is never built until 09-16's curated manifest exists. No 09-16 partition exists and this plan did not create one.

## Verification Transcript

```
$ ./.venv/bin/pytest tests -q
748 passed in 110.97s           (702 before this plan)

$ ./.venv/bin/ruff check .               All checks passed!
$ ./.venv/bin/ruff format --check .      124 files already formatted
$ ./.venv/bin/python3 -m tools.check_spec_diff              exit 0
$ ./.venv/bin/python3 -m tools.check_lockbox_containment    exit 0 (361 files scanned)
$ ./.venv/bin/python3 -m tools.check_catalogue_completeness exit 0
$ ./.venv/bin/python3 -m tools.check_manifest_id_integrity  exit 0
$ ./.venv/bin/python3 -m tools.check_ms_to_ns_site          exit 0
$ ./.venv/bin/python3 -m tools.check_numba_globals          exit 0
$ ./.venv/bin/python3 -m tools.check_no_manifest_rewrite --full \
      --lake-root tests/fixtures/lake --registry-root tests/fixtures/lake_registry   exit 0

# every commit: all 15 hooks, never --no-verify
ruff check ............ Passed        check_lockbox_containment ...... Passed
ruff format --check ... Passed        check_numba_globals ............ Passed
uv lock --check ....... Passed        check_spec_diff ................ Passed
check_pin_versions .... Passed        check_no_manifest_rewrite ...... Passed
check_ms_to_ns_site ... Passed        check_no_manifest_rewrite --full  Passed
check_catalogue_completeness  Passed  check_manifest_id_integrity .... Passed
check_latest_ban ...... Passed        check_manifest_append_only ..... Passed
                                      pytest (tests, via testpaths) .. Passed
```

## TDD Gate Compliance

**No `test(...)`-only commits exist, and cannot.** The pre-commit `pytest tests -x -q` hook runs on every commit and `--no-verify` is forbidden, so a commit whose tests fail by design cannot land. Each task therefore ran RED first (transcribed below), then landed as one `feat(...)` commit — the same shape Plan 01 used.

RED observations, before any implementation existed:

```
Task 1: tests/features/test_tier.py:23: ModuleNotFoundError: No module named 'features.tier'
Task 2: tests/store/test_features_tier_containment.py:28:
        ImportError: cannot import name 'load_features' from 'features.tier'
Task 3: tests/dq/test_feature_checks.py:22:
        ModuleNotFoundError: No module named 'data.dq.feature_checks'
```

## Deviations from Plan

### 1. [Rule 3 — Blocking] `tests/features/__init__.py` was NOT created

The plan's `<interfaces>` section says to create it if Plan 01 has not. Plan 01 created it, hit the shadowing bug, and **deleted** it — `mvp/features/` is a real package and a same-named test package shadows it on `sys.path`. Creating it would have broken every `from features.tier import ...` immediately. Shared fixtures live in `tests/fixtures/feature_tier.py` instead (`tests/fixtures/` is an existing package; no real `mvp/fixtures/` exists to collide with). This is the fourth appearance of the same lesson, now recorded in STATE.md in its general form.

### 2. [Rule 2 — Missing critical functionality] `write_feature_partition` takes `registry_root`

The plan's signature line is `write_feature_partition(df, *, lake_root, symbol, date)`, but its `key_links` require `assert_not_quarantined` "before any write". Without a registry root the writer cannot consult the holdout registry, and RP-4's "and `date=D/` does not exist afterwards" assertion would have been vacuous — nothing would have refused. Added as `registry_root: Path | None = None` (`None` = the git-committed default).

### 3. [Rule 2] `data/dq/checks.py` extended, though it is not in `files_modified`

`check_feature_label_coverage` must read its threshold from the TOML (mutation (c) proves it does). The only TOML reader in the codebase is `load_dq_thresholds`, so `DQThresholds` gained four frozen dataclasses. The alternative — a second loader inside `feature_checks.py` — is exactly the drift `dq_thresholds.toml`'s header warns about.

### 4. [Rule 1] The lockbox-literal tests were relocated, not granted a new sanction

`tools/check_lockbox_containment.py` flags the bare string `"lockbox"` as a path segment in any file outside its four `SANCTIONED_TEST_FILES`. The plan's `test_tier.py` and `test_features_tier_containment.py` would both have needed the literal. Extending `SANCTIONED_TEST_FILES` grants a file **full** lockbox access (every rule disabled), which is a real weakening of a guardrail this plan was told not to weaken. Instead:

- the tier-allowlist test and the "`load_features` cannot reach the quarantined tier" test went into `tests/store/test_loader_tier_containment.py`, already sanctioned for precisely this;
- `tests/store/test_features_tier_containment.py` exercises the **identical** `_enforce_tier_containment` code path with `raw/` as the escape target — the function cannot tell what is on the other side of an escape, so nothing is lost.

`SANCTIONED_TEST_FILES` is unchanged at four entries.

### 5. [Rule 1] `build_stats` is not a parameter of `issue_feature_manifest`

The plan's signature includes it. Phase 3's `build_curated_day` writes its own `build_stats.json` and `issue_manifest` knows nothing about it; "reuse Phase 3's machinery unchanged" (D-04-07) means the features tier does the same. `feature_build_stats_path` lives in `data/dq/feature_checks.py` so that `data/dq/report.py` never imports upward from `features/`. Plan 05's build writes the file.

### 6. [Rule 1] `issue_feature_manifest` takes `curated_manifests` as prepared entries

Built by a new `curated_manifest_input(dataset, manifest_id, *, registry_root, role)` rather than assembled inline, so the absolute path, the sha256 and the `row_count` come from one place and the `role` is explicit.

**Total deviations: 6.** No architectural change, no Rule 4 checkpoint.

## Threat Flags

None. Every file this plan touched is inside the `<threat_model>`'s existing surface; no new network endpoint, auth path or trust boundary was introduced.

## Known Stubs

None. Every function delivered is fully implemented and exercised. `lake/features/` holds no data because this plan deliberately writes none — that is Plan 05's first task, not a stub.

## Surprises

- **`_enforce_dq_pause` raises rather than skipping on a missing `date`.** The plan explicitly hedged that it might silently skip the DQ gate. It does not: `part["date"]` is a plain subscript and the `KeyError` propagates out of `load_features`. The guard added in `issue_feature_manifest` is for the error message, not for the safety.
- **A text-substitution mutation silently matched nothing** because `ruff format` had rewrapped the target lines after they were written, and 16/16 passing looked exactly like "the test does not cover this". Printing the removed block before running the suite is now the habit.
- **The report-regeneration mutation produces the healthiest-looking broken artifact in this codebase so far.** `report.parquet` carries `ok`/`degraded`/`failed`/`n/a` rows for both curated streams and nothing at all for features; the only symptom is a `DQPauseError` the next time anyone calls `load_features`.
- **`store.validate_dq_acknowledgement` has no stream allowlist**, checked because the plan predicts 09-14/09-15 will legitimately exceed the label-coverage threshold and must be acknowledged rather than threshold-widened. A `BTCUSDT__features__2026-09-14.json` acknowledgement works today. Noted in the TOML's own notes so the next reader does not have to re-verify it.

## Issues Encountered

None blocking.

## Next Phase Readiness — handoff notes

**For Plan 05 (the real build), three things it must do that nothing here can do for it:**

1. **`build_stats.json` must carry every key in `FEATURE_BUILD_STATS_KEYS`**, or the DQ report emits `failed` rows and `load_features` pauses. The contract, verbatim: `n_trade_rows`, `n_decision_rows`, `na_placeholder_excluded`, `unknown_side_rows`, `null_primary_label_rows`, `ret_10s_mid_zero_fraction`, `ret_1s_mid_zero_fraction`, `warmup_rows`, `post_gap_warmup_rows`, `max_window_occupancy`, `window_capacity`, `window_overflow`, `empty_window_rows`, `asof_convention_disagreement_rows` — plus `manifest_id` (or `partition_sha256`), which is what `build_stats_problem` binds the file to its build with.
2. **Its orchestrator prompt's writable-paths list needs `lake/features_meta/**` added**, alongside `lake/features/**`. The stats file is a sibling metadata tier, not part of the write-once partition tier.
3. **The buildable days are `2026-09-12`, `2026-09-13`, `2026-09-14`.** `2026-09-15` waits for 09-16's curated bookTicker manifest.

**For Phase 5 (declaring the holdout window) — T-04-09, accepted, not closed:**

When Phase 5 declares a date held out, it must **move or quarantine the affected partitions**, not merely add the date to `holdout.json`. `load_features` refuses at read time, which covers the code path; the bytes on disk stay readable to anything that bypasses the loader. A read-time refusal cannot delete a file, and pretending otherwise is how a residual becomes a breach.

**CORRECTED 2026-09-19 (04-REVIEW.md CR-01) — the affected partition is not the obvious one.** This note originally said "the partition for the declared date", which points Phase 5 at the wrong file. Declaring day `X` held out affects **`features/date=X-1` as well as `features/date=X`**, and on the lake as it stands today the day before is the only one that exists: day `D`'s last ten minutes of `ret_10min_mid` / `ret_1min_mid` / `ret_10s_mid` are computed from day `D+1`'s prevailing mids, `mid` is stored raw, and the labels are ratios — so `mid_t * (1 + ret_10min_mid)` reconstructs `D+1`'s price path to 1.5e-11 USDT. Measured on the real lake: 77,962 rows of `date=2026-09-14` reconstruct the first 600 s of 2026-09-15's mid path. Declaring `2026-09-15` held out therefore makes **`features/date=2026-09-14`** the partition to move — `features/date=2026-09-15` does not exist and never will until 09-16 is ingested. `load_features` now refuses on `refused_dates_for(manifest)` = partition dates ∪ their `D+1` label-tail days, so the code path is covered for both; only the bytes remain Phase 5's job.

**For Plan 07 (normalization):** `store.FEATURES_NORM_TIER` already exists, so Plan 07 never has to edit `data/store.py`. It is deliberately **not** in `BY_DATE_INDEXED_TIERS` — a normalization artifact belongs to a fold, not to a date.

**For anyone adding a tier:** `BY_DATE_INDEXED_TIERS` is the one line to change, and `test_a_quarantined_tier_manifest_still_gets_no_by_date_pointer` pins its exact contents, so the change cannot be made without someone noticing.

## Self-Check: PASSED

- `mvp/features/tier.py`, `mvp/data/holdout.py`, `mvp/data/dq/feature_checks.py`, `mvp/tests/features/test_tier.py`, `mvp/tests/features/test_holdout_refusal.py`, `mvp/tests/store/test_features_tier_containment.py`, `mvp/tests/dq/test_feature_checks.py`, `mvp/tests/fixtures/feature_tier.py` — all present on disk.
- `mvp/tests/features/__init__.py` — confirmed absent.
- Commits `722352d`, `cd8b436`, `19aea31` all present in `git log` on `feature/phase-04-feature-label-engine`.
- `mvp/spec.md`'s dq-thresholds block round-trips through `check_spec_diff` (exit 0) with the four new tables.
- `lake/features/` confirmed absent on the real lake: this plan wrote no lake data.
