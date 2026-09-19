"""Hermetic merged-event-array builders shared by the feature tests.

`features.event_stream` builds the real thing from two curated polars
frames; these helpers build the SAME arrays (`EVENT_SCHEMA` keys, `int64`
ns, NaN cross-stream filler, `(etime, source_rank, seq)` total order) from
a hand-written list of rows, so a kernel test can pin an exact arithmetic
case without a lake on disk.

Lives in `tests/fixtures/` rather than `tests/features/` for the reason
recorded three times already in this phase: `tests/features/` must not be a
package, because `mvp/features/` is a real one and a same-named test
package shadows it on `sys.path`.

The arrays handed back are WRITABLE and owned by the caller --
`features.event_stream.event_arrays` returns read-only zero-copy views of
polars memory, so a test that perturbs a row has to build its own.
"""

from __future__ import annotations

import math

import numpy as np

from features.event_stream import (
    EVENT_SCHEMA,
    SOURCE_RANK_BOOKTICKER,
    SOURCE_RANK_TRADE,
)

NAN = math.nan

#: Quantities are integral at 1e-8 on this venue (measured on 17.2M real
#: rows), which is what makes the int64 accumulator exact. Every generated
#: qty here is built as `n / QTY_SCALE_F` from an integer `n` so the
#: fixtures share that property rather than accidentally violating it.
QTY_SCALE_F = 1e8


def quote(etime, bid_price, bid_qty, ask_price, ask_qty):
    """One bookTicker event row (source_rank 0); trade payload is NaN."""
    return {
        "etime": int(etime),
        "source_rank": SOURCE_RANK_BOOKTICKER,
        "bid_price": float(bid_price),
        "bid_qty": float(bid_qty),
        "ask_price": float(ask_price),
        "ask_qty": float(ask_qty),
        "trade_price": NAN,
        "trade_qty": NAN,
        "trade_side": 0,
    }


def trade(etime, price, qty, side):
    """One trade event row (source_rank 1); quote payload is NaN.

    `side` is `data.ingest.trade_side`'s convention: +1 aggressor bought,
    -1 aggressor sold, 0 unknown (contributes 0 to signed flow, D-04-12).
    """
    return {
        "etime": int(etime),
        "source_rank": SOURCE_RANK_TRADE,
        "bid_price": NAN,
        "bid_qty": NAN,
        "ask_price": NAN,
        "ask_qty": NAN,
        "trade_price": float(price),
        "trade_qty": float(qty),
        "trade_side": int(side),
    }


def build_events(rows, *, sort=True):
    """Turn a list of `quote(...)`/`trade(...)` dicts into the array dict
    the kernel takes.

    `seq` is assigned per stream as a fresh row index, exactly as Phase 3's
    `materialize_seq` does, and the rows are stably sorted by
    `(etime, source_rank)` so the result carries the same total order
    `merge_curated_streams` produces. `sort=False` leaves the caller's
    order alone -- used by the tie-order tests, which deliberately build
    the trades-first arrangement `assert_strict_total_order` refuses.
    """
    items = list(rows)
    if sort:
        items.sort(key=lambda r: (r["etime"], r["source_rank"]))

    seq_counter = {SOURCE_RANK_BOOKTICKER: 0, SOURCE_RANK_TRADE: 0}
    for row in items:
        rank = row["source_rank"]
        row["seq"] = seq_counter[rank]
        seq_counter[rank] += 1

    dtypes = {
        "etime": np.int64,
        "source_rank": np.int8,
        "seq": np.int64,
        "trade_side": np.int8,
    }
    return {
        name: np.array([row[name] for row in items], dtype=dtypes.get(name, np.float64))
        for name in EVENT_SCHEMA
    }


def random_events(rng, *, n=40, span_ns=3_000_000_000, trade_fraction=0.4):
    """A random but VALID merged stream: ascending etimes over `span_ns`
    (wide enough that the 1 s trade_flow window fills and empties several
    times), integral-at-1e-8 quantities, a positive size on both sides of
    every quote, and a non-crossed book.
    """
    etimes = np.sort(rng.integers(0, span_ns, size=n))
    rows = []
    for etime in etimes:
        if rng.random() < trade_fraction:
            qty = int(rng.integers(1, 500_000_000)) / QTY_SCALE_F
            side = int(rng.choice([-1, 0, 1]))
            rows.append(trade(etime, 100.0 + rng.integers(-50, 50) * 0.1, qty, side))
        else:
            bid = 100.0 + int(rng.integers(-50, 50)) * 0.1
            rows.append(
                quote(
                    etime,
                    bid,
                    int(rng.integers(1, 1_000_000)) / QTY_SCALE_F,
                    bid + 0.1 * int(rng.integers(1, 4)),
                    int(rng.integers(1, 1_000_000)) / QTY_SCALE_F,
                )
            )
    return build_events(rows)
