---
phase: 06-event-driven-simulator
plan: 02
subsystem: sim
tags: [numpy, fixed-point, ticks, position-sizing, hypothesis]

# Dependency graph
requires:
  - phase: 06-event-driven-simulator
    provides: "Plan 06-01's feature-tier schema v2 (bid_price/ask_price bookkeeping columns) -- not consumed by this plan's own code, but the same real 2026-09-13 curated bookTicker partition this plan's tests read is the one Plan 06-01 built its DQ-report generalisation against"
provides:
  - "sim/ticks.py: PRICE_SCALE (= data.time_ns.QTY_SCALE), TICK_SIZE_SCALED = 10_000_000 (0.1 USDT), LOT_STEP_SCALED = 100_000 (0.001 BTC), MAX_NOTIONAL_SCALED (= $100), price_to_ticks(), position_size_ticks(), ZeroLotError -- a hermetic, numba-free, numpy-only module with no polars dependency"
  - "A gcd-against-real-data test pinning TICK_SIZE_SCALED and LOT_STEP_SCALED, so a venue tick/lot-step change breaks CI instead of silently rescaling P&L"
  - "A named round-trip proof on every price_to_ticks conversion, and a named ZeroLotError refusal (never a silent 0) above the measured ~$100,000 dead zone"
affects: [06-03-sim-kernel, 06-04-real-rebuild, 06-07-phase-close]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "A reference/kernel-shared constants module with zero numba/polars dependency, so a later @njit kernel and its polars-boundary wrapper both import ONE already-tested source instead of each defining its own copy (mirrors features/kernel.py's own separation of pure arithmetic from the polars boundary)"
    - "gcd-against-a-bounded-real-data-head as the re-derivation mechanism for a pinned venue constant, skipping loudly (pytest.skip, not silent pass) when the physical lake is unmounted -- reuses tools/check_no_manifest_rewrite.py's bare Path(DEFAULT_LAKE_ROOT).exists() idiom rather than data.lake_paths.lake_root() (which mkdirs and write-validates, wrong for a read-only existence probe)"

key-files:
  created:
    - mvp/sim/__init__.py
    - mvp/sim/ticks.py
    - mvp/tests/sim/test_ticks.py
  modified: []

key-decisions:
  - "TDD recovery: the implementation was drafted before the tests (an ordering slip), caught by advisor review before any commit. Recovered by moving the draft aside, writing Task 1's three tests against nothing, confirming ModuleNotFoundError (RED), then restoring only the Task-1-scoped subset of the implementation to reach GREEN -- Task 2's constants/functions were added the same way in their own commit. No RED/GREEN evidence was fabricated after the fact."
  - "The plan's own worked example for the median-price test (\"price_ticks=<76,805 in ticks, i.e. ~$77,061.35>\") does not arise from the declared tick arithmetic (77,061.35 / 0.1 = 770,613.5 ticks, not 76,805) and was not used verbatim. The median MID price is also a genuine half-tick value (average of two ticks; 06-RESEARCH.md Q6 measured 97.463% of real quotes at exactly a 1-tick spread), so round-tripping it through price_to_ticks correctly RAISES per this same plan's own D-06-05 round-trip proof. The test instead hardcodes price_ticks=770_613 (floor($77,061.35/$0.1), matching price_to_ticks's own floor-division convention) with a docstring explaining why it is not routed through the conversion function."
  - "The hypothesis strategy for price_to_ticks's round-trip proof generates prices ON the tick grid (multiples of $0.1, matching real bid_price/ask_price values) rather than 'rounded to the nearest cent' as an early draft attempted -- a cent-granularity price (e.g. $1234.56) is up to 0.4 ticks off-grid and legitimately fails the half-tick round-trip proof by construction, which is the proof working correctly, not a bug to paper over. This is flagged below for Plan 06-03's attention, since D-06-07 converts pred_mid/X_price to ticks and a mid is frequently a half-tick value."
  - "The 'not representable at PRICE_SCALE' test could not be triggered by a decimal-precision price (measured directly: rounding to the nearest 1e-8 grid point is bounded by 5e-9 for any float64 value in the real BTC price range -- 06-RESEARCH.md Q2's own 1.455e-11 measured maximum confirms this). The test instead uses a deliberately out-of-range magnitude (~$50 billion) where float64 itself has already lost more than the 1e-6 epsilon of precision, and documents this reasoning in the test docstring rather than silently retuning the epsilon to force a false failure on a realistic price."
  - "ZeroLotError's message states the $100,000 boundary applies 'at the MVP defaults' rather than unconditionally, since max_notional_scaled/lot_step_scaled are caller-overridable parameters and an unconditional claim would be misleading under an override."

