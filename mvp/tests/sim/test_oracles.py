"""D-06-09 #1 and #3, D-06-10: the hand-computed scenario oracle and the
zero-prediction oracle, each asserting an EXACT trade count (D-06-10's own
loophole-closing rule -- "every oracle asserts a TRADE COUNT", never
merely `>= 0` or unchecked). `06-RESEARCH.md` Q6 is this file's structural
(not merely empirical) argument for why `pred = mid` never trades.

D-06-09 #4 (the pure-Python-twin oracle) lives in
`tests/sim/test_kernel.py::test_kernel_and_reference_agree_on_hypothesis_
generated_sequences` (Plan 06-03) -- not duplicated here.
"""

from __future__ import annotations

import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sim.kernel import run_sim_checked
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED


def _price_at_ticks(ticks: float) -> float:
    """A raw price (the same scale `pred` arrives in) -- NOT
    `sim.ticks.price_to_ticks`'s inverse. Duplicated locally per this
    project's own per-file hypothesis-fixture convention (`tests/sim/` has
    no `__init__.py`)."""
    return ticks * TICK_SIZE_SCALED / PRICE_SCALE


def _closed_pnl_ticks(result) -> int:
    """The tick-only closed-P&L sum `06-RESEARCH.md` Q3-Q5 mean by
    "closed_pnl_ticks" -- see `test_path_dependence.py`'s own copy of this
    helper for the full derivation and why it is NOT
    `sim.kernel`'s `realized_pnl_scaled` accumulator."""
    k = result.fill_count
    prices = result.trade_log["price_ticks"][:k]
    positions = result.trade_log["position_after"][:k]
    return sum(
        int(positions[j - 1]) * (int(prices[j]) - int(prices[j - 1]))
        for j in range(1, k)
    )


# --------------------------------------------------------------------------
# D-06-09 #1: the hand-computed scenario oracle
# --------------------------------------------------------------------------


def test_hand_computed_scenario_oracle():
    """D-06-09 #1's own wording: "a short fixed event sequence whose P&L
    is worked out by hand in the test docstring, including at least one
    flip and one no-trade row." This is the SAME `06-RESEARCH.md` Q7
    four-row fixture `test_path_dependence.py::
    test_q7_adjacent_swap_changes_trade_count_and_pnl` uses for a
    DIFFERENT purpose (proving path dependence via the swap) --
    `06-RESEARCH.md`'s own design treats this as a legitimate reuse, not a
    redundancy to remove (this plan's own `<behavior>` text says so
    explicitly).

    `X_bps = 0`. Rows 1-3 include a flip (rows 2 and 3 are both flips);
    row 4 is the no-trade row (`pred_ticks=100`: `100 !> ask_ticks=101`
    and `100 !< bid_ticks=100`):

        row1: ask=101 bid=100 pred=105 -- flat, long_trigger (105>101) ->
              enter long @ ask=101.                             trades=1
        row2: ask=103 bid=102 pred=95  -- long, short_trigger (95<102) ->
              FLIP: close long (+1*(102-101)=+1 tick), open short @102.
                                                    trades=2 flips=1 pnl=1
        row3: ask=99  bid=98  pred=105 -- short, long_trigger (105>99) ->
              FLIP: close short (-1*(99-102)=+3 ticks), open long @99.
                                                    trades=3 flips=2 pnl=4
        row4: ask=101 bid=100 pred=100 -- long, neutral -> no trade (the
              named no-trade row).
        FINAL: trades=3 flips=2 closed_pnl_ticks=4 (tick-only sum, see
        this module's `_closed_pnl_ticks`), open position long@99
        unrealized (not counted).
    """
    etime = np.array([1, 2, 3, 4], dtype=np.int64)
    ask_ticks = np.array([101, 103, 99, 101], dtype=np.int64)
    bid_ticks = np.array([100, 102, 98, 100], dtype=np.int64)
    pred = np.array([_price_at_ticks(t) for t in (105, 95, 105, 100)], dtype=np.float64)

    result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)

    assert result.counters["trades"] == 3
    assert result.counters["flips"] == 2
    assert _closed_pnl_ticks(result) == 4
    assert result.counters["trades"] > 0  # anti-vacuity (D-06-10)


# --------------------------------------------------------------------------
# D-06-09 #3: the zero-prediction oracle, structural not just empirical
# --------------------------------------------------------------------------


