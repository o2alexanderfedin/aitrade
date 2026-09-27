"""The four catalogued features, written to be read: a pure-Python twin of
the `@njit` streaming kernel (D-04-10).

TWO JOBS, both of which need this to be a separate implementation rather
than a slow path through the same code:

1. It is the ORACLE for `tests/features/test_kernel.py`'s bitwise
   equivalence test. A kernel checked only against itself cannot be shown
   not to have drifted; this module shares no arithmetic, no buffer and no
   state representation with the thing it checks -- the trailing window is
   a `collections.deque` and the accumulator a Python `int`, so there is no
   ring-buffer subtlety in common with the code under test.
2. It is what `tests/leakage/test_feature_information_set.py`'s hypothesis
   properties run against. Those properties re-run the whole stream once
   per generated example and once per perturbed row; a JIT dispatch per
   example would make the suite unusable, and the equivalence test is what
   carries the result across to the kernel.

THE STATE CONTRACT (the kernel conforms to THIS, not the other way round).
State accumulates on EVERY row and values are emitted on EVERY row -- the
caller picks decision rows with `features.event_stream.decision_row_index`,
which is a pure function of position, so a prefix of the stream re-derives
the same decision rows (D-04-01). A run can be stopped at any row and
resumed by passing the same state object back, with bit-identical output:
the training path calls this once over a day and the simulator calls it row
by row, and if the state contract were incomplete those two would silently
disagree.

`ReferenceState` and `features.kernel.new_state()` hold the SAME slots with
the same meanings; only the representation differs (a dataclass with a
deque here, four numpy arrays there, because numba has neither).

SEMANTICS PINNED HERE, because this is the readable module.

`mid = (bid_price + ask_price) / 2` of the PREVAILING top of book -- the
most recent L1 update at or before `t`. NaN until the partition's first L1
update; never 0.

`imb_top = (bid_qty - ask_qty) / (bid_qty + ask_qty)` of the same prevailing
quote. Both sizes zero is an ASSERTION, not a branch (D-04-16): 0
occurrences in 17.2M real rows, and a null-propagation path for a case that
has never happened is untested code. A CROSSED or LOCKED book
(`bid_price >= ask_price`) is NOT an error: `mid` and `imb_top` stay
arithmetically well-defined, Phase 3's `check_crossed_locked_book` already
treats it as an informational per-day count, and hard-failing a feature
build on it would make this tier stricter than the curated tier it reads.
Crossed/locked L1 updates are COUNTED and the count is returned in the
state, so Plan 05 can report it. This resolves 04-RESEARCH-NOTES.md
ambiguity #7 explicitly: D-04-16 LOCKED the zero-size case as an assertion;
extending "assertion, not branch" to crossed books was research prose, not
a decision, and it is resolved here toward Phase 3's existing convention.

`ofi` is the Cont-Kukanov-Stoikov per-update `e_n` (arXiv:1011.6402 S2.1),
verbatim:

    e_n =  I{b_n >= b_n-1} * qb_n  -  I{b_n <= b_n-1} * qb_n-1
         - I{a_n <= a_n-1} * qa_n  +  I{a_n >= a_n-1} * qa_n-1

Four INDEPENDENT indicators, never an elif chain: at an unchanged price
both indicators on that side fire, the size-delta case falls out, and a
repeated identical quote is exactly 0.0 with no branch to test.
"Consecutive" means consecutive among L1 rows in `(etime, seq)` merged
order -- trades between two quotes neither reset nor contribute. NaN on the
first L1 update of a partition (undefined, never neutral) and no seeding
from the previous day, which would couple partitions to buy one row in
17.2M. Between L1 updates the value is carried forward, which is what makes
the declared information set `[prev_l1_update, t]` rather than `t`.

THE FOUR ADDITIONS ARE ORDERED, AND THE ORDER IS PART OF THE CONTRACT.
`e` accumulates `+qb`, `-pqb`, `-qa`, `+pqa` in that sequence. Float
addition is not associative, so a re-association changes the last bit -- it
is exactly why the paper's indicator form and the QuestDB CASE form agree
only to 2.84e-14 rather than exactly. `features.kernel` writes the same
four statements in the same order, and neither may be "simplified" into
`(qb - pqb) - (qa - pqa)`.

`trade_flow` is signed volume over the HALF-OPEN window
`(t - TRADE_FLOW_WINDOW_NS, t]` -- a trade at exactly `t` is inside, one at
exactly `t - 1s` is not (D-04-16). It accumulates as `int`, over
`round(qty * QTY_SCALE) * side`, and is divided by `QTY_SCALE` only at
emission (D-04-14): in float64 the value depends on the `etime` tie order
on 95% of decision rows, so reproducibility would depend on a merge
convention. The `round(qty * QTY_SCALE)` round trip is ASSERTED per trade
rather than assumed -- "every real qty is integral at 1e-8" is a
measurement, and an unasserted measured premise is an assumption. An empty
window emits 0.0, not NaN ("no signed volume traded in the last second" is
a measurement, not a gap); the empty-window row count comes back in the
state because the column alone cannot tell that apart from flow exactly
cancelling.

`warmup` is true when fewer than two L1 updates have been seen (so `ofi` is
still undefined) OR `etime < first_event_etime + TRADE_FLOW_WINDOW_NS` (so
the trailing window has not had a full second to fill). One flag, computed
once, travelling with the row (D-04-09); consumers never recompute it.
`post_gap_warmup` is a DIFFERENT flag, joined from Phase 3's
`resync_windows` sidecar, and Plan 05 owns it.

STATUS CODES LIVE HERE, NOT IN THE KERNEL, and `features.kernel` imports
them. Vocabulary only, no logic: the two implementations must not be able
to disagree about what `-2` means, and this module is the one that can be
imported without numba.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from data.time_ns import QTY_SCALE, TRADE_FLOW_WINDOW_NS
from features.event_stream import SOURCE_RANK_BOOKTICKER

__all__ = [
    "STATUS_OK",
    "STATUS_RING_OVERFLOW",
    "STATUS_BOTH_TOP_SIZES_ZERO",
    "STATUS_QTY_NOT_REPRESENTABLE",
    "STATUS_RING_NOT_POWER_OF_TWO",
    "STATUS_ARRAY_LENGTH_MISMATCH",
    "QTY_SCALE_F",
    "STATUS_MESSAGES",
    "FEATURE_OUTPUT_NAMES",
    "FeatureStatusError",
    "ReferenceState",
    "new_reference_state",
    "new_outputs",
    "run_reference",
    "run_reference_checked",
    "status_message",
]

#: Success. Every other status is NEGATIVE, so `status < 0` is the whole
#: error test at a call site and a new code cannot be mistaken for success.
STATUS_OK: int = 0

#: The trailing-window ring is full. Returned, never wrapped: a silently
#: wrapped ring is a feature that looks fine and is wrong (T-04-12).
STATUS_RING_OVERFLOW: int = -1

#: `bid_qty + ask_qty == 0` on the prevailing quote -- D-04-16's assertion.
STATUS_BOTH_TOP_SIZES_ZERO: int = -2

#: A trade quantity is NaN, or `round(qty * QTY_SCALE) / QTY_SCALE` does not
#: reproduce it. The int64 accumulator rests on every real qty being
#: integral at 1e-8; this is that premise, checked.
STATUS_QTY_NOT_REPRESENTABLE: int = -3

#: The caller's ring arrays are not a power-of-two length. The kernel
#: indexes with `& (cap - 1)`, which is only a modulo for powers of two.
STATUS_RING_NOT_POWER_OF_TWO: int = -4

#: An output, input or ring array is not the length the kernel was told to
#: walk. numba does not bounds-check, so an output array one row short is a
#: silent out-of-bounds WRITE -- checked before the loop, never discovered.
STATUS_ARRAY_LENGTH_MISMATCH: int = -5

STATUS_MESSAGES: dict[int, str] = {
    STATUS_OK: "ok",
    STATUS_RING_OVERFLOW: (
        "trailing-window ring buffer is full -- more trades inside one "
        "1 s window than the ring can hold; nothing was wrapped"
    ),
    STATUS_BOTH_TOP_SIZES_ZERO: (
        "imb_top is undefined: bid_qty + ask_qty == 0 on the prevailing "
        "quote (D-04-16 assertion; 0 occurrences in 17.2M real rows, so "
        "this signals feed corruption)"
    ),
    STATUS_QTY_NOT_REPRESENTABLE: (
        "trade qty is NaN or not integral at 1e-8, so the int64 fixed-point "
        "accumulator would not reproduce it"
    ),
    STATUS_RING_NOT_POWER_OF_TWO: (
        "ring buffer length is not a power of two; the kernel indexes with "
        "& (cap - 1), which is a modulo only for powers of two"
    ),
    STATUS_ARRAY_LENGTH_MISMATCH: (
        "an input, output or ring array is not the length the kernel was "
        "asked to walk; numba would have written out of bounds"
    ),
}

#: The kernel's output columns. The first four are catalogue feature names
#: (`spec/features.toml`); `bid_price`/`ask_price` are BOOKKEEPING outputs
#: mirroring the prevailing quote (D-06-17) -- raw observed state, not a
#: derived quantity with an information set, so they are never passed to
#: `spec.catalogue`; `warmup` is the D-04-09 flag that travels with the row,
#: and stays last.
FEATURE_OUTPUT_NAMES: tuple[str, ...] = (
    "mid",
    "imb_top",
    "ofi",
    "trade_flow",
    "bid_price",
    "ask_price",
    "warmup",
)

_NAN: float = math.nan

#: `QTY_SCALE` as a float64, defined ONCE so the reference and the kernel
#: cannot divide by two different constants. `float()` is not a scale
#: conversion: 1e8 is deliberately outside the {1e3, 1e6, 1e9} family
#: `check_ms_to_ns_site` polices, because this is a quantity scale and must
#: not read as a time one.
QTY_SCALE_F: float = float(QTY_SCALE)


class FeatureStatusError(ValueError):
    """A negative status from either implementation, with the offending row.

    `features.kernel.KernelStatusError` subclasses this, so a caller that
    does not care which implementation produced the failure catches one
    exception type.
    """


def status_message(status: int) -> str:
    return STATUS_MESSAGES.get(status, f"unknown status {status}")


@dataclass
class ReferenceState:
    """Carry-in / carry-out state, slot for slot the same as the kernel's
    `state_i64` / `state_f64` / ring pair.

    `window` holds `(etime, scaled_signed_qty)` for every trade currently
    inside the trailing window, oldest first -- the deque IS the kernel's
    ring, minus the capacity. It is deliberately unbounded here: a ring
    overflow is a fact about the kernel's representation, not about the
    feature, and giving the oracle the same limit would hide that.
    """

    # --- the kernel's state_i64 slots ---
    acc_scaled: int = 0
    max_occupancy: int = 0
    n_quotes_seen: int = 0
    first_event_etime: int = 0
    have_first_event: bool = False
    error_row: int = -1
    empty_window_rows: int = 0
    crossed_locked_rows: int = 0
    # --- the kernel's ring_t / ring_v pair, with head/tail implied ---
    window: deque = field(default_factory=deque)
    # --- the kernel's state_f64 slots ---
    prev_bid_price: float = _NAN
    prev_bid_qty: float = _NAN
    prev_ask_price: float = _NAN
    prev_ask_qty: float = _NAN
    last_ofi: float = _NAN


def new_reference_state() -> ReferenceState:
    """A fresh partition's state. Nothing is seeded from a previous day."""
    return ReferenceState()


