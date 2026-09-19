"""FEAT-04: what `mid_{t+h}` means, and the three ways a label is absent.

`mid_{t+h}` names a row that does not exist -- no quote lands exactly at
`t + 10s`. D-04-05 locks the PREVAILING mid (the last L1 update with
`etime <= t+h`, exact matches allowed) and these tests are that sentence
made arithmetic: every fixture below is small enough that the expected
value is computed by hand in the assertion itself.

A label is the ONE value in this pipeline allowed to look forward. The
properties that bound HOW FAR forward now live in
`tests/leakage/test_label_information_set.py`, together with the feature
side and under the named `leakage-suite` CI gate -- a leakage proof that
only runs as part of a 900-test suite is one nobody can point at in a CI
log. The generator they share with the one property left here
(`label_problem`) lives in `tests/fixtures/leakage_streams.py`, so a bug
in it cannot make one suite pass while the other fails.

NULLS ARE NaN HERE. Inside `features/labels.py` an absent label is NaN;
`features/tier.py:write_feature_partition` is the single NaN -> null
conversion point on the way to a partition. These tests assert NaN, which
is the correct assertion for this side of that boundary.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings

from data.time_ns import (
    LABEL_HORIZON_NS,
    NS_PER_SECOND,
    RET_1MIN_NS,
    RET_10S_NS,
)
from features.labels import (
    LabelInputError,
    big_quote_gaps,
    compute_labels,
)
from tests.fixtures.leakage_streams import label_problem

H10 = "ret_10s_mid"
GAP_30S = 30 * NS_PER_SECOND


def _i64(values) -> np.ndarray:
    return np.asarray(values, dtype=np.int64)


def _f64(values) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


def _one_horizon(
    decision_etime,
    decision_mid,
    quote_etime,
    quote_mid,
    *,
    horizon_ns: int = RET_10S_NS,
    gap_threshold_ns: int = GAP_30S,
):
    """`compute_labels` over a single horizon named `H10`, returning
    `(label_array, stats)` -- the shape most of these tests want."""
    labels, stats = compute_labels(
        _i64(decision_etime),
        _f64(decision_mid),
        _i64(quote_etime),
        _f64(quote_mid),
        horizons_ns={H10: horizon_ns},
        gap_threshold_ns=gap_threshold_ns,
    )
    return labels[H10], stats


# --------------------------------------------------------------------------
# The as-of convention (D-04-05)
# --------------------------------------------------------------------------


def test_label_uses_the_prevailing_quote_not_the_next_one():
    """Quotes at `t+9s` and `t+11s`, horizon 10 s. The two conventions
    disagree IN SIGN here, so a wrong `side=` is not a rounding difference.
    """
    s = NS_PER_SECOND
    label, _ = _one_horizon(
        decision_etime=[0],
        decision_mid=[100.0],
        quote_etime=[0, 9 * s, 11 * s],
        quote_mid=[100.0, 99.0, 101.0],
    )
    assert label[0] == pytest.approx((99.0 - 100.0) / 100.0)
    assert label[0] < 0, (
        "the prevailing quote at t+9s is BELOW mid_t; the next-quote "
        "convention would report a POSITIVE return from the t+11s quote"
    )


def test_exact_match_at_t_plus_h_is_used():
    """A quote landing exactly on `t+h` is the prevailing one -- backward
    as-of WITH exact matches, unlike Phase 3's trade-side join."""
    s = NS_PER_SECOND
    label, _ = _one_horizon(
        decision_etime=[0],
        decision_mid=[100.0],
        quote_etime=[0, 10 * s, 10 * s + 1],
        quote_mid=[100.0, 102.0, 200.0],
    )
    assert label[0] == pytest.approx(0.02)


def test_exact_match_takes_the_LAST_quote_of_the_tie_group():
    """Many L1 updates share one `etime` on this venue (17.2M quotes over
    6.86M distinct etimes). The prevailing quote at `t+h` is the LAST of
    them -- the same last-row-of-the-etime rule the decision rows use."""
    s = NS_PER_SECOND
    label, _ = _one_horizon(
        decision_etime=[0],
        decision_mid=[100.0],
        quote_etime=[0, 10 * s, 10 * s, 10 * s, 20 * s],
        quote_mid=[100.0, 101.0, 102.0, 103.0, 104.0],
    )
    assert label[0] == pytest.approx(0.03), "the tie group's LAST mid is 103.0"


# --------------------------------------------------------------------------
# Null reason 1: past the end of available data
# --------------------------------------------------------------------------


