---
phase: 06-event-driven-simulator
plan: 07
subsystem: sim
tags: [numba, quantisation-bug-fix, spec-doc, ci-verification, real-data]

# Dependency graph
requires:
  - phase: 06-event-driven-simulator
    provides: "Plan 06-06's own 'half-tick tie-break asymmetry' finding (deferred as Rule-4 architectural, flagged for this plan) and its real-day perfect-foresight ceiling evidence file, both re-measured and corrected here; Plans 06-01..06-05's sim/ package, spec.md's 'Decision rule (Stage 2)' section, and 06-04's schema-v2 rebuild, all cited in the new spec.md 'Simulator' section"
provides:
  - "sim/kernel.py + sim/reference.py: the asymmetric round-half-up prediction quantisation replaced with a symmetric floor(long)/ceil(short) rule -- a prediction exactly on a half-tick boundary (99.96% of real perfect-foresight predictions) now triggers neither direction, closing the bias Plan 06-06 found but deferred"
  - "tests/sim/test_kernel.py: test_symmetric_quantisation_at_exact_half_tick (the regression test) and its anti-vacuity companion, plus a half-tick-generating hypothesis strategy for the kernel/twin equivalence sweep (previously only exact-tick predictions were ever generated)"
  - "The corrected real-day perfect-foresight ceiling: trades=2192 flips=2191 closed_pnl_ticks=294554 on the v2 2026-09-13 partition -- an EXACT match to 06-RESEARCH.md's own prototype baseline, resolving (not merely mooting) Plan 06-06's own 'unexplained divergence' finding"
  - "mvp/spec.md's 'Simulator' section (SIM-01/02/03), six subsections per 06-RESEARCH.md Q12's recommended shape, citing this phase's own real measured numbers"
  - "SIM-01/SIM-02/SIM-03 marked complete in .planning/REQUIREMENTS.md, each with a named proof test"
affects: [07-stage-1-regression-vertical-slice]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "A quantisation rule gating two opposite-direction comparisons must round EACH comparison against its own trade (floor for the trigger it could wrongly relax, ceil for the other) rather than sharing one rounded value across both -- a shared round-half-up value is asymmetric whenever the underlying real-world value sits on ties 99%+ of the time, which is exactly the perfect-foresight case here (mid always sits on a half-tick when the spread is 1 tick, 97.463% of real rows)"
    - "The vectorised trigger-row count over the FULL row set (not the flip-only trade log's fill count) is the only metric that can show a quantisation-rule bias in a flip-only simulator -- the trade log's own long/short fill counts always alternate 1:1 by construction and prove nothing about the underlying rule's symmetry, regardless of which rule is running"
    - "A hypothesis strategy built from `(bid+ask)//2 + integer_offset` never generates a half-tick prediction -- integer floor division collapses the mid to an exact tick before the offset is even added. Any strategy meant to exercise a tie-boundary bug must construct the offset in real (float) tick units, not add an integer offset to an already-floored integer mid."

key-files:
  created:
    - .planning/phases/06-event-driven-simulator/06-07-SUMMARY.md
  modified:
    - mvp/sim/kernel.py
    - mvp/sim/reference.py
    - mvp/tests/sim/test_kernel.py
    - mvp/tests/sim/test_oracles.py
    - mvp/spec.md
    - .planning/phases/06-event-driven-simulator/06-06-SUMMARY.md
    - .planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json
    - .planning/REQUIREMENTS.md

