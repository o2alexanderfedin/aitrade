"""Tests for `sim/arrays.py` and `sim/kernel.py` -- the polars->numpy
boundary (D-06-01) and the sequential, integer-only, flip-only `@njit`
state machine (D-06-06/07/08, SIM-01/03), per 06-03-PLAN.md.

Task 1 tests are written FIRST, against nothing: every one of them fails
with `ModuleNotFoundError: No module named 'sim.kernel'` (or `sim.arrays`)
before either module exists -- that is the RED this file's `tdd="true"`
task requires, observed in this session's tool transcript before any
implementation line was written.

Task 2 (the hypothesis equivalence sweep against `sim/reference.py`, and
`sim/outputs.py`'s `new_trade_log`/`SimResult` contract) is appended below
the Task 1 section, once `sim/kernel.py` returns a `SimResult` instead of
a plain dict -- see that section's own docstring for the interface change.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from hypothesis import HealthCheck, find, given, settings
from hypothesis import strategies as st

from sim.arrays import sim_arrays
from sim.kernel import (
    STATE_I64_SLOTS,
    STATUS_TRADE_LOG_OVERFLOW,
    SimStatusError,
    new_state,
    run_sim,
    run_sim_checked,
)
from sim.outputs import SimResult, new_trade_log
from sim.reference import run_reference_sim
from sim.ticks import (
    LOT_STEP_SCALED,
    MAX_NOTIONAL_SCALED,
    PRICE_SCALE,
    TICK_SIZE_SCALED,
    position_size_ticks,
    price_to_ticks,
)


def _price_at_ticks(ticks: float) -> float:
    """A raw price (the same scale `pred` arrives in) at a given tick
    count -- NOT `ticks.price_to_ticks`'s inverse (that function is for
    bid/ask, never for a model prediction; see `sim/kernel.py`'s module
    docstring). `ticks` may be fractional, to place a price a fraction of
    a tick off the grid on purpose (the symmetry test below)."""
    return ticks * TICK_SIZE_SCALED / PRICE_SCALE


# --------------------------------------------------------------------------
# Task 1: arrays.py boundary and the run_sim state machine
# --------------------------------------------------------------------------


def test_arrays_refuse_a_nullable_bid_or_ask_column():
    df = pl.DataFrame(
        {
            "etime": [1, 2, 3],
            "bid_price": [100.0, None, 100.2],
            "ask_price": [100.1, 100.2, 100.3],
        },
        schema={"etime": pl.Int64, "bid_price": pl.Float64, "ask_price": pl.Float64},
    )
    with pytest.raises(ValueError, match="bid_price"):
        sim_arrays(df)


def test_arrays_converts_bid_ask_to_ticks_via_price_to_ticks():
    bid = [77_000.1, 77_000.2, 77_050.3]
    ask = [77_000.2, 77_000.4, 77_050.5]
    df = pl.DataFrame(
        {"etime": [1, 2, 3], "bid_price": bid, "ask_price": ask},
        schema={"etime": pl.Int64, "bid_price": pl.Float64, "ask_price": pl.Float64},
    )

    out = sim_arrays(df)

    assert out["etime"].dtype == np.int64
    assert np.array_equal(out["etime"], np.array([1, 2, 3], dtype=np.int64))
    assert np.array_equal(out["bid_ticks"], price_to_ticks(np.array(bid)))
    assert np.array_equal(out["ask_ticks"], price_to_ticks(np.array(ask)))


def test_flat_to_long_entry_at_ask_plus_threshold():
    etime = np.array([1], dtype=np.int64)
    bid_ticks = np.array([100], dtype=np.int64)
    ask_ticks = np.array([101], dtype=np.int64)
    pred = np.array([_price_at_ticks(105)], dtype=np.float64)  # 105 > 101 + 0

    result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)

    assert result.fill_count == 1
    assert int(result.trade_log["price_ticks"][0]) == 101  # entry at the ask
    assert int(result.trade_log["position_after"][0]) == 1
    assert int(result.trade_log["side"][0]) == 1
    assert result.counters["trades"] == 1
    assert result.counters["flips"] == 0


def test_flip_long_to_short_closes_and_reopens_same_row():
    """Row 1: flat -> long entry @ ask=101. Row 2: the market has moved
    (bid=102, ask=103) and `pred` triggers the short side -- this resolves
    as ONE trade-log row for row 2 (a flip), sized fresh from row 2's
    bid=102 (Q13), never from row 1's entry price (ask=101)."""
    etime = np.array([1, 2], dtype=np.int64)
    bid_ticks = np.array([100, 102], dtype=np.int64)
    ask_ticks = np.array([101, 103], dtype=np.int64)
    pred = np.array(
        [_price_at_ticks(105), _price_at_ticks(95)], dtype=np.float64
    )  # row1: 105 > 101 (long); row2: 95 < 102 (short flip)

    result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)

    assert result.fill_count == 2  # row1's entry + row2's flip, not four
    assert result.counters["flips"] == 1
    assert int(result.trade_log["etime"][1]) == 2
    assert int(result.trade_log["position_after"][1]) == -1
    assert int(result.trade_log["side"][1]) == -1
    assert int(result.trade_log["price_ticks"][1]) == 102  # row2's bid

    expected_qty = position_size_ticks(102)
    wrong_qty = position_size_ticks(101)  # what the OLD entry price would size
    assert expected_qty != wrong_qty, "fixture is vacuous: both prices size identically"
    assert int(result.trade_log["qty_scaled"][1]) == expected_qty