def new_outputs(n: int) -> dict[str, np.ndarray]:
    """Preallocate the five output arrays a run writes into.

    Allocated by the CALLER, never inside the hot loop, and never a view of
    the input: `features.event_stream.event_arrays` hands back READ-ONLY
    zero-copy views of polars memory, so an output array has to be its own
    allocation.
    """
    return {
        "mid": np.empty(n, dtype=np.float64),
        "imb_top": np.empty(n, dtype=np.float64),
        "ofi": np.empty(n, dtype=np.float64),
        "trade_flow": np.empty(n, dtype=np.float64),
        "bid_price": np.empty(n, dtype=np.float64),
        "ask_price": np.empty(n, dtype=np.float64),
        "warmup": np.empty(n, dtype=np.bool_),
    }


def run_reference(
    etime,
    source_rank,
    bid_price,
    bid_qty,
    ask_price,
    ask_qty,
    trade_qty,
    trade_side,
    out_mid,
    out_bid,
    out_ask,
    out_imb,
    out_ofi,
    out_flow,
    out_warmup,
    state: ReferenceState,
) -> int:
    """Stream `etime.shape[0]` rows through `state`, writing one value per
    row into each output array. Returns `STATUS_OK` or a negative status,
    leaving the offending row index in `state.error_row`.

    On a negative status the run stops at that row; outputs before it have
    been written and are correct. The same discipline as the kernel: never
    raise from the streaming function, because a status plus a row index is
    strictly more information at the call site than an exception message.
    """
    n = etime.shape[0]
    for i in range(n):
        t = int(etime[i])

        if not state.have_first_event:
            state.first_event_etime = t
            state.have_first_event = True

        # --- a trade joins the trailing window ---
        if source_rank[i] != SOURCE_RANK_BOOKTICKER:
            q = float(trade_qty[i])
            if q != q:  # NaN; Python's round() would raise, numba's would not
                state.error_row = i
                return STATUS_QTY_NOT_REPRESENTABLE
            scaled = int(round(q * QTY_SCALE_F))
            if float(scaled) / QTY_SCALE_F != q:
                state.error_row = i
                return STATUS_QTY_NOT_REPRESENTABLE
            signed = scaled * int(trade_side[i])
            state.window.append((t, signed))
            state.acc_scaled += signed

        # --- evict everything at or before t - 1s: HALF-OPEN (t-1s, t] ---
        horizon = t - TRADE_FLOW_WINDOW_NS
        while state.window and state.window[0][0] <= horizon:
            state.acc_scaled -= state.window.popleft()[1]

        occupancy = len(state.window)
        if occupancy > state.max_occupancy:
            state.max_occupancy = occupancy

        # --- an L1 update advances the quote state and produces e_n ---
        if source_rank[i] == SOURCE_RANK_BOOKTICKER:
            b = float(bid_price[i])
            qb = float(bid_qty[i])
            a = float(ask_price[i])
            qa = float(ask_qty[i])
            if state.n_quotes_seen >= 1:
                # Four independent indicators, accumulated in THIS order.
                # See the module docstring: the order is part of the
                # contract, not a formatting choice.
                e = 0.0
                if b >= state.prev_bid_price:
                    e += qb
                if b <= state.prev_bid_price:
                    e -= state.prev_bid_qty
                if a <= state.prev_ask_price:
                    e -= qa
                if a >= state.prev_ask_price:
                    e += state.prev_ask_qty
                state.last_ofi = e
            state.prev_bid_price = b
            state.prev_bid_qty = qb
            state.prev_ask_price = a
            state.prev_ask_qty = qa
            state.n_quotes_seen += 1
            if b >= a:
                state.crossed_locked_rows += 1

        # --- emit ---
        if state.n_quotes_seen == 0:
            out_mid[i] = _NAN
            out_bid[i] = _NAN
            out_ask[i] = _NAN
            out_imb[i] = _NAN
        else:
            out_mid[i] = (state.prev_bid_price + state.prev_ask_price) / 2
            out_bid[i] = state.prev_bid_price
            out_ask[i] = state.prev_ask_price
            denominator = state.prev_bid_qty + state.prev_ask_qty
            if denominator == 0.0:
                state.error_row = i
                return STATUS_BOTH_TOP_SIZES_ZERO
            out_imb[i] = (state.prev_bid_qty - state.prev_ask_qty) / denominator

        out_ofi[i] = state.last_ofi

        if occupancy == 0:
            state.empty_window_rows += 1
        # float(int) / float(int) is what numba's int64 / int64 compiles to;
        # written this way so the two implementations divide identically.
        out_flow[i] = float(state.acc_scaled) / QTY_SCALE_F

        out_warmup[i] = (
            state.n_quotes_seen < 2
            or t < state.first_event_etime + TRADE_FLOW_WINDOW_NS
        )

    return STATUS_OK


