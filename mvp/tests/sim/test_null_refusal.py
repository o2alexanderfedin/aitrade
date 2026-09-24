"""D-06-15: the simulator refuses a non-finite `pred` AT the offending row,
naming it, rather than silently skipping or propagating a NaN into the
P&L; and 06-RESEARCH.md Q10's forced reading of D-06-09's "null-labelled
rows excluded" -- "excluded" means DROPPED before the simulator ever sees
the row, never "present but NaN".

Written FIRST, per this file's `tdd="true"` task. Both `run_sim_checked`'s
`STATUS_NON_FINITE_PRED` path (`sim/kernel.py`) and `arrays.sim_arrays`'s
`null_count() == 0` assertion (`sim/arrays.py`, tested directly in
`tests/sim/test_kernel.py::test_arrays_refuse_a_nullable_bid_or_ask_column`,
Plan 06-03) already exist -- this plan's own action text expects "no
production code changes", only a gap-closing fix if either test below
finds one. None was found (see 06-05-SUMMARY.md); both behaviours passed
against the already-committed 06-03 implementation, so no RED from a
missing feature -- but a genuine RED WAS observed by disabling the
non-finite check directly (see this file's own mutation check, transcribed
in 06-05-SUMMARY.md), which is the only way to prove a refusal test
actually exercises the refusal path rather than passing vacuously.
"""

from __future__ import annotations

import numpy as np
import pytest

from sim.kernel import (
    STATE_I64_SLOTS,
    STATUS_MESSAGES,
    STATUS_NON_FINITE_PRED,
    SimStatusError,
    new_state,
    run_sim_checked,
)
from sim.reference import run_reference_sim
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED


def _price_at_ticks(ticks: int) -> float:
    """A raw price (the scale `pred` arrives in) at a given tick count --
    mirrors `tests/sim/test_kernel.py`'s own local helper of the same
    name (duplicated here, not imported, matching that file's own
    no-cross-test-module-import convention)."""
    return ticks * TICK_SIZE_SCALED / PRICE_SCALE


def test_non_finite_pred_stops_the_run_at_that_row_not_after():
    """A 10-row sequence: rows 0 and 2 trade (an entry, then a flip),
    rows 1/3/4 are deliberate no-ops, row 5 is the offending NaN, and rows
    6-9 are each, individually, a would-be flip-triggering prediction --
    included specifically so that if the kernel wrongly continued past
    row 5 instead of stopping there, `fill_count` would end up > 2 and
    `error_row` would end up != 5, and this test would catch it. `state`
    is passed in explicitly so it can be inspected after the raise (the
    kernel writes state back before returning on every path, including
    error paths -- `sim/kernel.py`'s own module docstring, "STATE
    CONTRACT")."""
    etime = np.arange(1, 11, dtype=np.int64)
    bid_ticks = np.full(10, 500_000, dtype=np.int64)
    ask_ticks = np.full(10, 500_010, dtype=np.int64)

    preds = [0.0] * 10
    preds[0] = _price_at_ticks(500_100)  # flat -> long entry (fill 1)
    preds[1] = _price_at_ticks(500_100)  # long, same-direction: no-op
    preds[2] = _price_at_ticks(499_900)  # long -> short flip (fill 2)
    preds[3] = _price_at_ticks(500_005)  # short, no cross: no-op
    preds[4] = _price_at_ticks(500_005)  # short, no cross: no-op
    preds[5] = float("nan")  # THE OFFENDING ROW
    for i in range(6, 10):
        preds[i] = _price_at_ticks(500_100)  # would flip short->long if reached
    pred = np.array(preds, dtype=np.float64)

    state = new_state()
    with pytest.raises(SimStatusError) as exc_info:
        run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0, state=state)

    message = str(exc_info.value)
    # Names the status (not just "an error happened") ...
    assert STATUS_MESSAGES[STATUS_NON_FINITE_PRED] in message
    # ... and the row, and that row's own values -- mirrors
    # `features/reference.py:_status_detail`'s "row plus values" shape.
    assert "row 5" in message
    assert f"etime={int(etime[5])}" in message
    assert f"bid_ticks={int(bid_ticks[5])}" in message
    assert f"ask_ticks={int(ask_ticks[5])}" in message
    assert "pred=nan" in message.lower()

    # Stopped AT row 5, not merely "eventually erred": state reflects only
    # rows 0-4's processing (the row-0 entry and the row-2 flip, 2 trades,
    # 1 flip) -- the row-6..9 triggers that would have fired had the loop
    # continued past the NaN never happened.
    assert int(state[STATE_I64_SLOTS["error_row"]]) == 5
    assert int(state[STATE_I64_SLOTS["fill_count"]]) == 2
    assert int(state[STATE_I64_SLOTS["flip_count"]]) == 1


