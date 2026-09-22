"""Tests for harness.kfold (purged + embargoed inner k-fold OOF blocks,
D-05-06/D-05-08) and the compressed_3seg layout that uses them
(harness.segments.issue_segment_manifest(layout="compressed_3seg", ...)).
05-02-PLAN.md Task 2.
"""

from __future__ import annotations

import numpy as np
import polars as pl
from data.time_ns import LABEL_HORIZON_NS, NS_PER_SECOND
from features.tier import load_features
from harness.accessor import materialize
from harness.budget import look_count
from harness.kfold import purged_embargoed_blocks, training_rows_for_block
from harness.purge_embargo import (
    FOLD_EMBARGO_NS,
    PURGE_HORIZON_NS,
    effective_train_intervals,
)
from harness.segments import issue_segment_manifest
from tests.fixtures.harness_span import build_span_partition

S = NS_PER_SECOND
H_MAX_NS = max(LABEL_HORIZON_NS.values())

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

#: h_max == PURGE_HORIZON_NS by construction (both are
#: max(LABEL_HORIZON_NS.values())) -- asserted once so the rest of this
#: file can use either name without re-deriving it.
assert H_MAX_NS == PURGE_HORIZON_NS

TRAIN_WIDTH_NS = 3_000 * S
BLOCK_COUNT = 5
BLOCK_WIDTH_NS = TRAIN_WIDTH_NS // BLOCK_COUNT  # 600s == PURGE_HORIZON_NS (Q8)
MIDDLE_BLOCK_INDEX = 2  # end_j + PURGE_HORIZON_NS lands well inside the span


