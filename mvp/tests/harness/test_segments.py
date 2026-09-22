"""Tests for harness.segments: D-05-02's geometric refusals, the
starvation refusal, and both layouts' derived purge/embargo fields
(D-05-07..10). `compressed_3seg` itself is Task 2's job
(tests/harness/test_kfold.py); this file covers Task 1: the 5seg layout's
real D-05-02 validation and the shared `_validate_segments`/
`_derive_purge_embargo_fields` pipeline Task 2 reuses unchanged.
"""

from __future__ import annotations

import inspect

import pytest
from data.time_ns import NS_PER_SECOND
from harness.purge_embargo import (
    FOLD_EMBARGO_NS,
    PURGE_HORIZON_NS,
    effective_train_intervals,
)
from harness.segments import (
    FIVE_SEG_NAMES,
    FIVE_SEG_ROLES,
    _covered_range,
    _train_effective_intervals,
    _validate_segments,
    issue_segment_manifest,
    read_segment_manifest,
    segment_manifest_path,
)
from tests.fixtures.harness_span import build_span_partition

S = NS_PER_SECOND

#: Widened from P1's uniform 900s (05-02 coordinator decision, Option A):
#: a UNIFORM 900s width starves `train_s2` under the real 600s purge
#: horizon -- `val_s1`'s trailing embargo band ∪ `val_s2`'s leading purge
#: band together cover [1800s, 2700s) exactly (verified against the real
#: `effective_train_intervals` BEFORE this change, not assumed -- see
#: `test_refuses_a_starved_train_entry`, which reuses that exact starved
#: geometry as its own fixture). 1800s train / 600s val leaves every train
#: entry's effective range non-empty (`test_default_5seg_fixture_covers_
#: and_is_non_starved` proves it) while still exercising the naive-filter-
#: vs-purge property P1's own accessor tests rely on (purge removes a
#: real, non-trivial fraction of each train entry's declared range).
TRAIN_WIDTH_NS = 1800 * S
VAL_WIDTH_NS = 600 * S
HELD_OUT_WIDTH_NS = 600 * S
SEGMENT_WIDTHS_NS = (
    TRAIN_WIDTH_NS,
    VAL_WIDTH_NS,
    TRAIN_WIDTH_NS,
    VAL_WIDTH_NS,
    HELD_OUT_WIDTH_NS,
)

ADMISSION_DEFAULT = {
    "policy": "stale_book",
    "max_age_ns": None,
    "exclude_undefined_age": True,
    "counts": {},
}


def five_seg_segments(start_ns: int = 0) -> list[dict]:
    boundaries = [start_ns]
    for width in SEGMENT_WIDTHS_NS:
        boundaries.append(boundaries[-1] + width)
    return [
        {
            "name": name,
            "role": role,
            "start_ns": boundaries[i],
            "end_ns": boundaries[i + 1],
        }
        for i, (name, role) in enumerate(zip(FIVE_SEG_NAMES, FIVE_SEG_ROLES))
    ]


def _other_entries(segments: list[dict], entry_name: str) -> list[dict]:
    return [
        s
        for s in segments
        if s["name"] != entry_name and s["role"] in ("val", "held_out")
    ]