def _build_dense_sequence(n: int):
    """A deterministic, no-RNG `n`-row sequence with real trades, small
    enough to reason about by hand but dense enough to trade more than
    once -- mirrors `tests/sim/test_determinism.py`'s own generator
    formula (duplicated here rather than imported, same reasoning: this
    file's `tests/sim/` package has no `__init__.py`, D-06-02, so
    cross-test-module import is not the established pattern in this
    package -- see `test_kernel.py`'s and `test_determinism.py`'s own
    identical `_price_at_ticks` duplication)."""
    etimes: list[int] = []
    bids: list[int] = []
    asks: list[int] = []
    preds: list[float] = []
    t = 0
    for i in range(n):
        t += 100 + (i % 7)
        bid = 500_000 + ((i * 37) % 401) - 200
        spread = 1 + (i % 5)
        ask = bid + spread
        offset = ((i * 13) % 81) - 40
        pred_ticks_target = (bid + ask) // 2 + offset
        etimes.append(t)
        bids.append(bid)
        asks.append(ask)
        preds.append(_price_at_ticks(pred_ticks_target))
    return etimes, bids, asks, preds


def test_dropped_null_rows_produce_a_correct_non_contiguous_run():
    """06-RESEARCH.md Q10's forced reading: a caller-side pre-filter drops
    null-labelled rows entirely BEFORE the simulator ever sees them (never
    feeds a NaN `pred` for them) -- so the simulator's own input can have
    real, non-uniform gaps in `etime` between consecutive decision rows.
    Dropping 5 NON-ADJACENT rows out of 20 proves the kernel places no
    adjacency assumption on `etime` spacing: it only ever reads `etime[i]`
    to copy it into the trade log, never to derive a time delta, so this
    test PINS that contract (rather than being the only possible proof of
    it) by checking the kernel's actual output against the independently
    written reference twin on the identical filtered sequence.
    """
    n = 20
    etimes, bids, asks, preds = _build_dense_sequence(n)

    # 5 non-adjacent "would have been null" rows, spread across the middle
    # of the sequence (never the first or last row).
    drop_indices = {2, 5, 9, 13, 17}
    keep = [i for i in range(n) if i not in drop_indices]
    assert len(keep) == 15

    etime = np.array([etimes[i] for i in keep], dtype=np.int64)
    bid_ticks = np.array([bids[i] for i in keep], dtype=np.int64)
    ask_ticks = np.array([asks[i] for i in keep], dtype=np.int64)
    pred = np.array([preds[i] for i in keep], dtype=np.float64)

    # Real, non-unit gaps between consecutive kept rows (a dropped row's
    # own etime never appears) -- the rows are strictly increasing but
    # NOT evenly spaced the way a naive "assume adjacency" implementation
    # might expect.
    gaps = np.diff(etime)
    assert np.all(gaps > 0), "etime must stay strictly increasing after the drop"
    assert len(set(gaps.tolist())) > 1, (
        "the gaps must be genuinely non-uniform, not an accidental "
        "constant stride that a contiguity assumption would also satisfy"
    )

    kernel_result = run_sim_checked(etime, bid_ticks, ask_ticks, pred, x_bps=0)
    reference_result = run_reference_sim(etime, bid_ticks, ask_ticks, pred, x_bps=0)

    assert kernel_result.fill_count > 0, "vacuous: no trades on the filtered sequence"
    assert kernel_result.fill_count == reference_result.fill_count
    k = kernel_result.fill_count
    for name in ("etime", "side", "price_ticks", "qty_scaled", "position_after"):
        assert np.array_equal(
            kernel_result.trade_log[name][:k], reference_result.trade_log[name][:k]
        ), name
    assert np.array_equal(kernel_result.equity_scaled, reference_result.equity_scaled)
    assert kernel_result.counters == reference_result.counters
