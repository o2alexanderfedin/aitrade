"""Tests for data.store.load_curated -- the default loader resolves a
manifest_id to verified paths, never a glob, never a `latest` path."""

from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl
import pytest

from data.store import ManifestHashMismatch, issue_manifest, load_curated


def _write_partition(lake_root: Path, rel_path: str, df: pl.DataFrame) -> dict:
    final_path = lake_root / rel_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(final_path, compression="zstd")
    st = final_path.stat()
    return {
        "date": "2026-09-12",
        "path": rel_path,
        "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
        "rows": df.height,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": int(df["etime"].min()),
        "etime_max": int(df["etime"].max()),
    }


def test_load_curated_returns_concatenated_verified_rows(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"

    df1 = pl.DataFrame(
        {"trade_id": [1, 2], "etime": [1_000, 2_000], "price": [1.0, 2.0]}
    )
    df2 = pl.DataFrame(
        {"trade_id": [3, 4], "etime": [3_000, 4_000], "price": [3.0, 4.0]}
    )
    part1 = _write_partition(lake_root, "curated/part-1.parquet", df1)
    part2 = _write_partition(lake_root, "curated/part-2.parquet", df2)

    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part1, part2],
        code_hash="deadbeef",
        registry_root=registry_root,
    )

    loaded = load_curated(
        manifest["manifest_id"],
        "BTCUSDT.trade",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert loaded.height == 4
    assert sorted(loaded["trade_id"].to_list()) == [1, 2, 3, 4]


def test_load_curated_raises_on_hash_mismatch_never_returns_wrong_data(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    df = pl.DataFrame({"trade_id": [1], "etime": [1_000], "price": [1.0]})
    part = _write_partition(lake_root, "curated/part-1.parquet", df)
    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
    )

    on_disk_path = lake_root / part["path"]
    with open(on_disk_path, "ab") as f:
        f.write(b"\x00")

    with pytest.raises(ManifestHashMismatch):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )
