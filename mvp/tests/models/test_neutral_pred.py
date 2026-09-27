"""`models.conversion`: the one return-to-price site, and the neutral fill
that makes an unpredicted row harmless instead of fatal.

WHY "NEUTRAL" NEEDS PROVING RATHER THAN ASSERTING. The kernel refuses a
non-finite `pred` outright (`STATUS_NON_FINITE_PRED`), so a row the model
could not score has to be given SOME price, and the price chosen has to be one
that cannot fabricate a trade. `mid` is that price -- measured, on both real
candidate `val` windows and here -- and the mechanism is the kernel's
symmetric floor/ceil rule: the mid lies inside the spread, and at the minimum
one-tick spread it sits exactly on the half tick that triggers NEITHER side.

THE ANTI-VACUITY COUNTERPART IS THE OTHER HALF OF THE SAME CLAIM. "pred = mid
never trades" says nothing unless the same fixture, the same frame and the
same kernel DO trade when the prediction crosses a quote -- so the full-tick
test below asserts exact counts on both sides rather than "more than zero".

Every test uses `tests/models/conftest.py`'s `tmp_path` lake, registry and
MLflow tracking root. Nothing here reaches a canonical root, so no validation
look is spent (D-07-34).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

from harness.accessor import materialize
from models.conversion import neutral_fill_null_predictions, pred_return_to_price
from sim.arrays import sim_arrays
from sim.kernel import run_sim_checked
from sim.outputs import SimResult
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED
from tests.fixtures.model_span import build_model_span_fixture

#: One tick in raw USDT price units -- the scale `pred` arrives in, by the
#: same fixed-point route `tests/sim/`'s `_price_at_ticks` takes.
ONE_TICK: float = TICK_SIZE_SCALED / PRICE_SCALE

RUN_TAGS: dict[str, str] = {
    "code_hash": "a" * 40,
    "data_hash": "none",
    "seed": "0",
    "env_hash": "b" * 64,
    "model_class": "none (conversion test, no model fitted)",
}


def _val_frame(lake_root: Path, registry_root: Path, tracking_root: Path):
    """One accessor look at the fixture's `val` segment, `tmp_path` roots
    only."""
    rig = build_model_span_fixture(lake_root, registry_root, tracking_root)
    return materialize(
        rig["manifest"]["manifest_id"],
        "val",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )


def _simulate(frame: pl.DataFrame, pred: np.ndarray) -> SimResult:
    arrays = sim_arrays(frame)
    return run_sim_checked(
        arrays["etime"],
        arrays["bid_ticks"],
        arrays["ask_ticks"],
        np.ascontiguousarray(pred, dtype=np.float64),
        x_bps=0,
    )


def test_pred_equal_to_mid_produces_zero_trades(
    lake_root, registry_root, tracking_root
):
    """The premise the neutral fill rests on, arrived at THROUGH THE
    CONVERSION rather than by writing `mid` directly: a predicted return of
    exactly zero converts to exactly `mid`, and `mid` never trades.

    Both halves are asserted, because only together do they say anything. The
    bit-identity (`array_equal`, not `allclose`) is what makes the zero-trade
    result a statement about the conversion and not about a value that merely
    rounds to the mid: `mid * (1.0 + 0.0)` is `mid` exactly in IEEE 754.

    `test_a_full_tick_offset_above_the_ask_does_trade_and_below_the_bid_does_too`
    is the counterpart proving this fixture can be made to trade at all."""
    val = _val_frame(lake_root, registry_root, tracking_root)
    mid = val["mid"].to_numpy()
    zero_return = np.zeros(val.height, dtype=np.float64)

    at_mid = pred_return_to_price(mid, zero_return)
    assert np.array_equal(at_mid, mid), (
        "a zero predicted return did not convert to exactly the mid -- the "
        "neutral fill's whole claim is bit-identity, not proximity"
    )

    result = _simulate(val, at_mid)
    print(
        f"pred = mid * (1 + 0.0) on {val.height} val rows: {result.fill_count} trades"
    )
    assert result.fill_count == 0, (
        "the neutral prediction traded -- a row with no prediction would be "
        "generating trades, which reads downstream as model behaviour"
    )
    # Anti-vacuity for the FIXTURE's own geometry: the mid must really sit on
    # the half tick this result depends on, not merely happen to.
    bid_ticks = sim_arrays(val)["bid_ticks"]
    ask_ticks = sim_arrays(val)["ask_ticks"]
    assert np.array_equal(ask_ticks - bid_ticks, np.ones(val.height, dtype=np.int64)), (
        "the fixture's spread is not one tick everywhere, so 'the mid is a "
        "half tick' is not the reason this produced zero trades"
    )


def test_a_full_tick_offset_above_the_ask_does_trade_and_below_the_bid_does_too(
    lake_root, registry_root, tracking_root
):
    """The anti-vacuity counterpart, on BOTH sides, with EXACT counts -- and
    the exact counts are where the trap is.

    A constant `ask + 1 tick` on every row yields exactly ONE trade, and that
    number is not a weak signal misread as a strong one: the kernel is
    FLIP-ONLY, so after the row-0 long entry the same-direction trigger is
    never even evaluated again. `bid - 1 tick` is the mirror: one short entry
    and nothing after it. 07-03 nearly accepted a one-sided signal's single
    trade as proof that a signal worked, which is why both the count and the
    SIDE are asserted here, and why the alternating case follows.

    Alternating the two gives a flip on every row -- `fill_count == height`,
    with sides strictly alternating `+1, -1, +1, ...`. That is the measurement
    that proves both triggers fire repeatedly on this frame, so
    `pred = mid`'s zero is a property of the mid and not of a fixture that
    cannot trade.

    NOTE WHAT IS NOT USED: `mid + 1 tick`. At a one-tick spread that is half a
    tick above the ASK, and the long trigger's `floor(pred) > ask_ticks`
    rounds it back down onto the ask -- zero trades. "One tick beyond" means
    one tick beyond the QUOTE, never one tick beyond the mid.
    """
    val = _val_frame(lake_root, registry_root, tracking_root)
    bid = val["bid_price"].to_numpy()
    ask = val["ask_price"].to_numpy()
    height = val.height

    long_only = _simulate(val, ask + ONE_TICK)
    short_only = _simulate(val, bid - ONE_TICK)
    alternating = _simulate(
        val, np.where(np.arange(height) % 2 == 0, ask + ONE_TICK, bid - ONE_TICK)
    )
    print(
        f"one tick beyond the quote on {height} val rows: "
        f"long-only={long_only.fill_count} short-only={short_only.fill_count} "
        f"alternating={alternating.fill_count}"
    )

    assert long_only.fill_count == 1, (
        "a constant above-the-ask prediction must enter long once and then "
        "never place a same-direction order again (the kernel is flip-only)"
    )
    assert int(long_only.trade_log["side"][0]) == 1
    assert int(long_only.trade_log["position_after"][0]) == 1

    assert short_only.fill_count == 1
    assert int(short_only.trade_log["side"][0]) == -1
    assert int(short_only.trade_log["position_after"][0]) == -1

    assert alternating.fill_count == height, (
        "alternating one tick beyond each quote must flip on every row; got "
        f"{alternating.fill_count} of {height}"
    )
    sides = alternating.trade_log["side"][: alternating.fill_count]
    expected = np.where(np.arange(height) % 2 == 0, 1, -1).astype(np.int8)
    assert np.array_equal(sides, expected), "the flips did not alternate by side"


def test_every_prediction_null_raises_rather_than_reporting_a_clean_zero_trade_run():
    """An all-NaN prediction column would substitute the neutral mid on every
    row, simulate cleanly, and report zero trades -- reading as "a model with
    no signal" when the truth is "a pipeline that predicted nothing". That is
    the one case the count cannot rescue, because nobody reads a count they
    were not warned about, so it is REFUSED.

    No lake, no simulator: the refusal is a property of the function."""
    mid = np.full(500, 70_000.05, dtype=np.float64)
    with pytest.raises(ValueError, match="every one of 500 predictions"):
        neutral_fill_null_predictions(np.full(500, np.nan), mid)

    # Anti-vacuity, and the boundary: 499 of 500 is NOT refused, and the count
    # says so. One real prediction is a model; zero is a pipeline failure.
    almost = np.full(500, np.nan)
    almost[123] = 1e-5
    price, count = neutral_fill_null_predictions(almost, mid)
    assert count == 499
    assert price[123] > mid[123]
    with pytest.raises(ValueError, match="column is empty"):
        neutral_fill_null_predictions(np.empty(0), np.empty(0))


def test_the_neutral_fill_counts_exactly_the_non_finite_rows_and_leaves_the_rest():
    """The count is the reason this is a function and not an inline
    `np.where`, so it is asserted as an EXACT number against deliberately
    injected non-finite rows of all three kinds -- NaN, +inf and -inf. A
    substitution count that under-reported by one, or that returned a constant
    0, would pass a `>= 0` assertion forever.

    The substituted rows must equal `mid` BIT FOR BIT, and the untouched rows
    must equal the plain conversion bit for bit: the fill changes exactly the
    rows it counts and no others."""
    rows = 1_000
    mid = 70_000.05 + np.arange(rows, dtype=np.float64) * 0.1
    pred_return = 1e-6 * np.sin(np.arange(rows, dtype=np.float64))
    injected = np.array([0, 7, 500, 999])
    pred_return[injected] = [np.nan, np.inf, -np.inf, np.nan]

    price, count = neutral_fill_null_predictions(pred_return, mid)

    assert count == injected.size, f"counted {count}, injected {injected.size}"
    assert np.array_equal(price[injected], mid[injected]), (
        "a substituted row is not exactly the mid"
    )
    kept = np.setdiff1d(np.arange(rows), injected)
    assert np.array_equal(
        price[kept], pred_return_to_price(mid[kept], pred_return[kept])
    ), "a row that was not substituted came out different from the plain conversion"
    # ...and the untouched rows really did move, or "unchanged" is vacuous.
    assert not np.array_equal(price[kept], mid[kept])


def test_the_return_to_price_conversion_round_trips_to_float64_roundoff():
    """`(price / mid) - 1.0` recovers the return, and the tolerance is stated
    in ULPs of the operands rather than as a round number: this is the
    conversion's own round-trip proof, in the register of
    `sim.ticks.price_to_ticks`, which asserts its round trip on every value it
    converts rather than assuming it.

    The returns swept here span the range that matters -- one tick at a
    $70,000 mid is about 1.4e-6 in return units, so the interesting scale is
    1e-7 to 1e-3, not 0.1."""
    mid = np.array([70_000.05, 100.0, 1.5, 12_345.67, 0.1], dtype=np.float64)
    for magnitude in (1e-9, 1e-7, 1.4e-6, 1e-4, 1e-3, 1e-1):
        for sign in (1.0, -1.0):
            pred_return = np.full(mid.shape, sign * magnitude)
            price = pred_return_to_price(mid, pred_return)
            recovered = (price / mid) - 1.0
            error = np.abs(recovered - pred_return)
            tolerance = 4.0 * np.spacing(np.abs(pred_return) + 1.0)
            assert np.all(error <= tolerance), (
                f"round trip at {sign * magnitude}: error {error.max()} "
                f"exceeds {tolerance.max()}"
            )
    # The direction is not symmetric-by-accident either: a positive return
    # must raise the price and a negative one must lower it, on every row.
    assert np.all(pred_return_to_price(mid, np.full(mid.shape, 1e-4)) > mid)
    assert np.all(pred_return_to_price(mid, np.full(mid.shape, -1e-4)) < mid)


def test_the_conversion_refuses_a_swapped_call_a_float32_input_and_a_ragged_pair():
    """Three refusals, each for a mistake that is silent without it.

    A SWAPPED call -- `pred_return_to_price(pred_return, mid)` -- passes
    near-zero returns as the midprice. Without the strictly-positive check
    that simulates a $1-scale instrument and reports a plausible P&L. (The
    two functions in this module take their arguments in opposite orders,
    because `mid` is the thing being scaled in one and the fallback in the
    other, so this refusal is load-bearing rather than defensive.)

    A float32 prediction is the hazard D-07-16 pins the stored column to
    float64 for: the kernel quantises with a symmetric floor/ceil rule and a
    float32 rounding error near the half-tick boundary flips a trigger. It is
    refused, never upcast -- an upcast repairs the symptom and keeps the
    defect.

    A LENGTH mismatch means one of the two arrays is a different frame."""
    mid = np.array([70_000.05, 70_000.15], dtype=np.float64)
    pred_return = np.array([1e-5, -1e-5], dtype=np.float64)

    with pytest.raises(ValueError, match="strictly positive"):
        pred_return_to_price(pred_return, mid)
    with pytest.raises(ValueError, match="must be float64"):
        pred_return_to_price(mid, pred_return.astype(np.float32))
    with pytest.raises(ValueError, match="does not match"):
        pred_return_to_price(mid, pred_return[:1])
    with pytest.raises(ValueError, match="not finite"):
        pred_return_to_price(mid, np.array([np.nan, 1e-5]))
