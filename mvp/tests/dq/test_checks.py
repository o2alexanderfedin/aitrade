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
    resync_windows_for_date,
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


def _l1_day(
    date: str, start_s: float, end_s: float, step_s: float = 10.0
) -> pl.DataFrame:
    """bookTicker etimes on `date` every `step_s` seconds from `start_s` to
    `end_s` seconds after that date's UTC midnight (int64 ns throughout)."""
    import datetime as _dt

    midnight = (
        (_dt.date.fromisoformat(date) - _dt.date(1970, 1, 1)).days
        * 86_400
        * NS_PER_SECOND
    )
    n = int((end_s - start_s) // step_s) + 1
    first = midnight + int(start_s * NS_PER_SECOND)
    return pl.DataFrame(
        {"etime": [first + i * int(step_s * NS_PER_SECOND) for i in range(n)]}
    )


DAY_END_S = 86_399.0
PAST = 4_000_000_000_000_000_000  # "now" far after every fixture day


def test_l1_sparsity_degrades_on_large_inter_arrival_gap():
    df = pl.concat(
        [_l1_day("2026-09-14", 5, 36_000), _l1_day("2026-09-14", 36_040, DAY_END_S)]
    )
    result = check_l1_sparsity(df, THRESHOLDS, date="2026-09-14", now_ns=PAST)
    assert result["dq_status"] == "degraded"
    assert result["value_seconds"] == pytest.approx(
        45.0, abs=1e-6
    )  # 35995 s -> 36040 s


def test_l1_sparsity_ok_within_threshold():
    df = _l1_day("2026-09-14", 5, DAY_END_S)
    result = check_l1_sparsity(df, THRESHOLDS, date="2026-09-14", now_ns=PAST)
    assert result["dq_status"] == "ok"


def test_l1_sparsity_is_na_not_ok_on_zero_row_partition():
    """03-VERIFICATION.md finding: a 0-row bookTicker partition has no etime
    at all -- "n/a", never a false "ok"."""
    df = pl.DataFrame({"etime": pl.Series([], dtype=pl.Int64)})
    result = check_l1_sparsity(df, THRESHOLDS, date="2026-09-14", now_ns=PAST)
    assert result["dq_status"] == "n/a"
    assert result["value_seconds"] is None
    assert "reason" in result


def test_l1_sparsity_single_row_partition_is_degraded_by_its_edges():
    """One row can no longer hide: the gaps from midnight to it and from it
    to the next midnight are measured (03-REVIEW.md WR-05). Previously n/a."""
    df = _l1_day("2026-09-14", 43_200, 43_200)
    result = check_l1_sparsity(df, THRESHOLDS, date="2026-09-14", now_ns=PAST)
    assert result["dq_status"] == "degraded"


# --- WR-05 (03-REVIEW.md): the start and end of the day are measured -------


def test_l1_sparsity_counts_the_trailing_gap_of_a_capture_that_died_mid_day():
    """Reproduction: daemon died at 12:00Z. Interior gaps are all 10 s, so
    the old check said ok while 12 h of L1 were missing."""
    df = _l1_day("2026-09-14", 5, 43_200)
    result = check_l1_sparsity(df, THRESHOLDS, date="2026-09-14", now_ns=PAST)
    assert result["dq_status"] == "degraded"
    assert result["value_seconds"] == pytest.approx(
        86_400 - 43_195, abs=1e-6
    )  # last row 43195 s


def test_l1_sparsity_counts_the_leading_gap_of_a_late_restart():
    df = _l1_day("2026-09-15", 4 * 3600, DAY_END_S)
    result = check_l1_sparsity(df, THRESHOLDS, date="2026-09-15", now_ns=PAST)
    assert result["dq_status"] == "degraded"
    assert result["value_seconds"] == pytest.approx(4 * 3600, abs=1e-6)


def test_l1_sparsity_regime_start_day_measures_the_leading_gap_from_regime_start():
    """2026-09-12: L1 capture began at 06:37:10.882Z by design (two-regime
    boundary). The leading gap is measured from that instant, not midnight
    -- explicitly, not by accident."""
    regime_s = 6 * 3600 + 37 * 60 + 10.882
    on_time = _l1_day("2026-09-12", regime_s + 1, DAY_END_S)
    assert (
        check_l1_sparsity(on_time, THRESHOLDS, date="2026-09-12", now_ns=PAST)[
            "dq_status"
        ]
        == "ok"
    )
    late = _l1_day("2026-09-12", regime_s + 600, DAY_END_S)
    result = check_l1_sparsity(late, THRESHOLDS, date="2026-09-12", now_ns=PAST)
    assert result["dq_status"] == "degraded"
    assert result["value_seconds"] == pytest.approx(600, abs=1e-3)


def test_l1_sparsity_exempts_the_trailing_gap_of_the_in_progress_day():
    df = _l1_day("2026-09-16", 5, 43_200)
    import datetime as _dt

    midnight = (
        (_dt.date(2026, 9, 16) - _dt.date(1970, 1, 1)).days * 86_400 * NS_PER_SECOND
    )
    now_ns = midnight + 43_205 * NS_PER_SECOND
    result = check_l1_sparsity(df, THRESHOLDS, date="2026-09-16", now_ns=now_ns)
    assert result["dq_status"] == "ok"


# --- resync_windows_for_date: etime_approx naming (03-VERIFICATION.md ---
# --- gap-closure finding 3) ----------------------------------------------


def test_resync_windows_schema_names_the_join_columns_etime_approx():
    """The columns Phase 4 actually joins against curated etime must carry
    the `_etime_approx` suffix -- never a bare `_rtime` name secretly used
    as etime. gap_start_rtime/gap_end_rtime stay as the ledger's own,
    honestly-labeled audit values."""
    # 2026-09-14T01:00:00Z -- same verified-midnight constant as the
    # UTC-boundary test above, offset well clear of the day edge.
    day1_midnight_ns = 1_789_344_000_000_000_000
    t0 = day1_midnight_ns + 3600 * NS_PER_SECOND
    rows = [
        _ledger_row(
            "__connection__",
            "merged",
            t0,
            t0 + 120 * NS_PER_SECOND,
            "merged-silent: no message for 120.0s",
        )
    ]
    result = resync_windows_for_date(_ledger_df(rows), "2026-09-14", warmup_seconds=60)
    assert set(result.columns) == {
        "gap_start_rtime",
        "gap_end_rtime",
        "gap_end_etime_approx",
        "warmup_end_etime_approx",
    }
    assert result.height == 1
    assert result["gap_end_etime_approx"][0] == t0 + 120 * NS_PER_SECOND
    assert result["warmup_end_etime_approx"][0] == t0 + 180 * NS_PER_SECOND


def test_resync_windows_empty_ledger_has_the_same_etime_approx_schema():
    empty = resync_windows_for_date(_ledger_df([]), "2026-09-14", warmup_seconds=60)
    assert empty.height == 0
    assert set(empty.columns) == {
        "gap_start_rtime",
        "gap_end_rtime",
        "gap_end_etime_approx",
        "warmup_end_etime_approx",
    }


# --- WR-04 (03-REVIEW.md): probable-loss on pre-capture archive days --------
# Informational since the 2026-09-17 re-measure: a run is flagged only when it
# is implausible as an X="NA" placeholder burst on BOTH axes (ids AND etime
# span), and the check never reports degraded/failed.

_T0 = 1_789_171_200_000_000_000  # 2026-09-12T00:00:00Z
_MS = 1_000_000


def _trades(points: list[tuple[int, int]]) -> pl.DataFrame:
    """(trade_id, etime) pairs -> the two columns check_probable_loss reads."""
    return pl.DataFrame(
        {"trade_id": [p[0] for p in points], "etime": [p[1] for p in points]},
        schema={"trade_id": pl.Int64, "etime": pl.Int64},
    )


def _with_skip(run_ids: int, span_ns: int) -> pl.DataFrame:
    """Contiguous trades, then one skip of `run_ids` ids whose etime step is
    `span_ns`, then contiguous trades again."""
    head = [(i, _T0 + i * _MS) for i in range(1, 11)]
    last_id, last_t = head[-1]
    first_id = last_id + run_ids + 1
    tail = [(first_id + i, last_t + span_ns + i * _MS) for i in range(10)]
    return _trades(head + tail)


def test_probable_loss_thresholds_are_the_measured_two_axis_values():
    assert THRESHOLDS.probable_loss.flag_run_ids_over == 100
    assert THRESHOLDS.probable_loss.flag_span_seconds_over == 1


def test_probable_loss_ignores_na_shaped_bursts_and_quiet_market_skips():
    """The shapes measured over all 107 archive days: fast bursts of up to 16
    ids within ~20 ms, and 1-5 id skips spanning up to ~11 s in a quiet
    market. None is flagged; status is ok."""
    from data.dq.checks import check_probable_loss

    points, tid, t = [], 1, _T0
    for run, step_ns in [
        (16, 5 * _MS),
        (15, 11 * _MS),
        (6, 21 * _MS),
        (1, 10_886 * _MS),
        (5, 900 * _MS),
    ]:
        points.append((tid, t))
        tid, t = tid + run + 1, t + step_ns
        points.append((tid, t))
        tid, t = tid + 1, t + _MS
    result = check_probable_loss(_trades(points), THRESHOLDS)
    assert result["dq_status"] == "ok"
    assert result["count"] == 0
    assert "max_run_ids=16" in result["reason"]
    assert "max_run_span_s=10.886" in result["reason"]


def test_probable_loss_flags_a_genuine_loss_but_stays_informational():
    from data.dq.checks import check_probable_loss

    result = check_probable_loss(_with_skip(5_000, 30 * NS_PER_SECOND), THRESHOLDS)
    assert result["count"] == 1
    assert result["dq_status"] == "ok"  # informational: never degraded/failed
    assert "largest_flagged: run_ids=5000 span_s=30.000" in result["reason"]
    assert "ids_in_flagged_runs=5000" in result["reason"]


def test_probable_loss_large_ids_in_a_tiny_span_is_not_flagged():
    """Pinned choice: a big id skip within a few ms is indistinguishable
    from a placeholder storm, so the size axis alone does not flag."""
    from data.dq.checks import check_probable_loss

    result = check_probable_loss(_with_skip(500, 5 * _MS), THRESHOLDS)
    assert result["count"] == 0
    assert "max_run_ids=500" in result["reason"]


def test_probable_loss_small_ids_over_a_long_span_is_not_flagged():
    """Pinned choice: 87,733 real archive skip runs span > 1 s, all of 1-5
    ids (a lone placeholder in a quiet market), so the span axis alone does
    not flag."""
    from data.dq.checks import check_probable_loss

    result = check_probable_loss(_with_skip(2, 10 * NS_PER_SECOND), THRESHOLDS)
    assert result["count"] == 0


def test_probable_loss_both_thresholds_are_strict():
    from data.dq.checks import check_probable_loss

    at_limit = _with_skip(100, 1 * NS_PER_SECOND)
    assert check_probable_loss(at_limit, THRESHOLDS)["count"] == 0
    just_over = _with_skip(101, 1 * NS_PER_SECOND + 1)
    assert check_probable_loss(just_over, THRESHOLDS)["count"] == 1


def test_probable_loss_sorts_by_trade_id_and_needs_two_trades():
    from data.dq.checks import check_probable_loss

    shuffled = _with_skip(5_000, 30 * NS_PER_SECOND).reverse()
    assert check_probable_loss(shuffled, THRESHOLDS)["count"] == 1
    single = check_probable_loss(_trades([(1, _T0)]), THRESHOLDS)
    assert single["dq_status"] == "n/a"


# --- 03-REVIEW-ITER2.md CR-06: the runtime backstop for a wrong ms->ns scale --


def _day_of_binance_ms(date: str) -> pl.Series:
    """A full UTC day of Binance millisecond timestamps (first and last ms)."""
    from data.dq.checks import _ns_midnight_utc

    start_ms = _ns_midnight_utc(date) // 1_000_000
    return pl.Series("time", [start_ms, start_ms + 86_400_000 - 1], dtype=pl.Int64)


def _plausibility_of(etimes: list[int], date: str) -> str:
    manifest = {"etime_range": [min(etimes), max(etimes)]}
    return check_etime_plausibility(manifest, date, THRESHOLDS)["dq_status"]


def test_etime_plausibility_pins_the_four_ms_to_ns_scaling_outcomes():
    """`tools/check_ms_to_ns_site.py` only enforces the single-site hygiene
    invariant and is statically evadable; correctness of the scale is carried
    by this data gate, whose `failed` pauses `load_curated`. Pin all four
    outcomes so the backstop cannot silently regress."""
    date = "2026-09-12"
    ms = _day_of_binance_ms(date)
    frame = pl.DataFrame({"time": ms})

    correct = frame.select(pl.col("time") * 1_000_000)["time"].to_list()
    assert _plausibility_of(correct, date) == "ok"

    forgot = ms.to_list()  # raw ms stored as if ns: 1970-01-21
    assert _plausibility_of(forgot, date) == "failed"

    under = frame.select(pl.col("time") * 1_000)["time"].to_list()  # us, 1970-01-21
    assert _plausibility_of(under, date) == "failed"

    # Over-converted: ms * 1e9 is not representable in int64 (~1.79e21 > 9.22e18).
    exact = [t * 1_000_000_000 for t in ms.to_list()]
    assert all(v > 2**63 - 1 for v in exact)
    assert _plausibility_of(exact, date) == "failed"
    with pytest.raises((TypeError, OverflowError)):
        pl.Series("etime", exact, dtype=pl.Int64)  # a strict Int64 build refuses it
    # ...but a polars Int64 EXPRESSION (the ingest path) wraps silently, so the
    # gate -- not an exception -- is what catches it: a day spans 86.4e6 ms,
    # i.e. 8.64e16 ns after the wrap, far wider than the 3-day window.
    wrapped = frame.select(pl.col("time") * 1_000_000_000)["time"].to_list()
    assert wrapped != exact
    assert _plausibility_of(wrapped, date) == "failed"


# --- 03-REVIEW-ITER3.md IN-20: the same backstop for event_time -------------


def test_event_time_plausibility_uses_the_etime_window_and_scales():
    from data.dq.checks import check_event_time_plausibility

    date = "2026-09-12"
    ms = _day_of_binance_ms(date).to_list()
    ns = [t * 1_000_000 for t in ms]

    def status(bounds):
        return check_event_time_plausibility(bounds, date, THRESHOLDS)["dq_status"]

    assert status((min(ns), max(ns))) == "ok"
    assert status((min(ms), max(ms))) == "failed"  # conversion forgotten
    assert status((min(ns) // 1_000, max(ns) // 1_000)) == "failed"  # under
    assert status((min(ns), max(ns) + 3 * 86_400 * 1_000_000_000)) == "failed"
    assert status((None, None)) == "n/a"  # no event_time values to judge


# --- 03-FOLLOWUPS.md item 4: rtime is judged by the manifest's own source ---


def _rtime_manifest(etime_max: int, built_at: int) -> dict:
    return {
        "built_at": built_at,
        "etime_range": [etime_max - 86_400 * 10**9, etime_max],
    }


def _rtime_status(stats, source, manifest) -> dict:
    from data.dq.checks import check_rtime_plausibility

    return check_rtime_plausibility(stats, source, manifest, THRESHOLDS)


def test_rtime_plausibility_capture_allows_real_measured_skew():
    """The real capture population must pass: measured skew over the 4 real
    capture days runs from -0.192 s to +307.069 s (a post-sleep backlog
    flush), and the bounds are -60 s / +3600 s."""
    etime_max = 1_789_430_399_999_000_000
    manifest = _rtime_manifest(etime_max, built_at=etime_max + 10**12)
    stats = {
        "rtime_min": etime_max - 86_400 * 10**9,
        "rtime_max": etime_max,
        "skew_min": -192_158_000,
        "skew_max": 307_068_672_000,
    }
    assert _rtime_status(stats, "capture", manifest)["dq_status"] == "ok"


def test_rtime_plausibility_capture_fails_a_wrongly_scaled_receive_clock():
    etime_max = 1_789_430_399_999_000_000
    manifest = _rtime_manifest(etime_max, built_at=etime_max + 10**12)
    # rtime left in raw milliseconds: lands in 1970, ~56 years before etime.
    raw_ms = etime_max // 1_000_000
    result = _rtime_status(
        {
            "rtime_min": raw_ms,
            "rtime_max": raw_ms,
            "skew_min": raw_ms - etime_max,
            "skew_max": raw_ms - etime_max,
        },
        "capture",
        manifest,
    )
    assert result["dq_status"] == "failed"
    assert "below" in result["reason"]


def test_rtime_plausibility_capture_fails_a_skew_beyond_the_ceiling():
    etime_max = 1_789_430_399_999_000_000
    manifest = _rtime_manifest(etime_max, built_at=etime_max + 10 * 86_400 * 10**9)
    result = _rtime_status(
        {
            "rtime_min": etime_max,
            "rtime_max": etime_max + 2 * 3600 * 10**9,
            "skew_min": 0,
            "skew_max": 2 * 3600 * 10**9,  # 2 h > the 3600 s ceiling
        },
        "capture",
        manifest,
    )
    assert result["dq_status"] == "failed"
    assert "above" in result["reason"]


def test_rtime_plausibility_capture_fails_when_the_skew_cannot_be_computed():
    """rtime present but no etime to compare it with: "could not judge it"
    is never an ok."""
    etime_max = 1_789_430_399_999_000_000
    manifest = _rtime_manifest(etime_max, built_at=etime_max + 10**12)
    result = _rtime_status(
        {
            "rtime_min": etime_max,
            "rtime_max": etime_max,
            "skew_min": None,
            "skew_max": None,
        },
        "capture",
        manifest,
    )
    assert result["dq_status"] == "failed"


def test_rtime_plausibility_archive_accepts_a_download_long_after_the_day():
    """The archive population: one rtime literal per day, measured 21.5 h to
    107.9 days after etime. A skew window would pause all 107 real days."""
    etime_max = 1_780_358_399_294_000_000
    built_at = 1_789_594_165_761_991_000
    downloaded = 1_789_592_751_978_096_932  # the real staged-file mtime
    stats = {
        "rtime_min": downloaded,
        "rtime_max": downloaded,
        "skew_min": downloaded - etime_max - 86_400 * 10**9,
        "skew_max": downloaded - etime_max,
    }
    manifest = _rtime_manifest(etime_max, built_at)
    assert _rtime_status(stats, "archive", manifest)["dq_status"] == "ok"
    # ...and the same day scored as capture-sourced is a failure, which is
    # exactly why the check has to know the source.
    assert _rtime_status(stats, "capture", manifest)["dq_status"] == "failed"


def test_rtime_plausibility_archive_fails_a_download_predating_its_own_data():
    etime_max = 1_780_358_399_294_000_000
    manifest = _rtime_manifest(etime_max, built_at=etime_max + 10**15)
    result = _rtime_status(
        {
            "rtime_min": etime_max - 10**12,
            "rtime_max": etime_max - 10**12,
            "skew_min": 0,
            "skew_max": 0,
        },
        "archive",
        manifest,
    )
    assert result["dq_status"] == "failed"
    assert "predate" in result["reason"]


def test_rtime_plausibility_fails_an_rtime_after_the_manifest_was_issued():
    etime_max = 1_789_430_399_999_000_000
    built_at = etime_max + 10**12
    for source in ("capture", "archive"):
        result = _rtime_status(
            {
                "rtime_min": built_at + 10**9,
                "rtime_max": built_at + 10**9,
                "skew_min": 0,
                "skew_max": 0,
            },
            source,
            _rtime_manifest(etime_max, built_at),
        )
        assert result["dq_status"] == "failed", source
        assert "built_at" in result["reason"]


def test_rtime_plausibility_is_na_without_rtime_values():
    manifest = _rtime_manifest(1_789_430_399_999_000_000, built_at=10**19)
    result = _rtime_status(
        {"rtime_min": None, "rtime_max": None, "skew_min": None, "skew_max": None},
        "capture",
        manifest,
    )
    assert result["dq_status"] == "n/a"


# --- 03-REVIEW-FOLLOWUPS.md WR-03: an unknown source must not default -------


@pytest.mark.parametrize(
    ("label", "inputs"),
    [
        ("no inputs key", None),
        ("empty inputs list", []),
        ("a vendor source", [{"path": "raw/source=tardis/symbol=BTCUSDT/x.parquet"}]),
        (
            "a mixed list",
            [
                {"path": "raw/source=archive/symbol=BTCUSDT/x.parquet"},
                {"path": "raw/source=capture/symbol=BTCUSDT/y.parquet"},
            ],
        ),
        ("no recognisable shape at all", [{"path": "raw/somewhere/x.parquet"}]),
        ("an input with no path key", [{"rows": 1}]),
        (
            "capture mixed with a vendor",
            [
                {"path": "/d/symbol=BTCUSDT/stream=trade/date=2026-01-01/p.parquet"},
                {"path": "raw/source=tardis/symbol=BTCUSDT/x.parquet"},
            ],
        ),
    ],
    ids=["no-key", "empty", "vendor", "mixed", "no-shape", "no-path", "capture+vendor"],
)
def test_an_unrecognised_manifest_source_is_unknown_not_capture(label, inputs):
    """`manifest_source` returned `"archive"` only when EVERY input path was
    an archive partition, and `"capture"` for everything else -- including a
    manifest with no inputs at all and a future `/source=tardis/`. Tardis is
    this project's documented plan for historical L1, and a vendor download's
    rtime is a download time, days from etime: it would have been judged by
    the capture skew window and paused on every single day."""
    from data.store import UNKNOWN_MANIFEST_SOURCE, manifest_source

    manifest = {} if inputs is None else {"inputs": inputs}
    assert manifest_source(manifest) == UNKNOWN_MANIFEST_SOURCE, label


def test_the_two_known_sources_are_recognised_by_their_own_path_shape():
    """Capture is recognised POSITIVELY, by the capture daemon's own layout,
    not as the fall-through. `curated_build` records `str(path.resolve())`,
    and only the archive staging tree is hive-partitioned by `source=` -- so
    the fall-through used to be "capture", which is what made every unknown
    source silently a capture one."""
    from data.store import manifest_source

    capture = "/Volumes/x/capture/parsed/symbol=BTCUSDT/stream=bookTicker/date=2026-09-12/part-1.parquet"  # noqa: E501
    archive = "/Volumes/x/backfill/staged/source=archive/symbol=BTCUSDT/stream=trade/date=2026-06-01/p.parquet"
    assert manifest_source({"inputs": [{"path": capture}]}) == "capture"
    assert manifest_source({"inputs": [{"path": archive}]}) == "archive"


def test_rtime_plausibility_fails_an_unknown_source_with_an_actionable_reason():
    """`failed` pauses `load_curated`, which is right -- but the report must
    say WHY, naming the file and section where the missing bounds go, rather
    than blaming a skew window that was never meant for this source."""
    from data.store import UNKNOWN_MANIFEST_SOURCE

    etime_max = 1_789_430_399_999_000_000
    manifest = _rtime_manifest(etime_max, built_at=etime_max + 10**12)
    downloaded = etime_max + 5 * 86_400 * 10**9
    result = _rtime_status(
        {
            "rtime_min": downloaded,
            "rtime_max": downloaded,
            "skew_min": downloaded - etime_max,
            "skew_max": downloaded - etime_max,
        },
        UNKNOWN_MANIFEST_SOURCE,
        manifest,
    )
    assert result["dq_status"] == "failed"
    assert "capture-sourced" not in result["reason"], (
        "an unknown source must not be blamed on the capture skew window"
    )
    assert "is above" not in result["reason"]
    assert "dq_thresholds.toml" in result["reason"]
    assert "rtime_plausibility" in result["reason"]


def test_every_real_curated_manifest_still_has_a_known_source():
    """Measured read-only over every committed CURATED by-date manifest:
    8 capture, 111 archive, zero unknown (05-00-PLAN.md widened the pool by
    4 bookTicker capture days and 4 trade days -- 2026-09-16..19, the
    trade days built capture-sourced first, then superseded to
    archive-sourced once data.backfill.downloader fetched the published
    zips, from the prior 4 capture / 107 archive). The fail-closed branch
    of `check_rtime_plausibility` must not pause a single real day.

    SCOPED TO CURATED, and the scoping is the finding rather than a
    loosening. Phase 4's features tier issues by-date pointers of its own,
    and `manifest_source` reports `"unknown"` for every one of them -- a
    features manifest's `inputs[]` are manifest JSON paths, not
    `/source=.../` partition paths, so there is nothing for it to read.
    That is harmless exactly once, and the assertion below is what keeps it
    so: `data.dq.report.STREAMS` drives the only loop that ever reaches
    `check_rtime_plausibility`, and `"features"` is not in it. A decision
    row has no receive clock; there is no rtime to judge.
    """
    import json

    from data.dq.report import STREAMS
    from data.lake_paths import LAKE_REGISTRY_ROOT
    from data.store import manifest_source

    manifests_dir = LAKE_REGISTRY_ROOT / "manifests"
    pointers = sorted(manifests_dir.rglob("by-date/*.json"))
    curated = [p for p in pointers if "features" not in p.parent.parent.name]
    features = [p for p in pointers if "features" in p.parent.parent.name]
    assert len(curated) == 119, f"expected 119 curated by-date pointers, {len(curated)}"

    def source_of(pointer):
        manifest_id = json.loads(pointer.read_text())["manifest_id"]
        body = json.loads(next(manifests_dir.rglob(f"{manifest_id}.json")).read_text())
        return manifest_source(body)

    counts: dict[str, int] = {}
    for pointer in curated:
        source = source_of(pointer)
        counts[source] = counts.get(source, 0) + 1
    assert counts == {"capture": 8, "archive": 111}, counts

    assert features, "the features tier has no committed by-date pointer"
    assert {source_of(p) for p in features} == {"unknown"}
    assert "features" not in STREAMS, (
        "adding 'features' to the curated stream loop would send "
        "check_rtime_plausibility at a manifest with no rtime, and its "
        "fail-closed unknown-source branch would pause every features day"
    )


def test_the_label_gap_threshold_converts_exactly_not_by_float_multiply():
    """04-REVIEW.md IN-05: `int(2.3 * 1_000_000_000)` is `2299999999`.

    The integral `30` in the TOML today is exact either way, which is
    precisely why a float multiply here would have gone unnoticed until
    the first fractional threshold -- in a project whose stated discipline
    is that time arithmetic is integer nanoseconds.
    """
    from data.dq.checks import LabelGapThresholds

    def ns(seconds):
        return LabelGapThresholds(
            max_quote_gap_seconds=seconds, notes=""
        ).max_quote_gap_ns

    assert ns(30) == 30_000_000_000
    assert ns(2.3) == 2_300_000_000, "the float multiply gives 2299999999"
    assert ns(0.5) == 500_000_000
    assert ns(1.000000001) == 1_000_000_001
    assert all(isinstance(ns(v), int) for v in (30, 2.3, 0.5))