def test_zero_prediction_oracle_is_structurally_flat():
    """`pred[i] = (bid_ticks[i] + ask_ticks[i]) * TICK_SIZE_SCALED /
    (2 * PRICE_SCALE)` -- the exact midpoint expressed in RAW PRICE UNITS,
    the same scale as `bid_price`/`ask_price`, which is what
    `sim.kernel.run_sim`'s `s = round(pred[i] * PRICE_SCALE)` expects.
    Writing the midpoint in TICK units instead
    (`(bid_ticks+ask_ticks)/2`, with no `TICK_SIZE_SCALED`/`PRICE_SCALE`
    conversion) is the checker-iteration-2 blocker this plan's own
    `read_first` note warns about: at a 0.1 tick that value is ~10x the
    ask, and the "never trades" oracle trades on every row instead.

    ONE WORKED VALUE, to show the unit conversion produces zero trades:
    `bid_ticks=100` ($10.00), `ask_ticks=101` ($10.10) -- the tightest,
    ODD-spread (1 tick) case, the exact tie the structural argument below
    must survive. `pred = (100+101) * 10_000_000 / (2 * 100_000_000)
    = $10.05`. `s = round(10.05 * 100_000_000) = 1_005_000_000`.
    `pred_ticks = (1_005_000_000 + 5_000_000) // 10_000_000 = 101` -- the
    NEAREST-TICK rounding rule (D-06-07) rounds this tie UP to
    `ask_ticks`, not down. `long_trigger`: `101 > 101 + 0` is FALSE (the
    STRICT `>` is what saves it -- `>=` would trigger here, see this
    plan's own `>=`-mutation check, transcribed in 06-06-SUMMARY.md).
    `short_trigger`: `101 < 100 - 0` is FALSE. Zero trades on this row.

    STRUCTURAL, not a fixture accident (Q6's own distinction): because the
    comparison is STRICT (`>`/`<`, never `>=`/`<=`), and `pred_ticks`
    (nearest-tick-rounded mid) can NEVER exceed `ask_ticks` or fall below
    `bid_ticks` regardless of which way a tie rounds -- the true mid sits
    STRICTLY between `bid` and `ask` whenever `spread > 0`, so its nearest
    tick is at most `ask_ticks` (when the tie rounds up, as the ODD-spread
    case above demonstrates) or at least `bid_ticks` (when it rounds
    down), never strictly beyond either -- zero trades is GUARANTEED BY
    CONSTRUCTION, not an artifact of this one fixture. A hypothesis sweep
    below confirms this holds on every generated example with `spread >
    0` (including odd spreads, which land the tie exactly on a half-tick),
    not just the fixed fixture above.
    """
    etime = np.array([1], dtype=np.int64)
    bid_ticks = np.array([100], dtype=np.int64)
    ask_ticks = np.array([101], dtype=np.int64)
    mid_price = (100 + 101) * TICK_SIZE_SCALED / (2 * PRICE_SCALE)
    assert mid_price == 10.05  # the worked value transcribed above
    pred = np.array([mid_price], dtype=np.float64)

    # BEFORE changing any comparison: if this reports trades, the FIRST
    # diagnostic is a unit-scale error in the fixture, not the
    # inequality -- print pred alongside bid/ask in the SAME raw-price
    # units and confirm pred lies strictly between them.
    bid_price = bid_ticks[0] * TICK_SIZE_SCALED / PRICE_SCALE
    ask_price = ask_ticks[0] * TICK_SIZE_SCALED / PRICE_SCALE
    print(f"pred={pred[0]!r} bid_price={bid_price!r} ask_price={ask_price!r}")
    assert bid_price < pred[0] < ask_price

    result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)
    assert result.counters["trades"] == 0

    # Multi-row fixture: a longer sequence with varying spread, never
    # crossed/locked, still structurally flat.
    etimes = np.arange(1, 11, dtype=np.int64)
    bids = np.array([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000], dtype=np.int64)
    spreads = np.array([1, 2, 3, 1, 5, 1, 7, 1, 9, 1], dtype=np.int64)
    asks = bids + spreads
    mids = (
        (bids.astype(np.float64) + asks.astype(np.float64))
        * TICK_SIZE_SCALED
        / (2 * PRICE_SCALE)
    )
    multi_result = run_sim_checked(etimes, bids, asks, mids, x_bps=0)
    assert multi_result.counters["trades"] == 0


@settings(deadline=None, max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(
    seq=st.lists(
        st.tuples(
            st.integers(min_value=100_000, max_value=900_000),  # bid_ticks
            st.integers(min_value=1, max_value=50),  # spread, always > 0
        ),
        min_size=1,
        max_size=100,
    )
)
def test_hypothesis_zero_prediction_is_flat_over_random_spreads(seq):
    """The structural argument above, confirmed on every generated
    example -- never just the one fixed fixture. `spread` is always
    `>= 1` (never crossed/locked, D-06-15's own precondition), including
    ODD values (the tie-rounds-up case)."""
    n = len(seq)
    etime = np.arange(1, n + 1, dtype=np.int64)
    bid_ticks = np.array([b for b, _ in seq], dtype=np.int64)
    spread = np.array([s for _, s in seq], dtype=np.int64)
    ask_ticks = bid_ticks + spread
    pred = (
        (bid_ticks.astype(np.float64) + ask_ticks.astype(np.float64))
        * TICK_SIZE_SCALED
        / (2 * PRICE_SCALE)
    )

    result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)
    assert result.counters["trades"] == 0


# --------------------------------------------------------------------------
# D-06-10's own loophole, closed explicitly
# --------------------------------------------------------------------------


def test_anti_vacuity_every_oracle_names_a_trade_count():
    """A small meta-test: each oracle above returns a `SimResult` whose
    `counters["trades"]` is checked against an EXACT expected value --
    never merely `>= 0` or unchecked (D-06-10's own wording, "every oracle
    asserts a TRADE COUNT")."""
    etime = np.array([1, 2, 3, 4], dtype=np.int64)
    ask_ticks = np.array([101, 103, 99, 101], dtype=np.int64)
    bid_ticks = np.array([100, 102, 98, 100], dtype=np.int64)
    pred = np.array([_price_at_ticks(t) for t in (105, 95, 105, 100)], dtype=np.float64)
    hand_computed = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)
    assert hand_computed.counters["trades"] == 3

    zp_etime = np.array([1], dtype=np.int64)
    zp_bid = np.array([100], dtype=np.int64)
    zp_ask = np.array([101], dtype=np.int64)
    zp_pred = np.array(
        [(100 + 101) * TICK_SIZE_SCALED / (2 * PRICE_SCALE)], dtype=np.float64
    )
    zero_prediction = run_sim_checked(zp_etime, zp_bid, zp_ask, zp_pred, x_bps=0)
    assert zero_prediction.counters["trades"] == 0
