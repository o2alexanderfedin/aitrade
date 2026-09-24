"""Daily data-quality report entrypoint (DATA-07): runs every
`data.dq.checks` function against a single `(symbol, date)` (or a date
range) and writes `lake_root()/dq/date=.../report.parquet` + a
`resync_windows.parquet` sidecar + a rendered `report.md`.

This module is the one place in the data lake allowed to read curated
Parquet DIRECTLY (`data.store.resolve_manifest` + `pl.read_parquet`), not
through `data.store.load_curated` -- it PRODUCES the `dq_status` signal
`load_curated`'s pause enforcement later reads; going through the enforced
loader here would be circular (the report would have to already exist to
read the report it is about to write). Read-only against `curated/` and
`curated_meta/` -- never writes into either tier (both stay someone
else's write-once/immutable territory); this module's own outputs
(`dq/date=.../*`) are freely re-computable/overwritable, unlike a curated
partition.

CLI: `python -m data.dq.report --symbol BTCUSDT --date 2026-09-12` or
`--range 2026-06-01 2026-09-15`, invoked via the project's `.venv` python
directly for a real range (never `uv run` for anything long-running, per
environment rules) -- `uv run --directory mvp python -m data.dq.report ...`
is fine for a single `--date` smoke run.

CARDINALITY (IN-01, 06-REVIEW.md): a `report.parquet` row is scoped to
`(date, symbol, stream, manifest_id)`, NOT just `(date, symbol, stream)` --
`build_feature_report_rows_for_date` emits one full features row set PER
MANIFEST that has ever covered that date (a schema-v1-then-v2 rebuild
produces two). Every reader that groups or filters by `(date, symbol,
stream)` alone, without also matching `manifest_id`, will silently
double-count or read another manifest's verdict. `data.store
._dq_verdict_for_date` is the one confirmed-correct consumer (filters on
`manifest_id`); `write_report`/`build_report_rows_for_date` (this module)
regenerates the whole file wholesale on every run rather than reading its
own prior output, so it is a producer only, never a stale-shape reader.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl

from data.backfill.downloader import daterange
from data.capture.gap_ledger import GapLedger
from data.dq.feature_checks import (
    check_feature_asof_convention,
    check_feature_label_coverage,
    check_feature_quantization,
    check_feature_row_filters,
    check_feature_warmup,
    check_feature_window,
    feature_build_stats_path,
)
from data.dq.checks import (
    DQThresholds,
    check_crossed_locked_book,
    check_etime_plausibility,
    check_event_time_plausibility,
    check_gap_coverage,
    check_l1_sparsity,
    check_na_placeholder,
    check_probable_loss,
    check_reconciliation,
    check_rtime_plausibility,
    load_dq_thresholds,
    resync_windows_for_date,
)
from data.lake_paths import LAKE_REGISTRY_ROOT
from data.lake_paths import lake_root as default_lake_root
from data.store import (
    CURATED_TIER,
    FEATURES_TIER,
    ManifestHashMismatch,
    ManifestTierError,
    by_date_index_path,
    manifest_source,
    manifests_for_dataset,
    resolve_manifest,
)

#: The CURATED streams this report loops over, and only those. A
#: `report.parquet` row's `stream` may also be `"features"` (see
#: `build_feature_report_rows_for_date`), but that value must NEVER be
#: added here: this tuple drives the curated two-stream loop, and a third
#: entry would send `check_l1_sparsity` at a features manifest.
STREAMS: tuple[str, ...] = ("trade", "bookTicker")

DEFAULT_GAP_LEDGER_DATA_ROOT = "/Volumes/ProjectsSSD/aihedgefund/capture"

REPORT_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Utf8,
    "symbol": pl.Utf8,
    "stream": pl.Utf8,
    #: The manifest this row scored (03-REVIEW-ITER2.md WR-15): the pause
    #: check in `data.store` judges a manifest only by its own rows.
    "manifest_id": pl.Utf8,
    "check": pl.Utf8,
    "dq_status": pl.Utf8,
    "value": pl.Float64,
    "count": pl.Int64,
    "detail": pl.Utf8,
}

__all__ = [
    "dq_report_dir",
    "dq_report_path",
    "dq_resync_windows_path",
    "dq_report_markdown_path",
    "build_stats_path",
    "build_report_rows_for_date",
    "build_feature_report_rows_for_date",
    "build_stats_problem",
    "normalize_row",
    "render_report_markdown",
    "write_report",
    "main",
]


def dq_report_dir(lake_root: Path, date: str) -> Path:
    """Single source of truth for a date's DQ report directory."""
    return Path(lake_root) / "dq" / f"date={date}"


