# Phase 6: Event-Driven Simulator - Research

**Researched:** 2026-09-23
**Domain:** numba event-driven backtest simulator, taker-only threshold policy, integer-tick P&L accounting
**Confidence:** HIGH on everything measured against the real lake (2026-09-13, unquarantined); MEDIUM on the one item that needed a geo-blocked external API; LOW-until-decided on two schema-conflict findings the planner must resolve before task-writing.

## Summary

Two locked decisions collide on inspection of the actual lake schema: D-06-01 requires
`bid_ticks`/`ask_ticks` in the kernel's inputs, and D-06-03 requires the simulator consume
only `harness.accessor.materialize`'s frame — but that frame's schema
(`features.tier.FEATURE_ROW_SCHEMA`) has never stored `bid_price`/`ask_price`, only `mid`.
This is not a hypothetical: it is confirmed by reading the real 2026-09-13 partition's schema
directly off disk. The planner must resolve this before writing tasks — three options are laid
out below with their measured costs, none silently preferred.

The second load-bearing finding: the $100 max-notional cap combined with the measured 0.001 BTC
venue lot step produces **exactly zero tradeable lots once BTC trades above $100,000** —
`floor(100 / price / 0.001) * 0.001 == 0` for any `price > 100_000`. On 2026-09-13 (median mid
$77,061) this floors to exactly one lot, so every oracle in this research is non-vacuous today,
but the policy has a hard, price-dependent dead zone the spec text does not mention and the
planner must decide whether to flag it, parameterize around it, or accept it as an MVP
simplification.

Every other locked decision (D-06-05 through D-06-16) checks out cleanly against real data: the
tick is exactly 0.1 USDT with zero round-trip ambiguity anywhere in a full day's prices, the
spread never touches zero so the zero-prediction oracle is non-vacuously always-flat, the
quantised-vs-float comparison costs single-digit-percent trade-count differences (not the
order-of-magnitude divergence a locked decision might have silently accepted), and the
perfect-foresight ceiling trades in the low thousands per day — but a zero-skill prediction on
the same rows trades 1.88 million times (Q3), so `n_decision_rows` is the only true bound for
the kernel's output-array preallocation, not the ceiling count. `mvp.md`'s decision-rule
text is confirmed struck; `mvp/spec.md`'s "Decision rule (Stage 2)" section (the corrected
version) is what every oracle below is computed against.

**Primary recommendation:** resolve the schema gap first (extend `FEATURE_ROW_SCHEMA` with
`bid_price`/`ask_price`, schema_version bump, rebuild the 7 existing feature days — cheapest and
most honest option, detailed below) before writing any task that touches the kernel's inputs;
everything else in this research is ready to plan directly against.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SIM-01 | Event-driven numba simulator: taker-only, zero fees, zero latency, $100 max notional, flip-only, sequential scan, no vectorization | Q3 sizes the kernel's output arrays from a measured real trade count; Q7's worked example gives the exact test that proves "sequential, not vectorized" as a runtime property; Q11 confirms the `@njit` discipline (signature style, `cache=True`, globals rule) the kernel must follow |
| SIM-02 | Oracle tests validate policy math against hand-computed scenarios | Q5/Q6 give real-data ceiling and floor numbers for the perfect-foresight and zero-prediction oracles; Q7's 4-row worked example is a ready-to-use hand-computed scenario for the path-dependence oracle |
| SIM-03 | Integer-tick price accounting in the hot path | Q2 confirms the tick-conversion rule is unambiguous on real data (zero disagreement between three candidate expressions, zero tie-zone rows); Q4 quantifies exactly what quantising the comparison costs; Q8 confirms `cache=True` is deterministic across processes on this machine (measured, not assumed) |

