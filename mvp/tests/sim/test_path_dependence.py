"""D-06-11: path dependence is PROVEN, not asserted -- a test that permutes
the input rows and requires the result to CHANGE. This is what "sequential
scan, no vectorization" (SIM-01) means as a runtime property: a vectorized
reimplementation computes each row's contribution independently of its
neighbours and would therefore be INSENSITIVE to an adjacent-row swap that
straddles a trigger. `06-RESEARCH.md` Q7 is this file's ready-to-use
worked example; its own text supplies the argument for why an
order-independent kernel would pass a same-order equality test and fail
this one.

Written against Plan 06-03's already-existing `sim.kernel.run_sim_checked`
(wave ordering: that dependency is already satisfied) -- "fails first"
here is 06-06-PLAN.md Task 1's own TDD discipline applied to a fixture
whose right answer is already known by hand: the WRONG numbers were
asserted first, observed failing, then corrected to the numbers this
file's own docstrings transcribe (re-derived independently below, not
copied blind from the plan's own transcription of Q7 -- see
`test_q7_adjacent_swap_changes_trade_count_and_pnl`'s docstring).

CLOSED P&L, IN THIS FILE, MEANS THE TICK-ONLY SUM Q3/Q4/Q5 OF
06-RESEARCH.md REPORT -- `Sum_{j=1}^{k-1} position_after[j-1] *
(price_ticks[j] - price_ticks[j-1])` over the trade log sliced to
`[:fill_count]` -- NOT `sim.kernel`'s own `realized_pnl_scaled`
accumulator. The two are related but NOT interchangeable: `kernel.py`'s
module docstring states `realized_pnl_scaled` accumulates `(price_ticks
diff) * qty_scaled`, and Q7's fixture prices sit near $10 (not the real
day's ~$77,000), so `qty_scaled` varies materially per fill
(measured directly, not assumed: 990,000,000 / 980,300,000 / 1,010,100,000
at $10.10 / $10.20 / $9.90 respectively) -- `realized_pnl_scaled` for the
Q7 fixture is therefore 3,930,900,000, NOT `4 * qty` for any single `qty`.
`_closed_pnl_ticks` below is the tick-only sum this file (and
`06-RESEARCH.md`) means by "closed_pnl_ticks"; a separate assertion in
this file's Q7 test additionally pins `realized_pnl_scaled` to the
qty-weighted form of the SAME sum, so the two derivations agree with each
other, not just with the plan's transcribed numbers.
"""

from __future__ import annotations

import numpy as np
from hypothesis import HealthCheck, assume, find, given, settings
from hypothesis import strategies as st

from sim.kernel import STATE_I64_SLOTS, new_state, run_sim_checked
from sim.outputs import SimResult
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED, position_size_ticks


def _price_at_ticks(ticks: float) -> float:
    """A raw price (the same scale `pred` arrives in), at a given tick
    count -- NOT `sim.ticks.price_to_ticks`'s inverse (that function is
    for bid/ask; see `sim/kernel.py`'s module docstring for why a model
    prediction needs a different rule)."""
    return ticks * TICK_SIZE_SCALED / PRICE_SCALE


def _closed_pnl_ticks(result: SimResult) -> int:
    """The tick-only closed-P&L sum this file (and `06-RESEARCH.md`
    Q3-Q5) mean by "closed_pnl_ticks": walk the trade log sliced to
    `[:fill_count]` and sum, for every row after the first, the PREVIOUS
    row's `position_after` times the tick move from the previous row's
    fill price to this row's fill price. This is exact under the
    flip-only rule because every trade-log row after the first CLOSES the
    previous row's leg at the previous row's price -- there is no
    same-direction re-entry to complicate the walk (D-06-08, proven
    separately by `tests/sim/test_flip_invariant.py`)."""
    k = result.fill_count
    prices = result.trade_log["price_ticks"][:k]
    positions = result.trade_log["position_after"][:k]
    return sum(
        int(positions[j - 1]) * (int(prices[j]) - int(prices[j - 1]))
        for j in range(1, k)
    )


