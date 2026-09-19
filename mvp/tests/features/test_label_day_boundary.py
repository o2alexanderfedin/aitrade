"""Day D's last ten minutes need day D+1, and partitions are write-once.

D-04-05's day-boundary rule in two halves:

1. A day is built ONLY once the next day's curated manifest exists. The
   most recent day is never built, so no partition is ever issued with a
   null tail that a later rebuild would have to repoint -- and since
   partitions are write-once, there is no repointing available.
2. D+1's quotes extend the as-of SEARCH SPACE and nothing else. They
   produce no decision rows of their own; a D+1 row appearing in the
   output would be a day's worth of rows written into the wrong
   partition, with labels computed from a mid D's own build never saw.

The fourth test is the one that proves the cross-day read is load-bearing
rather than decorative: delete D+1 and a real label disappears.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from data.store import dq_report_path, issue_manifest
from data.time_ns import LABEL_HORIZON_NS, NS_PER_DAY, NS_PER_SECOND, RET_1MIN_NS
from features.labels import (
    NextDayUnavailableError,
    append_next_day_quotes,
    assert_next_day_available,
    compute_labels,
    next_day_quote_series,
    next_utc_date,
)

SYMBOL = "BTCUSDT"
STREAM = "bookTicker"
DATES = ("2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15")

REPORT_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Utf8,
    "symbol": pl.Utf8,
    "stream": pl.Utf8,
    "manifest_id": pl.Utf8,
    "check": pl.Utf8,
    "dq_status": pl.Utf8,
    "value": pl.Float64,
    "count": pl.Int64,
    "detail": pl.Utf8,
}

#: 2026-09-13T00:00:00Z in ns -- the fixture days are anchored to a real
#: UTC midnight so "the last minute of D" and "the first minute of D+1"
#: are the real thing rather than two arbitrary integers.
DAY_13_START = 1_789_257_600 * NS_PER_SECOND


def _curated_quotes(etimes: list[int], mids: list[float]) -> pl.DataFrame:
    """A curated bookTicker partition carrying exactly the columns
    `features.event_stream.project_bookticker` reads, with a 0.1 spread so
    `mid` is the number the fixture names."""
    return pl.DataFrame(
        {
            "seq": list(range(len(etimes))),
            "etime": etimes,
            "bid_price": [m - 0.05 for m in mids],
            "bid_qty": [1.0] * len(mids),
            "ask_price": [m + 0.05 for m in mids],
            "ask_qty": [2.0] * len(mids),
        },
        schema={
            "seq": pl.Int64,
            "etime": pl.Int64,
            "bid_price": pl.Float64,
            "bid_qty": pl.Float64,
            "ask_price": pl.Float64,
            "ask_qty": pl.Float64,
        },
    )


def _issue_curated_day(
    lake_root: Path,
    registry_root: Path,
    date: str,
    etimes: list[int] | None = None,
    mids: list[float] | None = None,
) -> str:
    """Write one curated bookTicker partition for `date`, issue its
    manifest (which writes the by-date pointer the gate reads), score it
    `ok` in the day's DQ report, and return the manifest id."""
    if etimes is None:
        etimes = [1_000, 2_000]
        mids = [100.0, 100.1]
    df = _curated_quotes(etimes, list(mids or []))
    rel = f"curated/symbol={SYMBOL}/stream={STREAM}/date={date}/part-1.parquet"
    path = lake_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, compression="zstd")
    st = path.stat()
    manifest = issue_manifest(
        dataset=f"{SYMBOL}.{STREAM}",
        symbol=SYMBOL,
        stream=STREAM,
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[
            {
                "date": date,
                "path": rel,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "rows": df.height,
                "size_bytes": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "etime_min": int(df["etime"].min()),
                "etime_max": int(df["etime"].max()),
            }
        ],
        code_hash="deadbeef",
        registry_root=registry_root,
        dates=[date],
    )
    report = dq_report_path(lake_root, date)
    report.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "date": [date],
            "symbol": [SYMBOL],
            "stream": [STREAM],
            "manifest_id": [manifest["manifest_id"]],
            "check": ["l1_sparsity"],
            "dq_status": ["ok"],
            "value": [0.0],
            "count": [None],
            "detail": [None],
        },
        schema=REPORT_SCHEMA,
    ).write_parquet(report, compression="zstd")
    return manifest["manifest_id"]


