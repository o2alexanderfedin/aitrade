"""The grammar that turns a catalogue declaration into a number.

An `information_set` string is a PROMISE about what a value is allowed to
see. Until it parses, nothing can check the promise against the code --
which is how `mid`'s bare `"t"` sat next to `ofi`'s dependency on the
PREVIOUS quote for as long as nobody looked.

These tests pin the grammar to exactly the forms the catalogue uses today.
The parser is deliberately narrow: an unrecognised string must RAISE, never
degrade to "unbounded" and never to 0, because both of those would let a
leakage check pass on a declaration nobody had read.
"""

from __future__ import annotations

import pytest

from data.time_ns import (
    LABEL_HORIZON_NS,
    RET_1MIN_NS,
    RET_1S_NS,
    RET_10MIN_NS,
    RET_10S_NS,
    TRADE_FLOW_WINDOW_NS,
)
from spec.information_set import (
    DURATION_TOKEN_NS,
    PREV_UPDATE,
    InformationSetError,
    parse_embargo,
    parse_information_set,
)


def test_parse_information_set_covers_every_catalogued_form():
    """The four feature/label forms, each pinned to a DIFFERENT number so a
    parser that returned one constant could not pass this."""
    bare = parse_information_set("t")
    assert (bare.lookback_ns, bare.lookback_events, bare.lookahead_ns) == (0, 0, 0)
    assert bare.mask_extra is None

    window = parse_information_set("[t-1s, t]")
    assert window.lookback_ns == TRADE_FLOW_WINDOW_NS
    assert window.lookback_events is None
    assert window.lookahead_ns == 0

    prev = parse_information_set("[prev_l1_update, t]")
    assert prev.lookback_ns is None, (
        "the previous L1 update is an unbounded wait in TIME -- a number here "
        "would be a bound the code does not have"
    )
    assert prev.lookback_events == PREV_UPDATE
    assert prev.lookahead_ns == 0

    ahead = parse_information_set("data through t + 10s")
    assert ahead.lookahead_ns == RET_10S_NS
    assert ahead.lookback_ns == 0
    assert ahead.mask_extra is None


def test_parse_information_set_reads_the_two_part_label_form():
    """A label's VALUE and its NULL MASK have different information sets
    (04-04 Deviation 3), and the catalogue says both. The parser returns the
    VALUE's horizon as `lookahead_ns` and hands the mask's config reference
    back UNRESOLVED -- `spec/` must not import the DQ config to read its own
    grammar."""
    text = (
        "data through t + 10s for the VALUE; the quote ARRIVAL TIMES through "
        "t + 10s + label_gap.max_quote_gap_seconds additionally decide whether "
        "the label exists (a gap that long overlapping [t, t+10s] nulls it) -- "
        "that direction can only REMOVE a label, never change a finite one"
    )
    parsed = parse_information_set(text)
    assert parsed.lookahead_ns == RET_10S_NS
    assert parsed.mask_extra == "label_gap.max_quote_gap_seconds"


def test_the_two_clauses_must_name_the_SAME_horizon():
    """A mask clause that widens a different horizon than the value clause
    is a typo the parser is the only thing positioned to catch."""
    with pytest.raises(InformationSetError, match="disagree"):
        parse_information_set(
            "data through t + 10s for the VALUE; quote ARRIVAL TIMES through "
            "t + 1min + label_gap.max_quote_gap_seconds decide whether it exists"
        )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "t + 10s",
        "everything",
        "[t-1s, t)",
        "[t-5s, t]",
        "data through t + 5s",
        "data through t - 10s",
        "data through t + 10s for the VALUE",
        "data through t + 10s for the VALUE; arrival times are fine really",
        "data through t + 10s for the VALUE; t + 10s + made_up.threshold decides",
        "[prev_trade, t]",
    ],
)
def test_an_unparseable_declaration_raises(text):
    """It never degrades to "unbounded" and never to 0. `"[t-5s, t]"` and
    `"data through t + 5s"` are in this list on purpose: `5s` is a duration
    with no constant in `data/time_ns.py`, and the intended way to add one
    is to add it THERE first. That friction is the feature."""
    with pytest.raises(InformationSetError):
        parse_information_set(text)


def test_parse_embargo():
    assert parse_embargo(">= 10s") == RET_10S_NS
    assert parse_embargo(">= 1s") == RET_1S_NS
    assert parse_embargo(">= 1min") == RET_1MIN_NS
    assert parse_embargo(">= 10min") == RET_10MIN_NS


@pytest.mark.parametrize(
    "text", ["10s", "> 10s", ">= 5s", ">=10s", "", ">= 10 s", "<= 10s"]
)
def test_a_malformed_embargo_raises(text):
    with pytest.raises(InformationSetError):
        parse_embargo(text)


def test_the_duration_table_is_a_lookup_never_a_multiplication():
    """`DURATION_TOKEN_NS` aliases the ALREADY-MULTIPLIED constants in
    `data/time_ns.py`. If this module multiplied instead,
    `tools.check_ms_to_ns_site` would (correctly) call it a second
    seconds-to-ns conversion site -- only `data/time_ns.py` is allowlisted.
    """
    assert DURATION_TOKEN_NS == {
        "1s": RET_1S_NS,
        "10s": RET_10S_NS,
        "1min": RET_1MIN_NS,
        "10min": RET_10MIN_NS,
    }
    assert DURATION_TOKEN_NS["1s"] == TRADE_FLOW_WINDOW_NS, (
        "`[t-1s, t]` must parse to the same int the kernel evicts on"
    )
    assert set(DURATION_TOKEN_NS.values()) >= set(LABEL_HORIZON_NS.values())
