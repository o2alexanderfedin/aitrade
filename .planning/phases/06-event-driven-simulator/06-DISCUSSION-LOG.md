# Phase 6: Event-Driven Simulator - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-23
**Phase:** 06-event-driven-simulator
**Mode:** smart discuss under `/gsd-autonomous`. Four grey areas proposed as tables with a
recommended answer per question; the user accepted every recommendation, including the one
genuine trade-off (quantising the threshold comparison into ticks).
**Areas discussed:** Input and placement; Integer-tick accounting; Oracles; Outputs and determinism.

---

## Area 1 — What the simulator consumes and where it lives

| Option | Description | Selected |
|--------|-------------|----------|
| Bare numpy into the kernel; `mvp/sim/`; the accessor's frame; predictions as a parameter | | ✓ |
| DataFrame inside `@njit` | numba has poor DataFrame support; the boundary is where null handling belongs | |
| Simulator inside `mvp/features/` | Different lifecycle; the feature path is guarded by its own single-path scanner | |
| Simulator reads `lake/features` itself | A second path to rows is how a simulated row and a trained row drift apart | |

## Area 2 — Integer-tick accounting

| Option | Description | Selected |
|--------|-------------|----------|
| Declared tick constant pinned to a measurement; integer accumulation; quantised comparison; notional-derived size | | ✓ |
| Fetch the tick from exchangeInfo at runtime | A network call in the hot path's setup, and a silent rescale if the venue changes it | |
| Accumulate in float64 | The stored prices are not exact multiples of the tick (measured: one tick reads as 0.09999999999126885); drift is indistinguishable from P&L | |
| Compare the threshold in float64, accumulate in int64 | Defensible and slightly more faithful at sub-tick distances; rejected for cross-platform reproducibility of the DECISION, with the cost recorded | |

## Area 3 — Oracles

| Option | Description | Selected |
|--------|-------------|----------|
| Four oracles: hand-computed, perfect foresight, zero prediction, pure-Python twin; trade counts asserted; path dependence proven; flip invariant as a property test | | ✓ |
| Hand-computed scenarios only | Cannot bound the ceiling and cannot catch an inert kernel | |
| Trust the code for sequential-scan behaviour | A vectorized reimplementation passes every same-order test | |

## Area 4 — Outputs and determinism

| Option | Description | Selected |
|--------|-------------|----------|
| Trade log + equity curve + counters; two runs in one process and one in a fresh process; raise on null; fees/latency as zero-defaulted parameters | | ✓ |
| Return only total P&L | Phase 9's reporting would then need a second run or a second code path | |
| Double-run in one process only | Misses numba cache state and module-level mutation | |
| Hard-code the zeros | Removing the first simplification post-MVP would mean editing the kernel | |

---

## Raised during discussion, outside the phase

- The tick was measured rather than assumed (gcd over 2M real rows = 0.1 USDT exactly), and the
  same measurement revealed that the curated tier's Float64 prices are not exact multiples of it —
  which turned SIM-03 from a stated requirement into an observed necessity.
- The capture daemon remains stopped since the 2026-09-19 reboot; the pool is fixed at 7 built days.