def test_no_same_direction_or_risk_increasing_order():
    etime = np.array([1, 2], dtype=np.int64)
    bid_ticks = np.array([100, 100], dtype=np.int64)
    ask_ticks = np.array([101, 101], dtype=np.int64)
    pred = np.array(
        [_price_at_ticks(105), _price_at_ticks(110)], dtype=np.float64
    )  # both rows trigger long; already long by row 2

    result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)

    assert result.fill_count == 1  # only row 1's entry
    assert int(result.trade_log["position_after"][0]) == 1
    assert result.counters["trades"] == 1


def test_quantised_threshold_is_symmetric_at_0_4_ticks():
    """X_bps=0 so x_ticks=0. ask_ticks=101 ($10.10), bid_ticks=100
    ($10.00). Re-derived 2026-09-24 under the SYMMETRIC floor/ceil
    quantisation rule (`sim/kernel.py`'s module docstring) -- the
    predecessor round-half-up rule produced the SAME two zero-trade
    outcomes here (transcribed in this test's git history), so this
    fixture's own numbers do not move; only the mechanism producing them
    does, and that mechanism is what this docstring now re-derives by
    hand rather than assume-carries-forward.

    Row A: pred=$10.14 (101.4 ticks, i.e. 0.4 ticks ABOVE the long
    threshold ask_ticks+x_ticks=101). The LONG side gates on
    `floor(pred)`.
        s = round(10.14 * PRICE_SCALE) = 1_014_000_000
        pred_ticks_floor = 1_014_000_000 // 10_000_000 = 101
        101 > 101 is FALSE -- no trade.

    Row B: pred=$9.96 (99.6 ticks, i.e. 0.4 ticks BELOW the short
    threshold bid_ticks-x_ticks=100). The SHORT side gates on
    `ceil(pred) = -((-s) // TICK_SIZE_SCALED)`.
        s = round(9.96 * PRICE_SCALE) = 996_000_000
        pred_ticks_ceil = -((-996_000_000) // 10_000_000) = -(-100) = 100
        100 < 100 is FALSE -- no trade.

    BOTH rows produce zero trades: a 0.4-tick overshoot rounds AGAINST the
    trade on both sides (floor pulls Row A's overshoot back down to
    exactly the ask; ceil pulls Row B's overshoot back up to exactly the
    bid), and the boundary itself never triggers under the strict >/<
    comparison -- this is what makes the SAME +x_ticks/-x_ticks rule
    symmetric, now by TWO SEPARATE formulas that are symmetric BY
    CONSTRUCTION (mirror images of each other via the floor-ceil
    identity), not by one shared rounded value that happened to land the
    same on both sides at this particular offset. Contrast
    `test_symmetric_quantisation_at_exact_half_tick` below, which is the
    offset where the predecessor rule (one shared round-half-up value)
    was NOT symmetric.
    """
    etime = np.array([1], dtype=np.int64)
    bid_ticks = np.array([100], dtype=np.int64)
    ask_ticks = np.array([101], dtype=np.int64)

    row_a = run_sim_checked(
        etime, bid_ticks, ask_ticks, np.array([10.14], dtype=np.float64), x_bps=0
    )
    assert row_a.fill_count == 0

    row_b = run_sim_checked(
        etime, bid_ticks, ask_ticks, np.array([9.96], dtype=np.float64), x_bps=0
    )
    assert row_b.fill_count == 0


