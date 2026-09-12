"""Tests for data.capture.parse — single ms-to-ns conversion site."""

import copy

import pytest

from data.capture.parse import FrameParseError, decode_frame, parse_combined_frame
from data.schema import SCHEMA_VERSION
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
