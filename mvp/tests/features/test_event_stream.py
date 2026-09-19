"""The merged event stream and the decision-row rule (04-01 Task 2).

Every fixture here is hand-built and tiny: the answer is known by
construction, so a failure names a broken rule rather than a surprising
dataset. The real 18.6M-row day is exercised ONCE by a script, never by a
collected test -- `.pre-commit-config.yaml` runs `pytest tests -x -q` on
every commit and the lake is not present on every machine.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from features.event_stream import (
    EVENT_SCHEMA,
    SOURCE_RANK_BOOKTICKER,
    SOURCE_RANK_TRADE,
    assert_strict_total_order,
    decision_row_index,
    event_arrays,
    merge_curated_streams,
    project_bookticker,
    project_trade,
)

# --------------------------------------------------------------------------
# Curated-shaped fixture factories.
#
# The full curated column set, not a convenient subset: `project_trade`
# reuses `data.ingest.curated_build.filter_na_placeholders`, which reads
# `schema_version`/`price`/`qty`/`exec_type`, so a trimmed fixture would
# test a different code path than the build runs.
# --------------------------------------------------------------------------

_BOOKTICKER_SCHEMA = {
    "seq": pl.Int64,
    "symbol": pl.Utf8,
    "stream": pl.Utf8,
    "update_id": pl.Int64,
    "etime": pl.Int64,
    "event_time": pl.Int64,
    "bid_price": pl.Float64,
    "bid_qty": pl.Float64,
    "ask_price": pl.Float64,
    "ask_qty": pl.Float64,
    "capture_seq": pl.Int64,
    "rtime": pl.Int64,
    "source": pl.Utf8,
    "schema_version": pl.Int32,
}

_TRADE_SCHEMA = {
    "seq": pl.Int64,
    "symbol": pl.Utf8,
    "stream": pl.Utf8,
    "trade_id": pl.Int64,
    "etime": pl.Int64,
    "event_time": pl.Int64,
    "price": pl.Float64,
    "qty": pl.Float64,
    "is_buyer_maker": pl.Boolean,
    "rtime": pl.Int64,
    "source": pl.Utf8,
    "schema_version": pl.Int32,
    "exec_type": pl.Utf8,
    "tradeSide_raw": pl.Int8,
    "tradeSide_corrected": pl.Int8,
    "side_method": pl.Utf8,
}


def curated_bookticker(
    etimes: list[int],
    *,
    bid_price: list[float] | None = None,
    bid_qty: list[float] | None = None,
    ask_price: list[float] | None = None,
    ask_qty: list[float] | None = None,
) -> pl.DataFrame:
    """A curated bookTicker partition: `seq` is a FRESH row index from 0."""
    n = len(etimes)
    return pl.DataFrame(
        {
            "seq": list(range(n)),
            "symbol": ["BTCUSDT"] * n,
            "stream": ["bookTicker"] * n,
            "update_id": [1000 + i for i in range(n)],
            "etime": etimes,
            "event_time": etimes,
            "bid_price": bid_price
            if bid_price is not None
            else [100.0 + i for i in range(n)],
            "bid_qty": bid_qty if bid_qty is not None else [1.0 + i for i in range(n)],
            "ask_price": ask_price
            if ask_price is not None
            else [100.1 + i for i in range(n)],
            "ask_qty": ask_qty if ask_qty is not None else [2.0 + i for i in range(n)],
            "capture_seq": list(range(n)),
            "rtime": [e + 1 for e in etimes],
            "source": ["capture"] * n,
            "schema_version": [2] * n,
        },
        schema=_BOOKTICKER_SCHEMA,
    )


def curated_trade(
    etimes: list[int],
    *,
    price: list[float] | None = None,
    qty: list[float] | None = None,
    side: list[int] | None = None,
    side_method: list[str] | None = None,
    exec_type: list[str] | None = None,
    schema_version: list[int] | None = None,
) -> pl.DataFrame:
    """A curated trade partition: `seq` is a FRESH row index from 0 too --
    which is exactly why `(etime, seq)` across the two streams is NOT a
    total order and `source_rank` has to exist."""
    n = len(etimes)
    sides = side if side is not None else [1] * n
    methods = side_method if side_method is not None else ["exact_flag"] * n
    return pl.DataFrame(
        {
            "seq": list(range(n)),
            "symbol": ["BTCUSDT"] * n,
            "stream": ["trade"] * n,
            "trade_id": [5000 + i for i in range(n)],
            "etime": etimes,
            "event_time": etimes,
            "price": price if price is not None else [100.05 + i for i in range(n)],
            "qty": qty if qty is not None else [0.5 + i for i in range(n)],
            "is_buyer_maker": [s < 0 for s in sides],
            "rtime": [e + 1 for e in etimes],
            "source": ["capture"] * n,
            "schema_version": schema_version if schema_version is not None else [2] * n,
            "exec_type": exec_type if exec_type is not None else ["MARKET"] * n,
            "tradeSide_raw": sides,
            "tradeSide_corrected": sides,
            "side_method": methods,
        },
        schema=_TRADE_SCHEMA,
    )


def merged_shape_frame(rows: list[dict]) -> pl.DataFrame:
    """A frame already in `EVENT_SCHEMA` shape, built directly so a test can
    construct states `merge_curated_streams` refuses to produce."""
    return pl.DataFrame(rows, schema=dict(EVENT_SCHEMA))


# --------------------------------------------------------------------------
# The six behaviours
# --------------------------------------------------------------------------


def test_bookticker_wins_an_etime_tie():
    """A quote and a trade at the same `etime` merge quote-first, and the
    decision row for that `etime` is therefore the TRADE row (D-04-01)."""
    bt = curated_bookticker([10])
    tr = curated_trade([10])

    merged, stats = merge_curated_streams(bt, tr)

    assert merged["source_rank"].to_list() == [
        SOURCE_RANK_BOOKTICKER,
        SOURCE_RANK_TRADE,
    ]
    assert merged["etime"].to_list() == [10, 10]

    idx = decision_row_index(merged["etime"].to_numpy())
    assert idx.tolist() == [1]
    assert merged["source_rank"].to_numpy()[idx].tolist() == [SOURCE_RANK_TRADE]
    assert stats["n_decision_rows"] == 1
    assert stats["n_quote_rows"] == 1
    assert stats["n_trade_rows"] == 1
    assert stats["n_events"] == 2


def test_merged_order_is_a_strict_total_order():
    """Every adjacent pair advances in `(etime, source_rank, seq)`; a frame
    with two identical key triples is refused, naming the first offender."""
    bt = curated_bookticker([1, 2, 2, 5])
    tr = curated_trade([2, 3, 5])

    merged, _ = merge_curated_streams(bt, tr)

    etime = merged["etime"].to_numpy().astype(np.int64)
    rank = merged["source_rank"].to_numpy().astype(np.int64)
    seq = merged["seq"].to_numpy().astype(np.int64)
    d_etime = np.diff(etime)
    d_rank = np.diff(rank)
    d_seq = np.diff(seq)
    advances = (
        (d_etime > 0)
        | ((d_etime == 0) & (d_rank > 0))
        | ((d_etime == 0) & (d_rank == 0) & (d_seq > 0))
    )
    assert advances.all()
    assert_strict_total_order(merged)  # the runtime control itself

    duplicated = pl.concat([merged.slice(2, 1), merged.slice(2, 1)])
    with pytest.raises(ValueError) as excinfo:
        assert_strict_total_order(duplicated)
    message = str(excinfo.value)
    assert "row 1" in message
    assert "(etime=2" in message


def test_one_decision_row_per_distinct_etime():
    """Exactly `n_distinct(etime)` decision rows, each the LAST position of
    its etime run, strictly increasing -- including a trade-only etime."""
    bt = curated_bookticker([1, 2, 2, 5])
    tr = curated_trade([2, 3, 5])
    merged, stats = merge_curated_streams(bt, tr)

    etime = merged["etime"].to_numpy()
    idx = decision_row_index(etime)

    assert idx.dtype == np.int64
    assert len(idx) == merged["etime"].n_unique()
    assert stats["n_decision_rows"] == len(idx)
    assert np.all(np.diff(idx) > 0)
    # merged etimes are [1, 2, 2, 2, 3, 5, 5]; last position of each run:
    assert idx.tolist() == [0, 3, 4, 6]
    assert etime[idx].tolist() == [1, 2, 3, 5]
    # the trade-only etime 3 still produces exactly one decision row
    assert merged["source_rank"].to_numpy()[4] == SOURCE_RANK_TRADE


def test_event_arrays_refuse_a_nullable_hot_path_column():
    """A null in a hot-path column raises at the boundary. Without the
    check polars silently returns a NaN-filled COPY (measured), so numba
    would receive a degraded array and never know."""
    rows = [
        {
            "etime": 1,
            "source_rank": 0,
            "seq": 0,
            "bid_price": 100.0,
            "bid_qty": 1.0,
            "ask_price": 100.1,
            "ask_qty": 2.0,
            "trade_price": math.nan,
            "trade_qty": math.nan,
            "trade_side": 0,
        },
        {
            "etime": 2,
            "source_rank": 0,
            "seq": 1,
            "bid_price": None,
            "bid_qty": 1.0,
            "ask_price": 100.1,
            "ask_qty": 2.0,
            "trade_price": math.nan,
            "trade_qty": math.nan,
            "trade_side": 0,
        },
    ]
    nullable = merged_shape_frame(rows)
    assert nullable["bid_price"].null_count() == 1

    with pytest.raises(ValueError, match="bid_price"):
        event_arrays(nullable)

    clean = nullable.with_columns(pl.col("bid_price").fill_null(99.0))
    arrays = event_arrays(clean)
    assert set(arrays) == set(EVENT_SCHEMA)
    assert arrays["etime"].dtype == np.int64
    assert arrays["bid_price"].dtype == np.float64
    assert arrays["source_rank"].dtype == np.int8

    wrong_dtype = clean.with_columns(pl.col("etime").cast(pl.Int32))
    with pytest.raises(ValueError, match="etime"):
        event_arrays(wrong_dtype)


def test_quote_payload_filler_on_trade_rows_is_nan_not_zero():
    """Cross-stream filler is NaN, never 0.0: a kernel that wrongly reads a
    trade row's `bid_price` produces a visible NaN, not a plausible size."""
    bt = curated_bookticker([10])
    tr = curated_trade([11], price=[100.05], qty=[0.25], side=[-1])
    merged, _ = merge_curated_streams(bt, tr)

    quote_row = merged.row(0, named=True)
    trade_row = merged.row(1, named=True)

    for col in ("bid_price", "bid_qty", "ask_price", "ask_qty"):
        assert math.isnan(trade_row[col]), f"{col} filler on a trade row must be NaN"
    for col in ("trade_price", "trade_qty"):
        assert math.isnan(quote_row[col]), f"{col} filler on a quote row must be NaN"

    assert quote_row["trade_side"] == 0  # the project's existing "unknown" sign
    assert trade_row["trade_side"] == -1
    assert trade_row["trade_price"] == 100.05
    assert trade_row["trade_qty"] == 0.25
    assert merged.schema == dict(EVENT_SCHEMA)
    for col in EVENT_SCHEMA:
        assert merged[col].null_count() == 0


