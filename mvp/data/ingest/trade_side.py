"""Trade-side resolution: exact-flag passthrough plus a nearest-quote
classifier for legacy rows whose side is absent.

Sign convention, pinned (03-CONTEXT.md, spec.md's "Trades-backfill
side-exactness" subsection -- do not re-derive this elsewhere):

    `is_buyer_maker = true`  -> the buyer was the maker -> the AGGRESSOR
                                (taker) SOLD -> `tradeSide = -1`.
    `is_buyer_maker = false` -> the buyer was the taker/aggressor -> the
                                aggressor BOUGHT -> `tradeSide = +1`.

This is named in RESEARCH.md as the most common Binance sign-convention bug
in the wild -- getting it backwards silently flips the sign of every flow
feature downstream. `tests/ingest/test_trade_side.py` pins it against real
committed rows in `tests/fixtures/side_convention_rows.py`, not a synthetic
fixture.

`resolve_side` adds three columns:
- `tradeSide_raw`      -- the exact sign from `is_buyer_maker` when the flag
                          is present (non-null); `0` for legacy rows with no
                          exact side.
- `tradeSide_corrected`-- equal to `tradeSide_raw` for exact rows; the
                          nearest-quote result for legacy rows. NEVER
                          recomputed for a row that already has an exact
                          flag (03-CONTEXT.md invariant: "an exact side is
                          never re-classified").
- `side_method`         -- `"exact_flag"` | `"nearest_quote"` | `"unknown"`.

For this phase's real BTCUSDT-perp data every row carries an exact flag
(archive `is_buyer_maker`, capture `m`), so `side_method` is `"exact_flag"`
for effectively all rows -- 0/564,047 side mismatches measured in
PROBE-RESULTS.md section 2. The nearest-quote branch exists and is unit
tested against a synthetic fixture, but is not expected to fire on real
data this phase.

Quote-join strategy: `nearest_quote_side` uses `join_asof(..., strategy=
"backward")` -- the prevailing L1 quote AT OR BEFORE the trade's `etime`,
never a quote that postdates the trade (a "nearest" strategy could pick a
post-trade quote that already reflects the trade's own price impact --
leakage into the very classification the quote is supposed to inform).
Both frames must be sorted by `etime` before the join; this function sorts
its own copies internally so callers never have to pre-sort.
"""

from __future__ import annotations

import polars as pl

# Pinned sign-convention constants -- see module docstring.
SIGN_MAKER_IS_BUYER: int = -1  # is_buyer_maker=true  -> aggressor sold
SIGN_MAKER_IS_SELLER: int = 1  # is_buyer_maker=false -> aggressor bought


def resolve_side(df: pl.DataFrame, quotes: pl.DataFrame | None = None) -> pl.DataFrame:
    """Add `tradeSide_raw`/`tradeSide_corrected`/`side_method` to `df`.

    `quotes` (an L1 bookTicker frame with `etime`/`bid_price`/`ask_price`) is
    optional -- when `None` (this plan's real 2026-09-12 build: no curated
    L1 quotes exist yet), legacy rows (none expected on real data) stay
    `side_method="unknown"`/`tradeSide_corrected=0` rather than raising.
    Rows with an exact `is_buyer_maker` flag are never affected by whether
    `quotes` is supplied.
    """
    exact_mask = pl.col("is_buyer_maker").is_not_null()
    raw_expr = (
        pl.when(exact_mask)
        .then(
            pl.when(pl.col("is_buyer_maker"))
            .then(pl.lit(SIGN_MAKER_IS_BUYER, dtype=pl.Int8))
            .otherwise(pl.lit(SIGN_MAKER_IS_SELLER, dtype=pl.Int8))
        )
        .otherwise(pl.lit(0, dtype=pl.Int8))
    )
    df = df.with_columns(raw_expr.alias("tradeSide_raw"))
    df = df.with_columns(
        pl.col("tradeSide_raw").alias("tradeSide_corrected"),
        pl.when(pl.col("tradeSide_raw") != 0)
        .then(pl.lit("exact_flag"))
        .otherwise(pl.lit("unknown"))
        .alias("side_method"),
    )

    legacy_mask = pl.col("tradeSide_raw") == 0
    n_legacy = df.filter(legacy_mask).height
    if quotes is None or n_legacy == 0:
        return df

    # Preserve original row order across the split/reclassify/concat below.
    df = df.with_row_index("_resolve_side_order")
    legacy_df = df.filter(legacy_mask)
    exact_df = df.filter(~legacy_mask)

    reclassified = nearest_quote_side(legacy_df.drop("_resolve_side_order"), quotes)
    reclassified = reclassified.with_columns(legacy_df["_resolve_side_order"]).select(
        exact_df.columns
    )

    out = pl.concat([exact_df, reclassified], how="vertical").sort(
        "_resolve_side_order"
    )
    return out.drop("_resolve_side_order")


