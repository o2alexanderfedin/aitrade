"""Full-window backfill orchestrator (03-03-PLAN.md): monthly-vs-daily
regime selection across `2026-06-01 -> yesterday`, extract-to-disk for
monthly zips, and skip-and-continue per-date idempotency so a re-run after
an interruption picks up where it left off rather than crashing on the
first already-ingested day.

Regime (03-CONTEXT.md, re-confirmed live this session): monthly zips exist
for 2026-06/07/08 (measured URL template + content-length, see Task 1's
commit message); daily zips from 2026-09-01 onward, landing ~T+7h after the
UTC day closes. Both share `BackfillClient` (`data/backfill/client.py`,
Plan 01) via its `granularity` field -- one download/checksum/idempotent-
marker code path, not two.

T-03-06 (Denial of Service, mitigated): a monthly zip's CSV member is
extracted to `backfill_staging_root()` once, then read via `pl.scan_csv`
and an explicit per-UTC-day `.filter(...).collect()` -- never one
in-memory `pl.DataFrame` for the whole month. Measured this session
(2026-06 file, 7,761,689,388 B decompressed): a single day-slice
`.filter().collect()` peaked at ~5.1GB RSS (`/usr/bin/time -l`), far below
the ~23GB naive-full-read estimate 03-RESEARCH.md's Pitfall 3 warns
against -- see the Task 1 commit message and 03-03-SUMMARY.md for the full
transcript.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import urllib.error
from pathlib import Path

import polars as pl

from data.backfill.client import BackfillClient, extract_expected_member, list_month
from data.ingest.normalize import (
    ARCHIVE_TRADES_SCHEMA_OVERRIDES,
    normalize_archive_frame,
    raw_partition_exists,
    write_raw_partition,
)
from data.lake_paths import backfill_staging_root, lake_root
from data.unit_registry import UnitRegistryEntry, get_unit_entry

logger = logging.getLogger(__name__)

MONTHLY_YEAR_MONTHS: list[str] = ["2026-06", "2026-07", "2026-08"]
DAILY_REGIME_START = "2026-09-01"

_DAILY_ZIP_DATE_RE = re.compile(r"-trades-(\d{4}-\d{2}-\d{2})\.zip$")

__all__ = [
    "MONTHLY_YEAR_MONTHS",
    "DAILY_REGIME_START",
    "daterange",
    "month_dates",
    "day_bounds_ms",
    "published_daily_dates",
    "existing_partition_row_count",
    "ingest_daily_date",
    "ingest_monthly",
    "run_backfill",
    "main",
]


def daterange(start: str, end: str) -> list[str]:
    """Inclusive list of `YYYY-MM-DD` dates from `start` to `end` (empty if
    `end < start`)."""
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    if end_d < start_d:
        return []
    n = (end_d - start_d).days
    return [(start_d + dt.timedelta(days=i)).isoformat() for i in range(n + 1)]


def month_dates(year_month: str) -> list[str]:
    """All calendar dates in `year_month` (`YYYY-MM`), in order."""
    year, month = (int(p) for p in year_month.split("-"))
    start = dt.date(year, month, 1)
    next_month = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    n = (next_month - start).days
    return [(start + dt.timedelta(days=i)).isoformat() for i in range(n)]


def day_bounds_ms(date: str) -> tuple[int, int]:
    """`[start_ms, end_ms)` UTC epoch-millisecond bounds for one calendar
    date -- deliberately in milliseconds (the archive's own `time` unit),
    never `* 1_000_000`/`* 1_000_000_000` (the ms->ns conversion happens
    exactly once, inside `normalize_archive_frame`'s `ms_to_ns` call, per
    `tools/check_ms_to_ns_site.py`'s exact-one-site assertion)."""
    d = dt.date.fromisoformat(date)
    start_dt = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)
    return start_ms, start_ms + 86_400_000


def published_daily_dates(symbol: str, year_month: str, *, fetch=None) -> set[str]:
    """Return the set of `YYYY-MM-DD` dates with a published daily trades
    zip in `year_month`, via `list_month` (Plan 01, reused not
    reimplemented) -- avoids attempting (and 404-ing on) a date the archive
    has not published yet (e.g. "yesterday" landing ~T+7h late, or -- in
    principle -- "today")."""
    kwargs = {"fetch": fetch} if fetch is not None else {}
    keys = list_month(symbol, year_month, **kwargs)
    dates: set[str] = set()
    for key in keys:
        name = key.rsplit("/", 1)[-1]
        match = _DAILY_ZIP_DATE_RE.search(name)
        if match and name.endswith(".zip"):
            dates.add(match.group(1))
    return dates


def existing_partition_row_count(
    lake_root_path: Path, symbol: str, stream: str, source: str, date: str
) -> int:
    """Row count of an already-written raw partition, via parquet metadata
    (`pl.scan_parquet(...).select(pl.len())`) -- cheap, no full-column read.
    Used only for the monthly path's full-month row-conservation sanity
    check (`ingest_monthly`'s docstring)."""
    from data.ingest.normalize import raw_partition_dir

    part_dir = raw_partition_dir(lake_root_path, symbol, stream, source, date)
    files = sorted(part_dir.glob("part-*.parquet"))
    if not files:
        return 0
    return int(pl.scan_parquet(files).select(pl.len()).collect().item())


def ingest_daily_date(
    symbol: str,
    date: str,
    *,
    client: BackfillClient,
    lake_root_path: Path,
    unit_entry: UnitRegistryEntry,
) -> dict:
    """Download+verify, extract, normalize, and raw-tier-write one daily
    zip's trades.

    Skip-and-continue (idempotent-range idiom, mirrors `build_curated_range`
    in Task 2): if the raw partition already exists, this makes zero
    network calls and returns `status="already_present"` immediately --
    the "re-run after interruption resumes cleanly" guarantee at the
    RAW-tier level. If `write_raw_partition` itself still raises
    `FileExistsError` (a race between the pre-check and the write, e.g. a
    concurrent process), that exact exception is caught here too, never
    allowed to propagate and abort the caller's date-range loop.
    """
    if raw_partition_exists(lake_root_path, symbol, "trade", "archive", date):
        logger.info("daily %s: raw partition already present, skipping", date)
        return {"date": date, "status": "already_present", "rows": None}

    zip_path = client.ensure_downloaded(symbol, date)
    csv_name = f"{symbol}-{client.dataset}-{date}.csv"
    csv_path = extract_expected_member(zip_path, csv_name, zip_path.parent)
    try:
        raw = pl.read_csv(
            csv_path,
            has_header=unit_entry.header,
            schema_overrides=ARCHIVE_TRADES_SCHEMA_OVERRIDES,
        )
        rtime_ns = zip_path.stat().st_mtime_ns
        frame = normalize_archive_frame(raw, unit_entry, symbol, rtime_ns)
        try:
            write_raw_partition(frame, symbol, "trade", "archive", date, lake_root_path)
        except FileExistsError:
            logger.info(
                "daily %s: raw partition already present (race), skipping", date
            )
            return {"date": date, "status": "already_present", "rows": frame.height}
        return {"date": date, "status": "written", "rows": frame.height}
    finally:
        csv_path.unlink(missing_ok=True)


def ingest_monthly(
    symbol: str,
    year_month: str,
    *,
    client: BackfillClient,
    lake_root_path: Path,
    unit_entry: UnitRegistryEntry,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Ingest one month's trades.

    Skips the ENTIRE download+extract (zero network calls, no disk usage
    beyond what already exists) if every in-range day already has a raw
    partition -- this is what makes a resumed run cheap after a completed
    month, not just per-day cheap (T-03-06's mitigation would be undermined
    if every resumed run re-extracted 7.6GB just to skip every day inside
    the loop).

    Otherwise downloads+verifies the monthly zip once, extracts it once,
    and for each in-range day not yet written runs an explicit lazy
    `.filter(...).collect()` slice (never one in-memory `pl.DataFrame` for
    the whole month) through `normalize_archive_frame` +
    `write_raw_partition`. The extracted CSV is deleted in a `finally`
    block regardless of how the loop exits.

    Row-conservation sanity check (guards a day-boundary filter bug
    silently dropping rows): after processing, the sum of every date's row
    count (days processed this call, plus already-written days' row counts
    read cheaply from parquet metadata) must equal the month's total row
    count from a single `pl.scan_csv(...).select(pl.len()).collect()` --
    raises `ValueError` naming the discrepancy if not, rather than shipping
    a silent gap.
    """
    dates = month_dates(year_month)
    if start_date:
        dates = [d for d in dates if d >= start_date]
    if end_date:
        dates = [d for d in dates if d <= end_date]
    if not dates:
        return []

    missing = [
        d
        for d in dates
        if not raw_partition_exists(lake_root_path, symbol, "trade", "archive", d)
    ]
    if not missing:
        logger.info(
            "monthly %s: all %d in-range day(s) already present, skipping "
            "download+extract entirely",
            year_month,
            len(dates),
        )
        return [{"date": d, "status": "already_present", "rows": None} for d in dates]

    zip_path = client.ensure_downloaded(symbol, year_month)
    csv_name = f"{symbol}-{client.dataset}-{year_month}.csv"
    csv_path = extract_expected_member(zip_path, csv_name, zip_path.parent)
    rtime_ns = zip_path.stat().st_mtime_ns

    results: list[dict] = []
    try:
        lazy = pl.scan_csv(
            csv_path,
            has_header=unit_entry.header,
            schema_overrides=ARCHIVE_TRADES_SCHEMA_OVERRIDES,
        )
        for d in dates:
            if raw_partition_exists(lake_root_path, symbol, "trade", "archive", d):
                results.append({"date": d, "status": "already_present", "rows": None})
                continue

            start_ms, end_ms = day_bounds_ms(d)
            day_df = lazy.filter(
                (pl.col("time") >= start_ms) & (pl.col("time") < end_ms)
            ).collect()
            if day_df.height == 0:
                logger.warning(
                    "monthly %s day %s: zero rows in [%d, %d), skipping "
                    "(no archive data for this day)",
                    year_month,
                    d,
                    start_ms,
                    end_ms,
                )
                results.append({"date": d, "status": "no_rows", "rows": 0})
                continue

            frame = normalize_archive_frame(day_df, unit_entry, symbol, rtime_ns)
            try:
                write_raw_partition(
                    frame, symbol, "trade", "archive", d, lake_root_path
                )
                results.append({"date": d, "status": "written", "rows": frame.height})
            except FileExistsError:
                logger.info(
                    "monthly %s day %s: raw partition already present (race), skipping",
                    year_month,
                    d,
                )
                results.append(
                    {"date": d, "status": "already_present", "rows": frame.height}
                )

        total_scanned = int(lazy.select(pl.len()).collect().item())
        accounted = 0
        for r in results:
            if r["rows"] is not None:
                accounted += r["rows"]
            elif r["status"] == "already_present":
                accounted += existing_partition_row_count(
                    lake_root_path, symbol, "trade", "archive", r["date"]
                )
        if dates == month_dates(year_month) and accounted != total_scanned:
            raise ValueError(
                f"ingest_monthly {year_month}: row-conservation check failed -- "
                f"scanned {total_scanned} total rows but accounted for only "
                f"{accounted} across all {len(dates)} day(s); a day-boundary "
                "filter may be silently dropping rows"
            )
    finally:
        csv_path.unlink(missing_ok=True)

    return results


def run_backfill(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    staging_root_path: Path | None = None,
    lake_root_path: Path | None = None,
    fetch=None,
    open_stream=None,
) -> dict:
    """Orchestrate the full `[start_date, end_date]` window: monthly zips
    for any of `MONTHLY_YEAR_MONTHS` intersecting the range, daily zips for
    `max(DAILY_REGIME_START, start_date) .. end_date`. Returns a summary
    dict with the full per-date `results` list plus status counts.

    `fetch`/`open_stream` are optional injectable overrides (default: real
    `urllib.request`, via `BackfillClient`'s own defaults) -- tests pass
    fakes here so the regime-selection/resume logic is exercised with zero
    real network calls, the same injectable-callable idiom `client.py`
    itself uses.
    """
    staging_root_path = Path(staging_root_path or backfill_staging_root())
    lake_root_path = Path(lake_root_path or lake_root())
    unit_entry = get_unit_entry("futures-um", "trades")

    client_kwargs = {}
    if fetch is not None:
        client_kwargs["fetch"] = fetch
    if open_stream is not None:
        client_kwargs["open_stream"] = open_stream

    monthly_client = BackfillClient(
        staging_root=staging_root_path, granularity="monthly", **client_kwargs
    )
    daily_client = BackfillClient(
        staging_root=staging_root_path, granularity="daily", **client_kwargs
    )

    all_results: list[dict] = []

    for ym in MONTHLY_YEAR_MONTHS:
        month_first, month_last = month_dates(ym)[0], month_dates(ym)[-1]
        if month_last < start_date or month_first > end_date:
            continue
        all_results.extend(
            ingest_monthly(
                symbol,
                ym,
                client=monthly_client,
                lake_root_path=lake_root_path,
                unit_entry=unit_entry,
                start_date=start_date,
                end_date=end_date,
            )
        )

    daily_start = max(DAILY_REGIME_START, start_date)
    daily_dates = daterange(daily_start, end_date)
    if daily_dates:
        by_month: dict[str, list[str]] = {}
        for d in daily_dates:
            by_month.setdefault(d[:7], []).append(d)
        published: set[str] = set()
        for ym in by_month:
            try:
                published |= published_daily_dates(symbol, ym, fetch=daily_client.fetch)
            except urllib.error.URLError as exc:
                logger.warning(
                    "published_daily_dates(%s) failed (%s); falling back to "
                    "attempting every date directly",
                    ym,
                    exc,
                )
                published |= set(by_month[ym])

        for d in daily_dates:
            if d not in published:
                logger.info(
                    "daily %s: not yet published by the archive (T+7h latency), "
                    "skipping -- capture is authoritative for this day until it "
                    "publishes",
                    d,
                )
                all_results.append({"date": d, "status": "not_published", "rows": None})
                continue
            try:
                all_results.append(
                    ingest_daily_date(
                        symbol,
                        d,
                        client=daily_client,
                        lake_root_path=lake_root_path,
                        unit_entry=unit_entry,
                    )
                )
            except urllib.error.HTTPError as exc:
                logger.warning("daily %s: HTTP %s, treating as not_found", d, exc.code)
                all_results.append({"date": d, "status": "not_found", "rows": None})

    statuses = [r["status"] for r in all_results]
    return {
        "results": all_results,
        "written": statuses.count("written"),
        "already_present": statuses.count("already_present"),
        "no_rows": statuses.count("no_rows"),
        "not_published": statuses.count("not_published"),
        "not_found": statuses.count("not_found"),
        "total_rows_written": sum(
            r["rows"] for r in all_results if r["status"] == "written" and r["rows"]
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--staging-root", default=None)
    parser.add_argument("--lake-root", default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    summary = run_backfill(
        args.symbol,
        args.start,
        args.end,
        staging_root_path=Path(args.staging_root) if args.staging_root else None,
        lake_root_path=Path(args.lake_root) if args.lake_root else None,
    )
    print(
        f"written={summary['written']} already_present={summary['already_present']} "
        f"no_rows={summary['no_rows']} not_published={summary['not_published']} "
        f"not_found={summary['not_found']} "
        f"total_rows_written={summary['total_rows_written']}"
    )
    for r in summary["results"]:
        print(f"  {r['date']}: {r['status']} rows={r['rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