key-decisions:
  - "The fix rounds AGAINST the trade per direction (floor for long, ceil for short) rather than toward zero, toward the mid, or via any other single shared rule -- this is the literal reading of D-06-07's own stated cost ('crosses by at least one tick beyond the threshold'), and it is what makes an exact half-tick prediction trigger NEITHER direction (the conservative, symmetric case) rather than favoring one side by construction."
  - "The vectorised trigger-row-count evidence (before: long=1,924,947/short=1,897,533; after: long=1,868,904/short=1,897,533, short UNCHANGED) is reported as the actual bias evidence, in place of the trade-log side-count split the added task literally asked for -- that split (1096 long/1096 short both before and after) is mathematically guaranteed to be balanced in a flip-only kernel regardless of which quantisation rule runs, and reporting it alone would have been misleading rather than merely uninformative. Both numbers are recorded in the evidence file; only the vectorised one is load-bearing."
  - "06-RESEARCH.md's 2,192/294,554 prototype baseline is NOT treated as 'superseded and moot' (the added task's own hedge, anticipating the numbers might not reconcile) -- the post-fix measurement matches it EXACTLY, so Plan 06-06's own 'unexplained divergence' is reported as RESOLVED, with the specific reason 06-06's four tested candidate tie-break rules missed it (none of them split the rounding direction by long/short side)."
  - "Pre-fix numbers in evidence/06-06-real-day-oracle.json and 06-06-SUMMARY.md were kept verbatim (not deleted or renamed) with a new SUPERSEDED_BY block / notice added -- per the added task's own explicit instruction and this project's mutation-check-style discipline of preserving an audit trail rather than overwriting history."

patterns-established:
  - "Symmetric-by-direction quantisation (round each side of a two-directional comparison against its own trigger, never share one rounded value) as the reusable fix pattern for any future two-sided threshold check in this codebase"

requirements-completed: [SIM-01, SIM-02, SIM-03]

# Metrics
duration: ~26 min from Task 0's fix commit to Task 1's spec.md commit (01:10:54 to 01:16:09 PDT); required-reading, the advisor consultation, the before/after real-day re-measurement (two full 6,864,853-row sequential scans plus a v1 reconciliation run), and Task 2's verification pass are not separately timestamped, matching prior 06-* SUMMARY convention
completed: 2026-09-24
---

# Phase 6 Plan 7: Symmetric quantisation fix, the corrected real-day ceiling, and the closing spec.md Simulator section Summary

**Fixed the asymmetric round-half-up prediction quantisation Plan 06-06 found but deferred (`sim/kernel.py` + `sim/reference.py`, symmetric floor-for-long/ceil-for-short), re-measured the real 2026-09-13 perfect-foresight ceiling at `trades=2192 flips=2191 closed_pnl_ticks=294554` ($29.4554 actual USD) -- an EXACT match to `06-RESEARCH.md`'s own baseline that resolves, not merely moots, Plan 06-06's "unexplained divergence" -- then closed the phase with `spec.md`'s "Simulator" section and a full green verification pass (19/19 hooks, 1,126 tests, CI run `35974993394` green).**

## Performance

- **Duration:** ~26 min from Task 0's fix commit (`545a408`, 01:10:54 PDT) to Task 1's spec.md commit (`6d84924`, 01:16:09 PDT); required-reading, the advisor consultation before committing to an approach, the real-day before/after re-measurement, and Task 2's phase-wide verification are not separately timestamped
- **Tasks:** 3 (added Task 0: quantisation fix + re-measurement; Task 1: spec.md; Task 2: phase-wide verification), all executed, all committed individually (Task 0 and Task 1; Task 2 produces no code commit of its own, only this SUMMARY + state updates), all hooks green
- **Files modified:** 8 (2 production modules, 2 test files, 1 spec doc, 1 requirements doc, 2 evidence/summary docs) + this SUMMARY

## Accomplishments

