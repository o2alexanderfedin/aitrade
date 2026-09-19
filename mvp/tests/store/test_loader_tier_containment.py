"""03-REVIEW.md CR-04: the default loader must enforce the curated tier and
path containment itself -- these tests CALL `load_curated` (the test the
review singled out, `test_default_loader_cannot_reach_lockbox`, never did).

Each test builds a readable (never chmod'd) fixture, so the ONLY thing that
can stop the read is the loader's own refusal -- the state of the lockbox
directory during a human-unlocked gate evaluation, when the physical barrier
is lifted.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import polars as pl
import pytest

from data import store
from data.store import by_date_index_path, dq_report_path, issue_manifest, load_curated

DATE = "2026-09-13"


def _ok_report(lake_root: Path, manifest_id: str = "unbound") -> None:
    path = dq_report_path(lake_root, DATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "date": [DATE],
            "symbol": ["BTCUSDT"],
            "stream": ["trade"],
            "manifest_id": [manifest_id],
            "check": ["gap_coverage"],
            "dq_status": ["ok"],
            "value": [0.0],
            "count": [None],
            "detail": [None],
        },
        schema={
            "date": pl.Utf8,
            "symbol": pl.Utf8,
            "stream": pl.Utf8,
            "manifest_id": pl.Utf8,
            "check": pl.Utf8,
            "dq_status": pl.Utf8,
            "value": pl.Float64,
            "count": pl.Int64,
            "detail": pl.Utf8,
        },
    ).write_parquet(path)


def _partition(lake_root: Path, rel: str, *, on_disk: Path | None = None) -> dict:
    target = on_disk if on_disk is not None else lake_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        pl.DataFrame(
            {"trade_id": [1, 2], "etime": [1_000, 2_000], "price": [1.0, 2.0]}
        ).write_parquet(target)
    data = (
        (lake_root / rel).read_bytes()
        if (lake_root / rel).exists()
        else target.read_bytes()
    )
    return {
        "date": DATE,
        "path": rel,
        "sha256": hashlib.sha256(data).hexdigest(),
        "rows": 2,
        "size_bytes": len(data),
        "mtime_ns": 0,
        "etime_min": 1_000,
        "etime_max": 2_000,
    }


def _issue(registry_root: Path, part: dict, tier: str) -> dict:
    if store.partition_path_problem(part["path"]) is not None:
        return _hand_write(registry_root, part, tier)
    return issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier=tier,
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
    )


def _hand_write(registry_root: Path, part: dict, tier: str) -> dict:
    """`issue_manifest` refuses an escaping, absolute or non-canonical path
    (03-REVIEW-ITER3.md IN-19), so the manifest is written by hand, as an
    attacker would: the loader must refuse it on its own."""
    body = {
        "dataset": "BTCUSDT.trade",
        "symbol": "BTCUSDT",
        "stream": "trade",
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
    path = store.manifest_path(registry_root, "BTCUSDT.trade", manifest["manifest_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2))
    return manifest


def _spy_reads(monkeypatch) -> list:
    """Record every partition read `load_curated` attempts, so a test can
    assert the refusal happened BEFORE any file was touched."""
    reads: list = []
    real_read = pl.read_parquet

    def spy(path, *args, **kwargs):
        reads.append(path)
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(store.pl, "read_parquet", spy)
    return reads


def test_lockbox_tier_manifest_is_refused_before_any_read(tmp_path: Path, monkeypatch):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    _ok_report(lake_root)
    part = _partition(
        lake_root, "lockbox/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-1.parquet"
    )
    manifest = _issue(registry_root, part, tier="lockbox")
    reads = _spy_reads(monkeypatch)
    with pytest.raises(store.ManifestTierError):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )
    assert reads == []


@pytest.mark.parametrize(
    "rel",
    [
        "lockbox/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-1.parquet",
        "curated/../lockbox/symbol=BTCUSDT/date=2026-09-13/part-1.parquet",
        "curated/symbol=BTCUSDT/../../raw/part-1.parquet",
    ],
)
def test_curated_tier_manifest_naming_a_path_outside_curated_is_refused(
    tmp_path: Path, monkeypatch, rel: str
):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    _ok_report(lake_root)
    part = _partition(lake_root, rel)
    manifest = _issue(registry_root, part, tier="curated")
    reads = _spy_reads(monkeypatch)
    with pytest.raises(store.ManifestTierError):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )
    assert reads == []


def test_absolute_partition_path_is_refused(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    _ok_report(lake_root)
    outside = tmp_path / "elsewhere" / "part-1.parquet"
    part = _partition(lake_root, str(outside), on_disk=outside)
    manifest = _issue(registry_root, part, tier="curated")
    with pytest.raises(store.ManifestTierError):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )


def test_symlink_inside_curated_pointing_into_lockbox_is_refused(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    _ok_report(lake_root)
    real = _partition(lake_root, "lockbox/segment/part-1.parquet")
    link = (
        lake_root / "curated/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-1.parquet"
    )
    link.parent.mkdir(parents=True)
    os.symlink(lake_root / real["path"], link)
    part = {**real, "path": str(link.relative_to(lake_root))}
    manifest = _issue(registry_root, part, tier="curated")
    with pytest.raises(store.ManifestTierError):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )


def test_lockbox_manifest_never_repoints_the_curated_by_date_index(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    curated = _issue(
        registry_root,
        _partition(
            lake_root,
            "curated/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-1.parquet",
        ),
        tier="curated",
    )
    _issue(
        registry_root,
        _partition(
            lake_root,
            "lockbox/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-2.parquet",
        ),
        tier="lockbox",
    )
    idx = by_date_index_path(registry_root, "BTCUSDT.trade", "BTCUSDT", "trade", DATE)
    assert json.loads(idx.read_text())["manifest_id"] == curated["manifest_id"]


def test_well_formed_curated_manifest_still_loads(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    part = _partition(
        lake_root, "curated/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-1.parquet"
    )
    manifest = _issue(registry_root, part, tier="curated")
    _ok_report(lake_root, manifest["manifest_id"])
    df = load_curated(
        manifest["manifest_id"],
        "BTCUSDT.trade",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert df.height == 2


# --- 03-REVIEW-ITER2.md IN-10: resolve() cannot see a hard link, and it
# --- agrees with itself when the tier directory is itself a link -----------


def _curated_part_and_manifest(lake_root: Path, registry_root: Path) -> dict:
    part = _partition(
        lake_root, "curated/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-1.parquet"
    )
    manifest = _issue(registry_root, part, tier="curated")
    _ok_report(lake_root, manifest["manifest_id"])
    by_date_index_path(
        registry_root, "BTCUSDT.trade", "BTCUSDT", "trade", DATE
    ).parent.mkdir(parents=True, exist_ok=True)
    return manifest


def test_a_hard_link_into_curated_is_refused(tmp_path: Path, monkeypatch):
    """A hard link in `curated/` to a lockbox partition IS a path under
    `curated/` -- `resolve()` reports the link's own path, so the whitelist
    test passes while the bytes behind it are quarantined."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    secret = lake_root / "lockbox/symbol=BTCUSDT/stream=trade/date=2026-09-13/s.parquet"
    secret.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"trade_id": [9], "etime": [9], "price": [9.0]}).write_parquet(secret)

    rel = "curated/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-1.parquet"
    link = lake_root / rel
    link.parent.mkdir(parents=True, exist_ok=True)
    os.link(secret, link)
    assert link.resolve().is_relative_to((lake_root / "curated").resolve())

    part = {
        "date": DATE,
        "path": rel,
        "sha256": hashlib.sha256(link.read_bytes()).hexdigest(),
        "rows": 1,
        "size_bytes": link.stat().st_size,
        "mtime_ns": 0,
        "etime_min": 9,
        "etime_max": 9,
    }
    manifest = _issue(registry_root, part, tier="curated")
    _ok_report(lake_root, manifest["manifest_id"])
    reads = _spy_reads(monkeypatch)

    with pytest.raises(store.ManifestTierError, match="hard link"):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )
    assert reads == []


