"""The four features' arithmetic, pinned case by case on the readable
implementation.

Every number in the OFI tests is computed by hand from
04-RESEARCH-NOTES.md Q1's three-case table, not from a second
implementation -- an oracle that shares code with the thing it checks is
not an oracle. `tests/features/test_kernel.py` then proves the `@njit`
kernel is bitwise identical to this, so these cases cover both.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from data.ingest.trade_side import SIGN_MAKER_IS_SELLER
from data.time_ns import QTY_SCALE, TRADE_FLOW_WINDOW_NS
from features.event_stream import decision_row_index
from features.reference import (
    STATUS_BOTH_TOP_SIZES_ZERO,
    STATUS_OK,
    STATUS_QTY_NOT_REPRESENTABLE,
    FeatureStatusError,
    new_outputs,
    new_reference_state,
    run_reference,
    run_reference_checked,
)
from tests.fixtures.event_streams import build_events, quote, trade

SECOND = TRADE_FLOW_WINDOW_NS

#: The quote every OFI case starts from. Distinct sizes on the two sides,
#: so a sign error cannot cancel out.
PREV = dict(bid_price=100.0, bid_qty=5.0, ask_price=101.0, ask_qty=7.0)


def _run(rows, *, sort=True):
    return run_reference_checked(build_events(rows, sort=sort))


def _two_quote_ofi(second_quote):
    """`e_n` for a two-quote stream: PREV then `second_quote`."""
    out = _run(
        [
            quote(
                0,
                PREV["bid_price"],
                PREV["bid_qty"],
                PREV["ask_price"],
                PREV["ask_qty"],
            ),
            quote(SECOND, *second_quote),
        ]
    )
    return out["ofi"][1]


# --------------------------------------------------------------------------
# ofi
# --------------------------------------------------------------------------


def test_ofi_repeated_identical_quote_is_exactly_zero():
    """Not `pytest.approx`. The four independent indicators make an
    unchanged quote cancel term for term -- `qb - qb` then `-qa + qa` -- so
    the result is exactly 0.0, with no branch and no floating-point
    residue. A `==` here is the assertion that no elif chain crept in to
    "handle" the case.
    """
    value = _two_quote_ofi(
        (PREV["bid_price"], PREV["bid_qty"], PREV["ask_price"], PREV["ask_qty"])
    )
    assert value == 0.0
    assert not math.isnan(value)


def test_ofi_first_quote_of_the_partition_is_nan_not_zero():
    """`e_1` is UNDEFINED, never neutral: there is no previous quote, and
    emitting 0.0 would manufacture a "no imbalance" observation that was
    never measured. Costs 1 row in 17.2M on a real day; the alternative is
    seeding from the previous day, which couples partitions.
    """
    out = _run(
        [
            quote(0, 100.0, 5.0, 101.0, 7.0),
            trade(SECOND // 2, 100.5, 1.0, 1),
            quote(SECOND, 100.1, 5.0, 101.0, 7.0),
        ]
    )
    assert math.isnan(out["ofi"][0])
    assert math.isnan(out["ofi"][1]), "the value must stay NaN until a SECOND quote"
    assert not math.isnan(out["ofi"][2])


@pytest.mark.parametrize(
    ("case", "second_quote", "expected"),
    [
        # bid side, holding the ask identical so its resize term is 0
        ("bid price-improving -> +qb_n", (100.1, 3.0, 101.0, 7.0), 3.0),
        (
            "bid resized at same price -> +(qb_n - qb_n-1)",
            (100.0, 9.0, 101.0, 7.0),
            4.0,
        ),
        ("bid swept/pulled -> -qb_n-1", (99.9, 2.0, 101.0, 7.0), -5.0),
        # ask side, holding the bid identical
        ("ask price-improving -> -qa_n", (100.0, 5.0, 100.9, 4.0), -4.0),
        (
            "ask resized at same price -> -(qa_n - qa_n-1)",
            (100.0, 5.0, 101.0, 10.0),
            -3.0,
        ),
        ("ask swept/pulled -> +qa_n-1", (100.0, 5.0, 101.1, 6.0), 7.0),
    ],
)
def test_ofi_three_cases_per_side(case, second_quote, expected):
    """The six rows of the CKS three-case table (04-RESEARCH-NOTES.md Q1),
    each arithmetic computed by hand from PREV = (100.0, 5.0, 101.0, 7.0).

    This is the test that an `if/elif` chain cannot survive: a chain that
    treats an unchanged price as "contribute 0" still passes the
    repeated-quote test above, and fails on the two resize rows here.
    """
    assert _two_quote_ofi(second_quote) == expected, case


def test_ofi_sign_agrees_with_trade_side():
    """Both "buy pressure" columns point the same way, so a model never has
    to learn that they disagree about which way is up.

    The easy inversion, called out in Q1: a FALLING ask is NEGATIVE,
    because CKS classes `a_n < a_n-1` as an increase in supply.
    """
    assert SIGN_MAKER_IS_SELLER == 1, "the aggressor-bought sign this pins against"

    price_improving_bid = _two_quote_ofi((100.1, 3.0, 101.0, 7.0))
    falling_ask = _two_quote_ofi((100.0, 5.0, 100.9, 4.0))
    assert price_improving_bid > 0
    assert falling_ask < 0

    buy_flow = _run(
        [quote(0, 100.0, 5.0, 101.0, 7.0), trade(1, 100.5, 2.0, SIGN_MAKER_IS_SELLER)]
    )["trade_flow"][-1]
    assert buy_flow > 0, "an aggressor-bought trade is positive signed flow"


# --------------------------------------------------------------------------
# trade_flow
# --------------------------------------------------------------------------


def test_trade_flow_window_is_half_open():
    """`(t - 1s, t]`: the trade at exactly `t` is inside, the trade at
    exactly `t - 1s` is not, and one nanosecond later it is (D-04-16).

    Three streams that differ only in one trade's etime, so the assertion
    is about the endpoint and nothing else.
    """
    base = [quote(0, 100.0, 5.0, 101.0, 7.0), trade(SECOND, 100.5, 0.25, 1)]

    at_open_end = _run([*base, trade(0, 100.5, 0.5, 1)])
    one_ns_inside = _run([*base, trade(1, 100.5, 0.5, 1)])
    at_close_end = _run(base)

    k = -1
    assert at_open_end["trade_flow"][k] == 0.25, (
        "a trade at exactly t - 1s must be OUTSIDE the window"
    )
    assert one_ns_inside["trade_flow"][k] == 0.75, (
        "a trade one ns after t - 1s must be INSIDE the window"
    )
    assert at_close_end["trade_flow"][k] == 0.25, (
        "the trade at exactly t must be INSIDE the window"
    )


def test_trade_flow_unknown_side_contributes_zero():
    """D-04-12: an unknown-side trade keeps its row and contributes 0 to
    signed flow -- it is not dropped, and it is not guessed at."""
    known = _run([quote(0, 100.0, 5.0, 101.0, 7.0), trade(1, 100.5, 3.0, 1)])
    unknown = _run([quote(0, 100.0, 5.0, 101.0, 7.0), trade(1, 100.5, 3.0, 0)])
    assert known["trade_flow"][-1] == 3.0
    assert unknown["trade_flow"][-1] == 0.0


def test_trade_flow_empty_window_is_zero_not_nan():
    """ "No signed volume traded in the last second" is a measurement, not a
    gap. The count of such rows comes back in the state, because the column
    alone cannot distinguish it from flow exactly cancelling."""
    events = build_events(
        [
            quote(0, 100.0, 5.0, 101.0, 7.0),
            trade(1, 100.5, 2.0, 1),
            quote(5 * SECOND, 100.1, 5.0, 101.0, 7.0),
        ]
    )
    state = new_reference_state()
    out = run_reference_checked(events, state=state)
    assert out["trade_flow"][-1] == 0.0
    assert not math.isnan(out["trade_flow"][-1])
    assert state.empty_window_rows == 2, "row 0 and the post-gap row"

    cancelled = _run(
        [
            quote(0, 100.0, 5.0, 101.0, 7.0),
            trade(1, 100.5, 2.0, 1),
            trade(2, 100.5, 2.0, -1),
        ]
    )
    assert cancelled["trade_flow"][-1] == 0.0, (
        "indistinguishable from the empty window in the column alone -- "
        "which is why the count is state, not a value"
    )


def _tie_order_streams(n=4000, span=3 * SECOND):
    """One stream built twice: quotes-first and trades-first at every tied
    `etime`. Same rows, same etimes -- only the tie order differs.

    Many rows on purpose, and TIE-DENSE on purpose: the difference a float64
    accumulator makes is a last-bit rounding difference, and it can only
    appear where a tie lets the eviction subtractions and the push addition
    swap places. The etime grid below gives ~2.7 rows per distinct etime,
    the real day's ratio (18.58M rows over 6.86M etimes). An earlier version
    of this fixture drew etimes from a grid so fine that only 3 of 4000 rows
    tied -- the two "orders" were the same stream, and the float64 mutation
    survived the test. On the real day the float64 accumulator moved 95% of
    decision rows by at most 1.17e-13.
    """
    rng = np.random.default_rng(20260919)
    rows = []
    n_etimes = 1500
    for i in range(n):
        etime = int(rng.integers(0, n_etimes)) * (span // n_etimes)
        if i % 2:
            qty = int(rng.integers(1, 10**10)) / 1e8
            rows.append(trade(etime, 100.5, qty, int(rng.choice([-1, 1]))))
        else:
            rows.append(
                quote(
                    etime,
                    100.0,
                    int(rng.integers(1, 10**9)) / 1e8,
                    101.0,
                    int(rng.integers(1, 10**9)) / 1e8,
                )
            )
    quotes_first = sorted(rows, key=lambda r: (r["etime"], r["source_rank"]))
    trades_first = sorted(rows, key=lambda r: (r["etime"], -r["source_rank"]))
    return (
        build_events(quotes_first, sort=False),
        build_events(trades_first, sort=False),
    )


def test_trade_flow_is_bit_identical_under_either_tie_order():
    """D-04-14. The merge convention must not be able to change a feature
    value: in float64 it does, on 95% of a real day's decision rows, and
    that would make reproducibility depend on which frame is the left
    argument of `merge_sorted`.

    Compared at the LAST row of each distinct `etime` -- the decision rows.
    The two orders put different rows there, but the same etimes.
    """
    quotes_first, trades_first = _tie_order_streams()

    a = run_reference_checked(quotes_first)
    b = run_reference_checked(trades_first)
    ka = decision_row_index(quotes_first["etime"])
    kb = decision_row_index(trades_first["etime"])

    assert np.array_equal(quotes_first["etime"][ka], trades_first["etime"][kb])
    assert len(ka) > 1000, "the fixture must exercise many decision rows"
    differing = int(np.sum(a["trade_flow"][ka] != b["trade_flow"][kb]))
    assert differing == 0, (
        f"{differing} of {len(ka)} decision rows' trade_flow depend on the "
        "etime tie order -- the accumulator is not exact"
    )


def test_trade_flow_refuses_a_qty_it_cannot_represent_exactly():
    """ "Every real qty is integral at 1e-8" is a MEASUREMENT, and an
    unasserted measured premise is an assumption. A qty that the int64
    fixed point cannot round-trip stops the run with the offending row."""
    events = build_events(
        [quote(0, 100.0, 5.0, 101.0, 7.0), trade(1, 100.5, 0.123456789, 1)]
    )
    with pytest.raises(FeatureStatusError) as excinfo:
        run_reference_checked(events)
    assert "not integral at 1e-8" in str(excinfo.value)
    assert "trade_qty=0.123456789" in str(excinfo.value)

    assert QTY_SCALE == 100_000_000


# --------------------------------------------------------------------------
# mid / imb_top
# --------------------------------------------------------------------------


def test_imb_top_zero_on_both_sides_raises():
    """D-04-16: an ASSERTION, not a branch. Measured 0 occurrences in 17.2M
    real rows, so a null-propagation path for it would be untested code."""
    events = build_events([quote(0, 100.0, 0.0, 101.0, 0.0)])
    with pytest.raises(FeatureStatusError) as excinfo:
        run_reference_checked(events)
    assert "imb_top is undefined" in str(excinfo.value)
    assert "at row 0" in str(excinfo.value)


def test_mid_and_imb_top_are_nan_before_the_first_quote():
    """The 2026-09-12 shape: L1 capture starts at 06:37:10.882 UTC and every
    earlier decision row is trade-only. Those rows get NaN, not a carried or
    invented book."""
    out = _run(
        [
            trade(0, 100.5, 1.0, 1),
            trade(SECOND // 2, 100.5, 1.0, -1),
            quote(SECOND, 100.0, 5.0, 101.0, 7.0),
            trade(SECOND + 1, 100.5, 1.0, 1),
        ]
    )
    assert math.isnan(out["mid"][0])
    assert math.isnan(out["imb_top"][0])
    assert math.isnan(out["mid"][1])
    assert out["mid"][2] == 100.5
    assert out["imb_top"][2] == (5.0 - 7.0) / (5.0 + 7.0)
    assert out["mid"][3] == 100.5, "carried from the prevailing quote onto a trade row"
    assert out["trade_flow"][0] == 1.0, "trade_flow needs no book and has one"


def test_a_crossed_or_locked_book_is_counted_not_refused():
    """04-RESEARCH-NOTES.md ambiguity #7, resolved toward Phase 3: `mid` and
    `imb_top` stay arithmetically well-defined on a crossed book, and
    hard-failing here would make the feature tier stricter than the curated
    tier it reads."""
    events = build_events(
        [
            quote(0, 101.0, 5.0, 101.0, 7.0),  # locked
            quote(SECOND, 101.5, 5.0, 101.0, 7.0),  # crossed
            quote(2 * SECOND, 100.0, 5.0, 101.0, 7.0),  # normal
        ]
    )
    state = new_reference_state()
    out = run_reference_checked(events, state=state)
    assert state.crossed_locked_rows == 2
    assert out["mid"][0] == 101.0
    assert out["mid"][1] == 101.25


# --------------------------------------------------------------------------
# warmup and the state contract
# --------------------------------------------------------------------------


def test_warmup_is_one_flag_computed_once():
    """D-04-09: true until two L1 updates have been seen (so `ofi` is
    defined) AND a full window has elapsed since the partition's first
    event. It travels with the row; consumers never recompute it."""
    out = _run(
        [
            quote(0, 100.0, 5.0, 101.0, 7.0),
            quote(SECOND // 2, 100.1, 5.0, 101.0, 7.0),
            trade(SECOND - 1, 100.5, 1.0, 1),
            trade(SECOND, 100.5, 1.0, 1),
            quote(2 * SECOND, 100.2, 5.0, 101.0, 7.0),
        ]
    )
    assert list(out["warmup"]) == [True, True, True, False, False]
    assert math.isnan(out["ofi"][0]) and not out["warmup"][3]


@pytest.mark.parametrize("chunk", [1, 2, 3, 7])
def test_reference_resumes_mid_stream_with_identical_output(chunk):
    """The contract the kernel conforms to: stop anywhere, carry the state,
    resume, get byte-identical output."""
    events = build_events(
        [
            quote(i * SECOND // 4, 100.0 + 0.1 * (i % 5), 1.0 + i, 101.0, 2.0 + i)
            if i % 3
            else trade(i * SECOND // 4, 100.5, (i + 1) / 8, 1 if i % 2 else -1)
            for i in range(24)
        ]
    )
    n = events["etime"].shape[0]

    whole = run_reference_checked(events)

    out = new_outputs(n)
    state = new_reference_state()
    for start in range(0, n, chunk):
        stop = min(start + chunk, n)
        status = run_reference(
            *(
                events[name][start:stop]
                for name in (
                    "etime",
                    "source_rank",
                    "bid_price",
                    "bid_qty",
                    "ask_price",
                    "ask_qty",
                    "trade_qty",
                    "trade_side",
                )
            ),
            *(
                out[name][start:stop]
                for name in ("mid", "imb_top", "ofi", "trade_flow")
            ),
            out["warmup"][start:stop],
            state,
        )
        assert status == STATUS_OK

    for name in ("mid", "imb_top", "ofi", "trade_flow", "warmup"):
        assert np.array_equal(whole[name], out[name], equal_nan=name != "warmup"), name


def test_status_codes_are_negative_and_distinct():
    """`status < 0` is the whole error test at a call site, so a new code
    cannot be mistaken for success."""
    codes = (
        STATUS_BOTH_TOP_SIZES_ZERO,
        STATUS_QTY_NOT_REPRESENTABLE,
    )
    assert STATUS_OK == 0
    assert all(code < 0 for code in codes)
    assert len(set(codes)) == len(codes)