def dq_report_path(lake_root: Path, date: str) -> Path:
    return dq_report_dir(lake_root, date) / "report.parquet"


def dq_resync_windows_path(lake_root: Path, date: str) -> Path:
    return dq_report_dir(lake_root, date) / "resync_windows.parquet"


def dq_report_markdown_path(lake_root: Path, date: str) -> Path:
    return dq_report_dir(lake_root, date) / "report.md"


def build_stats_path(lake_root: Path, symbol: str, stream: str, date: str) -> Path:
    """Same path convention as `data.ingest.curated_build`'s private
    `_curated_build_stats_path` -- duplicated here as a small, stable path
    constant rather than importing a leading-underscore name across
    package boundaries."""
    return (
        Path(lake_root)
        / "curated_meta"
        / f"symbol={symbol}"
        / f"stream={stream}"
        / f"date={date}"
        / "build_stats.json"
    )


def _load_manifest_for_date(
    symbol: str, stream: str, date: str, *, registry_root: Path, lake_root: Path
) -> dict | None:
    """Resolve the by-date index -> manifest for `(symbol, stream, date)`,
    or `None` if no curated partition exists for it yet (no manifest
    issued -- e.g. bookTicker before 2026-09-12, or a not-yet-built day)."""
    dataset = f"{symbol}.{stream}"
    idx_path = by_date_index_path(registry_root, dataset, symbol, stream, date)
    if not idx_path.exists():
        return None
    manifest_id = json.loads(idx_path.read_text())["manifest_id"]
    return resolve_manifest(
        manifest_id,
        dataset,
        registry_root=registry_root,
        lake_root=lake_root,
        expected_tier=CURATED_TIER,
    )


def build_stats_problem(build_stats: dict | None, manifest: dict) -> str | None:
    """`None` if `build_stats` exists and belongs to `manifest`'s build, else
    why not. Bound by `manifest_id`, or -- for a build that crashed after
    issuing the manifest but before recording its id -- by the partition
    sha256 written before issuance. A stats file from an earlier build of
    the same date (e.g. before a supersede) is stale, not evidence."""
    if build_stats is None:
        return "build_stats.json missing -- reconciliation/NA checks cannot run"
    if build_stats.get("manifest_id") == manifest["manifest_id"]:
        return None
    partition_hashes = {p["sha256"] for p in manifest["partitions"]}
    if build_stats.get("partition_sha256") in partition_hashes:
        return None
    return (
        "build_stats.json belongs to a different build (manifest_id="
        f"{build_stats.get('manifest_id')}) than manifest {manifest['manifest_id']}"
    )


def _read_curated(
    manifest: dict, lake_root: Path, columns: list[str] | None = None
) -> pl.DataFrame:
    frames = [
        pl.read_parquet(Path(lake_root) / part["path"], columns=columns)
        for part in manifest["partitions"]
    ]
    return pl.concat(frames, how="vertical")


def _column_range(
    manifest: dict, lake_root: Path, column: str
) -> tuple[int | None, int | None]:
    """`(min, max)` of the manifest's non-null `column` values, or
    `(None, None)` when its partitions have no such column or no value.

    One lazy scan of that single column; Parquet column statistics make it
    cheap even on a 42M-row day (measured: 1.0 s for the largest real one)."""
    frames = [
        pl.scan_parquet(Path(lake_root) / part["path"])
        for part in manifest["partitions"]
    ]
    if any(column not in f.collect_schema().names() for f in frames):
        return None, None
    bounds = (
        pl.concat([f.select(column) for f in frames], how="vertical")
        .select(pl.col(column).min().alias("lo"), pl.col(column).max().alias("hi"))
        .collect()
        .row(0)
    )
    return (
        int(bounds[0]) if bounds[0] is not None else None,
        int(bounds[1]) if bounds[1] is not None else None,
    )


def _event_time_range(manifest: dict, lake_root: Path) -> tuple[int | None, int | None]:
    """`(min, max)` of the manifest's non-null `event_time` values."""
    return _column_range(manifest, lake_root, "event_time")


