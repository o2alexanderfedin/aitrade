"""CR-04 for the features tier: `load_features` refuses a manifest of
another tier, and any partition path that does not GENUINELY resolve
inside `lake_root/features/` -- each before a byte is read.

The same four escapes `tests/store/test_loader_tier_containment.py` pins
for the curated loader, one tier over: wrong tier, a `..` path that leaves
the tier, a symlinked tier DIRECTORY (where containment-by-resolved-path
agrees with itself), and a hard link (which `resolve()` cannot see).

None of them needs the quarantined tier by name: the escape target here is
`raw/`, and the code path exercised is identical -- `_enforce_tier_
containment` does not know what is on the other side of the escape. The
quarantined-tier half is asserted in `test_loader_tier_containment.py`,
the file `tools/check_lockbox_containment.py` sanctions for naming it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import polars as pl
import pytest

from data import store
from features.tier import load_features
from tests.fixtures.feature_tier import (
    DATE,
    SYMBOL,
    feature_frame,
    write_features_dq_report,
)

DATASET = f"{SYMBOL}.features"
GOOD_REL = f"features/symbol={SYMBOL}/date={DATE}/part-1.parquet"


def _write(lake_root: Path, rel: str, *, on_disk: Path | None = None) -> dict:
    target = on_disk if on_disk is not None else lake_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        feature_frame().write_parquet(target, compression="zstd")
    data = target.read_bytes()
    return {
        "date": DATE,
        "path": rel,
        "sha256": hashlib.sha256(data).hexdigest(),
        "rows": 3,
        "size_bytes": len(data),
        "mtime_ns": 0,
        "etime_min": 1_000,
        "etime_max": 3_000,
    }


def _manifest(registry_root: Path, part: dict, tier: str, dataset: str = DATASET):
    """Hand-written when `issue_manifest` would refuse the path itself
    (03-REVIEW-ITER3.md IN-19) -- an attacker writes the JSON, so the
    loader must refuse it on its own."""
    body = {
        "dataset": dataset,
        "symbol": SYMBOL,
        "stream": "features",
        "tier": tier,
        "schema_version": 1,
        "built_at": 0,
        "code_hash": "deadbeef",
        "inputs": [],
        "partitions": [part],
        "row_count": part["rows"],
        "etime_range": [part["etime_min"], part["etime_max"]],
    }
    manifest = {"manifest_id": store.compute_manifest_id(body), **body}
    path = store.manifest_path(registry_root, dataset, manifest["manifest_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2))
    return manifest


def _spy_reads(monkeypatch) -> list:
    reads: list = []
    real_read = pl.read_parquet

    def spy(path, *args, **kwargs):
        reads.append(path)
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(store.pl, "read_parquet", spy)
    return reads


def _load(manifest, registry_root: Path, lake_root: Path, dataset: str = DATASET):
    return load_features(
        manifest["manifest_id"],
        dataset,
        registry_root=registry_root,
        lake_root=lake_root,
    )


def test_a_curated_tier_manifest_is_refused_before_any_read(
    tmp_path: Path, monkeypatch
):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    part = _write(lake_root, GOOD_REL)
    manifest = _manifest(registry_root, part, tier=store.CURATED_TIER)
    reads = _spy_reads(monkeypatch)
    with pytest.raises(store.ManifestTierError):
        _load(manifest, registry_root, lake_root)
    assert reads == []


@pytest.mark.parametrize(
    "rel",
    [
        f"raw/symbol={SYMBOL}/date={DATE}/part-1.parquet",
        f"features/../raw/symbol={SYMBOL}/date={DATE}/part-1.parquet",
        f"features/symbol={SYMBOL}/../../curated/part-1.parquet",
    ],
)
def test_a_partition_path_outside_the_features_tier_is_refused(
    tmp_path: Path, monkeypatch, rel: str
):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    part = _write(lake_root, rel)
    manifest = _manifest(registry_root, part, tier=store.FEATURES_TIER)
    reads = _spy_reads(monkeypatch)
    with pytest.raises(store.ManifestTierError):
        _load(manifest, registry_root, lake_root)
    assert reads == []


def test_an_absolute_partition_path_is_refused(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    outside = tmp_path / "elsewhere" / "part-1.parquet"
    part = _write(lake_root, str(outside), on_disk=outside)
    manifest = _manifest(registry_root, part, tier=store.FEATURES_TIER)
    with pytest.raises(store.ManifestTierError):
        _load(manifest, registry_root, lake_root)


def test_a_symlinked_features_tier_directory_is_refused(tmp_path: Path, monkeypatch):
    """With `lake/features -> lake/raw`, the tier root and every partition
    resolve to the same place, so containment-by-resolved-path agrees with
    itself and would let the read through."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    real = lake_root / "raw"
    part_dir = real / f"symbol={SYMBOL}/date={DATE}"
    part_dir.mkdir(parents=True, exist_ok=True)
    feature_frame().write_parquet(part_dir / "part-1.parquet", compression="zstd")
    (lake_root / "features").symlink_to(real, target_is_directory=True)

    part = _write(lake_root, GOOD_REL)
    manifest = _manifest(registry_root, part, tier=store.FEATURES_TIER)
    reads = _spy_reads(monkeypatch)
    with pytest.raises(store.ManifestTierError, match="is a symlink"):
        _load(manifest, registry_root, lake_root)
    assert reads == []


def test_a_hard_link_into_the_features_tier_is_refused(tmp_path: Path, monkeypatch):
    """A hard link under `features/` IS a path under `features/` --
    `resolve()` reports the link's own path, so the containment test passes
    while the bytes behind it live somewhere else entirely."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    elsewhere = lake_root / "raw" / f"symbol={SYMBOL}" / f"date={DATE}" / "s.parquet"
    elsewhere.parent.mkdir(parents=True, exist_ok=True)
    feature_frame().write_parquet(elsewhere, compression="zstd")

    link = lake_root / GOOD_REL
    link.parent.mkdir(parents=True, exist_ok=True)
    os.link(elsewhere, link)
    assert link.resolve().is_relative_to((lake_root / "features").resolve())

    part = _write(lake_root, GOOD_REL)
    manifest = _manifest(registry_root, part, tier=store.FEATURES_TIER)
    reads = _spy_reads(monkeypatch)
    with pytest.raises(store.ManifestTierError, match="hard link"):
        _load(manifest, registry_root, lake_root)
    assert reads == []


def test_a_well_formed_features_manifest_still_loads(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    part = _write(lake_root, GOOD_REL)
    manifest = _manifest(registry_root, part, tier=store.FEATURES_TIER)
    write_features_dq_report(lake_root, DATE, manifest["manifest_id"])
    assert _load(manifest, registry_root, lake_root).height == 3
