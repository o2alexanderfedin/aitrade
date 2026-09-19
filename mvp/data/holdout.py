"""Which dates are held out, and the refusal that keeps them that way.

Phase 5 declares a held-out window; from that moment nothing outside the
gate protocol may materialize those dates into a readable lake tier.
04-CONTEXT.md D-04-11 is why this module exists at all: decision rows are a
near-lossless transform of L1, so a feature partition for a held-out date
hands back exactly what the holdout withholds. The refusal therefore has to
be in place BEFORE the first date is declared -- which is today. Measured
2026-09-17 and re-measured for 04-02: no date has been declared yet, so
every real L1 day is still buildable and this module's only job so far is
to be ready.

DELIBERATELY DEPENDENCE-FREE -- `json`, `pathlib`, `re`, `logging` and
nothing else. In particular it does NOT import the quarantined tier's own
audited module: that module imports `mlflow` at its top, and a feature
pipeline acquiring an mlflow import for a date-list lookup is a coupling
nobody asked for. Nor does it name that tier's path anywhere: what it reads
is a different artifact, a git-committed list of dates.

GIT-COMMITTED, for the same reason every other audit artifact here is:
"every look appears in a diff" has to be literally true, and a holdout list
that can be silently emptied protects nothing.

FAIL-CLOSED IS THE ONLY SAFE DIRECTION. An unreadable or malformed registry
RAISES. A registry that silently read as empty when it was corrupt would
quietly un-hold the entire held-out window, and the failure would look
exactly like success.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path

from data.lake_paths import LAKE_REGISTRY_ROOT

__all__ = [
    "QuarantinedDateError",
    "HOLDOUT_REGISTRY_PATH",
    "HOLDOUT_REGISTRY_VERSION",
    "QuarantinedDates",
    "holdout_registry_path",
    "quarantined_dates",
    "assert_not_quarantined",
]

logger = logging.getLogger(__name__)

HOLDOUT_REGISTRY_VERSION = 1

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Registries already reported as absent, so the "not declared yet" notice
#: is logged once per root rather than once per lookup.
_ABSENCE_LOGGED: set[str] = set()


class QuarantinedDateError(ValueError):
    """Raised when a build or a read touches a held-out date.

    Names every offending date AND the context that needed it, because the
    interesting case is the indirect one: day D's own date is fine and its
    long-horizon label tail reaches into a day that is not.
    """


def holdout_registry_path(registry_root: Path | None = None) -> Path:
    """`<registry_root>/holdout/holdout.json` -- git-committed, one
    document per repository.

    Shape::

        {"version": 1, "symbol": "BTCUSDT", "dates": ["YYYY-MM-DD", ...],
         "locked_at": <int64 ns>, "reason": "..."}
    """
    return Path(registry_root or LAKE_REGISTRY_ROOT) / "holdout" / "holdout.json"


#: The default registry document's path. A module-level constant so a
#: caller can point at it in a message without re-deriving the layout.
HOLDOUT_REGISTRY_PATH = holdout_registry_path()


class QuarantinedDates(frozenset):
    """The held-out dates, plus whether a registry exists at all.

    A bare `frozenset()` cannot tell "Phase 5 has not chosen a held-out
    window yet" from "a declared window that happens to be empty", and
    those two are not the same claim: the first says the protection is not
    yet armed, the second says it is armed and currently covers nothing.
    `declared` carries that distinction to every caller, and the absent
    case is logged as well.
    """

    declared: bool

    def __new__(cls, dates: Iterable[str], *, declared: bool) -> QuarantinedDates:
        self = super().__new__(cls, dates)
        self.declared = declared
        return self


def _registry_problem(body: object, *, symbol: str) -> str | None:
    if not isinstance(body, dict):
        return f"holdout registry is a {type(body).__name__}, not a JSON object"
    # THE VERSION IS READ, NOT JUST DECLARED (04-REVIEW.md WR-07). A v2
    # document with a different shape -- per-symbol maps, ranges instead of
    # dates, an `exclusions` key -- parses cleanly enough under the rules
    # below that any date this parser failed to interpret would silently
    # un-hold. An ABSENT version is refused for the same reason: absence is
    # not evidence of v1, and guessing is the direction that fails open.
    if body.get("version") != HOLDOUT_REGISTRY_VERSION:
        return (
            f"holdout registry version {body.get('version')!r} is not "
            f"{HOLDOUT_REGISTRY_VERSION} -- refusing to read a document this "
            "parser was not written for"
        )
    if "dates" not in body:
        return "holdout registry has no 'dates' key"
    dates = body["dates"]
    if not isinstance(dates, list):
        return f"holdout registry 'dates' is a {type(dates).__name__}, not a list"
    for entry in dates:
        if not isinstance(entry, str) or not _DATE_RE.match(entry):
            return f"holdout registry date {entry!r} is not YYYY-MM-DD"
    registry_symbol = body.get("symbol")
    if registry_symbol != symbol:
        return (
            f"holdout registry is for symbol {registry_symbol!r}, not {symbol!r} "
            "-- refusing to decide one symbol's holdout from another's registry"
        )
    return None


def quarantined_dates(
    *, registry_root: Path | None = None, symbol: str
) -> QuarantinedDates:
    """The set of `YYYY-MM-DD` dates declared held out for `symbol`.

    An ABSENT registry returns an empty set with `declared is False` and
    logs (once per root) that no holdout window has been declared yet --
    the true state today, and a state a caller may want to surface rather
    than treat as "nothing is held out".

    Anything else that is not a well-formed registry for THIS symbol
    RAISES `ValueError`: unreadable, not JSON, not an object, no `dates`
    key, a `dates` value that is not a list of `YYYY-MM-DD` strings, or a
    document belonging to another symbol. See the module docstring for why
    degrading to "nothing is held out" is not an option.
    """
    path = holdout_registry_path(registry_root)
    if not path.exists():
        key = str(path)
        if key not in _ABSENCE_LOGGED:
            _ABSENCE_LOGGED.add(key)
            logger.info(
                "no holdout registry at %s: no held-out window has been declared "
                "yet (this is NOT the same as an empty one)",
                path,
            )
        return QuarantinedDates((), declared=False)

    try:
        raw = path.read_text()
    except OSError as exc:
        raise ValueError(
            f"holdout registry {path} is unreadable ({exc.__class__.__name__}: "
            f"{exc}) -- refusing to read it as 'nothing is held out'"
        ) from None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"holdout registry {path} is not valid JSON ({exc}) -- refusing to "
            "read it as 'nothing is held out'"
        ) from None

    problem = _registry_problem(body, symbol=symbol)
    if problem is not None:
        raise ValueError(f"{path}: {problem}")
    return QuarantinedDates(body["dates"], declared=True)


def assert_not_quarantined(
    dates: Iterable[str],
    *,
    symbol: str,
    registry_root: Path | None = None,
    context: str,
) -> None:
    """Raise `QuarantinedDateError` if any of `dates` is held out.

    `context` is the sentence fragment the message is built around --
    `"feature build of 2026-09-14"`, `"label tail of 2026-09-14 reads
    2026-09-15"` -- so the failure says which day was refused AND why that
    day was being read at all.
    """
    held_out = quarantined_dates(registry_root=registry_root, symbol=symbol)
    offenders = sorted(set(dates) & held_out)
    if offenders:
        raise QuarantinedDateError(
            f"{context}: date(s) {offenders} are held out for {symbol} "
            f"(declared in {holdout_registry_path(registry_root)}). The "
            "held-out window is reachable only through the Phase 9/10 gate "
            "protocol, never through this path."
        )
