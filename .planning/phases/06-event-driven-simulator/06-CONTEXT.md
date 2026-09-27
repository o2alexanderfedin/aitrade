---
phase: 6
phase_name: Event-Driven Simulator
created: 2026-09-23
mode: smart discuss (autonomous run; four grey areas proposed as tables, the user accepted every recommended answer including the quantised threshold comparison)
amended: 2026-09-23 after 06-RESEARCH.md found that the feature tier never stored bid/ask -- the user chose the schema migration over a curated-tier adapter (D-06-17..20)
---

# Phase 6 Context: Event-Driven Simulator

<domain>
A verified-correct simulator whose policy math is proven against oracles before any model
output touches it. Deliverables (SIM-01..03): a numba event-driven kernel running taker-only,
zero-fee, zero-latency, $100-max-notional, flip-only logic as a SEQUENTIAL scan over decision
rows; an oracle suite that reproduces hand-computed P&L exactly and bounds the achievable
ceiling with a perfect-foresight run; integer-tick price accounting in the hot path with
CI-enforced bit-identical P&L across double runs.

Not in this phase: any model or prediction of its own (Phase 7 produces the first real
predictions — this phase takes a prediction array as a parameter), the threshold sweep over
X_bps (Phase 9 fits it), reporting suites and Sharpe statistics (EVAL-05, Phase 9), fees,
latency, maker orders or queue position (post-MVP simplifications, mvp.md Q2). This phase
produces the execution engine and proves its arithmetic.
</domain>

<canonical_refs>
- `mvp/spec.md` "Decision rule (Stage 2)" — THE authoritative rule (SPEC-03). It supersedes
  `mvp.md`'s struck "Decision logic (explicit)" block, which multiplied a predicted RETURN by
  the midprice and compared that price CHANGE against a price LEVEL, producing wrong signs
  except by coincidence. The oracle tests re-derive P&L from the spec rule, never from mvp.md's.
- `mvp/mvp.md` lines 44-47 — taker-only; entry when predicted mid crosses TOB by X bps; exit
  only by opposite-direction flip; no time-based exit; max position $100 notional; no
  risk-increasing order from a non-zero position.
- `mvp/spec.md` line 68 — decision rows are the last row of each `etime` group; the simulator
  decides on exactly those rows, the same ones Stage 1 trains and infers on.
- `mvp/features/tier.py` — `FEATURE_ROW_SCHEMA`: `etime` Int64, `decision_source_rank` Int8,
  `decision_seq` Int64, four Float64 features, four Float64 labels, `warmup`,
  `post_gap_warmup`, `schema_version`.
- `mvp/harness/accessor.py:materialize` — the only sanctioned path to segment rows; its frame
  is what the simulator's Python wrapper accepts.
- `mvp/features/kernel.py` + `mvp/features/reference.py` — the Phase 4 pattern this phase
  copies: one `@njit(cache=True)` kernel, a pure-Python twin, an equivalence test.
- `mvp/data/time_ns.py` — `QTY_SCALE`, `TRADE_FLOW_WINDOW_NS`, `LABEL_HORIZON_NS`; the one
  place scaling constants live.
- `mvp/tools/check_numba_globals.py` — every `@njit` input is an explicit parameter; only
  UPPER_CASE compile-time constants may be read from module scope.
</canonical_refs>

<data_reality>
Measured on the real lake 2026-09-23 (not read from summaries):

- **The tick is exactly 0.1 USDT.** The gcd of every positive price difference across the first
  2,000,000 rows of `curated/symbol=BTCUSDT/stream=bookTicker/date=2026-09-13` is 10,000,000 in
  1e-8 units. 1,552 distinct prices in that window.
- **Curated prices are `Float64`, and the tick is NOT exact in them.** The minimum observed
  spread reads `0.09999999999126885` and the median `0.10000000000582077` — one tick, twice,
  differing in the 12th decimal. This is the concrete case for SIM-03: a float accumulator over
  millions of decision rows drifts, and the drift is indistinguishable from P&L.