def test_symmetric_quantisation_at_exact_half_tick():
    """THE REGRESSION TEST for the 2026-09-24 fix (`sim/kernel.py`'s
    module docstring, "QUANTISATION IS SYMMETRIC BY DIRECTION"). This is
    the test that WOULD HAVE CAUGHT Plan 06-06's finding: under the
    predecessor round-half-up rule, a prediction sitting exactly half a
    tick above the ask triggered a long entry, while a prediction sitting
    exactly half a tick below the bid did NOT trigger a short entry --
    same distance from the opposite quote, opposite outcome. Perfect-
    foresight `pred` sits on exactly this half-tick boundary 99.96% of the
    time on a real day (06-06-SUMMARY.md), so this was not an edge case.

    X_bps=0, bid_ticks=100 ($10.00), ask_ticks=101 ($10.10).

    Row A: pred = ask + 0.5 tick = 101.5 ticks = $10.15.
        s = round(10.15 * PRICE_SCALE) = 1_015_000_000
        pred_ticks_floor = 1_015_000_000 // 10_000_000 = 101
        long_trigger: 101 > 101 is FALSE.
        pred_ticks_ceil = -((-1_015_000_000) // 10_000_000) = 102
        short_trigger: 102 < 100 is FALSE.
        -> ZERO trades. (Predecessor rule: round-half-up(101.5) = 102,
        102 > 101 TRUE -- this row USED TO trigger a long entry.)

    Row B: pred = bid - 0.5 tick = 99.5 ticks = $9.95.
        s = round(9.95 * PRICE_SCALE) = 995_000_000
        pred_ticks_ceil = -((-995_000_000) // 10_000_000) = 100
        short_trigger: 100 < 100 is FALSE.
        pred_ticks_floor = 995_000_000 // 10_000_000 = 99
        long_trigger: 99 > 101 is FALSE.
        -> ZERO trades. (Predecessor rule: round-half-up(99.5) = 100,
        100 < 100 is FALSE too -- this row never triggered under either
        rule; the asymmetry lived entirely on the long side, Row A.)

    Row A and Row B produce the SAME (non-)decision -- zero trades in
    both -- which is the symmetry the predecessor rule broke. Checked
    against BOTH the kernel and the pure-Python twin, so a fix applied to
    only one implementation is caught immediately.
    """
    etime = np.array([1], dtype=np.int64)
    bid_ticks = np.array([100], dtype=np.int64)
    ask_ticks = np.array([101], dtype=np.int64)

    row_a_pred = np.array([10.15], dtype=np.float64)
    row_a_kernel = run_sim_checked(etime, bid_ticks, ask_ticks, row_a_pred, x_bps=0)
    row_a_twin = run_reference_sim(etime, bid_ticks, ask_ticks, row_a_pred, x_bps=0)
    assert row_a_kernel.fill_count == 0
    assert row_a_twin.fill_count == 0

    row_b_pred = np.array([9.95], dtype=np.float64)
    row_b_kernel = run_sim_checked(etime, bid_ticks, ask_ticks, row_b_pred, x_bps=0)
    row_b_twin = run_reference_sim(etime, bid_ticks, ask_ticks, row_b_pred, x_bps=0)
    assert row_b_kernel.fill_count == 0
    assert row_b_twin.fill_count == 0


