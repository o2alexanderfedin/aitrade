"""Tests for harness.accessor: the ordered-gates accessor (D-05-11..15) --
real purge/embargo exclusion, held_out refusal, look accounting for `val`
AND `oof_block` roles."""

from __future__ import annotations

import json

import polars as pl
import pytest
from data.store import compute_manifest_id
from data.time_ns import NS_PER_SECOND
from features.tier import (
    FEATURE_COLUMNS,
    FEATURE_ROW_SCHEMA,
    FEATURE_SCHEMA_VERSION,
    LABEL_COLUMNS,
    issue_feature_manifest,
    write_feature_partition,
)
from harness import row_admission
from harness.accessor import materialize
from harness.budget import look_count
from harness.purge_embargo import (
    FOLD_EMBARGO_NS,
    PURGE_HORIZON_NS,
    effective_train_intervals,
)
from harness.segments import (
    FIVE_SEG_NAMES,
    FIVE_SEG_ROLES,
    issue_segment_manifest,
    segment_manifest_path,
)
from tests.fixtures.feature_build import write_dq_report
from tests.fixtures.harness_span import build_span_partition

#: Kept for the synthetic oof_block entry in
#: `test_materialize_counts_val_and_oof_block_but_not_train_as_a_look`
#: below -- 900s is still a valid sub-range of `train_s1`'s widened 1800s
#: span, so that test's own geometry needs no change.
SEGMENT_WIDTH_NS = 900 * NS_PER_SECOND

#: 05-02 (coordinator decision, Option A): widened from P1's uniform 900s.
#: A uniform 900s width starves `train_s2` under the real 600s purge
#: horizon -- `val_s1`'s trailing embargo band ∪ `val_s2`'s leading purge
#: band together cover [1800s, 2700s) exactly, verified against the real
#: `effective_train_intervals` before this change, not assumed (05-02-
#: PLAN.md Task 1 added a starvation refusal at issuance that fires on
#: exactly that geometry). 1800s train / 600s val leaves every train
#: entry's own effective range non-empty (asserted directly below, in the
#: fixture itself) while still exercising the naive-filter-vs-purge
#: property this file's own tests rely on.
TRAIN_WIDTH_NS = 1800 * NS_PER_SECOND
VAL_WIDTH_NS = 600 * NS_PER_SECOND
HELD_OUT_WIDTH_NS = 600 * NS_PER_SECOND
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

RUN_TAGS = {
    "code_hash": "a" * 40,
    "data_hash": "none",
    "seed": "0",
    "env_hash": "b" * 64,
    "model_class": "none (harness probe, no model in this phase)",
}


def _five_seg_segments(start_ns: int = 0) -> list[dict]:
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


def _build_fixture(lake_root, registry_root, tracking_root, *, rows: int = 6_000):
    span = build_span_partition(
        lake_root,
        registry_root,
        date="2026-09-13",
        start_ns=0,
        step_ns=NS_PER_SECOND,
        rows=rows,
    )
    segments = _five_seg_segments()
    # Anti-vacuity (absolute rule): the fixture's own physical span must be
    # asserted to actually cover all five declared segments BEFORE any
    # behavioural test relies on it -- otherwise a too-short `rows` would
    # still pass every `df.height > 0` check while several segments
    # silently receive zero rows.
    assert span["etime_min"] <= segments[0]["start_ns"], (
        f"fixture span starts at {span['etime_min']} but train_s1 starts at "
        f"{segments[0]['start_ns']} -- the fixture does not cover the first segment"
    )
    assert span["etime_max"] >= segments[-1]["end_ns"] - NS_PER_SECOND, (
        f"fixture span ends at {span['etime_max']} but held_out ends at "
        f"{segments[-1]['end_ns']} -- the fixture does not cover the last segment"
    )
    # 05-02 Task 1 (coordinator decision, Option A): the widened geometry's
    # OWN non-starvation, asserted in the fixture itself -- every train
    # entry's real `effective_train_intervals` against its own val/held_out
    # neighbours must be non-empty, or `issue_segment_manifest` below would
    # refuse the whole layout as starved before any test in this file ever
    # ran.
    for entry in (s for s in segments if s["role"] == "train"):
        others = [
            other
            for other in segments
            if other["name"] != entry["name"] and other["role"] in ("val", "held_out")
        ]
        intervals = effective_train_intervals(
            entry["start_ns"],
            entry["end_ns"],
            others,
            purge_ns=PURGE_HORIZON_NS,
            embargo_ns=FOLD_EMBARGO_NS,
        )
        assert intervals, f"{entry['name']} is starved under this fixture's geometry"

    manifest = issue_segment_manifest(
        layout="5seg",
        segments=segments,
        upstream_feature_manifest_ids=[span["manifest_id"]],
        admission=ADMISSION_DEFAULT,
        errata_id=None,
        budget_allowance=1,
        symbol="BTCUSDT",
        version=1,
        code_hash="deadbeef",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
    )
    return manifest, span