- Feature tier: 7 built days (2026-09-12..18), 60,926,503 decision rows. The simulator is
  tested on `tmp_path` fixtures in this phase; it touches no real segment and spends no look.
- The capture daemon has been stopped since the 2026-09-19 host reboot (user declined a
  relaunch), so the pool is fixed at those 7 days for this phase.
</data_reality>

<decisions>

## Area 1 — What the simulator consumes, and where it lives

### D-06-01 — The kernel takes bare numpy arrays; a thin wrapper does the conversion
`@njit` sees `etime` (int64), `bid_ticks`/`ask_ticks` (int64), `pred` (float64) and scalars —
never a DataFrame. `mvp/sim/arrays.py` (or the wrapper module) performs the polars→numpy
conversion and asserts `null_count() == 0` per column first, exactly as
`features/event_stream.py:event_arrays` does: polars' `.to_numpy()` on a Float64 column WITH
nulls returns a NaN-filled copy and does NOT raise (re-measured in Phase 4), so the assertion is
the control, not the convention.

### D-06-02 — New package `mvp/sim/`, tests in `mvp/tests/sim/` with NO `__init__.py`
Checked 2026-09-23: neither name exists today. The no-`__init__.py` rule is four instances old
(tests/spec, tests/tools, tests/features, tests/harness) — a test package shadowing a real one
is a checklist item, not a judgement call.

### D-06-03 — The simulator accepts the harness accessor's frame; it never reads the lake itself
One path to segment rows, so a simulated run and a training run cannot diverge on what a row is.
In Phase 6 every test builds its own `tmp_path` fixtures; no real segment is materialized and no
selection-bias look is spent. Wiring a real run through the accessor (and thus counting a look)
is Phase 7/9's step.

### D-06-04 — Predictions are a parameter, not something this phase produces
No model exists before Phase 7. The kernel takes a `pred` array aligned row-for-row with the
decision rows; Phase 6 fills it from the oracles in D-06-09.

## Area 2 — Integer-tick accounting (SIM-03)

### D-06-05 — One declared tick constant, pinned to the measurement
`TICK_SIZE_SCALED = 10_000_000` (0.1 USDT in 1e-8 units), declared once, with a test that
re-derives the gcd from a real curated partition and asserts equality — so a venue tick change
breaks CI instead of silently rescaling P&L. Float price → tick conversion uses an explicit,
named rounding rule, with a round-trip proof asserted on every converted row
(`abs(ticks * tick - price) < half a tick`), because the stored floats are not exact multiples.

### D-06-06 — Accumulation is integer; position, cash and P&L never touch float
P&L accrues as (tick difference) × (scaled quantity) in int64. Exactness is then a property of
the arithmetic, not of a tolerance — and the bit-identical double-run requirement (SIM-03) holds
by construction rather than by luck. The test asserts it anyway.

### D-06-07 — The threshold comparison is quantised to ticks too
`pred_mid` and `X_price` are converted to ticks before the comparison, so the entire decision is
integer. This removes the class of "the prediction sat 1e-13 above the ask" divergences between
machines and between runs. The cost, stated openly: a prediction 0.4 ticks above the ask no
longer triggers an entry — the rule becomes "crosses by at least one tick beyond the threshold".
Documented in the kernel docstring and in the oracle that pins it.

### D-06-08 — Position size comes from the $100 notional cap at entry price
Quantity = floor(max_notional / entry_price) in scaled units, rounded down to the venue lot step
(measured, like the tick, not assumed). A flip closes the existing position and opens the
opposite one in a single transition at the same decision row. Position stays in {-1, 0, +1}
lots of that size; no same-direction or risk-increasing order is ever placed.

## Area 3 — Oracles (SIM-02)

### D-06-09 — Four oracles, each catching something the others cannot
1. **Hand-computed scenario** — a short fixed event sequence whose P&L is worked out by hand in
   the test docstring, including at least one flip and one no-trade row.