def test_label_is_null_past_the_end_of_available_data():
    s = NS_PER_SECOND
    quote_etime = [i * s for i in range(21)]  # 0 .. 20s, one per second
    quote_mid = [100.0 + i for i in range(21)]
    decision_etime = quote_etime
    decision_mid = quote_mid

    label, stats = _one_horizon(decision_etime, decision_mid, quote_etime, quote_mid)

    # t + 10s <= 20s  ->  labelled; t + 10s > 20s  ->  NULL, never the last mid.
    for i in range(11):
        assert not math.isnan(label[i]), f"row {i} has a real t+10s quote"
        assert label[i] == pytest.approx(10.0 / quote_mid[i])
    for i in range(11, 21):
        assert math.isnan(label[i]), (
            f"row {i}'s horizon ends past the last quote -- a null, never the "
            f"carried-forward last mid {quote_mid[-1]}"
        )
    assert stats["per_horizon"][H10]["null_past_end"] == 10
    assert stats["per_horizon"][H10]["null_gap"] == 0
    assert stats["per_horizon"][H10]["null_no_mid"] == 0


def test_a_label_is_null_even_when_the_last_quote_is_the_prevailing_one():
    """The guard `searchsorted` alone cannot supply: with `t+h` past the
    end, the index happily points at the last quote and the arithmetic
    succeeds. That stale value is what the explicit guard refuses."""
    s = NS_PER_SECOND
    label, _ = _one_horizon(
        decision_etime=[0],
        decision_mid=[100.0],
        quote_etime=[0, 5 * s],
        quote_mid=[100.0, 150.0],
    )
    assert math.isnan(label[0]), (
        "without the past-end guard this row reports +50% from a quote 5s "
        "old, as if the last known price had held for the other 5s"
    )


# --------------------------------------------------------------------------
# Null reason 2: a gap longer than the threshold
# --------------------------------------------------------------------------


def test_big_quote_gaps_finds_the_intervals_and_is_strict_at_the_threshold():
    s = NS_PER_SECOND
    # steps of 29s, 30s, 31s: only the last is a big gap
    quote_etime = _i64([0, 29 * s, 59 * s, 90 * s])
    starts, ends = big_quote_gaps(quote_etime, GAP_30S)
    assert starts.tolist() == [59 * s]
    assert ends.tolist() == [90 * s]

    exactly_30 = _i64([0, 30 * s])
    starts, ends = big_quote_gaps(exactly_30, GAP_30S)
    assert starts.size == 0, "a gap of EXACTLY the threshold is not a big gap"


def test_label_is_null_across_a_gap_longer_than_the_threshold():
    s = NS_PER_SECOND
    # decision at t=0, horizon 1min; one hole inside [t, t+60s].
    hole_31s = [0, 10 * s, 41 * s, 60 * s]  # 31s hole from 10s to 41s
    hole_29s = [0, 10 * s, 39 * s, 60 * s]  # 29s hole from 10s to 39s

    label_31, stats_31 = _one_horizon(
        [0], [100.0], hole_31s, [100.0, 101.0, 102.0, 103.0], horizon_ns=RET_1MIN_NS
    )
    label_29, _ = _one_horizon(
        [0], [100.0], hole_29s, [100.0, 101.0, 102.0, 103.0], horizon_ns=RET_1MIN_NS
    )
    assert math.isnan(label_31[0]), "a 31s hole inside the window nulls the label"
    assert stats_31["per_horizon"][H10]["null_gap"] == 1
    assert label_29[0] == pytest.approx(0.03), "a 29s hole does not"


def test_the_gap_rule_is_overlap_not_containment():
    """A gap STRADDLING `t` is the case containment misses: the decision
    row is a trade inside the hole, its `mid_t` is the stale pre-gap quote,
    and `[t, t+h]` is half unknown."""
    s = NS_PER_SECOND
    quote_etime = [0, 100 * s, 110 * s]  # 100s hole from 0 to 100s
    quote_mid = [100.0, 150.0, 151.0]
    # a trade at t=50s, inside the hole; its prevailing mid is the 0s quote.
    label, stats = _one_horizon(
        [50 * s], [100.0], quote_etime, quote_mid, horizon_ns=RET_1MIN_NS
    )
    assert math.isnan(label[0]), (
        "the gap starts before t and ends inside [t, t+60s]; containment "
        "(gap_start >= t and gap_end <= t+h) would not see it"
    )
    assert stats["per_horizon"][H10]["null_gap"] == 1


