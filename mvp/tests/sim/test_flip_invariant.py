"""D-06-12: the flip-only invariant is a PROPERTY test, checked on the
trade log itself -- the artifact that PROVES what happened -- never on
`sim.kernel`'s internal state array. Over every hypothesis-generated
sequence: `position_after` is always in `{-1, +1}` (0 never appears in the
LOG -- flat is the pre-trade state, never a logged fill, since D-06-08's
rule has no flat exit); every transition from one trade-log row to the
next is to the OPPOSITE sign, never the same sign (same-direction
re-entry) and never a larger same-sign magnitude (risk-increasing); and
every fill's own quantity is a single, FRESH `position_size_ticks` call at
that row's own price (D-06-08/Q13) -- never larger, which is the concrete
form "no risk-increasing order" takes once quantity is always exactly one
notional-cap's worth.

Because this kernel's rule has NO flat exit (only an opposite-direction
flip ever closes a position, per `mvp.md` lines 44-47 and `spec.md`'s
Decision rule), "every transition from a non-zero position is either flat
or the opposite sign" (D-06-12's own general wording) collapses, FOR THIS
KERNEL SPECIFICALLY, to "every transition is to the opposite sign" --
`position_after[j] == -position_after[j-1]` for every `j >= 1` in the
trade log. That collapse is itself asserted below (via the `abs(...) == 1`
check on every row), not silently assumed.
"""

from __future__ import annotations

import numpy as np
from hypothesis import HealthCheck, find, given, settings
from hypothesis import strategies as st

from sim.kernel import run_sim_checked
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED, position_size_ticks


def _price_at_ticks(ticks: float) -> float:
    return ticks * TICK_SIZE_SCALED / PRICE_SCALE


@st.composite
def _decision_sequences(draw):
    """`(etime, bid_ticks, ask_ticks, pred)` decision-row arrays, length
    1-150. Duplicated locally rather than imported from
    `test_path_dependence.py`/`test_kernel.py` -- this project's own
    per-file hypothesis-fixture convention (`tests/sim/` has no
    `__init__.py`, D-06-02; see `test_determinism.py`/`test_null_refusal.py`
    for the same duplication). `bid_ticks` confined to [100_000, 900_000]
    ($10,000-$90,000), comfortably under D-06-20's ~$100,000 zero-lot dead
    zone; `ask = bid + spread`, spread in [1, 50] ticks; `pred` is a RAW
    PRICE offset from the row's own mid by a signed tick count in
    [-40, 40] -- never a bare tick-range integer (see this file's
    `read_first` note and `sim/kernel.py`'s own module docstring on `pred`
    units)."""
    n = draw(st.integers(min_value=1, max_value=150))
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


#: Aggregated across every hypothesis example the sweep below runs, so the
#: anti-vacuity assertion is genuinely "did ANY generated example trade at
#: all" -- not a per-example check that could pass on 50 examples that
#: each individually traded zero times (D-06-10's own requirement, applied
#: to this property test as the plan's own text names explicitly).
_TRADED_AT_LEAST_ONCE: list[bool] = []
_TRADED_AT_LEAST_TWICE: list[bool] = []


@settings(deadline=None, max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(seq=_decision_sequences())
def _flip_only_property_sweep(seq):
    etime, bid_ticks, ask_ticks, pred = seq
    result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)
    k = result.fill_count

    if k > 0:
        _TRADED_AT_LEAST_ONCE.append(True)
    if k > 1:
        _TRADED_AT_LEAST_TWICE.append(True)

    positions = result.trade_log["position_after"][:k]
    prices = result.trade_log["price_ticks"][:k]
    qtys = result.trade_log["qty_scaled"][:k]

    for j in range(k):
        # position stays in {-1, 0, +1} (D-06-12); 0 never appears in the
        # LOG under this flip-only rule (see module docstring).
        assert abs(int(positions[j])) == 1, (
            f"row {j}: trade-log position_after={positions[j]!r} is not +/-1"
        )
        # No risk-increasing order: quantity is always a single FRESH
        # position_size_ticks call at THIS row's own price, never a
        # larger same-sign accumulation (D-06-08/Q13).
        assert int(qtys[j]) == position_size_ticks(int(prices[j])), (
            f"row {j}: trade-log qty does not match a fresh "
            "position_size_ticks at this row's own fill price"
        )

    for j in range(1, k):
        # Every transition out of a non-zero position is the OPPOSITE
        # sign, never the same sign (same-direction re-entry) -- this
        # kernel's flip-only rule has no flat exit, so "flat or opposite"
        # collapses to "always opposite" (module docstring).
        assert int(positions[j]) == -int(positions[j - 1]), (
            f"row {j}: position_after={positions[j]} did not flip sign "
            f"from row {j - 1}'s position_after={positions[j - 1]} -- a "
            "same-direction re-entry or a risk-increasing order slipped "
            "through"
        )


def test_flip_only_property():
    _TRADED_AT_LEAST_ONCE.clear()
    _TRADED_AT_LEAST_TWICE.clear()
    _flip_only_property_sweep()
    assert any(_TRADED_AT_LEAST_ONCE), (
        "anti-vacuity (D-06-10): no generated example traded even once "
        "across the whole sweep -- the flip-only property would then hold "
        "vacuously"
    )
    assert any(_TRADED_AT_LEAST_TWICE), (
        "anti-vacuity: no generated example produced two or more trade-log "
        "rows -- the transition (opposite-sign) clause is unexercised "
        "unless at least one example actually flips"
    )


def test_the_strategy_can_generate_a_multi_trade_sequence():
    """Companion `hypothesis.find` proof (mirrors
    `tests/sim/test_kernel.py::test_the_strategy_can_generate_a_trading_
    sequence`'s pattern): the strategy CAN produce a sequence with two or
    more trade-log rows -- `NoSuchExample` otherwise -- rather than only
    hoping the aggregate check above happens to see one among 50 random
    draws."""

    def _has_two_or_more_fills(seq) -> bool:
        etime, bid_ticks, ask_ticks, pred = seq
        result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)
        return result.fill_count > 1

    seq = find(_decision_sequences(), _has_two_or_more_fills)
    assert _has_two_or_more_fills(seq)
