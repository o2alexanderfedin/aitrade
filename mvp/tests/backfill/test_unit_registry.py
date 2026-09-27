"""Tests for data.unit_registry against the real committed unit_registry.toml."""

import pytest

from data.unit_registry import UnitRegistryError, get_unit_entry


def test_futures_um_trades_resolves_measured_convention():
    entry = get_unit_entry("futures-um", "trades")
    assert entry.header is True
    assert entry.time_unit == "ms"
    assert entry.columns == [
        "id",
        "price",
        "qty",
        "quote_qty",
        "time",
        "is_buyer_maker",
    ]


def test_spot_trades_resolves_declared_but_unused_row():
    entry = get_unit_entry("spot", "trades")
    assert entry.header is False
    assert entry.time_unit == "us_since_2025-01-01"


def test_unknown_dataset_raises_unit_registry_error():
    with pytest.raises(UnitRegistryError, match="unknown"):
        get_unit_entry("futures-um", "unknown-dataset")


def test_unknown_market_raises_unit_registry_error():
    with pytest.raises(UnitRegistryError, match="unknown"):
        get_unit_entry("unknown-market", "trades")


def test_malformed_entry_missing_required_key_raises(tmp_path):
    bad_toml = tmp_path / "unit_registry.toml"
    bad_toml.write_text('["futures-um.trades"]\nheader = true\n')
    with pytest.raises(UnitRegistryError, match="missing required key"):
        get_unit_entry("futures-um", "trades", path=bad_toml)
