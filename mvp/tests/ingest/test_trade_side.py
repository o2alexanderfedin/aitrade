"""Tests for data.ingest.trade_side -- exact-flag passthrough, the
nearest-quote classifier, and the forced cross-check."""

from __future__ import annotations

import polars as pl

from data.ingest.trade_side import (
    cross_check_agreement,
    nearest_quote_side,
    resolve_side,
)
from tests.fixtures.side_convention_rows import SIDE_CONVENTION_ROWS


def _real_rows_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "trade_id": [r.trade_id for r in SIDE_CONVENTION_ROWS],
            "etime": [r.etime_ms * 1_000_000 for r in SIDE_CONVENTION_ROWS],
            "price": [r.price for r in SIDE_CONVENTION_ROWS],
            "qty": [r.qty for r in SIDE_CONVENTION_ROWS],
            "is_buyer_maker": [r.is_buyer_maker for r in SIDE_CONVENTION_ROWS],
        }
    )


def test_sign_convention_pinned_against_real_committed_rows():
    """m=true -> tradeSide=-1, m=false -> tradeSide=+1, against real archive
    rows from BTCUSDT-trades-2026-09-12 (tests/fixtures/side_convention_rows.py)."""
    df = _real_rows_df()
    resolved = resolve_side(df)

    expected = [r.expected_trade_side for r in SIDE_CONVENTION_ROWS]
    assert resolved["tradeSide_raw"].to_list() == expected
    assert resolved["tradeSide_corrected"].to_list() == expected
    assert set(resolved["side_method"].to_list()) == {"exact_flag"}

    # Spot-check the pinned convention directly against two known rows.
    buyer_maker_true = resolved.filter(pl.col("is_buyer_maker")).head(1)
    buyer_maker_false = resolved.filter(~pl.col("is_buyer_maker")).head(1)
    assert buyer_maker_true["tradeSide_corrected"].item() == -1
    assert buyer_maker_false["tradeSide_corrected"].item() == 1


def test_exact_flag_never_reclassified_by_nearest_quote():
    """A row with an exact is_buyer_maker flag keeps its exact side even
    when nearest_quote_side, run directly on the same row, WOULD disagree."""
    df = pl.DataFrame(
        {
            "trade_id": [1],
            "etime": [1_000],
            "price": [100.0],
            "qty": [1.0],
            "is_buyer_maker": [True],  # exact -> tradeSide = -1 (sell)
        }
    )
    # Quotes engineered so nearest-quote alone would say BUY (+1): price is
    # far from bid, right at the ask.
    quotes = pl.DataFrame({"etime": [900], "bid_price": [50.0], "ask_price": [100.0]})

    # Sanity: confirm the disagreement is real before asserting it's ignored.
    direct = nearest_quote_side(df, quotes)
    assert direct["tradeSide_corrected"].to_list() == [1]

    resolved = resolve_side(df, quotes=quotes)
    assert resolved["tradeSide_corrected"].to_list() == [-1]
    assert resolved["tradeSide_raw"].to_list() == [-1]
    assert resolved["side_method"].to_list() == ["exact_flag"]


def test_legacy_row_classified_by_nearest_quote_when_present():
    """A row with is_buyer_maker=None (absent side) IS reclassified by
    nearest_quote_side, unlike an exact row."""
    df = pl.DataFrame(
        {
            "trade_id": [1],
            "etime": [1_000],
            "price": [49.0],
            "qty": [1.0],
            "is_buyer_maker": pl.Series([None], dtype=pl.Boolean),
        }
    )
    quotes = pl.DataFrame({"etime": [900], "bid_price": [50.0], "ask_price": [100.0]})
    resolved = resolve_side(df, quotes=quotes)
    assert resolved["tradeSide_raw"].to_list() == [0]
    assert resolved["tradeSide_corrected"].to_list() == [-1]  # closer to bid
    assert resolved["side_method"].to_list() == ["nearest_quote"]


def test_legacy_row_stays_unknown_without_quotes():
    df = pl.DataFrame(
        {
            "trade_id": [1],
            "etime": [1_000],
            "price": [49.0],
            "qty": [1.0],
            "is_buyer_maker": pl.Series([None], dtype=pl.Boolean),
        }
    )
    resolved = resolve_side(df, quotes=None)
    assert resolved["tradeSide_corrected"].to_list() == [0]
    assert resolved["side_method"].to_list() == ["unknown"]


def test_nearest_quote_side_tie_is_unknown():
    df = pl.DataFrame(
        {"trade_id": [1], "etime": [1_000], "price": [75.0], "qty": [1.0]}
    )
    quotes = pl.DataFrame({"etime": [900], "bid_price": [50.0], "ask_price": [100.0]})
    result = nearest_quote_side(df, quotes)
    assert result["tradeSide_corrected"].to_list() == [0]
    assert result["side_method"].to_list() == ["unknown"]


def test_cross_check_agreement_full_agreement_fixture():
    """A fixture engineered so nearest-quote is known to agree with the
    exact side on every row returns 1.0."""
    exact_trades = pl.DataFrame(
        {
            "trade_id": [1, 2],
            "etime": [1_000, 2_000],
            "price": [101.0, 49.0],
            "qty": [1.0, 1.0],
            "is_buyer_maker": [False, True],  # exact raw: +1, -1
        }
    )
    resolved = resolve_side(exact_trades)
    quotes = pl.DataFrame(
        {"etime": [900, 1_900], "bid_price": [50.0, 50.0], "ask_price": [100.0, 100.0]}
    )
    # row1 price=101 -> closer to ask(100) -> nearest-quote +1, matches raw +1
    # row2 price=49  -> closer to bid(50)  -> nearest-quote -1, matches raw -1
    rate = cross_check_agreement(resolved, quotes)
    assert rate == 1.0