def _qty_weighted_realized_pnl(result: SimResult) -> int:
    """The SAME walk as `_closed_pnl_ticks`, but weighted by the CLOSED
    leg's own quantity at each step -- this is what `sim.kernel.run_sim`'s
    `realized_pnl_scaled` accumulator actually computes (see this file's
    own module docstring). Used only to cross-check the kernel's internal
    accumulator against the trade log it wrote, never as a substitute for
    `_closed_pnl_ticks` itself."""
    k = result.fill_count
    prices = result.trade_log["price_ticks"][:k]
    positions = result.trade_log["position_after"][:k]
    qtys = result.trade_log["qty_scaled"][:k]
    return sum(
        int(positions[j - 1]) * (int(prices[j]) - int(prices[j - 1])) * int(qtys[j - 1])
        for j in range(1, k)
    )


def _q7_original_order():
    etime = np.array([1, 2, 3, 4], dtype=np.int64)
    ask_ticks = np.array([101, 103, 99, 101], dtype=np.int64)
    bid_ticks = np.array([100, 102, 98, 100], dtype=np.int64)
    pred_ticks = [105, 95, 105, 100]
    pred = np.array([_price_at_ticks(t) for t in pred_ticks], dtype=np.float64)
    return etime, bid_ticks, ask_ticks, pred


def _q7_swapped_order():
    """`etime` unchanged; only the `(ask, bid, pred)` PAYLOAD at positions
    2 and 3 (1-indexed in 06-RESEARCH.md's own table, i.e. array indices 1
    and 2) is exchanged -- 06-RESEARCH.md Q7's own perturbation."""
    etime = np.array([1, 2, 3, 4], dtype=np.int64)
    ask_ticks = np.array([101, 99, 103, 101], dtype=np.int64)
    bid_ticks = np.array([100, 98, 102, 100], dtype=np.int64)
    pred_ticks = [105, 105, 95, 100]
    pred = np.array([_price_at_ticks(t) for t in pred_ticks], dtype=np.float64)
    return etime, bid_ticks, ask_ticks, pred


