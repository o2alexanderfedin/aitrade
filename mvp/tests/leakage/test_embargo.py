"""FEAT-04's CI half: every label's declared embargo is at least its
declared horizon, and both agree with the constant the code actually uses.

`tools/check_spec_diff.py` compares `computation` only. `embargo` and
`horizon` are unguarded by every other tripwire in this repo -- a future
editor shortening `ret_10s_mid`'s embargo to `">= 1s"` would pass ruff,
pass `check_spec_diff`, pass `check_catalogue_completeness` and pass the
whole feature suite. THIS FILE is the guard, which is why it is checked by
mutation rather than by assertion that it exists.

Three numbers for the same horizon live in three places -- `labels.toml`'s
`horizon`, its `information_set`, and `data/time_ns.py`'s
`LABEL_HORIZON_NS` -- so all three are compared against each other here
rather than each against itself.
"""

from __future__ import annotations

from data.time_ns import LABEL_HORIZON_NS
from spec.catalogue import load_labels
from spec.information_set import (
    DURATION_TOKEN_NS,
    parse_embargo,
    parse_information_set,
)


def test_every_label_embargo_is_at_least_its_horizon():
    """FEAT-04. An embargo shorter than the horizon means a fold boundary
    that lets a training row's own future sit in validation."""
    labels = load_labels()
    assert labels, "the label catalogue is empty -- this test would be vacuous"
    for name, entry in sorted(labels.items()):
        embargo_ns = parse_embargo(entry.embargo)
        horizon_ns = parse_information_set(entry.information_set).lookahead_ns
        assert embargo_ns >= horizon_ns, (
            f"{name}: embargo {entry.embargo!r} ({embargo_ns} ns) is SHORTER "
            f"than the horizon its own information_set declares ({horizon_ns} "
            "ns) -- a training row's own future would land in validation"
        )
        assert horizon_ns == LABEL_HORIZON_NS[name], (
            f"{name}: the information_set declares a {horizon_ns} ns horizon "
            f"but data/time_ns.py computes {LABEL_HORIZON_NS[name]} ns"
        )


def test_declared_horizon_matches_LABEL_HORIZON_NS():
    """The catalogue's `horizon` token and the constant the code uses agree,
    for all four labels. Two places the same number lives cannot drift apart
    unnoticed."""
    labels = load_labels()
    assert set(labels) == set(LABEL_HORIZON_NS), (
        "a label with no horizon constant (or a constant with no label) -- "
        "data/time_ns.py and spec/labels.toml must name the same four"
    )
    for name, entry in sorted(labels.items()):
        assert entry.horizon in DURATION_TOKEN_NS, (
            f"{name}: horizon token {entry.horizon!r} has no constant in "
            "data/time_ns.py -- add it there first"
        )
        assert DURATION_TOKEN_NS[entry.horizon] == LABEL_HORIZON_NS[name]


def test_the_embargo_bound_is_tight_enough_to_bite():
    """Anti-vacuity: `>= 0s` would satisfy the test above for every label.
    Every embargo here is POSITIVE and equals its horizon exactly, so the
    assertion above is operating at the boundary rather than far from it."""
    for name, entry in sorted(load_labels().items()):
        embargo_ns = parse_embargo(entry.embargo)
        assert embargo_ns > 0, f"{name}: a zero embargo is not an embargo"
        assert embargo_ns == LABEL_HORIZON_NS[name], (
            f"{name}: embargo {embargo_ns} ns is not the horizon exactly -- "
            "if that is deliberate, this assertion is the place to say why"
        )