def test_a_symlinked_curated_tier_directory_is_refused(tmp_path: Path, monkeypatch):
    """With `lake/curated -> lake/lockbox`, the tier root and every partition
    resolve to the same place, so containment-by-resolved-path agrees with
    itself and lets the read through."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    real = lake_root / "lockbox"
    part_dir = real / "symbol=BTCUSDT/stream=trade/date=2026-09-13"
    part_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"trade_id": [9], "etime": [9], "price": [9.0]}).write_parquet(
        part_dir / "part-1.parquet"
    )
    (lake_root / "curated").symlink_to(real, target_is_directory=True)

    rel = "curated/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-1.parquet"
    data = (lake_root / rel).read_bytes()
    part = {
        "date": DATE,
        "path": rel,
        "sha256": hashlib.sha256(data).hexdigest(),
        "rows": 1,
        "size_bytes": len(data),
        "mtime_ns": 0,
        "etime_min": 9,
        "etime_max": 9,
    }
    manifest = _issue(registry_root, part, tier="curated")
    _ok_report(lake_root, manifest["manifest_id"])
    reads = _spy_reads(monkeypatch)

    with pytest.raises(store.ManifestTierError, match="is a symlink"):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )
    assert reads == []


def test_the_verified_bytes_are_the_returned_bytes(tmp_path: Path, monkeypatch):
    """IN-10's third strand: the loader used to hash the file and then let
    `pl.read_parquet` REOPEN it, so the bytes that were verified and the bytes
    that were returned came from two different opens.

    The swap here lands after the verifying read of the final pass. With one
    open, the rows returned are the ones that were hashed; with two, the
    caller silently gets the substituted file under a passing hash check.
    """
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    manifest = _curated_part_and_manifest(lake_root, registry_root)
    rel = manifest["partitions"][0]["path"]

    real_read_bytes = Path.read_bytes
    reads = {"n": 0}

    def swapping_read_bytes(self):
        data = real_read_bytes(self)
        if self.name.endswith(".parquet"):
            reads["n"] += 1
            if reads["n"] == 2:  # the read inside read_verified_partitions
                pl.DataFrame(
                    {"trade_id": [7], "etime": [7], "price": [7.0]}
                ).write_parquet(lake_root / rel)
        return data

    monkeypatch.setattr(Path, "read_bytes", swapping_read_bytes)
    df = load_curated(
        manifest["manifest_id"],
        "BTCUSDT.trade",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert sorted(df["trade_id"].to_list()) == [1, 2], (
        "the returned rows came from a second open, not from the verified bytes"
    )


# --- 04-02-PLAN.md T-04-06: `issue_manifest` now writes a by-date pointer
# --- for the features tier as well as the curated one
# --- (`store.BY_DATE_INDEXED_TIERS`). These two tests are the control on
# --- that extension: the quarantined tier stays outside the set, so it
# --- still gets no date-addressable entry point of its own.


def _issue_for(
    registry_root: Path, dataset: str, stream: str, tier: str, rel: str, lake_root: Path
) -> dict:
    part = _partition(lake_root, rel)
    return issue_manifest(
        dataset=dataset,
        symbol="BTCUSDT",
        stream=stream,
        tier=tier,
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
    )


def test_a_quarantined_tier_manifest_still_gets_no_by_date_pointer(tmp_path: Path):
    """The tier allowlist, asserted from the side that must stay excluded:
    a features manifest gets a pointer, a quarantined-tier one gets none --
    in its OWN dataset namespace, where no curated pointer exists to be
    protected by the namespace argument alone."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"

    features = _issue_for(
        registry_root,
        "BTCUSDT.features",
        "features",
        store.FEATURES_TIER,
        "features/symbol=BTCUSDT/date=2026-09-13/part-1.parquet",
        lake_root,
    )
    feature_idx = by_date_index_path(
        registry_root, "BTCUSDT.features", "BTCUSDT", "features", DATE
    )
    assert json.loads(feature_idx.read_text())["manifest_id"] == features["manifest_id"]

    _issue_for(
        registry_root,
        "BTCUSDT.held_out",
        "held_out",
        "lockbox",
        "lockbox/symbol=BTCUSDT/date=2026-09-13/part-2.parquet",
        lake_root,
    )
    quarantined_idx = by_date_index_path(
        registry_root, "BTCUSDT.held_out", "BTCUSDT", "held_out", DATE
    )
    assert not quarantined_idx.exists(), (
        "the quarantined tier must never be date-addressable"
    )
    assert "lockbox" not in store.BY_DATE_INDEXED_TIERS
    assert store.BY_DATE_INDEXED_TIERS == frozenset({"curated", "features"})
