"""RP-1 (immutability) and RP-2 (manifest-hash integrity) as `tmp_path`
fixtures -- zero dependency on the real mounted `/Volumes/ProjectsSSD` lake,
which is what makes `check_no_manifest_rewrite`'s own correctness provable
in CI, where the real lake is never mounted."""

from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl
import pytest

from data.store import (
    ManifestHashMismatch,
    issue_manifest,
    resolve_manifest,
)
from tools import check_no_manifest_rewrite
from tools.check_no_manifest_rewrite import main, verify_manifest, verify_manifest_fast


def _write_partition(lake_root: Path, rel_path: str, df: pl.DataFrame) -> dict:
    """Write `df` to `lake_root/rel_path` and return the `partitions[]`
    entry `issue_manifest` expects: path, sha256, rows, size_bytes, mtime_ns,
    etime_min, etime_max, date."""
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


def _sample_df(offset: int = 0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "trade_id": [1 + offset, 2 + offset],
            "etime": [1_000_000 + offset, 2_000_000 + offset],
            "price": [100.0 + offset, 101.0 + offset],
        }
    )


def test_issue_manifest_resolve_manifest_round_trip_matching_hashes(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    df = _sample_df()
    partition = _write_partition(lake_root, "curated/part-1.parquet", df)
    input_path = tmp_path / "raw" / "input.parquet"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(input_path)
    inputs = [
        {
            "path": str(input_path),
            "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "rows": df.height,
        }
    ]

    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=inputs,
        partitions=[partition],
        code_hash="deadbeef",
        registry_root=registry_root,
    )

    resolved = resolve_manifest(
        manifest["manifest_id"],
        "BTCUSDT.trade",
        registry_root=registry_root,
        lake_root=lake_root,
        expected_tier="curated",
    )
    assert resolved["manifest_id"] == manifest["manifest_id"]
    assert resolved["partitions"][0]["sha256"] == partition["sha256"]
    assert resolved["row_count"] == df.height


def test_rp1_immutability_append_byte_then_restore(tmp_path: Path):
    """RP-1: append one byte to a committed partition file -> the guardrail
    exits non-zero (verify_manifest returns the path) naming that path.
    Restore -> green."""
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    df = _sample_df()
    partition = _write_partition(lake_root, "curated/part-1.parquet", df)
    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[partition],
        code_hash="deadbeef",
        registry_root=registry_root,
    )

    # Sanity: green before any mutation.
    assert verify_manifest(manifest, lake_root) == []
    assert verify_manifest_fast(manifest, lake_root) == []

    on_disk_path = lake_root / partition["path"]
    original_bytes = on_disk_path.read_bytes()
    original_stat = on_disk_path.stat()

    # RED: append one byte.
    with open(on_disk_path, "ab") as f:
        f.write(b"\x00")

    bad_paths = verify_manifest(manifest, lake_root)
    assert bad_paths == [partition["path"]]
    bad_paths_fast = verify_manifest_fast(manifest, lake_root)
    assert bad_paths_fast == [partition["path"]]

    # Restore: truncate back to the original bytes AND the original mtime,
    # so the fast check goes green too (a restored-content-but-new-mtime
    # file would otherwise still trip verify_manifest_fast).
    on_disk_path.write_bytes(original_bytes)
    import os

    os.utime(
        on_disk_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )

    assert verify_manifest(manifest, lake_root) == []
    assert verify_manifest_fast(manifest, lake_root) == []


