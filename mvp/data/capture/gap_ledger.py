"""Persisted ledger of detected capture outages.

Reactive (`record_gap()` called by `rotation.consume()` when a subsequent
frame reveals a just-ended silence) AND proactive (called by
`watchdog.py`'s periodic tick, which detects total, still-ongoing silence
even when no later frame ever arrives to trigger the reactive path).

`ledger_version` (Plan 04): Plan 03's reactive rule fired on PER-STREAM
silence, which conflated market lulls with real capture outages (two
false-positive `trade` rows logged live in 17 minutes while bookTicker
flowed and `trade_id` stayed contiguous). Plan 04 replaces that rule
entirely with three connection-keyed signals (`connection-silent`,
`merged-silent`, `trade-id-skip`) recorded by `rotation.consume()`, plus
`watchdog.py`'s proactive stall/free-space alarms. Existing rows already on
disk from Plan 03's defective policy predate this column and are backfilled
with `ledger_version=1` on read; every row this module writes from Plan 04
onward carries `ledger_version=2`. Phase 3 DQ reports should filter
`ledger_version >= 2` to exclude the two known Plan 03 false positives
without deleting the historical record of what the daemon actually
reported at the time (see `01-04-SUMMARY.md` for the specific rows and the
decision to version rather than delete).

Uses the same tmp-path + `Path.replace()` atomic-write idiom as
`rotation.write_partition_atomic` (inlined here, not imported, to avoid a
`rotation.py` <-> `gap_ledger.py` import cycle — `rotation.py` imports
this module).
"""

from __future__ import annotations

import time
from pathlib import Path

import polars as pl

GAP_LEDGER_SCHEMA: dict[str, pl.DataType] = {
    "stream": pl.Utf8,
    "conn_id": pl.Utf8,
    "gap_start_rtime": pl.Int64,
    "gap_end_rtime": pl.Int64,
    "cause": pl.Utf8,
    "detected_at": pl.Int64,
    "ledger_version": pl.Int32,
}

# v1: Plan 03's per-stream reactive rule (defective policy, superseded).
# v2: Plan 04's connection-silent/merged-silent/trade-id-skip policy plus
#     watchdog.py's proactive stall/free-space alarms.
CURRENT_LEDGER_VERSION = 2
LEGACY_LEDGER_VERSION = 1


class GapLedger:
    """Append-only, atomically-written Parquet ledger of capture outages."""

    def __init__(self, data_root: Path) -> None:
        self._path = Path(data_root) / "gap_ledger" / "ledger.parquet"

    def record_gap(
        self,
        stream: str,
        conn_id: str,
        gap_start_rtime: int,
        gap_end_rtime: int,
        cause: str,
    ) -> None:
        new_row = pl.DataFrame(
            {
                "stream": [stream],
                "conn_id": [conn_id],
                "gap_start_rtime": [gap_start_rtime],
                "gap_end_rtime": [gap_end_rtime],
                "cause": [cause],
                "detected_at": [time.time_ns()],
                "ledger_version": [CURRENT_LEDGER_VERSION],
            },
            schema=GAP_LEDGER_SCHEMA,
        )

        if self._path.exists():
            # read_all() backfills ledger_version on legacy (Plan 03) rows
            # so this concat never hits a schema mismatch against new_row.
            existing = self.read_all()
            combined = pl.concat([existing, new_row])
        else:
            combined = new_row

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        combined.write_parquet(tmp_path, compression="zstd")
        tmp_path.replace(self._path)

    def read_all(self) -> pl.DataFrame:
        if not self._path.exists():
            return pl.DataFrame(schema=GAP_LEDGER_SCHEMA)
        df = pl.read_parquet(self._path)
        if "ledger_version" not in df.columns:
            df = df.with_columns(
                pl.lit(LEGACY_LEDGER_VERSION, dtype=pl.Int32).alias("ledger_version")
            )
        return df
