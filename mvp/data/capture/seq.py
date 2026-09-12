"""Post-merge monotonic sequence assignment.

Locked contract (verbatim from SKELETON.md and data/schema.py): seq is
assigned exactly once, by the single writer, AFTER redundant connections
are merged/deduped — never inside a per-connection socket callback.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from data.capture.rotation import partition_dir


class SeqAssigner:
    """Per-(symbol, stream) monotonic counter."""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, str], int] = {}

    def next(self, symbol: str, stream: str) -> int:
        """Return the next seq value for `(symbol, stream)`, starting at 0."""
        key = (symbol, stream)
        value = self._counters.get(key, -1) + 1
        self._counters[key] = value
        return value

    def peek(self, symbol: str, stream: str) -> int:
        """Return what `.next()` would return next, without consuming it.

        Diagnostic-only (e.g. daemon startup logging of resumed seq
        counters) — never used on the hot ingest path.
        """
        return self._counters.get((symbol, stream), -1) + 1

    def seed(self, symbol: str, stream: str, last_value: int) -> None:
        """Seed the counter so the next `.next()` call returns `last_value + 1`.

        Exists so Plan 02's restart-resume path can prime the counter from
        the last row written on disk without touching this file's counter
        logic.
        """
        self._counters[(symbol, stream)] = last_value


def resume_seq_assigner(
    assigner: SeqAssigner, data_root: Path, symbol: str, streams: list[str]
) -> None:
    """Seed `assigner` from the max `seq` already persisted on disk.

    For each `stream`, globs its partition directory's `date=*/part-*.parquet`
    files (excluding any `.tmp`); if any exist, reads only the `seq` column
    across all of them and seeds the assigner with the max value found. If no
    partition files exist yet for that stream, does nothing — the counter
    correctly starts at 0 from `SeqAssigner.__init__`.
    """
    for stream in streams:
        stream_dir = partition_dir(data_root, symbol, stream)
        part_files = sorted(stream_dir.glob("date=*/part-*.parquet"))
        part_files = [p for p in part_files if p.suffix == ".parquet"]
        if not part_files:
            continue

        max_seq = -1
        for part_file in part_files:
            df = pl.read_parquet(part_file, columns=["seq"])
            if df.height == 0:
                continue
            file_max = df["seq"].max()
            if file_max is not None and file_max > max_seq:
                max_seq = file_max

        if max_seq >= 0:
            assigner.seed(symbol, stream, max_seq)