def test_rp2_manifest_resolves_to_exactly_the_bytes_it_names(tmp_path: Path):
    """RP-2: swap two partition files between two manifests -> resolve_manifest
    raises ManifestHashMismatch rather than returning the wrong data."""
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"

    df_a = _sample_df(offset=0)
    df_b = _sample_df(offset=1000)
    part_a = _write_partition(lake_root, "curated/part-a.parquet", df_a)
    part_b = _write_partition(lake_root, "curated/part-b.parquet", df_b)

    manifest_a = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part_a],
        code_hash="deadbeef",
        registry_root=registry_root,
        dates=["2026-09-12"],
    )
    manifest_b = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part_b],
        code_hash="deadbeef",
        registry_root=registry_root,
        dates=["2026-09-13"],
    )

    # Swap the on-disk bytes at the two partition paths (not the manifests'
    # own recorded sha256/paths) -- simulating a corrupted/tampered lake.
    path_a = lake_root / part_a["path"]
    path_b = lake_root / part_b["path"]
    bytes_a = path_a.read_bytes()
    bytes_b = path_b.read_bytes()
    path_a.write_bytes(bytes_b)
    path_b.write_bytes(bytes_a)

    with pytest.raises(ManifestHashMismatch):
        resolve_manifest(
            manifest_a["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
            expected_tier="curated",
        )
    with pytest.raises(ManifestHashMismatch):
        resolve_manifest(
            manifest_b["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
            expected_tier="curated",
        )


def test_verify_manifest_fast_catches_size_changing_mutation_without_reading_contents(
    tmp_path: Path,
):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    df = _sample_df()
    partition = _write_partition(lake_root, "curated/part-1.parquet", df)
    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[partition],
        code_hash="deadbeef",
        registry_root=registry_root,
    )

    on_disk_path = lake_root / partition["path"]
    with open(on_disk_path, "ab") as f:
        f.write(b"\x00")

    assert verify_manifest_fast(manifest, lake_root) == [partition["path"]]


def test_main_skips_honestly_when_lake_root_unmounted(tmp_path, monkeypatch, capsys):
    """T-03-09: when the real lake root does not exist at all (e.g. a
    GitHub Actions runner, which never has the SSD mounted), main() prints
    SKIP and exits 0 -- an honest, disposition=accept blind spot -- rather
    than crashing or silently passing without saying why."""
    fake_lake_root = tmp_path / "nonexistent_lake"
    registry_root = tmp_path / "registry"
    # A non-empty registry (WR-07: zero manifests is a FAIL, not a SKIP) --
    # the SKIP under test is about the unmounted LAKE, nothing else.
    partition = _write_partition(
        tmp_path / "elsewhere", "curated/p.parquet", _sample_df()
    )
    issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[partition],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    monkeypatch.setattr(
        check_no_manifest_rewrite, "DEFAULT_LAKE_ROOT", str(fake_lake_root)
    )
    monkeypatch.setattr(check_no_manifest_rewrite, "LAKE_REGISTRY_ROOT", registry_root)

    exit_code = main([])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "SKIP" in out
    assert "1 manifest(s) unverified" in out
    assert str(fake_lake_root) in out


def test_main_fails_on_a_real_finding_even_when_lake_root_exists(tmp_path, monkeypatch):
    """main() must not silently pass just because the lake root exists --
    a real hash mismatch against a mounted lake is a genuine finding."""
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    lake_root.mkdir(parents=True)

    df = pl.DataFrame({"trade_id": [1], "etime": [1_000], "price": [1.0]})
    partition = _write_partition(lake_root, "curated/part-1.parquet", df)
    issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[partition],
        code_hash="deadbeef",
        registry_root=registry_root,
    )

    on_disk_path = lake_root / partition["path"]
    with open(on_disk_path, "ab") as f:
        f.write(b"\x00")

    monkeypatch.setattr(check_no_manifest_rewrite, "DEFAULT_LAKE_ROOT", str(lake_root))
    monkeypatch.setattr(check_no_manifest_rewrite, "LAKE_REGISTRY_ROOT", registry_root)

    assert main([]) == 1
    assert main(["--full"]) == 1


def test_explicit_lake_root_override_runs_the_full_scan_against_it(tmp_path: Path):
    """--lake-root/--registry-root (finding 1's fixture-lake CI leg): an
    explicit override runs the real verify_manifest code path against
    whatever lake it names, independent of DEFAULT_LAKE_ROOT/
    LAKE_REGISTRY_ROOT -- this is what lets CI exercise the real scan
    against a small committed fixture lake instead of always SKIPping."""
    lake_root = tmp_path / "fixture_lake"
    registry_root = tmp_path / "fixture_registry"
    df = _sample_df()
    partition = _write_partition(lake_root, "curated/part-1.parquet", df)
    issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[partition],
        code_hash="deadbeef",
        registry_root=registry_root,
    )

    argv = [
        "--full",
        "--lake-root",
        str(lake_root),
        "--registry-root",
        str(registry_root),
    ]
    assert main(argv) == 0

    # RED: mutate the fixture partition -> the override scan fails.
    on_disk_path = lake_root / partition["path"]
    original_bytes = on_disk_path.read_bytes()
    with open(on_disk_path, "ab") as f:
        f.write(b"\x00")
    assert main(argv) == 1

    # Restore -> green again.
    on_disk_path.write_bytes(original_bytes)
    assert main(argv) == 0


def test_explicit_missing_lake_root_is_a_hard_fail_never_a_skip(tmp_path: Path):
    """An explicitly-passed --lake-root that does not exist must exit 1,
    not SKIP -- only the unset DEFAULT_LAKE_ROOT may SKIP. A typo'd CI path
    must not silently recreate T-03-09's inert-guardrail failure mode."""
    missing_lake_root = tmp_path / "does_not_exist"
    registry_root = tmp_path / "registry"
    registry_root.mkdir()

    exit_code = main(["--full", "--lake-root", str(missing_lake_root)])
    assert exit_code == 1


# --- WR-07 (03-REVIEW.md): zero manifests checked is never a pass ---------


def test_typod_registry_root_is_a_hard_fail(tmp_path: Path, capsys):
    """Reproduction from 03-REVIEW.md WR-07: a valid --lake-root with a
    misspelled --registry-root used to print `checked 0 manifest(s)` and
    exit 0 -- the CI fixture leg silently inert."""
    lake_root = tmp_path / "fixture_lake"
    registry_root = tmp_path / "fixture_registry"
    partition = _write_partition(lake_root, "curated/part-1.parquet", _sample_df())
    issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[partition],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    typo = tmp_path / "fixture_regsitry"
    argv = ["--full", "--lake-root", str(lake_root), "--registry-root", str(typo)]
    assert main(argv) == 1
    assert "FAIL" in capsys.readouterr().out


def test_existing_but_empty_registry_root_is_a_hard_fail(tmp_path: Path, capsys):
    lake_root = tmp_path / "lake"
    lake_root.mkdir()
    registry_root = tmp_path / "registry"
    (registry_root / "manifests").mkdir(parents=True)
    argv = [
        "--full",
        "--lake-root",
        str(lake_root),
        "--registry-root",
        str(registry_root),
    ]
    assert main(argv) == 1
    assert "0 manifest" in capsys.readouterr().out
