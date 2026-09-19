"""The one streaming kernel: `mid`, `imb_top`, `ofi` and `trade_flow` in a
single `@njit(cache=True)` pass over the merged event array (FEAT-02).

D-04-02 -- ONE CODE PATH. Training, inference and the simulator call
`run_kernel_checked`. Nothing recomputes a feature anywhere else, and the
control that makes that claim mean something is a runtime one, not a
convention: `tests/features/test_kernel.py` proves that one call over a
whole stream and N calls over its chunks emit byte-identical values, which
is exactly the difference between the trainer's call shape and the
simulator's.

WHAT THIS MODULE IS NOT. It is not the definition of the features -- that
is `features/reference.py`, written to be read, with the semantics, the
edge cases and the reasons in its docstring. This file is the same
arithmetic expressed so numba can compile it, and it is checked against the
reference bitwise on every fixture plus hypothesis-generated streams. If
the two ever disagree, the reference is right and this is a bug.

=============================== STATE CONTRACT ==============================

Later plans and the simulator conform to THIS. `new_state()` returns four
arrays -- there are no Python objects in the hot path (D-04-10), so the
state is arrays and the slot names are index constants:

    state_i64, state_f64, ring_t, ring_v = new_state()

`state_i64`, indexed by `STATE_I64_SLOTS`:

    head, tail            ring-buffer cursors; the window holds
                          `(tail - head) & (cap - 1)` trades
    acc_scaled            signed volume inside the window, int64 fixed
                          point at 1e-8 (D-04-14) -- divided by QTY_SCALE
                          only at emission
    max_occupancy         high-water mark of the window, sampled AFTER
                          eviction; the number the ring is sized against
    n_quotes_seen         L1 updates so far; ofi is NaN below 2
    first_event_etime     the partition's first row's etime, for warmup
    have_first_event      0 until the first row has been seen
    error_row             the offending row index of the last negative
                          status; -1 when no status has been returned
    empty_window_rows     rows whose trailing window was empty (4.2% of a
                          real day) -- the column emits 0.0 there, and only
                          this counter can tell that apart from flow
                          exactly cancelling
    crossed_locked_rows   L1 updates with bid >= ask; COUNTED, not refused

`state_f64`, indexed by `STATE_F64_SLOTS`: `prev_bid_price`, `prev_bid_qty`,
`prev_ask_price`, `prev_ask_qty` -- the prevailing top of book -- and
`last_ofi`.

`last_ofi` IS PART OF THE STATE, and it is the one slot the plan's sketch
did not list. It has to be: `ofi` is carried forward between L1 updates (a
trade decision row emits the most recent quote's `e_n`), and unlike `mid`
and `imb_top` it cannot be recomputed from `prev_*`. Without it a chunk
boundary between two quotes would emit NaN, and on the real 2026-09-13 day
the NaN count would be 329,581 -- one per trade decision row -- instead of
1.

RING CAPACITY IS THE ARRAY'S LENGTH, NOT THE MODULE CONSTANT. `new_state()`
allocates `RING_CAPACITY` entries and that is what production uses, but the
kernel reads `cap = ring_t.shape[0]`, so a test can hand it a cap-8 ring
and watch the overflow guard fire. `& (cap - 1)` still replaces the `%`
division -- the mask works for any power-of-two cap, runtime value or not
-- and a cap that is NOT a power of two is refused before the first row,
because `& (cap - 1)` would silently corrupt the window rather than wrap it
cleanly.

NEVER RAISE FROM INSIDE THE `@njit` FUNCTION. numba's exception support
costs the compile-time specialisation and hands back only a message; a
negative status plus `state_i64["error_row"]` is strictly more information
at the call site. `run_kernel_checked` is the thin Python wrapper that
turns one into a `KernelStatusError` naming the status, the row and that
row's values, and it is what everything outside this module calls.

OUTPUTS ARE WRITTEN FOR EVERY ROW. State accumulates everywhere; the caller
selects decision rows with `event_stream.decision_row_index` (D-04-01).
Decision-row selection is deliberately NOT folded in here: keeping it a
pure function of position is what lets the leakage properties delete rows
after `t` and re-derive the same decision rows for the prefix.

OUTPUT ARRAYS ARE THE CALLER'S. `event_arrays` hands back READ-ONLY
zero-copy views of polars memory, so an output array cannot be one of them;
nothing is allocated inside the loop.

`cache=True` writes its `*.nbi`/`*.nbc` next to the defining source file
unless `NUMBA_CACHE_DIR` is set. `tests/conftest.py` pins it before any
import can reach numba; a run OUTSIDE pytest must export it too (T-04-14).
"""

