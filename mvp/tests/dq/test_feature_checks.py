"""Feature-tier DQ checks, and the report rows that carry them.

Every check reads PRECOMPUTED values out of the build's own
`build_stats.json`, the same way `check_reconciliation`/`check_na_placeholder`
do and for the same reason: the written artifact structurally cannot answer
questions about rows that were filtered out before it was written.

Fixture values are the real 2026-09-13 numbers wherever one exists
(04-CONTEXT.md D-04-12/D-04-17, 04-RESEARCH-NOTES.md), so a test that
passes is a test that passed on a shape the lake actually produces.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from data.dq.checks import load_dq_thresholds
from data.dq.feature_checks import (
    FEATURE_BUILD_STATS_KEYS,
    check_feature_asof_convention,
    check_feature_label_coverage,
    check_feature_quantization,
    check_feature_row_filters,
    check_feature_warmup,
    check_feature_window,
    feature_build_stats_path,
)
from data.dq.report import (
    build_feature_report_rows_for_date,
    build_stats_path,
    dq_report_path,
    write_report,
)
from features.tier import issue_feature_manifest, write_feature_partition
from tests.fixtures.feature_tier import (
    DATE,
    SYMBOL,
    feature_frame,
    issue_curated_day,
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

#: A complete, healthy stats dict shaped like the real 2026-09-13 day.
REAL_DAY_STATS: dict = {
    "n_quote_rows": 17_167_290,
    "n_trade_rows": 1_409_705,
    "n_events": 18_576_995,
    "n_decision_rows": 6_864_853,
    "na_placeholder_excluded": 0,
    "unknown_side_rows": 0,
    "null_primary_label_rows": 432,
    "ret_10s_mid_zero_fraction": 0.297,
    "ret_1s_mid_zero_fraction": 0.625,
    "warmup_rows": 1,
    "post_gap_warmup_rows": 0,
    "resync_sidecar_present": True,
    "max_window_occupancy": 5_092,
    "window_capacity": 1 << 16,
    "window_overflow": False,
    "empty_window_rows": 784_343,
    "asof_convention_disagreement_rows": 17_848,
}


def _stats(**overrides) -> dict:
    return {**REAL_DAY_STATS, **overrides}


def _row(check_result: dict) -> dict:
    return check_result


# --------------------------------------------------------------------------
# The checks themselves
# --------------------------------------------------------------------------


def test_row_filter_counts_are_reported_not_silent():
    result = check_feature_row_filters(
        _stats(na_placeholder_excluded=4_270, unknown_side_rows=0), THRESHOLDS
    )
    assert result["check"] == "feature_row_filters"
    assert result["dq_status"] == "ok"
    detail = result["reason"]
    assert "na_placeholder_excluded=4270" in detail
    assert "unknown_side_rows=0" in detail
    assert "n_trade_rows=1409705" in detail
    assert result["count"] == 4_270
    assert result["value_pct"] == pytest.approx(4_270 / 1_409_705 * 100)


@pytest.mark.parametrize(
    "missing", ["na_placeholder_excluded", "unknown_side_rows", "n_trade_rows"]
)
def test_row_filters_fail_closed_on_a_missing_key(missing: str):
    stats = _stats()
    del stats[missing]
    result = check_feature_row_filters(stats, THRESHOLDS)
    assert result["dq_status"] == "failed", (
        "a stats dict that cannot answer the question must never read as ok"
    )
    assert missing in result["reason"]


def test_label_coverage_degrades_past_the_threshold():
    ok = check_feature_label_coverage(
        _stats(n_decision_rows=1_000_000, null_primary_label_rows=19_000), THRESHOLDS
    )
    assert ok["dq_status"] == "ok"
    assert ok["value_pct"] == pytest.approx(1.9)

    bad = check_feature_label_coverage(
        _stats(n_decision_rows=1_000_000, null_primary_label_rows=21_000), THRESHOLDS
    )
    assert bad["dq_status"] == "degraded"
    assert bad["value_pct"] == pytest.approx(2.1)
    assert bad["count"] == 21_000


def test_label_coverage_fails_closed_on_a_missing_key():
    stats = _stats()
    del stats["null_primary_label_rows"]
    assert check_feature_label_coverage(stats, THRESHOLDS)["dq_status"] == "failed"


def test_label_quantization_is_recorded_and_never_pauses():
    result = check_feature_quantization(_stats(), THRESHOLDS)
    assert result["dq_status"] == "ok"
    assert result["value_pct"] == pytest.approx(29.7)
    assert "ret_10s_mid_zero_fraction=0.297" in result["reason"]
    assert "ret_1s_mid_zero_fraction=0.625" in result["reason"]

    # A venue property, not a defect (D-04-17): even an extreme value is ok.
    extreme = check_feature_quantization(
        _stats(ret_10s_mid_zero_fraction=0.99, ret_1s_mid_zero_fraction=0.999),
        THRESHOLDS,
    )
    assert extreme["dq_status"] == "ok"


def test_ring_overflow_is_a_failed_check():
    overflowed = check_feature_window(_stats(window_overflow=True), THRESHOLDS)
    assert overflowed["dq_status"] == "failed"

    fine = check_feature_window(_stats(), THRESHOLDS)
    assert fine["dq_status"] == "ok"
    assert fine["count"] == 5_092
    assert "empty_window_rows=784343" in fine["reason"]


def test_warmup_and_asof_convention_are_informational():
    warmup = check_feature_warmup(_stats(), THRESHOLDS)
    assert warmup["dq_status"] == "ok"
    assert warmup["count"] == 1
    assert "resync_sidecar_present=True" in warmup["reason"]

    asof = check_feature_asof_convention(_stats(), THRESHOLDS)
    assert asof["dq_status"] == "ok"
    assert asof["value_pct"] == pytest.approx(17_848 / 6_864_853 * 100, rel=1e-6)


def test_every_check_names_the_stats_keys_it_needs():
    """The build (Plan 05) writes `build_stats.json`; this frozenset is the
    contract between the two, in one place rather than scattered across six
    `.get` calls."""
    stats = _stats()
    assert FEATURE_BUILD_STATS_KEYS <= set(stats)
    for key in sorted(FEATURE_BUILD_STATS_KEYS):
        partial = {k: v for k, v in stats.items() if k != key}
        statuses = [
            check(partial, THRESHOLDS)["dq_status"]
            for check in (
                check_feature_row_filters,
                check_feature_label_coverage,
                check_feature_quantization,
                check_feature_warmup,
                check_feature_window,
                check_feature_asof_convention,
            )
        ]
        assert "failed" in statuses, (
            f"dropping {key!r} from build_stats left every check non-failed"
        )


# --------------------------------------------------------------------------
# ...and their report rows
# --------------------------------------------------------------------------


def _build_a_feature_day(lake_root: Path, registry_root: Path, stats: dict) -> dict:
    entry = write_feature_partition(
        feature_frame(), lake_root=lake_root, symbol=SYMBOL, date=DATE
    )
    manifest = issue_feature_manifest(
        symbol=SYMBOL,
        date=DATE,
        partition_entry=entry,
        curated_manifests=[],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    stats_path = feature_build_stats_path(lake_root, SYMBOL, DATE)
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps({"manifest_id": manifest["manifest_id"], **stats}))
    return manifest


def test_features_rows_absent_when_no_features_manifest_for_the_date(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    assert (
        build_feature_report_rows_for_date(
            SYMBOL,
            DATE,
            lake_root=lake_root,
            registry_root=registry_root,
            thresholds=THRESHOLDS,
        )
        == []
    )


def test_feature_rows_fail_closed_without_build_stats(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    manifest = _build_a_feature_day(lake_root, registry_root, _stats())
    feature_build_stats_path(lake_root, SYMBOL, DATE).unlink()

    rows = build_feature_report_rows_for_date(
        SYMBOL,
        DATE,
        lake_root=lake_root,
        registry_root=registry_root,
        thresholds=THRESHOLDS,
    )
    assert [r["dq_status"] for r in rows] == ["failed"]
    assert rows[0]["check"] == "feature_build_stats"
    assert rows[0]["manifest_id"] == manifest["manifest_id"]


def test_features_rows_survive_a_report_regeneration(tmp_path: Path):
    """`write_report` rebuilds a date's report.parquet WHOLESALE, so
    "survive" means "are recomputed every time", not "are appended to".
    A features manifest with no rows of its own is `missing` in
    `store._dq_verdict_for_date`, which pauses `load_features` permanently
    -- this test is what stands between the tier and that state."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    issue_curated_day(lake_root, registry_root, "trade", DATE)
    issue_curated_day(lake_root, registry_root, "bookTicker", DATE)
    trade_stats = build_stats_path(lake_root, SYMBOL, "trade", DATE)
    trade_stats.parent.mkdir(parents=True, exist_ok=True)
    trade_manifest_id = json.loads(
        (
            registry_root
            / "manifests"
            / f"{SYMBOL}.trade"
            / "by-date"
            / f"{SYMBOL}__trade__{DATE}.json"
        ).read_text()
    )["manifest_id"]
    trade_stats.write_text(
        json.dumps(
            {
                "manifest_id": trade_manifest_id,
                "reconciliation_missing_from_capture": 0,
                "reconciliation_missing_from_archive": 0,
                "reconciliation_overlap_rows": 100,
                "na_placeholder_dropped": 0,
                "na_placeholder_rate": 0.0,
            }
        )
    )
    features = _build_a_feature_day(lake_root, registry_root, _stats())

    ledger = pl.DataFrame(schema=GAP_LEDGER_SCHEMA)
    args = dict(
        lake_root=lake_root,
        registry_root=registry_root,
        thresholds=THRESHOLDS,
        ledger_df=ledger,
    )
    write_report(SYMBOL, DATE, **args)
    first = pl.read_parquet(dq_report_path(lake_root, DATE))
    write_report(SYMBOL, DATE, **args)  # regenerate, wholesale
    second = pl.read_parquet(dq_report_path(lake_root, DATE))

    for report in (first, second):
        feature_rows = report.filter(pl.col("stream") == "features")
        assert feature_rows.height >= 6, (
            "a features manifest with no rows of its own is `missing`, which "
            "pauses load_features permanently"
        )
        assert set(feature_rows["manifest_id"].to_list()) == {features["manifest_id"]}
        assert set(feature_rows["check"].to_list()) >= {
            "feature_row_filters",
            "feature_label_coverage",
            "feature_quantization",
            "feature_warmup",
            "feature_window",
            "feature_asof_convention",
        }

    curated_first = first.filter(pl.col("stream") != "features").sort("stream", "check")
    curated_second = second.filter(pl.col("stream") != "features").sort(
        "stream", "check"
    )
    assert (
        curated_first["dq_status"].to_list() == curated_second["dq_status"].to_list()
    ), "the curated rows must keep their original verdicts"
    assert curated_first.height > 0


