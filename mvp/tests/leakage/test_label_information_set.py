"""FEAT-03, label side: how far past `t` a label reaches, and the proof
that it reaches no further.

A LABEL IS THE ONE VALUE IN THIS PIPELINE ALLOWED TO LOOK FORWARD. So the
property is not "nothing after `t` matters" -- it is "nothing after `t+h`
changes the number, and nothing after `t+h+max_quote_gap` changes whether
there is a number at all".

THE PLAN THIS FILE IMPLEMENTS ASKED FOR A PROPERTY THAT IS FALSE. "Nothing
after `t+h` can change the label" does not hold against correct code and
should not: a gap that BEGINS before `t+h` and runs longer than the
threshold is only knowable from the NEXT arrival, which can be up to
`max_quote_gap` past `t+h`. 04-04 split it in two and said so in the
catalogue; this file measures both halves and asserts the second is
GENUINELY wider than the first, so the split cannot be quietly undone.

Three kinds of property, the same three as the feature side:

1. INVARIANCE -- re-pricing quotes after `t+h` leaves every finite label
   bit-identical.
2. ANTI-VACUITY -- the prevailing quote at `t+h` DOES change it. Without
   this, (1) passes on `compute_labels` returning a constant.
3. CONTAINMENT -- the measured reach, in nanoseconds past `t`, is inside
   what `parse_information_set` reads out of the catalogue. Two numbers,
   because a label has two information sets.

NULLS ARE NaN HERE; `features/tier.py` is the single NaN -> null
conversion point on the way to a partition.
"""

from __future__ import annotations

import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from data.time_ns import LABEL_HORIZON_NS, NS_PER_SECOND, RET_1S_NS, RET_10S_NS
from features.labels import compute_labels, default_gap_threshold_ns
from spec.catalogue import load_labels
from spec.information_set import KNOWN_MASK_REFS, parse_information_set
from tests.fixtures.leakage_streams import f64, horizon_probe, i64, label_problem

#: The label names these properties are written against. The catalogue
#: cross-check in `test_catalogue_information_set.py` compares this tuple
#: with `load_labels()`, so a fifth label fails the suite rather than
#: silently going unchecked.
LABEL_NAMES: tuple[str, ...] = (
    "ret_10s_mid",
    "ret_1s_mid",
    "ret_1min_mid",
    "ret_10min_mid",
)

H10 = "ret_10s_mid"
GAP_30S = 30 * NS_PER_SECOND


def _one_horizon(
    decision_etime,
    decision_mid,
    quote_etime,
    quote_mid,
    *,
    horizon_ns: int = RET_10S_NS,
    gap_threshold_ns: int = GAP_30S,
):
    labels, stats = compute_labels(
        i64(decision_etime),
        f64(decision_mid),
        i64(quote_etime),
        f64(quote_mid),
        horizons_ns={H10: horizon_ns},
        gap_threshold_ns=gap_threshold_ns,
    )
    return labels[H10], stats


# --------------------------------------------------------------------------
# 1 + 2. Invariance and anti-vacuity, over generated streams
# --------------------------------------------------------------------------


@settings(deadline=None, max_examples=150, suppress_health_check=[HealthCheck.too_slow])
@given(problem=label_problem(), bump=st.floats(min_value=1.0, max_value=50.0))
def test_perturbing_a_quote_after_t_plus_h_cannot_change_a_label(problem, bump):
    """T-04-25/T-04-15. The MID VALUES of quotes strictly after the last
    row's `t+h` are re-priced; every label must be bit-identical.

    Only mid values -- moving their ETIMES is a different question, and
    the next test is where it is answered.
    """
    decision_etime, decision_mid, quote_etime, quote_mid = problem
    horizon = RET_10S_NS
    label, _ = _one_horizon(
        decision_etime, decision_mid, quote_etime, quote_mid, horizon_ns=horizon
    )

    last_needed = int(decision_etime[-1]) + horizon
    future = quote_etime > last_needed
    if not future.any():
        return
    perturbed = quote_mid.copy()
    perturbed[future] = perturbed[future] + bump
    label_after, _ = _one_horizon(
        decision_etime, decision_mid, quote_etime, perturbed, horizon_ns=horizon
    )
    assert np.array_equal(label, label_after, equal_nan=True)


