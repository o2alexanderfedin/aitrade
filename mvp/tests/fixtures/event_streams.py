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


def canonical_streams():
    """Every stream SHAPE Phase 4 Plan 03 pins, by name.

    `tests/features/test_reference.py` asserts what each of these shapes
    must produce; `tests/features/test_kernel.py` runs the same shapes
    through both implementations and compares them bitwise. The
    expectations live with the reference and the shapes live here, so the
    equivalence test cannot quietly cover fewer cases than the arithmetic
    tests do.
    """
    second = 1_000_000_000
    prev = (100.0, 5.0, 101.0, 7.0)
    streams = {
        "ofi_repeated_identical_quote": [quote(0, *prev), quote(second, *prev)],
        "ofi_bid_price_improving": [
            quote(0, *prev),
            quote(second, 100.1, 3.0, 101.0, 7.0),
        ],
        "ofi_bid_resized": [quote(0, *prev), quote(second, 100.0, 9.0, 101.0, 7.0)],
        "ofi_bid_swept": [quote(0, *prev), quote(second, 99.9, 2.0, 101.0, 7.0)],
        "ofi_ask_price_improving": [
            quote(0, *prev),
            quote(second, 100.0, 5.0, 100.9, 4.0),
        ],
        "ofi_ask_resized": [quote(0, *prev), quote(second, 100.0, 5.0, 101.0, 10.0)],
        "ofi_ask_swept": [quote(0, *prev), quote(second, 100.0, 5.0, 101.1, 6.0)],
        "ofi_first_quote_is_nan": [
            quote(0, *prev),
            trade(second // 2, 100.5, 1.0, 1),
            quote(second, 100.1, 5.0, 101.0, 7.0),
        ],
        "window_open_end_excluded": [
            quote(0, *prev),
            trade(0, 100.5, 0.5, 1),
            trade(second, 100.5, 0.25, 1),
        ],
        "window_one_ns_inside": [
            quote(0, *prev),
            trade(1, 100.5, 0.5, 1),
            trade(second, 100.5, 0.25, 1),
        ],
        "unknown_side_contributes_zero": [quote(0, *prev), trade(1, 100.5, 3.0, 0)],
        "empty_window_after_a_gap": [
            quote(0, *prev),
            trade(1, 100.5, 2.0, 1),
            quote(5 * second, 100.1, 5.0, 101.0, 7.0),
        ],
        "flow_exactly_cancelled": [
            quote(0, *prev),
            trade(1, 100.5, 2.0, 1),
            trade(2, 100.5, 2.0, -1),
        ],
        "trade_only_before_the_first_quote": [
            trade(0, 100.5, 1.0, 1),
            trade(second // 2, 100.5, 1.0, -1),
            quote(second, *prev),
            trade(second + 1, 100.5, 1.0, 1),
        ],
        "locked_then_crossed_then_normal": [
            quote(0, 101.0, 5.0, 101.0, 7.0),
            quote(second, 101.5, 5.0, 101.0, 7.0),
            quote(2 * second, 100.0, 5.0, 101.0, 7.0),
        ],
        "warmup_boundary": [
            quote(0, *prev),
            quote(second // 2, 100.1, 5.0, 101.0, 7.0),
            trade(second - 1, 100.5, 1.0, 1),
            trade(second, 100.5, 1.0, 1),
            quote(2 * second, 100.2, 5.0, 101.0, 7.0),
        ],
        "etime_ties_in_both_streams": [
            quote(0, *prev),
            quote(second, 100.1, 5.0, 101.0, 7.0),
            trade(second, 100.5, 0.5, 1),
            trade(second, 100.5, 0.25, -1),
            quote(second, 100.2, 6.0, 101.2, 8.0),
        ],
        "single_row": [quote(0, *prev)],
    }
    return {name: build_events(rows) for name, rows in streams.items()}
