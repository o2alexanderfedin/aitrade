"""The event-driven simulator's rule, written straight (D-06-09 #4): a
pure-Python twin of `sim.kernel.run_sim` that shares no arithmetic, no
buffer and no state representation with the thing it checks -- plain
Python `int`s throughout (never a numpy scalar, never int64 wraparound
semantics), no numba, no shared module-level constant beyond the same
`sim.ticks` values the kernel itself imports (this twin MAY call
`sim.ticks.position_size_ticks` directly, since it is not `@njit`-
constrained; the kernel cannot, and re-derives the same arithmetic inline
-- this module IS the oracle that inline arithmetic is checked against).

Never imports anything from `sim.kernel` -- `tests/sim/test_kernel.py`'s
hypothesis sweep is only a real proof of independence if the two
implementations cannot accidentally converge by sharing code.

Re-derives every rule `sim/kernel.py`'s module docstring states: the same
symmetric-by-direction floor/ceil quantisation, the same flip-only state
machine, the same fresh-at-fill-price sizing (Q13), the same bid/ask
equity mark (never a rounded mid). If this module and the kernel ever disagree, one of them has
a bug -- the hypothesis sweep is what would catch it.
"""

from __future__ import annotations

import math

import numpy as np

from sim.outputs import SimResult, new_trade_log
from sim.ticks import (
    PRICE_SCALE,
    TICK_SIZE_SCALED,
    X_TICKS_DENOMINATOR,
    position_size_ticks,
    resolve_x_bps_scaled,
)

__all__ = ["run_reference_sim"]


def run_reference_sim(
    etime: np.ndarray,
    bid_ticks: np.ndarray,
    ask_ticks: np.ndarray,
    pred: np.ndarray,
    *,
    x_bps: int | float | None = None,
    x_bps_scaled: int | float | None = None,
    max_notional_scaled: int | None = None,
    lot_step_scaled: int | None = None,
    fee_bps: int = 0,
    latency_ns: int = 0,
) -> SimResult:
    """The kernel's rule, in plain Python. `fee_bps`/`latency_ns` are
    accepted and ignored, exactly like the kernel (D-06-16). Raises
    `ValueError` on a non-finite/negative prediction or a zero-lot fill --
    a plain exception is enough here, since the hypothesis strategy this
    twin is checked against is designed to stay inside OK-status territory
    (see `tests/sim/test_kernel.py`'s strategy docstring); this twin is
    not required to reproduce the kernel's specific status-code vocabulary,
    only its arithmetic on the happy path.

    `x_bps` (whole basis points) and `x_bps_scaled` (units of
    `1 / sim.ticks.X_BPS_SCALE` bps) are the kernel's own two spellings of one
    threshold, resolved through the same `sim.ticks.resolve_x_bps_scaled` the
    kernel uses -- `sim.ticks` is the one module this twin is allowed to share
    with the thing it checks (this module's own docstring, and the precedent
    that it calls `position_size_ticks` directly). The int64 overflow bound is
    NOT re-checked here: plain Python ints do not wrap, so this twin would
    happily compute a product the kernel must refuse, and that asymmetry is
    exactly what `run_sim_checked`'s own guard exists to prevent from reaching
    the kernel at all.
    """
    n = int(etime.shape[0])
    threshold_scaled = resolve_x_bps_scaled(x_bps=x_bps, x_bps_scaled=x_bps_scaled)
    trade_log = new_trade_log(n)
    equity_scaled = np.empty(n, dtype=np.int64)

    position = 0
    entry_price_ticks = 0
    qty_scaled = 0
    realized_pnl_scaled = 0
    fill_count = 0
    flip_count = 0
    rows_in_market = 0

    position_size_kwargs = {}
    if max_notional_scaled is not None:
        position_size_kwargs["max_notional_scaled"] = int(max_notional_scaled)
    if lot_step_scaled is not None:
        position_size_kwargs["lot_step_scaled"] = int(lot_step_scaled)

    for i in range(n):
        p = float(pred[i])
        if not math.isfinite(p):
            raise ValueError(f"run_reference_sim: pred[{i}]={p!r} is not finite")

        s = int(round(p * PRICE_SCALE))
        if s < 0:
            raise ValueError(f"run_reference_sim: pred[{i}]={p!r} scales negative")
        # Symmetric-by-direction quantisation (D-06-07, fixed
        # 2026-09-24): floor(pred) gates the long side, ceil(pred) gates
        # the short side -- see sim/kernel.py's module docstring for the
        # full derivation and why a single round-half-up value (the
        # predecessor rule) was asymmetric.
        pred_ticks_floor = s // TICK_SIZE_SCALED
        pred_ticks_ceil = -((-s) // TICK_SIZE_SCALED)

        b = int(bid_ticks[i])
        a = int(ask_ticks[i])
        x_ticks = (b + a) * threshold_scaled // X_TICKS_DENOMINATOR

        long_trigger = pred_ticks_floor > a + x_ticks
        short_trigger = pred_ticks_ceil < b - x_ticks

        triggered = False
        is_flip = False
        new_position = position
        fill_price_ticks = 0
        side = 0

        if position == 0:
            if long_trigger:
                triggered = True
                new_position = 1
                fill_price_ticks = a
                side = 1
            elif short_trigger:
                triggered = True
                new_position = -1
                fill_price_ticks = b
                side = -1
        elif position == 1:
            if short_trigger:
                triggered = True
                is_flip = True
                new_position = -1
                fill_price_ticks = b
                side = -1
        else:  # position == -1
            if long_trigger:
                triggered = True
                is_flip = True
                new_position = 1
                fill_price_ticks = a
                side = 1

        if triggered:
            # Fresh sizing at the CURRENT fill price (Q13), via the real
            # oracle function directly -- this twin is not @njit-bound.
            new_qty_scaled = position_size_ticks(
                fill_price_ticks, **position_size_kwargs
            )

            if is_flip:
                realized_pnl_scaled += (
                    position * (fill_price_ticks - entry_price_ticks) * qty_scaled
                )

            trade_log["etime"][fill_count] = int(etime[i])
            trade_log["side"][fill_count] = side
            trade_log["price_ticks"][fill_count] = fill_price_ticks
            trade_log["qty_scaled"][fill_count] = new_qty_scaled
            trade_log["position_after"][fill_count] = new_position
            fill_count += 1
            if is_flip:
                flip_count += 1

            position = new_position
            entry_price_ticks = fill_price_ticks
            qty_scaled = new_qty_scaled

        if position == 1:
            unrealized = (b - entry_price_ticks) * qty_scaled
        elif position == -1:
            unrealized = (entry_price_ticks - a) * qty_scaled
        else:
            unrealized = 0
        equity_scaled[i] = realized_pnl_scaled + unrealized

        if position != 0:
            rows_in_market += 1

    counters = {
        "trades": fill_count,
        "flips": flip_count,
        "rows_in_market": rows_in_market,
    }
    return SimResult(
        trade_log=trade_log,
        fill_count=fill_count,
        equity_scaled=equity_scaled,
        counters=counters,
    )
