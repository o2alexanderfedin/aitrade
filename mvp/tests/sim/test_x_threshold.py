"""The X threshold's GRANULARITY: one basis point is 77 ticks at BTC's price,
so an integer-only `x_bps` gives `spec.md`'s "X swept" exactly one usable
value and the sweep is a fiction.

WHAT THE OOF VIABILITY RUN MEASURED, and why this file exists. On the five
cached OOF blocks `x_ticks = (bid_ticks + ask_ticks) * x_bps // 20_000` came
out at 74 to 79 ticks for `x_bps=1`, while the frozen model's predictions
clear the touch by at most a few ticks. So `x_bps=1` produced ZERO trigger
rows and ZERO trades on every one of the five blocks -- not a smaller trade
set, an empty one. `x_bps=0` was the only feasible point, and a swept
hyperparameter with one feasible value is not swept.

THE HARD SAFETY PROPERTY THIS FILE GUARDS. Every committed number in phases 6
and 7 was simulated at `x_bps=0`: 2,192 trades / 294,554 ticks / $29.4554 on
2026-09-13, 9,946 / 1,120,460 / $112.0460 on the approved `val` window, 12
trades / 50 ticks / +$0.005000 on the 07-09 fixture rig. Making sub-basis-
point thresholds expressible must not move any of them, so
`test_the_three_ways_of_saying_zero_are_byte_identical` asserts the three
spellings of a zero threshold agree column for column, and the evidence-level
proof is a re-run of `scripts/oof_viability_check.py` on `oof_block_1` diffed
against the committed evidence JSON.

NO LAKE, NO MLFLOW, NO LOOK. Every fixture here is hand-built integer ticks.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sim.kernel import run_sim_checked
from sim.reference import run_reference_sim
from sim.ticks import (
    PRICE_SCALE,
    TICK_SIZE_SCALED,
    X_BPS_SCALE,
    X_TICKS_DENOMINATOR,
    XThresholdError,
    resolve_x_bps_scaled,
)

#: A real BTC row: $77,000.00 bid / $77,000.10 ask, the minimum one-tick
#: spread that is 97.5% of real rows (06-RESEARCH.md Q6). `bid + ask =
#: 1_540_001`, which is the number every worked figure below divides.
BID_TICKS: int = 770_000
ASK_TICKS: int = 770_001
BOOK_SUM: int = BID_TICKS + ASK_TICKS

#: `x_bps_scaled` for exactly ONE tick of threshold on the row above:
#: `1_540_001 * 130 // 200_000_000 = 200_200_130 // 200_000_000 = 1`, while
#: 129 gives `198_660_129 // 200_000_000 = 0`. 130 units is 0.013 bps -- the
#: threshold a whole basis point cannot express.
ONE_TICK_SCALED: int = 130


def _price_at_ticks(ticks: float) -> float:
    """A raw price at a (possibly fractional) tick count -- `sim/kernel.py`'s
    `pred` scale, never `sim.ticks.price_to_ticks`'s inverse (that conversion
    is for bid/ask only; see that module's docstring)."""
    return ticks * TICK_SIZE_SCALED / PRICE_SCALE


def _one_row(
    pred_ticks: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.array([1], dtype=np.int64),
        np.array([BID_TICKS], dtype=np.int64),
        np.array([ASK_TICKS], dtype=np.int64),
        np.array([_price_at_ticks(pred_ticks)], dtype=np.float64),
    )


