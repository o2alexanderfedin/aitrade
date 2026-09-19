"""FEAT-03: no feature value at `t` can see a row after `t`, and each
feature IS sensitive inside the window the catalogue declares for it.

D-04-08 says the leakage proof is a PROPERTY test and that it must be red
before the kernel is written -- which it was: this file was committed to
disk before `features/reference.py` existed and failed at import.

Three kinds of property live here, and the third is the one that gives the
first two meaning:

1. INVARIANCE -- permuting or deleting rows strictly after `t` cannot
   change any feature value at or before `t`.
2. ANTI-VACUITY -- a row INSIDE a feature's declared window can change it.
   Without this, (1) passes on a function that returns a constant.
3. CONTAINMENT -- the set of rows that CAN change a feature at `t`,
   measured by perturbing them one at a time, is inside what the catalogue
   declares. A declared information set the code exceeds is a leakage
   claim nobody checked.

These run against `features.reference`, not `features.kernel`: the two are
proved bit-identical in `tests/features/test_kernel.py`, and the reference
imports in milliseconds where a JIT dispatch per hypothesis example would
make this suite unusable.

WHAT `information_set = "t"` MEANS HERE. `mid` and `imb_top` read the
PREVAILING top of book -- the most recent L1 update at or before `t`. On a
decision row that is a trade (329,580 of a real day's 6,864,853), that
update is older than `t`. The catalogue note says so in those words; the
`_ALLOWED_SOURCE_ROWS` table below is that sentence made executable.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from data.time_ns import TRADE_FLOW_WINDOW_NS
from features.event_stream import SOURCE_RANK_BOOKTICKER, decision_row_index
from features.reference import FEATURE_OUTPUT_NAMES, run_reference_checked
from spec.catalogue import get_feature
from tests.fixtures.event_streams import build_events, quote, trade

FEATURE_NAMES = ("mid", "imb_top", "ofi", "trade_flow")


def _features(events):
    """Every feature at every row, as a plain dict of arrays."""
    return run_reference_checked(events)


def _same(a, b):
    """Bitwise equality with NaN == NaN -- never `allclose`. A silent drift
    is exactly what a tolerance hides."""
    return np.array_equal(a, b, equal_nan=True)


def _slice_events(events, stop):
    return {name: array[:stop].copy() for name, array in events.items()}


# --------------------------------------------------------------------------
# 1. Invariance: the future cannot reach backwards
# --------------------------------------------------------------------------


@st.composite
def _event_stream(draw, max_rows=40, span_ns=3_000_000_000):
    """A valid merged stream: ascending etimes (ties allowed), quotes with a
    positive size on each side, trades with integral-at-1e-8 quantities."""
    n = draw(st.integers(min_value=2, max_value=max_rows))
    etimes = sorted(
        draw(
            st.lists(
                st.integers(min_value=0, max_value=span_ns),
                min_size=n,
                max_size=n,
            )
        )
    )
    rows = []
    for etime in etimes:
        is_trade = draw(st.booleans())
        if is_trade:
            qty = draw(st.integers(min_value=0, max_value=500_000_000)) / 1e8
            side = draw(st.sampled_from([-1, 0, 1]))
            price = draw(st.integers(min_value=900, max_value=1100)) / 10.0
            rows.append(trade(etime, price, qty, side))
        else:
            bid = draw(st.integers(min_value=900, max_value=1100)) / 10.0
            spread = draw(st.integers(min_value=1, max_value=5)) / 10.0
            bid_qty = draw(st.integers(min_value=1, max_value=1_000_000)) / 1e8
            ask_qty = draw(st.integers(min_value=1, max_value=1_000_000)) / 1e8
            rows.append(quote(etime, bid, bid_qty, bid + spread, ask_qty))
    return build_events(rows)


def _permute_tail_payloads(events, k, order_seed):
    """Permute every row strictly after `k`, within its own stream.

    Permuting WITHIN a stream is what keeps the permuted tail valid: the
    `etime` column and the `(etime, source_rank, seq)` total order are
    untouched, so the result is a stream the merge could genuinely have
    produced, while every quote after `k` now carries a different quote's
    prices and sizes and every trade a different trade's size and side.
    """
    out = {name: array.copy() for name, array in events.items()}
    n = out["etime"].shape[0]
    payload = {
        SOURCE_RANK_BOOKTICKER: ("bid_price", "bid_qty", "ask_price", "ask_qty"),
        1: ("trade_price", "trade_qty", "trade_side"),
    }
    rng = np.random.default_rng(order_seed)
    for rank, columns in payload.items():
        positions = [i for i in range(k + 1, n) if out["source_rank"][i] == rank]
        if len(positions) < 2:
            continue
        permuted = list(rng.permutation(positions))
        for column in columns:
            values = events[column][positions]
            out[column][permuted] = values
    return out


@settings(deadline=None, max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(events=_event_stream(), seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_future_shuffle_cannot_change_a_feature_at_t(events, seed):
    """Permuting every row after a decision row `k` leaves every feature
    value at every decision row `<= k` bit-identical."""
    decisions = decision_row_index(events["etime"])
    k = int(decisions[len(decisions) // 2])

    baseline = _features(events)
    shuffled = _features(_permute_tail_payloads(events, k, seed))

    past = decisions[decisions <= k]
    for name in FEATURE_OUTPUT_NAMES:
        assert _same(baseline[name][past], shuffled[name][past]), (
            f"{name} at a decision row <= {k} changed when rows after {k} were permuted"
        )


@settings(deadline=None, max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(events=_event_stream(), cut=st.floats(min_value=0.0, max_value=1.0))
def test_future_deletion_cannot_change_a_feature_at_t(events, cut):
    """Deleting an arbitrary suffix leaves the surviving decision rows'
    feature values bit-identical -- and the surviving decision rows are the
    same rows, because `decision_row_index` is a pure function of position.
    """
    n = events["etime"].shape[0]
    decisions = decision_row_index(events["etime"])
    k = int(decisions[min(int(cut * len(decisions)), len(decisions) - 1)])

    baseline = _features(events)
    truncated = _features(_slice_events(events, k + 1))

    assert k + 1 <= n
    past = decisions[decisions <= k]
    assert _same(decision_row_index(events["etime"][: k + 1]), past)
    for name in FEATURE_OUTPUT_NAMES:
        assert _same(baseline[name][past], truncated[name][past]), (
            f"{name} at a decision row <= {k} changed when every row after "
            f"{k} was deleted"
        )


# --------------------------------------------------------------------------
# 2 + 3. Anti-vacuity and containment
# --------------------------------------------------------------------------

_SECOND = TRADE_FLOW_WINDOW_NS


def _crafted_stream():
    """A deliberately shaped stream: quotes and trades interleaved over ~4 s
    so that the 1 s window fills, empties and refills, with one `etime` tie
    and a wide spread (perturbing a price cannot accidentally cross the
    book, which would make the test measure a different thing)."""
    rows = [
        quote(0 * _SECOND // 10, 100.0, 0.5, 101.0, 0.7),
        trade(1 * _SECOND // 10, 100.5, 0.25, 1),
        quote(2 * _SECOND // 10, 100.1, 0.6, 101.1, 0.4),
        trade(4 * _SECOND // 10, 100.5, 0.75, -1),
        quote(6 * _SECOND // 10, 100.2, 0.3, 101.3, 0.9),
        trade(6 * _SECOND // 10, 100.6, 0.125, 1),
        quote(9 * _SECOND // 10, 100.0, 0.8, 101.0, 0.2),
        trade(12 * _SECOND // 10, 100.4, 1.5, -1),
        quote(15 * _SECOND // 10, 100.3, 0.45, 101.2, 0.55),
        trade(18 * _SECOND // 10, 100.7, 0.0625, 1),
        quote(21 * _SECOND // 10, 100.4, 0.35, 101.4, 0.65),
        trade(23 * _SECOND // 10, 100.8, 2.25, 1),
        quote(25 * _SECOND // 10, 100.5, 0.15, 101.5, 0.85),
        trade(27 * _SECOND // 10, 100.9, 0.5, -1),
        quote(30 * _SECOND // 10, 100.6, 0.95, 101.6, 0.05),
        trade(36 * _SECOND // 10, 101.0, 0.75, 1),
        quote(40 * _SECOND // 10, 100.7, 0.25, 101.7, 0.75),
    ]
    return build_events(rows)


def _perturbations(events, j):
    """Every single-field perturbation of row `j` worth trying, as full
    copies of the stream. The union of what they move is the measured
    sensitivity of row `j`."""
    if events["source_rank"][j] == SOURCE_RANK_BOOKTICKER:
        columns = (
            ("bid_price", 0.1),
            ("bid_qty", 1.0),
            ("ask_price", 0.1),
            ("ask_qty", 1.0),
        )
    else:
        columns = (("trade_qty", 1.0), ("trade_side", -2.0))
    for column, delta in columns:
        perturbed = {name: array.copy() for name, array in events.items()}
        if column == "trade_side":
            perturbed[column][j] = np.int8(1 if events[column][j] != 1 else -1)
        else:
            perturbed[column][j] = events[column][j] + delta
        yield perturbed


def _measured_sources(events):
    """`{feature: {decision_row: {row indices that can change it}}}`."""
    baseline = _features(events)
    decisions = decision_row_index(events["etime"])
    measured = {name: {int(k): set() for k in decisions} for name in FEATURE_NAMES}

    for j in range(events["etime"].shape[0]):
        for perturbed in _perturbations(events, j):
            moved = _features(perturbed)
            for name in FEATURE_NAMES:
                for k in decisions:
                    if j > k:
                        continue
                    if not _same(baseline[name][k], moved[name][k]):
                        measured[name][int(k)].add(j)
    return measured, decisions


def _last_two_l1_rows(events, k):
    """The indices of the most recent and second-most-recent L1 update at
    or before `k` (`-1` when there is none)."""
    quotes = [
        i for i in range(k + 1) if events["source_rank"][i] == SOURCE_RANK_BOOKTICKER
    ]
    first = quotes[-1] if quotes else -1
    second = quotes[-2] if len(quotes) > 1 else -1
    return first, second


def _allowed_sources(events, declared, k):
    """The row indices `declared` permits a feature at `k` to depend on."""
    i1, i2 = _last_two_l1_rows(events, k)
    if declared == "t":
        return {k, i1}
    if declared == "[prev_l1_update, t]":
        return {k, i1, i2}
    if declared == "[t-1s, t]":
        t = int(events["etime"][k])
        return {k} | {
            j
            for j in range(k + 1)
            if t - TRADE_FLOW_WINDOW_NS < int(events["etime"][j]) <= t
        }
    raise AssertionError(f"no executable reading of information_set {declared!r}")


def test_declared_information_set_matches_the_measured_lookback():
    """For each catalogued feature, every row that CAN change its value at a
    decision row is inside the `information_set` the catalogue declares.

    Names are literals: `tools/check_catalogue_completeness.py` rejects a
    dynamically-constructed `get_feature` argument, which is what stops this
    test drifting away from the catalogue it is checking.
    """
    declared = {
        "mid": get_feature("mid").information_set,
        "imb_top": get_feature("imb_top").information_set,
        "ofi": get_feature("ofi").information_set,
        "trade_flow": get_feature("trade_flow").information_set,
    }
    assert declared == {
        "mid": "t",
        "imb_top": "t",
        "ofi": "[prev_l1_update, t]",
        "trade_flow": "[t-1s, t]",
    }

    events = _crafted_stream()
    measured, decisions = _measured_sources(events)

    for name in FEATURE_NAMES:
        for k in decisions:
            k = int(k)
            allowed = _allowed_sources(events, declared[name], k)
            excess = measured[name][k] - allowed
            assert not excess, (
                f"{name} at decision row {k} (etime={events['etime'][k]}) can be "
                f"changed by row(s) {sorted(excess)}, which its declared "
                f"information_set {declared[name]!r} does not permit"
            )


def test_a_feature_IS_sensitive_to_its_own_declared_window():
    """The anti-vacuity guard. Every invariance property above passes on a
    function that returns a constant; these assertions do not.

    Checked per feature, on the SAME measured sensitivity map the
    containment test uses, so the two cannot disagree about what was
    measured.
    """
    events = _crafted_stream()
    measured, decisions = _measured_sources(events)
    late = [int(k) for k in decisions if k >= 8]
    assert late, "the crafted stream must have decision rows past warm-up"

    for name in FEATURE_NAMES:
        assert any(measured[name][k] for k in late), (
            f"{name} is not sensitive to ANY row -- the invariance properties "
            "above would pass on a constant"
        )

    for k in late:
        i1, i2 = _last_two_l1_rows(events, k)
        assert i1 in measured["mid"][k], (
            f"mid at {k} ignores its prevailing quote (row {i1})"
        )
        assert i1 in measured["imb_top"][k], (
            f"imb_top at {k} ignores its prevailing quote (row {i1})"
        )
        assert i2 in measured["ofi"][k], (
            f"ofi at {k} ignores the L1 update before its prevailing one "
            f"(row {i2}) -- it would not be a quote DELTA"
        )
    # A trade INSIDE the declared window moves trade_flow; the same trade
    # pushed one nanosecond past the open end of the half-open window does
    # not. One assertion pair, because the window's length and its endpoint
    # convention are the same claim.
    k = int(decision_row_index(events["etime"])[-1])
    t = int(events["etime"][k])
    inside = [
        j
        for j in range(k + 1)
        if events["source_rank"][j] != SOURCE_RANK_BOOKTICKER
        and t - TRADE_FLOW_WINDOW_NS < int(events["etime"][j]) <= t
        and events["trade_side"][j] != 0
    ]
    assert inside, "the crafted stream must end with a non-empty trade window"
    assert set(inside) <= measured["trade_flow"][k], (
        "a trade inside trade_flow's declared 1 s window did not change it"
    )
    outside = [
        j
        for j in range(k + 1)
        if events["source_rank"][j] != SOURCE_RANK_BOOKTICKER
        and int(events["etime"][j]) <= t - TRADE_FLOW_WINDOW_NS
    ]
    assert outside, "the crafted stream must have trades older than the window"
    assert not (set(outside) & measured["trade_flow"][k]), (
        "a trade outside trade_flow's declared window changed it"
    )


def test_every_catalogued_feature_is_covered_by_these_properties():
    """The properties above name four features by literal. If a fifth is
    catalogued, this test fails rather than the suite silently proving
    nothing about it."""
    from spec.catalogue import load_features

    assert set(load_features()) == set(FEATURE_NAMES)


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_reference_emits_the_catalogued_feature_by_name(name):
    """The reference's output keys ARE the catalogue's feature names."""
    assert name in FEATURE_OUTPUT_NAMES
