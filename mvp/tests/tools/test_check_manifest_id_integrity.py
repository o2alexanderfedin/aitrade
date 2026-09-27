"""Tests for tools.check_manifest_id_integrity -- the CI-native manifest
self-consistency check (needs no mounted lake, 03-VERIFICATION.md gap
closure finding 1). Hermetic: tmp_path-rooted registry, never touching the
real committed 111 manifests except in the one test that scans them."""

from __future__ import annotations

import json
from pathlib import Path

from data.store import compute_manifest_id, issue_manifest
from tools.check_manifest_id_integrity import check_manifest_file, main


def _issue_fixture_manifest(registry_root: Path) -> Path:
    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[
            {
                "date": "2026-09-12",
                "path": "curated/part-1.parquet",
                "sha256": "deadbeef" * 8,
                "rows": 2,
                "size_bytes": 123,
                "mtime_ns": 456,
                "etime_min": 1_000,
                "etime_max": 2_000,
            }
        ],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    return (
        registry_root
        / "manifests"
        / "BTCUSDT.trade"
        / f"{manifest['manifest_id']}.json"
    )


def test_check_manifest_file_passes_on_untouched_manifest(tmp_path: Path):
    registry_root = tmp_path / "registry"
    manifest_file = _issue_fixture_manifest(registry_root)
    assert check_manifest_file(manifest_file) is None


def test_red_proof_hand_edited_body_fails_naming_the_file(tmp_path: Path):
    """RP: hand-edit a manifest's body (without touching manifest_id) ->
    check_manifest_file names the file and explains why. Restore -> green."""
    registry_root = tmp_path / "registry"
    manifest_file = _issue_fixture_manifest(registry_root)

    original_text = manifest_file.read_text()
    body = json.loads(original_text)
    body["row_count"] = body["row_count"] + 999  # hand-edit, id now stale
    manifest_file.write_text(json.dumps(body, sort_keys=True, indent=2))

    err = check_manifest_file(manifest_file)
    assert err is not None
    assert str(manifest_file) in err
    assert "manifest_id field" in err

    # Restore.
    manifest_file.write_text(original_text)
    assert check_manifest_file(manifest_file) is None


def test_red_proof_renamed_manifest_file_fails_on_filename_mismatch(tmp_path: Path):
    """RP: an untouched-body manifest filed under the WRONG filename (e.g.
    a copy/paste or manual rename) fails on the filename-stem check even
    though its own manifest_id field is internally self-consistent."""
    registry_root = tmp_path / "registry"
    manifest_file = _issue_fixture_manifest(registry_root)

    wrong_path = manifest_file.with_name("0" * 64 + ".json")
    wrong_path.write_text(manifest_file.read_text())

    err = check_manifest_file(wrong_path)
    assert err is not None
    assert "filename stem" in err

    # The original, correctly-named file is untouched and still green.
    assert check_manifest_file(manifest_file) is None


def test_main_passes_on_untouched_registry(tmp_path: Path, monkeypatch):
    registry_root = tmp_path / "registry"
    _issue_fixture_manifest(registry_root)
    monkeypatch.setattr(
        "tools.check_manifest_id_integrity.LAKE_REGISTRY_ROOT", registry_root
    )
    assert main([]) == 0


def test_main_fails_and_names_the_file_on_a_corrupted_manifest(
    tmp_path: Path, monkeypatch
):
    registry_root = tmp_path / "registry"
    manifest_file = _issue_fixture_manifest(registry_root)
    body = json.loads(manifest_file.read_text())
    body["code_hash"] = "tampered"
    manifest_file.write_text(json.dumps(body, sort_keys=True, indent=2))

    monkeypatch.setattr(
        "tools.check_manifest_id_integrity.LAKE_REGISTRY_ROOT", registry_root
    )
    assert main([]) == 1


