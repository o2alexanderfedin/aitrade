"""Tests for harness.segments: the 5seg-only segment manifest writer
(D-05-07..10)."""

from __future__ import annotations

import pytest
from data.time_ns import NS_PER_SECOND
from harness.purge_embargo import FOLD_EMBARGO_NS, PURGE_HORIZON_NS
from harness.segments import (
    FIVE_SEG_NAMES,
    FIVE_SEG_ROLES,
    issue_segment_manifest,
    read_segment_manifest,
    segment_manifest_path,
)

#: Each of the 5seg layout's segments is this wide, contiguous with its
#: neighbours -- REAL magnitude (900 s), never a microsecond span, so a
#: purge zone genuinely reaches into a declared segment's own range.
SEGMENT_WIDTH_NS = 900 * NS_PER_SECOND

ADMISSION_DEFAULT = {
    "policy": "stale_book",
    "max_age_ns": None,
    "exclude_undefined_age": True,
    "counts": {},
}


def five_seg_segments(start_ns: int = 0) -> list[dict]:
    boundaries = [start_ns + i * SEGMENT_WIDTH_NS for i in range(6)]
    return [
        {
            "name": name,
            "role": role,
            "start_ns": boundaries[i],
            "end_ns": boundaries[i + 1],
        }
        for i, (name, role) in enumerate(zip(FIVE_SEG_NAMES, FIVE_SEG_ROLES))
    ]


def _issue(registry_root, **overrides):
    kwargs = dict(
        layout="5seg",
        segments=five_seg_segments(),
        upstream_feature_manifest_ids=["deadbeef" * 8],
        admission=ADMISSION_DEFAULT,
        errata_id=None,
        budget_allowance=1,
        symbol="BTCUSDT",
        version=1,
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    kwargs.update(overrides)
    return issue_segment_manifest(**kwargs)


def test_issue_segment_manifest_omits_partitions_key(registry_root):
    manifest = _issue(registry_root)
    assert "partitions" not in manifest


def test_issue_segment_manifest_records_every_d05_09_field(registry_root):
    manifest = _issue(registry_root)
    assert [s["name"] for s in manifest["segments"]] == list(FIVE_SEG_NAMES)
    assert len(manifest["segments"]) == 5
    assert manifest["purge_ns"] == PURGE_HORIZON_NS
    assert manifest["embargo_ns"] == FOLD_EMBARGO_NS
    assert manifest["upstream_feature_manifest_ids"] == ["deadbeef" * 8]
    assert manifest["admission"] == ADMISSION_DEFAULT
    assert manifest["errata_id"] is None
    assert manifest["budget_allowance"] == 1
    assert manifest["symbol"] == "BTCUSDT"
    assert manifest["version"] == 1
    assert manifest["code_hash"] == "deadbeef"
    assert "manifest_id" in manifest


def test_compressed_3seg_not_yet_implemented(registry_root):
    with pytest.raises(NotImplementedError, match="05-02-PLAN"):
        issue_segment_manifest(
            layout="compressed_3seg",
            segments=[],
            upstream_feature_manifest_ids=[],
            admission=ADMISSION_DEFAULT,
            errata_id=None,
            budget_allowance=0,
            symbol="BTCUSDT",
            version=1,
            code_hash="deadbeef",
            registry_root=registry_root,
        )


def test_5seg_refuses_wrong_names_or_roles(registry_root):
    bad = five_seg_segments()
    bad[0]["role"] = "val"
    with pytest.raises(ValueError, match="requires segments named"):
        _issue(registry_root, segments=bad)


def test_read_segment_manifest_self_hash_verified(registry_root):
    manifest = _issue(registry_root)
    reread = read_segment_manifest(registry_root, manifest["manifest_id"])
    assert reread == manifest

    path = segment_manifest_path(registry_root, manifest["manifest_id"])
    tampered = path.read_text().replace('"BTCUSDT"', '"ETHUSDT"')
    path.write_text(tampered)
    with pytest.raises(ValueError, match="hash mismatch"):
        read_segment_manifest(registry_root, manifest["manifest_id"])
