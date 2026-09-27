"""One chronological event stream from the two curated partitions, and the
rule that says which of its rows is a decision row (D-04-01, FEAT-01).

`mvp.md`'s decision-point rule -- "state accumulates on every row, decisions
emit on the last row of each `etime`" -- and `spec.md:68`'s "the latest in
arrival order within the file" were both written when there was ONE file.
With bookTicker and trade merged there is no "within the file", so the order
has to be pinned explicitly. This module is where it is pinned:

    (etime, source_rank, seq),  source_rank: bookTicker = 0, trade = 1

`seq` cannot carry the stream identity on its own. `data/ingest/
curated_build.py:materialize_seq` assigns `seq` as a FRESH per-partition row
index, so both streams start at 0 and `(etime, seq)` across the merged frame
is not a total order at all (measured, 04-RESEARCH-NOTES.md Q2).

WHY THE RANK'S VALUE IS PINNED EVEN THOUGH IT IS OUTPUT-NEUTRAL. Measured
both ways over the whole real 2026-09-13 day, `mid`, `imb_top` and `ofi` at
the decision rows are bit-identical, and `trade_flow` is bit-identical too
once its accumulator is int64 (D-04-14; in float64 it moved on 95 % of rows,
by at most 1.17e-13 BTC -- pure non-associativity of `+`). What the rank DOES
change is which row is the decision row: 329,580 of 6,864,853 decision rows
are trade rows under quotes-first, versus 1,267 under trades-first. That
matters for any future feature that reads the decision row's OWN payload
rather than carried state. So the rank is a reproducibility pin, not a
modelling choice -- and it is materialized as a column rather than left
implied by argument position, so the order is inspectable in the artefact.

HOW THE ORDER IS PRODUCED. `bookticker.merge_sorted(trade, key="etime")`:
`merge_sorted` is a stable merge that takes from the LEFT frame on a tie, so
the left frame IS `source_rank` 0 (pinned on a 5-row toy, re-verified in
this venv). Measured on the real day: 0.610 s / 2.74 GB peak, against
1.009 s / 3.05 GB for concat+sort, for byte-identical output. No sort runs
afterwards -- a sort would be 1.65x slower and could not be checked against
anything, whereas `assert_strict_total_order` checks the merge's claim
directly.

THE ORDER CLAIM IS CHECKED AT RUNTIME, NOT ONLY IN TESTS (STATE.md's
"guardrails runtime-first" lesson): `merge_curated_streams` runs
`assert_strict_total_order` on every merge it returns. Measured cost is
~0.05 s on 18.6M rows -- 4 % of the merge itself. The test is the tripwire
that proves the assertion works; the assertion is the control.

NO CACHED MERGE TIER. End-to-end `read_parquet -> merge_sorted -> to_numpy`
is ~1.15 s for a real day, so caching the merged frame as a second lake tier
would buy ~1 s and cost a second write-once tier to keep immutable forever.
The merge is recomputed from the curated manifests every time.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl

from data.ingest.curated_build import filter_na_placeholders

__all__ = [
    "SOURCE_RANK_BOOKTICKER",
    "SOURCE_RANK_TRADE",
    "EVENT_SCHEMA",
    "project_bookticker",
    "project_trade",
    "merge_curated_streams",
    "assert_strict_total_order",
    "event_arrays",
    "decision_row_index",
]

#: bookTicker wins an `etime` tie. It is the LEFT frame of `merge_sorted`,
#: and this value is also materialized as a column so the total order is
#: inspectable rather than implied by argument position.
SOURCE_RANK_BOOKTICKER: int = 0
SOURCE_RANK_TRADE: int = 1

#: The merged event row. ORDERED and exact: `merge_sorted` raises
#: `SchemaError` on any column-order or dtype difference between the two
#: frames, so this dict is the contract that makes the merge possible, and
#: `event_arrays` refuses anything that has drifted from it.
#:
#: Every column is NON-NULLABLE by construction -- a null Float64 column
#: hands `.to_numpy()` a NaN-filled COPY instead of a zero-copy view, and
#: does it silently (measured). The cross-stream payload filler is therefore
#: NaN (a value), never null and never 0.0: a kernel that wrongly reads a
#: trade row's `bid_price` produces a visible NaN downstream, whereas 0.0 is
#: a plausible size that would propagate unnoticed.
EVENT_SCHEMA: dict[str, pl.DataType] = {
    "etime": pl.Int64,
    "source_rank": pl.Int8,
    "seq": pl.Int64,
    "bid_price": pl.Float64,
    "bid_qty": pl.Float64,
    "ask_price": pl.Float64,
    "ask_qty": pl.Float64,
    "trade_price": pl.Float64,
    "trade_qty": pl.Float64,
    "trade_side": pl.Int8,
}

#: The sign a trade contributes to signed flow when its side is unknown --
#: `data/ingest/trade_side.py`'s existing convention, reused rather than
#: re-invented. Quote rows carry it too: they are not trades, and 0
#: contributes 0 to signed flow either way (D-04-12).
_SIDE_UNKNOWN: int = 0

_NAN: float = math.nan

_HOT_PATH_FLOAT_COLUMNS = (
    "bid_price",
    "bid_qty",
    "ask_price",
    "ask_qty",
    "trade_price",
    "trade_qty",
)


def _assert_projection_is_clean(df: pl.DataFrame, stream: str) -> None:
    """Raise unless `df` is exactly `EVENT_SCHEMA` with zero nulls anywhere.

    Checked on the projection rather than only on the merge, because a null
    that reaches `merge_sorted` is no longer attributable to a stream.
    """
    if df.schema != dict(EVENT_SCHEMA):
        raise ValueError(
            f"project_{stream}: projected schema {df.schema} does not match "
            f"EVENT_SCHEMA {dict(EVENT_SCHEMA)}"
        )
    for column in EVENT_SCHEMA:
        nulls = df[column].null_count()
        if nulls:
            raise ValueError(
                f"project_{stream}: column {column!r} has {nulls} null value(s); "
                "every EVENT_SCHEMA column must be non-nullable by construction"
            )


def project_bookticker(df: pl.DataFrame) -> pl.DataFrame:
    """Project a curated bookTicker partition onto `EVENT_SCHEMA`.

    The trade payload is filled with NaN and `trade_side` with 0 (the
    project's existing "unknown" sign). `select` in EVENT_SCHEMA order, not
    `with_columns`: `merge_sorted` compares schemas field by field, in
    order.
    """
    projected = df.select(
        pl.col("etime").cast(pl.Int64),
        pl.lit(SOURCE_RANK_BOOKTICKER, dtype=pl.Int8).alias("source_rank"),
        pl.col("seq").cast(pl.Int64),
        pl.col("bid_price").cast(pl.Float64),
        pl.col("bid_qty").cast(pl.Float64),
        pl.col("ask_price").cast(pl.Float64),
        pl.col("ask_qty").cast(pl.Float64),
        pl.lit(_NAN, dtype=pl.Float64).alias("trade_price"),
        pl.lit(_NAN, dtype=pl.Float64).alias("trade_qty"),
        pl.lit(_SIDE_UNKNOWN, dtype=pl.Int8).alias("trade_side"),
    )
    _assert_projection_is_clean(projected, "bookticker")
    return projected


def project_trade(df: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, int]]:
    """Project a curated trade partition onto `EVENT_SCHEMA`, returning the
    frame AND the counts of the two rows classes D-04-12 requires be
    counted rather than dropped silently.

    `exec_type == "NA"` placeholder trades (price 0, qty 0 -- the rows the
    archive omits entirely) are excluded here, through
    `data.ingest.curated_build.filter_na_placeholders` so there is ONE
    definition of the placeholder signature in the codebase, not a second
    one that could drift. Phase 3's curated build already applies that
    filter on capture-sourced days, so on archive-sourced days the count is
    legitimately 0 and the filter is a uniform no-op rather than dead code
    -- running it unconditionally is what keeps the two day kinds on one
    path.

    Trades whose `side_method` is `"unknown"` KEEP their row and get sign 0,
    contributing 0 to signed flow. Their count comes back too: Plan 05's
    build persists both numbers into the feature-tier DQ report, which is
    what DATA-07's "dropped-event counts per filter" asks for.
    """
    cleaned, na_placeholder_excluded = filter_na_placeholders(df)
    unknown_side_rows = cleaned.filter(pl.col("side_method") == "unknown").height

    projected = cleaned.select(
        pl.col("etime").cast(pl.Int64),
        pl.lit(SOURCE_RANK_TRADE, dtype=pl.Int8).alias("source_rank"),
        pl.col("seq").cast(pl.Int64),
        pl.lit(_NAN, dtype=pl.Float64).alias("bid_price"),
        pl.lit(_NAN, dtype=pl.Float64).alias("bid_qty"),
        pl.lit(_NAN, dtype=pl.Float64).alias("ask_price"),
        pl.lit(_NAN, dtype=pl.Float64).alias("ask_qty"),
        pl.col("price").cast(pl.Float64).alias("trade_price"),
        pl.col("qty").cast(pl.Float64).alias("trade_qty"),
        pl.when(pl.col("side_method") == "unknown")
        .then(pl.lit(_SIDE_UNKNOWN, dtype=pl.Int8))
        .otherwise(pl.col("tradeSide_corrected").cast(pl.Int8))
        .alias("trade_side"),
    )
    _assert_projection_is_clean(projected, "trade")
    return projected, {
        "na_placeholder_excluded": int(na_placeholder_excluded),
        "unknown_side_rows": int(unknown_side_rows),
    }


def merge_curated_streams(
    bookticker: pl.DataFrame, trade: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Merge the two curated partitions into ONE event frame in
    `(etime, source_rank, seq)` order, plus the stats a build persists.

    `bookticker` is passed as the RECEIVER deliberately: `merge_sorted`
    takes from the left frame on an `etime` tie, so argument position is
    what makes bookTicker `source_rank` 0. Swapping the two arguments
    silently reverses the tie order, which is why
    `assert_strict_total_order` (which reads the materialized rank column,
    not argument position) runs on the result before it is returned.

    Both inputs must already be sorted by `etime` -- which every curated
    partition is, by `materialize_seq`. An unsorted input produces an
    interleaving that is not an order at all, and the assertion below is
    what catches it.
    """
    quotes = project_bookticker(bookticker)
    trades, trade_stats = project_trade(trade)

    merged = quotes.merge_sorted(trades, key="etime")
    assert_strict_total_order(merged)

    decisions = decision_row_index(merged["etime"].to_numpy())
    stats = {
        "n_quote_rows": quotes.height,
        "n_trade_rows": trades.height,
        "n_events": merged.height,
        "n_decision_rows": int(decisions.size),
        **trade_stats,
    }
    return merged, stats


def assert_strict_total_order(df: pl.DataFrame) -> None:
    """Raise `ValueError` unless every adjacent pair strictly advances in
    `(etime, source_rank, seq)`.

    This is the load-bearing control on the merge, not a test helper: it
    runs inside `merge_curated_streams` on every real build. Vectorised in
    polars (three `diff()`s) -- measured at ~0.05 s over the real day's
    18,576,994 adjacent pairs, 4 % of the merge itself.

    A pair is legal when `detime > 0`, or `detime == 0 and drank > 0`, or
    `detime == 0 and drank == 0 and dseq > 0`. Anything else -- an equal
    triple, a backwards step -- is a violation, and the message names the
    first one with both key triples so the failure points at a row rather
    than at a summary count.
    """
    if df.height <= 1:
        return

    deltas = df.select(
        pl.col("etime").diff().alias("d_etime"),
        pl.col("source_rank").cast(pl.Int64).diff().alias("d_rank"),
        pl.col("seq").diff().alias("d_seq"),
    ).slice(1)

    d_etime = pl.col("d_etime")
    d_rank = pl.col("d_rank")
    d_seq = pl.col("d_seq")
    violation = (d_etime < 0) | (
        (d_etime == 0) & ((d_rank < 0) | ((d_rank == 0) & (d_seq <= 0)))
    )
    offenders = deltas.select(violation.alias("bad"))["bad"].arg_true()
    if offenders.len() == 0:
        return

    row = int(offenders[0]) + 1  # deltas was sliced past the first row
    previous = df.row(row - 1, named=True)
    current = df.row(row, named=True)
    raise ValueError(
        f"assert_strict_total_order: row {row} does not advance on "
        f"(etime, source_rank, seq): row {row - 1} is "
        f"(etime={previous['etime']}, source_rank={previous['source_rank']}, "
        f"seq={previous['seq']}) and row {row} is "
        f"(etime={current['etime']}, source_rank={current['source_rank']}, "
        f"seq={current['seq']}); {offenders.len()} violation(s) total"
    )


def event_arrays(df: pl.DataFrame) -> dict[str, np.ndarray]:
    """Return one numpy array per `EVENT_SCHEMA` column, as zero-copy views.

    The boundary where nullability and dtype go silently lossy. A Float64
    column with a null does NOT raise on `.to_numpy()` -- polars returns a
    NaN-filled copy, which numba then reads as if it were the data
    (measured in this venv). So every column is checked for exact dtype and
    zero nulls BEFORE conversion, and a failure names the column.

    On non-null numeric columns polars hands back views: measured at 0.000 s
    for six columns of 18.58M rows, which is why the merge stays in polars
    instead of being done in numba to "avoid a conversion".
    """
    if df.schema != dict(EVENT_SCHEMA):
        missing = [c for c in EVENT_SCHEMA if c not in df.columns]
        if missing:
            raise ValueError(
                f"event_arrays: missing EVENT_SCHEMA column(s) {missing}; "
                f"got {list(df.columns)}"
            )
        for column, expected in EVENT_SCHEMA.items():
            actual = df.schema[column]
            if actual != expected:
                raise ValueError(
                    f"event_arrays: column {column!r} has dtype {actual}, "
                    f"EVENT_SCHEMA requires {expected}"
                )
        raise ValueError(
            f"event_arrays: column set/order {list(df.columns)} does not match "
            f"EVENT_SCHEMA {list(EVENT_SCHEMA)}"
        )

    for column in EVENT_SCHEMA:
        nulls = df[column].null_count()
        if nulls:
            raise ValueError(
                f"event_arrays: column {column!r} has {nulls} null value(s); "
                "a nullable column becomes a NaN-filled COPY at .to_numpy() "
                "and the kernel cannot tell"
            )

    return {column: df[column].to_numpy() for column in EVENT_SCHEMA}


def decision_row_index(etime: np.ndarray) -> np.ndarray:
    """The int64 positions of the LAST row of each distinct `etime` run in
    the merged order -- `spec.md`'s decision-point rule, given a referent.

    A quote and a trade at the same `etime` collapse into ONE decision row
    that has seen both: state accumulates on every row, and the decision
    emits on the last row of the group (D-04-01). That is the whole reason
    the merged stream had to exist -- "the latest in arrival order within
    the file" has no meaning across two files.

    Implemented as a boolean over positions (`etime[:-1] != etime[1:]`, plus
    the final row), never a groupby: it must stay a PURE FUNCTION OF
    POSITION so Plan 06's leakage property can delete rows after `t` and
    re-derive the same decision rows for the prefix.
    """
    if etime.ndim != 1:
        raise ValueError(
            f"decision_row_index: expected a 1-D array, got {etime.ndim}-D"
        )
    if etime.dtype != np.int64:
        raise ValueError(
            f"decision_row_index: expected int64 etime, got {etime.dtype} "
            "(the ns clock is int64 everywhere; a float etime would compare wrong)"
        )
    n = etime.shape[0]
    if n == 0:
        return np.empty(0, dtype=np.int64)

    is_last_of_run = np.empty(n, dtype=np.bool_)
    np.not_equal(etime[:-1], etime[1:], out=is_last_of_run[:-1])
    is_last_of_run[-1] = True
    return np.flatnonzero(is_last_of_run).astype(np.int64)