def test_a_gap_ending_exactly_at_t_does_not_null_the_label():
    s = NS_PER_SECOND
    quote_etime = [0, 100 * s, 105 * s, 110 * s]  # gap ends exactly at t = 100s
    quote_mid = [100.0, 200.0, 202.0, 202.0]
    label, _ = _one_horizon(
        [100 * s], [200.0], quote_etime, quote_mid, horizon_ns=RET_10S_NS
    )
    assert label[0] == pytest.approx(0.01), (
        "the window [t, t+10s] touches the gap only at its closed end -- "
        "nothing inside the window is unknown"
    )


def test_a_gap_starting_exactly_at_t_plus_h_does_not_null_the_label():
    s = NS_PER_SECOND
    quote_etime = [0, 5 * s, 10 * s, 110 * s]  # gap starts exactly at t+10s
    quote_mid = [100.0, 100.5, 101.0, 500.0]
    label, _ = _one_horizon([0], [100.0], quote_etime, quote_mid)
    assert label[0] == pytest.approx(0.01), (
        "the prevailing quote at t+10s exists; what happens after it is "
        "outside this label's window"
    )


# --------------------------------------------------------------------------
# Null reason 3: no mid at t
# --------------------------------------------------------------------------


def test_label_is_null_when_mid_at_t_is_null():
    """The real 2026-09-12 shape: L1 starts at 06:37:10.882 UTC, so every
    decision row before it is a trade with no prevailing quote at all."""
    s = NS_PER_SECOND
    labels, stats = compute_labels(
        _i64([0, 1 * s, 100 * s]),
        _f64([math.nan, math.nan, 100.0]),
        _i64([100 * s, 160 * s]),
        _f64([100.0, 110.0]),
        horizons_ns=LABEL_HORIZON_NS,
        gap_threshold_ns=100 * NS_PER_SECOND,
    )
    for name in LABEL_HORIZON_NS:
        assert math.isnan(labels[name][0])
        assert math.isnan(labels[name][1])
        assert stats["per_horizon"][name]["null_no_mid"] == 2
    assert labels["ret_1min_mid"][2] == pytest.approx(0.1), (
        "the row that HAS a mid_t is still labelled -- otherwise this test "
        "would pass on a function that nulls everything"
    )


def test_null_reasons_partition_the_nulls():
    """Split by REASON means a partition: the three counts sum to the
    number of NaNs, with no row counted twice."""
    s = NS_PER_SECOND
    decision_etime = [0, 10 * s, 50 * s, 215 * s, 400 * s]
    decision_mid = [math.nan, 100.0, 100.0, 100.0, 100.0]
    quote_etime = [10 * s, 20 * s, 50 * s, 120 * s, 210 * s, 215 * s, 220 * s, 226 * s]
    quote_mid = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]

    label, stats = _one_horizon(decision_etime, decision_mid, quote_etime, quote_mid)
    per = stats["per_horizon"][H10]
    n_nan = int(np.isnan(label).sum())
    assert n_nan == per["null_total"]
    assert per["null_no_mid"] + per["null_past_end"] + per["null_gap"] == n_nan
    assert per["null_total"] > 0 and per["n_labelled"] > 0, (
        "a fixture with no nulls, or with nothing but nulls, would make the "
        "partition assertion vacuous"
    )


# --------------------------------------------------------------------------
# Reported numbers
# --------------------------------------------------------------------------


def test_asof_convention_delta_is_reported():
    """The prevailing/next disagreement is a DQ NUMBER, not a second
    column (an uncatalogued label column would break the catalogue rule)."""
    s = NS_PER_SECOND
    # Rows 0 and 1 disagree: their `t+10s` falls strictly between the 9s
    # quote and the 12s one. Row 2's `t+10s` lands EXACTLY on the 12s
    # quote, where the two conventions name the same row and agree.
    decision_etime = [0, 1 * s, 2 * s]
    decision_mid = [100.0, 100.0, 100.0]
    quote_etime = [0, 1 * s, 2 * s, 9 * s, 12 * s, 30 * s]
    quote_mid = [100.0, 100.0, 100.0, 101.0, 103.0, 103.0]

    _, stats = _one_horizon(decision_etime, decision_mid, quote_etime, quote_mid)
    per = stats["per_horizon"][H10]
    assert per["asof_comparable_rows"] == 3
    assert per["asof_disagreement_rows"] == 2
    assert per["asof_disagreement_fraction"] == pytest.approx(2 / 3)
    assert stats["asof_convention_disagreement_rows"] == 2, (
        "the flat key is the one data/dq/feature_checks.py reads out of "
        "build_stats.json"
    )