def _rtime_stats(manifest: dict, lake_root: Path) -> dict[str, int | None]:
    """`rtime`'s bounds plus the `rtime - etime` skew bounds the
    capture-sourced branch of `check_rtime_plausibility` needs.

    The skew is computed only when BOTH columns exist; a partition with
    `rtime` but no `etime` returns `skew_min/skew_max = None`, which the
    check treats as "could not judge it" -> failed, never a silent ok."""
    rtime_min, rtime_max = _column_range(manifest, lake_root, "rtime")
    stats: dict[str, int | None] = {
        "rtime_min": rtime_min,
        "rtime_max": rtime_max,
        "skew_min": None,
        "skew_max": None,
    }
    if rtime_min is None:
        return stats
    frames = [
        pl.scan_parquet(Path(lake_root) / part["path"])
        for part in manifest["partitions"]
    ]
    if any(
        {"rtime", "etime"} - set(f.collect_schema().names()) for f in frames
    ):  # missing either column
        return stats
    skew = pl.col("rtime") - pl.col("etime")
    bounds = (
        pl.concat([f.select("rtime", "etime") for f in frames], how="vertical")
        .select(skew.min().alias("lo"), skew.max().alias("hi"))
        .collect()
        .row(0)
    )
    stats["skew_min"] = int(bounds[0]) if bounds[0] is not None else None
    stats["skew_max"] = int(bounds[1]) if bounds[1] is not None else None
    return stats


def build_report_rows_for_date(
    symbol: str,
    date: str,
    *,
    lake_root: Path,
    registry_root: Path,
    thresholds: DQThresholds,
    ledger_df: pl.DataFrame,
) -> list[dict]:
    """Run every applicable check for `(symbol, date)` across both streams.

    A stream with no manifest for this date (no curated partition exists)
    emits NO rows for that stream -- absence of curated data is not itself
    a finding this report makes (`store.py`'s pause enforcement treats
    "no report row at all for this (symbol,stream,date)" as its own
    "missing" pause status, which is the correct signal for a date nobody
    has built yet; it is a DIFFERENT case from a date that WAS built and
    reported on but came back n/a on every check).
    """
    rows: list[dict] = []
    for stream in STREAMS:
        manifest = _load_manifest_for_date(
            symbol, stream, date, registry_root=registry_root, lake_root=lake_root
        )
        if manifest is None:
            continue
        stream_rows_start = len(rows)

        stats_path = build_stats_path(lake_root, symbol, stream, date)
        build_stats = (
            json.loads(stats_path.read_text()) if stats_path.exists() else None
        )
        stats_problem = (
            build_stats_problem(build_stats, manifest) if stream == "trade" else None
        )
        archive_sourced_trades = (
            stream == "trade"
            and stats_problem is None
            and build_stats.get("chosen_source") == "archive"
        )

        if archive_sourced_trades:
            # 03-REVIEW.md WR-04(a): a capture outage says nothing about an
            # archive-sourced trade day's completeness (L1 for the same
            # outage is still covered by the bookTicker stream's row).
            gap = {
                "check": "gap_coverage",
                "dq_status": "n/a",
                "reason": "archive-sourced trade day: capture outages do not affect it",
            }
        else:
            gap = check_gap_coverage(ledger_df, date, thresholds)
        rows.append({"date": date, "symbol": symbol, "stream": stream, **gap})

        etime_check = check_etime_plausibility(manifest, date, thresholds)
        rows.append({"date": date, "symbol": symbol, "stream": stream, **etime_check})
        event_time_check = check_event_time_plausibility(
            _event_time_range(manifest, lake_root), date, thresholds
        )
        rows.append(
            {"date": date, "symbol": symbol, "stream": stream, **event_time_check}
        )
        rtime_check = check_rtime_plausibility(
            _rtime_stats(manifest, lake_root),
            manifest_source(manifest),
            manifest,
            thresholds,
        )
        rows.append({"date": date, "symbol": symbol, "stream": stream, **rtime_check})

        if archive_sourced_trades:
            if build_stats.get("capture_available"):
                loss = {
                    "check": "probable_loss",
                    "dq_status": "n/a",
                    "reason": "capture overlaps this day: reconciliation is the loss detector",
                }
            else:
                loss = check_probable_loss(
                    _read_curated(manifest, lake_root, columns=["trade_id", "etime"]),
                    thresholds,
                )
            rows.append({"date": date, "symbol": symbol, "stream": stream, **loss})

        if stream == "trade":
            if stats_problem is not None:
                # Fail closed (03-REVIEW.md WR-02): without the precomputed
                # stats the reconciliation and NA checks cannot run, and
                # their absence must never read as "ok".
                rows.append(
                    {
                        "date": date,
                        "symbol": symbol,
                        "stream": stream,
                        "check": "build_stats",
                        "dq_status": "failed",
                        "reason": stats_problem,
                    }
                )
            else:
                recon = check_reconciliation(build_stats, thresholds)
                rows.append({"date": date, "symbol": symbol, "stream": stream, **recon})
                na = check_na_placeholder(build_stats, thresholds)
                rows.append({"date": date, "symbol": symbol, "stream": stream, **na})
        else:  # bookTicker
            df = _read_curated(manifest, lake_root)
            crossed = check_crossed_locked_book(df)
            rows.append({"date": date, "symbol": symbol, "stream": stream, **crossed})
            sparsity = check_l1_sparsity(df, thresholds, date=date)
            rows.append({"date": date, "symbol": symbol, "stream": stream, **sparsity})

        for row in rows[stream_rows_start:]:
            row["manifest_id"] = manifest["manifest_id"]

    # One report.parquet per date carries all three streams. Assembled
    # HERE, in the same pass, rather than appended by a separate writer:
    # `write_report` rebuilds the file wholesale, so a features row that is
    # not recomputed on every regeneration is a features row that the next
    # curated regen deletes -- leaving a report that looks perfectly
    # healthy while `load_features` is paused on `missing` forever
    # (T-04-08).
    rows.extend(
        build_feature_report_rows_for_date(
            symbol,
            date,
            lake_root=lake_root,
            registry_root=registry_root,
            thresholds=thresholds,
        )
    )
    return rows