def nearest_quote_side(trades: pl.DataFrame, quotes: pl.DataFrame) -> pl.DataFrame:
    """Classify `trades` (rows needing a side) by nearest L1 quote at/before
    each trade's `etime`.

    Returns `trades` with `tradeSide_corrected`/`side_method` overwritten:
    closer to the prevailing bid -> sell (`-1`, `"nearest_quote"`); closer to
    the prevailing ask -> buy (`+1`, `"nearest_quote"`); exact tie, or no
    prevailing quote at all (trade precedes the first quote) -> `0`,
    `"unknown"`. `tradeSide_raw` is left untouched -- it is the exact-flag
    value (`0` for these rows by construction), never overwritten by this
    classifier.
    """
    # Preserve the caller's original row order across the sort join_asof
    # requires -- both resolve_side's legacy-row path and a direct caller
    # (e.g. cross_check_agreement) depend on output row N corresponding to
    # input row N.
    trades_indexed = trades.with_row_index("_nqs_order")
    trades_sorted = trades_indexed.sort("etime")
    quotes_sorted = quotes.sort("etime").select("etime", "bid_price", "ask_price")

    joined = trades_sorted.join_asof(quotes_sorted, on="etime", strategy="backward")

    dist_bid = (pl.col("price") - pl.col("bid_price")).abs()
    dist_ask = (pl.col("ask_price") - pl.col("price")).abs()
    has_quote = pl.col("bid_price").is_not_null() & pl.col("ask_price").is_not_null()

    corrected_expr = (
        pl.when(~has_quote)
        .then(pl.lit(0, dtype=pl.Int8))
        .when(dist_bid < dist_ask)
        .then(pl.lit(SIGN_MAKER_IS_BUYER, dtype=pl.Int8))
        .when(dist_ask < dist_bid)
        .then(pl.lit(SIGN_MAKER_IS_SELLER, dtype=pl.Int8))
        .otherwise(pl.lit(0, dtype=pl.Int8))
    )
    method_expr = (
        pl.when(~has_quote)
        .then(pl.lit("unknown"))
        .when(dist_bid == dist_ask)
        .then(pl.lit("unknown"))
        .otherwise(pl.lit("nearest_quote"))
    )

    result = joined.with_columns(
        corrected_expr.alias("tradeSide_corrected"),
        method_expr.alias("side_method"),
    ).drop("bid_price", "ask_price")

    # `with_columns` overwrites `tradeSide_corrected`/`side_method` in place
    # if `trades` already carried them (resolve_side's legacy-row path), or
    # appends them as new columns otherwise (a direct caller, e.g.
    # cross_check_agreement, that hands in a frame without them) -- either
    # way the returned frame always carries both, never silently dropping
    # them via a column-set-restricted select. Restore original row order
    # (see `_nqs_order` above) before returning.
    return result.sort("_nqs_order").drop("_nqs_order")


def cross_check_agreement(
    exact_side_trades: pl.DataFrame, quotes: pl.DataFrame
) -> float:
    """Deliberately re-classify KNOWN-CORRECT rows (rows that already carry
    an exact side) with `nearest_quote_side`, and return the fraction where
    the nearest-quote result agrees with the known-exact sign.

    This is the "real deliverable" cross-check (03-CONTEXT.md): it measures
    how much the nearest-quote mechanism itself can be trusted for any
    future legacy backfill, run once over a fixed sample day
    (03-RESEARCH.md Open Question 2, resolved: one-time mechanism
    validation, not a per-day recurring DQ check). A tie (`side_method=
    "unknown"` from the classifier) counts as disagreement -- the
    classifier failing to commit to a side is not "agreement" with a known
    exact sign.

    Raises `ValueError` if `exact_side_trades` is empty (an agreement rate
    over zero rows is undefined, not `1.0` or `0.0` by convention).
    """
    if exact_side_trades.height == 0:
        raise ValueError("cross_check_agreement: exact_side_trades is empty")

    known_exact = exact_side_trades["tradeSide_raw"]
    reclassified = nearest_quote_side(exact_side_trades, quotes)
    agree = (reclassified["tradeSide_corrected"] == known_exact).sum()
    return agree / exact_side_trades.height
