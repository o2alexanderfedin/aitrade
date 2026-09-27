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

DELIBERATELY DEPENDENCE-FREE -- `json`, `pathlib`, `re`, `logging`, `time`
(05-05-PLAN.md's `write_holdout_registry`, this module's first WRITER, needs
`time.time_ns()` for `locked_at` -- the same stdlib-only posture, nothing
heavier) and nothing else. In particular it does NOT import the quarantined
tier's own audited module: that module imports `mlflow` at its top, and a
feature pipeline acquiring an mlflow import for a date-list lookup is a
coupling nobody asked for. Nor does it name that tier's path anywhere: what
it reads is a different artifact, a git-committed list of dates.

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
import os
import re
import threading
import time
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
    "write_holdout_registry",
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


class _AlreadyExistsError(Exception):
    """Internal signal from `_exclusive_write_json`: `path` already exists.
    Never escapes this module -- `write_holdout_registry` catches it and
    raises its own, message-compatible `ValueError` naming what the
    existing file declares."""


def _exclusive_write_json(path: Path, body: dict) -> None:
    """Create `path` for the first time ONLY -- atomically, at the
    filesystem level, so "does a file already exist here" and "create it"
    can never be two separate steps two concurrent callers each observe
    differently (05-REVIEW.md WR-02).

    `path.exists()` then `_atomic_write_json` (the check-then-act this
    replaces) has a window: two concurrent callers can both observe
    `False`, both pass the write-once refusal, and one `tmp_path.replace`
    silently wins over the other -- the loser's `dates`/`reason` vanish
    with no error to either caller. This closes it with `os.link`, which
    POSIX guarantees is atomic and raises `FileExistsError` if the target
    already exists: write the body to a per-call-unique temp file (unique
    per PID and thread, so two concurrent callers never share, and thus
    never race on, the SAME temp path), then `os.link` that temp file onto
    `path` -- whichever caller's `os.link` lands first wins `path`; every
    other caller's `os.link` raises `FileExistsError`, translated here to
    `_AlreadyExistsError` so the caller can build its own message from the
    file that actually won.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp_path.write_text(json.dumps(body, sort_keys=True, indent=2))
    try:
        os.link(tmp_path, path)
    except FileExistsError:
        raise _AlreadyExistsError() from None
    finally:
        tmp_path.unlink(missing_ok=True)


def write_holdout_registry(
    dates: list[str],
    *,
    reason: str,
    symbol: str,
    registry_root: Path | None = None,
    locked_at_ns: int | None = None,
) -> Path:
    """Write `holdout_registry_path(registry_root)` for the first (and, per
    this function's own refusal, ONLY) time -- this module's first WRITER;
    everything above stays a reader.

    WRITE-ONCE, LIKE EVERY OTHER REGISTRY ARTIFACT IN THIS CODEBASE. Refuses
    (`ValueError`) if the target file already exists, naming the dates it
    already declares -- a re-declaration (widening or narrowing an already
    -armed held-out window) is out of Phase 5's scope entirely (D-05-17: this
    phase builds the tool, it does not declare), so this function does not
    need, and deliberately does not offer, an update path. Callers who need
    to change a declared window are a later phase's design problem, not a
    silent overwrite here.

    THE REFUSAL ITSELF IS ATOMIC (05-REVIEW.md WR-02): the write goes
    through `_exclusive_write_json`, an `os.link`-based create that the
    filesystem itself refuses if `path` already exists -- never a Python
    -level `path.exists()` check followed by a separate write. The
    previous shape (check, THEN write) had a window: two concurrent
    callers (e.g. a retried declaration attempt racing the first one still
    finishing) could both observe `path.exists() is False`, both pass the
    refusal, and one write would silently win over the other with no error
    to either caller -- exactly the quiet-failure direction this module's
    own fail-closed doctrine forbids. On a losing race, the existing file
    is read fresh (not the pre-race body) so the error message always
    names whichever declaration actually won.

    VALIDATES `dates` BEFORE WRITING (fail-closed cuts both ways, per the
    module docstring above): every entry must match `_DATE_RE`, the same
    shape `_registry_problem` requires on READ. A malformed write here would
    make every later `quarantined_dates` call raise -- wedging
    `write_feature_partition`, `load_features`, and the harness accessor for
    every symbol, not just this one -- so the same shape check that guards
    the read guards the write too.

    Writes exactly the schema `quarantined_dates` already parses:
    `{"version": HOLDOUT_REGISTRY_VERSION, "symbol": symbol,
    "dates": sorted(dates), "locked_at": <int64 ns>, "reason": reason}`.
    `locked_at_ns` defaults to `time.time_ns()` -- a caller-supplied value
    exists only so a test can pin a deterministic timestamp without
    monkeypatching `time.time_ns` itself.

    NEVER CALLS `chmod`/`os.chmod` (source-inspected by a dedicated
    red-proof test, mirroring `data/lockbox.py`'s own): this writer's job
    ends at the JSON. The physical `lake/lockbox/` barrier is a human,
    out-of-band operation (`data/lockbox_POLICY.md`) this function has no
    part in.
    """
    path = holdout_registry_path(registry_root)
    bad = [d for d in dates if not (isinstance(d, str) and _DATE_RE.match(d))]
    if bad:
        raise ValueError(
            f"write_holdout_registry: date(s) {bad!r} are not YYYY-MM-DD -- "
            "refusing to write a registry every later reader would then "
            "have to raise on"
        )
    body = {
        "version": HOLDOUT_REGISTRY_VERSION,
        "symbol": symbol,
        "dates": sorted(dates),
        "locked_at": locked_at_ns if locked_at_ns is not None else time.time_ns(),
        "reason": reason,
    }
    try:
        _exclusive_write_json(path, body)
    except _AlreadyExistsError:
        existing = json.loads(path.read_text())
        raise ValueError(
            f"write_holdout_registry: {path} already exists (declares "
            f"{existing.get('dates')!r} for {existing.get('symbol')!r}, "
            f"locked_at={existing.get('locked_at')!r}) -- this writer is "
            "write-once; a re-declaration is out of scope"
        ) from None
    return path
