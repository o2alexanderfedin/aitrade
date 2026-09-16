"""Tests for data.dq.checks -- the six DQ checks plus the day-split/dedup
algorithm check (1) depends on. Hermetic: small synthetic fixtures with a
known-correct answer, never touching the real gap ledger or real curated
data (see tests/dq/test_pause_enforcement.py for the real-data RP-4 cases).
"""

from __future__ import annotations

import polars as pl
import pytest

from data.dq.checks import (
    NS_PER_SECOND,
    check_crossed_locked_book,
    check_etime_plausibility,
    check_gap_coverage,
    check_l1_sparsity,
    check_na_placeholder,
    check_reconciliation,
    collapse_outage_intervals,
    load_dq_thresholds,
    split_at_day_boundaries,
)

THRESHOLDS = load_dq_thresholds()

GAP_LEDGER_SCHEMA = {
    "stream": pl.Utf8,
    "conn_id": pl.Utf8,
    "gap_start_rtime": pl.Int64,
    "gap_end_rtime": pl.Int64,
    "cause": pl.Utf8,
    "detected_at": pl.Int64,
    "ledger_version": pl.Int32,
}


def _ledger_row(
    stream: str,
    conn_id: str,
    gap_start_rtime: int,
    gap_end_rtime: int,
    cause: str,
    ledger_version: int = 2,
) -> dict:
    return {
        "stream": stream,
        "conn_id": conn_id,
        "gap_start_rtime": gap_start_rtime,
        "gap_end_rtime": gap_end_rtime,
        "cause": cause,
        "detected_at": gap_end_rtime,
        "ledger_version": ledger_version,
    }


def _ledger_df(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=GAP_LEDGER_SCHEMA)


# --- Test 1: ongoing/reactive dedup for the SAME outage -------------------


def test_ongoing_and_reactive_rows_for_same_outage_collapse_to_max_end():
    t0 = 1_800_000_000_000_000_000  # arbitrary, mid-day, well clear of a UTC boundary
    rows = [
        _ledger_row(
            "__connection__",
            "watchdog",
            t0,
            t0 + 30 * NS_PER_SECOND,
            "merged-silent-ongoing: no message from either connection for 30.0s (watchdog)",
        ),
        _ledger_row(
            "__connection__",
            "merged",
            t0,
            t0 + 120 * NS_PER_SECOND,
            "merged-silent: no message from either connection for 120.0s",
        ),
    ]
    collapsed = collapse_outage_intervals(_ledger_df(rows))
    assert collapsed.height == 1
    assert collapsed["gap_start_rtime"][0] == t0
    assert collapsed["gap_end_rtime"][0] == t0 + 120 * NS_PER_SECOND  # NOT 30+120=150s


# --- Test 2: UTC-midnight-crossing split -----------------------------------


def test_outage_crossing_utc_midnight_splits_proportionally_no_double_count():
    # 2026-09-14 23:55:00Z -> 2026-09-15 00:05:00Z, 10 minutes total.
    day1_midnight_ns = (
        1_789_344_000_000_000_000  # 2026-09-14T00:00:00Z (verified below)
    )
    start = day1_midnight_ns + 23 * 3600 * NS_PER_SECOND + 55 * 60 * NS_PER_SECOND
    end = start + 10 * 60 * NS_PER_SECOND

    rows = [
        _ledger_row(
            "__connection__",
            "merged",
            start,
            end,
            "merged-silent: no message for 600.0s",
        )
    ]
    collapsed = collapse_outage_intervals(_ledger_df(rows))
    split = split_at_day_boundaries(collapsed)

    assert split.height == 2
    dates = sorted(split["date"].to_list())
    assert dates == ["2026-09-14", "2026-09-15"]
    for date in dates:
        seconds = split.filter(pl.col("date") == date)["seconds_in_day"][0]
        assert seconds == pytest.approx(300.0, abs=1e-6)
    assert split["seconds_in_day"].sum() == pytest.approx(600.0, abs=1e-6)


def test_gap_coverage_check_sums_split_seconds_for_requested_date_only():
    day1_midnight_ns = 1_789_344_000_000_000_000
    start = day1_midnight_ns + 23 * 3600 * NS_PER_SECOND + 55 * 60 * NS_PER_SECOND
    end = start + 10 * 60 * NS_PER_SECOND
    rows = [
        _ledger_row(
            "__connection__",
            "merged",
            start,
            end,
            "merged-silent: no message for 600.0s",
        )
    ]

    result_day1 = check_gap_coverage(_ledger_df(rows), "2026-09-14", THRESHOLDS)
    result_day2 = check_gap_coverage(_ledger_df(rows), "2026-09-15", THRESHOLDS)
    assert result_day1["value_seconds"] == pytest.approx(300.0, abs=1e-6)
    assert result_day2["value_seconds"] == pytest.approx(300.0, abs=1e-6)
    # 300s > 60s degraded_seconds but < 900s failed_seconds on each side.
    assert result_day1["dq_status"] == "degraded"
    assert result_day2["dq_status"] == "degraded"