def _random_walk(
    n: int = 4_000, seed: int = 20260929
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """A deterministic one-tick-spread walk with predictions scattered a few
    ticks either side of the mid -- the shape the frozen model's own
    predictions have (measured: it clears the touch by at most a few ticks,
    which is why 77 ticks of threshold silences it entirely).

    Seeded, so the trade counts the ladder test asserts are reproducible
    rather than a fresh draw on every run.
    """
    rng = np.random.default_rng(seed)
    steps = rng.integers(-1, 2, size=n)
    bid = BID_TICKS + np.cumsum(steps)
    ask = bid + 1
    offsets = rng.integers(-6, 7, size=n).astype(np.float64)
    pred_ticks = (bid + ask) / 2.0 + offsets
    return (
        np.arange(1, n + 1, dtype=np.int64),
        bid.astype(np.int64),
        ask.astype(np.int64),
        np.ascontiguousarray(
            pred_ticks * TICK_SIZE_SCALED / PRICE_SCALE, dtype=np.float64
        ),
    )


def _logs_equal(left, right) -> bool:
    if int(left.fill_count) != int(right.fill_count):
        return False
    k = int(left.fill_count)
    for name in ("etime", "side", "price_ticks", "qty_scaled", "position_after"):
        if not np.array_equal(left.trade_log[name][:k], right.trade_log[name][:k]):
            return False
    return bool(np.array_equal(left.equity_scaled, right.equity_scaled)) and (
        left.counters == right.counters
    )


# --------------------------------------------------------------------------
# 1. The measured degeneracy: one basis point is 77 ticks here
# --------------------------------------------------------------------------


def test_one_whole_basis_point_is_seventy_seven_ticks_on_a_real_btc_row():
    """The arithmetic the OOF run measured as "74 to 79 ticks", pinned on one
    hand-computed row and asserted through the KERNEL'S OWN RULE rather than
    against a second copy of the formula.

        bid_ticks = 770_000 ($77,000.00), ask_ticks = 770_001 ($77,000.10)
        x_bps = 1  ->  x_bps_scaled = 1 * 10_000 = 10_000
        x_ticks = 1_540_001 * 10_000 // 200_000_000
                = 15_400_010_000 // 200_000_000 = 77

    So a prediction at `ask + 77` ticks must NOT trade (the rule is strict
    `>`), and one at `ask + 78` must. Anti-vacuity first: the same `ask + 1`
    prediction that trades at `x_bps=0` is silenced at `x_bps=1`, which is
    what makes 77 the number that matters rather than a number that is merely
    printed.
    """
    at_one_tick = _one_row(ASK_TICKS + 1)
    assert run_sim_checked(*at_one_tick, x_bps=0).fill_count == 1, (
        "a full tick above the ask must trade at a zero threshold, or every "
        "assertion below is vacuous"
    )
    assert run_sim_checked(*at_one_tick, x_bps=1).fill_count == 0, (
        "one basis point silenced a prediction a full tick past the ask -- "
        "this is the measured degeneracy and it must be reproduced here"
    )

    assert run_sim_checked(*_one_row(ASK_TICKS + 77), x_bps=1).fill_count == 0
    assert run_sim_checked(*_one_row(ASK_TICKS + 78), x_bps=1).fill_count == 1


def test_no_integer_basis_point_can_express_a_one_tick_threshold_here():
    """The degeneracy stated as an assertion, not as prose: on this row the
    integer knob jumps 0 -> 77 ticks with nothing in between, so every
    threshold between one tick and seventy-six is INEXPRESSIBLE in whole
    basis points. That is the defect."""
    reachable = {
        BOOK_SUM * resolve_x_bps_scaled(x_bps=whole) // X_TICKS_DENOMINATOR
        for whole in range(0, 3)
    }
    assert reachable == {0, 77, 154}, reachable
    assert 1 not in reachable, (
        "if a whole basis point could express a one-tick threshold there "
        "would be nothing to fix"
    )


# --------------------------------------------------------------------------
# 2. Sub-basis-point thresholds, and the three spellings of zero
# --------------------------------------------------------------------------


def test_a_sub_basis_point_threshold_expresses_exactly_one_tick():
    """`x_bps_scaled = 130` is 0.013 bps and is exactly one tick on this row;
    129 is exactly zero. Both boundaries are exercised, so an off-by-one in
    the denominator moves one of them.

        1_540_001 * 130 // 200_000_000 = 200_200_130 // 200_000_000 = 1
        1_540_001 * 129 // 200_000_000 = 198_660_129 // 200_000_000 = 0
    """
    assert BOOK_SUM * ONE_TICK_SCALED // X_TICKS_DENOMINATOR == 1
    assert BOOK_SUM * (ONE_TICK_SCALED - 1) // X_TICKS_DENOMINATOR == 0

    # x_ticks == 1: `ask + 1` no longer trades, `ask + 2` does.
    assert run_sim_checked(*_one_row(ASK_TICKS + 1), x_bps_scaled=130).fill_count == 0
    assert run_sim_checked(*_one_row(ASK_TICKS + 2), x_bps_scaled=130).fill_count == 1
    # x_ticks == 0 one unit lower: `ask + 1` trades again.
    assert run_sim_checked(*_one_row(ASK_TICKS + 1), x_bps_scaled=129).fill_count == 1
    # The short side moves by the same amount, subtracted instead of added.
    assert run_sim_checked(*_one_row(BID_TICKS - 1), x_bps_scaled=130).fill_count == 0
    assert run_sim_checked(*_one_row(BID_TICKS - 2), x_bps_scaled=130).fill_count == 1
    assert run_sim_checked(*_one_row(BID_TICKS - 1), x_bps_scaled=129).fill_count == 1


def test_the_three_ways_of_saying_zero_are_byte_identical():
    """THE HARD SAFETY PROPERTY, at the unit level: the default, an explicit
    `x_bps=0` and an explicit `x_bps_scaled=0` produce the same trade log, the
    same equity array and the same counters, on a 4,000-row walk that actually
    trades.

    Every committed figure in phases 6 and 7 was measured at `x_bps=0`. If
    this test can be made to fail, those numbers moved.
    """
    walk = _random_walk()
    default = run_sim_checked(*walk)
    explicit_bps = run_sim_checked(*walk, x_bps=0)
    explicit_scaled = run_sim_checked(*walk, x_bps_scaled=0)
    assert default.fill_count > 0, (
        "the walk never trades, so agreeing on zero trades proves nothing"
    )
    assert _logs_equal(default, explicit_bps)
    assert _logs_equal(default, explicit_scaled)
    assert _logs_equal(default, run_reference_sim(*walk, x_bps_scaled=0))


def test_a_whole_basis_point_and_its_scaled_spelling_agree_exactly():
    """`x_bps=1` and `x_bps_scaled=X_BPS_SCALE` are the same threshold, and
    the integer identity that makes them the same is exact:
    `k * 10_000 // 200_000_000 == k // 20_000` for every non-negative `k`.

    Checked on a walk whose predictions are a few ticks wide, so BOTH spellings
    produce zero trades here -- which is the measured degeneracy again, and is
    why the equality is also asserted on the raw arithmetic over a tick range
    rather than only on the (empty) trade logs.
    """
    walk = _random_walk()
    assert _logs_equal(
        run_sim_checked(*walk, x_bps=1),
        run_sim_checked(*walk, x_bps_scaled=X_BPS_SCALE),
    )
    for book_sum in range(1_500_000, 1_600_000, 7_919):
        assert book_sum * X_BPS_SCALE // X_TICKS_DENOMINATOR == book_sum // 20_000, (
            book_sum
        )


