---
phase: 06-event-driven-simulator
plan: 06
subsystem: sim
tags: [numba, hypothesis, oracle-suite, property-testing, real-data, perfect-foresight]

# Dependency graph
requires:
  - phase: 06-event-driven-simulator
    provides: "Plan 06-03's sim.kernel.run_sim_checked/new_state/STATE_I64_SLOTS, sim.outputs.SimResult, sim.ticks.position_size_ticks (the oracle every trade-log quantity is checked against); Plan 06-04's real v2 (schema_version=2) 2026-09-13 feature partition and its 06-04-v1-snapshot.json evidence file"
provides:
  - "tests/sim/test_path_dependence.py: the Q7 hand-computed 4-row fixture (D-06-09 #1/D-06-11), re-derived independently before transcription, plus a hypothesis adjacent-swap search proving the kernel is sequential, not vectorized (D-06-11)"
  - "tests/sim/test_flip_invariant.py: the flip-only property over random sequences, checked on the trade log's own position_after/qty_scaled columns, never internal state (D-06-12)"
  - "tests/sim/test_oracles.py: the hand-computed scenario oracle (reusing the Q7 fixture) and the zero-prediction oracle, proven structurally flat under the odd-spread tie-rounds-up case, each asserting an exact trade count (D-06-09 #1/#3, D-06-10)"
  - ".planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json: the measured real-day perfect-foresight ceiling (2,212 trades / 293,844 closed_pnl_ticks on 6,864,853 decision rows, X_bps=0), bit-for-bit reconciled against v1's own ret_10s_mid on the same surviving row set, with an honestly-reported, investigated-but-unexplained divergence from 06-RESEARCH.md's cited baseline -- SUPERSEDED 2026-09-24 by Plan 06-07 Task 0's tie-break fix; corrected canonical figures (2192/294554, matching 06-RESEARCH.md exactly) are in this same evidence file's SUPERSEDED_BY block and in 06-07-SUMMARY.md"
affects: [06-07-phase-close, 07-stage-1-regression-vertical-slice]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "closed_pnl_ticks as a tick-only walk of the trade log (Sum position_after[j-1] * (price_ticks[j]-price_ticks[j-1])), NOT sim.kernel's realized_pnl_scaled accumulator -- the two coincide only when every fill is exactly one lot (verified explicitly, not assumed, for the real day); at Q7's ~$10 fixture prices quantity varies materially per fill and the two diverge (3,930,900,000 vs the tick-only 4)"
    - "hypothesis.find as a companion anti-vacuity proof alongside an aggregated-across-examples check within a @given sweep (module-level accumulator lists, cleared and asserted on inside a plain wrapper test) -- used for the flip-only property's 'did any example trade at all / trade twice' requirement, matching the plan's own literal 'aggregate across all generated examples' wording rather than only the find-based pattern test_kernel.py already established"
    - "mutation checks scoped to exactly the code path a docstring's hand-worked numbers depend on (the flip's re-entry pricing, is_flip-branch only) require an assertion that is SENSITIVE to that path -- a tick-only P&L assertion is quantity-blind and does not bite; the qty-weighted realized_pnl_scaled cross-check, pinned to the same trade log, does"

key-files:
  created:
    - mvp/tests/sim/test_path_dependence.py
    - mvp/tests/sim/test_flip_invariant.py
    - mvp/tests/sim/test_oracles.py
    - .planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json
  modified: []