# --- Test 3: connection-silent / trade-id-skip excluded from check (1) ----


def test_connection_silent_and_trade_id_skip_excluded_from_check_1():
    t0 = 1_800_000_000_000_000_000
    rows = [
        _ledger_row(
            "__connection__",
            "B",
            t0,
            t0 + 500 * NS_PER_SECOND,
            "connection-silent: no message from conn_id=B for 500.0s",
        ),
        _ledger_row(
            "trade",
            "merged",
            t0,
            t0 + 500 * NS_PER_SECOND,
            "trade-id-skip: missing 42 ids (123..165)",
        ),
    ]
    collapsed = collapse_outage_intervals(_ledger_df(rows))
    assert collapsed.height == 0


# --- Test 4: check (3) reads persisted build_stats, never recomputes ------


def test_na_placeholder_check_reads_persisted_rate_never_recomputes():
    build_stats_ok = {"na_placeholder_dropped": 3, "na_placeholder_rate": 0.001}
    result = check_na_placeholder(build_stats_ok, THRESHOLDS)
    assert result["dq_status"] == "ok"
    assert result["value_pct"] == pytest.approx(0.1)

    build_stats_degraded = {"na_placeholder_dropped": 500, "na_placeholder_rate": 0.03}
    result = check_na_placeholder(build_stats_degraded, THRESHOLDS)
    assert result["dq_status"] == "degraded"
    assert result["value_pct"] == pytest.approx(3.0)


# --- Test 5: check (2) n/a on single-source day ----------------------------


def test_reconciliation_check_is_na_on_single_source_day():
    build_stats = {
        "reconciliation_missing_from_capture": None,
        "reconciliation_missing_from_archive": None,
        "reconciliation_overlap_rows": None,
    }
    result = check_reconciliation(build_stats, THRESHOLDS)
    assert result["dq_status"] == "n/a"


def test_reconciliation_check_degrades_over_threshold():
    build_stats = {
        "reconciliation_missing_from_capture": 10,
        "reconciliation_missing_from_archive": 990,
        "reconciliation_overlap_rows": 9000,
    }
    # missing_from_archive pct = 990/(9000+990) = 9.9% >> 0.5% degraded_pct
    result = check_reconciliation(build_stats, THRESHOLDS)
    assert result["dq_status"] == "degraded"
    assert result["value_pct"] == pytest.approx(9.9, abs=0.01)


# --- Test 6: check (6) etime plausibility ----------------------------------


def test_etime_plausibility_passes_within_window():
    from data.dq.checks import _ns_midnight_utc

    date = "2026-09-12"
    midnight = _ns_midnight_utc(date)
    manifest = {"etime_range": [midnight, midnight + 3600 * NS_PER_SECOND]}
    result = check_etime_plausibility(manifest, date, THRESHOLDS)
    assert result["dq_status"] == "ok"


def test_etime_plausibility_fails_when_max_exceeds_window():
    from data.dq.checks import _ns_midnight_utc

    date = "2026-09-12"
    midnight = _ns_midnight_utc(date)
    # etime_max is 3 full days past date's midnight -- well outside [date-1d, date+2d)
    manifest = {"etime_range": [midnight, midnight + 3 * 86_400 * NS_PER_SECOND]}
    result = check_etime_plausibility(manifest, date, THRESHOLDS)
    assert result["dq_status"] == "failed"


# --- Additional coverage: checks (4)/(5) -----------------------------------


def test_crossed_locked_book_counts_and_is_always_ok():
    df = pl.DataFrame(
        {
            "etime": [1, 2, 3],
            "bid_price": [100.0, 100.5, 99.9],
            "ask_price": [100.2, 100.4, 100.1],  # row 2 (idx1) is crossed: bid>=ask
        }
    )
    result = check_crossed_locked_book(df)
    assert result["dq_status"] == "ok"
    assert result["count"] == 1


def test_l1_sparsity_degrades_on_large_inter_arrival_gap():
    base = 1_800_000_000_000_000_000
    df = pl.DataFrame(
        {"etime": [base, base + 1 * NS_PER_SECOND, base + 40 * NS_PER_SECOND]}
    )
    result = check_l1_sparsity(df, THRESHOLDS)
    assert result["dq_status"] == "degraded"
    assert result["value_seconds"] == pytest.approx(39.0, abs=1e-6)


def test_l1_sparsity_ok_within_threshold():
    base = 1_800_000_000_000_000_000
    df = pl.DataFrame(
        {"etime": [base, base + 1 * NS_PER_SECOND, base + 5 * NS_PER_SECOND]}
    )
    result = check_l1_sparsity(df, THRESHOLDS)
    assert result["dq_status"] == "ok"
