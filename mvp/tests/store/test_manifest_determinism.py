"""Regression guard for 03-RESEARCH.md Pattern 3's premise: `pl.write_parquet`
is byte-identical for identical input, across separate writes -- so
comparing a manifest's stored hash against the file currently on disk (never
a rebuild-and-compare) is a sound identity check."""

from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl


def _sample_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "trade_id": list(range(1000)),
            "etime": [1_000_000_000 + i for i in range(1000)],
            "price": [100.0 + i * 0.01 for i in range(1000)],
        }
    )


def test_write_parquet_byte_identical_across_two_writes(tmp_path: Path):
    df = _sample_df()
    path_a = tmp_path / "a.parquet"
    path_b = tmp_path / "b.parquet"

    df.write_parquet(path_a, compression="zstd")
    df.write_parquet(path_b, compression="zstd")

    hash_a = hashlib.sha256(path_a.read_bytes()).hexdigest()
    hash_b = hashlib.sha256(path_b.read_bytes()).hexdigest()
    assert hash_a == hash_b


def test_write_parquet_byte_identical_across_process_reconstruction(tmp_path: Path):
    """Same logical DataFrame, rebuilt from scratch (not the same in-memory
    object) -- still byte-identical, since the writer's encoding is a
    property of the (fixed-version) writer, not the data's provenance."""
    df1 = _sample_df()
    df2 = pl.DataFrame(
        {
            "trade_id": list(range(1000)),
            "etime": [1_000_000_000 + i for i in range(1000)],
            "price": [100.0 + i * 0.01 for i in range(1000)],
        }
    )

    path_a = tmp_path / "a.parquet"
    path_b = tmp_path / "b.parquet"
    df1.write_parquet(path_a, compression="zstd")
    df2.write_parquet(path_b, compression="zstd")

    assert (
        hashlib.sha256(path_a.read_bytes()).hexdigest()
        == hashlib.sha256(path_b.read_bytes()).hexdigest()
    )