def test_the_threshold_thins_the_trade_set_across_a_sub_basis_point_ladder():
    """THE POINT OF THE FIX: a ladder of sub-basis-point thresholds produces a
    monotonically thinning trade set that reaches zero, so Stage 2's swept X
    has a continuum of feasible values instead of one.

    The counts are printed, and three things are asserted: the ladder is
    non-increasing, its top is strictly below its bottom (so the axis MOVES),
    and the whole-basis-point value silences the strategy completely (so the
    old axis really was degenerate).
    """
    walk = _random_walk()
    ladder = [0, 65, 130, 260, 520, 1_040]
    counts = [
        int(run_sim_checked(*walk, x_bps_scaled=scaled).fill_count) for scaled in ladder
    ]
    print(f"trades by x_bps_scaled {dict(zip(ladder, counts, strict=True))}")
    assert counts[0] > 0
    assert all(
        later <= earlier for earlier, later in zip(counts, counts[1:], strict=False)
    ), counts
    assert counts[-1] < counts[0], (
        "the ladder never thinned the trade set -- the new knob does not reach "
        "the strategy"
    )
    assert len(set(counts)) >= 3, (
        f"only {len(set(counts))} distinct trade counts across the ladder; a "
        "sweep axis needs more than a switch"
    )
    assert int(run_sim_checked(*walk, x_bps=1).fill_count) == 0, (
        "one whole basis point must still be the empty set on this walk -- "
        "that is the measured behaviour the fix does not change"
    )