- **The asymmetric tie-break bug is fixed, not merely documented.** `sim/kernel.py`'s `@njit` kernel and `sim/reference.py`'s pure-Python twin both replaced a single shared round-half-up `pred_ticks` (compared against both triggers) with two direction-specific formulas: `pred_ticks_floor = s // TICK_SIZE_SCALED` gates the long trigger, `pred_ticks_ceil = -((-s) // TICK_SIZE_SCALED)` gates the short trigger. Both are exact integer floor division (never a float round), so D-06-06's integer-only accounting discipline is preserved throughout.
- **The regression test that would have caught this from the start.** `test_symmetric_quantisation_at_exact_half_tick` (`tests/sim/test_kernel.py`) asserts a prediction exactly half a tick above the ask and one exactly half a tick below the bid both produce zero trades -- checked against BOTH the kernel and the pure-Python twin. Its anti-vacuity companion, `test_symmetric_quantisation_anti_vacuity_full_tick_beyond_triggers_both_sides`, proves a full tick beyond either quote DOES trigger, in both directions, so the first test isn't passing because this fixture never trades at all.
- **The kernel/twin equivalence sweep now actually exercises the tie case.** Every prior version of `test_kernel.py`'s hypothesis strategy generated `pred` via `(bid+ask)//2 + integer_offset` -- integer floor division collapses the mid to an exact tick BEFORE the offset is added, so a half-tick prediction was never once generated across any of this phase's hypothesis sweeps. The strategy now draws a coin-flip half-tick addend, so kernel/twin agreement at the tie boundary (99.96% of real perfect-foresight predictions) is proven, not merely assumed by extension from the exact-tick case.
- **No existing fixture number moved -- verified by hand, not assumed.** Every pre-existing fixture (both Q7 copies, the 0.4-tick symmetry test, the odd-spread zero-prediction case, `test_determinism.py`'s fixed 500-row sequence, every pre-existing hypothesis strategy) places `pred` on either an exact tick (floor==ceil==old round-half-up trivially) or, for the zero-prediction oracle, a value structurally bounded away from both quotes regardless of rounding direction. The full `mvp/tests` suite went from 1,124 (06-06) to 1,126 passed (+2, the new regression test and its anti-vacuity companion) -- zero fixture regressions.
- **Mutation check, observed biting and restored.** Reverted the short-side trigger to `pred_ticks_floor` (replaying the pre-fix bug for the short side only) -- file hash changed (`87e9e9bf...b4a`->`21166 82d...39e`), `test_symmetric_quantisation_at_exact_half_tick` failed exactly as predicted (`bid-0.5-tick` spuriously produced 1 fill instead of 0), restored to the original hash exactly, full `tests/sim` suite (36 tests) re-confirmed green.
- **The real-day ceiling re-measured on the same v2 2026-09-13 partition Plan 06-06 used (6,864,853 decision rows, 0 dropped for nullity), before and after the fix, from the same script run twice (once before editing the kernel, once after):**

  | | trades | flips | closed_pnl_ticks | actual USD (0.001-BTC lot) |
  |---|---|---|---|---|
  | Before (pre-fix, matches 06-06's committed evidence exactly) | 2,212 | 2,211 | 293,844 | $29.38 |
  | After (post-fix) | **2,192** | **2,191** | **294,554** | **$29.4554** |
  | 06-RESEARCH.md's own prototype baseline | 2,192 | -- | 294,554 | -- |

  The post-fix figures are an **exact match** to research's own baseline. `v2_perfect_foresight == v1_on_surviving_rows_perfect_foresight` still holds bit-for-bit post-fix (2192/2191 both, `pred_v1` still bitwise-identical to `pred_v2`), matching 06-06's own proof methodology.
- **The bias evidence that actually discriminates the fix (not the trade-log side-count split the added task literally asked for, which is mathematically vacuous in a flip-only kernel -- see "Deviations" below).** Vectorised trigger-row counts over the full 6,864,853-row set: `short_trigger_rows` is IDENTICAL before and after (1,897,533 both -- expected, since `ceil(pred)` equals the old shared round-half-up value on an exact tie); `long_trigger_rows` drops from 1,924,947 to 1,868,904 (-2.91%), which is the mechanism-driven long-favoring bias being removed.
- **06-06's "unexplained divergence" is RESOLVED, not merely mooted.** 06-06 tested four alternative tie-break candidates (`06-RESEARCH.md` Q2's own list: `np.round(price/0.1)`, `np.round(price*10)`, floor-no-rounding, Python `round()` ties-to-even) and ruled all four out because none reproduced research's baseline -- because every one of them applies ONE rounding rule uniformly to both comparisons, and the actual fix applies a DIFFERENT rule per direction, which none of the four candidates tested.
- **USD convention corrected wherever cited:** `294,554` ticks is `$29.4554` actually realized at the traded 0.001-BTC lot -- not `$29,455.40`, which is research's own per-1-BTC convention (its own text labels this "per one-lot", which is a mislabeling this SUMMARY and `spec.md` both call out explicitly).
- **`mvp/spec.md`'s "Simulator" section**, positioned after "Decision rule (Stage 2)" and before "DOs" (body + Contents TOC), six subsections (tick constant/rounding, quantised comparison incl. the fix, position sizing/dead zone, the four oracles, outputs/memory cost, determinism), citing this phase's own real measured numbers throughout, not research's uncorrected projections. `check_spec_diff` confirms zero catalogue drift (bid_price/ask_price stay bookkeeping-only, D-06-17). Mutation check (heading-line deletion) observed the anchored `^## Simulator` grep correctly fail while a bare-word grep still (wrongly) passed, confirming the acceptance criterion's own anchoring is load-bearing.
- **Phase-wide verification pass, all green:** 15 guardrail scripts run individually (ruff check/format, uv lock --check, check_pin_versions, check_ms_to_ns_site, check_catalogue_completeness, check_latest_ban, check_lockbox_containment, check_single_feature_path, check_numba_globals, check_spec_diff, check_no_manifest_rewrite plain + `--full` + `--full` fixture-lake, check_manifest_id_integrity, check_manifest_append_only, check_harness_accessor_only), full `tests` suite (1,126 passed), `tests/leakage` suite separately (23 passed), zero stray `*.nbi`/`*.nbc` files under `mvp/features`/`mvp/sim`, CI run `35974993394` on the pushed branch green (21/21 steps).
- **All 20 of `06-CONTEXT.md`'s D-06-01..20 decisions traced to a committed artifact** -- see the closing table below.

## Task Commits

1. **Task 0 (added): symmetric floor/ceil prediction quantisation, closing the tie-break bug** - `545a408` (fix)
2. **Task 1: `spec.md` Simulator section** - `6d84924` (docs)
3. **Task 2: phase-wide verification pass** - no separate commit (verification-only, per this plan's own action text); results transcribed above and in the closing table below

**Plan metadata:** pending (this commit, together with STATE.md/ROADMAP.md/REQUIREMENTS.md)

## Files Created/Modified

- `mvp/sim/kernel.py` - the symmetric floor(long)/ceil(short) quantisation fix, module docstring rewritten to derive it
- `mvp/sim/reference.py` - the identical fix in the pure-Python twin
- `mvp/tests/sim/test_kernel.py` - the regression test + anti-vacuity companion, the half-tick-generating hypothesis strategy, re-derived docstrings for the 0.4-tick test
- `mvp/tests/sim/test_oracles.py` - the zero-prediction oracle's structural argument re-derived as a single unconditional floor/ceil inequality
- `mvp/spec.md` - the new "Simulator" section, Contents TOC entry, Change log entry
- `.planning/phases/06-event-driven-simulator/06-06-SUMMARY.md` - a prominent `SUPERSEDED` notice added at the top (historical text kept verbatim below it)
- `.planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json` - a `SUPERSEDED_BY` block added with the corrected numbers, the resolved-mystery note, and the vectorised trigger-row-count bias evidence (pre-fix numbers kept, not deleted)
- `.planning/REQUIREMENTS.md` - SIM-01/02/03 checked complete, traceability table updated, each with a named proof test

## Decisions Made

See `key-decisions` in the frontmatter for the four substantive ones: rounding against the trade per direction (not toward zero/mid), reporting the vectorised trigger-row count as the real bias evidence instead of the (mathematically vacuous) trade-log side-count split, treating the research-baseline match as a resolved mystery rather than a moot coincidence, and preserving pre-fix numbers verbatim with a superseding notice rather than deleting them.

## Deviations from Plan

### Auto-fixed Issues

None requiring the standard Rule 1-3 auto-fix protocol -- Task 0 itself IS a planned bug fix (the added task's own explicit instruction), not a deviation discovered mid-task.

### Investigated and Reported (not a deviation from scope, but worth flagging explicitly)

**1. [Added task's own instruction was imprecise] The trade-log long-vs-short fill-count split cannot show the bias, and reporting it alone (as the added task literally requested) would have been misleading**
- **Found during:** Task 0, before writing the re-measurement script (surfaced during the advisor consultation, before any code was written)
- **Issue:** This kernel is flip-only, so consecutive trade-log fills strictly alternate sign by construction -- the long/short fill-count split is ALWAYS balanced (1,106/1,106 before the fix at 2,212 total trades; 1,096/1,096 after the fix at 2,192 total trades), regardless of which quantisation rule is running. Reporting only this split (as the added task's literal wording asked for -- "the long-vs-short trade split BEFORE and AFTER the fix") would have appeared to show "no change in balance" and obscured the actual, real bias.
- **Fix:** Added a second, discriminating metric -- the vectorised long/short TRIGGER-ROW count over the full 6,864,853-row surviving set (not just fill rows), which is state-free and therefore actually sensitive to the quantisation rule's asymmetry. This is the number reported as evidence in this SUMMARY, `spec.md`, and the evidence file; the (non-discriminating) fill-count split is still recorded in the evidence file for completeness, with an explicit note on why it proves nothing.
- **Files modified:** `.planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json` (`SUPERSEDED_BY.trade_log_side_counts_POST_FIX` carries the note)
- **Verification:** Derived by hand before measuring (half-tick tie cases: `ceil(pred)` equals the old shared round-half-up value, `floor(pred)` does not -- so only the long side should move), then confirmed empirically: `short_trigger_rows` identical before/after (1,897,533), `long_trigger_rows` dropped 2.91%.

---

**Total deviations:** 0 auto-fixed. 1 instruction-precision issue identified and resolved by adding a better metric alongside (not instead of) the one literally requested.
**Impact on plan:** None on the added task's actual intent (proving the bias existed and is gone) -- the literal metric requested is still reported, correctly labelled as non-discriminating, and the metric that actually proves the claim is added alongside it.

## Issues Encountered

None. No `SimStatusError`, no `ZeroLotError`, no DQ pause, no holdout refusal at any point across the before/after re-measurement's `load_features` calls (v2, plus the v1 reconciliation check).

## User Setup Required

None - no external service configuration required.

## Known Stubs

None. Every new test assertion runs against the real kernel/twin; the re-measured ceiling is built entirely from real `load_features` output and real `run_sim_checked`/`run_reference_sim` runs -- no placeholder or hardcoded-empty value flows into any assertion, the evidence file, or `spec.md`.

## Threat Flags

None. This plan reads real lake data read-only (via the same 4-gate `load_features` chain prior plans used) and writes only test files, `spec.md`, evidence/summary JSON and markdown, and `.planning/REQUIREMENTS.md` -- no new network endpoint, auth path, file-access pattern, or schema change at a trust boundary. `mvp/data/lake_registry/` and `lake/` were not written (confirmed via `git status --short` after every `load_features` call in this session showing no changes under either path).

## D-06-01..20 Decision Coverage (closing table, T-06-18's own mitigation)

| Decision | Citing artifact |
|---|---|
| D-06-01 (bare numpy arrays, thin wrapper) | `mvp/sim/arrays.py`; `tests/sim/test_kernel.py::test_arrays_refuse_a_nullable_bid_or_ask_column`; `spec.md` Simulator intro |
| D-06-02 (new `sim/` package, no `tests/sim/__init__.py`) | `mvp/sim/__init__.py` (06-02); confirmed via `ls tests/sim/` (no `__init__.py`) this session |
| D-06-03 (accepts harness accessor's frame, never reads the lake itself) | `sim/arrays.py`'s own docstring (numba/polars-free, testable in isolation); real accessor wiring explicitly deferred to Phase 7/9 per D-06-03's own text -- not tested this phase by design |
| D-06-04 (predictions are a parameter) | `sim/kernel.py::run_sim`'s `pred` parameter; the four oracles (D-06-09) supply it |
| D-06-05 (one tick constant, pinned to measurement) | `sim/ticks.py::TICK_SIZE_SCALED`; `tests/sim/test_ticks.py::test_tick_size_matches_the_measured_venue_gcd`; `spec.md` "Tick constant and rounding" |
| D-06-06 (integer accumulation, never float) | `sim/kernel.py`'s int64 accumulators throughout; `spec.md` "Quantised threshold comparison" |
| D-06-07 (quantised threshold comparison, symmetric) | `sim/kernel.py`/`sim/reference.py` (this plan's Task 0 fix); `tests/sim/test_kernel.py::test_symmetric_quantisation_at_exact_half_tick` + `test_quantised_threshold_is_symmetric_at_0_4_ticks`; `spec.md` "Quantised threshold comparison" |
| D-06-08 (position size from $100 cap at entry price, fresh per flip) | `sim/ticks.py::position_size_ticks`; `tests/sim/test_kernel.py::test_flip_long_to_short_closes_and_reopens_same_row`, `test_no_same_direction_or_risk_increasing_order` |
| D-06-09 (four oracles) | `tests/sim/test_oracles.py`, `test_path_dependence.py`, `test_flip_invariant.py`, `test_kernel.py`'s twin-equivalence sweep; `spec.md` "The four oracles" |
| D-06-10 (anti-vacuity: every oracle names a trade count) | `tests/sim/test_oracles.py::test_anti_vacuity_every_oracle_names_a_trade_count`; every oracle's own exact-count assertion |
| D-06-11 (path dependence proven via adjacent swap) | `tests/sim/test_path_dependence.py` (Q7 swap + hypothesis straddle sweep) |
| D-06-12 (flip-only invariant, property test) | `tests/sim/test_flip_invariant.py::test_flip_only_property` |
| D-06-13 (trade log, equity curve, counters) | `sim/outputs.py`; `spec.md` "Outputs" |
| D-06-14 (bit-identical, same-process and cross-process) | `tests/sim/test_determinism.py`; `spec.md` "Determinism guarantee" |
| D-06-15 (refuses nulls, never re-implements admission) | `sim/arrays.py`'s `null_count()==0` assertion; `sim/kernel.py`'s `STATUS_NON_FINITE_PRED`; `tests/sim/test_null_refusal.py` |
| D-06-16 (zero fees/latency as named parameters) | `sim/kernel.py::run_sim_checked`'s `fee_bps`/`latency_ns` params; `tests/sim/test_kernel.py::test_zero_fee_and_zero_latency_are_true_defaults` |
| D-06-17 (bid_price/ask_price as bookkeeping columns) | `features/tier.py::BOOKKEEPING_COLUMNS` (06-01); `spec.md` "Schema v2 migration" |
| D-06-18 (schema v2, additive, never overwrites v1) | `features/tier.py::_part_glob` (06-01); the 7-day additive rebuild (06-04-SUMMARY.md); `spec.md` "Schema v2 migration" |
| D-06-19 (the rebuild is its own regression proof, 249 cells) | `tests/features/test_schema_v2_regression.py`; `evidence/06-04-v1-v2-label-diff.json`; `spec.md` "Schema v2 migration" |
| D-06-20 ($100k dead zone refused loudly) | `sim/ticks.py::ZeroLotError`; `sim/kernel.py::STATUS_ZERO_LOT`; `tests/sim/test_kernel.py::test_zero_lot_dead_zone_raises_a_named_status`; `spec.md` "Position sizing" |

## Next Phase Readiness

- **The canonical real-day perfect-foresight ceiling for Phases 7-9's own model comparisons is now `2,192` trades / `294,554` closed_pnl_ticks on 6,864,853 decision rows (`X_bps=0`, 2026-09-13, schema v2) -- an exact match to `06-RESEARCH.md`'s own baseline, replacing 06-06's pre-fix `2,212`/`293,844`.** USD: `$29.4554` actually realized at the traded 0.001-BTC lot (not `$29,455.40`, the per-1-BTC convention). Phase 9's Core Value check ("Net P&L > 0") should use the real dollar figure.
- **`mvp/spec.md` now carries a durable "Simulator" section** -- any future plan reading spec.md for the decision rule's operationalization (tick constant, quantisation, sizing, oracles, outputs, determinism) has one place to read, not six scattered SUMMARYs.
- **Phase 6 closes with zero open items.** Both of 06-06's own forward-flagged items (the unexplained divergence, the half-tick asymmetry) are resolved by this plan, not carried forward.
- No blockers identified for Phase 7.

---
*Phase: 06-event-driven-simulator*
*Completed: 2026-09-24*

## CI

- **Run:** https://github.com/o2alexanderfedin/aitrade/actions/runs/35974993394
- **Conclusion:** success (21/21 steps green, `guardrails` job, 3m12s) on the pushed commits `545a408` (Task 0 fix) + `6d84924` (Task 1 spec.md)
- **Note:** this run covers every substantive (code + spec) change in this plan. The final metadata commit (this SUMMARY + STATE.md/ROADMAP.md/REQUIREMENTS.md) is docs/JSON-only, pushed and confirmed separately after this SUMMARY was written -- see the executor's closing chat message for that run's URL/conclusion, not re-added here to avoid amending a committed file.

## Self-Check: PASSED

- `mvp/sim/kernel.py` -- FOUND, symmetric quantisation confirmed via `grep -n "pred_ticks_floor\|pred_ticks_ceil" sim/kernel.py`
- `mvp/sim/reference.py` -- FOUND, identical fix confirmed
- `mvp/tests/sim/test_kernel.py::test_symmetric_quantisation_at_exact_half_tick` -- FOUND on disk, passes
- `mvp/tests/sim/test_kernel.py::test_symmetric_quantisation_anti_vacuity_full_tick_beyond_triggers_both_sides` -- FOUND on disk, passes
- `mvp/spec.md` -- `grep -n "^## Simulator" spec.md` finds exactly one match at line 197, after "Decision rule (Stage 2)" (line 174) and before "DOs" (line 366)
- `.planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json` -- FOUND, `SUPERSEDED_BY` block present, valid JSON (`python3 -c "import json; json.load(open(...))"` exits 0)
- `.planning/REQUIREMENTS.md` -- SIM-01/02/03 all `[x]`, traceability table all `Complete`
- Commit `545a408` -- FOUND in `git log --oneline`
- Commit `6d84924` -- FOUND in `git log --oneline`
- `mvp/tests/sim/` has no `__init__.py` -- confirmed via `ls`
- `find mvp/features mvp/sim -name '*.nb[ci]'` (from `mvp/`) -- empty, confirmed before every commit
- Full `mvp/tests` suite -- 1,126 passed (152.58s); `mvp/tests/leakage` -- 23 passed separately
- 15 guardrail scripts run individually -- all exit 0 (transcribed above)
- CI run `35974993394` -- `gh run watch --exit-status` returned success, all 21 steps green
