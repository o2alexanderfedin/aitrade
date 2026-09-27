"""Hermetic fixture factories for the `lake/features/` tier tests.

Lives under `tests/fixtures/` (an existing package whose name collides with
no real top-level package) rather than under `tests/features/`, which must
NOT be a package: `mvp/features/` is a real package and a same-named test
package shadows it on `sys.path` (04-01-SUMMARY.md, and the same lesson
from Phase 2).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl

from data.store import dq_report_path, issue_manifest
from features.tier import FEATURE_ROW_SCHEMA, FEATURE_SCHEMA_VERSION

SYMBOL = "BTCUSDT"
DATE = "2026-09-13"
NEXT_DATE = "2026-09-14"

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


def feature_frame(rows: int = 3, *, nan_in: str | None = None) -> pl.DataFrame:
    """A well-formed `FEATURE_ROW_SCHEMA` frame, optionally with one NaN
    planted in `nan_in` (the writer must turn it into a NULL)."""
    data: dict[str, list] = {}
    for name, dtype in FEATURE_ROW_SCHEMA.items():
        if name == "etime":
            data[name] = [1_000 * (i + 1) for i in range(rows)]
        elif name == "decision_source_rank":
            data[name] = [0] * rows
        elif name == "decision_seq":
            data[name] = list(range(rows))
        elif name == "schema_version":
            data[name] = [FEATURE_SCHEMA_VERSION] * rows
        elif dtype == pl.Boolean:
            data[name] = [False] * rows
        else:
            data[name] = [float(i + 1) for i in range(rows)]
    if nan_in is not None:
        data[nan_in] = [float("nan"), *data[nan_in][1:]]
    return pl.DataFrame(data, schema=FEATURE_ROW_SCHEMA)


def _curated_frame(stream: str, date: str) -> pl.DataFrame:
    if stream == "trade":
        return pl.DataFrame(
            {"trade_id": [1, 2], "etime": [1_000, 2_000], "price": [1.0, 2.0]}
        )
    return pl.DataFrame(
        {
            "etime": [1_000, 2_000],
            "bid_price": [100.0, 100.1],
            "ask_price": [100.2, 100.3],
        }
    )


def issue_curated_day(
    lake_root: Path, registry_root: Path, stream: str, date: str
) -> dict:
    """Write one curated partition for `(stream, date)` and issue its
    manifest -- the input a real feature build would consume."""
    rel = f"curated/symbol={SYMBOL}/stream={stream}/date={date}/part-1.parquet"
    path = lake_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    df = _curated_frame(stream, date)
    df.write_parquet(path, compression="zstd")
    st = path.stat()
    part = {
        "date": date,
        "path": rel,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": df.height,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": int(df["etime"].min()),
        "etime_max": int(df["etime"].max()),
    }
    return issue_manifest(
        dataset=f"{SYMBOL}.{stream}",
        symbol=SYMBOL,
        stream=stream,
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
        dates=[date],
    )


def write_features_dq_report(
    lake_root: Path,
    date: str,
    manifest_id: str,
    *,
    dq_status: str = "ok",
    check: str = "feature_row_filters",
) -> Path:
    """A minimal `report.parquet` scoring `manifest_id` on the features
    stream -- a features manifest with no rows of its own is `missing` in
    `store._dq_verdict_for_date`, which pauses the loader."""
    path = dq_report_path(lake_root, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "date": [date],
            "symbol": [SYMBOL],
            "stream": ["features"],
            "manifest_id": [manifest_id],
            "check": [check],
            "dq_status": [dq_status],
            "value": [0.0],
            "count": [None],
            "detail": [None],
        },
        schema=REPORT_SCHEMA,
    ).write_parquet(path)
    return path


def write_holdout_registry(
    registry_root: Path, dates: list[str], *, symbol: str = SYMBOL, body: object = None
) -> Path:
    """Write a holdout registry under `registry_root`. `body` overrides the
    whole document (used by the malformed-registry tests)."""
    from data.holdout import holdout_registry_path

    path = holdout_registry_path(registry_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if body is None:
        body = {
            "version": 1,
            "symbol": symbol,
            "dates": dates,
            "locked_at": 1_700_000_000_000_000_000,
            "reason": "test fixture",
        }
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    return path