FEATURE_CHECKS = (
    check_feature_row_filters,
    check_feature_label_coverage,
    check_feature_quantization,
    check_feature_warmup,
    check_feature_window,
    check_feature_asof_convention,
)


def _feature_report_rows_for_manifest(
    symbol: str,
    date: str,
    manifest: dict,
    *,
    lake_root: Path,
    registry_root: Path,
    thresholds: DQThresholds,
) -> list[dict]:
    """Every feature-tier check row for ONE manifest dict already discovered
    by `store.manifests_for_dataset` -- re-verified through
    `resolve_manifest` before anything else about it is trusted (integrity
    first, exactly as the by-date-pointer path always did), and its
    `build_stats.json` looked up at ITS OWN `schema_version` (T-06-02):
    `feature_build_stats_path(..., schema_version=manifest["schema_version"])`
    is what makes a v1 manifest's checks read v1's stats file even after a
    v2 build has moved the global `FEATURE_SCHEMA_VERSION` on and started
    writing `build_stats-v2.json` -- the bug this task fixes is exactly a
    v1 manifest silently being judged against v2's (unrelated) stats.
    """
    dataset = f"{symbol}.{FEATURES_TIER}"
    try:
        resolved = resolve_manifest(
            manifest.get("manifest_id"),
            dataset,
            registry_root=registry_root,
            lake_root=lake_root,
            expected_tier=FEATURES_TIER,
        )
    except (
        ManifestHashMismatch,
        ManifestTierError,
        OSError,
        KeyError,
        TypeError,
    ) as exc:
        # CONTAINED TO THE FEATURE ROWS (04-REVIEW.md WR-06). Unguarded,
        # this propagated out of `build_report_rows_for_date`, out of
        # `write_report` and out of `main`'s date loop -- so a corrupt
        # DOWNSTREAM partition stopped the UPSTREAM tier's verdict being
        # refreshed, and the stale report left behind kept vouching for
        # curated manifests while the operator saw a traceback naming
        # `features`. One `failed` row is still fail-closed: it pauses
        # `load_features` on this date, which is the correct verdict, and
        # the curated half of the report regenerates. `manifest_id` stays
        # `None`: the manifest did not resolve, so no id may be claimed
        # for it, even though the directory scan that found this dict
        # already knows what it CLAIMS its id is.
        return [
            {
                "date": date,
                "symbol": symbol,
                "stream": FEATURES_TIER,
                "check": "feature_manifest",
                "dq_status": "failed",
                "reason": (
                    f"features manifest for {date} does not resolve: "
                    f"{exc.__class__.__name__}: {exc}"
                ),
                "manifest_id": None,
            }
        ]

    stats_path = feature_build_stats_path(
        lake_root, symbol, date, schema_version=resolved["schema_version"]
    )
    build_stats = json.loads(stats_path.read_text()) if stats_path.exists() else None
    stats_problem = build_stats_problem(build_stats, resolved)

    base = {"date": date, "symbol": symbol, "stream": FEATURES_TIER}
    if stats_problem is not None:
        rows = [
            {
                **base,
                "check": "feature_build_stats",
                "dq_status": "failed",
                "reason": stats_problem,
            }
        ]
    else:
        rows = [{**base, **check(build_stats, thresholds)} for check in FEATURE_CHECKS]
    for row in rows:
        row["manifest_id"] = resolved["manifest_id"]
    return rows