@settings(deadline=None, max_examples=150, suppress_health_check=[HealthCheck.too_slow])
@given(problem=label_problem(), bump=st.floats(min_value=1.0, max_value=50.0))
def test_perturbing_an_etime_after_t_plus_h_only_toggles_nullness(problem, bump):
    """The null MASK legitimately reads arrival TIMES past `t+h`: a gap
    that begins before `t+h` and runs longer than the threshold means the
    prevailing quote is the last thing known for 30+ seconds.

    So the honest property is weaker than "nothing after `t+h` matters" --
    a label may appear or disappear, but a finite label never becomes a
    DIFFERENT finite label.
    """
    decision_etime, decision_mid, quote_etime, quote_mid = problem
    horizon = RET_10S_NS
    label, _ = _one_horizon(
        decision_etime, decision_mid, quote_etime, quote_mid, horizon_ns=horizon
    )

    last_needed = int(decision_etime[-1]) + horizon
    future = quote_etime > last_needed
    if not future.any():
        return
    moved = quote_etime.copy()
    moved[future] = moved[future] + int(bump) * NS_PER_SECOND
    label_after, _ = _one_horizon(
        decision_etime, decision_mid, moved, quote_mid, horizon_ns=horizon
    )
    both_finite = ~np.isnan(label) & ~np.isnan(label_after)
    assert np.array_equal(label[both_finite], label_after[both_finite])


@settings(deadline=None, max_examples=150, suppress_health_check=[HealthCheck.too_slow])
@given(problem=label_problem(), bump=st.floats(min_value=1.0, max_value=50.0))
def test_label_is_sensitive_to_the_prevailing_quote(problem, bump):
    """Anti-vacuity for the two properties above: without this, both pass
    on `compute_labels` returning a constant array."""
    decision_etime, decision_mid, quote_etime, quote_mid = problem
    horizon = RET_1S_NS
    label, _ = _one_horizon(
        decision_etime, decision_mid, quote_etime, quote_mid, horizon_ns=horizon
    )
    labelled = np.flatnonzero(~np.isnan(label))
    if labelled.size == 0:
        return
    row = int(labelled[0])
    target = int(decision_etime[row]) + horizon
    j = int(np.searchsorted(quote_etime, target, side="right")) - 1

    perturbed = quote_mid.copy()
    perturbed[j] = perturbed[j] + bump
    label_after, _ = _one_horizon(
        decision_etime, decision_mid, quote_etime, perturbed, horizon_ns=horizon
    )
    assert label_after[row] != label[row], (
        "the prevailing quote at t+h is exactly the row this label reads; "
        "if moving it changes nothing, the label reads nothing"
    )


# --------------------------------------------------------------------------
# 3. Containment: the measured reach, against the declared one
# --------------------------------------------------------------------------


def _measure_reach(name: str, horizon_ns: int, gap_ns: int, *, exact_match=True):
    """Perturb every quote of `horizon_probe` one at a time and report, in
    OFFSETS FROM `t`, which ones can move the label and which can toggle
    its existence.

    Two perturbations per quote, because a label has two information sets:

    - re-price it (`quote_mid[j] += 1`) -- moves the VALUE;
    - DELAY it and every quote after it -- moves the ARRIVAL TIMES. The
      tail moves together because delaying one row alone can put the
      series out of order, and `compute_labels` (rightly) refuses that.
      "Everything from here on arrives later" is also what an outage
      actually looks like.
    """
    decision_etime, decision_mid, quote_etime, quote_mid, offsets = horizon_probe(
        horizon_ns, gap_ns, exact_match=exact_match
    )
    base, _ = compute_labels(
        decision_etime,
        decision_mid,
        quote_etime,
        quote_mid,
        horizons_ns={name: horizon_ns},
        gap_threshold_ns=gap_ns,
    )
    base_label = base[name][0]
    assert np.isfinite(base_label), (
        f"{name}: the probe's baseline label must be finite, or every "
        "perturbation below would be measuring the same null"
    )

    delay = 2 * gap_ns + 1
    value_offsets: list[int] = []
    mask_offsets: list[int] = []
    for j in range(quote_etime.size):
        repriced = quote_mid.copy()
        repriced[j] = repriced[j] + 1.0
        moved_value = compute_labels(
            decision_etime,
            decision_mid,
            quote_etime,
            repriced,
            horizons_ns={name: horizon_ns},
            gap_threshold_ns=gap_ns,
        )[0][name][0]
        if not _same_scalar(moved_value, base_label):
            value_offsets.append(int(offsets[j]))

        delayed = quote_etime.copy()
        delayed[j:] = delayed[j:] + np.int64(delay)
        moved_mask = compute_labels(
            decision_etime,
            decision_mid,
            delayed,
            quote_mid,
            horizons_ns={name: horizon_ns},
            gap_threshold_ns=gap_ns,
        )[0][name][0]
        if np.isnan(moved_mask) != np.isnan(base_label):
            mask_offsets.append(int(offsets[j]))
        elif (
            int(offsets[j]) > horizon_ns
            and np.isfinite(moved_mask)
            and moved_mask != base_label
        ):
            # Only claimed for quotes AFTER `t+h`. Delaying the tail from a
            # quote at or before `t+h` moves the prevailing quote itself,
            # and the label SHOULD follow it -- that is the lookback side
            # working, not a leak.
            raise AssertionError(
                f"{name}: delaying the quote at offset {int(offsets[j])} ns "
                "turned one finite label into a DIFFERENT finite label -- "
                "arrival times past t+h may only add or remove a label"
            )

    return value_offsets, mask_offsets, [int(o) for o in offsets]


