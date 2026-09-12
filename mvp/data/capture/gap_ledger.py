"""Persisted ledger of detected capture outages.

Reactive only: `record_gap()` is called by `rotation.consume()` when a
message arrives after a per-stream silence longer than a configured
threshold — it records the gap once the silence is over. A proactive
watchdog that detects total silence *while it is happening* (no message
ever arriving to trigger this reactive check) is Plan 04's job, not this
plan's.

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
}


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
            },
            schema=GAP_LEDGER_SCHEMA,
        )

        if self._path.exists():
            existing = pl.read_parquet(self._path)
            combined = pl.concat([existing, new_row])
        else:
            combined = new_row

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        combined.write_parquet(tmp_path, compression="zstd")
        tmp_path.replace(self._path)

    def read_all(self) -> pl.DataFrame:
        if self._path.exists():
            return pl.read_parquet(self._path)
        return pl.DataFrame(schema=GAP_LEDGER_SCHEMA)