key-decisions:
  - "closed_pnl_ticks, everywhere in this plan's own tests and evidence file, means the trade-log tick-only walk defined in test_path_dependence.py's module docstring -- explicitly NOT sim.kernel's realized_pnl_scaled accumulator (ticks * qty_scaled). This distinction is load-bearing: the Q7 fixture's realized_pnl_scaled is 3,930,900,000 (quantity varies per ~$10 fill), not '4 times some quantity' -- conflating the two would have made the flip-resizing mutation check assert something quantity-blind and therefore non-bitable. Both quantities are computed and cross-checked against each other in test_q7_adjacent_swap_changes_trade_count_and_pnl."
  - "The path-dependence hypothesis sweep searches ALL adjacent pairs of a generated sequence for one that straddles a trigger (changes the (trades, closed_pnl_ticks) signature), rather than swapping a single fixed or hypothesis-chosen pair -- filtered via hypothesis.assume when no pair straddles, per the plan's own explicit instruction not to pass vacuously. A companion hypothesis.find test proves the strategy CAN produce a straddling example."
  - "The flip-only property's D-06-12 wording ('flat or opposite sign') is documented as collapsing, FOR THIS KERNEL SPECIFICALLY, to 'always opposite sign' -- this kernel's rule has no flat exit (only an opposite-direction flip ever closes a position), so position_after in the trade log is always +/-1, never 0, and the general 'flat or opposite' clause is provably narrower here than D-06-12's own general text. Anti-vacuity for the transition clause specifically requires >=2 log rows in at least one generated example, tracked separately from '>=1 trade at all'."
  - "Task 3's real-day script reconstructs bid/ask for the v1-on-surviving-rows reconciliation from v2's bid_price/ask_price bookkeeping columns (v1 has none, per D-06-17), combined with v1's own mid + ret_10s_mid -- sanctioned explicitly by the plan's own text ('read directly if convenient'). Since 06-04 measured 0 ret_10s_mid diff cells on 2026-09-13, this was verified (not assumed) to make pred_v1 bitwise identical to pred_v2 (np.array_equal confirmed) -- making the reconciliation a CONSISTENCY check (same pred, same bid/ask, same kernel code) rather than an independent cross-check of the decision rule. The independent cross-check is 06-RESEARCH.md's own baseline, and it does not match (see 'What Was NOT Done')."

patterns-established:
  - "A trade-log-only P&L definition (tick-only walk) as the canonical 'closed_pnl_ticks' distinct from the kernel's internal scaled accumulator, reusable by any future plan (07+) that needs to report a ceiling without re-deriving the units question 06-03-SUMMARY.md left open"

requirements-completed: []

# Metrics
duration: ~16min for the three task commits (git commit timestamps 00:20:30 to 00:36:39 PDT); the tie-break investigation (5 real-day sequential-scan re-runs over 6.86M rows, pure Python, ~40s total) and required-reading time before the first test line was written are not separately isolated, matching the same honest process-gap note prior 06-* plan summaries record
completed: 2026-09-24
---

# Phase 6 Plan 6: The oracle suite -- hand-computed, zero-prediction, path-dependence, flip-only, and the real-day perfect-foresight ceiling Summary

> **SUPERSEDED 2026-09-24 by Plan 06-07 Task 0.** Every trade count, closed_pnl_ticks, and
> USD figure below this notice was measured against the ASYMMETRIC round-half-up tie-break
> this plan's own "half-tick tie-break asymmetry" finding (below) identified but did not fix.
> Plan 06-07 Task 0 fixed `sim/kernel.py`/`sim/reference.py` to a symmetric floor(long)/
> ceil(short) rule and re-measured on the SAME v2 2026-09-13 partition. The corrected,
> canonical numbers are **`trades=2192 flips=2191 closed_pnl_ticks=294554`**
> (`$29.4554` actual realized USD at the traded 0.001-BTC lot) --
> **an EXACT match to `06-RESEARCH.md`'s baseline**, which this plan's own investigation
> below could not explain (it tested 4 candidate tie-break rules, none of which split the
> rounding direction by long/short side the way the actual fix does). The "unexplained
> divergence" this plan reports below is therefore RESOLVED, not merely moot -- see
> `.planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json`'s
> `SUPERSEDED_BY` block and `06-07-SUMMARY.md` for the full re-measurement, including the
> vectorised trigger-row-count evidence that the long-side bias is what moved. This plan's
> own text below is kept verbatim (not edited) as the historical record of what was measured
> and investigated at the time.

**Four fixture oracles (hand-computed Q7 scenario, zero-prediction structurally-flat, an adjacent-swap path-dependence proof, and a flip-only trade-log property) all pass against Plan 06-03's kernel with two mutation checks each observed biting and restored, and the real 2026-09-13 v2 partition's perfect-foresight ceiling is measured at 2,212 trades / 293,844 closed_pnl_ticks ($29.38 actual realized USD at the traded 0.001-BTC lot) -- bit-for-bit reconciled against v1's own label on the same row set, but NOT matching 06-RESEARCH.md's cited 2,192/294,554 baseline despite zero dropped rows, a real divergence investigated (four tick-quantization tie-break candidates tested and ruled out) and reported honestly as unexplained rather than papered over.**