def test_materialize_refuses_held_out_unconditionally(
    lake_root, registry_root, tracking_root
):
    manifest, _span = _build_fixture(lake_root, registry_root, tracking_root)
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
    manifest, _span = _build_fixture(lake_root, registry_root, tracking_root)
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
    manifest, _span = _build_fixture(lake_root, registry_root, tracking_root)
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
    manifest, _span = _build_fixture(lake_root, registry_root, tracking_root)
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
    manifest, _span = _build_fixture(lake_root, registry_root, tracking_root)
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


# --------------------------------------------------------------------------
# 05-04-PLAN.md Task 2: real row-admission (D-05-21) + errata (D-05-20)
# gates, wired into gate 6 for val/oof_block roles only.
# --------------------------------------------------------------------------

#: `decision_seq % ADMISSION_QUOTE_PERIOD == 0` is a quote (rank 0); every
#: other row is a trade, 1s apart -- one full quote-to-quote cycle grows
#: the stale-book age from 0s to (ADMISSION_QUOTE_PERIOD - 1)s, crossing
#: the DECIDED 5s threshold for the LAST row of every cycle (age 6s).
#: Unlike `build_span_partition` (every row a fresh quote), this fixture
#: has REAL stale rows -- the absolute rule the admission gate must be
#: shown against.
ADMISSION_QUOTE_PERIOD = 7
ADMISSION_ROWS = 70


def _build_admission_errata_fixture(
    lake_root, registry_root, *, date: str = "2026-09-13"
):
    """A small, real feature partition whose `decision_source_rank`
    genuinely alternates quote/trade (never all-rank-0), spaced 1s apart,
    so the stale-book age crosses the decided 5s threshold once per
    7-second cycle. `ret_1s_mid`/`ret_10s_mid` are never `0.0` by
    construction, so a masked-to-null cell is unambiguous against its
    un-masked neighbours."""
    symbol = "BTCUSDT"
    etimes = [i * NS_PER_SECOND for i in range(ADMISSION_ROWS)]
    ranks = [0 if i % ADMISSION_QUOTE_PERIOD == 0 else 1 for i in range(ADMISSION_ROWS)]

    columns: dict[str, list] = {
        "etime": etimes,
        "decision_source_rank": ranks,
        "decision_seq": list(range(ADMISSION_ROWS)),
    }
    for name in FEATURE_COLUMNS:
        columns[name] = [float(i % 97) for i in range(ADMISSION_ROWS)]
    for name in LABEL_COLUMNS:
        columns[name] = [0.0001 * (i + 1) for i in range(ADMISSION_ROWS)]
    columns["warmup"] = [False] * ADMISSION_ROWS
    columns["post_gap_warmup"] = [False] * ADMISSION_ROWS
    columns["schema_version"] = [FEATURE_SCHEMA_VERSION] * ADMISSION_ROWS
    df = pl.DataFrame(columns, schema=FEATURE_ROW_SCHEMA)

    partition_entry = write_feature_partition(
        df, lake_root=lake_root, symbol=symbol, date=date, registry_root=registry_root
    )
    manifest = issue_feature_manifest(
        symbol=symbol,
        date=date,
        partition_entry=partition_entry,
        curated_manifests=[],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    write_dq_report(
        lake_root, date, [("features", manifest["manifest_id"], "ok")], symbol=symbol
    )
    return {
        "manifest_id": manifest["manifest_id"],
        "rows": ADMISSION_ROWS,
        "etimes": etimes,
        "etime_max": partition_entry["etime_max"],
    }


def _write_probe_manifest(registry_root, partition, *, admission: dict) -> dict:
    """A minimal, HAND-BUILT segment manifest (one `val` entry spanning
    the whole probe partition), written directly via
    `segment_manifest_path` -- the same pattern
    `test_materialize_counts_val_and_oof_block_but_not_train_as_a_look`
    above already establishes for a synthetic manifest, used here because
    `issue_segment_manifest`'s 5seg/compressed_3seg shape and purge/
    embargo derivation are irrelevant to what this test exercises."""
    entry = {
        "name": "val_probe",
        "role": "val",
        "start_ns": 0,
        "end_ns": partition["etime_max"] + 1,
    }
    body = {
        "layout": "ad_hoc_probe",
        "segments": [entry],
        "upstream_feature_manifest_ids": [partition["manifest_id"]],
        "admission": admission,
        "errata_id": None,
        "budget_allowance": 10,
        "symbol": "BTCUSDT",
        "version": 1,
        "code_hash": "deadbeef",
        "purge_ns": 0,
        "embargo_ns": 0,
    }
    manifest_id = compute_manifest_id(body)
    manifest = {"manifest_id": manifest_id, **body}
    path = segment_manifest_path(registry_root, manifest_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2))
    return manifest