def _same_scalar(a, b) -> bool:
    return bool(a == b) or (bool(np.isnan(a)) and bool(np.isnan(b)))


def test_label_measured_lookahead_matches_the_declared_horizon():
    """For every catalogued label, measure how far past `t` a quote can sit
    and still matter -- and compare it with what the catalogue declares.

    TWO measurements, because the catalogue declares two reaches:

    - a quote whose MID can move the label sits at most `lookahead_ns`
      past `t` (and at least one does, or the label reads nothing);
    - a quote whose ARRIVAL TIME can toggle the label's existence sits at
      most `lookahead_ns + max_quote_gap` past `t` -- and at least one sits
      STRICTLY past `lookahead_ns`, which is the fact the plan's original
      one-line property denied.
    """
    gap_ns = default_gap_threshold_ns()
    for name, entry in sorted(load_labels().items()):
        declared = parse_information_set(entry.information_set)
        assert declared.mask_extra in KNOWN_MASK_REFS, (
            f"{name}: the label declares no wider information set for its "
            "null mask, but the mask reads arrival times past t+h"
        )
        horizon_ns = declared.lookahead_ns
        assert horizon_ns == LABEL_HORIZON_NS[name]

        # BOTH probes: one with a quote exactly at `t+h`, one without.
        # Each catches an as-of mutation the other cannot see.
        for exact_match in (True, False):
            value_offsets, mask_offsets, offsets = _measure_reach(
                name, horizon_ns, gap_ns, exact_match=exact_match
            )
            prevailing = max(o for o in offsets if o <= horizon_ns)

            assert value_offsets, (
                f"{name}: NO quote's price can change this label -- the "
                "invariance properties above would pass on a constant"
            )
            assert max(value_offsets) <= horizon_ns, (
                f"{name}: a quote at offset {max(value_offsets)} ns past t "
                f"can change the label's VALUE, but the catalogue declares a "
                f"{horizon_ns} ns horizon"
            )
            assert max(value_offsets) > 0, (
                f"{name}: only quotes at or before t move the label -- it is "
                "not reading its horizon at all"
            )
            # The reach is not merely BOUNDED by the horizon, it lands on
            # the prevailing quote exactly: the one quote whose price this
            # label reads is the last one at or before `t+h`. An as-of rule
            # off by one quote in either direction fails here, on the probe
            # that can see it.
            assert set(value_offsets) == {prevailing}, (
                f"{name} (exact_match={exact_match}): the label reads the "
                f"quote(s) at offset(s) {sorted(value_offsets)} ns, but the "
                f"declared backward as-of rule names the one at {prevailing} "
                "ns -- the last quote at or before t+h, exact matches allowed"
            )

        value_offsets, mask_offsets, offsets = _measure_reach(
            name, horizon_ns, gap_ns, exact_match=False
        )
        assert mask_offsets, (
            f"{name}: no arrival time can toggle this label's existence -- "
            "the gap rule is not connected to anything"
        )
        assert max(mask_offsets) <= horizon_ns + gap_ns, (
            f"{name}: an arrival at offset {max(mask_offsets)} ns past t can "
            f"toggle the label, past the declared {horizon_ns} + {gap_ns} ns"
        )
        assert max(mask_offsets) > horizon_ns, (
            f"{name}: the null mask reaches no further than the value's "
            "horizon -- then the catalogue's two-part information_set is "
            "overstating what the code reads, and should be simplified"
        )


def test_the_value_reach_is_strictly_narrower_than_the_mask_reach():
    """The two-part information set is not decoration. Measured on the
    primary label: the VALUE stops at `t+h`, the MASK does not.

    If this ever fails, 04-04's Deviation 3 has been undone and the
    catalogue should go back to the simpler one-line declaration.

    AND IT ONLY REACHES PAST `t+h` WHEN NO QUOTE LANDS EXACTLY THERE. A
    gap that STARTS at `t+h` touches the horizon without leaving anything
    inside it unknown, and the gap rule's comparison is strict, so with an
    exact-match quote the mask stops at `t+h` too. Both halves are
    asserted, because "the mask is wider" and "the mask is wider only
    sometimes" are different claims and only the second is true.
    """
    gap_ns = default_gap_threshold_ns()
    horizon_ns = LABEL_HORIZON_NS[H10]

    value_offsets, mask_offsets, _ = _measure_reach(
        H10, horizon_ns, gap_ns, exact_match=False
    )
    assert max(mask_offsets) > max(value_offsets)
    assert max(value_offsets) < horizon_ns < max(mask_offsets)

    _, exact_mask_offsets, _ = _measure_reach(H10, horizon_ns, gap_ns, exact_match=True)
    assert max(exact_mask_offsets) <= horizon_ns, (
        "with a quote exactly at t+h, a later gap STARTS at t+h and the "
        "overlap test is strict -- nothing past the horizon can null the "
        "label, and the mask's extra reach is empty here"
    )