from __future__ import annotations

import numpy as np
from numba import njit

from data.time_ns import QTY_SCALE, TRADE_FLOW_WINDOW_NS
from features.event_stream import SOURCE_RANK_BOOKTICKER
from features.reference import (
    QTY_SCALE_F,
    STATUS_ARRAY_LENGTH_MISMATCH,
    STATUS_BOTH_TOP_SIZES_ZERO,
    STATUS_OK,
    STATUS_QTY_NOT_REPRESENTABLE,
    STATUS_RING_NOT_POWER_OF_TWO,
    STATUS_RING_OVERFLOW,
    FeatureStatusError,
    _status_detail,
    new_outputs,
)

__all__ = [
    "RING_CAPACITY",
    "STATE_I64_SLOTS",
    "STATE_F64_SLOTS",
    "KernelStatusError",
    "new_state",
    "run_kernel",
    "run_kernel_checked",
]

#: 1<<16 entries x 16 B = 1 MB, against a MEASURED maximum real window
#: occupancy of 5,092 trades on 2026-09-13 -- 12.9x headroom. A power of
#: two so `& (RING_CAPACITY - 1)` replaces a modulo division.
RING_CAPACITY: int = 1 << 16

# Slot indices. Every one is UPPER_CASE because `check_numba_globals` lets
# an @njit function read a module-level global only when it is an
# UPPER_CASE compile-time constant -- everything else must be a parameter.
# The kernel indexes with these; Python callers use the maps below.
SLOT_HEAD: int = 0
SLOT_TAIL: int = 1
SLOT_ACC_SCALED: int = 2
SLOT_MAX_OCCUPANCY: int = 3
SLOT_N_QUOTES_SEEN: int = 4
SLOT_FIRST_EVENT_ETIME: int = 5
SLOT_HAVE_FIRST_EVENT: int = 6
SLOT_ERROR_ROW: int = 7
SLOT_EMPTY_WINDOW_ROWS: int = 8
SLOT_CROSSED_LOCKED_ROWS: int = 9

SLOT_PREV_BID_PRICE: int = 0
SLOT_PREV_BID_QTY: int = 1
SLOT_PREV_ASK_PRICE: int = 2
SLOT_PREV_ASK_QTY: int = 3
SLOT_LAST_OFI: int = 4

#: Name -> index. Declaration order IS the array order, and
#: `test_the_state_slot_maps_are_the_documented_contract` asserts it.
STATE_I64_SLOTS: dict[str, int] = {
    "head": SLOT_HEAD,
    "tail": SLOT_TAIL,
    "acc_scaled": SLOT_ACC_SCALED,
    "max_occupancy": SLOT_MAX_OCCUPANCY,
    "n_quotes_seen": SLOT_N_QUOTES_SEEN,
    "first_event_etime": SLOT_FIRST_EVENT_ETIME,
    "have_first_event": SLOT_HAVE_FIRST_EVENT,
    "error_row": SLOT_ERROR_ROW,
    "empty_window_rows": SLOT_EMPTY_WINDOW_ROWS,
    "crossed_locked_rows": SLOT_CROSSED_LOCKED_ROWS,
}

STATE_F64_SLOTS: dict[str, int] = {
    "prev_bid_price": SLOT_PREV_BID_PRICE,
    "prev_bid_qty": SLOT_PREV_BID_QTY,
    "prev_ask_price": SLOT_PREV_ASK_PRICE,
    "prev_ask_qty": SLOT_PREV_ASK_QTY,
    "last_ofi": SLOT_LAST_OFI,
}


class KernelStatusError(FeatureStatusError):
    """A negative status from `run_kernel`, named and located.

    Subclasses `FeatureStatusError` so a caller that does not care which
    implementation produced the failure catches one type.
    """


def new_state(
    capacity: int = RING_CAPACITY,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """A fresh partition's carry-in state: `(state_i64, state_f64, ring_t,
    ring_v)`. Nothing is seeded from a previous day (D-04-04)."""
    if capacity <= 0 or capacity & (capacity - 1):
        raise ValueError(
            f"new_state: ring capacity {capacity} is not a positive power of "
            "two; the kernel indexes with & (cap - 1)"
        )
    state_i64 = np.zeros(len(STATE_I64_SLOTS), dtype=np.int64)
    state_i64[SLOT_ERROR_ROW] = -1
    state_f64 = np.full(len(STATE_F64_SLOTS), np.nan, dtype=np.float64)
    return (
        state_i64,
        state_f64,
        np.empty(capacity, dtype=np.int64),
        np.empty(capacity, dtype=np.int64),
    )


@njit(cache=True)
def run_kernel(
    etime,
    source_rank,
    bid_price,
    bid_qty,
    ask_price,
    ask_qty,
    trade_qty,
    trade_side,
    out_mid,
    out_imb,
    out_ofi,
    out_flow,
    out_warmup,
    ring_t,
    ring_v,
    state_i64,
    state_f64,
):
    """Stream every row of `etime` through the carried state, writing one
    value per row into each preallocated output array.

    Returns `STATUS_OK` (0) or a negative status, with the offending row
    index in `state_i64[SLOT_ERROR_ROW]`. Row-by-row semantics are pinned
    and explained in `features/reference.py`; this is the same arithmetic,
    in the same order.
    """
    n = etime.shape[0]
    cap = ring_t.shape[0]

    if cap <= 0 or (cap & (cap - 1)) != 0:
        state_i64[SLOT_ERROR_ROW] = -1
        return STATUS_RING_NOT_POWER_OF_TWO

    # numba does not bounds-check: an output array one row short would be a
    # silent out-of-bounds write, not a crash.
    if (
        ring_v.shape[0] != cap
        or source_rank.shape[0] != n
        or bid_price.shape[0] != n
        or bid_qty.shape[0] != n
        or ask_price.shape[0] != n
        or ask_qty.shape[0] != n
        or trade_qty.shape[0] != n
        or trade_side.shape[0] != n
        or out_mid.shape[0] != n
        or out_imb.shape[0] != n
        or out_ofi.shape[0] != n
        or out_flow.shape[0] != n
        or out_warmup.shape[0] != n
    ):
        state_i64[SLOT_ERROR_ROW] = -1
        return STATUS_ARRAY_LENGTH_MISMATCH

    mask = cap - 1

    head = state_i64[SLOT_HEAD]
    tail = state_i64[SLOT_TAIL]
    acc = state_i64[SLOT_ACC_SCALED]
    max_occupancy = state_i64[SLOT_MAX_OCCUPANCY]
    n_quotes_seen = state_i64[SLOT_N_QUOTES_SEEN]
    first_event_etime = state_i64[SLOT_FIRST_EVENT_ETIME]
    have_first_event = state_i64[SLOT_HAVE_FIRST_EVENT]
    empty_window_rows = state_i64[SLOT_EMPTY_WINDOW_ROWS]
    crossed_locked_rows = state_i64[SLOT_CROSSED_LOCKED_ROWS]

    prev_bid_price = state_f64[SLOT_PREV_BID_PRICE]
    prev_bid_qty = state_f64[SLOT_PREV_BID_QTY]
    prev_ask_price = state_f64[SLOT_PREV_ASK_PRICE]
    prev_ask_qty = state_f64[SLOT_PREV_ASK_QTY]
    last_ofi = state_f64[SLOT_LAST_OFI]

    status = STATUS_OK
    error_row = -1

    for i in range(n):
        t = etime[i]

        if have_first_event == 0:
            first_event_etime = t
            have_first_event = 1

        # --- a trade joins the trailing window ---
        if source_rank[i] != SOURCE_RANK_BOOKTICKER:
            q = trade_qty[i]
            if q != q:  # NaN
                status = STATUS_QTY_NOT_REPRESENTABLE
                error_row = i
                break
            scaled = np.int64(round(q * QTY_SCALE_F))
            if np.float64(scaled) / QTY_SCALE_F != q:
                status = STATUS_QTY_NOT_REPRESENTABLE
                error_row = i
                break
            signed = scaled * np.int64(trade_side[i])
            ring_t[tail] = t
            ring_v[tail] = signed
            acc += signed
            tail = (tail + 1) & mask
            if tail == head:
                # A full ring is indistinguishable from an empty one, so
                # the entry just written is the last that fits. Refuse --
                # never wrap: a silently wrapped ring is a feature that
                # looks fine and is wrong (T-04-12).
                status = STATUS_RING_OVERFLOW
                error_row = i
                break

        # --- evict everything at or before t - 1s: HALF-OPEN (t-1s, t] ---
        horizon = t - TRADE_FLOW_WINDOW_NS
        while head != tail and ring_t[head] <= horizon:
            acc -= ring_v[head]
            head = (head + 1) & mask

        occupancy = (tail - head) & mask
        if occupancy > max_occupancy:
            max_occupancy = occupancy

        # --- an L1 update advances the quote state and produces e_n ---
        if source_rank[i] == SOURCE_RANK_BOOKTICKER:
            b = bid_price[i]
            qb = bid_qty[i]
            a = ask_price[i]
            qa = ask_qty[i]
            if n_quotes_seen >= 1:
                # Four independent indicators, accumulated in THIS order --
                # the same four statements in the same order as
                # features/reference.py. Float addition is not associative,
                # so the order is part of the contract, not formatting.
                e = 0.0
                if b >= prev_bid_price:
                    e += qb
                if b <= prev_bid_price:
                    e -= prev_bid_qty
                if a <= prev_ask_price:
                    e -= qa
                if a >= prev_ask_price:
                    e += prev_ask_qty
                last_ofi = e
            prev_bid_price = b
            prev_bid_qty = qb
            prev_ask_price = a
            prev_ask_qty = qa
            n_quotes_seen += 1
            if b >= a:
                crossed_locked_rows += 1

        # --- emit ---
        if n_quotes_seen == 0:
            out_mid[i] = np.nan
            out_imb[i] = np.nan
        else:
            out_mid[i] = (prev_bid_price + prev_ask_price) / 2
            denominator = prev_bid_qty + prev_ask_qty
            if denominator == 0.0:
                status = STATUS_BOTH_TOP_SIZES_ZERO
                error_row = i
                break
            out_imb[i] = (prev_bid_qty - prev_ask_qty) / denominator

        out_ofi[i] = last_ofi

        if occupancy == 0:
            empty_window_rows += 1
        # int64 / int64 in numba is float(a) / float(b); the reference
        # writes it that way explicitly so the two divide identically.
        out_flow[i] = acc / QTY_SCALE

        out_warmup[i] = n_quotes_seen < 2 or t < first_event_etime + (
            TRADE_FLOW_WINDOW_NS
        )

    state_i64[SLOT_HEAD] = head
    state_i64[SLOT_TAIL] = tail
    state_i64[SLOT_ACC_SCALED] = acc
    state_i64[SLOT_MAX_OCCUPANCY] = max_occupancy
    state_i64[SLOT_N_QUOTES_SEEN] = n_quotes_seen
    state_i64[SLOT_FIRST_EVENT_ETIME] = first_event_etime
    state_i64[SLOT_HAVE_FIRST_EVENT] = have_first_event
    state_i64[SLOT_EMPTY_WINDOW_ROWS] = empty_window_rows
    state_i64[SLOT_CROSSED_LOCKED_ROWS] = crossed_locked_rows
    if status != STATUS_OK:
        state_i64[SLOT_ERROR_ROW] = error_row

    state_f64[SLOT_PREV_BID_PRICE] = prev_bid_price
    state_f64[SLOT_PREV_BID_QTY] = prev_bid_qty
    state_f64[SLOT_PREV_ASK_PRICE] = prev_ask_price
    state_f64[SLOT_PREV_ASK_QTY] = prev_ask_qty
    state_f64[SLOT_LAST_OFI] = last_ofi

    return status


class _StateView:
    """Adapter so one message builder serves both implementations."""

    __slots__ = ("error_row",)

    def __init__(self, error_row: int) -> None:
        self.error_row = error_row


def run_kernel_checked(
    events: dict[str, np.ndarray],
    *,
    state: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None,
    out: dict[str, np.ndarray] | None = None,
) -> dict[str, np.ndarray]:
    """`run_kernel` over an `event_arrays` dict, raising `KernelStatusError`
    on a negative status. This is what everything outside this module calls.

    `state=None` starts a fresh partition; passing a state back in resumes
    a stream mid-flight with bit-identical output.
    """
    if state is None:
        state = new_state()
    state_i64, state_f64, ring_t, ring_v = state
    n = events["etime"].shape[0]
    if out is None:
        out = new_outputs(n)

    status = run_kernel(
        events["etime"],
        events["source_rank"],
        events["bid_price"],
        events["bid_qty"],
        events["ask_price"],
        events["ask_qty"],
        events["trade_qty"],
        events["trade_side"],
        out["mid"],
        out["imb_top"],
        out["ofi"],
        out["trade_flow"],
        out["warmup"],
        ring_t,
        ring_v,
        state_i64,
        state_f64,
    )
    if status != STATUS_OK:
        raise KernelStatusError(
            _status_detail(
                "run_kernel", status, _StateView(int(state_i64[SLOT_ERROR_ROW])), events
            )
        )
    return out
