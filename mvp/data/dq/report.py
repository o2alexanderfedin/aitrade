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
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl

from data.backfill.downloader import daterange
from data.capture.gap_ledger import GapLedger
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
    load_dq_thresholds,
    resync_windows_for_date,
)
from data.lake_paths import LAKE_REGISTRY_ROOT
from data.lake_paths import lake_root as default_lake_root
from data.store import CURATED_TIER, by_date_index_path, resolve_manifest

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


def _event_time_range(manifest: dict, lake_root: Path) -> tuple[int | None, int | None]:
    """`(min, max)` of the manifest's non-null `event_time` values, or
    `(None, None)` when its partitions have no such column or no value."""
    frames = [
        pl.scan_parquet(Path(lake_root) / part["path"])
        for part in manifest["partitions"]
    ]
    if any("event_time" not in f.collect_schema().names() for f in frames):
        return None, None
    bounds = (
        pl.concat([f.select("event_time") for f in frames], how="vertical")
        .select(
            pl.col("event_time").min().alias("lo"),
            pl.col("event_time").max().alias("hi"),
        )
        .collect()
        .row(0)
    )
    return (
        int(bounds[0]) if bounds[0] is not None else None,
        int(bounds[1]) if bounds[1] is not None else None,
    )


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
