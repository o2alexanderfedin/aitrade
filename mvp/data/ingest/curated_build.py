"""Curated-tier build: deterministic `(etime, seq)` materialization, the
NA-placeholder ingest filter, per-day whole-source precedence, and manifest
issuance -- the pipeline stage between the raw tier (Plan 01) and the
`store.py` loader (Task 2 of this plan).

Source precedence (03-RESEARCH.md Pattern 2): a per-day WHOLE-SOURCE switch,
never a row-level merge -- `trade_id` stays unique within a curated
partition by construction, which is what makes `materialize_seq`'s
`(etime, seq)` total order valid. The archive is authoritative for trades on
every published day; capture is authoritative otherwise (03-CONTEXT.md).

The capture `parsed/date=.../` read is schema-tolerant BY DESIGN
(`read_capture_partition`): per-file `pl.scan_parquet` then
`pl.concat(..., how="diagonal_relaxed")`, never a bare glob read -- measured
on polars 1.41.2 against a directory mixing `schema_version=1` (no
`exec_type` column) and `schema_version=2` (`exec_type` present) files: a
naive glob read raises `SchemaError`, a default `pl.concat` raises
`ShapeError`, only `diagonal_relaxed` unions the schemas cleanly (filling
`exec_type=null` for the pre-restart files). Plan 06 introduces
`exec_type`, so a restart day's directory will be exactly this mixed case.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import polars as pl

from data.backfill.downloader import daterange
from data.capture.rotation import write_parquet_atomic
from data.ingest.trade_side import resolve_side
from data.store import by_date_index_path, issue_manifest

__all__ = [
    "materialize_seq",
    "select_source_for_day",
    "filter_na_placeholders",
    "read_capture_partition",
    "build_curated_day",
    "build_curated_range",
]


def materialize_seq(df: pl.DataFrame, sort_keys: list[str]) -> pl.DataFrame:
    """Return `df` sorted by `sort_keys` with a fresh, deterministic `seq`
    column (`row_number()` over the sort order) materialized.

    `sort_keys` must be a genuine total order within this partition --
    `sort_keys[-1]` is treated as the tiebreak identity column (`trade_id`
    for trades, `update_id` for bookTicker) and asserted unique BEFORE
    sorting (03-RESEARCH.md Pattern 2's defensive check): a duplicate id
    within a single-source partition is a bug, not a scenario the per-day
    source switch should ever produce.

    Any existing `seq` column is handled before the sort: if every value is
    the archive normalizer's `-1` sentinel (Plan 01's
    `normalize_archive_trades`), it is DROPPED (not a meaningful
    `capture_seq` to preserve); otherwise it is renamed to `capture_seq` and
    preserved (capture's own live, arrival-ordered sequence). Capture's
    `seq` is arrival-ordered and per-run; merging archive rows into that
    sequence would break monotonicity, which is exactly why this function
    materializes a NEW `seq` from the sort order rather than reusing either
    source's own column.
    """
    id_col = sort_keys[-1]
    dup_count = df.height - df[id_col].n_unique()
    if dup_count != 0:
        raise ValueError(
            f"materialize_seq: {id_col!r} has {dup_count} duplicate value(s) "
            "within this partition -- the per-day source switch should make "
            "this impossible; investigate before trusting (etime, seq)."
        )

    if "seq" in df.columns:
        if (df["seq"] == -1).all():
            df = df.drop("seq")
        else:
            df = df.rename({"seq": "capture_seq"})

    sorted_df = df.sort(sort_keys)
    return sorted_df.with_row_index("seq").with_columns(pl.col("seq").cast(pl.Int64))


def select_source_for_day(
    archive_df: pl.DataFrame | None,
    capture_df: pl.DataFrame | None,
    published: bool,
) -> tuple[pl.DataFrame, dict]:
    """Choose ONE source wholesale for this day (never a row-level merge),
    and compute the reconciliation stats Plan 04's DQ check (2) needs --
    precomputed here, BEFORE the non-chosen source is discarded, because the
    curated OUTPUT structurally contains only one source per day and cannot
    itself answer a reconciliation question after the fact.

    `published`: whether an archive file exists for this date. If `True`
    and `archive_df` is not `None`, the archive is chosen; otherwise capture
    is chosen (03-CONTEXT.md's precedence rule).

    Reconciliation is scoped to the `trade_id` range where BOTH sources
    overlap (`[max(mins), min(maxes)]`) -- matching PROBE-RESULTS.md's own
    methodology (capture starts mid-day on 2026-09-12, so a naive full-set
    diff would count every pre-capture archive id as "missing", which is
    not a reconciliation finding, just capture not having started yet).
    When only one source is available, every reconciliation field is `None`
    (not applicable, not a violation).
    """
    archive_available = archive_df is not None
    capture_available = capture_df is not None

    if published and archive_available:
        chosen_df, chosen_source = archive_df, "archive"
    elif capture_available:
        chosen_df, chosen_source = capture_df, "capture"
    elif archive_available:
        chosen_df, chosen_source = archive_df, "archive"
    else:
        raise ValueError("select_source_for_day: no source available for this day")

    stats: dict = {
        "chosen_source": chosen_source,
        "archive_available": archive_available,
        "capture_available": capture_available,
        "reconciliation_missing_from_capture": None,
        "reconciliation_missing_from_archive": None,
        "reconciliation_overlap_rows": None,
        "reconciliation_overlap_id_min": None,
        "reconciliation_overlap_id_max": None,
    }

    if archive_available and capture_available:
        id_col = "trade_id" if "trade_id" in archive_df.columns else "update_id"
        overlap_min = max(archive_df[id_col].min(), capture_df[id_col].min())
        overlap_max = min(archive_df[id_col].max(), capture_df[id_col].max())
        if overlap_min <= overlap_max:
            archive_overlap_ids = set(
                archive_df.filter(pl.col(id_col).is_between(overlap_min, overlap_max))[
                    id_col
                ].to_list()
            )
            capture_overlap_ids = set(
                capture_df.filter(pl.col(id_col).is_between(overlap_min, overlap_max))[
                    id_col
                ].to_list()
            )
            stats["reconciliation_missing_from_capture"] = len(
                archive_overlap_ids - capture_overlap_ids
            )
            stats["reconciliation_missing_from_archive"] = len(
                capture_overlap_ids - archive_overlap_ids
            )
            stats["reconciliation_overlap_rows"] = len(
                archive_overlap_ids & capture_overlap_ids
            )
            stats["reconciliation_overlap_id_min"] = int(overlap_min)
            stats["reconciliation_overlap_id_max"] = int(overlap_max)

    return chosen_df, stats


def filter_na_placeholders(df: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Drop `X="NA"` placeholder trade rows (03-CONTEXT.md's locked
    canonical-ingest filter, `filter=na_placeholder`).

    Schema-version-conditional: `schema_version=1` rows are identified by
    `price==0.0 AND qty==0.0` (the only Parquet-side signature v1 has --
    `TRADE_SCHEMA` v1 has no `exec_type` column); `schema_version=2` rows
    (Plan 06) are identified by `exec_type=="NA"`. Both signatures are
    checked per-row (never a single day-level branch) because one day's
    capture-sourced rows may straddle Plan 06's schema-v2 restart. If
    `exec_type` is entirely absent from `df` (every real day before Plan
    06's restart, including this plan's archive-only build), only the v1
    predicate is evaluated -- referencing a nonexistent column would raise
    `ColumnNotFoundError`.

    Returns `(cleaned_df, drop_count)`. Never called for `stream ==
    "bookTicker"` (no such columns exist on that schema) -- the caller
    gates this.
    """
    v1_na = (
        (pl.col("schema_version") == 1)
        & (pl.col("price") == 0.0)
        & (pl.col("qty") == 0.0)
    )
    if "exec_type" in df.columns:
        v2_na = (pl.col("schema_version") == 2) & (pl.col("exec_type") == "NA")
        na_mask = v1_na | v2_na
    else:
        na_mask = v1_na

    drop_count = df.filter(na_mask).height
    cleaned = df.filter(~na_mask)
    return cleaned, drop_count


def read_capture_partition(date_dir: Path) -> pl.DataFrame | None:
    """Schema-tolerant read of a capture `parsed/.../date=.../` directory:
    per-file `pl.scan_parquet` then `pl.concat(..., how="diagonal_relaxed")`
    -- see module docstring for why this is never a bare glob read. Returns
    `None` if the directory is absent or has no `part-*.parquet` files
    (read-only against the capture daemon's own root; never mutated)."""
    date_dir = Path(date_dir)
    if not date_dir.exists():
        return None
    files = sorted(date_dir.glob("part-*.parquet"))
    if not files:
        return None
    frames = [pl.scan_parquet(f) for f in files]
    return pl.concat(frames, how="diagonal_relaxed").collect()


def _atomic_write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(body, sort_keys=True, indent=2, default=str))
    tmp_path.replace(path)


def build_curated_day(
    symbol: str,
    stream: str,
    date: str,
    lake_root: Path,
    capture_root: Path,
    *,
    registry_root: Path,
    code_hash: str,
) -> dict:
    """Orchestrate one day's curated build: read raw archive + capture
    partitions, select a source, filter/resolve-side (trades only),
    materialize `(etime, seq)`, write the curated partition, issue its
    manifest, and persist `build_stats.json`.

    `stream="bookTicker"` (03-03-PLAN.md Task 2) is capture-ONLY by
    construction, not by a special-cased branch here: bookTicker has no
    backfill source at all (the two-regime boundary decision -- L1 exists
    only from capture's own first etime, 2026-09-12T06:37:10.882Z onward,
    03-CONTEXT.md/PROBE-RESULTS.md section 3), so `archive_dir` below is
    simply never populated for this stream and `select_source_for_day`
    falls through to its `capture_available` branch with `published=False`
    every time -- the same code path `stream="trade"` uses on any
    not-yet-archived day, exercised here unconditionally. `resolve_side`
    and the NA-placeholder filter are skipped for bookTicker below (no
    side/exec_type concept on that schema); `materialize_seq`'s sort key
    is `["etime", "update_id"]` instead of `["etime", "trade_id"]`.

    Returns the issued manifest dict.
    """
    lake_root = Path(lake_root)

    # Never populated for stream="bookTicker" -- see docstring above.
    archive_dir = (
        lake_root
        / "raw"
        / f"symbol={symbol}"
        / f"stream={stream}"
        / "source=archive"
        / f"date={date}"
    )
    archive_files = (
        sorted(archive_dir.glob("part-*.parquet")) if archive_dir.exists() else []
    )
    published = bool(archive_files)
    archive_df = pl.read_parquet(archive_files[0]) if archive_files else None

    capture_dir = (
        Path(capture_root) / f"symbol={symbol}" / f"stream={stream}" / f"date={date}"
    )
    capture_df = read_capture_partition(capture_dir)

    chosen_df, stats = select_source_for_day(archive_df, capture_df, published)
    chosen_source = stats["chosen_source"]

    if chosen_source == "archive":
        input_entries = [
            {
                "path": str(archive_files[0].resolve()),
                "sha256": hashlib.sha256(archive_files[0].read_bytes()).hexdigest(),
                "rows": archive_df.height,
            }
        ]
    else:
        capture_files = sorted(capture_dir.glob("part-*.parquet"))
        input_entries = [
            {
                "path": str(f.resolve()),
                "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                "rows": None,
            }
            for f in capture_files
        ]

    if stream == "trade":
        pre_filter_rows = chosen_df.height
        chosen_df, na_dropped = filter_na_placeholders(chosen_df)
        na_rate = na_dropped / pre_filter_rows if pre_filter_rows > 0 else 0.0
        chosen_df = resolve_side(chosen_df)
        sort_keys = ["etime", "trade_id"]
    else:
        na_dropped = 0
        na_rate = 0.0
        sort_keys = ["etime", "update_id"]

    chosen_df = materialize_seq(chosen_df, sort_keys)

    curated_date_dir = (
        lake_root / "curated" / f"symbol={symbol}" / f"stream={stream}" / f"date={date}"
    )
    # Write-once, same existing-file refusal as Plan 01's write_raw_partition
    # -- a second build_curated_day call for an already-written day is a
    # no-op-that-errors, never a silent overwrite or silent duplicate.
    existing = sorted(curated_date_dir.glob("part-*.parquet"))
    if existing:
        raise FileExistsError(
            f"curated partition {curated_date_dir} already has a written "
            f"part file: {existing[0]}"
        )

    final_path = curated_date_dir / f"part-{time.time_ns()}.parquet"
    write_parquet_atomic(chosen_df, final_path)

    on_disk_bytes = final_path.read_bytes()
    st = final_path.stat()
    partition_entry = {
        "date": date,
        "path": str(final_path.relative_to(lake_root)),
        "sha256": hashlib.sha256(on_disk_bytes).hexdigest(),
        "rows": chosen_df.height,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": int(chosen_df["etime"].min()),
        "etime_max": int(chosen_df["etime"].max()),
    }

    schema_version = int(chosen_df["schema_version"].max())

    # build_stats.json is written BEFORE the manifest (03-REVIEW.md WR-02):
    # issuing the manifest also repoints the by-date index, so a crash
    # between the two used to leave a resolvable curated day with no stats,
    # and the DQ report silently dropped the reconciliation and NA checks.
    # The pre-manifest write binds the stats to the partition's sha256; the
    # manifest_id is filled in right after issuance.
    build_stats = {
        **stats,
        "na_placeholder_dropped": na_dropped,
        "na_placeholder_rate": na_rate,
        "partition_path": partition_entry["path"],
        "partition_sha256": partition_entry["sha256"],
        "manifest_id": None,
    }
    stats_path = _curated_build_stats_path(lake_root, symbol, stream, date)
    _atomic_write_json(stats_path, build_stats)

    manifest = issue_manifest(
        dataset=f"{symbol}.{stream}",
        symbol=symbol,
        stream=stream,
        tier="curated",
        schema_version=schema_version,
        inputs=input_entries,
        partitions=[partition_entry],
        code_hash=code_hash,
        registry_root=registry_root,
        dates=[date],
    )

    build_stats["manifest_id"] = manifest["manifest_id"]
    _atomic_write_json(stats_path, build_stats)

    return manifest


def _curated_build_stats_path(
    lake_root: Path, symbol: str, stream: str, date: str
) -> Path:
    """Single source of truth for a date's `build_stats.json` path --
    mirrors `build_curated_day`'s own `stats_path` construction, reused by
    `build_curated_range` to report `chosen_source` per date without
    changing `build_curated_day`'s return contract (manifest dict only)."""
    return (
        Path(lake_root)
        / "curated_meta"
        / f"symbol={symbol}"
        / f"stream={stream}"
        / f"date={date}"
        / "build_stats.json"
    )


def _day_has_any_source(
    symbol: str, stream: str, date: str, lake_root: Path, capture_root: Path
) -> bool:
    """True if EITHER the raw archive partition OR the capture partition
    has data for this `(symbol, stream, date)`.

    Checked by `build_curated_range` BEFORE calling `build_curated_day`, so
    a day with neither source (expected for `stream="bookTicker"` on every
    date before capture's own first etime, 2026-09-12T06:37:10.882Z --
    there is no L1 backfill source at all, per the two-regime boundary
    decision) is a documented, counted skip -- not a crashed `ValueError`
    propagating up from `select_source_for_day`'s own "no source
    available" guard.
    """
    archive_dir = (
        Path(lake_root)
        / "raw"
        / f"symbol={symbol}"
        / f"stream={stream}"
        / "source=archive"
        / f"date={date}"
    )
    if archive_dir.exists() and any(archive_dir.glob("part-*.parquet")):
        return True
    capture_dir = (
        Path(capture_root) / f"symbol={symbol}" / f"stream={stream}" / f"date={date}"
    )
    return capture_dir.exists() and any(capture_dir.glob("part-*.parquet"))


def build_curated_range(
    symbol: str,
    stream: str,
    start_date: str,
    end_date: str,
    lake_root: Path,
    capture_root: Path,
    *,
    registry_root: Path,
    code_hash: str,
) -> list[dict]:
    """Call `build_curated_day` once per UTC date in `[start_date,
    end_date]` (inclusive), idempotently.

    Skips (never fails) two kinds of date, both counted and reported, not
    silently dropped:
    - `"already_present"`: the curated partition already exists (write-once
      immutability -- re-running the range build issues ZERO new
      manifests/partitions for these dates; the existing manifest_id is
      looked up via the by-date index, matching `build_curated_day`'s own
      write-once existing-file refusal so this function never even
      attempts a write that would raise).
    - `"no_source"`: neither the raw archive partition nor the capture
      partition has any data for this date (`_day_has_any_source`) --
      expected for `stream="bookTicker"` before capture's own history
      starts (2026-06/07/08, and 2026-09-01..11 before 06:37:10Z on
      2026-09-12); would be a genuine finding for `stream="trade"`, which
      has an archive source for every date in this phase's window.

    Returns one status dict per date: `{"date", "status", "manifest_id",
    "chosen_source"}` (`chosen_source` is `None` for `"no_source"` dates,
    and is read back from the just-written or already-existing
    `build_stats.json` otherwise -- `build_curated_day`'s own return
    contract, the manifest dict, is unchanged).
    """
    lake_root = Path(lake_root)
    dataset = f"{symbol}.{stream}"
    results: list[dict] = []

    for date in daterange(start_date, end_date):
        curated_dir = (
            lake_root
            / "curated"
            / f"symbol={symbol}"
            / f"stream={stream}"
            / f"date={date}"
        )
        if sorted(curated_dir.glob("part-*.parquet")):
            idx_path = by_date_index_path(registry_root, dataset, symbol, stream, date)
            manifest_id = (
                json.loads(idx_path.read_text())["manifest_id"]
                if idx_path.exists()
                else None
            )
            stats_path = _curated_build_stats_path(lake_root, symbol, stream, date)
            chosen_source = (
                json.loads(stats_path.read_text())["chosen_source"]
                if stats_path.exists()
                else None
            )
            results.append(
                {
                    "date": date,
                    "status": "already_present",
                    "manifest_id": manifest_id,
                    "chosen_source": chosen_source,
                }
            )
            continue

        if not _day_has_any_source(symbol, stream, date, lake_root, capture_root):
            results.append(
                {
                    "date": date,
                    "status": "no_source",
                    "manifest_id": None,
                    "chosen_source": None,
                }
            )
            continue

        manifest = build_curated_day(
            symbol,
            stream,
            date,
            lake_root,
            capture_root,
            registry_root=registry_root,
            code_hash=code_hash,
        )
        stats_path = _curated_build_stats_path(lake_root, symbol, stream, date)
        chosen_source = json.loads(stats_path.read_text())["chosen_source"]
        results.append(
            {
                "date": date,
                "status": "written",
                "manifest_id": manifest["manifest_id"],
                "chosen_source": chosen_source,
            }
        )

    return results