# --------------------------------------------------------------------------
# 3. The refusals: silent truncation, two knobs at once, negative, overflow
# --------------------------------------------------------------------------


def test_a_fractional_x_bps_is_refused_instead_of_truncated_to_zero():
    """`run_sim_checked` used to coerce with `int(x_bps)`, so `x_bps=0.5`
    silently BECAME `x_bps=0` and returned the zero-threshold trade log under
    a caller's belief that half a basis point had been simulated.

    The trap is specific: 0.5 truncates DOWNWARD to the one value that is
    known to work, so the run looks healthy. A refusal naming the exact knob
    is the only outcome a caller can act on.
    """
    walk = _random_walk()
    zero_threshold_trades = int(run_sim_checked(*walk, x_bps=0).fill_count)
    assert zero_threshold_trades > 0

    with pytest.raises(XThresholdError) as excinfo:
        run_sim_checked(*walk, x_bps=0.5)
    message = str(excinfo.value)
    print("fractional x_bps:", message)
    assert "x_bps_scaled" in message, (
        "the refusal must name the knob that CAN express half a basis point, "
        "or the caller's only option is to round"
    )
    with pytest.raises(XThresholdError):
        run_reference_sim(*walk, x_bps=0.5)
    with pytest.raises(XThresholdError):
        run_sim_checked(*walk, x_bps_scaled=130.5)


def test_passing_both_knobs_at_once_is_refused():
    walk = _random_walk()
    with pytest.raises(XThresholdError, match="exactly one"):
        run_sim_checked(*walk, x_bps=0, x_bps_scaled=0)
    with pytest.raises(XThresholdError, match="exactly one"):
        run_reference_sim(*walk, x_bps=1, x_bps_scaled=10_000)


def test_a_negative_threshold_is_refused_on_both_knobs():
    """A negative X is not a smaller threshold, it is a DIFFERENT RULE: the
    kernel would then fire on a prediction that has not reached the touch at
    all, trading inside the spread. `//` floors toward minus infinity, so a
    negative value produces a negative `x_ticks` silently.
    """
    walk = _random_walk()
    for kwargs in ({"x_bps": -1}, {"x_bps_scaled": -1}):
        with pytest.raises(XThresholdError, match="negative"):
            run_sim_checked(*walk, **kwargs)
        with pytest.raises(XThresholdError, match="negative"):
            run_reference_sim(*walk, **kwargs)


def test_an_x_bps_scaled_that_would_overflow_int64_is_refused_before_the_scan():
    """`(bid_ticks + ask_ticks) * x_bps_scaled` is int64 inside the `@njit`
    body, where an overflow WRAPS instead of raising -- and a wrapped product
    can land negative, which is the negative-threshold rule above arrived at
    silently. The bound is a property of this frame's own book, so it is
    checked against the frame rather than against a constant.

    Mirrors `test_notional_overflow_bound_kernel_succeeds_at_bound_raises_one_past_it`:
    the boundary VALUE succeeds and one past it raises, never a value near it.
    """
    walk = _random_walk()
    book_sum = int(walk[1].max()) + int(walk[2].max())
    at_bound = (2**63 - 1) // book_sum
    ok = run_sim_checked(*walk, x_bps_scaled=at_bound)
    assert ok.fill_count == 0, (
        "a threshold this enormous must silence the strategy; a trade here "
        "means the product already wrapped"
    )
    with pytest.raises(XThresholdError, match="WRAP") as excinfo:
        run_sim_checked(*walk, x_bps_scaled=at_bound + 1)
    message = str(excinfo.value)
    print("overflow refusal:", message)
    assert "int64" in message and str(book_sum) in message, (
        "the refusal must name the bound AND the frame's own book sum, or a "
        "reader cannot tell whether the threshold or the data moved"
    )


