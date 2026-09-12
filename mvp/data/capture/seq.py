"""Post-merge monotonic sequence assignment.

Locked contract (verbatim from SKELETON.md and data/schema.py): seq is
assigned exactly once, by the single writer, AFTER redundant connections
are merged/deduped — never inside a per-connection socket callback.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from data.capture.rotation import (
    part_ns_of,
    partition_dir,
    read_seq_sidecar,
    write_seq_state_atomic,
)


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

    Plan 04 correction (found live in Plan 02's checkpoint): the original
    implementation globbed `date=*/part-*.parquet` across EVERY partition
    ever written and read `seq` back from each one -- 1,261 files cost
    ~14s of daemon downtime; at ~2,600 partitions/day that reaches minutes
    within weeks, and every second of startup is lost, unrecoverable
    capture.

    Fixed by reading `<data_root>/seq_state.json` (`read_seq_sidecar`)
    first -- a tiny sidecar `rotation.consume()` writes atomically after
    every successful flush. If the sidecar's recorded `part_ns` for a
    stream already matches or exceeds every partition file present in that
    stream's NEWEST `date=*` directory, zero partition files are opened at
    all. Only partition files newer than the sidecar's `part_ns` (i.e. ones
    written after a crash cut the process off between the Parquet write and
    the sidecar update) are opened, via `pl.scan_parquet` rather than a
    per-file Python loop, and the resumed value is `max(sidecar, scan)` --
    never lower than what is actually on disk. If the sidecar is absent
    entirely (fresh store, or first run before Plan 04), every file in the
    newest `date=*` directory is scanned (still never the full history).
    Either way, the corrected value is written back to the sidecar
    (self-heal), so the *next* restart is sidecar-only again.

    If no partition files exist yet for a stream and no sidecar entry
    exists either, does nothing — the counter correctly starts at 0 from
    `SeqAssigner.__init__`.
    """
    sidecar = read_seq_sidecar(data_root, symbol)

    for stream in streams:
        entry = sidecar.get(stream)
        sidecar_seq = entry["seq"] if entry else None
        sidecar_part_ns = entry["part_ns"] if entry else None

        scan_seq: int | None = None
        newest_part_ns = sidecar_part_ns
        candidates: list[Path] = []

        stream_dir = partition_dir(data_root, symbol, stream)
        if stream_dir.exists():
            date_dirs = sorted(
                p for p in stream_dir.iterdir() if p.is_dir() and p.name.startswith("date=")
            )
            if date_dirs:
                newest_dir = date_dirs[-1]
                part_files = sorted(
                    p
                    for p in newest_dir.glob("part-*.parquet")
                    if p.suffix == ".parquet"
                )
                candidates = (
                    [p for p in part_files if part_ns_of(p) > sidecar_part_ns]
                    if sidecar_part_ns is not None
                    else part_files
                )

                if candidates:
                    lazy_frames = [pl.scan_parquet(p) for p in candidates]
                    lf = lazy_frames[0] if len(lazy_frames) == 1 else pl.concat(lazy_frames)
                    result = lf.select(pl.col("seq").max().alias("m")).collect()
                    if result.height and result["m"][0] is not None:
                        scan_seq = int(result["m"][0])
                        newest_part_ns = max(part_ns_of(p) for p in candidates)

        candidate_values = [v for v in (sidecar_seq, scan_seq) if v is not None]
        if not candidate_values:
            continue

        resumed = max(candidate_values)
        assigner.seed(symbol, stream, resumed)

        if scan_seq is None:
            source = "sidecar"
        elif sidecar_seq is None:
            source = f"scan(newest-date, {len(candidates)} files)"
        else:
            source = f"sidecar+{len(candidates)} newer files scanned"
        print(
            f"seq resume source: symbol={symbol} stream={stream} source={source}",
            flush=True,
        )

        # Self-heal: the scan found data the sidecar didn't already cover
        # (sidecar absent/stale) -- persist the corrected value so the next
        # restart can trust the sidecar alone again.
        if scan_seq is not None and (sidecar_seq is None or scan_seq > sidecar_seq):
            write_seq_state_atomic(
                data_root,
                symbol,
                {stream: {"seq": resumed, "part_ns": newest_part_ns or 0}},
            )