def test_q7_adjacent_swap_changes_trade_count_and_pnl():
    """06-RESEARCH.md Q7's four-row fixture, `X_bps=0`, walked by hand
    below and RE-VERIFIED independently against the running kernel before
    being transcribed here (never taken on faith from the plan's own
    transcription of Q7, per this plan's own STOP-and-re-derive
    instruction):

    ORIGINAL ORDER (ask_ticks / bid_ticks / pred_ticks per row):
        row1: 101 / 100 / 105 -- flat, long_trigger (105 > 101) ->
              enter long @ ask=101. qty = floor($100 / $10.10 / 0.001
              BTC) * 0.001 = 9.900 BTC = 990,000,000 scaled units.
              trades=1
        row2: 103 / 102 / 95  -- long, short_trigger (95 < 102) -> FLIP:
              close long (+1 * (102-101) * 990,000,000 = +990,000,000),
              open short @ bid=102, qty = floor($100/$10.20/0.001)*0.001
              = 9.803 BTC = 980,300,000 scaled units.
              trades=2 flips=1  closed_pnl_ticks so far = 1
        row3: 99 / 98 / 105   -- short, long_trigger (105 > 99) -> FLIP:
              close short (-1 * (99-102) * 980,300,000 = +2,940,900,000),
              open long @ ask=99, qty = floor($100/$9.90/0.001)*0.001 =
              10.101 BTC = 1,010,100,000 scaled units.
              trades=3 flips=2  closed_pnl_ticks so far = 1 + 3 = 4
        row4: 101 / 100 / 100 -- long, neutral (100 !> 101, 100 !< 100):
              no trade.
        FINAL: trades=3 flips=2 closed_pnl_ticks=4 (tick-only sum, see
        this module's own docstring), open position long@99 unrealized
        (not counted). `realized_pnl_scaled` (the kernel's own qty-weighted
        accumulator) = 990,000,000 + 2,940,900,000 = 3,930,900,000 --
        this is NOT `4 * some single qty`, because qty varies per fill at
        these ~$10 prices (see module docstring); it equals the
        qty-weighted walk of the SAME trade log, cross-checked below.

    SWAPPED ORDER (rows 2 and 3's `(ask,bid,pred)` payload exchanged,
    `etime` unchanged):
        row1: 101 / 100 / 105 -- flat, long_trigger -> enter long @ 101,
              qty=990,000,000.                                trades=1
        row2 (was row3's payload: 99/98/105) -- long, short_trigger?
              105 < 98? NO -> no trade, stays long @101.
        row3 (was row2's payload: 103/102/95) -- long, short_trigger?
              95 < 102? YES -> FLIP: close long (+1*(102-101)*990,000,000
              = +990,000,000), open short @ bid=102, qty=980,300,000.
                                                        trades=2 flips=1
        row4: 101 / 100 / 100 -- short, neutral (100 !> 101) -> no trade.
        FINAL: trades=2 flips=1 closed_pnl_ticks=1, open position
        short@102 (unrealized, not counted). `realized_pnl_scaled` =
        990,000,000.

    RESULT: trade count 3->2, closed_pnl_ticks 4->1 -- a single
    adjacent-row swap changes BOTH the count and the value under the
    correct sequential implementation. Anti-vacuity: both orders trade at
    least once (3 and 2 respectively), so this cannot pass by both sides
    trivially producing zero trades (D-06-10).
    """
    orig_etime, orig_bid, orig_ask, orig_pred = _q7_original_order()
    orig_state = new_state()
    original = run_sim_checked(
        orig_etime, orig_bid, orig_ask, orig_pred, x_bps=0, state=orig_state
    )

    assert original.counters["trades"] == 3
    assert original.counters["flips"] == 2
    assert _closed_pnl_ticks(original) == 4
    assert original.counters["trades"] > 0  # anti-vacuity (D-06-10)

    # The kernel's own accumulator, pinned to the qty-weighted walk of the
    # SAME trade log it wrote -- proves realized_pnl_scaled is not a
    # decoupled number, and is what the flip-sizing mutation check below
    # actually bites on (the tick-only sum above is quantity-blind).
    assert (
        int(orig_state[STATE_I64_SLOTS["realized_pnl_scaled"]])
        == _qty_weighted_realized_pnl(original)
        == 3_930_900_000
    )
    for j in range(original.fill_count):
        price = int(original.trade_log["price_ticks"][j])
        assert int(original.trade_log["qty_scaled"][j]) == position_size_ticks(price), (
            f"row {j}: trade-log qty does not match a FRESH position_size_ticks "
            "at this row's own fill price (D-06-08/Q13) -- exactly what the "
            "flip-resizing mutation check breaks"
        )

    swap_etime, swap_bid, swap_ask, swap_pred = _q7_swapped_order()
    swap_state = new_state()
    swapped = run_sim_checked(
        swap_etime, swap_bid, swap_ask, swap_pred, x_bps=0, state=swap_state
    )

    assert swapped.counters["trades"] == 2
    assert swapped.counters["flips"] == 1
    assert _closed_pnl_ticks(swapped) == 1
    assert swapped.counters["trades"] > 0  # anti-vacuity (D-06-10)
    assert int(swap_state[STATE_I64_SLOTS["realized_pnl_scaled"]]) == 990_000_000
    for j in range(swapped.fill_count):
        price = int(swapped.trade_log["price_ticks"][j])
        assert int(swapped.trade_log["qty_scaled"][j]) == position_size_ticks(price)


@st.composite
def _decision_sequences(draw):
    """`(etime, bid_ticks, ask_ticks, pred)` decision-row arrays, length
    10-100 -- long enough that an adjacent pair straddling a trigger is
    reasonably likely, short enough to keep the per-example swap search
    (below, O(n) kernel re-runs) fast across 50 hypothesis examples.

    Mirrors `tests/sim/test_kernel.py`'s own `_decision_sequences`
    strategy in shape (not by import -- this project's convention,
    per `test_determinism.py`/`test_null_refusal.py`, is to duplicate
    small per-file hypothesis fixtures rather than share them across
    `tests/sim/`'s no-`__init__.py` files): `bid_ticks` confined to
    [100_000, 900_000] ($10,000-$90,000), comfortably under D-06-20's
    ~$100,000 zero-lot dead zone; `ask = bid + spread`, spread in
    [1, 50] ticks. `pred` is drawn as a RAW PRICE near the row's own mid,
    offset by a signed tick count in [-40, 40] -- NEVER as a bare integer
    in the tick range, which would make every row trigger and turn the
    straddle search into a tautology (this plan's own read_first note;
    the mistake this project caught twice while designing `sim/ticks.py`'s
    and `sim/kernel.py`'s own tests).
    """
    n = draw(st.integers(min_value=10, max_value=100))
    etimes: list[int] = []
    bids: list[int] = []
    asks: list[int] = []
    preds: list[float] = []
    t = draw(st.integers(min_value=1, max_value=1_000))
    bid = draw(st.integers(min_value=100_000, max_value=900_000))
    for _ in range(n):
        t += draw(st.integers(min_value=1, max_value=1_000))
        bid = min(
            900_000,
            max(100_000, bid + draw(st.integers(min_value=-500, max_value=500))),
        )
        spread = draw(st.integers(min_value=1, max_value=50))
        ask = bid + spread
        offset = draw(st.integers(min_value=-40, max_value=40))
        pred_ticks_target = (bid + ask) // 2 + offset
        etimes.append(t)
        bids.append(bid)
        asks.append(ask)
        preds.append(_price_at_ticks(pred_ticks_target))
    return (
        np.array(etimes, dtype=np.int64),
        np.array(bids, dtype=np.int64),
        np.array(asks, dtype=np.int64),
        np.array(preds, dtype=np.float64),
    )


