---
phase: 06-event-driven-simulator
reviewed: 2026-09-24T17:53:03Z
depth: standard
files_reviewed: 33
files_reviewed_list:
  - mvp/data/dq/feature_checks.py
  - mvp/data/dq/report.py
  - mvp/data/store.py
  - mvp/features/api.py
  - mvp/features/build.py
  - mvp/features/kernel.py
  - mvp/features/reference.py
  - mvp/features/tier.py
  - mvp/sim/__init__.py
  - mvp/sim/arrays.py
  - mvp/sim/kernel.py
  - mvp/sim/outputs.py
  - mvp/sim/reference.py
  - mvp/sim/ticks.py
  - mvp/spec.md
  - mvp/tests/dq/test_report.py
  - mvp/tests/features/test_kernel.py
  - mvp/tests/features/test_reference.py
  - mvp/tests/features/test_schema_v2_regression.py
  - mvp/tests/features/test_tier.py
  - mvp/tests/fixtures/harness_span.py
  - mvp/tests/harness/test_accessor.py
  - mvp/tests/harness/test_errata.py
  - mvp/tests/leakage/test_catalogue_information_set.py
  - mvp/tests/sim/test_determinism.py
  - mvp/tests/sim/test_flip_invariant.py
  - mvp/tests/sim/test_kernel.py
  - mvp/tests/sim/test_null_refusal.py
  - mvp/tests/sim/test_oracles.py
  - mvp/tests/sim/test_path_dependence.py
  - mvp/tests/sim/test_ticks.py
  - mvp/tests/tools/test_check_harness_accessor_only.py
  - mvp/tools/check_harness_accessor_only.py
findings:
  critical: 0
  warning: 1
  info: 1
  total: 2
status: issues_found
---

# Phase 6: Code Review Report

**Reviewed:** 2026-09-24T17:53:03Z
**Depth:** standard
**Files Reviewed:** 33
**Status:** issues_found

## Summary

The simulator's job is to produce a P&L a human will believe, and on the question that matters
most — does this kernel compute the number it claims to compute — it holds up. I re-derived
`sim/kernel.py`'s trigger logic by hand against `spec.md`'s "Decision rule (Stage 2)" section
(strict inequalities, flip-only branching, `X_price` from integer bid/ask ticks) and found no
divergence. The 2026-09-24 floor(long)/ceil(short) quantisation fix is genuinely symmetric at
the boundary that mattered (verified the exact-half-tick and 0.4-tick-overshoot arithmetic by
hand) and the pure-Python twin (`sim/reference.py`) carries the identical formula.

I did not stop at reading the code. In an isolated `git worktree` clone (never the working
tree), I reproduced two of the SUMMARY-claimed mutation checks myself: (1) reverting the
short-side trigger from `pred_ticks_ceil` back to `pred_ticks_floor` (replaying the pre-fix bug)
made `test_symmetric_quantisation_at_exact_half_tick` fail with exactly the predicted spurious
fill; (2) forcing the flip's re-entry sizing to price off the stale `entry_price_ticks` instead
of the current fill price made the very first entry crash with `ZeroDivisionError` (since
`entry_price_ticks` starts at 0), exactly as `06-03-SUMMARY.md` claims. Both files were restored
to their original sha256 before the worktree was removed; `find mvp/features mvp/sim -name
'*.nb[ci]'` is empty. These are real, biting tests, not decorative ones.

I also independently recomputed the headline real-day ceiling's unit conversion by hand:
`realized_pnl_scaled_v2_POST_FIX` (29,455,400,000) equals `closed_pnl_ticks` (294,554) times
`LOT_STEP_SCALED` (100,000) exactly, and that figure converts to `$29.4554` via
`× TICK_SIZE_SCALED / (PRICE_SCALE × QTY_SCALE)` exactly as `spec.md` states — the internal
accounting is self-consistent, not just asserted. The int64 accumulation has enormous headroom
at real-day scale (≈2.9e10 against a ~9.2e18 ceiling).

