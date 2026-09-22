"""Tests for harness.row_admission: D-05-21's stale-book age and the
decided 5-second admission threshold -- pure, hand-built fixtures (no
lake needed; the real-lake 29,058-row cross-check is a standalone script,
not a collected test, per this plan's own constraints)."""

from __future__ import annotations

from pathlib import Path

import polars as pl

import harness.row_admission as row_admission
from data.time_ns import NS_PER_SECOND
from harness.row_admission import (
    NO_PRIOR_QUOTE_SENTINEL_NS,
    STALE_BOOK_MAX_AGE_NS,
    apply_admission_policy,
    stale_book_age_ns,
)

ADMISSION_DEFAULT_THRESHOLD = {
    "policy": "stale_book",
    "max_age_ns": STALE_BOOK_MAX_AGE_NS,
    "exclude_undefined_age": True,
    "counts": {},
}


def _rows(pairs: list[tuple[int, int]]) -> pl.DataFrame:
    """`(etime, decision_source_rank)` pairs -> the minimal frame
    `stale_book_age_ns` needs."""
    return pl.DataFrame(
        {
            "etime": pl.Series([p[0] for p in pairs], dtype=pl.Int64),
            "decision_source_rank": pl.Series([p[1] for p in pairs], dtype=pl.Int8),
        }
    )


def test_stale_book_max_age_is_five_seconds_derived_from_ns_per_second():
    assert STALE_BOOK_MAX_AGE_NS == 5 * NS_PER_SECOND
    source = Path(row_admission.__file__).read_text()
    # The multiplication above is the only conversion site: no second,
    # independent seconds-to-ns literal anywhere in the module.
    assert "5_000_000_000" not in source, (
        "row_admission.py must derive STALE_BOOK_MAX_AGE_NS from "
        "NS_PER_SECOND alone -- a bare 5_000_000_000 literal would be a "
        "second, independent conversion site"
    )


def test_stale_age_is_zero_at_a_quote_row_itself():
    df = _rows([(0, 0), (NS_PER_SECOND, 1), (2 * NS_PER_SECOND, 0)])
    age = stale_book_age_ns(df).to_list()
    assert age[0] == 0
    assert age[2] == 0


def test_stale_age_grows_across_a_synthetic_gap():
    df = _rows(
        [
            (0, 0),  # quote, age 0
            (1 * NS_PER_SECOND, 1),  # trade, age 1s
            (3 * NS_PER_SECOND, 1),  # trade, age 3s
            (6 * NS_PER_SECOND, 1),  # trade, age 6s
            (10 * NS_PER_SECOND, 0),  # next quote, resets to 0
            (11 * NS_PER_SECOND, 1),  # trade, age 1s
        ]
    )
    age = stale_book_age_ns(df).to_list()
    assert age == [
        0,
        1 * NS_PER_SECOND,
        3 * NS_PER_SECOND,
        6 * NS_PER_SECOND,
        0,
        1 * NS_PER_SECOND,
    ]
    assert age[1] < age[2] < age[3]


def test_leading_rows_before_any_quote_are_maximally_stale_not_zero():
    df = _rows(
        [
            (0, 1),  # trade, no prior quote -- undefined
            (NS_PER_SECOND, 1),  # trade, still no prior quote -- undefined
            (2 * NS_PER_SECOND, 0),  # first quote, age 0
            (3 * NS_PER_SECOND, 1),  # trade, age 1s
        ]
    )
    age = stale_book_age_ns(df).to_list()
    assert age[0] == NO_PRIOR_QUOTE_SENTINEL_NS
    assert age[1] == NO_PRIOR_QUOTE_SENTINEL_NS
    assert age[0] != 0
    assert age[1] != 0
    assert age[2] == 0
    assert age[3] == NS_PER_SECOND

    kept, counts = apply_admission_policy(df, ADMISSION_DEFAULT_THRESHOLD)
    assert counts["excluded_undefined"] == 2
    assert counts["excluded_stale"] == 0
    assert counts["admitted"] == 2
    assert kept["etime"].to_list() == [2 * NS_PER_SECOND, 3 * NS_PER_SECOND]


def test_apply_admission_policy_uses_the_decided_threshold_by_default():
    df = _rows(
        [
            (0, 0),  # quote, age 0
            (2 * NS_PER_SECOND, 1),  # trade, age 2s -- within 5s
            (11 * NS_PER_SECOND, 1),  # trade, age 11s -- stale
            (12 * NS_PER_SECOND, 0),  # quote, resets, age 0
        ]
    )
    kept, counts = apply_admission_policy(df, ADMISSION_DEFAULT_THRESHOLD)

    kept_ages = stale_book_age_ns(kept).to_list()
    assert all(age <= STALE_BOOK_MAX_AGE_NS for age in kept_ages)
    assert all(age != NO_PRIOR_QUOTE_SENTINEL_NS for age in kept_ages)

    assert counts == {"excluded_stale": 1, "excluded_undefined": 0, "admitted": 3}
    assert (
        counts["excluded_stale"] + counts["excluded_undefined"] + counts["admitted"]
        == df.height
    )


def test_none_max_age_excludes_nothing_stale_but_still_excludes_undefined():
    df = _rows(
        [
            (0, 1),  # trade, no prior quote -- undefined
            (NS_PER_SECOND, 0),  # quote, age 0
            (
                1000 * NS_PER_SECOND,
                1,
            ),  # trade, age 999s -- would be stale, but max_age=None
        ]
    )
    admission = {
        "policy": "stale_book",
        "max_age_ns": None,
        "exclude_undefined_age": True,
        "counts": {},
    }
    kept, counts = apply_admission_policy(df, admission)
    assert counts == {"excluded_stale": 0, "excluded_undefined": 1, "admitted": 2}
    assert kept["etime"].to_list() == [NS_PER_SECOND, 1000 * NS_PER_SECOND]
