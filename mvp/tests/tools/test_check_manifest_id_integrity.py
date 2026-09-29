"""Tests for tools.check_manifest_id_integrity -- the CI-native manifest
self-consistency check (needs no mounted lake, 03-VERIFICATION.md gap
closure finding 1). Hermetic: tmp_path-rooted registry, never touching the
real committed 111 manifests except in the one test that scans them."""

from __future__ import annotations

import json
from pathlib import Path

from data.store import compute_manifest_id, issue_manifest
from models.frozen import FrozenLinearPredictor, write_frozen_predictor
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


def _freeze_a_predictor(registry_root: Path) -> Path:
    """Land a predictor body through the PRODUCTION writer
    (`models.frozen.write_frozen_predictor`) -- 07-10-PLAN.md Task 2,
    D-07-22. Never a hand-rolled lookalike: the point of the check is that a
    body's `manifest_id` is the self-hash over the WHOLE body, coefficients
    included, so the body under test has to carry the real key set."""
    predictor = FrozenLinearPredictor(
        model_class="sklearn.ElasticNet",
        feature_names=("imb_top", "ofi", "trade_flow"),
        coef=(1.3e-05, 0.0, -0.0),
        intercept=1.2e-06,
        normalization_manifest_id="c7" * 32,
        seed=20260925,
        code_hash="deadbeef",
        hyperparameters={"alpha": 1e-04, "l1_ratio": 0.3},
        train_target_mean=1.2e-06,
        n_rows_fitted=44_229_781,
        n_rows_dropped=224_702,
    )
    body = write_frozen_predictor(predictor, registry_root=registry_root)
    return registry_root / "predictors" / f"{body['manifest_id']}.json"


def test_main_checks_the_predictors_directory_too(tmp_path: Path, monkeypatch, capsys):
    """`predictors/` is scanned and COUNTED -- `checked 2` rather than
    `checked 1`, which is what fails if the name is ever dropped from
    REGISTRY_DIR_NAMES."""
    registry_root = tmp_path / "registry"
    _issue_fixture_manifest(registry_root)
    _freeze_a_predictor(registry_root)
    monkeypatch.setattr(
        "tools.check_manifest_id_integrity.LAKE_REGISTRY_ROOT", registry_root
    )
    assert main([]) == 0
    assert "checked 2 manifest(s)" in capsys.readouterr().out


def test_main_fails_and_names_a_hand_edited_predictor_body(
    tmp_path: Path, monkeypatch, capsys
):
    """A coefficient edited after the freeze is caught, NAMED, and the exit
    code can only have come from the predictor body.

    THE HEALTHY `manifests/` MANIFEST BESIDE IT IS LOAD-BEARING, not scenery.
    With only a tampered body in the registry, dropping `"predictors"` from
    REGISTRY_DIR_NAMES would leave `_iter_manifest_files` empty, the GLOBAL
    vacuity guard would fire, and `main()` would still return 1 -- the test
    would pass while proving nothing about predictors at all. With a real
    manifest present the scan is non-empty either way, so exit 1 has exactly
    one available cause."""
    registry_root = tmp_path / "registry"
    _issue_fixture_manifest(registry_root)
    path = _freeze_a_predictor(registry_root)

    body = json.loads(path.read_text())
    body["coef"][0] = body["coef"][0] * 2  # id now stale, filename unchanged
    path.write_text(json.dumps(body, sort_keys=True, indent=2))

    monkeypatch.setattr(
        "tools.check_manifest_id_integrity.LAKE_REGISTRY_ROOT", registry_root
    )
    assert main([]) == 1
    out = capsys.readouterr().out
    assert "checked 2 manifest(s)" in out
    assert path.name in out
    assert "body hand-edited or corrupted" in out


def test_check_manifest_file_passes_on_an_untouched_predictor_body(tmp_path: Path):
    """The positive half, at the single-file level: what `--freeze` writes
    self-verifies on both the `manifest_id` field and the filename stem."""
    registry_root = tmp_path / "registry"
    assert check_manifest_file(_freeze_a_predictor(registry_root)) is None