def test_resolve_x_bps_scaled_defaults_to_zero_and_scales_a_whole_basis_point():
    assert resolve_x_bps_scaled() == 0
    assert resolve_x_bps_scaled(x_bps=0) == 0
    assert resolve_x_bps_scaled(x_bps_scaled=0) == 0
    assert resolve_x_bps_scaled(x_bps=1) == X_BPS_SCALE
    assert resolve_x_bps_scaled(x_bps=3) == 3 * X_BPS_SCALE
    assert resolve_x_bps_scaled(x_bps_scaled=130) == 130
    # numpy integers are integral and must be accepted -- a caller reading a
    # swept value out of an array should not have to cast it.
    assert resolve_x_bps_scaled(x_bps=np.int64(2)) == 2 * X_BPS_SCALE
    # A float that IS an exact integer is accepted; only a fractional one is
    # refused, because that is the value `int()` used to eat.
    assert resolve_x_bps_scaled(x_bps=2.0) == 2 * X_BPS_SCALE


# --------------------------------------------------------------------------
# 4. Kernel and twin agree at NONZERO thresholds
# --------------------------------------------------------------------------


@st.composite
def _threshold_sequences(draw):
    """`(etime, bid, ask, pred, x_bps_scaled)` where the threshold is drawn in
    a band that produces 0 to ~8 ticks at this price -- the range a real
    Stage-2 sweep would walk.

    WHY THIS SWEEP HAD TO BE ADDED. Every pre-existing kernel-vs-reference
    comparison in `tests/sim/test_kernel.py` runs at `x_bps=0`, and
    `0 * anything // anything` is 0 whatever the denominator is. A mutated
    `X_TICKS_DENOMINATOR` passes all of them. This is the sweep where the
    denominator is load-bearing.
    """
    n = draw(st.integers(min_value=1, max_value=200))
    scaled = draw(st.integers(min_value=0, max_value=1_100))
    etimes: list[int] = []
    bids: list[int] = []
    asks: list[int] = []
    preds: list[float] = []
    t = draw(st.integers(min_value=1, max_value=1_000))
    bid = draw(st.integers(min_value=700_000, max_value=900_000))
    for _ in range(n):
        t += draw(st.integers(min_value=1, max_value=1_000))
        bid = min(
            900_000,
            max(700_000, bid + draw(st.integers(min_value=-500, max_value=500))),
        )
        spread = draw(st.integers(min_value=1, max_value=50))
        ask = bid + spread
        offset = draw(st.integers(min_value=-40, max_value=40))
        half_tick = 0.5 if draw(st.booleans()) else 0.0
        etimes.append(t)
        bids.append(bid)
        asks.append(ask)
        preds.append(_price_at_ticks((bid + ask) / 2 + offset + half_tick))
    return (
        np.array(etimes, dtype=np.int64),
        np.array(bids, dtype=np.int64),
        np.array(asks, dtype=np.int64),
        np.array(preds, dtype=np.float64),
        scaled,
    )


@settings(deadline=None, max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(seq=_threshold_sequences())
def test_kernel_and_reference_agree_at_nonzero_thresholds(seq):
    etime, bid_ticks, ask_ticks, pred, scaled = seq
    kernel = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps_scaled=scaled)
    twin = run_reference_sim(etime, bid_ticks, ask_ticks, pred, x_bps_scaled=scaled)
    assert _logs_equal(kernel, twin)


def test_the_threshold_strategy_can_generate_a_nonzero_threshold_that_still_trades():
    """Anti-vacuity for the sweep above (D-06-10): if every drawn example
    either had a zero threshold or produced zero fills, the sweep would prove
    agreement on nothing. Constructed directly rather than by `find`, because
    the property needed is a conjunction of two conditions on one example.
    """
    walk = _random_walk()
    for scaled in (65, 130, 260):
        result = run_sim_checked(*walk, x_bps_scaled=scaled)
        if result.fill_count > 0:
            twin = run_reference_sim(*walk, x_bps_scaled=scaled)
            assert _logs_equal(result, twin)
            print(f"nonzero threshold {scaled} trades {result.fill_count} times")
            return
    raise AssertionError(
        "no nonzero threshold in the ladder produced a single fill, so the "
        "agreement sweep above is vacuous"
    )
