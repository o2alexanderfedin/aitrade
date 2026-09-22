"""Tests for harness.purge_embargo: the derived purge horizon, the pinned
fold-boundary embargo constant, and the shared two-sided-purge/one-sided-
embargo exclusion math (D-05-04, D-05-05).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import spec.catalogue as spec_catalogue
from data.time_ns import LABEL_HORIZON_NS, NS_PER_SECOND, TRADE_FLOW_WINDOW_NS
from harness.purge_embargo import (
    FOLD_EMBARGO_NS,
    PURGE_HORIZON_NS,
    effective_train_intervals,
    filter_train_rows,
)
from tests.fixtures.harness_span import build_span_partition

_MODULE_PATH = Path(__file__).resolve().parents[2] / "harness" / "purge_embargo.py"


def test_fold_embargo_pinned_to_trade_flow_and_ofi_text():
    assert FOLD_EMBARGO_NS == TRADE_FLOW_WINDOW_NS
    ofi = spec_catalogue.load_features()["ofi"]
    assert ofi.information_set == "[prev_l1_update, t]"


def test_purge_horizon_is_derived_never_hardcoded():
    assert PURGE_HORIZON_NS == max(LABEL_HORIZON_NS.values())
    source = _MODULE_PATH.read_text()
    assert "600" not in source, (
        "a bare '600' literal in purge_embargo.py would mean the purge "
        "horizon was hardcoded rather than derived from LABEL_HORIZON_NS"
    )


def test_effective_train_intervals_cuts_out_purge_and_embargo():
    other = {"start_ns": 4_000_000_000_000, "end_ns": 5_000_000_000_000, "role": "val"}
    result = effective_train_intervals(
        0,
        10_000_000_000_000,
        [other],
        purge_ns=600_000_000_000,
        embargo_ns=1_000_000_000,
    )
    assert result == [
        (0, 3_400_000_000_000),
        (5_601_000_000_000, 10_000_000_000_000),
    ]


def test_effective_train_intervals_empty_when_fully_covered():
    other = {"start_ns": 400_000_000_000, "end_ns": 600_000_000_000, "role": "val"}
    result = effective_train_intervals(
        0,
        1_000_000_000_000,
        [other],
        purge_ns=1_000_000_000_000,
        embargo_ns=1_000_000_000,
    )
    assert result == []


def test_effective_train_intervals_exact_boundary_is_half_open_left_closed():
    """05-REVIEW.md IN-02: the existing boundary test
    (`test_filter_train_rows_excludes_the_right_rows` below) only ever
    probes `val_start - purge_ns - 1` and `val_start - purge_ns + 1` --
    never the EXACT boundary value itself. This test closes that gap
    directly against `effective_train_intervals`: a row at exactly
    `val_start - purge_ns` must be EXCLUDED (the left boundary belongs to
    the excluded band, per the docstring's own precise statement), while
    `val_start - purge_ns - 1` (one ns earlier) must survive."""
    purge_ns = 600_000_000_000
    embargo_ns = 1_000_000_000
    val_start = 4_000_000_000_000
    val_end = 5_000_000_000_000
    other = {"start_ns": val_start, "end_ns": val_end, "role": "val"}

    exact_boundary = val_start - purge_ns
    one_before = exact_boundary - 1

    result = effective_train_intervals(
        0,
        10_000_000_000_000,
        [other],
        purge_ns=purge_ns,
        embargo_ns=embargo_ns,
    )
    # The train's own surviving interval ends exactly at the boundary
    # (half-open, so `exact_boundary` itself is NOT in the surviving
    # range -- it belongs to the excluded band instead).
    assert result[0] == (0, exact_boundary)
    assert exact_boundary not in range(result[0][0], result[0][1])
    assert one_before in range(result[0][0], result[0][1])


def test_filter_train_rows_excludes_the_right_rows():
    purge_ns = 600_000_000_000
    embargo_ns = 1_000_000_000
    val_start = 4_000_000_000_000
    val_end = 5_000_000_000_000
    train_entry = {"start_ns": 0, "end_ns": 10_000_000_000_000, "role": "train"}
    val_entry = {"start_ns": val_start, "end_ns": val_end, "role": "val"}

    just_outside = val_start - purge_ns - 1
    just_inside = val_start - purge_ns + 1
    etimes = [
        0,
        just_outside,
        just_inside,
        5_601_000_000_000,
        9_000_000_000_000,
    ]
    df = pl.DataFrame({"etime": etimes, "marker": list(range(len(etimes)))})

    result = filter_train_rows(
        df, train_entry, [val_entry], purge_ns=purge_ns, embargo_ns=embargo_ns
    )
    kept = set(result["etime"].to_list())

    assert just_outside in kept, "a row just outside the purge zone must survive"
    assert just_inside not in kept, "a row just inside the purge zone must be excluded"
    assert kept == {0, just_outside, 5_601_000_000_000, 9_000_000_000_000}


def test_span_fixture_actually_spans_five_segments_plus_gaps(lake_root, registry_root):
    """Anti-vacuity: the span fixture builder must be able to produce a
    partition wide enough to fit five real segments plus four real
    purge+embargo gaps -- never `feature_tier.py:feature_frame`'s
    3-microsecond trap. Asserted BEFORE any split test relies on it."""
    rows = 8_000
    result = build_span_partition(
        lake_root,
        registry_root,
        date="2026-09-13",
        start_ns=0,
        step_ns=NS_PER_SECOND,
        rows=rows,
    )
    span_ns = result["etime_max"] - result["etime_min"]
    expected_segment_width_ns = 200 * NS_PER_SECOND
    threshold = 5 * expected_segment_width_ns + 4 * (PURGE_HORIZON_NS + FOLD_EMBARGO_NS)
    assert span_ns >= threshold, (
        f"fixture span {span_ns} ns is smaller than {threshold} ns -- a "
        "purge/embargo invariant test built on it would pass vacuously"
    )
