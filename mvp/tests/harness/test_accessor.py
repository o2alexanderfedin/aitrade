"""Tests for harness.accessor: the ordered-gates accessor (D-05-11..15) --
real purge/embargo exclusion, held_out refusal, look accounting for `val`
AND `oof_block` roles."""

from __future__ import annotations

import json

import pytest
from data.store import compute_manifest_id
from data.time_ns import NS_PER_SECOND
from harness.accessor import materialize
from harness.budget import look_count
from harness.purge_embargo import effective_train_intervals
from harness.segments import (
    FIVE_SEG_NAMES,
    FIVE_SEG_ROLES,
    issue_segment_manifest,
    segment_manifest_path,
)
from tests.fixtures.harness_span import build_span_partition

SEGMENT_WIDTH_NS = 900 * NS_PER_SECOND

ADMISSION_DEFAULT = {
    "policy": "stale_book",
    "max_age_ns": None,
    "exclude_undefined_age": True,
    "counts": {},
}

RUN_TAGS = {
    "code_hash": "a" * 40,
    "data_hash": "none",
    "seed": "0",
    "env_hash": "b" * 64,
    "model_class": "none (harness probe, no model in this phase)",
}


def _five_seg_segments(start_ns: int = 0) -> list[dict]:
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


def _build_fixture(lake_root, registry_root, *, rows: int = 4_500):
    span = build_span_partition(
        lake_root,
        registry_root,
        date="2026-09-13",
        start_ns=0,
        step_ns=NS_PER_SECOND,
        rows=rows,
    )
    manifest = issue_segment_manifest(
        layout="5seg",
        segments=_five_seg_segments(),
        upstream_feature_manifest_ids=[span["manifest_id"]],
        admission=ADMISSION_DEFAULT,
        errata_id=None,
        budget_allowance=1,
        symbol="BTCUSDT",
        version=1,
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    return manifest, span


def test_materialize_refuses_held_out_unconditionally(
    lake_root, registry_root, tracking_root
):
    manifest, _span = _build_fixture(lake_root, registry_root)
    with pytest.raises(ValueError, match="held_out"):
        materialize(
            manifest["manifest_id"],
            "held_out",
            registry_root=registry_root,
            lake_root=lake_root,
            tracking_root=str(tracking_root),
            run_tags=dict(RUN_TAGS),
        )


def test_materialize_filters_by_half_open_time_window(
    lake_root, registry_root, tracking_root
):
    manifest, _span = _build_fixture(lake_root, registry_root)
    entry = next(s for s in manifest["segments"] if s["name"] == "val_s1")
    df = materialize(
        manifest["manifest_id"],
        "val_s1",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    assert df.height > 0
    etimes = df["etime"].to_list()
    assert all(entry["start_ns"] <= e < entry["end_ns"] for e in etimes)
    assert entry["end_ns"] not in etimes


def test_materialize_excludes_purge_and_embargo_zones_for_a_train_entry(
    lake_root, registry_root, tracking_root
):
    manifest, _span = _build_fixture(lake_root, registry_root)
    train_entry = next(s for s in manifest["segments"] if s["name"] == "train_s1")
    others = [
        s
        for s in manifest["segments"]
        if s["name"] != "train_s1" and s["role"] in ("val", "held_out", "oof_block")
    ]
    expected_intervals = effective_train_intervals(
        train_entry["start_ns"],
        train_entry["end_ns"],
        others,
        purge_ns=manifest["purge_ns"],
        embargo_ns=manifest["embargo_ns"],
    )
    assert expected_intervals, "fixture geometry left train_s1 fully purged -- vacuous"

    df = materialize(
        manifest["manifest_id"],
        "train_s1",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    assert df.height > 0
    for etime in df["etime"].to_list():
        assert any(start <= etime < end for start, end in expected_intervals), (
            f"row at etime={etime} survives materialize() but falls outside "
            f"every surviving interval {expected_intervals} -- purge/embargo "
            "did not exclude it"
        )

    # Anti-vacuity: the naive half-open segment filter alone would have kept
    # strictly more than purge/embargo actually left standing.
    naive_kept_ns = train_entry["end_ns"] - train_entry["start_ns"]
    purged_kept_ns = sum(end - start for start, end in expected_intervals)
    assert purged_kept_ns < naive_kept_ns


def test_materialize_counts_val_and_oof_block_but_not_train_as_a_look(
    lake_root, registry_root, tracking_root
):
    manifest, _span = _build_fixture(lake_root, registry_root)
    mid = manifest["manifest_id"]

    materialize(
        mid,
        "train_s1",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    assert look_count(mid, "train_s1", tracking_root=str(tracking_root)) == 0

    materialize(
        mid,
        "val_s1",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    assert look_count(mid, "val_s1", tracking_root=str(tracking_root)) == 1

    # A SYNTHETIC manifest entry with role="oof_block", hand-built for this
    # test since this plan's own 5seg layout has none (checker iteration 1
    # blocker 2) -- proves the look-accounting gate is keyed on ROLE, not on
    # layout, so a later compressed_3seg layout exercises the same path.
    oof_name = "oof_block_synthetic"
    first_segment = manifest["segments"][0]
    oof_entry = {
        "name": oof_name,
        "role": "oof_block",
        "start_ns": first_segment["start_ns"],
        "end_ns": first_segment["start_ns"] + SEGMENT_WIDTH_NS,
    }
    synthetic_body = {k: v for k, v in manifest.items() if k != "manifest_id"}
    synthetic_body["segments"] = [*manifest["segments"], oof_entry]
    synthetic_id = compute_manifest_id(synthetic_body)
    synthetic_manifest = {"manifest_id": synthetic_id, **synthetic_body}
    path = segment_manifest_path(registry_root, synthetic_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(synthetic_manifest, sort_keys=True, indent=2))

    materialize(
        synthetic_id,
        oof_name,
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    assert look_count(synthetic_id, oof_name, tracking_root=str(tracking_root)) == 1


def test_the_walking_skeleton_end_to_end(lake_root, registry_root, tracking_root):
    manifest, _span = _build_fixture(lake_root, registry_root)
    mid = manifest["manifest_id"]

    train_df = materialize(
        mid,
        "train_s1",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    assert train_df.height > 0

    val_df = materialize(
        mid,
        "val_s1",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    assert val_df.height > 0

    assert look_count(mid, "val_s1", tracking_root=str(tracking_root)) == 1
