"""The trade-log/equity-curve/counters assembly a simulator run returns
(D-06-13).

`new_trade_log(n)` preallocates at `n` -- the safe upper bound
(06-RESEARCH.md Q3, "Pattern 1": `n_decision_rows` is the only trade count
provable without assuming anything about the caller's prediction quality; a
zero-skill predictor was measured trading 1.88M times on one real day, 858x
the perfect-foresight count). The unfilled TAIL past `fill_count` is
UNINITIALISED MEMORY (`np.empty`, never zeroed) -- exactly the hazard
06-RESEARCH.md Q3 flags for D-06-14's later cross-run/cross-process
hashing (Plan 06-05+). ANY caller that hashes or compares a trade log MUST
slice every column to `[:fill_count]` first; that is why `SimResult` names
`fill_count` as its own field rather than handing back only the raw
arrays -- a caller cannot reach the trade log without also seeing the one
number that makes reading it safe.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

__all__ = ["new_trade_log", "SimResult"]


def new_trade_log(n: int) -> dict[str, np.ndarray]:
    """Preallocate the five trade-log columns at length `n`, uninitialised.

    `etime`/`price_ticks`/`qty_scaled` are int64 (ticks and QTY_SCALE-scaled
    quantity are both already int64 throughout the kernel, D-06-06);
    `side`/`position_after` are int8 (both live in {-1, 0, +1}).
    """
    return {
        "etime": np.empty(n, dtype=np.int64),
        "side": np.empty(n, dtype=np.int8),
        "price_ticks": np.empty(n, dtype=np.int64),
        "qty_scaled": np.empty(n, dtype=np.int64),
        "position_after": np.empty(n, dtype=np.int8),
    }


class SimResult(NamedTuple):
    """A run's whole output (D-06-13): a trade log, an equity curve, and
    counters -- exactly what EVAL-05 (Phase 9) will need and nothing more.

    `trade_log`'s columns are `new_trade_log(n)`-shaped, length
    `equity_scaled.shape[0]` (== the run's decision-row count `n`), and
    must be sliced to `[:fill_count]` before use (see the module
    docstring). `counters` holds exactly the three D-06-13 names: `trades`
    (== `fill_count`), `flips`, `rows_in_market`.
    """

    trade_log: dict[str, np.ndarray]
    fill_count: int
    equity_scaled: np.ndarray
    counters: dict[str, int]