def _build_fixture(
    lake_root, registry_root, *, rows: int = 6_000, segments=None, **overrides
):
    """Real span (through the actual writer/manifest-issuer path) plus a
    real 5seg issuance -- `segments` defaults to the widened, non-starved
    geometry (`five_seg_segments()`) but may be overridden with a
    deliberately malformed layout for a negative test."""
    span = build_span_partition(
        lake_root,
        registry_root,
        date="2026-09-13",
        start_ns=0,
        step_ns=S,
        rows=rows,
    )
    kwargs = dict(
        layout="5seg",
        segments=segments if segments is not None else five_seg_segments(),
        upstream_feature_manifest_ids=[span["manifest_id"]],
        admission=ADMISSION_DEFAULT,
        errata_id=None,
        budget_allowance=1,
        symbol="BTCUSDT",
        version=1,
        code_hash="deadbeef",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    kwargs.update(overrides)
    manifest = issue_segment_manifest(**kwargs)
    return manifest, span


# --------------------------------------------------------------------------
# Fixture integrity (anti-vacuity, asserted before anything relies on it)
# --------------------------------------------------------------------------


def test_default_5seg_fixture_covers_and_is_non_starved(lake_root, registry_root):
    """States the property the widened geometry now needs: the span
    covers every train/val segment, and every train entry's real
    `effective_train_intervals` (against its own val/held_out neighbours)
    is non-empty -- the same check `_derive_purge_embargo_fields` performs
    internally, run here directly against the fixture geometry so a
    future width change that silently re-starves a train entry fails this
    test first, not a downstream one."""
    span = build_span_partition(
        lake_root, registry_root, date="2026-09-13", start_ns=0, step_ns=S, rows=6_000
    )
    segments = five_seg_segments()
    non_held_out = [s for s in segments if s["role"] != "held_out"]
    assert span["etime_min"] <= non_held_out[0]["start_ns"]
    assert span["etime_max"] >= non_held_out[-1]["end_ns"]

    for entry in (s for s in segments if s["role"] == "train"):
        intervals = effective_train_intervals(
            entry["start_ns"],
            entry["end_ns"],
            _other_entries(segments, entry["name"]),
            purge_ns=PURGE_HORIZON_NS,
            embargo_ns=FOLD_EMBARGO_NS,
        )
        assert intervals, f"{entry['name']} is starved under the widened geometry"


# --------------------------------------------------------------------------
# D-05-02 geometric refusals (direct against _validate_segments -- pure
# geometry, no lake needed)
# --------------------------------------------------------------------------


def test_refuses_overlapping_segments():
    segments = five_seg_segments()
    val_s1 = next(s for s in segments if s["name"] == "val_s1")
    val_s1["end_ns"] += 1  # now overlaps train_s2's declared start by 1ns
    with pytest.raises(ValueError, match=r"val_s1.*train_s2.*overlap"):
        _validate_segments(segments, covered_start_ns=0, covered_end_ns=10_000 * S)


def test_refuses_out_of_order_segments():
    segments = five_seg_segments()
    train_s1 = next(s for s in segments if s["name"] == "train_s1")
    train_s2 = next(s for s in segments if s["name"] == "train_s2")
    train_s2["start_ns"] = (
        train_s1["end_ns"] - 1
    )  # train_s2 starts before train_s1 ends
    with pytest.raises(ValueError, match=r"train_s1.*train_s2.*overlap"):
        _validate_segments(segments, covered_start_ns=0, covered_end_ns=10_000 * S)


def test_refuses_a_segment_past_covered_range():
    segments = five_seg_segments()
    # train_s2 ends at 4200s; a covered range that stops at 3000s cannot
    # cover it.
    with pytest.raises(ValueError, match=r"train_s2.*exceeds the covered"):
        _validate_segments(segments, covered_start_ns=0, covered_end_ns=3_000 * S)


def test_held_out_is_exempt_from_the_upper_coverage_bound():
    segments = five_seg_segments()
    held_out = next(s for s in segments if s["role"] == "held_out")
    assert held_out["end_ns"] == 5_400 * S  # sanity on the fixture geometry
    # covered_end_ns covers every train/val entry (largest is val_s2 @
    # 4800s) but NOT held_out's own end_ns (5400s) -- accepted anyway
    # (D-05-16): held_out sits forward in time, open-ended.
    _validate_segments(segments, covered_start_ns=0, covered_end_ns=4_800 * S)


def test_refuses_val_before_its_train():
    segments = five_seg_segments()
    # Move val_s2 to sit chronologically BEFORE train_s2 (non-overlapping
    # with anything -- squeezed in right after val_s1), while it is still
    # DECLARED after train_s2 in the list, i.e. it precedes the train
    # entry it is meant to follow.
    val_s1 = next(s for s in segments if s["name"] == "val_s1")
    val_s2 = next(s for s in segments if s["name"] == "val_s2")
    train_s2 = next(s for s in segments if s["name"] == "train_s2")
    held_out = next(s for s in segments if s["role"] == "held_out")
    width = val_s2["end_ns"] - val_s2["start_ns"]
    val_s2["start_ns"] = val_s1["end_ns"]
    val_s2["end_ns"] = val_s1["end_ns"] + width
    train_s2["start_ns"] = val_s2["end_ns"]
    train_s2["end_ns"] = train_s2["start_ns"] + TRAIN_WIDTH_NS
    held_out["start_ns"] = train_s2["end_ns"]
    held_out["end_ns"] = held_out["start_ns"] + HELD_OUT_WIDTH_NS
    with pytest.raises(ValueError, match="does not match chronological order"):
        _validate_segments(segments, covered_start_ns=0, covered_end_ns=10_000 * S)


def test_no_auto_slicing_by_fraction():
    sig = inspect.signature(issue_segment_manifest)
    for name in sig.parameters:
        assert "fraction" not in name.lower()
        assert "percent" not in name.lower()


def test_refuses_a_starved_train_entry():
    """P1's own original uniform-900s geometry, reused verbatim as the
    starved fixture (advisor-verified against the real
    `effective_train_intervals`, not assumed): `train_s2`'s real
    purge/embargo bands from `val_s1`/`val_s2`/`held_out` cover its entire
    declared range."""
    width = 900 * S
    boundaries = [i * width for i in range(6)]
    segments = [
        {
            "name": name,
            "role": role,
            "start_ns": boundaries[i],
            "end_ns": boundaries[i + 1],
        }
        for i, (name, role) in enumerate(zip(FIVE_SEG_NAMES, FIVE_SEG_ROLES))
    ]
    with pytest.raises(ValueError, match=r"train_s2.*is starved"):
        _train_effective_intervals(segments)


# --------------------------------------------------------------------------
# D-05-09 derived fields, measured against real upstream partitions
# --------------------------------------------------------------------------


def test_records_effective_intervals_and_derived_purge_embargo(
    lake_root, registry_root
):
    manifest, _span = _build_fixture(lake_root, registry_root)
    segments = manifest["segments"]
    train_names = [s["name"] for s in segments if s["role"] == "train"]
    assert train_names == ["train_s1", "train_s2"]

    for entry in (s for s in segments if s["role"] == "train"):
        expected = effective_train_intervals(
            entry["start_ns"],
            entry["end_ns"],
            _other_entries(segments, entry["name"]),
            purge_ns=PURGE_HORIZON_NS,
            embargo_ns=FOLD_EMBARGO_NS,
        )
        assert manifest["effective_intervals"][entry["name"]] == [
            [start, end] for start, end in expected
        ]

    assert manifest["purge_ns"] == PURGE_HORIZON_NS
    assert manifest["embargo_ns"] == FOLD_EMBARGO_NS


def test_records_real_purged_and_embargoed_row_counts(lake_root, registry_root):
    manifest, span = _build_fixture(lake_root, registry_root)
    segments = manifest["segments"]
    all_etimes = list(range(span["etime_min"], span["etime_max"] + 1, S))

    for entry in (s for s in segments if s["role"] == "train"):
        others = _other_entries(segments, entry["name"])
        entry_etimes = [
            e for e in all_etimes if entry["start_ns"] <= e < entry["end_ns"]
        ]
        purged = 0
        embargoed = 0
        for etime in entry_etimes:
            if any(
                (o["start_ns"] - PURGE_HORIZON_NS)
                <= etime
                < (o["end_ns"] + PURGE_HORIZON_NS)
                for o in others
            ):
                purged += 1
                continue
            if any(
                (o["end_ns"] + PURGE_HORIZON_NS)
                <= etime
                < (o["end_ns"] + PURGE_HORIZON_NS + FOLD_EMBARGO_NS)
                for o in others
            ):
                embargoed += 1
        assert manifest["purged_row_count"][entry["name"]] == purged
        assert manifest["embargoed_row_count"][entry["name"]] == embargoed

    # Anti-vacuity, on train_s2 specifically: it has TWO val neighbours
    # (val_s1 on its left, val_s2 on its right) whose bands leave both a
    # genuine purged AND a genuine embargoed row -- neither zero, neither
    # the full row count -- proving the fixture actually exercises both
    # zones, not just one.
    train_s2_rows = 4_200 - 2_400  # entry width in seconds
    assert 0 < manifest["purged_row_count"]["train_s2"] < train_s2_rows
    assert 0 < manifest["embargoed_row_count"]["train_s2"] < train_s2_rows


# --------------------------------------------------------------------------
# P1's own tests, adapted to the new required `lake_root` parameter and
# the widened, real, non-starved geometry
# --------------------------------------------------------------------------


def test_issue_segment_manifest_omits_partitions_key(lake_root, registry_root):
    manifest, _span = _build_fixture(lake_root, registry_root)
    assert "partitions" not in manifest


def test_issue_segment_manifest_records_every_d05_09_field(lake_root, registry_root):
    manifest, span = _build_fixture(lake_root, registry_root)
    assert [s["name"] for s in manifest["segments"]] == list(FIVE_SEG_NAMES)
    assert len(manifest["segments"]) == 5
    assert manifest["purge_ns"] == PURGE_HORIZON_NS
    assert manifest["embargo_ns"] == FOLD_EMBARGO_NS
    assert manifest["upstream_feature_manifest_ids"] == [span["manifest_id"]]
    assert manifest["admission"] == ADMISSION_DEFAULT
    assert manifest["errata_id"] is None
    assert manifest["budget_allowance"] == 1
    assert manifest["symbol"] == "BTCUSDT"
    assert manifest["version"] == 1
    assert manifest["code_hash"] == "deadbeef"
    assert "manifest_id" in manifest
    for name in ("train_s1", "train_s2"):
        assert name in manifest["effective_intervals"]
        assert name in manifest["purged_row_count"]
        assert name in manifest["embargoed_row_count"]


def test_compressed_3seg_not_yet_implemented(lake_root, registry_root):
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
            lake_root=lake_root,
        )


def test_5seg_refuses_wrong_names_or_roles(lake_root, registry_root):
    bad = five_seg_segments()
    bad[0]["role"] = "val"
    with pytest.raises(ValueError, match="requires segments named"):
        _build_fixture(lake_root, registry_root, segments=bad)


def test_read_segment_manifest_self_hash_verified(lake_root, registry_root):
    manifest, _span = _build_fixture(lake_root, registry_root)
    reread = read_segment_manifest(registry_root, manifest["manifest_id"])
    assert reread == manifest

    path = segment_manifest_path(registry_root, manifest["manifest_id"])
    tampered = path.read_text().replace('"BTCUSDT"', '"ETHUSDT"')
    path.write_text(tampered)
    with pytest.raises(ValueError, match="hash mismatch"):
        read_segment_manifest(registry_root, manifest["manifest_id"])


def test_covered_range_refuses_empty_upstream_ids(lake_root, registry_root):
    with pytest.raises(ValueError, match="upstream_feature_manifest_ids is empty"):
        _covered_range(
            [], symbol="BTCUSDT", registry_root=registry_root, lake_root=lake_root
        )