def test_symmetric_quantisation_anti_vacuity_full_tick_beyond_triggers_both_sides():
    """Anti-vacuity companion to `test_symmetric_quantisation_at_exact_
    half_tick` (D-06-10's own "every oracle asserts a TRADE COUNT" rule,
    applied to this regression test too): a prediction a FULL tick beyond
    either quote DOES trigger, in BOTH directions, proving the half-tick
    test above passes because of the symmetric quantisation rule and not
    because this fixture never trades at all.

    X_bps=0, bid_ticks=100 ($10.00), ask_ticks=101 ($10.10).

    Row A: pred = ask + 1 tick = 102 ticks = $10.20.
        s = round(10.20 * PRICE_SCALE) = 1_020_000_000
        pred_ticks_floor = 1_020_000_000 // 10_000_000 = 102
        long_trigger: 102 > 101 is TRUE -> long entry @ ask=101.

    Row B (independent flat start, own fixture row): pred = bid - 1 tick
    = 99 ticks = $9.90.
        s = round(9.90 * PRICE_SCALE) = 990_000_000
        pred_ticks_ceil = -((-990_000_000) // 10_000_000) = 99
        short_trigger: 99 < 100 is TRUE -> short entry @ bid=100.

    Checked against both the kernel and the pure-Python twin.
    """
    etime = np.array([1], dtype=np.int64)
    bid_ticks = np.array([100], dtype=np.int64)
    ask_ticks = np.array([101], dtype=np.int64)

    row_a_pred = np.array([10.20], dtype=np.float64)
    row_a_kernel = run_sim_checked(etime, bid_ticks, ask_ticks, row_a_pred, x_bps=0)
    row_a_twin = run_reference_sim(etime, bid_ticks, ask_ticks, row_a_pred, x_bps=0)
    assert row_a_kernel.fill_count == 1
    assert row_a_twin.fill_count == 1
    assert int(row_a_kernel.trade_log["side"][0]) == 1
    assert int(row_a_kernel.trade_log["price_ticks"][0]) == 101

    row_b_pred = np.array([9.90], dtype=np.float64)
    row_b_kernel = run_sim_checked(etime, bid_ticks, ask_ticks, row_b_pred, x_bps=0)
    row_b_twin = run_reference_sim(etime, bid_ticks, ask_ticks, row_b_pred, x_bps=0)
    assert row_b_kernel.fill_count == 1
    assert row_b_twin.fill_count == 1
    assert int(row_b_kernel.trade_log["side"][0]) == -1
    assert int(row_b_kernel.trade_log["price_ticks"][0]) == 100


def test_zero_lot_dead_zone_raises_a_named_status():
    etime = np.array([1], dtype=np.int64)
    bid_ticks = np.array([1_000_000], dtype=np.int64)  # $100,000.0
    ask_ticks = np.array([1_000_010], dtype=np.int64)  # $100,001.0
    pred = np.array([_price_at_ticks(1_000_020)], dtype=np.float64)  # well above ask

    with pytest.raises(SimStatusError, match="zero lots") as exc_info:
        run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)

    message = str(exc_info.value)
    assert "row 0" in message
    assert "1000010" in message  # names the offending row's ask_ticks


def test_zero_fee_and_zero_latency_are_true_defaults():
    etime = np.array([1, 2], dtype=np.int64)
    bid_ticks = np.array([100, 102], dtype=np.int64)
    ask_ticks = np.array([101, 103], dtype=np.int64)
    pred = np.array([_price_at_ticks(105), _price_at_ticks(95)], dtype=np.float64)

    implicit = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)
    explicit = run_sim_checked(
        etime, bid_ticks, ask_ticks, pred, x_bps=0, fee_bps=0, latency_ns=0
    )

    k = implicit.fill_count
    assert k == explicit.fill_count
    # Anti-vacuity: at k=0 every column comparison below would be trivial
    # (comparing two empty slices).
    assert k > 0
    for name in ("etime", "side", "price_ticks", "qty_scaled", "position_after"):
        assert np.array_equal(
            implicit.trade_log[name][:k], explicit.trade_log[name][:k]
        ), name
    assert np.array_equal(implicit.equity_scaled, explicit.equity_scaled)