def test_features_is_not_in_the_curated_two_stream_loop():
    """`STREAMS` drives the curated loop; a third entry there would send
    `check_l1_sparsity` at a features manifest."""
    from data.dq.report import STREAMS

    assert STREAMS == ("trade", "bookTicker")


def test_a_corrupt_feature_partition_does_not_take_down_the_whole_date(
    tmp_path: Path,
):
    """04-REVIEW.md WR-06: the blast radius is the feature rows.

    `build_feature_report_rows_for_date` called `resolve_manifest` with no
    `try`/`except`, so a `ManifestHashMismatch` on a feature partition
    propagated out of `build_report_rows_for_date`, out of `write_report`
    and out of `main`'s date loop: that date's `report.parquet` was never
    rewritten AND every later date in a `--range` run was skipped. The
    direction of the dependency is what was new -- a downstream, fully
    rebuildable tier could block the refresh of the upstream tier's
    verdict, while the stale report left behind kept vouching for the
    curated manifests and `load_curated` carried on undisturbed.
    """
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    manifest = _build_a_feature_day(lake_root, registry_root, _stats())

    path = lake_root / manifest["partitions"][0]["path"]
    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    path.write_bytes(bytes(raw))

    rows = build_feature_report_rows_for_date(
        SYMBOL,
        DATE,
        lake_root=lake_root,
        registry_root=registry_root,
        thresholds=THRESHOLDS,
    )
    assert [r["dq_status"] for r in rows] == ["failed"], (
        "fail-closed: one failed row, which pauses load_features"
    )
    assert rows[0]["check"] == "feature_manifest"
    assert "does not resolve" in rows[0]["reason"]
    assert rows[0]["manifest_id"] is None, (
        "the manifest did not resolve, so no id may be claimed for it"
    )

    # ...and the row survives normalization into REPORT_SCHEMA, which is
    # what `write_report` does with it next.
    from data.dq.report import normalize_row

    assert normalize_row(rows[0])["dq_status"] == "failed"