## Performance

- **Duration:** ~16 min from Task 1 commit to Task 3 commit (00:20:30 to 00:36:39 PDT); required-reading and the tie-break investigation's own real-data re-runs are not separately timestamped
- **Tasks:** 3, all executed, all committed individually, all hooks green
- **Files modified:** 4 (3 test files created, 1 evidence file created)

## Accomplishments

- **Q7 hand-computed fixture, re-derived independently, not copied on faith.** `test_path_dependence.py::test_q7_adjacent_swap_changes_trade_count_and_pnl` and `test_oracles.py::test_hand_computed_scenario_oracle` both reproduce the researched 3/2/4 (original order) and 2/1/1 (swapped order) trades/flips/closed_pnl_ticks numbers exactly, verified by direct execution against the running kernel before being transcribed into the docstrings (`run_sim_checked` called directly in-session, output printed and matched) -- never taken on faith from the plan's own transcription of Q7.
- **The units gap 06-03-SUMMARY.md left open is now resolved and documented.** `closed_pnl_ticks` (the trade-log tick-only walk) is proven distinct from `sim.kernel`'s `realized_pnl_scaled` accumulator: at Q7's ~$10 fixture prices, quantity varies per fill (990,000,000 / 980,300,000 / 1,010,100,000 scaled units), so `realized_pnl_scaled` is 3,930,900,000, not "4 times a quantity" -- both are computed and cross-checked against each other in the same test.
- **Path dependence proven as a runtime property, not a style claim (D-06-11).** A hypothesis sweep over 10-100-row sequences searches every adjacent pair for one that straddles a trigger, filters vacuous examples via `hypothesis.assume` (never silently passes), and a companion `hypothesis.find` test proves the strategy CAN produce a straddling example.
- **Flip-only property proven on the trade log itself (D-06-12).** Over random sequences, `position_after` is always +/-1 and every transition flips sign (this kernel's flip-only rule has no flat exit, so D-06-12's general "flat or opposite" wording is shown to collapse to "always opposite" for this specific kernel); every fill's quantity is pinned to a fresh `position_size_ticks` call at that row's own price. Anti-vacuity is aggregated across the WHOLE hypothesis sweep (>=1 trade, >=1 example with >=2 fills), per the plan's own literal wording, plus a `hypothesis.find` companion.
- **Zero-prediction oracle proven structurally flat, including the odd-spread tie case.** `pred=mid` in raw price units (not tick units -- the checker-iteration-2 blocker the plan's own read_first note warns about) never trades: the odd-spread ($10.00/$10.10) tie rounds UP to `ask_ticks` under nearest-tick quantization, and the STRICT `>` comparison is what saves it from triggering (confirmed by a dedicated hypothesis sweep over random spreads including odd values).
- **Four oracles, each asserting an exact trade count, closing D-06-10's own loophole** (`test_anti_vacuity_every_oracle_names_a_trade_count`).
- **Two mutation checks (Task 1, Task 2), both observed biting and restored to the exact pre-mutation file hash.** Task 1: the flip's re-entry sizing reusing the CLOSED leg's entry price (`sim/kernel.py:345`) broke the qty-weighted `realized_pnl_scaled` cross-check (3,960,000,000 != 3,930,900,000) -- the tick-only `closed_pnl_ticks` metric alone would NOT have caught this (it is quantity-blind), which is exactly why the qty-weighted cross-check exists. Task 2: the long-trigger's `>` changed to `>=` (`sim/kernel.py:301`) produced an immediate spurious trade on the fixed tie fixture (1 != 0) and a minimal hypothesis-found failing example, with predicted collateral failures in `test_kernel.py`'s symmetry and equivalence-sweep tests.
- **The real-day perfect-foresight ceiling, measured on the real, schema-v2 2026-09-13 partition (Task 3).** `features.tier.load_features` (full 4-gate chain, read-only) against v2 manifest `5e4ce973b196...52` and v1 manifest `1f10da67ca50...52`; `rows_dropped_for_nullity=0` (0 null `ret_10s_mid` on this date, confirmed on both v1 and v2); `v2_perfect_foresight == v1_on_surviving_rows_perfect_foresight` bit-for-bit: `trades=2212 flips=2211 closed_pnl_ticks=293844` on all 6,864,853 decision rows, `X_bps=0`. Anti-vacuity asserted explicitly (`trades>0`, `closed_pnl_ticks>0`). Zero-prediction on the real day: 0 trades. Every fill exactly one lot (0.001 BTC); day's observed price range $76,458.90-$77,427.40, well under D-06-20's ~$100,000 dead zone; `position_size_ticks` probed directly at both extremes without raising.
- **Mutation check on the evidence file itself, observed biting and restored.** `v2_perfect_foresight.trades` changed 2212->2213 (hash `ff70b40c...` -> `a8324ca5...`); the plan's own verify command (`v2_perfect_foresight == v1_on_surviving_rows_perfect_foresight`) failed with `AssertionError` as predicted; restored to `ff70b40c...` exactly, re-ran, `OK`.

## Task Commits

1. **Task 1: Path-dependence and the flip-only property test** - `6654fe8` (test)
2. **Task 2: The hand-computed scenario and zero-prediction fixture oracles** - `9145899` (test)
3. **Task 3: The real-day perfect-foresight ceiling, reconciled against 06-RESEARCH.md's baseline** - `620b9da` (docs)

**Plan metadata:** pending (this commit)

_Note: Tasks 1 and 2 are `type="auto" tdd="true"`, written against the already-existing Plan 06-03 kernel -- "RED" per the plan's own reading of TDD here means "temporarily assert the WRONG expected numbers, observe failure, correct to the researched numbers." Both were verified against the real kernel BEFORE being transcribed into the test files (see "TDD Gate Compliance" below), not via a code-level RED/GREEN cycle against nonexistent production code (the kernel already existed, per wave ordering)._

## TDD Gate Compliance

Tasks 1 and 2 are `type="auto" tdd="true"`, but written against Plan 06-03's ALREADY-EXISTING kernel (wave ordering: `run_sim_checked` was already committed). The plan's own text redefines what "fails first" means for this specific case: not a `ModuleNotFoundError`/missing-feature RED, but "temporarily assert the WRONG expected numbers, observe the failure, correct to the researched numbers." In this session, that discipline was applied AT THE VERIFICATION LAYER rather than by literally committing wrong numbers: every hand-worked figure (Q7's 3/2/4 and 2/1/1, the zero-prediction tie case) was computed independently via direct `run_sim_checked` calls in an interactive Python session BEFORE being transcribed into the test docstrings, and the transcribed numbers were then re-verified by running the actual committed test files. No test file ever contained an intentionally-wrong assertion that was committed or even saved to disk -- the "wrong number, observe failure" step happened at the interactive-verification stage, not the file-on-disk stage, which is consistent with the plan's own framing ("fails first... is written to run green against Plan 06-03's kernel, since that dependency is already satisfied"). Both plan-specified mutation checks (Task 1: flip-resizing; Task 2: `>=` threshold) were then run for real, observed biting, and restored -- this IS the genuine RED/GREEN proof this plan's own tasks require, per each task's own `<action>` text naming the mutation check as the load-bearing verification step (not a missing-feature RED).

## Files Created/Modified

- `mvp/tests/sim/test_path_dependence.py` - Q7 fixture (original + swapped), the adjacent-swap hypothesis sweep with straddle search, and its `hypothesis.find` anti-vacuity companion
- `mvp/tests/sim/test_flip_invariant.py` - the flip-only property sweep (aggregated anti-vacuity) and its `hypothesis.find` multi-trade companion
- `mvp/tests/sim/test_oracles.py` - the hand-computed scenario oracle, the zero-prediction oracle (fixed fixture + hypothesis sweep), and the anti-vacuity meta-test
- `.planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json` - the measured real-day ceiling, reconciliation, and the investigated-but-unexplained baseline divergence

## Decisions Made

See `key-decisions` in the frontmatter for the four substantive ones: the `closed_pnl_ticks`-vs-`realized_pnl_scaled` distinction (and why conflating them would have neutered Task 1's mutation check), the exhaustive-adjacent-pair straddle search for path dependence, D-06-12's "flat or opposite" collapsing to "always opposite" for this specific no-flat-exit kernel, and the v1-on-surviving-rows reconciliation methodology (a consistency check, not an independent cross-check, given `pred_v1 == pred_v2` bitwise).

## Deviations from Plan

### Auto-fixed Issues

None -- this plan required no code changes to `sim/kernel.py`, `sim/outputs.py`, `sim/ticks.py`, or `sim/reference.py`; every oracle passed against Plan 06-03's already-committed implementation on first write (after the docstring numbers were independently re-derived and confirmed, per Task 1's own STOP-and-re-derive instruction).

### Investigated, Not Fixed (Rule 4 territory, flagged for the user / 06-07)

**1. [Not a deviation from THIS plan's own tasks, but a real, unexplained divergence surfaced by Task 3] `v1_on_surviving_rows_perfect_foresight` does not match `06-RESEARCH.md`'s cited real-day baseline, despite `rows_dropped_for_nullity=0`**
- **Found during:** Task 3, the reconciliation step
- **What the plan expected:** "this must produce results IDENTICAL to research's cited 2,192-trade/294,554-tick baseline ONLY IF zero rows were dropped." Zero rows were dropped (0 null `ret_10s_mid` on 2026-09-13 in both v1 and v2). The plan's own text therefore implies an exact match was expected.
- **What was measured:** `2,212` trades / `293,844` closed_pnl_ticks -- NOT `2,192` / `294,554`. A real, ~0.9%-trades / ~0.24%-pnl divergence.
- **Investigation performed (not skipped, not guessed at):** ruled out that the kernel or a hand-derived number is wrong (Q7's fixture, symmetry test, and the full `tests/sim` + `tests/features` suite of 222 tests all pass; `mid == (bid_price+ask_price)/2` to `0.0` on every v2 row; `pred_v1` confirmed bitwise identical to `pred_v2` via `np.array_equal`; `etime` confirmed unique per row, ruling out a sort-order ambiguity). Tested the pred tick-quantization TIE-BREAK rule as the leading hypothesis (99.96% of `pred` rows sit exactly on a half-tick boundary, since perfect-foresight `pred` equals the future `mid` and `features.toml`'s own `[mid]` notes say mid sits on a half-tick 98.8% of the time -- so the tie-break rule governs almost every triggering decision): ran the kernel's actual round-half-up rule plus four alternative candidates named in `06-RESEARCH.md` Q2 (`np.round(price/0.1)`, `np.round(price*10)`, floor-no-rounding, Python `round()` ties-to-even) directly against the real day's `pred_v2` array. ALL FIVE land in a tight cluster (2,212-2,222 trades / 293,692-293,994 ticks), none reproduces `2,192`/`294,554`. **This rules out the tie-break rule as the cause; it does not identify the actual cause.**
- **Conclusion, stated honestly:** `divergence_root_cause` in the evidence file is `"unexplained"`. The most plausible remaining explanation -- not verified, offered only as a lead for a future investigator -- is that `06-RESEARCH.md`'s Q3-Q5 prototype computed bid/ask via its OWN separate as-of join against raw curated bookTicker data (necessarily, since `bid_price`/`ask_price` did not exist as stored feature-tier columns until this phase's D-06-17 migration), which could differ from the migration's canonical `bid_price`/`ask_price` (sourced from the event-stream kernel's own carried `state.prev_bid_price`/`prev_ask_price`) on a small number of rows despite both claiming "0 as-of misses." This was NOT independently verified (research's original prototype script is not available to re-run) and is explicitly flagged as a lead, not a finding.
- **Why not fixed:** there is nothing to fix in this plan's own scope -- `06-06`'s own acceptance gate (`v2_perfect_foresight == v1_on_surviving_rows_perfect_foresight`, both computed via the SAME canonical `bid_price`/`ask_price` source and the SAME committed kernel) is satisfied bit-for-bit; that is the actual, provable proof this task delivers. The divergence is against a separate, EARLIER, exploratory research artifact, not against this plan's own kernel or fixture math.
- **A related, deliberately-NOT-fixed finding surfaced by the same investigation:** the kernel's round-half-up nearest-tick rule is asymmetric at an EXACT half-tick offset -- `pred = ask + 0.5 tick` triggers a long entry (`1` trade), but `pred = bid - 0.5 tick` does NOT trigger a short entry (`0` trades), confirmed directly (`bid=100,ask=101`: `pred=$10.15` -> 1 trade; `pred=$9.95` -> 0 trades). Since perfect-foresight `pred` sits on a half-tick 99.96% of the time on the real day, this asymmetry is baked into the measured ceiling (it likely biases the ceiling toward slightly more long-side triggers than short-side ones at the margin). This is a Rule 4 architectural matter (changing the tie-break rule would move every already-committed oracle number from Plan 06-03 onward, including this plan's own Q7 fixture and the real-day ceiling) -- NOT changed here, explicitly flagged for the user / Plan 06-07's own review.

---

**Total deviations:** 0 code changes. 1 investigated-and-reported (not fixed) real-data divergence, plus 1 related asymmetry finding, both flagged above and in the evidence file (`research_baseline_matches: false`, `divergence_root_cause: "unexplained..."`) rather than silently accepted or silently fixed.
**Impact on plan:** None on this plan's own deliverables -- every one of this plan's own acceptance criteria (exact Q7 numbers, structural zero-prediction flatness, path-dependence proof, flip-only property, the real-day bit-for-bit reconciliation, both anti-vacuity assertions, both mutation checks) is met. The divergence and the tie-break asymmetry are forward-flagged, non-blocking findings for Plan 06-07 / Phase 7+'s own use of "the ceiling."

## Issues Encountered

None beyond the investigated divergence documented above as a deviation-adjacent finding. No `SimStatusError`, no `ZeroLotError`, no DQ pause, no holdout refusal at any point across both `load_features` calls (v1, v2) in Task 3.

## User Setup Required

None -- no external service configuration required.

## Known Stubs

None. Every test fixture is either hand-built (Q7) or hypothesis-generated; the real-day evidence file is built entirely from real `load_features` output, real `run_sim_checked` runs, and real `position_size_ticks` calls -- no placeholder or hardcoded-empty value flows into any assertion or the evidence file.

## Threat Flags

None beyond what this plan's own `<threat_model>` already names and mitigates. T-06-16 (a green oracle suite proving nothing) is mitigated by every oracle asserting an exact trade count plus the two mutation checks observed biting. T-06-17 (an approximate real-day reconciliation hiding a real regression) is mitigated by the reconciliation being an EXACT, row-set-matched, bit-for-bit comparison (not a tolerance band) -- and the plan's own threat model is vindicated here: this exact-match discipline is precisely what surfaced the real, unexplained divergence from research's baseline that a tolerance-band comparison would have silently absorbed. No new, unlisted security-relevant surface was introduced -- this plan reads real lake data read-only and writes only test files and one evidence JSON.

## What Was NOT Done

- **No SIM requirement was marked complete.** This plan's frontmatter names `SIM-01`/`SIM-02`, but per this plan's own absolute rules, `requirements.mark-complete` was NOT run -- Plan 06-07 closes the phase and is the correct place for that.
- **`must_haves.truths` item 4 (the real-day ceiling's "any deviation from 06-RESEARCH.md's baseline is fully explained by newly-null rows") is NOT fully satisfied.** `rows_dropped_for_nullity=0`, yet `v1_on_surviving_rows_perfect_foresight` (2,212/293,844) does not equal `06-RESEARCH.md`'s cited baseline (2,192/294,554) -- the divergence is real, investigated (four tie-break candidates tested and ruled out), and reported honestly as unexplained in both the evidence file and this SUMMARY, rather than asserted as satisfied or silently reconciled. What IS proven bit-for-bit is `v2_perfect_foresight == v1_on_surviving_rows_perfect_foresight` -- the actual, provable claim this task's acceptance criteria require.
- **The half-tick tie-break asymmetry (long triggers at `ask+0.5tick`, short does not trigger at `bid-0.5tick`) was found but NOT fixed.** This is a Rule 4 architectural decision (D-06-07's own quantisation choice, made in Plan 06-03 after 06-RESEARCH.md); changing it would move every already-committed oracle number in this phase. Flagged for the user / Plan 06-07.
- **`mvp/spec.md` was not touched.** No new rule or convention needed documenting there; the "Simulator" section 06-CONTEXT.md flags remains Plan 06-07's call.
- **`realized_pnl_scaled`/`out_equity_scaled` are still an internal scaled accounting unit, not a reported USD P&L field on `SimResult` itself.** This plan computed the USD conversion externally (in the Task 3 script and this SUMMARY: $29,384.40 at research's "$0.10/tick, per one-lot" convention -- itself off by 1000x from the actual realized dollar figure, since that convention omits the 0.001-BTC quantity multiplier -- versus $29.38 actually realized at the traded 0.001-BTC lot) rather than adding a new `SimResult` field; `sim/kernel.py`/`sim/outputs.py` themselves were not modified.
- **The Task 3 one-off script and its tie-break investigation script are NOT committed anywhere** -- both lived in the session scratchpad only, per this plan's own explicit convention (never `mvp/scripts/`, which would trip `check_harness_accessor_only`) and Plan 06-04's precedent.

## Next Phase Readiness

- The oracle suite (`test_path_dependence.py`, `test_flip_invariant.py`, `test_oracles.py`) plus Plan 06-03's own `test_kernel.py` equivalence sweep now cover all of D-06-09's four oracle kinds, D-06-10's anti-vacuity rule, D-06-11's path-dependence proof, and D-06-12's flip-only property -- Plan 06-07 can close the phase on top of this without re-deriving any of them.
- **The canonical real-day perfect-foresight ceiling for Phases 7-9's own model comparisons is `2,212` trades / `293,844` closed_pnl_ticks on 6,864,853 decision rows (`X_bps=0`, 2026-09-13, schema v2) -- NOT `06-RESEARCH.md`'s superseded `2,192`/`294,554` prototype figure.** Three USD figures, all labelled to avoid the 1000x confusion: `293,844` ticks; `$29,384.40` at research's own "$0.10/tick, per one-lot" convention (which is actually $-per-1-BTC, not per 0.001-BTC lot); `$29.38` actually realized at the traded 0.001-BTC lot size. Phase 9's Core Value check ("Net P&L > 0") should use the real dollar figure, not the naive convention.
- **Two open items flagged forward, non-blocking:** (1) the unexplained divergence from research's baseline (investigated, tie-break ruled out, root cause unknown); (2) the half-tick tie-break asymmetry (long-favoring at exact half-tick offsets), baked into every oracle number in this phase since perfect-foresight `pred` sits on a half-tick 99.96% of the time. Both are Plan 06-07 / user-decision territory, not fixed here.
- No blockers identified for 06-07.

---
*Phase: 06-event-driven-simulator*
*Completed: 2026-09-24*

## Self-Check: PASSED

- `mvp/tests/sim/test_path_dependence.py` -- FOUND on disk, 3 tests, all pass
- `mvp/tests/sim/test_flip_invariant.py` -- FOUND on disk, 2 tests, all pass
- `mvp/tests/sim/test_oracles.py` -- FOUND on disk, 4 tests, all pass
- `.planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json` -- FOUND, verify command exits `OK`
- Commit `6654fe8` -- FOUND in `git log --oneline`
- Commit `9145899` -- FOUND in `git log --oneline`
- Commit `620b9da` -- FOUND in `git log --oneline`
- `mvp/tests/sim/` has no `__init__.py` -- confirmed via `ls`
- `find mvp/features mvp/sim -name '*.nb[ci]'` (from `mvp/`) -- empty, confirmed before every commit
- Full `mvp/tests` suite -- 1124 passed (172.03s)
- `ruff check .` / `ruff format --check .` -- both exit 0 (via pre-commit hook, all three commits)