def test_accessor_applies_admission_and_errata_gates(
    lake_root, registry_root, tracking_root
):
    partition = _build_admission_errata_fixture(lake_root, registry_root)
    admission = {
        "policy": "stale_book",
        "max_age_ns": row_admission.STALE_BOOK_MAX_AGE_NS,
        "exclude_undefined_age": True,
        "counts": {},
    }
    manifest = _write_probe_manifest(registry_root, partition, admission=admission)

    excluded_seqs = {
        i
        for i in range(partition["rows"])
        if i % ADMISSION_QUOTE_PERIOD == ADMISSION_QUOTE_PERIOD - 1
    }
    assert excluded_seqs, "fixture must have real stale rows -- vacuous otherwise"

    masked_seq = 1
    assert masked_seq not in excluded_seqs
    masked_etime = partition["etimes"][masked_seq]
    errata_cells = [
        {
            "date": "2026-09-13",
            "etime": masked_etime,
            "decision_seq": masked_seq,
            "label_column": "ret_1s_mid",
        }
    ]

    df = materialize(
        manifest["manifest_id"],
        "val_probe",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
        errata_cells=errata_cells,
    )

    # Admission (D-05-21): exactly the stale (age > 5s) rows are gone --
    # a REAL row-count change, not a no-op pass-through.
    assert df.height == partition["rows"] - len(excluded_seqs)
    kept_seqs = set(df["decision_seq"].to_list())
    assert kept_seqs.isdisjoint(excluded_seqs)
    assert kept_seqs == set(range(partition["rows"])) - excluded_seqs

    # Errata (D-05-20): exactly the named cell is null; the same row's
    # OTHER label column, and every other row's ret_1s_mid, survive
    # untouched.
    masked_row = df.filter(pl.col("decision_seq") == masked_seq)
    assert masked_row["ret_1s_mid"].to_list() == [None]
    assert masked_row["ret_10s_mid"].to_list() == [0.0001 * (masked_seq + 1)]

    other_row = df.filter(pl.col("decision_seq") == 2)
    assert other_row["ret_1s_mid"].to_list() == [0.0001 * (2 + 1)]

    # The accessor-internal stale-age bookkeeping column never leaks out.
    assert row_admission.STALE_BOOK_AGE_COLUMN not in df.columns


def test_accessor_gate_order_is_admission_then_errata_then_budget(
    lake_root, registry_root, tracking_root, monkeypatch
):
    import harness.accessor as accessor_module

    partition = _build_admission_errata_fixture(lake_root, registry_root)
    admission = {
        "policy": "stale_book",
        "max_age_ns": row_admission.STALE_BOOK_MAX_AGE_NS,
        "exclude_undefined_age": True,
        "counts": {},
    }
    manifest = _write_probe_manifest(registry_root, partition, admission=admission)

    order: list[str] = []
    real_admission = accessor_module.row_admission.apply_admission_policy
    real_errata = accessor_module.errata.mask_errata_cells
    real_look = accessor_module.budget.record_look

    def _admission_wrapper(df, adm):
        order.append("admission")
        return real_admission(df, adm)

    def _errata_wrapper(df, cells):
        order.append("errata")
        return real_errata(df, cells)

    def _look_wrapper(*args, **kwargs):
        order.append("budget")
        return real_look(*args, **kwargs)

    monkeypatch.setattr(
        accessor_module.row_admission, "apply_admission_policy", _admission_wrapper
    )
    monkeypatch.setattr(accessor_module.errata, "mask_errata_cells", _errata_wrapper)
    monkeypatch.setattr(accessor_module.budget, "record_look", _look_wrapper)

    materialize(
        manifest["manifest_id"],
        "val_probe",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
        errata_cells=[],
    )

    assert order == ["admission", "errata", "budget"]