@pytest.fixture
def lake(tmp_path: Path):
    """The lake as it really stands today: curated bookTicker for
    2026-09-12..15 and nothing after."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    for date in DATES:
        _issue_curated_day(lake_root, registry_root, date)
    return lake_root, registry_root


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_build_refuses_a_day_whose_successor_has_no_curated_manifest(lake):
    _, registry_root = lake
    with pytest.raises(NextDayUnavailableError) as excinfo:
        assert_next_day_available(SYMBOL, "2026-09-15", registry_root=registry_root)
    message = str(excinfo.value)
    assert "2026-09-16" in message, "the refusal must name the day it waited for"
    assert "2026-09-15" in message


def test_build_allows_a_day_whose_successor_exists(lake):
    _, registry_root = lake
    assert (
        assert_next_day_available(SYMBOL, "2026-09-14", registry_root=registry_root)
        == "2026-09-15"
    )


def test_the_buildable_days_are_exactly_the_days_with_a_successor(lake):
    """The list Plan 05 builds from, derived rather than transcribed: with
    curated bookTicker covering 09-12..09-15, the buildable feature days
    are 09-12, 09-13 and 09-14 -- never the most recent day."""
    _, registry_root = lake
    buildable = []
    for date in DATES:
        try:
            assert_next_day_available(SYMBOL, date, registry_root=registry_root)
        except NextDayUnavailableError:
            continue
        buildable.append(date)
    assert buildable == ["2026-09-12", "2026-09-13", "2026-09-14"]


def test_next_utc_date_crosses_a_month_boundary():
    assert next_utc_date("2026-09-30") == "2026-10-01"
    assert next_utc_date("2026-12-31") == "2027-01-01"


# --------------------------------------------------------------------------
# What D+1 contributes, and what it must not
# --------------------------------------------------------------------------


def test_next_day_quote_series_comes_back_as_etime_and_mid(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    _issue_curated_day(lake_root, registry_root, "2026-09-13")
    _issue_curated_day(
        lake_root,
        registry_root,
        "2026-09-14",
        etimes=[DAY_13_START + NS_PER_DAY, DAY_13_START + NS_PER_DAY + NS_PER_SECOND],
        mids=[200.0, 201.0],
    )
    etime, mid = next_day_quote_series(
        SYMBOL, "2026-09-13", registry_root=registry_root, lake_root=lake_root
    )
    assert etime.dtype == np.int64 and mid.dtype == np.float64
    assert etime.tolist() == [
        DAY_13_START + NS_PER_DAY,
        DAY_13_START + NS_PER_DAY + NS_PER_SECOND,
    ]
    assert mid.tolist() == [200.0, 201.0], (
        "mid comes from the ONE kernel, not from a second (bid+ask)/2 "
        "written in labels.py"
    )


def test_next_day_quotes_are_appended_not_merged_as_decision_rows():
    """D+1 extends the search space; it contributes no rows of its own."""
    s = NS_PER_SECOND
    d_end = DAY_13_START + NS_PER_DAY
    decision_etime = np.array([d_end - 90 * s, d_end - 30 * s, d_end - 1], np.int64)
    decision_mid = np.array([100.0, 100.0, 100.0], np.float64)
    d_quotes = (decision_etime.copy(), decision_mid.copy())
    d1_quotes = (
        np.array([d_end + 5 * s, d_end + 65 * s], np.int64),
        np.array([101.0, 102.0], np.float64),
    )

    quote_etime, quote_mid = append_next_day_quotes(*d_quotes, *d1_quotes)
    assert quote_etime.size == 5, "the QUOTE series grew"

    labels, stats = compute_labels(
        decision_etime,
        decision_mid,
        quote_etime,
        quote_mid,
        horizons_ns={"ret_1min_mid": RET_1MIN_NS},
        gap_threshold_ns=120 * s,
    )
    assert labels["ret_1min_mid"].size == decision_etime.size == 3, (
        "one label per decision row of day D -- appending D+1 to the "
        "DECISION rows would make this 5"
    )
    assert stats["n_decision_rows"] == 3


def test_append_next_day_quotes_refuses_an_out_of_order_seam():
    """The seam is the one join in this module where monotonicity can
    actually break (a mis-ordered pair of days), and `searchsorted` over
    an unsorted array is silently wrong."""
    s = NS_PER_SECOND
    with pytest.raises(ValueError, match="seam|non-decreasing"):
        append_next_day_quotes(
            np.array([10 * s, 20 * s], np.int64),
            np.array([100.0, 101.0], np.float64),
            np.array([15 * s], np.int64),
            np.array([102.0], np.float64),
        )


def test_label_tail_actually_reaches_into_the_next_day():
    """A decision row in D's last 60 s: labelled from a D+1 quote, and
    null without one."""
    s = NS_PER_SECOND
    d_end = DAY_13_START + NS_PER_DAY
    decision_etime = np.array([d_end - 30 * s], np.int64)
    decision_mid = np.array([100.0], np.float64)
    d_etime = np.array([d_end - 30 * s], np.int64)
    d_mid = np.array([100.0], np.float64)
    # two D+1 quotes: the prevailing one at t+60s, and a later one so the
    # past-end guard is not what this test is measuring
    d1_etime = np.array([d_end + 20 * s, d_end + 120 * s], np.int64)
    d1_mid = np.array([110.0, 111.0], np.float64)

    with_tail = compute_labels(
        decision_etime,
        decision_mid,
        *append_next_day_quotes(d_etime, d_mid, d1_etime, d1_mid),
        horizons_ns={"ret_1min_mid": RET_1MIN_NS},
        gap_threshold_ns=120 * s,
    )[0]["ret_1min_mid"]
    assert with_tail[0] == pytest.approx(0.1), (
        "t + 60s lands 30s into day D+1; the prevailing mid there is 110.0"
    )

    without_tail, stats = compute_labels(
        decision_etime,
        decision_mid,
        d_etime,
        d_mid,
        horizons_ns={"ret_1min_mid": RET_1MIN_NS},
        gap_threshold_ns=120 * s,
    )
    assert math.isnan(without_tail["ret_1min_mid"][0])
    assert stats["per_horizon"]["ret_1min_mid"]["null_past_end"] == 1, (
        "without D+1 the tail of every day is null_past_end -- 0.68 % of a "
        "real day at h=10min, which is the whole reason for the rule"
    )


def test_the_tail_is_needed_for_every_horizon_in_the_catalogue():
    """Not just the 10-minute diagnostic: the primary 10 s label loses its
    last rows too (0.0063 % of a real day)."""
    s = NS_PER_SECOND
    d_end = DAY_13_START + NS_PER_DAY
    decision_etime = np.array([d_end - 5 * s], np.int64)
    decision_mid = np.array([100.0], np.float64)
    d_etime, d_mid = decision_etime.copy(), decision_mid.copy()
    d1_etime = np.array([d_end + 300 * s, d_end + 700 * s], np.int64)
    d1_mid = np.array([110.0, 111.0], np.float64)

    labels, _ = compute_labels(
        decision_etime,
        decision_mid,
        *append_next_day_quotes(d_etime, d_mid, d1_etime, d1_mid),
        horizons_ns=LABEL_HORIZON_NS,
        gap_threshold_ns=700 * s,
    )
    for name in LABEL_HORIZON_NS:
        assert not math.isnan(labels[name][0]), f"{name} needs the D+1 quote"
    assert labels["ret_10min_mid"][0] == pytest.approx(0.1), (
        "the 10-minute label's prevailing mid is a D+1 quote, five minutes "
        "into the next day"
    )

    without, _ = compute_labels(
        decision_etime,
        decision_mid,
        d_etime,
        d_mid,
        horizons_ns=LABEL_HORIZON_NS,
        gap_threshold_ns=700 * s,
    )
    for name in LABEL_HORIZON_NS:
        assert math.isnan(without[name][0])


# --------------------------------------------------------------------------
# The gates the loader brings with it
# --------------------------------------------------------------------------


def test_a_bad_next_day_blocks_the_build_rather_than_truncating_the_tail(
    tmp_path: Path,
):
    """D+1 is loaded through `store.load_curated`, so its own DQ pause
    applies: an unacknowledged failed D+1 stops D being built instead of
    quietly producing a day with a null tail."""
    from data.store import DQPauseError

    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    _issue_curated_day(lake_root, registry_root, "2026-09-13")
    manifest_id = _issue_curated_day(lake_root, registry_root, "2026-09-14")

    report = dq_report_path(lake_root, "2026-09-14")
    body = pl.read_parquet(report).with_columns(pl.lit("failed").alias("dq_status"))
    body.write_parquet(report, compression="zstd")
    assert (
        json.loads(
            (
                registry_root
                / "manifests"
                / f"{SYMBOL}.{STREAM}"
                / "by-date"
                / f"{SYMBOL}__{STREAM}__2026-09-14.json"
            ).read_text()
        )["manifest_id"]
        == manifest_id
    )

    with pytest.raises(DQPauseError):
        next_day_quote_series(
            SYMBOL, "2026-09-13", registry_root=registry_root, lake_root=lake_root
        )


def test_a_quarantined_next_day_is_refused_before_the_load(tmp_path: Path):
    """T-04-18: D's long-horizon labels are D+1's prices. Building D while
    D+1 is held out would write held-out prices into a readable tier."""
    from data.holdout import QuarantinedDateError

    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    _issue_curated_day(lake_root, registry_root, "2026-09-13")
    _issue_curated_day(lake_root, registry_root, "2026-09-14")

    holdout = registry_root / "holdout" / "holdout.json"
    holdout.parent.mkdir(parents=True, exist_ok=True)
    holdout.write_text(
        json.dumps(
            {
                "version": 1,
                "symbol": SYMBOL,
                "dates": ["2026-09-14"],
                "locked_at": DAY_13_START,
                "reason": "test",
            }
        )
    )

    with pytest.raises(QuarantinedDateError):
        next_day_quote_series(
            SYMBOL, "2026-09-13", registry_root=registry_root, lake_root=lake_root
        )
