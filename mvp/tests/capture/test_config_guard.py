"""Tests for data.capture.config.validate_data_root and data.capture.seq.SeqAssigner."""

import pytest

from data.capture.config import DataRootError, validate_data_root
from data.capture.seq import SeqAssigner


def test_empty_data_root_raises_required():
    with pytest.raises(DataRootError, match="required"):
        validate_data_root("")


def test_cloud_storage_path_raises_before_existence_check():
    with pytest.raises(DataRootError, match="CloudStorage"):
        validate_data_root(
            "/Users/x/Library/CloudStorage/OneDrive-Personal/fake"
        )


def test_missing_path_raises_does_not_exist(tmp_path):
    with pytest.raises(DataRootError, match="does not exist"):
        validate_data_root(str(tmp_path / "missing"))


def test_insufficient_free_space_raises(tmp_path):
    with pytest.raises(DataRootError, match="GiB free"):
        validate_data_root(str(tmp_path), min_free_gb=10**9)


def test_zero_min_free_gb_returns_resolved_path(tmp_path):
    result = validate_data_root(str(tmp_path), min_free_gb=0)
    assert result == tmp_path.resolve()


def test_seq_assigner_increments_per_key():
    seq = SeqAssigner()
    assert seq.next("BTCUSDT", "bookTicker") == 0
    assert seq.next("BTCUSDT", "bookTicker") == 1
    assert seq.next("BTCUSDT", "bookTicker") == 2


def test_seq_assigner_separate_counter_per_key():
    seq = SeqAssigner()
    seq.next("BTCUSDT", "bookTicker")
    seq.next("BTCUSDT", "bookTicker")
    assert seq.next("ETHUSDT", "bookTicker") == 0


def test_seq_assigner_seed_resumes_from_last_value():
    seq = SeqAssigner()
    seq.seed("BTCUSDT", "trade", 41)
    assert seq.next("BTCUSDT", "trade") == 42
