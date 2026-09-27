"""Turn a catalogue `information_set`/`embargo` declaration into comparable
nanoseconds.

An `information_set` string is a PROMISE about what a value is allowed to
see. Until it is parseable, nothing can check the promise against the code:
`mid`'s bare `"t"` sat beside `ofi`'s dependency on the PREVIOUS quote for
as long as nobody looked, and the difference between them was prose.

THE GRAMMAR IS DELIBERATELY NARROW -- exactly the five forms the catalogue
uses today, and nothing wider:

    "t"                              a value read at t (the prevailing quote)
    "[t-<duration>, t]"              a trailing window, bounded in TIME
    "[prev_l1_update, t]"            bounded in EVENTS, unbounded in time
    "data through t + <duration>"    a label's horizon
    "data through t + <duration> for the VALUE; ... t + <duration> + <ref> ..."
                                     a label whose NULL MASK is wider than its
                                     value's horizon (04-04 Deviation 3)

A permissive parser here would be worse than none. The whole value of this
module is that an unrecognised declaration STOPS CI; degrading to
"unbounded" would silently pass every containment check, and degrading to 0
would silently pass every lookahead check. So anything else raises
`InformationSetError`.

THE TWO-PART LABEL FORM IS NOT A CONCESSION -- IT IS THE TRUTH. A label's
VALUE reads mids through `t+h`. Its NULL MASK additionally reads quote
ARRIVAL TIMES out to `t + h + max_quote_gap`: a gap that BEGINS at `t+h` and
runs longer than the threshold is only knowable from the next arrival. That
direction can only REMOVE a label, never turn one finite label into a
different finite label. The reference this module hands back
(`mask_extra`) is left UNRESOLVED on purpose: `spec/` naming
`dq_thresholds.toml`'s value would make the catalogue's grammar depend on
the DQ config, so the caller resolves it (`features.labels.
default_gap_threshold_ns`).

DURATIONS ARE A LOOKUP, NEVER ARITHMETIC. `DURATION_TOKEN_NS` aliases the
ALREADY-MULTIPLIED constants in `data/time_ns.py`. Writing `10 *
NS_PER_SECOND` here would make this file a second seconds-to-ns conversion
site and `tools.check_ms_to_ns_site` would (correctly) fail it -- only
`data/time_ns.py` is allowlisted. A new horizon therefore has to be added
to `data/time_ns.py` FIRST, which is the intended friction: the constant the
code uses and the token the catalogue writes come into existence together.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from data.time_ns import (
    RET_1MIN_NS,
    RET_1S_NS,
    RET_10MIN_NS,
    RET_10S_NS,
)

__all__ = [
    "DURATION_TOKEN_NS",
    "KNOWN_MASK_REFS",
    "PREV_UPDATE",
    "InformationSet",
    "InformationSetError",
    "parse_embargo",
    "parse_information_set",
]

#: Duration token -> nanoseconds. Aliases only; see the module docstring on
#: why there is no multiplication in this file. `"1s"` is also
#: `TRADE_FLOW_WINDOW_NS` (they are the same second), asserted in
#: `tests/spec/test_information_set.py` rather than duplicated here.
DURATION_TOKEN_NS: dict[str, int] = {
    "1s": RET_1S_NS,
    "10s": RET_10S_NS,
    "1min": RET_1MIN_NS,
    "10min": RET_10MIN_NS,
}

#: `lookback_events` for `"[prev_l1_update, t]"`: ONE L1 update back. The
#: feature is bounded in events and unbounded in time -- between two quotes
#: `ofi` is carried forward, and no number of nanoseconds describes that.
PREV_UPDATE: int = 1

#: Config references a label's NULL MASK clause may widen itself by. An
#: unknown reference raises: an unresolvable name is exactly as unverifiable
#: as unparsed prose.
KNOWN_MASK_REFS: frozenset[str] = frozenset({"label_gap.max_quote_gap_seconds"})


class InformationSetError(ValueError):
    """A declaration this grammar cannot read, or one that contradicts
    itself. Never downgraded to a warning: an uninterpretable information
    set is an unchecked leakage claim."""


@dataclass(frozen=True)
class InformationSet:
    """A parsed declaration, in units a measurement can be compared against.

    `lookback_ns is None` means "not bounded in time" -- NOT "zero" and NOT
    "unbounded in the sense that anything goes"; read it together with
    `lookback_events`.
    """

    text: str
    lookback_ns: int | None
    lookback_events: int | None
    lookahead_ns: int
    mask_extra: str | None = None


_DURATION = r"(?P<dur>[0-9]+(?:s|min))"
_WINDOW_RE = re.compile(rf"\A\[t-{_DURATION}, t\]\Z")
_PREV_RE = re.compile(r"\A\[prev_l1_update, t\]\Z")
_AHEAD_RE = re.compile(rf"\Adata through t \+ {_DURATION}\Z")
_VALUE_CLAUSE_RE = re.compile(rf"\Adata through t \+ {_DURATION} for the VALUE\Z")
_MASK_CLAUSE_RE = re.compile(
    rf"\bt \+ {_DURATION} \+ (?P<ref>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)"
)
_EMBARGO_RE = re.compile(rf"\A>= {_DURATION}\Z")


def _duration_ns(token: str, text: str) -> int:
    try:
        return DURATION_TOKEN_NS[token]
    except KeyError:
        raise InformationSetError(
            f"{text!r}: duration token {token!r} has no pre-multiplied constant "
            f"in data/time_ns.py (known: {sorted(DURATION_TOKEN_NS)}). Add the "
            "constant there first -- this module aliases, it never multiplies."
        ) from None


def parse_information_set(text: str) -> InformationSet:
    """Parse a catalogue `information_set`; raise on anything else."""
    if not isinstance(text, str):
        raise InformationSetError(f"information_set must be a string, got {type(text)}")

    if text == "t":
        return InformationSet(text, lookback_ns=0, lookback_events=0, lookahead_ns=0)

    if _PREV_RE.match(text):
        return InformationSet(
            text, lookback_ns=None, lookback_events=PREV_UPDATE, lookahead_ns=0
        )

    window = _WINDOW_RE.match(text)
    if window:
        return InformationSet(
            text,
            lookback_ns=_duration_ns(window.group("dur"), text),
            lookback_events=None,
            lookahead_ns=0,
        )

    ahead = _AHEAD_RE.match(text)
    if ahead:
        return InformationSet(
            text,
            lookback_ns=0,
            lookback_events=0,
            lookahead_ns=_duration_ns(ahead.group("dur"), text),
        )

    if ";" in text:
        return _parse_two_part_label(text)

    raise InformationSetError(
        f"no executable reading of information_set {text!r}. The grammar is "
        "exactly: 't', '[t-<duration>, t]', '[prev_l1_update, t]', "
        "'data through t + <duration>', and the two-clause label form. A new "
        "form needs a reading HERE before a leakage property can check it."
    )


def _parse_two_part_label(text: str) -> InformationSet:
    """`"<value clause>; <mask clause>"` -- the form 04-04 split a false
    one-line property into.

    The mask clause is prose with a required shape inside it: it must name
    `t + <the same duration> + <a known config reference>` exactly once. The
    prose around that is free, because it is explanation; the shape is not,
    because it is the claim.
    """
    parts = text.split(";")
    if len(parts) != 2:
        raise InformationSetError(
            f"{text!r}: the two-clause label form has exactly one ';' "
            f"separating the VALUE clause from the MASK clause, found "
            f"{len(parts) - 1}"
        )
    value_clause, mask_clause = parts[0].strip(), parts[1].strip()

    value = _VALUE_CLAUSE_RE.match(value_clause)
    if not value:
        raise InformationSetError(
            f"{text!r}: the VALUE clause must read exactly 'data through t + "
            f"<duration> for the VALUE', got {value_clause!r}"
        )
    lookahead_ns = _duration_ns(value.group("dur"), text)

    matches = _MASK_CLAUSE_RE.findall(mask_clause)
    if len(matches) != 1:
        raise InformationSetError(
            f"{text!r}: the MASK clause must name 't + <duration> + "
            f"<config reference>' exactly once, found {len(matches)}"
        )
    mask_dur, mask_ref = matches[0]
    if _duration_ns(mask_dur, text) != lookahead_ns:
        raise InformationSetError(
            f"{text!r}: the VALUE clause and the MASK clause disagree about "
            f"the horizon ({value.group('dur')!r} vs {mask_dur!r})"
        )
    if mask_ref not in KNOWN_MASK_REFS:
        raise InformationSetError(
            f"{text!r}: the MASK clause widens itself by {mask_ref!r}, which "
            f"resolves to nothing (known: {sorted(KNOWN_MASK_REFS)}). An "
            "unresolvable reference is as unverifiable as unparsed prose."
        )
    return InformationSet(
        text,
        lookback_ns=0,
        lookback_events=0,
        lookahead_ns=lookahead_ns,
        mask_extra=mask_ref,
    )


def parse_embargo(text: str) -> int:
    """Parse a catalogue `embargo` (`">= <duration>"`) into nanoseconds."""
    if not isinstance(text, str):
        raise InformationSetError(f"embargo must be a string, got {type(text)}")
    match = _EMBARGO_RE.match(text)
    if not match:
        raise InformationSetError(
            f"no executable reading of embargo {text!r} -- the grammar is "
            "'>= <duration>', with exactly one space after '>='"
        )
    return _duration_ns(match.group("dur"), text)