def test_trade_log_overflow_refuses_rather_than_truncates():
    """Not one of this task's eight named behaviours -- added under Rule 2
    (STATE.md's "guardrails runtime-first" lesson): the plan's own action
    text requires the overflow guard ("never silently truncate") but does
    not list a dedicated test for it, and an error path this consequential
    should not ship unexercised. Calls `run_sim` directly (not the checked
    wrapper, whose own allocation always sizes the trade log at
    `etime.shape[0]` and can therefore never overflow) with a deliberately
    undersized trade log -- mirrors `features/kernel.py`'s own
    ring-buffer-overflow discipline: refuse loudly, never wrap."""
    etime = np.array([1, 2], dtype=np.int64)
    bid_ticks = np.array([100, 102], dtype=np.int64)
    ask_ticks = np.array([101, 103], dtype=np.int64)
    pred = np.array([_price_at_ticks(105), _price_at_ticks(95)], dtype=np.float64)

    capacity = 1  # room for row 1's entry only; row 2's flip must refuse
    out_trade_etime = np.zeros(capacity, dtype=np.int64)
    out_trade_side = np.zeros(capacity, dtype=np.int8)
    out_trade_price_ticks = np.zeros(capacity, dtype=np.int64)
    out_trade_qty_scaled = np.zeros(capacity, dtype=np.int64)
    out_trade_position_after = np.zeros(capacity, dtype=np.int8)
    out_equity_scaled = np.zeros(2, dtype=np.int64)
    state = new_state()

    status = run_sim(
        etime,
        bid_ticks,
        ask_ticks,
        pred,
        0,
        MAX_NOTIONAL_SCALED,
        LOT_STEP_SCALED,
        0,
        0,
        out_trade_etime,
        out_trade_side,
        out_trade_price_ticks,
        out_trade_qty_scaled,
        out_trade_position_after,
        out_equity_scaled,
        state,
    )

    assert status == STATUS_TRADE_LOG_OVERFLOW
    assert int(state[STATE_I64_SLOTS["error_row"]]) == 1
    # Row 0's trade was written correctly and not corrupted by the refusal.
    assert int(out_trade_etime[0]) == 1
    assert int(out_trade_price_ticks[0]) == 101
    assert int(out_trade_position_after[0]) == 1


# --------------------------------------------------------------------------
# Task 2: outputs.py assembly, the pure-Python reference twin, and the
# bitwise equivalence test
#
# `run_sim_checked` now returns a `SimResult` NamedTuple (not the plain
# dict Task 1 initially returned) -- `sim/kernel.py` was rewired once
# `sim/outputs.py` existed, and every `result["field"]` access above was
# mechanically rewritten to `result.field` in the same commit as this
# section, so Task 1's tests keep passing under the final interface.
# --------------------------------------------------------------------------


@st.composite
def _decision_sequences(draw):
    """`(etime, bid_ticks, ask_ticks, pred)` decision-row arrays, length
    1-200.

    `bid_ticks` is confined to [100_000, 900_000] -- $10,000..$90,000,
    comfortably below D-06-20's ~$100,000 zero-lot dead zone
    (06-RESEARCH.md Q1's real median is ~$77,061) -- so every generated
    row stays in OK-status territory for BOTH implementations; this sweep
    proves the happy-path arithmetic agrees, not the error-status paths
    Task 1 already covers directly. `ask = bid + spread`, spread in
    [1, 50] ticks (mirrors the measured real spread distribution,
    06-RESEARCH.md Q6 -- 97.463% of real rows are exactly 1 tick). `pred`
    is drawn as a RAW PRICE near the row's own mid, offset by a signed
    tick count in [-40, 40] plus a coin-flip HALF-TICK (`+0.5`) -- never as
    a bare integer in the tick range, which would make every row trigger
    and the anti-vacuity check below vacuous (this plan's own read_first
    note: this mistake was caught twice while designing `sim/ticks.py`'s
    tests in Plan 06-02). The half-tick coin flip was ADDED alongside the
    2026-09-24 symmetric-quantisation fix (`sim/kernel.py`'s module
    docstring): every prior version of this strategy generated `pred` on
    an exact tick grid point only (`(bid+ask)//2 + integer offset`), which
    means this sweep never once exercised the half-tick tie case that is
    99.96% of a real day's perfect-foresight predictions and is exactly
    where the predecessor round-half-up rule was asymmetric -- kernel/twin
    agreement on THAT case was unproven until now.

    At this price band and the $100/0.001 BTC MVP defaults, `qty_scaled`
    stays on the order of 1e6-1e7 and the per-trade tick delta stays under
    ~90 (spread + offset bounds), so `realized_pnl_scaled`'s accumulation
    over up to 200 trades stays many orders of magnitude under int64's
    ~9.22e18 ceiling -- no overflow risk in this sweep (re-derive this
    bound if the price band or example count ever widens, per
    06-02-SUMMARY.md's own "must re-derive this bound" note about
    `position_size_ticks`'s intermediate product).
    """
    n = draw(st.integers(min_value=1, max_value=200))
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
        half_tick = 0.5 if draw(st.booleans()) else 0.0
        pred_ticks_target = (bid + ask) / 2 + offset + half_tick
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


