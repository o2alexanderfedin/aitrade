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
"backward", allow_exact_matches=False)` -- the prevailing L1 quote
STRICTLY BEFORE the trade's `etime`, never a quote at or after it (a
"nearest" strategy could pick a post-trade quote that already reflects the
trade's own price impact -- leakage into the very classification the quote
is supposed to inform). Both frames must be sorted by `etime` before the
join; this function sorts its own copies internally so callers never have
to pre-sort. Quote updates that share an `etime` are ordered by
`update_id` (else `seq`) and only the last one per `etime` is joined
(03-REVIEW.md WR-10), so the result never depends on input row order.

`allow_exact_matches=False` (03-03-PLAN.md Task 3, fixed after the real
2026-09-13 cross-check measured 57.5% agreement pre-fix): both `etime`
columns are derived from Binance's own MILLISECOND `E`/`T` fields via the
single `ms_to_ns` site, so their nanosecond "precision" is entirely
trailing zeros -- two genuinely different events (a trade and the
bookTicker update IT ITSELF caused) routinely share an identical `etime`
in a fast market. With `allow_exact_matches=True` (the default),
`join_asof(strategy="backward")` treats `quote.etime == trade.etime` as a
valid match and often picks the POST-trade quote (the update the trade's
own execution triggered), producing the exact price-impact leakage this
docstring already warned about in principle. Measured root cause: trades
whose `etime` collided with >1 quote update in the same millisecond
(877,254 / 1,409,705 = 62%) agreed only 39.0% of the time; trades on an
unambiguous (single-update) millisecond agreed 88.1% of the time. See
03-03-SUMMARY.md for the full pre-fix/post-fix transcript -- this is
exactly the "off-by-one in 'nearest in time'" the plan's own Task 3 text
anticipated and pre-authorized fixing.
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


#: Columns that order quote updates sharing one `etime`, in preference order:
#: Binance's own book-update id, then the curated tier's `(etime, seq)` seq.
QUOTE_TIEBREAK_COLUMNS: tuple[str, ...] = ("update_id", "seq")


def _prevailing_quote_per_etime(quotes: pl.DataFrame) -> pl.DataFrame:
    """One quote per `etime`: the LAST update in `(etime, tiebreak)` order.

    03-REVIEW.md WR-10: `quotes.sort("etime")` left same-millisecond updates
    in file order, and `join_asof(strategy="backward")` took whichever came
    last -- so the classification depended on input row order, not on
    `update_id`/`seq` (62 % of real trades sit on a millisecond with more
    than one quote update). Sorting by a total order and keeping the last
    row per `etime` makes the result a pure function of the quote set.
    Refuses (`ValueError`) tied `etime`s with no tiebreak column rather than
    silently picking one."""
    tiebreak = next((c for c in QUOTE_TIEBREAK_COLUMNS if c in quotes.columns), None)
    if tiebreak is None:
        if quotes["etime"].n_unique() != quotes.height:
            raise ValueError(
                "nearest_quote_side: quotes share an etime but carry no tiebreak "
                f"column ({' or '.join(QUOTE_TIEBREAK_COLUMNS)}) -- the prevailing "
                "quote would depend on input row order"
            )
        ordered = quotes.sort("etime")
    else:
        ordered = quotes.sort(["etime", tiebreak])
    return ordered.unique(subset="etime", keep="last", maintain_order=True).select(
        "etime", "bid_price", "ask_price"
    )


def nearest_quote_side(trades: pl.DataFrame, quotes: pl.DataFrame) -> pl.DataFrame:
    """Classify `trades` (rows needing a side) by the nearest L1 quote
    STRICTLY BEFORE each trade's `etime` (never at the same `etime` -- see
    module docstring's `allow_exact_matches=False` rationale).

    Returns `trades` with `tradeSide_corrected`/`side_method` overwritten:
    closer to the prevailing bid -> sell (`-1`, `"nearest_quote"`); closer to
    the prevailing ask -> buy (`+1`, `"nearest_quote"`); exact tie, or no
    strictly-prior quote at all (trade precedes or ties the first quote) ->
    `0`, `"unknown"`. `tradeSide_raw` is left untouched -- it is the
    exact-flag value (`0` for these rows by construction), never overwritten
    by this classifier.
    """
    # Preserve the caller's original row order across the sort join_asof
    # requires -- both resolve_side's legacy-row path and a direct caller
    # (e.g. cross_check_agreement) depend on output row N corresponding to
    # input row N.
    trades_indexed = trades.with_row_index("_nqs_order")
    trades_sorted = trades_indexed.sort("etime")
    quotes_sorted = _prevailing_quote_per_etime(quotes)

    joined = trades_sorted.join_asof(
        quotes_sorted, on="etime", strategy="backward", allow_exact_matches=False
    )

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