def test_stats_carry_the_feature_tier_dq_contract_keys():
    """`data/dq/feature_checks.py:FEATURE_BUILD_STATS_KEYS` is the contract
    between this computation and the DQ report; a missing key there is a
    `failed` row, so the names are asserted here rather than discovered in
    Plan 05."""
    s = NS_PER_SECOND
    quote_etime = [i * s for i in range(121)]
    # mid holds for three seconds at a time, so some 1 s returns ARE zero
    quote_mid = [100.0 + (i // 3) % 2 for i in range(121)]
    labels, stats = compute_labels(
        _i64(quote_etime),
        _f64(quote_mid),
        _i64(quote_etime),
        _f64(quote_mid),
    )
    assert set(labels) == set(LABEL_HORIZON_NS)
    for key in (
        "null_primary_label_rows",
        "ret_10s_mid_zero_fraction",
        "ret_1s_mid_zero_fraction",
        "asof_convention_disagreement_rows",
        "n_decision_rows",
    ):
        assert key in stats, key
    assert stats["n_decision_rows"] == 121
    assert stats["null_primary_label_rows"] == int(
        np.isnan(labels["ret_10s_mid"]).sum()
    )
    assert 0.0 <= stats["ret_10s_mid_zero_fraction"] <= 1.0
    assert stats["ret_1s_mid_zero_fraction"] > 0.0, (
        "this fixture repeats mids every 3s, so some 1s returns ARE zero -- "
        "a zero fraction of 0.0 would make the assertion vacuous"
    )


def test_zero_fraction_counts_real_equal_price_pairs_not_nulls():
    s = NS_PER_SECOND
    # 3 rows labelled: two with an unchanged mid, one with a move; 1 null.
    decision_etime = [0, 1 * s, 2 * s, 3 * s]
    decision_mid = [100.0, 100.0, 100.0, 100.0]
    quote_etime = [0, 1 * s, 2 * s, 10 * s, 11 * s, 12 * s]
    quote_mid = [100.0, 100.0, 100.0, 100.0, 100.0, 105.0]
    label, stats = _one_horizon(decision_etime, decision_mid, quote_etime, quote_mid)
    per = stats["per_horizon"][H10]
    assert per["n_labelled"] == 3 and per["null_total"] == 1
    assert per["zero_fraction"] == pytest.approx(2 / 3), (
        "the null row is not a zero -- it is not counted in either term"
    )


# --------------------------------------------------------------------------
# Input guards (guardrails runtime-first, STATE.md)
# --------------------------------------------------------------------------


def test_an_unsorted_quote_series_is_refused():
    s = NS_PER_SECOND
    with pytest.raises(LabelInputError, match="non-decreasing"):
        _one_horizon([0], [100.0], [0, 20 * s, 10 * s], [100.0, 101.0, 102.0])


def test_an_empty_quote_series_is_refused():
    with pytest.raises(LabelInputError, match="empty"):
        _one_horizon([0], [100.0], [], [])


def test_a_non_positive_mid_at_t_is_refused():
    s = NS_PER_SECOND
    with pytest.raises(LabelInputError, match="mid"):
        _one_horizon([0], [0.0], [0, 20 * s], [100.0, 101.0])


# --------------------------------------------------------------------------
# Properties
# --------------------------------------------------------------------------


@settings(deadline=None, max_examples=150, suppress_health_check=[HealthCheck.too_slow])
@given(problem=label_problem())
def test_label_is_never_zero_by_default(problem):
    """Every label is either NaN or exactly the hand-computed return. No
    code path invents a 0.0 that was not a real equal-price pair."""
    decision_etime, decision_mid, quote_etime, quote_mid = problem
    label, _ = _one_horizon(decision_etime, decision_mid, quote_etime, quote_mid)

    for i in range(decision_etime.size):
        target = int(decision_etime[i]) + RET_10S_NS
        j = int(np.searchsorted(quote_etime, target, side="right")) - 1
        if math.isnan(decision_mid[i]) or j < 0 or target > int(quote_etime[-1]):
            assert math.isnan(label[i])
            continue
        if math.isnan(label[i]):
            continue  # the gap rule; covered by its own tests
        expected = (float(quote_mid[j]) - float(decision_mid[i])) / float(
            decision_mid[i]
        )
        assert label[i] == expected, "bitwise, never approx"
        if label[i] == 0.0:
            assert float(quote_mid[j]) == float(decision_mid[i])
