"""Canonical capture row schemas for Binance USD-M futures bookTicker and trade rows.

Locked contract for Phases 1, 3, and 4 — changing dtypes here is a data migration,
not a loader rewrite (see `schema_version`).

Sequence assignment rule (verbatim from SKELETON.md — do not violate elsewhere in
the codebase): seq is assigned exactly once, by the single writer, after redundant
connections are merged/deduped — never inside a per-connection socket callback.

`SCHEMA_VERSION` is ONE constant shared by both `BOOKTICKER_SCHEMA` and
`TRADE_SCHEMA` (03-06-PLAN.md) — bumping it to 2 for `TRADE_SCHEMA`'s new
`exec_type` column means `BOOKTICKER_SCHEMA` rows ALSO report
`schema_version=2` after the capture-daemon restart that ships this change,
even though bookTicker's own column set is unchanged. The alternative
(per-schema version constants) is a larger, out-of-scope refactor; a shared
version number that bumps for either schema changing is the existing, locked
contract this docstring itself describes.
"""

import polars as pl

SCHEMA_VERSION: int = 2

BOOKTICKER_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "stream": pl.Utf8,
    "update_id": pl.Int64,
    "etime": pl.Int64,
    "event_time": pl.Int64,
    "bid_price": pl.Float64,
    "bid_qty": pl.Float64,
    "ask_price": pl.Float64,
    "ask_qty": pl.Float64,
    "seq": pl.Int64,
    "rtime": pl.Int64,
    "source": pl.Utf8,
    "schema_version": pl.Int32,
}

TRADE_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "stream": pl.Utf8,
    "trade_id": pl.Int64,
    "etime": pl.Int64,
    "event_time": pl.Int64,
    "price": pl.Float64,
    "qty": pl.Float64,
    "is_buyer_maker": pl.Boolean,
    "seq": pl.Int64,
    "rtime": pl.Int64,
    "source": pl.Utf8,
    "schema_version": pl.Int32,
    # schema_version=2 (03-06-PLAN.md): Binance's live trade frame's `X`
    # field, verbatim. Replaces the ambiguous `price==0 AND qty==0`
    # NA-placeholder heuristic with an explicit signal -- X="NA" for the
    # ~0.5% of live trade rows the archive omits entirely (p="0", q="0").
    # Rows written under schema_version=1 have no exec_type column; readers
    # spanning both versions (curated_build.py, resume_seq_assigner) must
    # tolerate its absence rather than assuming it is always present.
    "exec_type": pl.Utf8,
}


def assert_non_null_etime(df: pl.DataFrame) -> None:
    """Raise ValueError unless `df` has rows, a non-null Int64 `etime` column.

    Conditions checked, in order:
    - df.height == 0
    - df["etime"].null_count() != 0
    - df.schema["etime"] != pl.Int64
    """
    if df.height == 0:
        raise ValueError("assert_non_null_etime: DataFrame has zero rows")
    if df["etime"].null_count() != 0:
        raise ValueError(
            f"assert_non_null_etime: etime has {df['etime'].null_count()} null value(s)"
        )
    if df.schema["etime"] != pl.Int64:
        raise ValueError(
            f"assert_non_null_etime: etime dtype is {df.schema['etime']}, expected Int64"
        )
