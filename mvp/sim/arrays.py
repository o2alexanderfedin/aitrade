"""The polars -> numpy boundary for the simulator (D-06-01), mirroring
`features/event_stream.py:event_arrays` exactly: every required column is
asserted `null_count() == 0` BEFORE `.to_numpy()`, naming the offending
column on failure. Polars' `.to_numpy()` on a Float64 column WITH nulls
returns a NaN-filled COPY and does NOT raise (re-measured Phase 4), so the
assertion is the control, not the convention.

`pred` is NOT part of this function's contract (D-06-04). Predictions are a
parameter the caller supplies separately from whatever frame it came from
-- `sim_arrays` converts only what the harness accessor's frame itself
carries (`etime`, `bid_price`, `ask_price`), never `pred`, which is
asserted non-null/finite by `sim.kernel.run_sim_checked` itself, not here.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from sim.ticks import price_to_ticks

__all__ = ["SIM_ARRAY_SCHEMA", "sim_arrays"]

#: The columns this boundary reads, and the dtype each must already be.
SIM_ARRAY_SCHEMA: dict[str, pl.DataType] = {
    "etime": pl.Int64,
    "bid_price": pl.Float64,
    "ask_price": pl.Float64,
}


def sim_arrays(df: pl.DataFrame) -> dict[str, np.ndarray]:
    """`{"etime": int64, "bid_ticks": int64, "ask_ticks": int64}` from a
    frame carrying `SIM_ARRAY_SCHEMA`'s columns.

    Raises `ValueError` naming the column when a required column is
    missing, has the wrong dtype, or holds a null -- never silently hands
    numba a NaN-filled copy. `bid_ticks`/`ask_ticks` go through
    `sim.ticks.price_to_ticks`, which is bid/ask's own round-trip-proved
    conversion (never used on `pred` -- see `sim.kernel`'s module
    docstring for why a model prediction needs a different rule).
    """
    for column, expected in SIM_ARRAY_SCHEMA.items():
        if column not in df.columns:
            raise ValueError(f"sim_arrays: missing required column {column!r}")
        actual = df.schema[column]
        if actual != expected:
            raise ValueError(
                f"sim_arrays: column {column!r} has dtype {actual}, "
                f"SIM_ARRAY_SCHEMA requires {expected}"
            )
        nulls = df[column].null_count()
        if nulls:
            raise ValueError(
                f"sim_arrays: column {column!r} has {nulls} null value(s); "
                "a nullable column becomes a NaN-filled COPY at .to_numpy() "
                "and the kernel cannot tell"
            )

    return {
        "etime": df["etime"].to_numpy(),
        "bid_ticks": price_to_ticks(df["bid_price"].to_numpy()),
        "ask_ticks": price_to_ticks(df["ask_price"].to_numpy()),
    }