2. **Perfect foresight** — `pred = mid * (1 + ret_10s_mid)` from the label column, which is by
   construction the best possible prediction at that horizon. It bounds the achievable ceiling
   and is the reference every future model is measured against. Null-labelled rows are excluded.
3. **Zero prediction** — `pred = mid` exactly: zero trades, zero P&L, asserted as counts.
4. **Pure-Python twin** — the same rule written straight, asserted equal to the kernel on random
   sequences (the `features/reference.py` pattern).

### D-06-10 — Anti-vacuity: a simulator that never trades passes everything
Every oracle asserts a TRADE COUNT, and the perfect-foresight run must earn materially more than
zero. Without that, an accidentally inert kernel is green on the whole suite.

### D-06-11 — Path dependence is proven, not asserted
A test that permutes the input rows and requires the result to CHANGE. That is what "sequential
scan, no vectorization" means operationally; a vectorized reimplementation would pass a
same-order equality test and fail this one.

### D-06-12 — The flip-only invariant is a property test
Over random sequences: position ∈ {-1, 0, +1}, every transition from a non-zero position is
either flat or the opposite sign, and no order ever increases risk. Asserted on the trade log,
not on internal state, so the log is proven to be what happened.

## Area 4 — Outputs and determinism

### D-06-13 — A run returns a trade log, an equity curve and counters
Trade log: decision `etime`, side, price in ticks, quantity, resulting position. Equity curve:
one value per decision row. Counters: trades, flips, rows in market. This is exactly what
EVAL-05 (Phase 9) will need and nothing more — no statistics are computed here.

### D-06-14 — Bit-identical across runs AND across processes
The CI test runs the same simulation twice in one process and once in a fresh subprocess, and
compares the sha256 of the serialized outputs. A fresh process is the half that catches numba
cache state and module-level mutation.

### D-06-15 — The simulator refuses nulls; it does not re-implement admission
Rows arrive already filtered by the harness (stale-book exclusion, errata masking). The
simulator raises on a null in any column it needs, naming the column and the row count — it
never silently skips a row, which is how a P&L quietly becomes a P&L of a different strategy.

### D-06-16 — Zero fees and zero latency are parameters defaulting to zero
The MVP simplification stays exactly zero, but as named parameters with zero defaults, so
removing the simplification post-MVP (mvp.md Q2 lists taker fees first) is a call-site change
rather than a rewrite of the kernel.

</decisions>

## Area 5 — The schema migration the research forced (added 2026-09-23, after 06-RESEARCH.md)

### D-06-17 — The feature tier gains `bid_price` and `ask_price` as BOOKKEEPING columns
The authoritative decision rule compares a predicted mid against the best ask and the best bid;
`FEATURE_ROW_SCHEMA` has only `mid`. Reconstructing the quotes as `mid ∓ half a tick` is wrong on
2.5% of rows (measured: the spread is wider than one tick that often), and fabricating prices is
the exact failure class this project exists to prevent. So the tier stores what the rule needs.
They are BOOKKEEPING columns (`BOOKKEEPING_COLUMNS`), not catalogued features: they are raw
observed state, like `etime` and `decision_seq`, not a derived quantity with an information set —
`features.toml` stays untouched and `check_spec_diff` sees no catalogue change.
The kernel already carries `state.prev_bid_price` / `prev_ask_price` (that is where `mid` comes
from), so emitting them is two more output arrays, not new arithmetic. `features/reference.py`'s
twin gains the same two outputs and the existing equivalence test covers them.

### D-06-18 — `FEATURE_SCHEMA_VERSION` 1 → 2, and v2 partitions live beside v1, never on top
Committed manifests are write-once and `write_feature_partition` refuses a `date=` directory that
already holds a part file — by design. The rebuild therefore does NOT touch v1: v2 partitions get
their own part-file identity (a schema-scoped name or path level, the planner picks the mechanism)
so every v1 manifest keeps resolving to its own bytes, `check_no_manifest_rewrite --full` stays
green, and Phase 5's committed segment manifest — which names v1 feature manifest ids — remains
valid and readable. A new segment manifest over v2 is Phase 7's call, not this phase's.