Test quality is unusually high: every oracle in `tests/sim/` pairs an exact-count assertion with
an anti-vacuity companion (a `hypothesis.find` proof that the strategy CAN produce a trading/
flipping/straddling sequence, not just a hope that 50 random draws include one), and every
hypothesis strategy I checked generates `pred` in raw price units via a `_price_at_ticks` helper
(never a bare tick-range integer, the specific vacuity trap this project's own docs say it hit
twice before). I found one WARNING (a self-flagged overflow bound in position sizing that was
never re-derived for the `@njit` kernel, though not reachable at current MVP defaults) and one
INFO item (a new one-date-can-have-many-manifest-rows invariant in the DQ report that only one
confirmed consumer currently respects). No BLOCKER findings.

## Warnings

### WR-01: Position-sizing overflow bound flagged in `sim/ticks.py`'s own comment was never re-derived or guarded for the `@njit` kernel

**File:** `mvp/sim/kernel.py:382-384` (mirrors `mvp/sim/ticks.py:163-170`)
**Issue:** `sim/ticks.py::position_size_ticks` computes `max_notional_scaled * QTY_SCALE` as a
plain Python int (no overflow possible) and its own comment says explicitly: *"this module uses
plain Python ints, which do not overflow, but Plan 06-03's `@njit` mirror of this arithmetic will
use int64 and must re-derive this bound"* (`ticks.py:166-169`). `sim/kernel.py`'s inline mirror
(lines 382-384) never does this — there is no assertion, no named status code, and no test
covering a `max_notional_scaled` large enough to overflow. Numba/numpy int64 arithmetic overflows
silently (wraps), which is exactly the failure class this codebase otherwise refuses loudly for
(`STATUS_ZERO_LOT`, `STATUS_NON_FINITE_PRED`, `STATUS_TRADE_LOG_OVERFLOW`, `STATUS_NEGATIVE_PRED`
all exist to prevent a P&L that looks plausible but is wrong).

