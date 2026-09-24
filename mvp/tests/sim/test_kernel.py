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

from sim.arrays import sim_arrays
from sim.kernel import (
    STATE_I64_SLOTS,
    STATUS_TRADE_LOG_OVERFLOW,
    SimStatusError,
    new_state,
    run_sim,
    run_sim_checked,
)
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

    assert result["fill_count"] == 1
    assert int(result["trade_log"]["price_ticks"][0]) == 101  # entry at the ask
    assert int(result["trade_log"]["position_after"][0]) == 1
    assert int(result["trade_log"]["side"][0]) == 1
    assert result["counters"]["trades"] == 1
    assert result["counters"]["flips"] == 0


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

    assert result["fill_count"] == 2  # row1's entry + row2's flip, not four
    assert result["counters"]["flips"] == 1
    assert int(result["trade_log"]["etime"][1]) == 2
    assert int(result["trade_log"]["position_after"][1]) == -1
    assert int(result["trade_log"]["side"][1]) == -1
    assert int(result["trade_log"]["price_ticks"][1]) == 102  # row2's bid

    expected_qty = position_size_ticks(102)
    wrong_qty = position_size_ticks(101)  # what the OLD entry price would size
    assert expected_qty != wrong_qty, "fixture is vacuous: both prices size identically"
    assert int(result["trade_log"]["qty_scaled"][1]) == expected_qty


def test_no_same_direction_or_risk_increasing_order():
    etime = np.array([1, 2], dtype=np.int64)
    bid_ticks = np.array([100, 100], dtype=np.int64)
    ask_ticks = np.array([101, 101], dtype=np.int64)
    pred = np.array(
        [_price_at_ticks(105), _price_at_ticks(110)], dtype=np.float64
    )  # both rows trigger long; already long by row 2

    result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)

    assert result["fill_count"] == 1  # only row 1's entry
    assert int(result["trade_log"]["position_after"][0]) == 1
    assert result["counters"]["trades"] == 1


def test_quantised_threshold_is_symmetric_at_0_4_ticks():
    """X_bps=0 so x_ticks=0. ask_ticks=101 ($10.10), bid_ticks=100
    ($10.00).

    Row A: pred=$10.14 (101.4 ticks, i.e. 0.4 ticks ABOVE the long
    threshold ask_ticks+x_ticks=101).
        s = round(10.14 * PRICE_SCALE) = 1_014_000_000
        pred_ticks = (1_014_000_000 + 5_000_000) // 10_000_000
                   = 1_019_000_000 // 10_000_000 = 101
        101 > 101 is FALSE -- no trade.

    Row B: pred=$9.96 (99.6 ticks, i.e. 0.4 ticks BELOW the short
    threshold bid_ticks-x_ticks=100).
        s = round(9.96 * PRICE_SCALE) = 996_000_000
        pred_ticks = (996_000_000 + 5_000_000) // 10_000_000
                   = 1_001_000_000 // 10_000_000 = 100
        100 < 100 is FALSE -- no trade.

    BOTH rows produce zero trades: nearest-tick rounding pulls a 0.4-tick
    overshoot back to exactly the boundary in BOTH directions, and the
    boundary itself never triggers under the strict >/< comparison -- this
    is what makes the SAME +x_ticks/-x_ticks rule symmetric, not an
    accident of one side's rounding direction.
    """
    etime = np.array([1], dtype=np.int64)
    bid_ticks = np.array([100], dtype=np.int64)
    ask_ticks = np.array([101], dtype=np.int64)

    row_a = run_sim_checked(
        etime, bid_ticks, ask_ticks, np.array([10.14], dtype=np.float64), x_bps=0
    )
    assert row_a["fill_count"] == 0

    row_b = run_sim_checked(
        etime, bid_ticks, ask_ticks, np.array([9.96], dtype=np.float64), x_bps=0
    )
    assert row_b["fill_count"] == 0


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

    k = implicit["fill_count"]
    assert k == explicit["fill_count"]
    # Anti-vacuity: at k=0 every column comparison below would be trivial
    # (comparing two empty slices).
    assert k > 0
    for name in ("etime", "side", "price_ticks", "qty_scaled", "position_after"):
        assert np.array_equal(
            implicit["trade_log"][name][:k], explicit["trade_log"][name][:k]
        ), name
    assert np.array_equal(implicit["equity_scaled"], explicit["equity_scaled"])


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