def run_reference_checked(
    events: dict[str, np.ndarray],
    *,
    state: ReferenceState | None = None,
    out: dict[str, np.ndarray] | None = None,
) -> dict[str, np.ndarray]:
    """`run_reference` over an `event_arrays` dict, raising on a negative
    status. This is what everything outside this module calls.

    `state=None` starts a fresh partition; passing a state back in resumes
    a stream mid-flight with bit-identical output.
    """
    if state is None:
        state = new_reference_state()
    n = events["etime"].shape[0]
    if out is None:
        out = new_outputs(n)

    status = run_reference(
        events["etime"],
        events["source_rank"],
        events["bid_price"],
        events["bid_qty"],
        events["ask_price"],
        events["ask_qty"],
        events["trade_qty"],
        events["trade_side"],
        out["mid"],
        out["bid_price"],
        out["ask_price"],
        out["imb_top"],
        out["ofi"],
        out["trade_flow"],
        out["warmup"],
        state,
    )
    if status != STATUS_OK:
        raise FeatureStatusError(_status_detail("run_reference", status, state, events))
    return out


def _status_detail(
    where: str, status: int, state, events: dict[str, np.ndarray]
) -> str:
    """The message both wrappers raise with: the status, its meaning, the
    offending row index, and that row's values -- so the failure points at
    a row rather than at a summary."""
    row = int(state.error_row) if hasattr(state, "error_row") else -1
    detail = f"{where}: status {status} ({status_message(status)}) at row {row}"
    if 0 <= row < events["etime"].shape[0]:
        values = ", ".join(
            f"{name}={events[name][row]}"
            for name in (
                "etime",
                "source_rank",
                "seq",
                "bid_price",
                "bid_qty",
                "ask_price",
                "ask_qty",
                "trade_qty",
                "trade_side",
            )
        )
        detail = f"{detail}: {values}"
    return detail