def test_the_curated_half_of_the_report_still_regenerates(tmp_path: Path):
    """The other half of WR-06: the whole date's report is rebuilt, with
    the curated rows intact beside the one failed features row."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    issue_curated_day(lake_root, registry_root, "trade", DATE)
    issue_curated_day(lake_root, registry_root, "bookTicker", DATE)
    features = _build_a_feature_day(lake_root, registry_root, _stats())

    path = lake_root / features["partitions"][0]["path"]
    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    path.write_bytes(bytes(raw))

    write_report(
        SYMBOL,
        DATE,
        lake_root=lake_root,
        registry_root=registry_root,
        thresholds=THRESHOLDS,
        ledger_df=pl.DataFrame(schema=GAP_LEDGER_SCHEMA),
    )
    report = pl.read_parquet(dq_report_path(lake_root, DATE))
    assert set(report["stream"].to_list()) >= {"trade", "bookTicker", "features"}
    assert report.filter(pl.col("stream") == "features")["dq_status"].to_list() == [
        "failed"
    ]
    assert report.filter(pl.col("stream") == "bookTicker").height > 0, (
        "the curated verdict must still be refreshed -- a downstream tier "
        "must not be able to block it"
    )


def test_a_day_built_without_a_resync_sidecar_is_degraded_not_ok():
    """04-REVIEW.md WR-04: `post_gap_warmup_rows: 0` must not mean two
    different things.

    A build that runs before the DQ report exists tags no row at all, and
    the partition is write-once -- so the column is wrong forever and the
    only repair is a new manifest for a day already issued. The count it
    records is exactly the count a clean day records, so nothing in the
    report could tell them apart. `degraded` rather than `failed`: the day
    is acknowledgeable and its features are fine; what is not acceptable
    is reading the zero as "there was no outage" without saying so.
    """
    clean = check_feature_warmup(_stats(), THRESHOLDS)
    blind = check_feature_warmup(_stats(resync_sidecar_present=False), THRESHOLDS)

    assert clean["dq_status"] == "ok"
    assert blind["dq_status"] == "degraded"
    assert clean["count"] == blind["count"], (
        "the COUNTS are identical -- which is the whole finding; only the "
        "recorded fact distinguishes them"
    )
    assert "resync_sidecar_present=False" in blind["reason"]

    absent = {k: v for k, v in _stats().items() if k != "resync_sidecar_present"}
    assert check_feature_warmup(absent, THRESHOLDS)["dq_status"] == "failed", (
        "a stats file that cannot answer the question fails closed"
    )
