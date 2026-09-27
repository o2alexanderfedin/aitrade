---
phase: 05-fold-harness-overfitting-controls
fixed_at: 2026-09-22T14:47:03Z
review_path: .planning/phases/05-fold-harness-overfitting-controls/05-REVIEW.md
iteration: 1
findings_in_scope: 7
fixed: 7
skipped: 0
status: all_fixed
ci_run: https://github.com/o2alexanderfedin/aitrade/actions/runs/35742433621
ci_conclusion: success
---

# Phase 5: Code Review Fix Report

**Fixed at:** 2026-09-22T14:47:03Z
**Source review:** `.planning/phases/05-fold-harness-overfitting-controls/05-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 7 (CR-01, WR-01, WR-02, WR-03, WR-04, IN-01, IN-02 — all findings, per this project's standing rule that every error found gets fixed, including pre-existing ones)
- Fixed: 7
- Skipped: 0

## Fixed Issues

### CR-01: `record_look`'s check-then-act budget race

**Files modified:** `mvp/harness/budget.py`, `mvp/tests/harness/test_budget.py`
**Commits:** `3a026f3` (fix), `9b8c7b6` (hardening pass, see below)
**Applied fix:** `record_look` read `look_count` and only then wrote a new MLflow run, with no lock spanning the two — concurrent callers could all read the same spent count, all pass the allowance check, and all record a look (reproduced empirically: 8 threads racing `budget_allowance=1` all succeeded, final count 8). `record_look` now holds an exclusive `fcntl.flock` keyed on `(segment_manifest_id, segment_name)` across the whole check-then-act window. `flock` was chosen over `os.O_CREAT | os.O_EXCL` deliberately: a lock file left behind by a crashed holder would wedge every future `record_look` for that pair forever with no automatic recovery; `flock` is released by the kernel the instant the holding process's fd closes, on a clean exit, an exception, or a kill alike. Each call does its own `os.open`, so the lock genuinely contends between threads (distinct open-file-descriptions), not only processes.

**Guarantee, stated exactly (in the module docstring):** serialises `record_look` calls sharing one `(segment_manifest_id, segment_name)` pair, across threads and processes, as long as they share one local filesystem under `tracking_root`. **Not covered:** NFS/SMB-mounted tracking roots (flock semantics are unreliable-to-absent across network clients) and Windows (`fcntl` doesn't exist there).

**Second-pass hardening (commit `9b8c7b6`):** a self-review after landing the fix found `_look_lock` created `<tracking_root>/.locks/` (`mkdir(parents=True)`) *before* the lock was acquired — a non-canonical or nonexistent `tracking_root` got a real directory created inside it and only then was refused, breaking `look_count`'s own "refuse before touching anything" posture. `record_look` now runs the canonical-root and store-exists checks first, before `_look_lock` is entered at all.

**Tests:** `test_record_look_is_race_safe_under_real_concurrency` races 8 real threads against `budget_allowance=1`: exactly one succeeds, the other 7 raise `BudgetExhaustedError`, `look_count` is 1 afterward. `test_record_look_refuses_a_non_canonical_root_without_creating_a_lock_dir` proves the hardening.

**Mutation proof:** removing the `flock` call (hash `fffed5be…`→`f938feb8…`, confirmed changed) made the concurrency test fail red (8 successes instead of 1, run 3x for stability); restoring (`fffed5be…`) made `tests/harness/test_budget.py` pass green 3x in a row. The hardening's own mutation (removing the hoisted checks, hash `832949fa…`→`dd728025…`) made the new test fail red (`assert not True` — the `.locks/` dir existed); restored (`832949fa…`), green.

### WR-01: `quarantine_feature_partition` no rollback on `issue_manifest` failure

**Files modified:** `mvp/data/lockbox.py`, `mvp/tests/lockbox/test_quarantine_feature_partition.py`
**Commit:** `97e1e9e`
**Applied fix:** the sequence was resolve-manifest → `os.replace` (irreversible) → `issue_manifest`. If `issue_manifest` raised anything — disk-full, a partition-path collision, any of its several `ValueError`s — the bytes sat at the new lockbox path with no manifest naming them, and the old features-tier manifest was permanently broken. Confirmed `data.store.issue_manifest` never stats/hashes the new file itself (it only writes JSON using the already-verified original entry's fields), so wrapping it in `try/except BaseException` and `os.replace`-ing the bytes back to `old_path` on failure is safe — no reordering of resolve-then-move was needed.

**Tests:** `test_quarantine_feature_partition_rolls_back_on_issue_manifest_failure` monkeypatches `issue_manifest` to raise and asserts the original path has its bytes back, nothing is left under `lockbox/`, the original manifest still resolves byte-identical, and no lockbox-tier manifest was issued.

**Mutation proof:** removing the `try/except` rollback (hash `4fe5fe73…`→`05a68084…`, confirmed changed) made the new test fail red (`assert False == exists()`) with the other 7 tests in the file unaffected; restoring (`4fe5fe73…`) made `tests/lockbox/` pass green (108/108).

**Residual, stated honestly (not solved):** a hard kill (SIGKILL, power loss) between the `os.replace` and the `try` block's own completion is not covered — recoverable only by a human re-running `issue_manifest` by hand against the orphaned file, exactly as the review's finding describes.

### WR-02: `write_holdout_registry`'s write-once check-then-act

**Files modified:** `mvp/data/holdout.py`, `mvp/tests/lockbox/test_quarantine_feature_partition.py`
**Commit:** `931dafa`
**Applied fix:** `path.exists()` was checked, then the atomic tmp-then-rename write happened separately — two concurrent callers could both observe `False`, both pass the refusal, and one write would silently win with no error to either caller. Replaced with `_exclusive_write_json`: write the body to a per-PID-and-thread-unique temp file, then `os.link` it onto the target path — `os.link` is atomic at the filesystem level and raises `FileExistsError` if the target already exists, so "does this exist" and "create it" can never be two separate steps. The losing caller re-reads the file fresh (not a pre-race snapshot) so its error names whichever declaration actually won. The now-unused check-then-act `_atomic_write_json` was removed from `data/holdout.py` (confirmed no other module ever imported it — every other module keeps its own copy per the project's 05-PATTERNS.md convention).

**Tests:** `test_holdout_writer_write_once_guarantee_is_race_safe` races 8 threads against the same registry path: exactly one succeeds, the other 7 raise "already exists", and the file on disk matches the one winner.

**Mutation proof:** reverting to the check-then-act shape (hash `484e5fb2…`→`0360328909…`, confirmed changed) made the new test fail — via a race-induced `FileNotFoundError` on the shared `.tmp` path (an unhandled thread exception, not the "exactly 1 winner" assertion, because the old shape's race window is wide enough to corrupt the write itself before the assertion is even reached) — this is a genuine near-miss worth stating plainly: the mutation is caught, but by a different symptom than the primary assertion. Restoring (`484e5fb2…`) made the file pass green 3x in a row.

### WR-03: a starved individual `oof_block` was accepted silently

**Files modified:** `mvp/harness/segments.py`, `mvp/tests/harness/test_kfold.py`
**Commit:** `09f2535`
**Applied fix:** `_train_effective_intervals` already refused a starved top-level `train` entry; nothing refused the analogous starvation one level down — an individual `oof_block` whose own training-row set is empty after purge+embargo against its sibling block. `_refuse_starved_oof_blocks` now runs right after `oof_training_row_counts` is derived, raising the same class of `ValueError`, naming every starved block and the remedy (smaller `k`, or a wider `train` entry).

**Checked, not assumed (per the report's own instruction):** the real committed manifest's `oof_training_row_counts` — `{oof_block_0: 10,211,768, oof_block_1: 8,548,908, oof_block_2: 9,079,386, oof_block_3: 7,857,730, oof_block_4: 8,158,918}` — all five are non-zero. This fix changes nothing about the manifest already on disk.

**Tests:** `test_refuses_issuance_when_an_oof_block_is_starved` builds a 700s train (k=5, 140s/block, purge=600s, embargo=1s) where block 0's own purge+embargo band covers the whole train range, with two independent anti-vacuity proofs — (a) the *top-level* train is not starved under this fixture (so the refusal is specifically about the internal split, not an unrelated top-level refusal), and (b) block 0's own candidate range really is fully covered, computed directly via `effective_train_intervals`. `test_anti_vacuity_the_wide_kfold_fixture_has_no_starved_oof_block` proves the existing wide fixture is unaffected.

**Mutation proof:** removing the `_refuse_starved_oof_blocks` call (hash `2aedbb3d…`→`ebb17acc…`, confirmed changed) made the starvation test fail red ("DID NOT RAISE") while the anti-vacuity test stayed green; restoring (`2aedbb3d…`) made `tests/harness/` pass green (87/87).

### WR-04: unescaped f-string interpolation into MLflow `filter_string`

**Files modified:** `mvp/harness/budget.py`, `mvp/harness/negative_log.py`, `mvp/data/lockbox.py`, plus their test files
**Commits:** `78eade5` (fix), `9b8c7b6` (hardening — `.match` → `.fullmatch`)
**Applied fix:** `look_count`, `query_negative_results`, and `data.lockbox._mlflow_has_consumed` all spliced caller-supplied values into an MLflow `filter_string` via raw f-string interpolation. Every current caller's values happen to be safe today (sha256 digests or names from a fixed vocabulary), but the constraint lived entirely in the caller's discipline. Each module gained its own `_require_filter_safe` (duplicated per this project's 05-PATTERNS.md convention), rejecting anything outside `[A-Za-z0-9_.-]+` before any MLflow client is constructed.

**Extended beyond the review's named files:** `data.lockbox._mlflow_has_consumed`'s `token_id` was fixed too, though 05-REVIEW.md named only `harness.budget`/`harness.negative_log`. `token_id` is human-chosen at `issue_token` call time (unlike a segment name or a config fingerprint), making it this module's *largest* exposure to the same class of bug — fixed per the project's standing rule that every error found gets fixed, not only the ones named.

**Second-pass hardening:** `_FILTER_SAFE_RE.match()` against a pattern ending in `$` accepted values with a trailing newline (Python's `$` matches before a trailing `\n`) — a caller that forgot to `.strip()` would silently under-count. Changed `.match()` to `.fullmatch()` in all three copies.

**Tests:** each module gets a hostile-value test (`"val' or tags.segment_name != 'x"`-shaped) asserting the module's own typed error fires, plus a trailing-newline test for the hardening.

**Mutation proof (primary fix):** removing the validation call in each of the three modules made its own test fail — but with `mlflow.exceptions.MlflowException` ("Invalid clause(s) in filter string: 'or'") instead of the expected typed error, proving the validation does real, distinct work (failing before the query reaches MLflow's own parser) rather than being redundant with MLflow's own defenses. **Mutation proof (hardening):** reverting `.fullmatch()` to `.match()` in each module made its own trailing-newline test fail red ("DID NOT RAISE"). All three restored, `tests/harness/` + `tests/lockbox/` green (206/206).

### IN-01: `read_segment_manifest` missing the `manifest_id`-field cross-check

**Files modified:** `mvp/harness/segments.py`, `mvp/tests/harness/test_segments.py`
**Commit:** `680db85`
**Applied fix:** `read_segment_manifest` recomputed the hash and compared it to the `manifest_id` *argument*, but never cross-checked `manifest["manifest_id"]` itself — unlike the sibling `harness.errata.read_errata_manifest` (added by 05-VERIFICATION-FIX.md), which explicitly checks both. Since `compute_manifest_id` excludes the `manifest_id` key from what it hashes, a hand-edited body whose `manifest_id` field was changed (leaving everything else untouched) was accepted silently. Now checks `recomputed != manifest_id or body_manifest_id != manifest_id`, mirroring `read_errata_manifest`'s shape exactly.

**Tests:** `test_read_segment_manifest_refuses_a_body_whose_manifest_id_field_disagrees` isolates the second half of the check specifically — rewrites only the body's `manifest_id` field, so `recomputed` alone would still match.

**Mutation proof:** removing that half of the check (hash `04e626ea…`→`72ff7254…`, confirmed changed) made the new test fail red ("DID NOT RAISE"); restoring (`04e626ea…`) made `tests/harness/` pass green (91/91).

### IN-02: purge boundary docstring vs. D-05-04's literal open-interval text

**Files modified:** `mvp/harness/purge_embargo.py`, `mvp/tests/harness/test_purge_embargo.py`
**Commit:** `63a20e7`
**Applied fix:** the code was already correct (the safe, over-purging direction); only the documentation was missing. `effective_train_intervals`'s docstring now states the exact convention precisely — the combined excluded band is `[start_ns - purge_ns, end_ns + purge_ns + embargo_ns)`, half-open, left-closed — and explicitly calls out the deviation from D-05-04's literal open-interval wording, why it's safe (over-purging only, never under-purging), and that a future reader must not "fix" the implementation to match the open-interval text. **`05-CONTEXT.md`'s own D-05-04 decision text was left as-is** — a historical decision record, not re-issued for a docstring fix; the discrepancy is documented here and in the code docstring instead.

**Tests:** `test_effective_train_intervals_exact_boundary_is_half_open_left_closed` asserts a row at exactly `val_start - purge_ns` is excluded, one ns earlier survives.

**Mutation proof (verifying the new test is behavioral, not vacuous, since this was primarily a docstring change):** shifting the excluded band's left edge by 1 ns in the implementation (hash `9b896fe5…`→`76c16fdf…`, confirmed changed) made the new test fail red (`3400000000001 != 3400000000000`); restoring (`9b896fe5…`) made `tests/harness/` pass green (92/92).

## Skipped Issues

None — all 7 findings were fixed.

## What was NOT done

- **WR-01's residual SIGKILL window**: a hard kill between `os.replace` and the `try` block's own completion is not covered by the rollback — recoverable only by a human, as the original finding describes.
- **CR-01's NFS/SMB/Windows exclusion**: `fcntl.flock` semantics are unreliable-to-absent on network-mounted tracking roots, and `fcntl` doesn't exist on Windows. Stated in the module docstring, not solved.
- **IN-02's `05-CONTEXT.md` D-05-04 text**: left unedited (a historical decision record); the discrepancy is documented in the code docstring and this report instead of re-issuing the decision.
- **WR-04 extended beyond the review's literal scope**: `data/lockbox.py::_mlflow_has_consumed`'s `token_id` was fixed too, per the project's "every error found gets fixed" rule — noted here since it was not named by 05-REVIEW.md.
- **`data/holdout.py::_atomic_write_json` was removed**, not merely superseded — confirmed via grep that no module outside `data/holdout.py` ever imported it before deleting it.
- **The real committed manifest (`97964cb2…`) was not touched.** WR-03 would have accepted it as-is: checked (not assumed) that all five real `oof_training_row_counts` are non-zero (10,211,768 / 8,548,908 / 9,079,386 / 7,857,730 / 8,158,918).
- **WR-02 depends on `os.link` being available and atomic** on the target filesystem (true for local POSIX filesystems, the project's actual deployment target) — not verified against exotic network mounts.
- **Mutation near-miss, stated plainly (WR-02):** the mutation-check for WR-02 went red via an unhandled-thread `FileNotFoundError` on the shared `.tmp` path, not via the "exactly 1 winner" assertion the test primarily checks — the old check-then-act race window is wide enough that the shared temp file gets corrupted before the assertion is even reached under 8-way contention. Still proves the mutation is detected; the failure mode is just cruder than the test's own primary claim.

## CI

Pushed after every commit; final code commit `9b8c7b6` (the last `fix(05):` commit — the hardening pass) has a green CI run: `https://github.com/o2alexanderfedin/aitrade/actions/runs/35742433621`, conclusion `success`, all 19 guardrail steps passed (ruff check, ruff format --check, uv lock --check, 10 `check_*` tools, `check_no_manifest_rewrite --full` ×2, `check_manifest_id_integrity`, `check_manifest_append_only`, `check_harness_accessor_only`, both pytest steps), 2m13s. Full local suite: `tests -x -q` → 1080 passed (1065 pre-existing at start of this fix pass + 15 new across all 7 findings' regression tests plus the second-pass hardening tests).

---

_Fixed: 2026-09-22T14:47:03Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