def _first_straddling_swap(seq):
    """Search adjacent pairs of `seq` for one whose swap changes
    `(trade_count, closed_pnl_ticks)`. Returns `(index, swapped_result,
    swapped_closed_pnl)` for the FIRST straddling pair found, or `None` if
    every adjacent swap leaves the result unchanged (the vacuous case a
    hypothesis-generated example must be filtered out, not silently
    passed, per D-06-11/D-06-10)."""
    etime, bid_ticks, ask_ticks, pred = seq
    n = etime.shape[0]
    original = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)
    orig_signature = (original.fill_count, _closed_pnl_ticks(original))

    for i in range(n - 1):
        swapped_bid = bid_ticks.copy()
        swapped_ask = ask_ticks.copy()
        swapped_pred = pred.copy()
        swapped_bid[i], swapped_bid[i + 1] = swapped_bid[i + 1], swapped_bid[i]
        swapped_ask[i], swapped_ask[i + 1] = swapped_ask[i + 1], swapped_ask[i]
        swapped_pred[i], swapped_pred[i + 1] = swapped_pred[i + 1], swapped_pred[i]
        swapped_result = run_sim_checked(
            etime, swapped_bid, swapped_ask, swapped_pred, x_bps=0
        )
        swapped_signature = (
            swapped_result.fill_count,
            _closed_pnl_ticks(swapped_result),
        )
        if swapped_signature != orig_signature:
            return i, swapped_result, swapped_signature, orig_signature
    return None


@settings(deadline=None, max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(seq=_decision_sequences())
def test_hypothesis_random_adjacent_swap_that_straddles_a_trigger_changes_the_result(
    seq,
):
    found = _first_straddling_swap(seq)
    # No straddling pair in this generated example -- filter it out rather
    # than let the test pass vacuously (this plan's own explicit
    # instruction; NOT `HealthCheck.filter_too_much`-suppressed, so an
    # excessive filter rate surfaces as a hypothesis warning rather than
    # being silently hidden).
    assume(found is not None)

    i, swapped_result, swapped_signature, orig_signature = found
    assert swapped_signature != orig_signature, (
        f"adjacent swap at index {i} was supposed to straddle a trigger "
        f"but the signature (trades, closed_pnl_ticks) did not change: "
        f"{orig_signature}"
    )
    assert swapped_result.fill_count > 0 or orig_signature[0] > 0  # anti-vacuity


def test_the_strategy_can_generate_a_straddling_swap_example():
    """Anti-vacuity companion for the sweep above (D-06-10, mirroring
    `tests/sim/test_kernel.py::test_the_strategy_can_generate_a_trading_
    sequence`'s own `hypothesis.find` pattern): rather than merely hoping
    50 examples happen to include a straddling swap, `hypothesis.find`
    PROVES the strategy can produce one -- it raises `NoSuchExample`
    otherwise. A vectorized (order-independent) kernel would make this
    search return `None` on every example, so this test's PASS is itself
    evidence the running kernel is not vectorized, not merely that the
    strategy is well-formed."""
    seq = find(
        _decision_sequences(),
        lambda seq: _first_straddling_swap(seq) is not None,
    )
    assert _first_straddling_swap(seq) is not None