I computed the actual bound: overflow requires `max_notional_scaled > int64_max // QTY_SCALE`
≈ `9,223,372,036,854,775,807 // 100,000,000` ≈ `92,233,720,368` (scaled units) — about
**$922 billion** in notional cap, roughly 9.2 billion times today's $100 MVP default. At today's
defaults and Phase 9's planned use (sweeping `X_bps`, not `max_notional_scaled`), this is not
reachable. But `max_notional_scaled`/`lot_step_scaled` are named, caller-overridable parameters
(D-06-08's own design), and the code that would silently misbehave if one is ever raised "by
orders of magnitude post-MVP" (the comment's own words) carries no guard and no test proving the
guard's absence is safe at whatever value is eventually chosen.

**Fix:** Either assert the bound at the `run_sim_checked` boundary (Python-side, where raising is
safe) before entering the `@njit` loop —
```python
if max_notional_scaled * QTY_SCALE > np.iinfo(np.int64).max:
    raise ValueError(
        f"run_sim_checked: max_notional_scaled={max_notional_scaled} overflows int64 "
        f"when scaled by QTY_SCALE={QTY_SCALE}"
    )
```
— or add a code comment cross-referencing `ticks.py`'s own note so a future Phase-9-or-later
change to `max_notional_scaled` is forced to look at this bound before raising it. Add a test
that picks a `max_notional_scaled` just above/below the computed threshold and asserts the
guard fires (or, if left unguarded, asserts the silent-wraparound behavior is at least detected
by some other means) — right now no test exercises this parameter at any value other than the
MVP default.

## Info

### IN-01: The DQ report's new one-date-many-manifests row model is a new invariant only one confirmed consumer respects

**File:** `mvp/data/dq/report.py:479-533` (`build_feature_report_rows_for_date` /
`_feature_report_rows_for_manifest`)
**Issue:** Before this phase, `report.parquet` carried at most one features row per
`(date, symbol, stream, check)` — sourced from the by-date pointer's current manifest only. This
phase's rewrite emits one full row set **per manifest that has ever covered that date**
(`manifests_for_dataset` + the `matching` filter on `partitions[].date`), so a date rebuilt at
schema v2 after already being built at v1 (2026-09-12/13, per this phase's own migration) now
produces two full row sets for the same `(date, symbol, stream)`. This is deliberate and tested
(`tests/dq/test_report.py::test_two_feature_manifests_for_one_date_each_keep_their_own_report_row`),
and the one consumer I traced (`data/store.py::_dq_verdict_for_date`) correctly disambiguates by
filtering `rows.filter(pl.col("manifest_id") == manifest_id)` before judging a verdict — so this
is not a defect. It is a new invariant on `report.parquet`'s shape (no longer at-most-one-row-
per-date-per-check) that I could only confirm one consumer respects within this review's scope;
any future reader of `report.parquet` that groups by `(date, symbol, stream)` without also
including `manifest_id` will silently double-count.

**Fix:** No code change required now. Worth a one-line note in `report.py`'s module docstring
(or `spec.md`'s DQ section) stating the new cardinality explicitly, so a future consumer added
outside this phase's own test suite doesn't rediscover the invariant the hard way.

---

## What I verified but did not find a defect in (worth stating explicitly, per this project's own
"say what was NOT done" convention — the absence of a finding here is a checked absence, not an
unchecked one)

- **Decision-rule fidelity to `spec.md`**: strict `>`/`<`, the flip-only branch's omission of the
  same-direction test, `X_price` derived from integer `bid_ticks`/`ask_ticks` only — hand-traced
  against `sim/kernel.py:338-376` and `sim/reference.py:93-127`, both match.
- **Quantisation symmetry post-545a408**: re-derived the exact-half-tick and 0.4-tick-overshoot
  arithmetic by hand for both `sim/kernel.py` and `sim/reference.py`; confirmed identical via a
  from-scratch mutation reproduction (see Summary).
- **Integer-only accounting**: no float reaches `position`/`qty_scaled`/`realized_pnl_scaled`/
  `out_equity_scaled`; equity marks at `bid_ticks`/`ask_ticks`, never a rounded mid
  (`sim/kernel.py:418-424`).
- **Int64 overflow at real-day scale**: `realized_pnl_scaled` for the real 2026-09-13 ceiling is
  29,455,400,000 — about 3.2e-9 of int64's ~9.22e18 ceiling. No overflow risk at today's scale
  (see WR-01 for the one caller-overridable parameter that isn't bounded).
- **Fail-open paths**: `STATUS_ZERO_LOT`/`STATUS_TRADE_LOG_OVERFLOW`/`STATUS_NON_FINITE_PRED` are
  all checked before any trade-log write; `run_sim_checked` is the single conversion point from
  status code to exception; `sim/arrays.py` asserts `null_count()==0` before every `.to_numpy()`
  call it makes; `position_size_ticks` never returns `0`, only raises.
- **Schema-v2 migration**: `_part_glob`'s asymmetric-but-safe glob reasoning is correct as
  documented (v1's bare glob is never called again post-bump); `manifests_for_dataset` is
  non-recursive so it can't pick up the by-date pointer directory; `feature_build_stats_path`'s
  `schema_version=` parameter correctly reads a historical manifest's own stats file
  (`data/dq/report.py`'s exact bug this task fixed). Dedicated regression tests exist for the
  part-file/build-stats version-scoping and the superseded-manifest-still-resolves case.
- **Headline number**: the $29.4554 figure, the 2,192/2,191/294,554 counters, and the
  1,868,904/1,897,533 post-fix trigger-row split are all internally consistent under independent
  recomputation (see Summary); I did not re-run the full 6.8M-row simulation against the live lake
  myself (out of scope for `standard` depth given the evidence file's self-consistency), so this
  is a consistency check, not a from-scratch reproduction of the real-lake run.
- **Test vacuity**: every oracle/property/hypothesis test I read pairs its assertion with an
  anti-vacuity companion; no `pred` fixture I found is expressed in tick units where raw price
  units were required.

---

*Reviewed: 2026-09-24T17:53:03Z*
*Reviewer: Claude (gsd-code-reviewer)*
*Depth: standard*
