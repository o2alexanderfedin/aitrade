"""Archive CSV -> canonical TRADE_SCHEMA rows, and the raw-tier write-once
partition writer.

`normalize_archive_frame` is the ONLY call site outside `data/capture/
parse.py` that reaches `ms_to_ns` -- it must never spell out `* 1_000_000`
itself (enforced by `tools/check_ms_to_ns_site.py`'s exact-one-site
assertion; see that module and `data/capture/parse.py`'s docstrings).
`normalize_archive_trades` (Plan 01, single-day) is now a thin `csv_path`
-reading wrapper around it; Plan 03's monthly path calls
`normalize_archive_frame` directly against a per-day-filtered
`pl.LazyFrame.collect()` slice, never re-reading the whole month's CSV a
second time through the `csv_path` wrapper (03-03-PLAN.md Task 1: "never
fully materialized as one in-memory `pl.DataFrame`" -- the frame this
function receives is already just one UTC day's rows).

`write_raw_partition` reuses `data.capture.rotation.write_parquet_atomic`
(the same atomic `.tmp`+rename primitive the capture daemon uses) and
refuses to write a `date=...` partition that already has a `part-*.parquet`
file in it -- immutability by construction, not merely by convention.
`raw_partition_exists` is the single source of truth for that same
`part_dir` path, reused by the downloader's cheap pre-check (skip an entire
month's 7.6GB extract if every day in it is already written) so the
existing-file convention lives in exactly one place.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from data.capture.parse import ms_to_ns
from data.capture.rotation import write_parquet_atomic
from data.schema import TRADE_SCHEMA, assert_non_null_etime
from data.unit_registry import UnitRegistryEntry

# T-03-05 (Tampering / V5 input validation): explicit schema_overrides on
# every archive-CSV read, never bare pl.read_csv inference -- price/qty/
# quote_qty are read as Utf8 first and cast to Float64 explicitly below, so
# an unexpected non-numeric value fails loudly at cast time instead of being
# silently coerced to null or misread.
ARCHIVE_TRADES_SCHEMA_OVERRIDES: dict[str, pl.DataType] = {
    "id": pl.Int64,
    "time": pl.Int64,
    "price": pl.Utf8,
    "qty": pl.Utf8,
    "quote_qty": pl.Utf8,
    "is_buyer_maker": pl.Boolean,
}


def normalize_archive_frame(
    raw: pl.DataFrame,
    unit_entry: UnitRegistryEntry,
    symbol: str,
    rtime_ns: int,
) -> pl.DataFrame:
    """Return `raw` (an already-read archive-trades frame, shaped like
    `ARCHIVE_TRADES_SCHEMA_OVERRIDES`) shaped exactly like `TRADE_SCHEMA`'s
    columns.

    `raw` may be the full single-day CSV (`normalize_archive_trades`'s own
    call) or an already-day-filtered slice of a monthly CSV's lazy scan
    (the downloader's monthly path) -- this function itself never reads a
    file and never cares which.

    `rtime_ns` is an explicit parameter, not derived inside this function --
    it only receives an in-memory frame, never an HTTP response, so it
    cannot itself read a `Last-Modified` header. The caller computes
    `rtime_ns` uniformly from the local staged file's own mtime
    (`stat().st_mtime_ns`) whether the file was just downloaded or is a
    pre-existing cached file with no live HTTP response in this run -- one
    rule, not two branches that could silently diverge (for the monthly
    path, this is the staged MONTHLY zip's mtime, applied uniformly to
    every day sliced out of it).

    Archive rows carry no ingestion-vs-exchange time distinction (the
    archive has no separate `E` field), so `event_time` is deliberately set
    equal to `etime` -- a documented simplification, not a bug.

    `seq=-1` is a sentinel: archive rows do not get a capture `seq` here;
    Plan 02's curated build assigns the real deterministic `seq`.

    Raises `ValueError` if `unit_entry.time_unit != "ms"` -- this function
    is only correct for the ms convention; a future spot integration needs
    its own normalizer, not a silently-wrong branch here.
    """
    if unit_entry.time_unit != "ms":
        raise ValueError(
            f"normalize_archive_frame only supports time_unit='ms', "
            f"got {unit_entry.time_unit!r} for ({unit_entry.market}, {unit_entry.dataset})"
        )

    etime_expr = ms_to_ns(pl.col("time"))

    df = raw.select(
        pl.lit(symbol).alias("symbol"),
        pl.lit("trade").alias("stream"),
        pl.col("id").alias("trade_id"),
        etime_expr.alias("etime"),
        etime_expr.alias("event_time"),
        pl.col("price").cast(pl.Float64).alias("price"),
        pl.col("qty").cast(pl.Float64).alias("qty"),
        pl.col("is_buyer_maker"),
        pl.lit(-1, dtype=pl.Int64).alias("seq"),
        pl.lit(rtime_ns, dtype=pl.Int64).alias("rtime"),
        pl.lit("archive").alias("source"),
        pl.lit(1, dtype=pl.Int32).alias("schema_version"),
        # 03-06-PLAN.md: TRADE_SCHEMA gained exec_type at schema_version=2,
        # but archive CSVs carry no execution-type field at all (and this
        # writer deliberately keeps archive rows at the literal
        # schema_version=1 above, regardless of live capture's
        # SCHEMA_VERSION) -- null, not a fabricated value. curated_build.py
        # already only reads exec_type conditionally on schema_version==2.
        pl.lit(None, dtype=pl.Utf8).alias("exec_type"),
    )

    df = df.select(list(TRADE_SCHEMA.keys())).cast(TRADE_SCHEMA)
    assert_non_null_etime(df)
    return df


def normalize_archive_trades(
    csv_path: Path,
    unit_entry: UnitRegistryEntry,
    symbol: str,
    rtime_ns: int,
) -> pl.DataFrame:
    """Read an archive trades CSV in full and return it normalized via
    `normalize_archive_frame` -- the single-day (daily-zip) code path.
    """
    raw = pl.read_csv(
        csv_path,
        has_header=unit_entry.header,
        schema_overrides=ARCHIVE_TRADES_SCHEMA_OVERRIDES,
    )
    return normalize_archive_frame(raw, unit_entry, symbol, rtime_ns)


def raw_partition_dir(
    lake_root: Path, symbol: str, stream: str, source: str, date: str
) -> Path:
    """Single source of truth for a raw-tier partition's directory path --
    reused by `write_raw_partition`'s existing-file refusal and by the
    downloader's cheap pre-check (never duplicate this path shape)."""
    return (
        Path(lake_root)
        / "raw"
        / f"symbol={symbol}"
        / f"stream={stream}"
        / f"source={source}"
        / f"date={date}"
    )


def raw_partition_exists(
    lake_root: Path, symbol: str, stream: str, source: str, date: str
) -> bool:
    """True if `date`'s raw partition already has a written part file."""
    part_dir = raw_partition_dir(lake_root, symbol, stream, source, date)
    return part_dir.exists() and any(part_dir.glob("part-*.parquet"))


def write_raw_partition(
    df: pl.DataFrame,
    symbol: str,
    stream: str,
    source: str,
    date: str,
    lake_root: Path,
) -> Path:
    """Write `df` as a write-once raw-tier partition file.

    Target path: `lake_root/raw/symbol=<symbol>/stream=<stream>/
    source=<source>/date=<date>/part-<ns>.parquet`. Refuses (raises
    `FileExistsError`, naming the existing file) if that `date=...`
    directory already contains any `part-*.parquet` file -- a second
    normalize-and-write attempt for an already-written day is a no-op-that-
    errors, never a silent overwrite or a silent duplicate.
    """
    import time

    part_dir = raw_partition_dir(lake_root, symbol, stream, source, date)
    existing = sorted(part_dir.glob("part-*.parquet"))
    if existing:
        raise FileExistsError(
            f"raw partition {part_dir} already has a written part file: {existing[0]}"
        )

    final_path = part_dir / f"part-{time.time_ns()}.parquet"
    write_parquet_atomic(df, final_path)
    return final_path
