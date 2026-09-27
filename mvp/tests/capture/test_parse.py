"""Tests for data.capture.parse — single ms-to-ns conversion site."""

import copy

import polars as pl
import pytest

from data.capture.parse import (
    FrameParseError,
    decode_frame,
    parse_combined_frame,
    parse_trade,
)
from data.schema import SCHEMA_VERSION, TRADE_SCHEMA
from tests.fixtures.payloads import SAMPLE_BOOKTICKER_FRAME, SAMPLE_TRADE_FRAME


def test_parse_bookticker_frame():
    data = SAMPLE_BOOKTICKER_FRAME["data"]
    row = parse_combined_frame(SAMPLE_BOOKTICKER_FRAME, seq=0, rtime_ns=1)
    assert row["etime"] == data["T"] * 1_000_000
    assert row["event_time"] == data["E"] * 1_000_000
    assert row["bid_price"] == float(data["b"])
    assert row["schema_version"] == SCHEMA_VERSION
    assert row["stream"] == "bookTicker"


def test_parse_trade_frame():
    data = SAMPLE_TRADE_FRAME["data"]
    row = parse_combined_frame(SAMPLE_TRADE_FRAME, seq=0, rtime_ns=1)
    assert row["trade_id"] == data["t"]
    assert row["is_buyer_maker"] == bool(data["m"])
    assert row["price"] == float(data["p"])
    assert row["stream"] == "trade"


def test_parse_missing_field_raises_frame_parse_error():
    broken = copy.deepcopy(SAMPLE_BOOKTICKER_FRAME)
    del broken["data"]["s"]
    with pytest.raises(FrameParseError, match="s"):
        parse_combined_frame(broken, seq=0, rtime_ns=1)


def test_decode_frame_invalid_json_raises():
    with pytest.raises(FrameParseError):
        decode_frame(b"{not json")


# --- 03-06 Task 1: schema v2 (exec_type) -------------------------------


def test_parse_trade_populates_exec_type_from_x_field():
    """Test 1 (03-06-PLAN.md): a MARKET-execution trade frame produces a row
    with exec_type == "MARKET"."""
    data = dict(SAMPLE_TRADE_FRAME["data"])
    assert data["X"] == "MARKET"
    row = parse_trade(data, seq=0, rtime_ns=1)
    assert row["exec_type"] == "MARKET"


def test_parse_trade_na_placeholder_produces_self_describing_row():
    """Test 2 (03-06-PLAN.md): the live stream's X="NA" placeholder (p="0",
    q="0") is still written verbatim, now self-describing via exec_type
    instead of the ambiguous price==0 AND qty==0 heuristic."""
    data = dict(SAMPLE_TRADE_FRAME["data"])
    data["X"] = "NA"
    data["p"] = "0"
    data["q"] = "0"
    row = parse_trade(data, seq=0, rtime_ns=1)
    assert row["exec_type"] == "NA"
    assert row["price"] == 0.0
    assert row["qty"] == 0.0


def test_parse_trade_missing_x_field_raises_frame_parse_error():
    """Test 3 (03-06-PLAN.md): X is NOT optional once schema_version=2 is in
    effect -- a frame missing it fails the same way every other required
    field does."""
    data = dict(SAMPLE_TRADE_FRAME["data"])
    del data["X"]
    with pytest.raises(FrameParseError, match="X"):
        parse_trade(data, seq=0, rtime_ns=1)


def test_trade_schema_v2_accepts_parse_trade_rows_in_dataframe_construction():
    """Test 4 (03-06-PLAN.md): regression guard for rotation.py's
    construction-time hazard -- pl.DataFrame(rows, schema=TRADE_SCHEMA) must
    succeed for a batch of parse_trade-produced rows now that
    SCHEMA_VERSION=2 requires exec_type on every row."""
    assert SCHEMA_VERSION == 2
    assert "exec_type" in TRADE_SCHEMA
    rows = [
        parse_trade(SAMPLE_TRADE_FRAME["data"], seq=i, rtime_ns=i) for i in range(3)
    ]
    df = pl.DataFrame(rows, schema=TRADE_SCHEMA)
    assert df.height == 3
    assert df["exec_type"].to_list() == ["MARKET", "MARKET", "MARKET"]