def build_feature_report_rows_for_date(
    symbol: str,
    date: str,
    *,
    lake_root: Path,
    registry_root: Path,
    thresholds: DQThresholds,
) -> list[dict]:
    """Every feature-tier check for `(symbol, date)`, across EVERY manifest
    that has ever covered this date -- not only the by-date pointer's
    CURRENT one (T-06-02) -- or `[]` when none has.

    Discovery is `store.manifests_for_dataset`, a directory scan, not the
    by-date pointer: the pointer is a hint about which manifest is
    "current", never the sole path to a manifest that still exists and
    still needs its own verdict. This is what keeps a superseded manifest's
    `load_features` call resolving after a rebuild issues a new one for the
    same date -- without it, `write_report`'s wholesale rewrite of
    `report.parquet` silently drops the superseded manifest's rows and
    `store._dq_verdict_for_date` reports it `missing` forever.

    Absence is not a finding, matching the curated convention above: a date
    nobody has built features for emits no features rows. A date that WAS
    built but whose `build_stats.json` is missing or belongs to another
    build is a different case entirely -- one `failed` row per manifest,
    fail-closed, because the checks it would have carried cannot run.
    """
    dataset = f"{symbol}.{FEATURES_TIER}"
    manifests = manifests_for_dataset(registry_root, dataset)
    matching = [
        m
        for m in manifests
        if any(p.get("date") == date for p in m.get("partitions", []))
    ]
    if not matching:
        return []
    rows: list[dict] = []
    for manifest in matching:
        rows.extend(
            _feature_report_rows_for_manifest(
                symbol,
                date,
                manifest,
                lake_root=lake_root,
                registry_root=registry_root,
                thresholds=thresholds,
            )
        )
    return rows


def normalize_row(row: dict) -> dict:
    """Map one check-function dict (heterogeneous extra fields --
    `value_seconds`/`value_pct`/`count`/`etime_min`+`etime_max`/
    `missing_from_*_pct`/`dropped`) onto `REPORT_SCHEMA`'s fixed columns,
    so `report.parquet` has one stable schema across all six checks --
    `store.py`'s pause enforcement only ever needs to filter on
    `(symbol, stream, dq_status)`, never on a check-specific field."""
    value = None
    for key in ("value_seconds", "value_pct"):
        if row.get(key) is not None:
            value = float(row[key])
            break

    detail_parts: list[str] = []
    if row.get("missing_from_capture_pct") is not None:
        detail_parts.append(
            f"missing_from_capture_pct={row['missing_from_capture_pct']:.4f}"
        )
    if row.get("missing_from_archive_pct") is not None:
        detail_parts.append(
            f"missing_from_archive_pct={row['missing_from_archive_pct']:.4f}"
        )
    if row.get("dropped") is not None:
        detail_parts.append(f"dropped={row['dropped']}")
    if row.get("reason") is not None:
        detail_parts.append(f"reason={row['reason']}")
    if "etime_min" in row:
        detail_parts.append(
            f"etime_min={row['etime_min']} etime_max={row['etime_max']}"
        )
    if "event_time_min" in row:
        detail_parts.append(
            f"event_time_min={row['event_time_min']} "
            f"event_time_max={row['event_time_max']}"
        )
    if "rtime_min" in row:
        detail_parts.append(
            f"rtime_min={row['rtime_min']} rtime_max={row['rtime_max']}"
        )
    if row.get("rtime_source") is not None:
        detail_parts.append(f"rtime_source={row['rtime_source']}")

    return {
        "date": row["date"],
        "symbol": row["symbol"],
        "stream": row["stream"],
        "manifest_id": row["manifest_id"],
        "check": row["check"],
        "dq_status": row["dq_status"],
        "value": value,
        "count": row.get("count"),
        "detail": "; ".join(detail_parts) if detail_parts else None,
    }