@st.composite
def _stream_etimes(draw, *, max_rows: int = 12, max_etime: int = 6):
    """A sorted etime list with duplicates allowed -- `merge_sorted`'s
    precondition, and what `materialize_seq` guarantees per partition."""
    values = draw(
        st.lists(st.integers(min_value=0, max_value=max_etime), max_size=max_rows)
    )
    return sorted(values)


@settings(deadline=None, max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(bt_etimes=_stream_etimes(), tr_etimes=_stream_etimes())
def test_hypothesis_merge_is_order_of_construction_independent(bt_etimes, tr_etimes):
    """The merge is a pure function of the two column-wise inputs, and the
    decision-row positions are a pure function of the merged etime column."""
    bt_cols = curated_bookticker(bt_etimes)
    tr_cols = curated_trade(tr_etimes)
    # the same data rebuilt row-by-row (a different construction path)
    bt_rows = pl.DataFrame(bt_cols.to_dicts(), schema=_BOOKTICKER_SCHEMA)
    tr_rows = pl.DataFrame(tr_cols.to_dicts(), schema=_TRADE_SCHEMA)

    merged_a, stats_a = merge_curated_streams(bt_cols, tr_cols)
    merged_b, stats_b = merge_curated_streams(bt_rows, tr_rows)

    assert merged_a.equals(merged_b)
    assert stats_a == stats_b
    assert merged_a.height == len(bt_etimes) + len(tr_etimes)

    etime = merged_a["etime"].to_numpy()
    idx = decision_row_index(etime)
    # reference: the last position of each distinct-value run, in python
    expected = [
        i for i in range(len(etime)) if i == len(etime) - 1 or etime[i] != etime[i + 1]
    ]
    assert idx.tolist() == expected
    assert len(idx) == merged_a["etime"].n_unique()
    assert stats_a["n_decision_rows"] == len(idx)


# --------------------------------------------------------------------------
# Counted row filters (D-04-12): never silent
# --------------------------------------------------------------------------


def test_na_placeholder_trades_are_excluded_and_counted():
    """`exec_type == "NA"` placeholders leave the event stream, and the
    count comes back rather than vanishing."""
    tr = curated_trade(
        [1, 2, 3],
        price=[100.0, 0.0, 100.2],
        qty=[0.5, 0.0, 0.25],
        exec_type=["MARKET", "NA", "MARKET"],
    )
    projected, stats = project_trade(tr)

    assert projected.height == 2
    assert projected["etime"].to_list() == [1, 3]
    assert stats["na_placeholder_excluded"] == 1
    assert stats["unknown_side_rows"] == 0

    merged, merged_stats = merge_curated_streams(curated_bookticker([1]), tr)
    assert merged_stats["na_placeholder_excluded"] == 1
    assert merged_stats["n_trade_rows"] == 2
    assert merged.height == 3


def test_unknown_side_trades_are_counted_and_contribute_zero_sign():
    """`side_method == "unknown"` rows stay in the stream with sign 0 --
    they contribute 0 to signed flow either way (D-04-12)."""
    tr = curated_trade(
        [1, 2],
        side=[1, 0],
        side_method=["exact_flag", "unknown"],
    )
    projected, stats = project_trade(tr)

    assert projected["trade_side"].to_list() == [1, 0]
    assert stats["unknown_side_rows"] == 1


def test_projections_land_exactly_on_the_event_schema():
    """Both projections produce EVENT_SCHEMA in order, with zero nulls --
    `merge_sorted` raises SchemaError on any column-order or dtype drift,
    so this is the contract that makes the merge possible at all."""
    bt = project_bookticker(curated_bookticker([1, 2]))
    tr, _ = project_trade(curated_trade([1, 2]))

    assert bt.schema == dict(EVENT_SCHEMA)
    assert tr.schema == dict(EVENT_SCHEMA)
    assert list(bt.columns) == list(EVENT_SCHEMA)
    assert list(tr.columns) == list(EVENT_SCHEMA)
    assert bt["source_rank"].to_list() == [SOURCE_RANK_BOOKTICKER] * 2
    assert tr["source_rank"].to_list() == [SOURCE_RANK_TRADE] * 2