def _load_span_df(lake_root, registry_root, *, rows: int) -> tuple[pl.DataFrame, dict]:
    span = build_span_partition(
        lake_root, registry_root, date="2026-09-13", start_ns=0, step_ns=S, rows=rows
    )
    df = load_features(
        span["manifest_id"],
        "BTCUSDT.features",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    return df, span


def _no_label_window_overlap(train_etimes: list[int], oof_etimes: list[int]) -> bool:
    """`True` iff no `[t, t+h_max]` (train) intersects any `[t', t'+h_max]`
    (OOF) -- vectorised (numpy broadcasting) since the naive double loop is
    O(len(train) * len(oof))."""
    if not train_etimes or not oof_etimes:
        return True
    t = np.array(train_etimes)[:, None]
    tp = np.array(oof_etimes)[None, :]
    overlap = (t <= tp + H_MAX_NS) & (tp <= t + H_MAX_NS)
    return not bool(overlap.any())


# --------------------------------------------------------------------------
# purged_embargoed_blocks: pure geometry
# --------------------------------------------------------------------------


def test_blocks_partition_the_train_segment():
    # A width that does NOT divide evenly by BLOCK_COUNT (3000s + 3s) so
    # the exact-union assertion below is a real proof the last block
    # absorbs the remainder, not a vacuous pass that would hold even if a
    # mutant silently dropped it (BLOCK_COUNT=4 in the plan's own example
    # text; 5 is this file's own default -- both non-dividing on purpose).
    train_end_ns = TRAIN_WIDTH_NS + 3
    blocks = purged_embargoed_blocks(0, train_end_ns, BLOCK_COUNT)
    assert len(blocks) == BLOCK_COUNT
    assert [b["name"] for b in blocks] == [f"oof_block_{i}" for i in range(BLOCK_COUNT)]
    assert all(b["role"] == "oof_block" for b in blocks)
    # contiguous, non-overlapping, exact union
    assert blocks[0]["start_ns"] == 0
    for a, b in zip(blocks, blocks[1:]):
        assert a["end_ns"] == b["start_ns"]
    assert blocks[-1]["end_ns"] == train_end_ns


def test_blocks_last_block_absorbs_the_remainder():
    # 3001s does not divide evenly by 5 -- the last block must absorb the
    # 1s remainder rather than silently dropping it.
    train_end_ns = TRAIN_WIDTH_NS + 1
    blocks = purged_embargoed_blocks(0, train_end_ns, BLOCK_COUNT)
    widths = [b["end_ns"] - b["start_ns"] for b in blocks]
    assert widths[:-1] == [BLOCK_WIDTH_NS] * (BLOCK_COUNT - 1)
    assert widths[-1] == BLOCK_WIDTH_NS + 1
    assert blocks[-1]["end_ns"] == train_end_ns


# --------------------------------------------------------------------------
# training_rows_for_block: the shared two-sided-purge/one-sided-embargo
# formula, invariant + anti-vacuity (real horizon values, real fixture)
# --------------------------------------------------------------------------


def test_no_label_window_intersection_between_training_rows_and_oof_rows(
    lake_root, registry_root
):
    df, _span = _load_span_df(lake_root, registry_root, rows=int(TRAIN_WIDTH_NS // S))
    blocks = purged_embargoed_blocks(0, TRAIN_WIDTH_NS, BLOCK_COUNT)

    train_df = training_rows_for_block(
        df,
        blocks,
        MIDDLE_BLOCK_INDEX,
        purge_ns=PURGE_HORIZON_NS,
        embargo_ns=FOLD_EMBARGO_NS,
    )
    target = blocks[MIDDLE_BLOCK_INDEX]
    oof_df = df.filter(
        (pl.col("etime") >= target["start_ns"]) & (pl.col("etime") < target["end_ns"])
    )
    assert train_df.height > 0, (
        "fixture geometry left the training set empty -- vacuous"
    )
    assert oof_df.height > 0, "fixture geometry left the OOF block empty -- vacuous"

    assert _no_label_window_overlap(
        train_df["etime"].to_list(), oof_df["etime"].to_list()
    )


def test_anti_vacuity_zero_purge_makes_a_label_window_intersection_reappear(
    lake_root, registry_root
):
    df, _span = _load_span_df(lake_root, registry_root, rows=int(TRAIN_WIDTH_NS // S))
    blocks = purged_embargoed_blocks(0, TRAIN_WIDTH_NS, BLOCK_COUNT)
    target = blocks[MIDDLE_BLOCK_INDEX]
    oof_df = df.filter(
        (pl.col("etime") >= target["start_ns"]) & (pl.col("etime") < target["end_ns"])
    )

    zero_purge_train_df = training_rows_for_block(
        df, blocks, MIDDLE_BLOCK_INDEX, purge_ns=0, embargo_ns=FOLD_EMBARGO_NS
    )
    assert not _no_label_window_overlap(
        zero_purge_train_df["etime"].to_list(), oof_df["etime"].to_list()
    ), "purge_ns=0 should have let at least one label-window intersection survive"


def test_anti_vacuity_zero_embargo_admits_a_row_inside_the_embargo_window(
    lake_root, registry_root
):
    df, _span = _load_span_df(lake_root, registry_root, rows=int(TRAIN_WIDTH_NS // S))
    blocks = purged_embargoed_blocks(0, TRAIN_WIDTH_NS, BLOCK_COUNT)
    target = blocks[MIDDLE_BLOCK_INDEX]
    embargo_window_start = target["end_ns"] + PURGE_HORIZON_NS
    embargo_window_end = embargo_window_start + FOLD_EMBARGO_NS

    # Anti-vacuity setup: the fixture must actually HAVE a row inside the
    # embargo window before any behavioural assertion relies on it.
    in_window = df.filter(
        (pl.col("etime") >= embargo_window_start)
        & (pl.col("etime") < embargo_window_end)
    )
    assert in_window.height > 0, (
        "fixture has no row inside the embargo window -- vacuous"
    )

    full_strength = training_rows_for_block(
        df,
        blocks,
        MIDDLE_BLOCK_INDEX,
        purge_ns=PURGE_HORIZON_NS,
        embargo_ns=FOLD_EMBARGO_NS,
    )
    assert (
        full_strength.filter(
            (pl.col("etime") >= embargo_window_start)
            & (pl.col("etime") < embargo_window_end)
        ).height
        == 0
    ), "the full-strength config should exclude every row in the embargo window"

    zero_embargo = training_rows_for_block(
        df, blocks, MIDDLE_BLOCK_INDEX, purge_ns=PURGE_HORIZON_NS, embargo_ns=0
    )
    admitted = zero_embargo.filter(
        (pl.col("etime") >= embargo_window_start)
        & (pl.col("etime") < embargo_window_end)
    )
    assert admitted.height > 0, (
        "embargo_ns=0 should admit at least one row inside the window the "
        "full-strength config correctly excludes"
    )


# --------------------------------------------------------------------------
# compressed_3seg issuance
# --------------------------------------------------------------------------


def _compressed_3seg_segments():
    return [
        {"name": "train", "role": "train", "start_ns": 0, "end_ns": TRAIN_WIDTH_NS},
        {
            "name": "val",
            "role": "val",
            "start_ns": TRAIN_WIDTH_NS,
            "end_ns": TRAIN_WIDTH_NS + 600 * S,
        },
        {
            "name": "held_out",
            "role": "held_out",
            "start_ns": TRAIN_WIDTH_NS + 600 * S,
            "end_ns": TRAIN_WIDTH_NS + 1_200 * S,
        },
    ]


def _build_compressed_3seg_fixture(
    lake_root, registry_root, tracking_root, *, rows: int = 4_500, **overrides
):
    span = build_span_partition(
        lake_root, registry_root, date="2026-09-13", start_ns=0, step_ns=S, rows=rows
    )
    segments = _compressed_3seg_segments()
    # Anti-vacuity: train's own real effective_intervals (against val/
    # held_out only, never its own nested oof_block children) must be
    # non-empty before relying on a successful issuance below.
    train = segments[0]
    others = [s for s in segments[1:]]  # val, held_out
    intervals = effective_train_intervals(
        train["start_ns"],
        train["end_ns"],
        others,
        purge_ns=PURGE_HORIZON_NS,
        embargo_ns=FOLD_EMBARGO_NS,
    )
    assert intervals, "train is starved under this fixture's geometry -- vacuous"

    kwargs = dict(
        layout="compressed_3seg",
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
        k=BLOCK_COUNT,
    )
    kwargs.update(overrides)
    manifest = issue_segment_manifest(**kwargs)
    return manifest, span


def test_compressed_3seg_issues_with_oof_blocks(
    lake_root, registry_root, tracking_root
):
    manifest, _span = _build_compressed_3seg_fixture(
        lake_root, registry_root, tracking_root
    )
    oof_entries = [s for s in manifest["segments"] if s["role"] == "oof_block"]
    assert [s["name"] for s in oof_entries] == [
        f"oof_block_{i}" for i in range(BLOCK_COUNT)
    ]
    for entry in oof_entries:
        assert entry["end_ns"] > entry["start_ns"]
    assert "oof_training_row_counts" in manifest
    for entry in oof_entries:
        assert entry["name"] in manifest["oof_training_row_counts"]


def test_materializing_an_oof_block_counts_as_a_look(
    lake_root, registry_root, tracking_root
):
    # budget_allowance=2 (not the fixture's default 1): this test
    # materializes oof_block_0 TWICE on purpose, to prove look_count keeps
    # incrementing across repeated materializations of the same segment
    # (05-03-PLAN.md's exhaustion enforcement would otherwise refuse the
    # second call at the default allowance of 1).
    manifest, _span = _build_compressed_3seg_fixture(
        lake_root, registry_root, tracking_root, budget_allowance=2
    )
    mid = manifest["manifest_id"]

    assert look_count(mid, "oof_block_0", tracking_root=str(tracking_root)) == 0
    df = materialize(
        mid,
        "oof_block_0",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    assert df.height > 0
    assert look_count(mid, "oof_block_0", tracking_root=str(tracking_root)) == 1

    # materializing it again increments the SAME (manifest_id, segment_name)
    # pair -- exactly once per materialize() call.
    materialize(
        mid,
        "oof_block_0",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    assert look_count(mid, "oof_block_0", tracking_root=str(tracking_root)) == 2


def test_materialize_train_on_compressed_3seg_is_never_empty(
    lake_root, registry_root, tracking_root
):
    """The fix this task adds to harness.accessor: a train entry's own
    nested oof_block children must never be treated as an "other" purge/
    embargo source against that same train (D-05-04) -- prior to the fix,
    accessor.materialize's train-purge filter included role="oof_block",
    which starves a compressed_3seg train's own materialization to zero
    rows by construction (every row is inside SOME block's own purge
    zone). Also cross-checks the manifest's own recorded
    effective_intervals['train'] against materialize()'s actual etimes."""
    manifest, _span = _build_compressed_3seg_fixture(
        lake_root, registry_root, tracking_root
    )
    train_df = materialize(
        manifest["manifest_id"],
        "train",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    assert train_df.height > 0, (
        "materialize('train') on a compressed_3seg manifest must not be "
        "empty by construction -- oof_block children must never be treated "
        "as a purge/embargo source against their own parent train"
    )
    expected_intervals = manifest["effective_intervals"]["train"]
    for etime in train_df["etime"].to_list():
        assert any(start <= etime < end for start, end in expected_intervals), (
            f"row at etime={etime} survives materialize('train') but falls "
            f"outside every recorded effective interval {expected_intervals}"
        )
    # look_count is unaffected by a train materialization (only val/
    # oof_block roles are looks, D-05-11).
    assert (
        look_count(manifest["manifest_id"], "train", tracking_root=str(tracking_root))
        == 0
    )
