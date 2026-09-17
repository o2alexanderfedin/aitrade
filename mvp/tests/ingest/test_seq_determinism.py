"""Property test: shuffling synthetic input rows before calling
materialize_seq() yields an identical (etime, seq)-sorted decision-row
sequence -- seq is a pure function of a genuine total sort order, not of
arrival order (DATA-05)."""

from __future__ import annotations

import random

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from data.ingest.curated_build import materialize_seq


def _decision_rows(result: pl.DataFrame) -> pl.DataFrame:
    """Last row of each etime group, via arg max(etime, seq) -- the
    project's own decision-row rule (spec.md Conventions)."""
    ordered = result.sort(["etime", "seq"])
    return ordered.group_by("etime", maintain_order=True).agg(
        pl.col("trade_id").last().alias("trade_id"), pl.col("seq").last().alias("seq")
    )


@given(seed=st.integers(min_value=0, max_value=1_000_000))
@settings(max_examples=25)
def test_shuffled_input_yields_identical_decision_rows(seed: int):
    rows = [
        {"trade_id": i, "etime": (i // 3) * 1_000, "price": float(i)}
        for i in range(1, 31)
    ]
    rng = random.Random(seed)
    shuffled = rows[:]
    rng.shuffle(shuffled)

    df_in_order = pl.DataFrame(rows)
    df_shuffled = pl.DataFrame(shuffled)

    result_in_order = materialize_seq(df_in_order, ["etime", "trade_id"])
    result_shuffled = materialize_seq(df_shuffled, ["etime", "trade_id"])

    decisions_in_order = _decision_rows(result_in_order)
    decisions_shuffled = _decision_rows(result_shuffled)

    assert decisions_in_order.equals(decisions_shuffled)


def test_materialize_seq_raises_on_duplicate_id():
    df = pl.DataFrame(
        {
            "trade_id": [1, 1, 2],
            "etime": [1_000, 1_000, 2_000],
            "price": [1.0, 1.0, 2.0],
        }
    )
    with pytest.raises(ValueError, match="duplicate value"):
        materialize_seq(df, ["etime", "trade_id"])


def test_materialize_seq_drops_archive_sentinel_seq_column():
    df = pl.DataFrame(
        {
            "trade_id": [1, 2],
            "etime": [1_000, 2_000],
            "price": [1.0, 2.0],
            "seq": [-1, -1],
        }
    )
    result = materialize_seq(df, ["etime", "trade_id"])
    assert "capture_seq" not in result.columns
    assert result["seq"].to_list() == [0, 1]


def test_materialize_seq_preserves_real_capture_seq():
    df = pl.DataFrame(
        {
            "trade_id": [1, 2],
            "etime": [1_000, 2_000],
            "price": [1.0, 2.0],
            "seq": [500, 501],
        }
    )
    result = materialize_seq(df, ["etime", "trade_id"])
    assert result["capture_seq"].to_list() == [500, 501]
    assert result["seq"].to_list() == [0, 1]