def render_report_markdown(
    date: str, symbol: str, normalized_rows: list[dict], resync_windows: pl.DataFrame
) -> str:
    """`normalized_rows` must already be `normalize_row`-shaped (fixed
    `value`/`count`/`detail` columns) -- render_report_markdown never sees
    a raw check dict's heterogeneous `value_seconds`/`value_pct`/etc fields."""
    rows = normalized_rows
    lines = [f"# DQ report: {symbol} {date}", ""]
    if not rows:
        lines.append("No curated data for this date (no manifest for any stream).")
    else:
        lines.append("| stream | check | dq_status | value | count | detail |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in rows:
            lines.append(
                f"| {r['stream']} | {r['check']} | {r['dq_status']} | "
                f"{r['value'] if r['value'] is not None else ''} | "
                f"{r['count'] if r['count'] is not None else ''} | "
                f"{r['detail'] or ''} |"
            )
    lines.append("")
    lines.append(
        f"## resync_windows ({resync_windows.height} outage(s) resumed this day)"
    )
    if resync_windows.height:
        lines.append(
            "| gap_start_rtime | gap_end_rtime | gap_end_etime_approx | "
            "warmup_end_etime_approx |"
        )
        lines.append("| --- | --- | --- | --- |")
        for row in resync_windows.iter_rows(named=True):
            lines.append(
                f"| {row['gap_start_rtime']} | {row['gap_end_rtime']} | "
                f"{row['gap_end_etime_approx']} | {row['warmup_end_etime_approx']} |"
            )
    lines.append("")
    return "\n".join(lines)


def _write_parquet_atomic(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp_path, compression="zstd")
    tmp_path.replace(path)


def write_report(
    symbol: str,
    date: str,
    *,
    lake_root: Path,
    registry_root: Path,
    thresholds: DQThresholds,
    ledger_df: pl.DataFrame,
) -> Path:
    """Compute and write `report.parquet` + `resync_windows.parquet` +
    `report.md` for `(symbol, date)`. Freely re-computable/overwritable
    (never write-once) -- returns the report.parquet path."""
    raw_rows = build_report_rows_for_date(
        symbol,
        date,
        lake_root=lake_root,
        registry_root=registry_root,
        thresholds=thresholds,
        ledger_df=ledger_df,
    )
    normalized = [normalize_row(r) for r in raw_rows]
    report_df = pl.DataFrame(normalized, schema=REPORT_SCHEMA)
    report_path = dq_report_path(lake_root, date)
    _write_parquet_atomic(report_df, report_path)

    resync_df = resync_windows_for_date(
        ledger_df, date, thresholds.resync_warmup.seconds
    )
    _write_parquet_atomic(resync_df, dq_resync_windows_path(lake_root, date))

    markdown = render_report_markdown(date, symbol, normalized, resync_df)
    md_path = dq_report_markdown_path(lake_root, date)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown)

    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="single YYYY-MM-DD date")
    group.add_argument(
        "--range", nargs=2, metavar=("START", "END"), help="inclusive date range"
    )
    parser.add_argument("--lake-root", default=None)
    parser.add_argument("--registry-root", default=None)
    parser.add_argument(
        "--capture-data-root",
        default=DEFAULT_GAP_LEDGER_DATA_ROOT,
        help="capture daemon's data_root (read-only; only gap_ledger/ledger.parquet is read)",
    )
    args = parser.parse_args(argv)

    lake_root_path = Path(args.lake_root) if args.lake_root else default_lake_root()
    registry_root_path = (
        Path(args.registry_root) if args.registry_root else LAKE_REGISTRY_ROOT
    )
    thresholds = load_dq_thresholds()
    ledger_df = GapLedger(Path(args.capture_data_root)).read_all()

    dates = [args.date] if args.date else daterange(args.range[0], args.range[1])
    for date in dates:
        path = write_report(
            args.symbol,
            date,
            lake_root=lake_root_path,
            registry_root=registry_root_path,
            thresholds=thresholds,
            ledger_df=ledger_df,
        )
        print(f"wrote DQ report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
