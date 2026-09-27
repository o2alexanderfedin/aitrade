---
phase: 05-fold-harness-overfitting-controls
reviewed: 2026-09-22T00:00:00Z
depth: standard
files_reviewed: 22
files_reviewed_list:
  - mvp/harness/__init__.py
  - mvp/harness/accessor.py
  - mvp/harness/budget.py
  - mvp/harness/errata.py
  - mvp/harness/holdout_declare.py
  - mvp/harness/kfold.py
  - mvp/harness/negative_log.py
  - mvp/harness/purge_embargo.py
  - mvp/harness/row_admission.py
  - mvp/harness/segments.py
  - mvp/data/dates.py
  - mvp/data/holdout.py
  - mvp/data/lockbox.py
  - mvp/data/ingest/curated_build.py
  - mvp/tracking/mlflow_utils.py
  - mvp/tools/check_harness_accessor_only.py
  - mvp/tools/check_lockbox_containment.py
  - mvp/tools/check_manifest_append_only.py
  - mvp/tools/check_manifest_id_integrity.py
  - mvp/tools/check_ms_to_ns_site.py
  - mvp/tools/harness_negative_log_cli.py
  - mvp/scripts/holdout_declare_dry_run_real_lake.py
findings:
  critical: 1
  warning: 4
  info: 2
  total: 7
status: issues_found
---

# Phase 5: Code Review Report

**Reviewed:** 2026-09-22
**Depth:** standard
**Files Reviewed:** 22 (plus 8 test files inspected for vacuity, not as findings targets)
**Status:** issues_found

## Summary

The two-sided purge/embargo math (`harness/purge_embargo.py`, `harness/kfold.py`) is correct,
well-tested against a span fixture whose own width is asserted before any split test relies on
it, and consistently reused (never re-implemented) at issuance time (`segments.py`) and read
time (`accessor.py`). The manifest-handling guardrails (`check_manifest_append_only`,
`check_manifest_id_integrity`) are unusually thorough and correctly extended to the two new
registries with a genuine per-directory vacuity check. `data/holdout.py` and the errata
resolution added by 05-VERIFICATION-FIX.md both fail closed exactly as documented.

The one finding that matters: **the selection-bias budget — the single mechanism this whole
phase exists to build — can be silently exceeded by any concurrent access**, proven by direct
reproduction below (CR-01). Everything else is a lower-severity crash-safety or robustness gap
in code paths that are not yet exercised in production (the Phase-8 declaration tool) or in
code whose failure mode is already visible in the manifest it writes.

**Not checked in this pass:** the ~22M-row correctness of `_derive_purge_embargo_fields`'s
per-row Python loop against the real committed manifest (VERIFICATION.md already reproduced
its row-conservation arithmetic to the row; not re-derived here). `curated_build.py`'s
`_drop_capture_redelivery_duplicates` dedup logic was read but not independently tested against
real capture data (Plan 0 data-widening code, one step removed from the harness's own
guarantees). No attempt was made to reproduce the deferred `read_segment_manifest`
`manifest_id`-field gap's blast radius beyond what 05-VERIFICATION-FIX.md already scoped.

## Critical Issues

### CR-01: `record_look` has a check-then-act race — the budget can be spent past its allowance by any concurrent caller

**File:** `mvp/harness/budget.py:277-293` (`record_look`), consumed by `mvp/harness/accessor.py:209-215`

**Issue:** `record_look` reads the current spend (`spent = look_count(...)`, line 277), compares it
to `budget_allowance` (line 278), and only *then* creates the MLflow run that increments it
(`start_tracked_run`, line 288). The read and the write are two independent MLflow queries with
no lock, no transaction, and no compare-and-swap between them. Two callers that both read the
same `spent` value before either one's run commits will both pass the `spent >= budget_allowance`
check and both record a look — the allowance is exceeded, silently, with no error raised to
either caller. This defeats D-05-14's whole premise ("exhaustion is a hard refusal") and,
because D-05-12 explicitly rejected a git-JSON counter *in favor of* MLflow specifically to make
the count durable and un-revertible, the race reopens the identical class of vulnerability
(budget bypass) that MLflow was chosen to close — just via timing instead of `git checkout`.