### D-06-19 — The rebuild is its own regression proof
The v2 build runs the CURRENT label code, whose staleness fix (`null_stale`) landed after the v1
partitions were written. So v2's labels must differ from v1's in EXACTLY the 249 errata cells
(180 `ret_1s_mid`, 69 `ret_10s_mid`, all previously exactly 0.0, all on 2026-09-12/13) and nowhere
else. That comparison is a required acceptance criterion of the rebuild: it proves the migration
changed only what it was supposed to change, and it independently re-confirms the errata list from
a completely different direction. Any other differing cell STOPS the rebuild.
On the four days built after the fix (2026-09-15..18) the label columns must be bit-identical.

### D-06-20 — The $100 cap's dead zone is refused loudly, not traded silently
`floor(max_notional / price / lot_step) * lot_step` is zero for any BTC price above $100,000 at
the measured 0.001 BTC lot step. Today's median of $77,061 yields exactly one lot (~$77), so the
policy is non-vacuous now, but the boundary is real and the spec never mentions it. The kernel
RAISES when the cap yields zero lots, naming the price and the cap — it never silently produces a
run with no trades, which is indistinguishable from a strategy that found no signal.
`max_notional` and `lot_step` are parameters; the MVP defaults stay $100 and the measured step.

<code_context>
## Existing Code Insights

### Reusable assets
- `features/kernel.py` / `features/reference.py` — kernel + twin + equivalence test, including
  the ring buffer and the int64 accumulator lesson (float64 changed 95% of rows under a tie-order
  change; int64 made it order-independent)
- `features/event_stream.py:event_arrays` — the polars→numpy boundary with null assertions and
  read-only views
- `harness/accessor.py:materialize` — the sanctioned frame source
- `data/time_ns.py` — `QTY_SCALE` (1e8) and the single seconds→ns site
- `tests/fixtures/feature_build.py` — the real-span synthetic generator (NOT
  `feature_tier.py:feature_frame`, whose span is microseconds)

### Established patterns
- `@njit` functions take every input as an explicit parameter (`check_numba_globals`)
- `NUMBA_CACHE_DIR` is pinned by `tests/conftest.py`; any ad-hoc script must export it
- Every measured statistic names its row set; every invariance test has an anti-vacuity twin
- Guardrail commands byte-identical in `.pre-commit-config.yaml` and `.github/workflows/ci.yml`
- Mutation checks print the changed block and assert the file hash changed

### Integration points
- `mvp/sim/` (new) consuming `harness.accessor.materialize`'s frame
- `mvp/spec.md` gains a "Simulator" section (tick constant, quantised comparison, the four
  oracles, the outputs) — the PR-touches-spec rule applies; `check_spec_diff` watches only the
  catalogue tables, so this is a manual discipline with no CI backstop
- Possibly a 20th guardrail if a static check earns its place — not assumed
</code_context>

<specifics>
## Specific Ideas
- The perfect-foresight oracle is the most valuable artifact in the phase: it is the number every
  model in Phases 7–8 is compared against, and it costs one label column.
- The hand-computed scenario's arithmetic belongs in the test docstring in full, so a reader can
  check it without running anything.
</specifics>

<deferred>
## Deferred Ideas
- Taker fees, larger position size, non-zero latency, maker orders, L1-aware fills and queue
  position — mvp.md's post-MVP queue; fees are first in line
- The X_bps sweep and its robust-region check — Phase 9
- Sharpe, equity-curve plots, alpha decay, trade-log diagnostics — EVAL-05, Phase 9
- RL as a replacement for the threshold policy — mvp.md Q4, past MVP
- Capture daemon relaunch and moving capture to an always-on host — operational, user's call
</deferred>
