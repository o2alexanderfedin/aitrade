"""A whole synthetic lake, small enough to build features from in a test.

`tests/fixtures/feature_tier.py` seeds the two-row curated partitions the
TIER tests need (write-once, manifest, by-date pointer). This module seeds
the ones the BUILD needs, which is a strictly larger thing: both streams,
carrying every column `features.event_stream.project_bookticker` and
`project_trade` actually read, plus an `ok` DQ report row per stream so
`store.load_curated`'s pause gate lets the build through.

Lives under `tests/fixtures/` for the reason recorded four times in this
phase: `tests/features/` must NOT be a package, because `mvp/features/` is
a real one and a same-named test package shadows it on `sys.path`.

Etimes are anchored to a real UTC midnight rather than to small integers,
so "a label whose horizon runs past the end of day D" is the real thing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl

from data.store import dq_report_path, issue_manifest
from data.time_ns import NS_PER_SECOND

SYMBOL = "BTCUSDT"
L1_STREAM = "bookTicker"
TRADE_STREAM = "trade"

DATE = "2026-09-13"
NEXT_DATE = "2026-09-14"
THIRD_DATE = "2026-09-15"

#: 2026-09-13T00:00:00Z, in int64 ns since the epoch.
DAY_START = 1_789_257_600 * NS_PER_SECOND
NEXT_DAY_START = DAY_START + 86_400 * NS_PER_SECOND

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

RESYNC_SCHEMA: dict[str, pl.DataType] = {
    "gap_start_rtime": pl.Int64,
    "gap_end_rtime": pl.Int64,
    "gap_end_etime_approx": pl.Int64,
    "warmup_end_etime_approx": pl.Int64,
}


def quote_frame(etimes: list[int], mids: list[float]) -> pl.DataFrame:
    """A curated bookTicker partition with a 0.1 spread, so `mid` is
    exactly the number the fixture names."""
    return pl.DataFrame(
        {
            "seq": list(range(len(etimes))),
            "etime": [int(e) for e in etimes],
            "bid_price": [m - 0.05 for m in mids],
            "bid_qty": [1.0] * len(mids),
            "ask_price": [m + 0.05 for m in mids],
            "ask_qty": [2.0] * len(mids),
            "update_id": list(range(len(etimes))),
        },
        schema={
            "seq": pl.Int64,
            "etime": pl.Int64,
            "bid_price": pl.Float64,
            "bid_qty": pl.Float64,
            "ask_price": pl.Float64,
            "ask_qty": pl.Float64,
            "update_id": pl.Int64,
        },
    )


def trade_frame(
    etimes: list[int],
    prices: list[float],
    qtys: list[float],
    sides: list[int],
    *,
    na_rows: int = 0,
    unknown_rows: int = 0,
) -> pl.DataFrame:
    """A curated trade partition carrying the columns `project_trade`
    reads, plus the two row classes D-04-12 requires be COUNTED:
    `na_rows` schema-v1 NA placeholders (price 0, qty 0) appended at the
    end, and the first `unknown_rows` rows marked `side_method="unknown"`.
    """
    n = len(etimes)
    etimes = [int(e) for e in etimes]
    rows = {
        "seq": list(range(n)),
        "etime": list(etimes),
        "price": list(prices),
        "qty": list(qtys),
        "side_method": [
            "unknown" if i < unknown_rows else "exact_flag" for i in range(n)
        ],
        "tradeSide_corrected": [
            0 if i < unknown_rows else int(s) for i, s in enumerate(sides)
        ],
        "schema_version": [1] * n,
        "trade_id": list(range(n)),
    }
    for k in range(na_rows):
        rows["seq"].append(n + k)
        rows["etime"].append(etimes[-1] if etimes else 0)
        rows["price"].append(0.0)
        rows["qty"].append(0.0)
        rows["side_method"].append("exact_flag")
        rows["tradeSide_corrected"].append(1)
        rows["schema_version"].append(1)
        rows["trade_id"].append(n + k)
    return pl.DataFrame(
        rows,
        schema={
            "seq": pl.Int64,
            "etime": pl.Int64,
            "price": pl.Float64,
            "qty": pl.Float64,
            "side_method": pl.Utf8,
            "tradeSide_corrected": pl.Int8,
            "schema_version": pl.Int32,
            "trade_id": pl.Int64,
        },
    ).sort("etime", "trade_id")


def issue_curated_partition(
    lake_root: Path,
    registry_root: Path,
    *,
    stream: str,
    date: str,
    df: pl.DataFrame,
    symbol: str = SYMBOL,
) -> str:
    """Write one curated partition and issue its manifest (which writes the
    by-date pointer the day-boundary gate reads). Returns the manifest id."""
    rel = f"curated/symbol={symbol}/stream={stream}/date={date}/part-1.parquet"
    path = Path(lake_root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, compression="zstd")
    st = path.stat()
    manifest = issue_manifest(
        dataset=f"{symbol}.{stream}",
        symbol=symbol,
        stream=stream,
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
        registry_root=Path(registry_root),
        dates=[date],
    )
    return manifest["manifest_id"]


def write_dq_report(
    lake_root: Path,
    date: str,
    rows: list[tuple[str, str, str]],
    *,
    symbol: str = SYMBOL,
) -> Path:
    """One `report.parquet` for `date` carrying `(stream, manifest_id,
    dq_status)` rows.

    Written WHOLESALE, exactly as `data.dq.report.write_report` does: a
    report is rebuilt, never appended to, so a fixture that appends would
    be testing a shape the real writer cannot produce.
    """
    path = dq_report_path(Path(lake_root), date)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "date": [date] * len(rows),
            "symbol": [symbol] * len(rows),
            "stream": [r[0] for r in rows],
            "manifest_id": [r[1] for r in rows],
            "check": [f"fixture_{r[0]}" for r in rows],
            "dq_status": [r[2] for r in rows],
            "value": [0.0] * len(rows),
            "count": [None] * len(rows),
            "detail": [None] * len(rows),
        },
        schema=REPORT_SCHEMA,
    ).write_parquet(path)
    return path


def write_resync_windows(
    lake_root: Path, date: str, windows: list[tuple[int, int]]
) -> Path:
    """The Phase 3 sidecar, with `windows` given as
    `(gap_end_etime_approx, warmup_end_etime_approx)` pairs -- the two
    columns the `post_gap_warmup` tag actually joins against."""
    from data.dq.report import dq_resync_windows_path

    path = dq_resync_windows_path(Path(lake_root), date)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "gap_start_rtime": [int(g) - 1 for g, _ in windows],
            "gap_end_rtime": [int(g) for g, _ in windows],
            "gap_end_etime_approx": [int(g) for g, _ in windows],
            "warmup_end_etime_approx": [int(w) for _, w in windows],
        },
        schema=RESYNC_SCHEMA,
    ).write_parquet(path)
    return path


def seed_day(
    lake_root: Path,
    registry_root: Path,
    date: str,
    *,
    quotes: pl.DataFrame,
    trades: pl.DataFrame | None = None,
    dq_status: str = "ok",
    symbol: str = SYMBOL,
) -> dict[str, str]:
    """Seed one complete curated day: bookTicker (+ optional trade),
    manifests, by-date pointers, and the day's DQ report scoring every
    manifest it issued. Returns `{stream: manifest_id}`."""
    ids = {
        L1_STREAM: issue_curated_partition(
            lake_root,
            registry_root,
            stream=L1_STREAM,
            date=date,
            df=quotes,
            symbol=symbol,
        )
    }
    if trades is not None:
        ids[TRADE_STREAM] = issue_curated_partition(
            lake_root,
            registry_root,
            stream=TRADE_STREAM,
            date=date,
            df=trades,
            symbol=symbol,
        )
    write_dq_report(
        lake_root,
        date,
        [(stream, mid, dq_status) for stream, mid in ids.items()],
        symbol=symbol,
    )
    return ids


def seed_two_days(
    lake_root: Path,
    registry_root: Path,
    *,
    quote_etimes: list[int] | None = None,
    quote_mids: list[float] | None = None,
    trade_etimes: list[int] | None = None,
    na_rows: int = 1,
    unknown_rows: int = 1,
    seed_next_day: bool = True,
) -> dict:
    """The default buildable pair: day D with both streams, day D+1 with
    the L1 tail day D's long-horizon labels read.

    D's quotes are one per second for the WHOLE UTC day. Both numbers
    matter and neither is decoration: one per second keeps every
    inter-quote gap under `label_gap.max_quote_gap_seconds = 30`, so the
    fixture's labels are null only where the fixture means them to be; and
    running to 23:59:59 makes the seam into D+1 one second wide, the shape
    the real lake has. A 40-minute fixture day would hand `big_quote_gaps`
    an 84,000-second seam and null every long-horizon label in it, which
    looks exactly like the bug a coverage test is supposed to catch.

    D's trades sit half a second after the first quotes, so a decision row
    exists for a trade `etime` as well as an L1 one.
    """
    if quote_etimes is None:
        quote_etimes = [DAY_START + i * NS_PER_SECOND for i in range(86_400)]
    if quote_mids is None:
        quote_mids = [100.0 + 0.1 * (i % 50) for i in range(len(quote_etimes))]
    if trade_etimes is None:
        trade_etimes = [
            e + NS_PER_SECOND // 2 for e in quote_etimes[: min(100, len(quote_etimes))]
        ]

    trades = trade_frame(
        trade_etimes,
        [100.0] * len(trade_etimes),
        [(i + 1) / 1e8 for i in range(len(trade_etimes))],
        [1 if i % 2 else -1 for i in range(len(trade_etimes))],
        na_rows=na_rows,
        unknown_rows=unknown_rows,
    )
    out = {
        "d": seed_day(
            lake_root,
            registry_root,
            DATE,
            quotes=quote_frame(quote_etimes, quote_mids),
            trades=trades,
        )
    }
    if seed_next_day:
        # 20 minutes of D+1, twice the longest catalogued horizon, so
        # every label of D's last row resolves inside the tail.
        next_etimes = [NEXT_DAY_START + i * NS_PER_SECOND for i in range(1_200)]
        out["d1"] = seed_day(
            lake_root,
            registry_root,
            NEXT_DATE,
            quotes=quote_frame(
                next_etimes, [100.0 + 0.1 * (i % 50) for i in range(len(next_etimes))]
            ),
        )
    return out