def test_main_ignores_by_date_index_files(tmp_path: Path, monkeypatch):
    """by-date/ pointer files (`{"manifest_id": ...}`, no `partitions` body)
    must never be scanned as if they were manifests."""
    registry_root = tmp_path / "registry"
    _issue_fixture_manifest(registry_root)
    monkeypatch.setattr(
        "tools.check_manifest_id_integrity.LAKE_REGISTRY_ROOT", registry_root
    )
    by_date_files = list((registry_root / "manifests").glob("**/by-date/*.json"))
    assert by_date_files, "fixture setup should have produced a by-date index"
    assert main([]) == 0


def test_main_against_the_real_committed_111_manifests():
    """No monkeypatch: scans the real, git-committed
    mvp/data/lake_registry/manifests/ tree. Every one of the 111 real
    manifests must self-verify."""
    assert main([]) == 0


def _write_registry_manifest(registry_root: Path, dir_name: str, body: dict) -> Path:
    """Write a segment/errata-SHAPED manifest by hand into
    `registry_root/<dir_name>/<id>.json` -- 05-07-PLAN.md Task 2
    (D-05-07, 05-RESEARCH.md Q1): no `partitions` key, mirroring
    `harness.segments.issue_segment_manifest`'s own writer."""
    manifest_id = compute_manifest_id(body)
    path = registry_root / dir_name / f"{manifest_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"manifest_id": manifest_id, **body}, sort_keys=True, indent=2)
    )
    return path


def test_main_checks_segments_and_errata_directories_too(
    tmp_path: Path, monkeypatch, capsys
):
    registry_root = tmp_path / "registry"
    _issue_fixture_manifest(registry_root)
    _write_registry_manifest(
        registry_root, "segments", {"layout": "compressed_3seg", "segments": []}
    )
    _write_registry_manifest(
        registry_root, "errata", {"symbol": "BTCUSDT", "cells": []}
    )
    monkeypatch.setattr(
        "tools.check_manifest_id_integrity.LAKE_REGISTRY_ROOT", registry_root
    )
    assert main([]) == 0
    assert "checked 3 manifest(s)" in capsys.readouterr().out


def test_main_fails_and_names_a_hand_edited_segments_manifest(
    tmp_path: Path, monkeypatch
):
    registry_root = tmp_path / "registry"
    seg_path = _write_registry_manifest(
        registry_root, "segments", {"layout": "compressed_3seg", "segments": []}
    )
    body = json.loads(seg_path.read_text())
    body["layout"] = "tampered"  # id now stale, filename unchanged
    seg_path.write_text(json.dumps(body, sort_keys=True, indent=2))

    monkeypatch.setattr(
        "tools.check_manifest_id_integrity.LAKE_REGISTRY_ROOT", registry_root
    )
    assert main([]) == 1


def test_main_fails_and_names_a_hand_edited_errata_manifest(
    tmp_path: Path, monkeypatch
):
    registry_root = tmp_path / "registry"
    err_path = _write_registry_manifest(
        registry_root, "errata", {"symbol": "BTCUSDT", "cells": []}
    )
    body = json.loads(err_path.read_text())
    body["symbol"] = "ETHUSDT"  # id now stale, filename unchanged
    err_path.write_text(json.dumps(body, sort_keys=True, indent=2))

    monkeypatch.setattr(
        "tools.check_manifest_id_integrity.LAKE_REGISTRY_ROOT", registry_root
    )
    assert main([]) == 1


def test_main_fails_when_no_manifests_are_found(tmp_path: Path, monkeypatch, capsys):
    """03-REVIEW.md WR-07: a missing or empty `manifests/` directory used to
    print `checked 0 manifest(s)` and exit 0. The committed registry is known
    to be non-empty, so checking nothing is a failure."""
    monkeypatch.setattr(
        "tools.check_manifest_id_integrity.LAKE_REGISTRY_ROOT", tmp_path / "missing"
    )
    assert main([]) == 1
    empty = tmp_path / "empty"
    (empty / "manifests").mkdir(parents=True)
    monkeypatch.setattr("tools.check_manifest_id_integrity.LAKE_REGISTRY_ROOT", empty)
    assert main([]) == 1
    assert "FAIL" in capsys.readouterr().out
