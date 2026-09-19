"""The single home for seconds-to-nanoseconds arithmetic in feature/label
code (04-CONTEXT.md D-04-13).

WHY THIS MODULE EXISTS AT ALL. `tools.check_ms_to_ns_site` resolves VALUES
across modules, not literals: after `from data.time_ns import NS_PER_SECOND`,
writing `10 * NS_PER_SECOND` in `features/labels.py` is a seconds-to-ns site
in `features/labels.py`, and that file is not on
`ALLOWLISTED_SEC_TO_NS_SITES`, so CI fails. Binding the literal to a name
does not hide it either (03-REVIEW.md CR-05). The intended way through is not
to add another allowlist entry -- it is to do the multiplication HERE, once,
and import the PRE-MULTIPLIED `int` everywhere else. Every Phase 4 module
that needs a window or a horizon imports a name from this module and never
multiplies.

Three files in this codebase are allowed to convert a time scale, each for a
different reason:
- `data/capture/parse.py` owns milliseconds -> nanoseconds (Binance `E`/`T`
  fields); that is a DIFFERENT conversion, guarded by its own
  exactly-one-site rule.
- `data/dq/checks.py` owns day-boundary arithmetic and ns -> seconds display
  of outage/gap durations. It now IMPORTS `NS_PER_SECOND`/`NS_PER_DAY` from
  here rather than defining them.
- this module owns feature/label window and horizon lengths.

EVERY NAME IS UPPER_CASE AND EVERY VALUE IS AN `int`.
`tools.check_numba_globals` permits an `@njit` function to read a
module-level global only when it is an UPPER_CASE compile-time constant, and
later plans read `TRADE_FLOW_WINDOW_NS` from inside a kernel that walks an
int64 `etime` array -- a float constant there would silently widen the
comparison.

HORIZONS ARE NOT RE-DERIVED FROM THE CATALOGUE. Parsing `"10min"` out of
`spec/labels.toml` into nanoseconds at runtime would be a second conversion
site in whichever module did the parsing. Consistency is asserted in the
opposite direction instead: `LABEL_HORIZON_NS`'s key set must equal the
catalogue's label names (`tests/features/test_time_ns.py`, and again in Plan
06's catalogue cross-check). A new label therefore fails the test until its
horizon is added here.
"""

from __future__ import annotations

__all__ = [
    "NS_PER_SECOND",
    "NS_PER_DAY",
    "TRADE_FLOW_WINDOW_NS",
    "RET_1S_NS",
    "RET_10S_NS",
    "RET_1MIN_NS",
    "RET_10MIN_NS",
    "LABEL_HORIZON_NS",
    "QTY_SCALE",
]

#: Seconds <-> ns scale. The only reason a literal `1_000_000_000` is written
#: anywhere in feature/label code.
NS_PER_SECOND: int = 1_000_000_000
NS_PER_DAY: int = 86_400 * NS_PER_SECOND

#: `trade_flow`'s trailing window (D-04-03: 1 s). The window is HALF-OPEN,
#: `(t - TRADE_FLOW_WINDOW_NS, t]` -- a trade at exactly `t` is inside, a
#: trade at exactly `t - 1s` is not (D-04-16). The endpoint convention lives
#: in this comment because it is what makes the window checkable: the kernel
#: evicts while `rb_etime[head] <= t - TRADE_FLOW_WINDOW_NS`.
#:
#: A change of window length gets a NEW catalogue name (`trade_flow_5s`) and
#: a new constant here, never an edit to this one.
TRADE_FLOW_WINDOW_NS: int = NS_PER_SECOND

#: Label horizons, one named constant per catalogue label.
RET_1S_NS: int = NS_PER_SECOND
RET_10S_NS: int = 10 * NS_PER_SECOND
RET_1MIN_NS: int = 60 * NS_PER_SECOND
RET_10MIN_NS: int = 600 * NS_PER_SECOND

#: Catalogue label name -> horizon in ns. Keys must equal
#: `set(spec.catalogue.load_labels())`; `ret_10s_mid` is the primary label,
#: the other three are diagnostic (`spec/labels.toml`).
LABEL_HORIZON_NS: dict[str, int] = {
    "ret_10s_mid": RET_10S_NS,
    "ret_1s_mid": RET_1S_NS,
    "ret_1min_mid": RET_1MIN_NS,
    "ret_10min_mid": RET_10MIN_NS,
}

#: Fixed-point scale for the int64 `trade_flow` accumulator (D-04-14):
#: `qty * QTY_SCALE` is exact, because every real `qty`/`bid_qty`/`ask_qty` on
#: this venue is integral at 1e-8 (measured on 17.2M bookTicker + 1.4M trade
#: rows). A float64 running sum makes `trade_flow` depend on the merge tie
#: order on 95% of decision rows; in int64 it is bit-identical either way, so
#: reproducibility stops depending on a merge convention.
#:
#: 1e8 is deliberately OUTSIDE the `{1e3, 1e6, 1e9}` scale family that
#: `check_ms_to_ns_site` polices: this is a quantity scale, not a time
#: conversion, and it must not read as one.
QTY_SCALE: int = 100_000_000