</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Decision-row schema (what a "row" is) | Data / Storage (`lake/features/`) | — | `features.tier.FEATURE_ROW_SCHEMA` is the one schema every consumer (trainer, harness, simulator) must agree on; a second implicit schema in the simulator is exactly the FEAT-01/D-06-03 "one code path" property being defended |
| Feature/label computation | Data / Storage (build-time, `features/build.py`) | — | Already built; Phase 6 only reads the output |
| Segment gating (holdout, purge/embargo, admission, errata, look-budget) | API/Backend (`harness/accessor.py`) | — | Already built; Phase 6's OWN tests bypass this tier entirely (D-06-03, confirmed mechanically in Q9) |
| Tick conversion, threshold comparison, position state machine | API/Backend (`mvp/sim/kernel.py`, new) | — | The `@njit` hot path; pure numeric transform, no I/O |
| Trade log / equity curve / counters (outputs) | API/Backend (`mvp/sim/kernel.py`'s Python wrapper) | — | Assembled from kernel output arrays, handed to Phase 9's reporting (EVAL-05) later |
| Oracle test fixtures | Test / Storage (`mvp/tests/sim/`, `tests/fixtures/`) | — | Hand-built frames matching the real schema, never the real lake (D-06-02/D-06-03) |

## User Constraints (from CONTEXT.md)

<user_constraints>

### Locked Decisions (D-06-01 .. D-06-16, verbatim from 06-CONTEXT.md)

**Area 1 — What the simulator consumes, and where it lives**
- D-06-01: The kernel takes bare numpy arrays; a thin wrapper does the conversion. `@njit` sees
  `etime` (int64), `bid_ticks`/`ask_ticks` (int64), `pred` (float64) and scalars — never a
  DataFrame. `mvp/sim/arrays.py` performs the polars→numpy conversion and asserts
  `null_count() == 0` per column first.
- D-06-02: New package `mvp/sim/`, tests in `mvp/tests/sim/` with NO `__init__.py`.
- D-06-03: The simulator accepts the harness accessor's frame; it never reads the lake itself.
  In Phase 6 every test builds its own `tmp_path` fixtures; no real segment is materialized and
  no selection-bias look is spent.
- D-06-04: Predictions are a parameter, not something this phase produces. The kernel takes a
  `pred` array aligned row-for-row with the decision rows.

**Area 2 — Integer-tick accounting (SIM-03)**
- D-06-05: One declared tick constant, pinned to the measurement. `TICK_SIZE_SCALED =
  10_000_000` (0.1 USDT in 1e-8 units). Float→tick conversion uses an explicit, named rounding
  rule, with a round-trip proof asserted on every converted row (`abs(ticks*tick - price) <
  half a tick`).
- D-06-06: Accumulation is integer; position, cash and P&L never touch float.
- D-06-07: The threshold comparison is quantised to ticks too. `pred_mid` and `X_price` are
  converted to ticks before the comparison. Cost, stated openly: a prediction 0.4 ticks above
  the ask no longer triggers an entry.
- D-06-08: Position size comes from the $100 notional cap at entry price. Quantity =
  floor(max_notional / entry_price) in scaled units, rounded down to the venue lot step
  (measured, not assumed). A flip closes the existing position and opens the opposite one in a
  single transition at the same decision row. Position stays in {-1, 0, +1} lots of that size.

**Area 3 — Oracles (SIM-02)**
- D-06-09: Four oracles — hand-computed scenario, perfect foresight
  (`pred = mid * (1 + ret_10s_mid)`, null-labelled rows excluded), zero prediction
  (`pred = mid` exactly), pure-Python twin.
- D-06-10: Anti-vacuity — every oracle asserts a trade count; perfect foresight must earn
  materially more than zero.
- D-06-11: Path dependence is proven by a test that permutes input rows and requires the result
  to CHANGE.
- D-06-12: The flip-only invariant is a property test over random sequences, asserted on the
  trade log.

**Area 4 — Outputs and determinism**
- D-06-13: A run returns a trade log, an equity curve and counters.
- D-06-14: Bit-identical across runs AND across processes (same process twice, plus one fresh
  subprocess; sha256 of serialized outputs compared).
- D-06-15: The simulator refuses nulls; it does not re-implement admission.
- D-06-16: Zero fees and zero latency are parameters defaulting to zero.

### Claude's Discretion
Mode was "smart discuss (autonomous run)" — the user accepted every recommended answer
including the quantised threshold comparison (D-06-07). No separate discretion table was
recorded in 06-CONTEXT.md beyond the sixteen decisions above; those decisions ARE the
discretion outcome.

### Deferred Ideas (OUT OF SCOPE)
- Taker fees, larger position size, non-zero latency, maker orders, L1-aware fills and queue
  position (mvp.md's post-MVP queue; fees are first in line).
- The X_bps sweep and its robust-region check — Phase 9.
- Sharpe, equity-curve plots, alpha decay, trade-log diagnostics — EVAL-05, Phase 9.
- RL as a replacement for the threshold policy — mvp.md Q4, past MVP.
- Capture daemon relaunch — operational, user's call.

</user_constraints>

## Project Constraints (from CLAUDE.md)

- Python stack: `polars` + `numpy` + `numba` only. **No `pandas`** anywhere, including
  transitively — CI's ruff `banned-api` rule enforces this mechanically; the simulator must not
  import anything that pulls pandas in.
- Everything MVP-related lives under `mvp/`; nothing outside it (strict containment rule).
- int64 nanoseconds since epoch everywhere; `etime` is the only clock.
- `numba==0.65.1` hard-pins `numpy<2.5`; do not let a new dependency drift the lockfile.
- MLflow via `mlflow-skinny` + `sqlalchemy` + `alembic`, never the full `mlflow` package;
  never `mlflow.search_runs()` — not directly relevant to Phase 6's kernel work but relevant if
  Phase 6 wires any MLflow tagging around the oracle runs.
- `uv lock` + `uv sync --frozen` in CI; ruff + mypy + pytest via pre-commit, all hooks
  mandatory (no `--no-verify`).
- Every measured statistic in this document names its row set, per this project's own
  "guardrails runtime-first" convention (STATE.md) — followed throughout below.

## CRITICAL FINDING 1 — D-06-01 and D-06-03 cannot both hold as written

**What the victim experiences:** a task that says "build the kernel per D-06-01, wire it to
D-06-03's accessor frame" cannot be executed — the frame the accessor returns has no
`bid_price`/`ask_price` columns to convert to `bid_ticks`/`ask_ticks`.

**Mechanism, verified by reading the real partition's schema directly (2026-09-13,
`lake/features/symbol=BTCUSDT/date=2026-09-13/part-1789812323488894000.parquet`):**

```
FEATURE_ROW_SCHEMA (features/tier.py, confirmed matches the on-disk partition exactly):
etime, decision_source_rank, decision_seq,
mid, imb_top, ofi, trade_flow,                      <- 4 features, NOT bid/ask
ret_10s_mid, ret_1s_mid, ret_1min_mid, ret_10min_mid,
warmup, post_gap_warmup, schema_version
```
`[VERIFIED: lake partition, 2026-09-23]`

`mid` is stored; the half-spread that would let a reader reconstruct `bid`/`ask` from `mid`
alone is NOT stored, and is NOT reliably `±0.05` — the spread is 1 tick on 97.463% of rows but
up to 50.0 (500 ticks) on the rest `[VERIFIED: full-day spread stats below]`. Reconstructing
`bid = mid - spread_estimate/2` would be silently wrong on 2.537% of rows, with no way to detect
which rows those are from the stored columns alone.

**Three options, none picked here:**

| Option | Cost | Risk |
|---|---|---|
| (a) Add `bid_price`/`ask_price` to `FEATURE_ROW_SCHEMA`, bump `FEATURE_SCHEMA_VERSION`, rebuild the 7 existing feature days | One schema migration, ~7 partition rebuilds (`features/build.py` already has the values available — see below); every downstream reader of the tier (Phase 5's harness, any Phase 7/8 code already written against the old schema) must tolerate two new columns | Lowest: additive, backward-compatible for readers that select by name; matches this project's own established migration pattern (`FEATURE_SCHEMA_VERSION` exists precisely for this) |
| (b) Reconstruct `bid`/`ask` as `mid ∓ half_measured_spread` | No schema change | Wrong on the measured 2.537% of rows where spread ≠ 1 tick (up to 500 ticks off on the widest observed row) — silently corrupts the decision rule on exactly the rows where the market moved enough to matter |
| (c) An as-of join to curated bookTicker inside the sim wrapper | No schema change to the tier | Directly violates D-06-03 ("never reads the lake itself"); reintroduces a second path to the same information the accessor already gates (holdout, DQ pause), defeating the reason D-06-03 exists |

**Evidence that (a) is buildable from data the kernel already has, and the REAL scope of
that option, stated precisely (corrected — the first draft of this research understated it as "a
column-selection change," which it is not):** `features/kernel.py`'s `state_f64` already carries
`prev_bid_price`/`prev_ask_price`/`prev_bid_qty`/`prev_ask_qty` as first-class state slots
(`STATE_F64_SLOTS`, kernel.py:140-167) — the prevailing quote at every decision row is a value
the kernel already computes internally. But at a TRADE decision row, `events["bid_price"]` in the
merged event array is `NaN` by construction (the cross-stream payload filler,
`features/event_stream.py:project_trade`) — the real bid/ask only exists in `state_f64`, never in
the per-row input arrays. Emitting it therefore requires, concretely:
1. Two new `@njit` output arrays in `features/kernel.py` (`out_bid`, `out_ask`), written from
   `prev_bid_price`/`prev_ask_price` every row, mirroring how `out_mid` is already written.
2. The matching change in `features/reference.py` (the pure-Python twin) and new rows in
   `tests/features/test_kernel.py`'s bitwise equivalence sweep.
3. `features/api.py`'s `_select()`/`FEATURE_PASS_SCHEMA`/`_frame()` must carry the two new
   columns through all three call sites (`for_training`/`for_inference`/`for_simulation`) — this
   module is one of the few sanctioned callers of `features.kernel`/`features.reference` per
   `tools/check_single_feature_path.py`, so the change stays inside the sanctioned boundary, but
   it touches every call site, not one.
4. `features/tier.py`'s `FEATURE_ROW_SCHEMA` gains the two columns, `FEATURE_SCHEMA_VERSION`
   bumps, and `tests/features/test_tier.py::test_feature_row_schema_is_exactly_the_catalogue_plus_bookkeeping`
   forces an explicit choice: catalogue `bid_price`/`ask_price` as real features in
   `spec/features.toml` (which then falls under `check_spec_diff`'s definition-drift tracking,
   Q12) or add them to `BOOKKEEPING_COLUMNS` (uncatalogued provenance, like
   `decision_source_rank`) — this choice is not free and is not made here.
5. The rebuild of the 7 existing feature days is a PROCESS question, not a script run:
   `features/tier.py:write_feature_partition` refuses (`FileExistsError`) writing into a `date=`
   directory that already has a committed `part-*.parquet` file ("partitions are write-once"),
   and feature manifests are append-only content-addressed bodies (`tier.py`'s own docstring). A
   genuine schema-version-2 rebuild needs an explicit decision about how the write-once tier's
   own stated escape hatch (removing the old `part-` file, or a manifest-revision mechanism) is
   exercised for 7 days at once — this is exactly the kind of operation this phase's own
   `hard_constraints` forbid a research pass from performing.
`[VERIFIED: features/kernel.py, features/api.py, features/tier.py, features/event_stream.py,
tools/check_single_feature_path.py all read directly]`

**Recommendation:** option (a) is still the right one — it is the only option that doesn't either
violate a locked decision (c) or introduce a silent, unmeasurable-at-read-time error on 1-in-40
rows (b) — but it is a real, multi-file migration plus a 7-day data rebuild, not a column
addition. This is presented as a recommendation, not a silent pick: the planner (or a return to
discuss-phase) should confirm before task-writing, and should treat it as its own small
plan/wave rather than folding it invisibly into Phase 6's first task, given it touches Phase
4/5 artifacts already marked complete.

## CRITICAL FINDING 2 — the $100 cap has a hard zero-lot ceiling at BTC > $100,000

`[VERIFIED: arithmetic on measured lot step, see Q1 below]`

`floor(100 / price / 0.001) * 0.001` is exactly zero for any `price > 100_000` USDT (at
`price == 100_000` it is exactly `0.001`, one lot; the very next representable price above that
floors to zero). On 2026-09-13 (median mid $77,061.35) this produces exactly one lot
($77.06 notional per lot) — every oracle below is non-vacuous today. But this is a real,
price-dependent dead zone in D-06-08's formula that neither `mvp.md` nor `spec.md` mentions, and
BTC has historically traded above $100,000 `[ASSUMED: general market knowledge, not verified
against this project's own data — the lake's price history tops out at $77,242 on the dates
captured]`. **Flagged for the user, not silently accepted**: if the harness pool or any future
held-out window contains a day where BTC trades above $100k, the policy simulated with today's
`$100`/`0.001`-lot-step combination trades ZERO lots that day regardless of prediction skill,
which would silently zero out P&L rather than raise. The oracle suite should include an explicit
test asserting the cap does not floor to zero at the prices in the days actually being tested,
so this failure mode is caught mechanically rather than discovered as an unexplained flat P&L
day.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| numba | 0.65.1 (already pinned, `[VERIFIED: repo lockfile / CLAUDE.md]`) | `@njit(cache=True)` hot path | Already the project's sim-hot-path mandate; Phase 4's kernel is the load-bearing precedent this phase copies verbatim in style |
| numpy | 2.4.6 (already pinned) | Array inputs/outputs to the kernel | Same reason; numba 0.65.1 hard-pins `numpy<2.5` |
| polars | 1.41.2 (already pinned) | The thin wrapper's DataFrame↔array boundary | Matches `features/event_stream.py:event_arrays`'s existing pattern |

No new dependencies are needed for Phase 6. `[VERIFIED: nothing in the 16 locked decisions or
the phase description names a library outside the already-pinned stack]`

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| A hand-rolled `@njit` state machine (D-06-01) | `hftbacktest` or similar backtest frameworks | Explicitly out of scope per REQUIREMENTS.md's Out-of-Scope table: "hftbacktest at MVP — pays for latency/queue realism explicitly out of MVP scope; ~200-line numba kernel suffices" `[CITED: .planning/REQUIREMENTS.md]` |

## Architecture Patterns

### System Architecture Diagram

```
 Phase 5 harness                Phase 6 (this phase)                  Phase 7+ (future)
 accessor.materialize()  --->  mvp/sim/arrays.py            <---  pred: np.ndarray
 (feature-tier frame,          (polars -> numpy, asserts      (model output, a parameter
  gated: holdout, DQ,           null_count()==0 per column,    this phase does not produce)
  purge/embargo, budget)        converts price cols -> ticks)
                                        |
                                        v
                          mvp/sim/kernel.py: run_sim(etime, bid_ticks, ask_ticks,
                                              pred, X_bps, max_notional, lot_step,
                                              fee_bps=0, latency_ns=0)   @njit(cache=True)
                                        |
                                        v
                    sequential scan, one row at a time, flip-only state machine
                    (position in {-1,0,+1}; entry/exit prices in integer ticks;
                     P&L accumulates in int64)
                                        |
                                        v
                          trade log (etime, side, price_ticks, qty, position)
                          equity curve (one value per decision row)
                          counters (trades, flips, rows_in_market)
                                        |
                                        v
                    Phase 6's own oracle tests (hand-computed, perfect-foresight,
                    zero-prediction, pure-Python twin) -- consume ONLY tmp_path
                    fixtures built directly against FEATURE_ROW_SCHEMA, never the
                    accessor and never the real lake (D-06-03)
                                        |
                                        v
                    Phase 9 (future): EVAL-05 reporting, MON-01..03 threshold sweep
                    -- reads this phase's trade log / equity curve, computes nothing
                    Phase 6 doesn't already hand it
```

### Recommended Project Structure
```
mvp/sim/
├── __init__.py           # empty, package marker (mvp/sim IS a real package, unlike tests/sim)
├── arrays.py             # D-06-01: polars -> numpy boundary, null assertion, tick conversion
├── kernel.py             # D-06-01/05/06/07/08: @njit(cache=True) run_sim, state machine
├── reference.py          # D-06-09 #4: pure-Python twin, same shape as features/reference.py
└── outputs.py            # D-06-13: trade log / equity curve / counters assembly, serialization
                           #          for D-06-14's sha256 hashing

mvp/tests/sim/             # NO __init__.py (D-06-02; fourth instance of this rule in the repo)
├── test_kernel.py         # bitwise kernel-vs-reference equivalence (features/test_kernel.py pattern)
├── test_oracles.py        # D-06-09's four oracles, D-06-10's anti-vacuity assertions
├── test_path_dependence.py # D-06-11, using the adjacent-swap perturbation (Q7 below)
├── test_flip_invariant.py  # D-06-12, hypothesis property test
└── test_determinism.py     # D-06-14, same-process x2 + subprocess x1, sha256 compare
```

### Pattern 1: Fixed-length preallocated output with a fill count (not growth, not two passes)
**What:** the kernel preallocates trade-log output arrays at length `n_decision_rows` (a
trivially safe upper bound — at most one trade per decision row) and returns the number of rows
actually written, exactly mirroring `features/kernel.py`'s own established discipline of
preallocated, caller-owned output arrays plus an explicit status/count rather than dynamic
growth.
**When to use:** always, for this kernel — see Q3 below for the measured real-day numbers that
justify this over the alternatives (a smaller cap with an overflow guard, or two passes).
**Example:**
```python
# Source: features/kernel.py's own established pattern (kernel.py:178-197, 199-232),
# adapted -- NOT a literal quote, this file does not exist yet.
def new_trade_log(n_decision_rows: int) -> dict[str, np.ndarray]:
    """Preallocated at the SAFE UPPER BOUND (one trade per decision row max).
    The kernel returns a fill count; the caller slices [:fill_count]."""
    return {
        "etime": np.empty(n_decision_rows, dtype=np.int64),
        "side": np.empty(n_decision_rows, dtype=np.int8),
        "price_ticks": np.empty(n_decision_rows, dtype=np.int64),
        "qty_scaled": np.empty(n_decision_rows, dtype=np.int64),
        "position_after": np.empty(n_decision_rows, dtype=np.int8),
    }
```
**D-06-14 interaction, stated explicitly (a gap in this research's first draft):** `np.empty`
leaves the unfilled TAIL of each array as uninitialized garbage — hashing the FULL preallocated
array (rather than `[:fill_count]`) would make D-06-14's cross-process sha256 comparison flaky
from day one, since uninitialized memory is not guaranteed identical across processes. D-06-13's
serialization/hashing step must slice every trade-log column to `[:fill_count]` before hashing
(or the kernel must zero-fill with `np.zeros` instead of `np.empty` — slicing is cheaper and is
the recommended choice, matching the "fill count" already being returned for exactly this
purpose).

### Pattern 2: Scaled-integer tick conversion, matching the existing QTY_SCALE convention
**What:** convert a float price to ticks via 1e-8 fixed-point rounding first (matching
`data/time_ns.py`'s `QTY_SCALE` pattern already used for quantities), then integer floor-divide
by the tick size in the same scaled units — never a bare float division in the `@njit` hot path.
**When to use:** the `mvp/sim/arrays.py` boundary (D-06-01), for `bid_price`/`ask_price`
(whichever route Finding 1 resolves to) and for `pred`.
**Example:**
```python
# Source: this research's Q2 measurement (see below) -- np.round(price/0.1),
# np.round(price*10), and this scaled-int form disagree on ZERO of 34,334,580
# real prices measured on 2026-09-13; this form is recommended because it matches
# data/time_ns.py's QTY_SCALE fixed-point idiom rather than introducing a second one.
PRICE_SCALE: int = 100_000_000          # matches QTY_SCALE
TICK_SIZE_SCALED: int = 10_000_000      # D-06-05, 0.1 USDT at PRICE_SCALE

def price_to_ticks(price: np.ndarray) -> np.ndarray:
    scaled = np.round(price * PRICE_SCALE).astype(np.int64)
    round_trip = scaled.astype(np.float64) / PRICE_SCALE
    assert np.all(np.abs(round_trip - price) < 1e-6), "price not representable at 1e-8"
    ticks = scaled // TICK_SIZE_SCALED
    assert np.all(np.abs(ticks * TICK_SIZE_SCALED - scaled) < TICK_SIZE_SCALED // 2), (
        "D-06-05's round-trip proof: reconstructed tick price must be within half a tick"
    )
    return ticks
```

### Pattern 3: Mark an open position at `bid_ticks`/`ask_ticks`, never at rounded `mid`
**What, and why this is SIM-03, not a nit:** `mid = (bid+ask)/2`; since `bid`/`ask` are exact
tick multiples, `mid` sits EXACTLY on a half-tick whenever the spread is an odd number of ticks —
measured on 2026-09-13, that's at least the 97.463% of rows with exactly a 1-tick spread (Q6).
`round(mid / tick)` therefore hits a genuine, SYSTEMATIC tie on the large majority of rows, not a
rare edge case — confirmed directly: the last row of 2026-09-13's usable rows has
`bid=76805.0, ask=76805.1, mid=76805.05`, and `mid / TICK = 768050.5` exactly
`[VERIFIED: measured 2026-09-23]`. `numpy`'s round-half-to-even resolves this inconsistently
depending on the integer part's parity — exactly the ambiguity D-06-05's round-trip proof exists
to rule out for `bid`/`ask` (which never sit on a tie, Q2), and it must not be silently
reintroduced at the equity-curve mark.
**When to use:** D-06-13's per-row equity curve, and any other point that marks an open position
to the current market rather than to a realized trade price.
**Example:** mark a long position's unrealized value at `bid_ticks` (what it could be sold for)
and a short position's at `ask_ticks` (what it would cost to buy back) — both exact tick
multiples by construction, and conservative (marking against the position, never in its favor).
**Correction to this research's own Q5 measurement:** the `-64` open-mark-ticks figure reported
there was computed with `round(mid_ticks)` in the research prototype, which is exactly the tie
this pattern warns against — it is retained in Q5 as an approximate illustrative number only
(the open position there is a small fraction of the day's total P&L either way), not as a
recommended production convention. The production kernel must use the bid/ask-mark rule above.

### Anti-Patterns to Avoid
- **Growing the trade-log array inside the `@njit` loop:** numba's nopython mode does not
  support dynamic-length array growth the way Python lists do; the codebase's own established
  answer (ring buffer overflow guard, kernel.py:299-306) is "preallocate a safe bound, refuse
  loudly if exceeded" — never a silent reallocation.
- **Comparing raw floats after D-06-07 was accepted:** if any code path compares `pred_mid` or
  `X_price` as floats after the tick conversion (rather than as the converted int64 ticks), the
  quantisation the locked decision pays for buys nothing — the whole point is that the
  COMPARISON, not just the storage, happens in ticks.
- **A second `bid`/`ask` reconstruction formula inside the simulator itself:** if Finding 1 is
  resolved via option (b) (reconstruct from `mid`), that reconstruction must live in exactly one
  place, the same "one code path" discipline `features/api.py`'s docstring argues for at length.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Latency/queue-position-aware fills | A custom partial-fill/queue model | Nothing — explicitly deferred (REAL-05, `hftbacktest` named as the eventual post-MVP candidate) | Out of scope; zero-latency taker-only assumption makes it unnecessary now |
| Cross-process bit-identical hashing | A bespoke serialization format | `hashlib.sha256` over `dtype.str + shape-as-bytes + arr.tobytes()` per output array, in a fixed declared order | Matches the project's existing `sha256`-everywhere convention (curated/feature partitions, manifests) rather than inventing a new one |
| A backtesting framework in general | Any of the pandas-based backtest libraries (backtrader, zipline, vectorbt) | The ~200-line `@njit` kernel this phase builds | Every pandas-based option violates the no-pandas rule transitively; REQUIREMENTS.md's Out-of-Scope table already rules out `hftbacktest` for the same realism-cost reason |

**Key insight:** everything Phase 6 needs is either already built (the kernel/reference/tests
pattern from Phase 4, the schema and gating from Phases 4-5) or is explicitly out of scope
(latency, queue position, fees) — there is no library gap to fill here at all, only an internal
consistency gap (Finding 1) to resolve.

## Common Pitfalls

### Pitfall 1: Treating the quantised-comparison cost as negligible without measuring it
**What goes wrong:** D-06-07 states the cost qualitatively ("a prediction 0.4 ticks above the
ask no longer triggers"); a task that implements it without ever measuring the actual magnitude
risks discovering post-hoc that quantisation silently dominates the signal at tight thresholds.
**Why it happens:** the decision was accepted on reasoning, not a number.
**How to avoid:** Q4 below has the number — on 2026-09-13, quantisation changes 44/16/0 trades
out of 2236/1090/66 at X_bps=0/1/5 respectively (under 2% relative at the widest gap), and the
P&L delta is under 0.5% of the ceiling at every threshold tested. Cite these numbers in the
oracle test's docstring so a future regression has something to compare against.
**Warning signs:** an oracle test that only checks trade count is nonzero, never that it's
close to the float-comparison count.

### Pitfall 2: Reusing a stale null-label count from an earlier research doc
**What goes wrong:** `04-CONTEXT.md`'s D-04-17 states "507 of 6,864,853" `ret_10s_mid` rows are
null on 2026-09-13. This research measured **0** nulls on the CURRENT partition
(`build_stats.json`'s own `null_primary_label_rows: 0`, confirmed directly on the parquet file
too) `[VERIFIED: 2026-09-23]`.
**Why it happens:** the day was rebuilt between Phase 4 (when D-04-17 was written, 3 built days)
and Phase 5 (when it was rebuilt as part of the 3→7 day expansion, per STATE.md) — with day+1
(2026-09-14) now actually present as the label tail, the end-of-partition null count for THIS
build is zero. The 507 figure is real but describes a build that no longer exists on disk.
**How to avoid:** if a Phase 6 task cites a null-label count, cite this research's measurement or
re-measure — not D-04-17's.
**Warning signs:** a test hard-coding "507" anywhere.

### Pitfall 3: Assuming `mid ∓ half a tick` reconstructs `bid`/`ask`
**What goes wrong:** covered in Finding 1 — 97.463% of rows are exactly right, 2.537% are not,
and the error is unbounded (up to 500 ticks measured on this one day).
**Why it happens:** D-04-17 already documents "97.5% of quotes sit at a 1-tick spread" as a
well-known fact about this venue, which makes the wrong reconstruction LOOK right on casual
inspection or a small fixture.
**How to avoid:** resolve Finding 1 via schema extension, not reconstruction; if reconstruction
is chosen anyway, the 2.537%-of-rows error must be an explicit, tested, accepted cost — not
discovered later.
**Warning signs:** a fixture with only 1-tick spreads that never exercises the wide-spread case.

### Pitfall 4: Assuming `accessor.materialize` against a `tmp_path` is technically impossible
**What goes wrong (corrected — the first draft of this research overclaimed this):** it is
tempting to assume `harness.budget._require_canonical_tracking_root` makes it impossible to call
`accessor.materialize` in a test at all. It does not. `allowed_root` resolves from the
`AIHF_MLFLOW_TRACKING_ROOT` env var (`data/lake_paths.py`), not a hardcoded path — and Phase 5's
own tests (`tests/harness/test_accessor.py::test_materialize_counts_val_and_oof_block_but_not_train_as_a_look`,
`::test_the_walking_skeleton_end_to_end`) call `materialize()` for a `val` role UNMOCKED against a
`tmp_path`-derived tracking root and it WORKS, recording a real look
(`look_count(...) == 1`) — because `tests/harness/conftest.py`'s `tracking_root` fixture
initializes a real MLflow SQLite store at `tmp_path/mlflow_root` (`mlflow.db` created via a
client call) and points the env var at it FIRST. `[VERIFIED: mvp/tests/harness/conftest.py:59-70,
mvp/tests/harness/test_accessor.py:277-333, mvp/harness/budget.py:189-201, mvp/data/lake_paths.py,
all read directly]`
**Why it happens:** the refusal's wording ("not the project's canonical MLflow store") reads as
absolute; the env-var indirection that makes it satisfiable from a test is a level of misdirection
away.
**How to avoid:** state D-06-03's choice accurately — Phase 6 tests bypass `accessor.materialize`
by DESIGN CHOICE (test isolation from Phase 5's plumbing; no need to pay the MLflow SQLite
initialization cost in every sim test; no ambiguity about whether a sim test's frame matches the
real gated schema), not because calling it is technically closed off. Build fixtures directly
against `FEATURE_ROW_SCHEMA` (or its Finding-1 extension) the way
`tests/fixtures/feature_tier.py:feature_frame()` already does for Phases 4/5's own tests.
**Warning signs:** documentation (including an earlier draft of this very research) asserting the
accessor path is "mechanically refused" for a test — it is available, just not used here.

## Code Examples

### Q7: A hand-computed worked example proving path-dependence (ready for D-06-11's docstring)

Four decision rows, `X_bps = 0`, ticks already applied (`ask`/`bid` vary by row — a real market
moves; keeping them constant across rows would make ANY permutation give the same P&L, which
would be a vacuous test):

| row | ask_ticks | bid_ticks | pred_ticks |
|---|---|---|---|
| 1 | 101 | 100 | 105 (long trigger: 105 > 101) |
| 2 | 103 | 102 | 95  (short trigger relative to bid=102: 95 < 102) |
| 3 | 99  | 98  | 105 (long trigger: 105 > 99) |
| 4 | 101 | 100 | 100 (neutral: 100 ≯ 101, 100 ≮ 100) |

**Original order**, flip-only sequential scan (enter at ask when going long, at bid when going
short; a flip closes the open leg and opens the opposite one on the SAME row):
```
row1: flat -> long trigger  -> enter long @ ask=101                      trades=1
row2: long, short trigger   -> flip: close long (+bid2-entry = 102-101=+1 tick), open short @102
                                                                          trades=2 flips=1  running pnl=+1
row3: short, long trigger   -> flip: close short (+entry-ask3 = 102-99=+3 ticks), open long @99
                                                                          trades=3 flips=2  running pnl=+4
row4: long, neutral         -> no trade
FINAL: trades=3  flips=2  closed_pnl_ticks=4  open position: long @99 (unrealized, not counted)
```

**Perturbed order** — swap rows 2 and 3 (adjacent, straddles the flip), `etime` column
unchanged, only the `(ask, bid, pred)` payload at positions 2 and 3 exchanged:
```
row1: flat -> long trigger  -> enter long @ ask=101                      trades=1
row2 (was row3's payload, ask=99 bid=98 pred=105):
      long, short_trigger?  105 < 98? NO -> no trade, stays long @101
row3 (was row2's payload, ask=103 bid=102 pred=95):
      long, short_trigger?  95 < 102? YES -> flip: close long (+102-101=+1), open short @102
                                                                          trades=2 flips=1  running pnl=+1
row4: short, neutral (ask=101 bid=100 pred=100):
      long_trigger? 100 > 101? NO -> no trade
FINAL: trades=2  flips=1  closed_pnl_ticks=1  open position: short @102 (unrealized, not counted)
```

**Result: trade count 3→2, closed P&L 4→1 ticks** — a single adjacent-row swap changes both the
count and the value under the correct sequential implementation. Any implementation whose
per-row output does not depend on neighboring rows (the class of bug "no vectorization" is meant
to rule out) computes each row's contribution independently of order and would therefore be
INSENSITIVE to this swap — which is exactly what makes this the right property test. Anti-vacuity
guard: both runs trade at least once (3 and 2 respectively), so the test cannot pass by both
sides trivially producing zero trades.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `mvp.md`'s original decision-rule pseudocode (multiplied predicted RETURN by midprice, compared the resulting price CHANGE against a price LEVEL) | `spec.md`'s corrected rule (`pred_mid = mid*(1+pred)`, compared directly against `best_ask +/- X_price`) | SPEC-03, Phase 2 | `mvp.md`'s block is explicitly struck; every oracle in this research is computed against the spec.md rule, confirmed by reading spec.md lines 173-197 directly |

**Deprecated/outdated:** `mvp.md`'s "Decision logic (explicit)" section (struck, points readers
to spec.md) `[VERIFIED: mvp/mvp.md:50-59, read directly]`.

## Q1 — Lot step, measured

**Row set:** every row of `curated/symbol=BTCUSDT/stream=trade/date=2026-09-13`
(1,409,705 rows, `qty` column) and every row of
`curated/symbol=BTCUSDT/stream=bookTicker/date=2026-09-13` (17,167,290 rows, `bid_qty`/`ask_qty`
columns).

- `gcd(trade.qty)` at 1e-8 fixed point: **0.001 BTC** exactly (scaled gcd = 100,000 of
  100,000,000 units/BTC). Round-trip at 1e-8 exact for all 1,409,705 rows.
- `gcd(bid_qty)`: **0.001 BTC**. `gcd(ask_qty)`: **0.001 BTC**. All three agree exactly.
  `[VERIFIED: measured 2026-09-23]`
- Binance's live `/fapi/v1/exchangeInfo` is geo-blocked from this environment (HTTP 451,
  measured directly with `curl`) — could not cross-check the venue's own declared `stepSize`.
  A public Binance announcement confirms the USDS-M futures `MIN_NOTIONAL` floor is **$1 USD**
  (well under both the $100 cap and one lot's ~$77 notional, so it does not bind here)
  `[CITED: binance.com support announcement, fetched 2026-09-23 — could not independently
  verify against the live API]`. The measured 0.001 BTC step matches the widely-known public
  BTCUSDT perpetual `stepSize` `[ASSUMED: matches training-data knowledge of Binance's contract
  spec, not independently re-verified against the live API this session]`.
- Median mid on 2026-09-13: **$77,061.35**. `$100 / $77,061.35 = 0.0012977` BTC, `floor(...,
  step=0.001) = 0.001` BTC = **one lot**, notional **$77.06** — nonzero, the cap does NOT
  produce zero lots today.
- **The general formula floors to zero for any price > $100,000** (see Critical Finding 2
  above) — this is arithmetic on the measured step, not a separate assumption.

## Q2 — Float→tick round-trip, measured

**Row set:** ALL `bid_price` and `ask_price` values from the full 2026-09-13 bookTicker
partition, 17,167,290 rows each side, 34,334,580 values total.

| Candidate expression | Max abs round-trip error | Rows failing `< half a tick` | Disagrees with `np.round(p/0.1)`? |
|---|---|---|---|
| `np.round(price / 0.1)` | 1.455e-11 | 0 | — |
| `np.round(price * 10)` | 1.455e-11 | 0 | 0 of 34,334,580 |
| int64-scaled `round(price*1e8) // 10_000_000` | 1.455e-11 | 0 | 0 of 34,334,580 |
| int64-scaled `round(round(price*1e8) / 10_000_000)` | 1.455e-11 | 0 | 0 of 34,334,580 |

Rows within `1e-8` of a `.5`-tick-fraction tie boundary (where a rounding rule's tie-break would
matter): **0 of 34,334,580**. `[VERIFIED: measured 2026-09-23]`

**Recommendation:** the scaled-integer form (`round(price * PRICE_SCALE) // TICK_SIZE_SCALED`),
because it matches `data/time_ns.py`'s existing `QTY_SCALE` fixed-point idiom rather than
introducing float division into the hot path, and because all four candidates are proven
identical on real data — there is no accuracy tradeoff, only a style one, and the codebase
already has a style.

## Q3 — Kernel output sizing, measured

**Row set:** the 6,864,853 decision rows of 2026-09-13's feature partition, joined against the
prevailing bookTicker quote (0 as-of misses, see Q10).

**Corrected framing (this research's first draft got the direction of this argument backwards
and was caught before being finalized — recorded here so the mistake isn't silently repeated):**
perfect foresight is the *smoothest* possible prediction (true 10-second returns are
autocorrelated, so the signal flips direction rarely) — it bounds the achievable **P&L** from
above, not the trade **count**. A noisy or low-skill model is NOT bounded above by the
perfect-foresight trade count; a model with skill near zero can flip on nearly every sign change
of its own noise and trade far MORE often. Measured directly, not assumed:

| Prediction source | X_bps=0 trades | X_bps=1 trades | X_bps=5 trades |
|---|---|---|---|
| Perfect foresight (`pred = mid*(1+ret_10s_mid)`), tick cmp | 2,192 | 1,074 | 66 |
| **Zero-skill** (`pred = mid*(1+shuffled ret_10s_mid)`, same marginal distribution, tick cmp) | **1,881,760** | **889,687** | **39,753** |

`[VERIFIED: measured 2026-09-23; shuffled with `numpy.random.default_rng(42).permutation`, same
6,864,853-row set]`. At X_bps=0 the zero-skill count is **27.4% of all decision rows** — 858x the
perfect-foresight count, not a fraction of it. **The only trade count that is a true, provable
upper bound is `n_decision_rows` itself** (at most one trade per decision row, by construction of
the flip-only rule) — no prediction-quality argument bounds it tighter, because Phase 6 does not
control what Phase 7+ hands it as `pred`.

**Cost of the safe bound, at the scale this actually runs at:** one day (6,864,853 rows) is
≈275 MB for a 5-column int64/float64 trade log (measured: `6,864,853 * 8 bytes * 5 columns`).
The CURRENT 7-day pool (60,926,503 decision rows, per STATE.md) is **≈2.27 GiB per single
preallocated trade log** — and Phase 9's threshold sweep (MON-01, X_bps swept via Optuna) means
this allocation happens repeatedly, once per trial, potentially dozens to hundreds of times. This
is a real, non-trivial cost at pool scale, not a one-off.

**Recommendation:** for Phase 6 itself (single-day oracle tests, `tmp_path` fixtures, D-06-03),
preallocate at `n_decision_rows` per call — correctness-first, matches `features/kernel.py`'s own
established discipline of a safe fixed bound plus an explicit status, and the per-call fixture
sizes in this phase's own tests are tiny. **Flag explicitly for the Phase 9 planner:** the
capped-array-with-explicit-overflow-guard pattern this research initially (wrongly) rejected as
unnecessary is very much back on the table for the pool-scale sweep — mirroring
`features/kernel.py`'s `RING_CAPACITY` + `STATUS_RING_OVERFLOW` pattern exactly (a caller-chosen
cap, refused loudly and never silently truncated if exceeded), sized per-run from whatever
prior information the caller has about expected trade frequency, rather than reallocating
`n_decision_rows` on every one of a sweep's many trials.

## Q4 — Quantised comparison cost, measured

**Row set:** same 6,864,853 usable decision rows, perfect-foresight predictions, at each X_bps.

| X_bps | float trades | tick trades | Δ trades | float pnl_ticks | tick pnl_ticks | Δ pnl_ticks | Δ pnl relative |
|---|---|---|---|---|---|---|---|
| 0 | 2,236 | 2,192 | 44 (2.0%) | 293,248 | 294,554 | -1,306 | -0.45% |
| 1 | 1,090 | 1,074 | 16 (1.5%) | 293,600 | 292,722 | +878 | +0.30% |
| 5 | 66 | 66 | 0 (0%) | 39,281 | 39,311 | -30 | -0.08% |

`[VERIFIED: measured 2026-09-23]`. D-06-07's accepted cost is real (dozens of trades differ per
day at tight thresholds) but small relative to the ceiling (under 0.5% P&L impact at every
threshold measured) — the locked decision's tradeoff is now backed by a number, not just
reasoning.

## Q5 — Perfect-foresight oracle mechanics, measured

**Row set:** 6,864,853 decision rows of 2026-09-13; `null ret_10s_mid` count on the CURRENT
partition is **0** (see Pitfall 2 — supersedes 04-CONTEXT.md's stale 507 figure, confirmed via
`build_stats.json`'s `null_primary_label_rows: 0` for this exact partition
`[VERIFIED: 2026-09-23]`); as-of quote misses: 0 (every decision row has a resolvable prevailing
quote, including the partition's very first row).

At X_bps=0 (the ceiling): 2,236 trades (float) / 2,192 (tick), closed gross P&L **293,248 /
294,554 ticks = 29,324.80 / 29,455.40 USDT per one-lot (0.001 BTC) position**, over one trading
day, zero fees, zero latency. End-of-day open position marked to last mid: -64 ticks
(-$6.40), immaterial relative to the closed total. This is computed against `spec.md`'s
corrected rule, not `mvp.md`'s struck one (confirmed by reading spec.md:173-197 directly before
writing the prototype). **This is the ceiling every real model (Phase 7+) is measured against,
using information (the realized future return) no real model can have exactly.**

## Q6 — Zero-prediction oracle, verified structurally (not just empirically)

**Row set:** full 2026-09-13 bookTicker partition, 17,167,290 rows.

- Min spread: **0.1** (exactly 1 tick). Median spread: 0.1. Max spread: 50.0 (500 ticks).
- Rows at exactly 1 tick: 16,731,719 (**97.463%**).
- Crossed/locked rows (`bid >= ask`): **0**. Zero-or-negative spread rows: **0**.
  `[VERIFIED: measured 2026-09-23; consistent with Phase 4's own kernel counter finding of 0
  crossed books on this same day]`

Since spread is always strictly positive, `mid = (bid+ask)/2` is always strictly `< ask` and
`> bid`. With `pred = mid` exactly and `X_bps = 0`, `pred_mid > best_ask` is `mid > ask`, which
is false for every row STRUCTURALLY (not merely empirically) — the same argument holds for the
short side by symmetry. The simulation run confirms: **0 trades, 0 P&L**, and non-vacuously so,
because the spread never once touches zero on this real day (so the "always false" result isn't
an artifact of a degenerate all-equal-price fixture).

## Q7 — Path dependence test design

See the worked example under "Code Examples" above — an adjacent-row swap (positions 2 and 3 of
a 4-row fixture) changes trade count 3→2 and closed P&L 4→1 ticks under the correct sequential
implementation, with an argument for why an order-independent (vectorized) reimplementation
would be insensitive to it. Recommended perturbation for D-06-11's actual test: generate a
longer random sequence (hypothesis-style, matching `features/reference.py`'s equivalence-test
pattern), swap one randomly-chosen adjacent pair that straddles at least one trigger, assert the
result differs, and assert both runs have `trade_count > 0` (anti-vacuity, D-06-10's own
requirement applied here too).

## Q8 — Bit-identical across processes

**Precedent for the subprocess mechanism**, already in this codebase:
`mvp/tests/capture/test_rotation_atomicity.py` and
`mvp/tests/capture/test_ws_client_raw_archive.py` both use
`subprocess.run([sys.executable, "-c", script], cwd=mvp_root, capture_output=True, text=True,
timeout=...)` `[VERIFIED: read directly]`. `mvp/tests/features/test_kernel.py:471-472` also
spawns `subprocess.run([sys.executable, "-m", "tools.check_numba_globals"])` — direct precedent
for D-06-14's cross-process test.

**Hash recommendation:** `hashlib.sha256` over `dtype.str + shape-as-bytes + arr.tobytes()` per
output array (trade log columns, equity curve), concatenated in a fixed declared column order —
never a `repr()`/string form (float repr formatting is not guaranteed bit-stable the way raw
bytes are).

**Numba cache determinism — measured, not cited.** Ran `features.kernel.run_kernel` (the
existing, already-compiled kernel) twice, each in a fully separate `python3` process, each with
a distinct, freshly-created `NUMBA_CACHE_DIR`. The resulting `.nbi` and `.nbc` cache files were
**byte-identical** (`cmp` confirms) across both processes on this machine, same numba/llvmlite/
numpy versions, same CPU. `[VERIFIED: measured 2026-09-23; NUMBA_CACHE_DIR set to
`/tmp/aihf-numba-research-{1,2}`, confirmed no cache artifact leaked into `mvp/features/`
afterward per the hard constraint]`. `cache=True` is deterministic across processes here; D-06-14
can rely on this rather than merely hoping it.

## Q9 — Where the simulator sits relative to the harness

**Corrected (the first draft of this research overclaimed this as a mechanical impossibility —
see Pitfall 4):** `accessor.materialize`'s look-accounting is not closed off by construction. It
CAN be exercised in a test: `harness.budget._require_canonical_tracking_root` compares
`tracking_root` against `allowed_root`, which resolves from the `AIHF_MLFLOW_TRACKING_ROOT` env
var — Phase 5's own `tests/harness/conftest.py:tracking_root` fixture initializes a real MLflow
store under `tmp_path` and points that env var at it, and Phase 5's own tests call `materialize()`
for `val`/`oof_block` roles unmocked against it, successfully recording a look. `[VERIFIED: see
Pitfall 4's citations]` So the answer to "does calling it in a test against a `tmp_path` tracking
root spend anything real" is: no real production budget (the tmp_path store is thrown away with
the test), but it DOES exercise the real gating code path, including a real (test-local) look
being counted — nothing about it is a no-op or a mock unless the test chooses to monkeypatch it
(which some Phase 5 tests also do, for spying/observability, not because the unmocked path fails).

**D-06-03's choice to bypass the accessor entirely is therefore a deliberate simplicity/isolation
choice, not a technical wall** — restated accurately here because it changes how the planner
should describe the decision to a future reader.

**Cheapest way to build a valid frame without the accessor:** `tests/fixtures/feature_tier.py`'s
existing `feature_frame(rows=N, nan_in=...)` factory already builds well-formed
`FEATURE_ROW_SCHEMA` frames this way, and is reused across `tests/features/test_tier.py`,
`test_build.py`, and `tests/harness/test_accessor.py` `[VERIFIED: grep across mvp/tests/, read
directly]`. Phase 6 should add an analogous factory (in `tests/fixtures/`, not `tests/sim/`,
matching the no-package-shadowing rule) that imports the real schema constant (whichever Finding
1 resolves it to) rather than hand-listing columns.

**Risk of a second, divergent idea of a decision row:** real and concrete — if the sim's test
fixture is hand-rolled with its own column list instead of asserting `df.schema ==
dict(FEATURE_ROW_SCHEMA)` (the pattern `event_arrays`/`write_feature_partition` already use
elsewhere in this codebase), it can silently drift from the real schema with no test catching
it — this is precisely how Finding 1 surfaced in the first place (a locked decision assumed a
column the real schema doesn't have). Mitigate with the same "assert exact schema equality
against the imported constant" discipline used throughout Phases 3-5.

## Q10 — Null and edge-case inventory, measured

**Row set:** all 6,864,853 decision rows of 2026-09-13.

| Column | Nulls | Note |
|---|---|---|
| `ofi` | 1 | The partition's first decision row — matches `reference.py`'s documented rule ("NaN on the first L1 update... never seeded from the previous day") |
| every other feature/label column | 0 | Including all four label columns — see Pitfall 2 |
| `bid_price`/`ask_price` (via as-of join) | 0 | Every decision row has a resolvable prevailing quote, including row 1 |

`warmup=True`: 157 rows. `post_gap_warmup=True`: 0 rows (no reconnect gaps this day).
Crossed/locked book (`bid>=ask`): 0. Zero-or-negative spread: 0. `[VERIFIED: measured
2026-09-23]`

**Implication for D-06-15 (refuse-on-null), scoped correctly (the first draft of this research
over-generalized a one-day measurement — corrected here):** on 2026-09-13 specifically, the
simulator's actual inputs per D-06-04/D-06-01 (`etime`, `bid_ticks`/`ask_ticks`, `pred`) are
never null — `ofi` is the only null-bearing column, and it is never fed to the simulator. But
**this does NOT generalize to every built day.** 2026-09-14 (the day of a real 303.7s capture
outage, per `spec.md`'s `resync_windows` discussion) has REAL null labels:
`ret_10s_mid.null_gap = 38,565` of 11,323,694 decision rows (0.34%)
`[VERIFIED: lake/features_meta/symbol=BTCUSDT/date=2026-09-14/build_stats.json, read directly]`.
`bid_price`/`ask_price`/`mid`/`etime` themselves are unaffected by this (the gap nulls the LABEL
lookup, not the quote), but if `pred` is ever derived from a label read through the accessor on a
day like this (e.g., a perfect-foresight-style oracle run against a real, accessor-gated segment
rather than this phase's own `tmp_path` fixtures), `pred` would be null on those rows too.

**This forces, rather than merely illustrates, D-06-09's "null-labelled rows excluded" wording:**
combined with D-06-15's blanket refuse-on-null, a perfect-foresight oracle CANNOT feed a
NaN-derived `pred` into the general simulator and expect anything other than a refusal — so
"excluded" must mean **dropped from the sequence entirely before the simulator ever sees the
row**, not "present but predicted as NaN and silently skipped." This is not an accidental
convention this research's prototype happened to pick (an earlier draft implied that); it is the
only reading consistent with D-06-15's own refusal policy, and should be stated as such,
explicitly, in the oracle's own docstring.

**Errata note, checked directly:** `harness.errata.mask_errata_cells` nulls cells keyed
EXCLUSIVELY by `label_column` (`by_label` dict, keyed on `cell["label_column"]`) — it cannot
structurally target `mid`, `bid_price`, `ask_price`, or any feature column, only the four
`ret_*_mid` labels `[VERIFIED: mvp/harness/errata.py:253-293, read directly]`. One real errata
manifest exists in the registry today
(`mvp/data/lake_registry/errata/22190ad9...json`). So errata is a live concern for the simulator
only at the same single point label-nulls already are — a `pred` sourced from an
accessor-gated `ret_10s_mid` — and is subsumed by the same open question (drop vs. refuse), not a
separate one. Not a concern at all for Phase 6's own tests (D-06-03's `tmp_path` fixtures never
go through the accessor, so never see errata masking).

## Q11 — Numba specifics, confirmed

- Signature style: bare `@njit(cache=True)`, no `nopython=` keyword (numba's own default has
  been `nopython=True` since 0.59), no `parallel=True`. Every input is an explicit positional
  parameter; the ONE module-level global the existing kernel reads inside its `@njit` body is
  `TRADE_FLOW_WINDOW_NS` (UPPER_CASE, a plain `int`) — the sanctioned exception
  `check_numba_globals.py` exists to allow. `[VERIFIED: features/kernel.py:199-232, 309]`
- Read-only input views: `event_arrays` (the analogous existing boundary) hands back read-only
  zero-copy numpy views straight into the `@njit` function; outputs are always the CALLER's own
  separate allocation, never a view of input memory (`new_outputs(n)`).
  `[VERIFIED: features/reference.py:254-268, features/event_stream.py]`
- What `check_numba_globals.py` would reject: an explicit `global` statement inside the `@njit`
  function, or a Load of any module-level name that is NOT all-uppercase. **Gap, stated
  precisely:** the AST check only tests `name.isupper()` — a module-level `PARAMS = {"x": 1}`
  dict, if declared directly (not via `from x import TABLE`), would still be treated as an
  UPPER_CASE-named "constant" and PASS the lint check mechanically, since the tool does not
  inspect the assigned value's type `[VERIFIED: tools/check_numba_globals.py:_check_reads, read
  directly — the isupper() test is the entire discriminator]`. The actual backstop for a
  container-typed global is numba's own compiler: nopython mode cannot reflect a Python `dict`
  as a compile-time constant and would raise a `TypingError` at JIT-compile time — a real but
  separate enforcement layer from the repo's own lint tool. State both when documenting this for
  the planner: the lint tool catches ordinary scalar-constant mistakes; numba's compiler is the
  backstop for anything the lint tool's `isupper()`-only check would wrongly let through.
- Exact passing style: `TICK_SIZE_SCALED: int = 10_000_000` — bare `int`, module scope,
  UPPER_CASE — exactly `data/time_ns.py`'s and `features/kernel.py`'s (`RING_CAPACITY`) existing
  convention.

## Q12 — Spec update

`tools/check_spec_diff.py`'s docstring, confirmed by reading it directly: it fails CI only if
"`mvp/spec.md`'s rendered catalogue tables drift from `mvp/spec/{features,labels}.toml`, or if a
feature's `definition` / a label's `computation` changed under an existing name." `[VERIFIED:
tools/check_spec_diff.py:1-13]` It has no mechanism that inspects any other prose section of
spec.md — a new "Simulator" section is NOT enforced by any CI hook, confirming D-06 CONTEXT's own
"manual discipline with no CI backstop" claim.

**Recommended section placement:** immediately after "Decision rule (Stage 2)" in spec.md's
Contents TOC (it operationalizes that exact rule) and before "DOs", with subsections:
- Tick constant & rounding rule (D-06-05, this research's Q2 numbers)
- Quantised threshold comparison and its measured cost (D-06-07, this research's Q4 numbers)
- Position sizing & the $100/lot-step interaction, including the >$100k-price zero-lot edge case
  (D-06-08, Critical Finding 2)
- The four oracles (D-06-09)
- Outputs: trade log / equity curve / counters (D-06-13)
- Determinism guarantee: bit-identical, same-process and cross-process (D-06-14)

## Q13 — What "$100 max notional, flip-only" means for P&L accounting at a flip

Given Critical Finding 2 / Q1: at every price in this dataset's real range (2026-09-13, ~$77k)
and indeed at ANY price `<= $100,000`, `floor($100/price / 0.001) * 0.001` evaluates to exactly
`0.001` BTC regardless of whether size is recomputed fresh at each entry/flip or held fixed from
the first entry — **the two readings are observationally identical below $100k, and both break
identically (to zero lots) above it.** The ambiguity only has independent teeth once a redesign
addresses the >$100k dead zone (e.g., a larger `MAX_NOTIONAL_USD` or a smaller effective step at
higher prices) — until then it is subsumed by Critical Finding 2, not a separate risk axis.

**Recommendation, directly supported by D-06-08's own wording** ("Quantity comes from the $100
notional cap **at entry price**" — singular, per-entry, not "at the position's original entry
price"): recompute size fresh at every entry/flip from the CURRENT entry price. This is the more
standard reading of a per-order notional cap and is not an invented tie-break — it's the locked
decision's own text, made explicit because the CONTEXT.md phrasing doesn't spell out the flip
case.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Binance BTCUSDT perpetual futures `LOT_SIZE.stepSize` is publicly 0.001 BTC, matching the measured gcd | Q1 | Low — the gcd was measured directly on three independent real data sources (trade qty, bid_qty, ask_qty) and all three agree exactly; this assumption only supplements that measurement with an unverified cross-check against the (geo-blocked) live API |
| A2 | BTC has historically traded above $100,000 USD, making Critical Finding 2's dead zone a live concern rather than a theoretical one | Critical Finding 2 | If wrong (BTC never approaches $100k in any window this project actually simulates), Finding 2 is a documented edge case rather than an active risk — still worth the explicit test either way |
| A3 | Binance USDS-M futures `MIN_NOTIONAL` is $1 USD (does not bind against the $100 cap or the ~$77 one-lot notional) | Q1 | Low — even if the actual figure differs somewhat, it would need to exceed ~$77 to bind against the measured one-lot notional at current prices, which the announcement's stated figure (well below that) makes unlikely |

## Open Questions

1. **Which option resolves Critical Finding 1 (the schema gap)?**
   - What we know: three concrete options with measured costs (schema extension, lossy
     reconstruction, accessor-bypassing lake read), and evidence the data needed for option (a)
     already exists in the kernel's own state.
   - What's unclear: whether extending `FEATURE_ROW_SCHEMA` and rebuilding 7 feature days is
     Phase 6's own responsibility or belongs to a small preceding phase/plan, given it touches
     Phase 4/5 artifacts already marked complete.
   - Recommendation: surface to the user or a return to discuss-phase before task-writing;
     research recommends option (a) but does not decide it.

2. **Should the >$100k dead zone (Critical Finding 2) be mitigated in Phase 6, or accepted and
   flagged as a known MVP simplification?**
   - What we know: the exact price threshold and the exact arithmetic that produces it.
   - What's unclear: whether any date in the project's eventual held-out window is expected to
     cross $100k (this project's own captured data tops out at $77,242 as of 2026-09-19).
   - Recommendation: at minimum, add an oracle-suite test that asserts the lot size is nonzero
     at the prices in whatever fixture/day the suite actually exercises, so a future day crossing
     the threshold fails loudly rather than silently reporting a flat P&L.

3. **Does `features/build.py`'s cadence make a schema-version-2 rebuild of 7 days cheap or
   expensive in wall-clock terms?**
   - What we know: build_stats.json exists per day and the build is already automated
     (`build_features_range`).
   - What's unclear: exact per-day build time (not measured this session — out of scope for a
     read-only research pass that must not write to the lake).
   - Recommendation: a quick timing measurement (read-only, does not require writing) could be
     added if the planner picks option (a) and wants a wall-clock estimate before committing to
     the migration.

4. **When Phase 7+ eventually sources `pred` from a real, accessor-gated segment (not this
   phase's own oracle), how should a null/errata-masked label be handled?**
   - What we know: D-06-09's own oracle forces "excluded" to mean "dropped from the sequence
     entirely" (Q10) for THIS phase's perfect-foresight oracle, because D-06-15 refuses nulls
     outright and there is no other consistent reading. Errata can only ever null a label column,
     never `mid`/`bid`/`ask` (Q10).
   - What's unclear: whether that same "drop the row" convention is right once a real model
     (which cannot itself have a null prediction the way a label-derived oracle can) is the one
     supplying `pred` — a dropped row changes which etimes the policy is evaluated at, which is a
     different kind of decision than an oracle-construction convenience.
   - Recommendation: not Phase 6's decision to make (D-06-04's `pred` is an opaque parameter to
     this phase) — flag for whichever future phase wires a real accessor-gated segment through
     the simulator for the first time.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| numba | `@njit` kernel (SIM-01) | Yes | 0.65.1 | — |
| numpy | array boundary | Yes | 2.4.6 | — |
| polars | DataFrame boundary | Yes | 1.41.2 | — |
| Binance `fapi` REST API | Cross-checking `LOT_SIZE`/`MIN_NOTIONAL` (research only) | No — HTTP 451, geo-blocked from this environment (measured directly with `curl`) | — | Not required for Phase 6 execution at all (the simulator never calls a live API); the measured real-data gcd (Q1) and a public announcement page fully substitute for research purposes |

**Missing dependencies with no fallback:** none — the one unavailable dependency (live Binance
API) is not required by anything Phase 6 actually builds.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.x (already the project standard) + hypothesis 6.x for property tests |
| Config file | `mvp/pyproject.toml` (existing `pythonpath = ["."]`, `testpaths` config — no new config needed) |
| Quick run command | `cd mvp && ./.venv/bin/pytest tests/sim -x -q` |
| Full suite command | `cd mvp && ./.venv/bin/pytest -q` (pre-commit already runs the whole suite on every commit; no `--no-verify`) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SIM-01 | Sequential, taker-only, flip-only, zero-fee/latency-parameterized state machine | unit (kernel-vs-reference equivalence) | `./.venv/bin/pytest tests/sim/test_kernel.py -x -q` | ❌ Wave 0 |
| SIM-01 | Path dependence (D-06-11) | property (hypothesis) | `./.venv/bin/pytest tests/sim/test_path_dependence.py -x -q` | ❌ Wave 0 |
| SIM-01 | Flip-only invariant (D-06-12) | property (hypothesis) | `./.venv/bin/pytest tests/sim/test_flip_invariant.py -x -q` | ❌ Wave 0 |
| SIM-02 | Four oracles + anti-vacuity (D-06-09, D-06-10) | unit | `./.venv/bin/pytest tests/sim/test_oracles.py -x -q` | ❌ Wave 0 |
| SIM-03 | Tick round-trip proof (D-06-05) | unit (asserted per-row in the conversion function itself, per Pattern 2 above) | `./.venv/bin/pytest tests/sim/test_kernel.py -k round_trip -x -q` | ❌ Wave 0 |
| SIM-03 | Bit-identical same-process x2 + subprocess x1 (D-06-14) | integration (subprocess) | `./.venv/bin/pytest tests/sim/test_determinism.py -x -q` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `./.venv/bin/pytest tests/sim -x -q` (fast; no real lake I/O, only
  `tmp_path` fixtures per D-06-03)
- **Per wave merge:** full suite (`./.venv/bin/pytest -q`) — mirrors this project's existing
  pre-commit hook, which already runs the whole suite on every commit
- **Phase gate:** full suite green, plus `python -m tools.check_numba_globals` green, before
  `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `mvp/tests/sim/test_kernel.py` — kernel-vs-reference bitwise equivalence, covers SIM-01
- [ ] `mvp/tests/sim/test_oracles.py` — the four D-06-09 oracles, covers SIM-02
- [ ] `mvp/tests/sim/test_path_dependence.py` — D-06-11, covers SIM-01 (uses this research's Q7
      worked example as a starting fixture)
- [ ] `mvp/tests/sim/test_flip_invariant.py` — D-06-12, covers SIM-01
- [ ] `mvp/tests/sim/test_determinism.py` — D-06-14, covers SIM-03 (subprocess precedent already
      exists elsewhere in the repo, see Q8)
- [ ] `mvp/tests/fixtures/sim_frame.py` (or an extension of `tests/fixtures/feature_tier.py`) —
      a `FEATURE_ROW_SCHEMA`-shaped (or Finding-1-extended-schema-shaped) frame factory, shared
      by all of the above, importing the real schema constant rather than hand-listing columns
      (Q9's stated risk-mitigation)
- [ ] Framework install: none — pytest/hypothesis/numba/numpy/polars are already pinned and
      present in `mvp/.venv`

## Security Domain

`security_enforcement` is absent from `.planning/config.json` (treated as enabled per the
instruction default), but Phase 6 builds an offline, batch, numeric simulation kernel with no
network interface, no user input, no authentication surface, and no secrets — most ASVS
categories do not apply to this phase's actual attack surface.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | No auth surface in this phase |
| V3 Session Management | No | No sessions |
| V4 Access Control | No | Access control (holdout, DQ, budget) is already enforced one layer up, in `harness.accessor` — Phase 6 does not re-implement it (D-06-15's own framing: "it does not re-implement admission") |
| V5 Input Validation | Yes | Schema/null validation at the `mvp/sim/arrays.py` boundary (D-06-01, D-06-15) — exact assert-then-raise pattern already used throughout `features/event_stream.py:event_arrays` and `features/tier.py`'s schema checks; no new library needed, this is the existing codebase idiom |
| V6 Cryptography | Partial | sha256 for D-06-14's determinism hashing (`hashlib`, stdlib) — not a security control in the traditional sense, but the same primitive; no key material or secrets are involved |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| A malformed/null-laden input frame silently producing a plausible-looking but wrong P&L | Tampering (of data integrity, not an adversary) | D-06-15's refuse-on-null policy, enforced at the `arrays.py` boundary before the kernel ever runs — matches `event_arrays`'s existing "a null Float64 column becomes a NaN-filled copy silently" control elsewhere in this codebase |
| A `@njit` function silently reading stale module-global state across calls (numba freezes globals at first compile) | Tampering | `check_numba_globals.py`'s lint rule (Q11) + numba's own `TypingError` backstop for container globals |
| Numba cache poisoning across machines/CI runners producing divergent compiled output undetected | Tampering / Repudiation | D-06-14's cross-process sha256 comparison is exactly this control, generalized from "same machine, different process" (measured here) to "different CI runner" (not measured this session — same mechanism, different scope) |

## Sources

### Primary (HIGH confidence — measured directly this session)
- `lake/features/symbol=BTCUSDT/date=2026-09-13/part-1789812323488894000.parquet` — schema, null
  counts, label values (read via `pl.read_parquet`)
- `lake/curated/symbol=BTCUSDT/stream=bookTicker/date=2026-09-13/part-1789594249174018000.parquet`
  — bid/ask prices/quantities, spread stats, lot-step gcd
- `lake/curated/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-1789594213253946000.parquet` —
  trade qty gcd
- `lake/features_meta/symbol=BTCUSDT/date=2026-09-13/build_stats.json` — `null_primary_label_rows: 0`
- `mvp/spec.md` lines 173-197 ("Decision rule (Stage 2)") — the authoritative rule
- `mvp/mvp.md` lines 44-59 — the struck block, confirmed pointing to spec.md
- `mvp/features/kernel.py`, `mvp/features/reference.py`, `mvp/features/event_stream.py`,
  `mvp/features/api.py`, `mvp/features/tier.py` — read in full
- `mvp/harness/accessor.py`, `mvp/harness/budget.py:189-201`, `mvp/harness/purge_embargo.py` —
  read directly, including `_require_canonical_tracking_root`
- `mvp/tools/check_numba_globals.py`, `mvp/tools/check_spec_diff.py` — read in full
- `mvp/tests/conftest.py`, `mvp/tests/fixtures/feature_build.py`,
  `mvp/tests/fixtures/feature_tier.py` — read directly
- `mvp/tests/harness/conftest.py` (the `tracking_root`/`isolated_canonical_tracking_root`
  fixtures), `mvp/tests/harness/test_accessor.py:277-333` — read directly, corrected Q9/Pitfall 4
- `mvp/harness/errata.py:253-293` (`mask_errata_cells`), `mvp/data/lake_registry/errata/` (one
  real manifest present) — read directly
- `lake/features_meta/symbol=BTCUSDT/date=2026-09-14/build_stats.json` — `ret_10s_mid.null_gap =
  38,565` (outage-day label nulls, corrects the one-day-only Q10 claim)
- Numba cache determinism — measured directly (two fresh processes, two fresh cache dirs, `cmp`
  on the resulting `.nbi`/`.nbc` files)
- Shuffled-label (zero-skill) trade-count measurement — measured directly (`numpy.random.default_rng(42).permutation`
  on `ret_10s_mid`, same 2026-09-13 row set, corrects Q3's original ceiling-count framing)

### Secondary (MEDIUM confidence)
- Binance USDS-M futures `MIN_NOTIONAL = $1 USD` — fetched from a Binance support announcement
  page; could not cross-check against the live `/fapi/v1/exchangeInfo` endpoint (geo-blocked,
  HTTP 451, confirmed via direct `curl`)

### Tertiary (LOW confidence — flagged for validation)
- BTCUSDT perpetual futures `LOT_SIZE.stepSize = 0.001 BTC` as a Binance-published constant
  (rather than merely the measured gcd of realized fills, which IS high-confidence) — general
  knowledge, not independently re-verified against the live API this session

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies, everything already pinned and verified present
- Architecture: MEDIUM — the architecture is sound EXCEPT for Critical Finding 1, which is a
  real, unresolved conflict between two locked decisions that must be settled before task-writing
- Pitfalls: HIGH — every pitfall listed here was caught by directly measuring real data, not by
  pattern-matching training knowledge

**Research date:** 2026-09-23
**Valid until:** the lake's feature-tier schema changes (would invalidate Finding 1's premise)
or a held-out/simulated window includes a day where BTC trades above $100,000 (would activate
Finding 2) — otherwise treat as valid through Phase 6's execution; the underlying data (a fixed
historical day) does not go stale the way a live API measurement would.
