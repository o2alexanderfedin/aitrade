"""Archive CSV -> canonical TRADE_SCHEMA rows, and the raw-tier write-once
partition writer.

`normalize_archive_trades` is the ONLY call site outside `data/capture/
parse.py` that reaches `ms_to_ns` -- it must never spell out `* 1_000_000`
itself (enforced by `tools/check_ms_to_ns_site.py`'s exact-one-site
assertion; see that module and `data/capture/parse.py`'s docstrings).

`write_raw_partition` reuses `data.capture.rotation.write_parquet_atomic`
(the same atomic `.tmp`+rename primitive the capture daemon uses) and
refuses to write a `date=...` partition that already has a `part-*.parquet`
file in it -- immutability by construction, not merely by convention.
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


def normalize_archive_trades(
    csv_path: Path,
    unit_entry: UnitRegistryEntry,
    symbol: str,
    rtime_ns: int,
) -> pl.DataFrame:
    """Read an archive trades CSV and return rows shaped exactly like
    `TRADE_SCHEMA`'s columns.

    `rtime_ns` is an explicit parameter, not derived inside this function --
    it only receives a local `csv_path`, never an HTTP response, so it
    cannot itself read a `Last-Modified` header. The caller computes
    `rtime_ns` uniformly from the local staged file's own mtime
    (`stat().st_mtime_ns`) whether the file was just downloaded or is a
    pre-existing cached file with no live HTTP response in this run -- one
    rule, not two branches that could silently diverge.

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
            f"normalize_archive_trades only supports time_unit='ms', "
            f"got {unit_entry.time_unit!r} for ({unit_entry.market}, {unit_entry.dataset})"
        )

    raw = pl.read_csv(
        csv_path,
        has_header=unit_entry.header,
        schema_overrides=ARCHIVE_TRADES_SCHEMA_OVERRIDES,
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
    )

    df = df.select(list(TRADE_SCHEMA.keys())).cast(TRADE_SCHEMA)
    assert_non_null_etime(df)
    return df


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

    part_dir = (
        Path(lake_root)
        / "raw"
        / f"symbol={symbol}"
        / f"stream={stream}"
        / f"source={source}"
        / f"date={date}"
    )
    existing = sorted(part_dir.glob("part-*.parquet"))
    if existing:
        raise FileExistsError(
            f"raw partition {part_dir} already has a written part file: {existing[0]}"
        )

    final_path = part_dir / f"part-{time.time_ns()}.parquet"
    write_parquet_atomic(df, final_path)
    return final_path