This is not a hypothetical: the project's own stack (Optuna, `05-CONTEXT.md`'s HPO plans) makes
parallel trials calling `record_look` concurrently ordinary, not exotic — `n_jobs > 1` in Optuna,
or two agent processes iterating at once, are the normal way this code will be exercised at
scale, not an edge case.

**Reproduction (proven empirically, not just argued):** ran the following against a scratch
`tmp_path`-style MLflow store (never the real registry — full script and output preserved in the
session's scratchpad, not committed):

```
allowance=1, threads=8
successful record_look calls: 8
final look_count: 8
```

Eight Python threads in a single process, each calling `record_look("mid-race", "val",
tracking_root=..., run_tags=..., budget_allowance=1)`, all eight succeeded and all eight were
durably recorded — an allowance of 1 was exceeded 8-fold, and `look_count` afterward reports the
correct (fully spent) total, i.e. the SQLite store isn't lying — nothing ever prevented the writes.

**Fix:** the check-then-act sequence needs to become atomic. Two concrete options:
1. Hold a process-local lock is not sufficient (cross-process/cross-host is the real threat
   model) — instead, insert the "reservation" row first and then verify no over-allocation
   happened, e.g. write the run with `stage="val_look"` unconditionally, then re-query
   `look_count` for the pair; if the count *now* exceeds `budget_allowance`, mark this run's
   outcome as refused (a tag) rather than trying to un-write it (MLflow runs are not
   transactional either). This makes the allowance a race-tolerant *soft* cap with an audit
   trail of overspend, which is honest about what a shared SQLite file can actually guarantee.
2. Simpler and stronger: wrap `look_count` + the `start_tracked_run` call in a single SQLite
   transaction using `BEGIN IMMEDIATE` against `mlflow.db` directly (bypassing MLflow's own
   connection pooling for just this one section), so the read-then-write is genuinely
   serialized at the file level even across processes.
Whichever direction is chosen, add a regression test that exercises real concurrency (threads
or subprocesses against one `tmp_path` store), not just sequential calls — every existing test
in `tests/harness/test_budget.py` is sequential and would pass unchanged with the race still
open.

## Warnings

### WR-01: `quarantine_feature_partition` moves partition bytes before the new manifest is durably issued, with no rollback on failure

**File:** `mvp/data/lockbox.py:701` (`os.replace`) through `:714-723` (`issue_manifest`)

**Issue:** the sequence is: resolve and verify the old manifest (safe, read-only) → `os.replace`
the partition file from the features tier into the lockbox tier (line 701, irreversible: the old
path is gone) → `issue_manifest(...)` for the new lockbox-tier manifest (line 714). If
`issue_manifest` raises anything between those two steps — a disk-full on the JSON write, a
partition-path collision, any of the several `ValueError`s `data.store.issue_manifest` itself
can raise — the bytes now sit at the new lockbox path with **no manifest anywhere naming them**,
and the OLD features-tier manifest is now permanently broken (`resolve_manifest` on it raises,
since its partition file no longer exists at the recorded path). This is a stricter failure than
the "undeclared-but-partially-quarantined" state `holdout_declare.py`'s own docstring accepts as
the safe direction (lines 14-17 of that file) — that acceptance assumes each partition that
"moved" also got a valid new manifest; this gap means a partition can be moved with *no* manifest
on either side, recoverable only by a human re-running `issue_manifest` by hand against the
orphaned file. Neither `05-05-SUMMARY.md` nor `05-VERIFICATION.md`/`05-VERIFICATION-FIX.md`
discloses this specific ordering as an accepted risk (05-05-SUMMARY.md's own mutation checks
covered `os.replace` → `shutil.copy2` and step-reordering inside `declare()`, not a failure of
`issue_manifest` itself after the rename).

Real-world exposure is currently low: `quarantine_feature_partition` is "never called by any
Phase 5 plan, fixture, or CLI wiring" per its own module docstring — this is Phase 8's first
real invocation, against the single most sensitive data operation in the project (the held-out
declaration). That is exactly why the gap is worth closing before Phase 8, not after.

**Fix:** wrap the `os.replace` + `issue_manifest` pair in a `try/except` that `os.replace`s the
file back to `old_path` on any exception from `issue_manifest`, re-raising afterward — restoring
the pre-call state rather than leaving an unregistered file. Add a test that makes `issue_manifest`
raise (e.g. monkeypatch it) after the move and asserts the original file is back at `old_path`
and the old manifest still resolves.

### WR-02: `write_holdout_registry`'s write-once guarantee is a check-then-act, not atomic

**File:** `mvp/data/holdout.py:270-292`

**Issue:** `path.exists()` is checked at line 270; the atomic tmp-then-rename write happens later
at line 292, with no lock spanning the two. Two concurrent callers (e.g. a Phase 8 declaration
retried after an apparent timeout while the first attempt is still finishing) can both observe
`path.exists() == False`, both pass the "write-once" refusal, and one `_atomic_write_json` call
will silently win over the other — the second declaration is never rejected, and whichever
`dates`/`reason` loses the race is gone without a trace (each individual write is atomic; the
*sequence of two* is not). Given D-05-17 scopes this to a single, rare, human-triggered Phase 8
action, likelihood is low, but the consequence (silently discarding one of two held-out-window
declarations with no error to either caller) is exactly the kind of quiet failure this module's
own docstring says fail-closed exists to prevent ("a registry that silently read as empty... the
failure would look exactly like success" — the same principle applies to a silently-overwritten
write).

**Fix:** use `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)` (or the tmp-file's
`os.link`/`os.rename` with `os.O_EXCL` on a fixed target name) to make "does a file already exist
here" and "create it" a single atomic filesystem operation, rather than a Python-level
check-then-write.

### WR-03: `issue_segment_manifest` refuses a starved `train` entry but silently accepts a starved individual `oof_block`

**File:** `mvp/harness/segments.py:173-188` (`_derive_oof_training_row_counts`), contrast with `:416-451` (`_train_effective_intervals`, which raises on starvation)

**Issue:** `_train_effective_intervals` (called from `_derive_purge_embargo_fields`) raises
`ValueError` if a `train` entry's own effective interval is fully purged/embargoed away
(starvation refusal, D-05-14's remedy path). `_derive_oof_training_row_counts` computes the
analogous quantity for each `oof_block` — `kfold.training_rows_for_block(...).height` — and
just records it into `oof_training_row_counts`, even when that height is `0`. Nothing in
`issue_segment_manifest` refuses issuance when one or more OOF blocks are starved to zero
training rows; the manifest is written and looks otherwise valid. `mvp/tests/harness/test_kfold.py`
and `test_segments.py` both assert `oof_training_row_counts` is *present*, but neither asserts a
starved-block manifest is refused, nor exercises a fixture narrow/`k`-large enough to produce a
zero count — confirmed by grep, no such test exists.

A `0`-row OOF block isn't hidden (a downstream consumer reading the manifest sees it), so this
is lower severity than a silent failure, but it is inconsistent with the sibling starvation
check one function up, and a careless Optuna loop iterating `k` blocks by index without checking
`oof_training_row_counts[name] > 0` first would train on an empty frame for that fold without
any harness-level refusal telling it not to.

**Fix:** after computing `oof_training_row_counts` in `issue_segment_manifest`, raise the same
class of `ValueError` `_train_effective_intervals` does for any block whose count is `0`, naming
the block and suggesting a smaller `k` or a wider `train` entry.

### WR-04: MLflow `filter_string`s are built by raw f-string interpolation of caller-supplied values

**File:** `mvp/harness/budget.py:174-177` (`look_count`), `mvp/harness/negative_log.py:231-233` (`query_negative_results`)

**Issue:** `segment_manifest_id`, `segment_name`, and `config_fingerprint` are spliced directly
into MLflow's `filter_string` DSL with no escaping (`f"tags.segment_name = '{segment_name}'"`).
Today this is not exploitable through any production path: `segment_name` values are constrained
to a fixed, validated vocabulary at manifest-issuance time (`_validate_5seg`/
`_validate_compressed_3seg_shape` require exact name/role tuples; `oof_block_N` names are
generated internally, never caller-supplied), and `segment_manifest_id`/`config_fingerprint` are
sha256 hex digests. But the constraint lives entirely in the *caller's* discipline, not in
`look_count`/`query_negative_results` themselves — a future caller (e.g. a hand-rolled debugging
script, or a later phase that lets a human type a segment name) that passes an unvalidated string
containing a `'` could corrupt the filter (best case: a syntax error MLflow surfaces; worst case,
depending on MLflow's filter parser, a broader-than-intended match that silently reports the
wrong `look_count` — under- or over-counting a budget).

**Fix:** validate/escape the interpolated values before building the filter string (reject any
value containing a single quote, or use MLflow's parameterized search if available), so the
function's own safety doesn't depend entirely on every future caller re-deriving the same
constraint the current, narrow set of callers happens to satisfy.

## Info

### IN-01: `read_segment_manifest` re-verifies content hash but not the body's own `manifest_id` field

**File:** `mvp/harness/segments.py:103-117`

**Issue:** `compute_manifest_id` excludes the `manifest_id` key from what it hashes (by design,
so the id can be embedded in its own body). `read_segment_manifest` recomputes the hash from the
body and compares it to the `manifest_id` **argument** (derived from the filename/caller), but
never cross-checks it against `manifest["manifest_id"]` itself — unlike the sibling function
this phase's own verify-fix added, `harness.errata.read_errata_manifest`
(`mvp/harness/errata.py:358-366`), which explicitly checks both. A hand-edited body whose
`manifest_id` field was changed (leaving the rest of the content, and thus the hash, untouched)
would be accepted silently by `read_segment_manifest`, returning a dict whose own `manifest_id`
field disagrees with the filename/id it was actually read by — that stale field then appears in
error messages (`accessor.py:33`, `:124`) and could confuse an operator debugging a mismatch.
This was already noted as a known, explicitly out-of-scope gap in `05-VERIFICATION-FIX.md`'s
"What was NOT done" section, and is substantially mitigated for anything that reaches git:
`tools/check_manifest_id_integrity.py:87-91` DOES check the field against the recomputed hash,
and runs in both pre-commit and CI — so a tampered field cannot land in the committed registry
through the normal workflow. The runtime gap remains for any manifest reaching disk outside that
workflow (e.g. a future programmatic writer that skips git entirely). Recorded here only because
review scope explicitly asked about this exact class of gap; no action requested beyond what
05-VERIFICATION-FIX.md already flagged.

### IN-02: purge-zone boundary is more conservative than D-05-04's literal open interval (safe direction, undocumented discrepancy)

**File:** `mvp/harness/purge_embargo.py:96-122` (`effective_train_intervals`)

**Issue:** D-05-04's decision text describes the purge zone as the *open* interval
`(val_start - h_max, val_end + h_max)` — a train row with `etime` exactly equal to
`val_start - h_max` is, per that text, NOT purged. The implementation's exclusion band is
half-open and left-closed (`[val_start - h_max, ...)`, via the `excl_start <= cursor` /
`clipped_start` survivor-splitting logic), so a row at exactly that boundary value IS excluded.
This is the safe direction (over-purging by at most one boundary instant, never under-purging),
so it is not a leakage risk, but it is an undocumented deviation from the decision's literal
wording and untested at the exact boundary (`test_filter_train_rows_excludes_the_right_rows`
tests `val_start - purge_ns - 1` and `val_start - purge_ns + 1`, never the exact value). Worth a
one-line docstring note or a boundary test so a future reader doesn't "fix" the implementation to
match the open-interval text and introduce genuine under-purging.

---

_Reviewed: 2026-09-22_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