@settings(deadline=None, max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(seq=_decision_sequences())
def test_kernel_and_reference_agree_on_hypothesis_generated_sequences(seq):
    etime, bid_ticks, ask_ticks, pred = seq

    kernel_result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)
    reference_result = run_reference_sim(etime, bid_ticks, ask_ticks, pred, x_bps=0)

    k = kernel_result.fill_count
    assert k == reference_result.fill_count
    for name in ("etime", "side", "price_ticks", "qty_scaled", "position_after"):
        assert np.array_equal(
            kernel_result.trade_log[name][:k], reference_result.trade_log[name][:k]
        ), name
    assert np.array_equal(kernel_result.equity_scaled, reference_result.equity_scaled)
    assert kernel_result.counters == reference_result.counters


def test_the_strategy_can_generate_a_trading_sequence():
    """Anti-vacuity for the sweep above (D-06-10): rather than merely
    hoping `max_examples=50` happens to include a trading sequence,
    `hypothesis.find` proves the strategy CAN produce one -- it raises
    `NoSuchExample` otherwise, which IS the assertion this test makes."""
    etime, bid_ticks, ask_ticks, pred = find(
        _decision_sequences(),
        lambda seq: run_reference_sim(*seq).fill_count > 0,
    )
    assert run_reference_sim(etime, bid_ticks, ask_ticks, pred).fill_count > 0


def test_new_trade_log_preallocates_at_n_decision_rows_and_returns_a_fill_count():
    n = 10
    trade_log = new_trade_log(n)
    expected_dtypes = {
        "etime": np.int64,
        "side": np.int8,
        "price_ticks": np.int64,
        "qty_scaled": np.int64,
        "position_after": np.int8,
    }
    for name, dtype in expected_dtypes.items():
        assert trade_log[name].shape == (n,)
        assert trade_log[name].dtype == dtype

    etime = np.array([1, 2, 3, 4, 5], dtype=np.int64)
    bid_ticks = np.array([100] * 5, dtype=np.int64)
    ask_ticks = np.array([101] * 5, dtype=np.int64)
    # Row 1 enters long; rows 2-5 are same-direction no-ops (D-06-08) -- so
    # k=1 < n=5, exercising the fill-count-vs-capacity distinction.
    pred = np.array([_price_at_ticks(105)] * 5, dtype=np.float64)

    result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)

    assert isinstance(result, SimResult)
    assert result.fill_count == 1
    # The trade log is allocated at n (Pattern 1), not k -- a caller reads
    # fill_count to know where the real data ends.
    assert result.trade_log["etime"].shape == (5,)
    k = result.fill_count
    sliced = result.trade_log["etime"][:k]
    assert sliced.shape == (1,)
    assert int(sliced[0]) == 1


def test_equity_mark_matches_both_implementations_on_a_long_position():
    """A direct (non-hypothesis) check that the equity mark is at
    bid_ticks for a long position (Pattern 3), agreeing between both
    implementations -- the sweep above proves agreement broadly; this test
    is the one the Task 2 mutation check targets specifically."""
    etime = np.array([1, 2], dtype=np.int64)
    bid_ticks = np.array([100, 105], dtype=np.int64)
    ask_ticks = np.array([101, 106], dtype=np.int64)
    pred = np.array([_price_at_ticks(105), _price_at_ticks(105)], dtype=np.float64)

    kernel_result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)
    reference_result = run_reference_sim(etime, bid_ticks, ask_ticks, pred, x_bps=0)

    # Long @ ask=101 (row 1); row 2 marks at bid=105 -> unrealized (105-101)*qty.
    expected_qty = position_size_ticks(101)
    expected_equity_row2 = (105 - 101) * expected_qty
    assert int(kernel_result.equity_scaled[1]) == expected_equity_row2
    assert np.array_equal(kernel_result.equity_scaled, reference_result.equity_scaled)
