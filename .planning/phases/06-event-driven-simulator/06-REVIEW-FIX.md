---
phase: 06-event-driven-simulator
fixed_at: 2026-09-24T18:16:00Z
review_path: .planning/phases/06-event-driven-simulator/06-REVIEW.md
iteration: 1
findings_in_scope: 2
fixed: 2
skipped: 0
status: all_fixed
---

# Phase 6: Code Review Fix Report

**Fixed at:** 2026-09-24T18:16:00Z
**Source review:** `.planning/phases/06-event-driven-simulator/06-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 2 (WR-01, IN-01)
- Fixed: 2
- Skipped: 0

## Fixed Issues

### WR-01: Position-sizing overflow bound flagged in `sim/ticks.py`'s own comment was never re-derived or guarded for the `@njit` kernel

**Files modified:** `mvp/sim/ticks.py`, `mvp/sim/kernel.py`, `mvp/tests/sim/test_ticks.py`, `mvp/tests/sim/test_kernel.py`
**Commit:** `1834374`
**Applied fix:** Derived the exact int64 overflow bound and added a named guard on both sides of the kernel/twin split:

- `sim/ticks.py` gains `MAX_NOTIONAL_SCALED_INT64_BOUND = (2**63 - 1) // QTY_SCALE` (`92,233,720,368` scaled units) with the derivation and a `NotionalOverflowError(ValueError)`, checked at the top of `position_size_ticks` before the overflow-risking product is computed. `sim/reference.py` (the pure-Python twin) calls `position_size_ticks` directly, so it inherits the same refusal without a separate change.
- `sim/kernel.py` gains `STATUS_NOTIONAL_OVERFLOW = -6`, checked once before the row scan (loop-invariant — `max_notional_scaled` never changes mid-run), converted to `SimStatusError` by `run_sim_checked` exactly like every other status. No `raise` or `assert` inside the `@njit` body.
- The now-satisfied TODO in `ticks.py` ("Plan 06-03's `@njit` mirror... must re-derive this bound") was replaced with a cross-reference to this closed finding.
- Boundary tests pin both sides at `MAX_NOTIONAL_SCALED_INT64_BOUND` (succeeds, real non-zero-lot fill) and `BOUND + 1` (raises) for: `position_size_ticks` directly, the kernel with no trigger in the row (proves the check is unconditional, not sizing-time-only), the kernel with an actual triggering fill, and `run_reference_sim` with an actual triggering fill.
- **Mutation check:** removed the new kernel guard, `sim/kernel.py`'s sha256 changed (`3580a210...` → `02bca22f...`), both boundary tests failed with `DID NOT RAISE SimStatusError` exactly as predicted, restored the guard, sha256 matched the pre-mutation value exactly (`3580a210...`), full `tests/sim` suite (39/39) green again.

**Correction to the review's own arithmetic (documented, not silently fixed):** 06-REVIEW.md's WR-01 text computes the bound as `92,233,720,368` scaled units and calls this "**$922 billion**... roughly 9.2 billion times today's $100 MVP default," concluding the bound is "not reachable" absent an "orders of magnitude" change. That arithmetic stops one division short: `92,233,720,368` is still in `PRICE_SCALE`-scaled units (`1e8`), not dollars. Dividing again by `PRICE_SCALE` gives the actual bound: **`$922.34`**, only **~9.22x** today's $100 default — a single caller-chosen `max_notional_scaled` well within realistic Phase-9-or-later parameter sweeps, not an astronomical one. This correction is recorded in `sim/ticks.py`'s own `MAX_NOTIONAL_SCALED_INT64_BOUND` docstring so a future reader sees the right number next to the wrong one's citation. `06-REVIEW.md` itself was committed verbatim (not edited) per this task's own instruction — the correction lives in the fix, not a rewrite of the review.

### IN-01: The DQ report's new one-date-many-manifests row model is a new invariant only one confirmed consumer respects

**Files modified:** `mvp/data/dq/report.py`, `mvp/tests/store/test_loader.py`
**Commit:** `6f3a6b3`
**Applied fix:** No source-behavior change was needed (the review itself found no defect) — closed as documentation + a regression test, per the review's own "Fix" guidance:

- Added a "CARDINALITY (IN-01, 06-REVIEW.md)" paragraph to `data/dq/report.py`'s module docstring, stating the per-`manifest_id` row scoping explicitly, naming `data.store._dq_verdict_for_date` as the confirmed-correct consumer, and stating that `write_report` regenerates `report.parquet` wholesale every run (so the writer is never itself a stale-shape reader).
- **Grep evidence gathered fresh for this fix**, repo-wide (`.` from the repo root, not just `mvp/`): the only production code path that reads `report.parquet` via `pl.read_parquet` is `data/store.py::_dq_verdict_for_date` (already filters on `manifest_id`, confirmed by the review). `features/build.py`'s `read_parquet` call is a different file (`resync_windows.parquet`) and unrelated. All other repo-wide matches are prose in `.planning/*.md` docs, not code.
- Added `test_load_curated_scores_two_manifests_sharing_one_date_independently` (`tests/store/test_loader.py`): two manifests cover the same date with different partition content (distinct `manifest_id`s) and one `report.parquet` carries one "ok" row and one "failed" row, one per manifest. Verified through the real `load_curated` path, in both directions: the ok manifest loads its own rows (not poisoned by the other's failed row) and the failed manifest raises `DQPauseError` (not masked by the other's ok row).
- **Mutation check:** commented out `rows = own` in `data/store.py::_dq_verdict_for_date` (the line that scopes rows to the requested manifest), sha256 changed (`c321e9d5...` → `122082e2...`), the new test failed exactly as predicted (the ok manifest wrongly raised `DQPauseError`, poisoned by the other manifest's failed row), restored, sha256 matched the pre-mutation value exactly (`c321e9d5...`), `tests/store/test_loader.py` + `tests/dq/test_report.py` (32/32) green again.

## Skipped Issues

None — both in-scope findings (WR-01, WR-tier equivalent; IN-01) were fixed.

## What Was NOT Done

- **`fill_price_ticks * TICK_SIZE_SCALED` (the OTHER intermediate product in `position_size_ticks`/the kernel's inline mirror) is still unbounded.** `price_ticks` is caller/market-data-controlled, not a named parameter like `max_notional_scaled` — WR-01 only asked for the notional-cap bound, and this review didn't flag the price-ticks side. At real BTC prices (`~$100,000` → `~1,000,000` ticks) this is nowhere near int64's ceiling, but it is not asserted the way the notional bound now is. Left out of scope; worth a follow-up finding if a future review wants it.
- **Negative `max_notional_scaled` is still unchecked.** A caller passing a negative value would not overflow (the product would be negative, not wrap), but it would silently size a nonsensical negative-notional position rather than refusing. Not part of WR-01's stated finding; not fixed here.
- **`spec.md`'s "Position sizing" subsection was not updated** with the new overflow-bound guard or the corrected `$922.34` arithmetic. The advisor's guidance flagged this as "only if cheap" — given the fix report is the durable record of the derivation and the correction, and `spec.md` changes carry their own `check_spec_diff` scrutiny, this was left for a future plan rather than added speculatively here.
- **The kernel's guard and the twin's guard are not byte-for-byte equivalent in one edge case**, disclosed rather than silently accepted: the kernel checks `max_notional_scaled` once, unconditionally, before row 0 (so a run that never trades still refuses an out-of-bound cap); the twin (via `position_size_ticks`) only checks when a fill is actually attempted (since it's called from inside the trigger branch). A `max_notional_scaled` past the bound with a hypothesis-generated row sequence that never trades would make the kernel raise and the twin return cleanly. This is disclosed here rather than fixed because reconciling it would mean either moving the twin's check to always run up front (changing its "written straight" simplicity, D-06-09 #4) or removing the kernel's stronger unconditional guard — a design trade-off outside this fix's scope. No existing hypothesis equivalence sweep varies `max_notional_scaled` away from the default, so this asymmetry is not currently reachable by any test in the suite.

## CI

- **Run:** https://github.com/o2alexanderfedin/aitrade/actions/runs/36039614306
- **Conclusion:** success (21/21 steps green, `guardrails` job, 2m57s) on the pushed commits `1834374` (WR-01 fix + `06-REVIEW.md`) + `6f3a6b3` (IN-01 fix)

---

_Fixed: 2026-09-24T18:16:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