patterns-established:
  - "A pinned venue constant (tick size, lot step) is re-derived from a bounded real-data head via gcd in its own dedicated test, with an anti-vacuity size/non-zero assertion on the diff/value set before computing the gcd, and the real-data test skips loudly (not silently) when the physical lake is unmounted"

requirements-completed: []

# Metrics
duration: 25min
completed: 2026-09-23
---

# Phase 6 Plan 2: Tick, lot-step, and $100-cap dead-zone arithmetic Summary

**`sim/ticks.py` converts a USDT price to an integer tick count with a proven half-tick round-trip, sizes a position from the $100 notional cap at the venue's measured 0.001 BTC lot step, and raises `ZeroLotError` by name rather than silently returning zero once BTC crosses roughly $100,000 -- with the tick size and lot step themselves re-derived by gcd from real 2026-09-13 exchange data rather than copied as literals.**

## Performance

- **Duration:** ~25 min (git commit timestamps: 22:16:26 to 22:24:06 for the two task commits, plus prior file-reading/derivation and this SUMMARY's tail)
- **Started:** 2026-09-23T22:00:00-07:00 (approx, first file read)
- **Completed:** 2026-09-23T22:24:06-07:00 (Task 2 commit)
- **Tasks:** 2
- **Files modified:** 3 (2 created new: `sim/__init__.py`, `sim/ticks.py`; 1 created new: `tests/sim/test_ticks.py`)

## Accomplishments

- `sim/ticks.py` exists as a hermetic, numpy-only module (no polars, no numba) exporting `PRICE_SCALE`, `TICK_SIZE_SCALED`, `LOT_STEP_SCALED`, `MAX_NOTIONAL_SCALED`, `price_to_ticks()`, `position_size_ticks()`, `ZeroLotError` -- every scale constant an UPPER_CASE `int`, importable by both Plan 06-03's `@njit` kernel and its polars-boundary wrapper without either defining its own copy.
- `TICK_SIZE_SCALED = 10_000_000` (0.1 USDT) is pinned by a test that recomputes `gcd` of every positive adjacent `bid_price`/`ask_price` difference over a bounded 2,000,000-row head of the real, manifest-resolved 2026-09-13 curated bookTicker partition (row set: `curated/symbol=BTCUSDT/stream=bookTicker/date=2026-09-13`, resolved via `by_date_index_path` + `resolve_manifest`, 17,167,290 rows total in the partition, 2,000,000 read). Measured directly this session: `bid_price` gcd = 10,000,000 over 2,055 positive diffs; `ask_price` gcd = 10,000,000 over 1,529 positive diffs.
- `LOT_STEP_SCALED = 100_000` (0.001 BTC) is pinned the same way against `bid_qty`/`ask_qty` (same bookTicker head) and `trade.qty` (bounded 2,000,000-row head of `curated/symbol=BTCUSDT/stream=trade/date=2026-09-13`, 1,409,705 rows total in the partition -- the bound exceeds the file, so the full trade day was read). All three gcd to exactly 100,000, agreeing.
- `price_to_ticks()` converts an array of prices to int64 ticks via `round(price * PRICE_SCALE) // TICK_SIZE_SCALED`, asserting BOTH a representability check and a half-tick round-trip proof on the WHOLE array (via `np.all`/boolean indexing, never a per-element Python loop), raising `ValueError` naming the first offending index and value rather than truncating silently -- proven by a hypothesis sweep of 200 examples of tick-grid-aligned prices ($1,000-$200,000) and a deliberately out-of-range magnitude (~$50 billion) that trips the representability guard.
- `position_size_ticks()` computes quantity fresh from the given price on every call (never from a position's original entry price, per D-06-08/Q13), returns exactly one lot (100,000 scaled units) at the measured 2026-09-13 median mid price and at exactly $100,000, and raises `ZeroLotError` -- naming the price and the cap -- at $100,000.10 and above; a 300-example hypothesis sweep from $1 to $500,000 confirmed no price ever returns a silent `0`.
- Two mutation checks (one per task), both print-and-hash-confirmed: `TICK_SIZE_SCALED` off-by-one broke the gcd test naming `10000000 != 10000001`; disabling the zero-lot branch (`if False`) broke both the $100k-boundary test (`DID NOT RAISE ZeroLotError`) and the anti-vacuity sweep (`qty=0` at `price_ticks=1000001`, i.e. $100,000.10). Both restored to the pre-mutation hash and re-run green.

## Task Commits

1. **Task 1: Tick constant, conversion rule, and its round-trip proof** - `ffab475` (feat)
2. **Task 2: Lot-step measurement and the $100-cap zero-lot dead-zone refusal** - `4148878` (feat)

**Plan metadata:** pending (this commit)

## Files Created/Modified

- `mvp/sim/__init__.py` - empty package marker (new `mvp/sim/` package)
- `mvp/sim/ticks.py` - `PRICE_SCALE`, `TICK_SIZE_SCALED`, `LOT_STEP_SCALED`, `MAX_NOTIONAL_SCALED`, `price_to_ticks()`, `position_size_ticks()`, `ZeroLotError`
- `mvp/tests/sim/test_ticks.py` - two real-data gcd tests (skip loudly off-SSD), a hypothesis round-trip proof, a deliberately-out-of-range representability test, a hand-pinned median-price sizing test, a $100k-boundary test, and a $1-$500k anti-vacuity sweep

## Decisions Made

See `key-decisions` in the frontmatter for the four decisions with full rationale (TDD-ordering recovery, the corrected median-price test value, the tick-grid hypothesis strategy, the out-of-range representability test, and the honest `ZeroLotError` message). All four are corrections to a literal reading of the plan's own worked examples, made because directly implementing the plan's stated numbers would either not compile against `price_to_ticks`'s own round-trip proof or could never actually fail as a test.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Implementation drafted before its tests (TDD ordering slip)**
- **Found during:** Task 1, before any commit -- caught by advisor review, not by a test failure
- **Issue:** `sim/ticks.py`'s Task-1-and-2-scoped implementation was written first, ahead of `tests/sim/test_ticks.py`, inverting `tdd="true"`'s required RED-before-GREEN order
- **Fix:** Moved the draft implementation to the session scratchpad, wrote Task 1's three tests against nothing, ran and observed `ModuleNotFoundError: No module named 'sim.ticks'` (genuine RED), then restored only the Task-1-scoped subset of the implementation and reached GREEN; Task 2's tests and implementation followed the same RED-then-GREEN sequence in their own commit
- **Files modified:** `mvp/sim/ticks.py`, `mvp/tests/sim/test_ticks.py`
- **Committed in:** `ffab475`, `4148878`

**2. [Rule 1 - Bug] The plan's own median-price test value does not match its stated arithmetic**
- **Found during:** Task 2, while deriving `position_size_ticks`'s expected output for the measured median
- **Issue:** The plan's action text names `price_ticks=<76,805 in ticks, i.e. ~$77,061.35>`; at `TICK_SIZE_SCALED = 10_000_000`/`PRICE_SCALE = 100_000_000` (0.1 USDT/tick), $77,061.35 is 770,613.5 ticks, not 76,805 -- a roughly 10x mismatch. Additionally, $77,061.35 is itself a half-tick value (it is a MID price, the average of two ticks; 06-RESEARCH.md Q6 measured 97.463% of real quotes at exactly a 1-tick spread), so passing it through `price_to_ticks` correctly RAISES under this same plan's D-06-05 round-trip proof (verified directly: round-trip error lands exactly at `TICK_SIZE_SCALED // 2`, the assertion's own strict-inequality boundary)
- **Fix:** Hardcoded `price_ticks = 770_613` (`floor($77,061.35 / $0.1)`, matching `price_to_ticks`'s own floor-division convention) with a docstring explaining both the arithmetic and why the value is not round-tripped through the conversion function
- **Files modified:** `mvp/tests/sim/test_ticks.py`
- **Committed in:** `4148878`

**3. [Rule 1 - Bug] Cent-rounded hypothesis prices cannot pass the half-tick round-trip proof**
- **Found during:** Task 1, while designing the round-trip hypothesis strategy
- **Issue:** The plan's behavior text suggests prices "rounded to the nearest cent"; a cent-granularity price such as $1,234.56 is up to 0.4 ticks off the $0.1 grid and its floor-division tick genuinely sits more than half a tick away from the scaled input -- this is the round-trip proof correctly rejecting an off-grid value, not a bug in `price_to_ticks`
- **Fix:** Generated hypothesis prices ON the tick grid (`st.integers(10_000, 2_000_000).map(lambda t: t / 10.0)`), matching how real `bid_price`/`ask_price` values are actually shaped (06-RESEARCH.md Q2's own row set is bid/ask, never a mid)
- **Files modified:** `mvp/tests/sim/test_ticks.py`
- **Committed in:** `ffab475`

**4. [Rule 1 - Bug] The "not representable" test's suggested construction (9+ decimal digits) cannot trigger the assertion**
- **Found during:** Task 1, while writing the non-representability test
- **Issue:** Rounding any float64 to the nearest `PRICE_SCALE` (1e-8) grid point is bounded by half a scale unit (5e-9) for any value in the real BTC price range -- confirmed by direct computation (`77061.123456789` round-trips to within `9.9e-10`, far under the `1e-6` epsilon), so no realistic decimal-precision price can ever fail this check
- **Fix:** Used a deliberately out-of-range magnitude (`50_000_000_000.123456`, ~$50 billion) where float64's own precision has degraded past the `1e-6` epsilon, confirmed empirically (`7.6e-6` measured discrepancy) before writing the test; documented in the test's own docstring why this is a constructed edge case, never a realistic price
- **Files modified:** `mvp/tests/sim/test_ticks.py`
- **Committed in:** `ffab475`

---

**Total deviations:** 4 auto-fixed (all Rule 1 -- correcting the plan's own worked examples against arithmetic that its own implementation enforces; none expanded functional scope beyond `TICK_SIZE_SCALED`/`LOT_STEP_SCALED`/`price_to_ticks`/`position_size_ticks`/`ZeroLotError` as specified)
**Impact on plan:** No scope creep. Every correction narrows toward the plan's own stated invariants (D-06-05's round-trip proof, D-06-20's refusal) rather than away from them.

## Issues Encountered

- `resolve_manifest` sha256-verifies the ENTIRE named partition file before this plan's own `.head(2_000_000)` read ever runs -- there is no bounded-verify form. For the bookTicker partition that is a ~240 MB read on every test invocation that reaches it (not skipped). Accepted here because it is the plan's own sanctioned resolution path ("resolved via `by_date_index_path` + `resolve_manifest`, exactly as `harness_span.py`/research did"); flagged for whichever future plan's test suite grows sensitive to this cost.
- No guardrail in `tools/check_*.py` restricts direct `data.store.resolve_manifest`/`by_date_index_path` calls from a test file (unlike `check_harness_accessor_only.py`'s restriction on `harness.accessor.materialize`/`features.tier.load_features`) -- confirmed by grep before writing the real-lake test, so no guardrail update was needed.

## User Setup Required

None -- no external service configuration required.

## Known Stubs

None. No hardcoded empty/placeholder values were introduced; both real-data tests either run against real, resolved, sha256-verified partition bytes or skip loudly (not silently) when the physical lake is unmounted.

## Threat Flags

None. Both trust boundaries this plan's `<threat_model>` names (a price entering the hot path as a silently-wrong tick; the $100 cap's arithmetic yielding zero) are exactly what `price_to_ticks`'s round-trip proof and `position_size_ticks`'s `ZeroLotError` mitigate -- no new, unlisted surface was introduced. `T-06-04`/`T-06-05`/`T-06-06` are each covered by a named test (see Accomplishments).

## What Was NOT Done

- **No SIM requirement was marked complete.** This plan's frontmatter names `SIM-03`, but per this plan's own absolute rules, `requirements mark-complete` was NOT run -- Plan 06-07 closes the phase and is the correct place for that.
- **No `@njit` kernel exists yet.** `sim/ticks.py` is deliberately numba-free; Plan 06-03's kernel and its polars-boundary wrapper both still need to be written, and must re-derive (not assume) the int64 overflow bound this plan documented in a comment (`max_notional_scaled * QTY_SCALE` at int64, not Python-int, arithmetic).
- **The half-tick `mid`/`pred_mid` conversion rule is not designed here.** This plan discovered (Deviation 2/3) that a real `mid` price is very often exactly a half-tick value, and that `price_to_ticks`'s own round-trip proof correctly refuses such a value. D-06-07 requires `pred_mid`/`X_price` to be converted to ticks for the kernel's threshold comparison -- Plan 06-03 (or whichever plan implements that comparison) must decide and document a tie-break rule for exactly-half-tick prices; this plan neither designed nor assumed one (Rule 4 territory, correctly left to the plan that owns the decision).
- **No lock on the exact row-set discrepancy between this plan's own bookTicker-wide median ($76,975.05, measured directly this session over the full 17,167,290-row partition) and 06-RESEARCH.md Q1's cited $77,061.35 median** was investigated -- both values land on exactly one lot under `position_size_ticks`, so the discrepancy has no effect on this plan's own tests, but a future plan computing P&L against the exact median should re-derive it from its own row set rather than citing either number here.
- **Neither test file nor `sim/ticks.py` was run under a subprocess / cross-process determinism check** (D-06-14) -- this plan's functions are pure Python-int/numpy arithmetic with no numba compilation and no cache, so D-06-14's cross-process concern does not yet apply; it will once Plan 06-03's `@njit` kernel exists.

## Next Phase Readiness

- Plan 06-03's kernel and polars-boundary wrapper can import `PRICE_SCALE`, `TICK_SIZE_SCALED`, `LOT_STEP_SCALED`, `MAX_NOTIONAL_SCALED` directly from `sim.ticks` as `@njit`-readable UPPER_CASE globals, and can check their own int64 arithmetic against `position_size_ticks`'s plain-Python reference implementation.
- The open half-tick tie-break question (see "What Was NOT Done") is the one concrete blocker-in-waiting for whichever plan implements D-06-07's threshold comparison; it is not a blocker for Plan 06-03's kernel skeleton itself, which does not yet need to convert a `mid`.
- No blockers identified for Plan 06-03 beyond the above.

---
*Phase: 06-event-driven-simulator*
*Completed: 2026-09-23*

## Self-Check: PASSED

All created files confirmed present on disk; both task commit hashes (`ffab475`, `4148878`) confirmed in `git log`.
