"""Proactive liveness watchdog: periodic stall alarm + continuous
free-space monitoring.

`rotation.consume()`'s connection-silent/merged-silent checks (see its
module docstring) are REACTIVE: they only fire when a subsequent frame
arrives to reveal that a preceding silence exceeded the threshold. A
connection that goes silent and NEVER sends another frame has no later
frame to trigger that check at all -- exactly the case this module closes,
by ticking on a fixed wall-clock interval independent of whether any frame
ever arrives again.

Per-connection, not per-stream (a deliberate divergence from this module's
originally-planned per-stream `streams: list[str]` contract, forced by
01-04-PLAN.md's own later "gap-ledger policy correction" block, which
mandates removing every per-stream silence rule -- see 01-04-SUMMARY.md's
Deviations for the full reasoning). `last_seen_state` is the SAME dict
`rotation.consume()` shares and updates (keyed by each `conn_id` plus the
literal string `"merged"`), so this task observes exactly what the
consumer sees without duplicating any tracking logic.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from data.capture.config import DEFAULT_MIN_FREE_GB
from data.capture.gap_ledger import GapLedger


class Watchdog:
    """Periodic stall alarm (per connection + merged) and continuous
    free-space monitor, both recorded via the shared `GapLedger`."""

    def __init__(
        self,
        last_seen_state: dict[str, int],
        gap_ledger: GapLedger,
        data_root: Path,
        conn_ids: list[str],
        stall_threshold_seconds: float = 30.0,
        min_free_gb: float = DEFAULT_MIN_FREE_GB,
    ) -> None:
        self.last_seen_state = last_seen_state
        self.gap_ledger = gap_ledger
        self.data_root = Path(data_root)
        self.conn_ids = conn_ids
        self.stall_threshold_seconds = stall_threshold_seconds
        self.min_free_gb = min_free_gb

        # One row per ONGOING outage, not one row per tick -- mirrors
        # rotation.consume()'s reactive one-row-per-outage semantics.
        # Keyed by conn_id, plus the literal key "merged".
        self._active_stall: dict[str, bool] = {}
        self._low_space_alarmed = False

    def _tick(self) -> None:
        now_ns = time.time_ns()

        for key in (*self.conn_ids, "merged"):
            last_seen = self.last_seen_state.get(key)
            if last_seen is None:
                # Not started yet (e.g. connection B during daemon.py's
                # stagger window) -- absence is not silence, do not alarm
                # on a connection that was never supposed to be live yet.
                self._active_stall[key] = False
                continue

            silence_ns = now_ns - last_seen
            if silence_ns > self.stall_threshold_seconds * 1e9:
                if not self._active_stall.get(key, False):
                    silence_seconds = silence_ns / 1e9
                    cause = (
                        f"merged-silent-ongoing: no message from either connection "
                        f"for {silence_seconds:.1f}s (watchdog)"
                        if key == "merged"
                        else (
                            f"connection-silent-ongoing: no message from "
                            f"conn_id={key} for {silence_seconds:.1f}s (watchdog)"
                        )
                    )
                    self.gap_ledger.record_gap(
                        stream="__connection__",
                        conn_id="watchdog" if key == "merged" else key,
                        gap_start_rtime=last_seen,
                        gap_end_rtime=now_ns,
                        cause=cause,
                    )
                    self._active_stall[key] = True
            else:
                self._active_stall[key] = False

        try:
            free_bytes = shutil.disk_usage(self.data_root).free
        except OSError as exc:
            # The deferred external-volume-unmount operational check's
            # failure mode: disk_usage() itself can raise if the volume
            # disappears mid-run. Record it and keep looping -- a dead
            # watchdog task is worse than a noisy one.
            if not self._low_space_alarmed:
                self.gap_ledger.record_gap(
                    stream="__disk__",
                    conn_id="watchdog",
                    gap_start_rtime=now_ns,
                    gap_end_rtime=now_ns,
                    cause=f"free space check failed: {exc}",
                )
                self._low_space_alarmed = True
            return

        free_gb = free_bytes / (1024**3)
        if free_gb < self.min_free_gb:
            if not self._low_space_alarmed:
                self.gap_ledger.record_gap(
                    stream="__disk__",
                    conn_id="watchdog",
                    gap_start_rtime=now_ns,
                    gap_end_rtime=now_ns,
                    cause=(
                        f"free space {free_gb:.1f} GiB below minimum "
                        f"{self.min_free_gb} GiB"
                    ),
                )
                self._low_space_alarmed = True
        else:
            self._low_space_alarmed = False

    async def run(
        self,
        interval_seconds: float = 30.0,
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        """Tick once immediately, then every `interval_seconds`, until
        `shutdown_event` is set. Breaks promptly on shutdown rather than
        sleeping out the full interval (a long interval must never delay
        the daemon's proven ~1.5s graceful-shutdown flush)."""
        print(
            f"watchdog started: interval={interval_seconds}s "
            f"stall_threshold={self.stall_threshold_seconds}s "
            f"min_free_gb={self.min_free_gb} conn_ids={self.conn_ids}",
            flush=True,
        )
        while True:
            self._tick()

            if shutdown_event is not None and shutdown_event.is_set():
                return

            if shutdown_event is None:
                await asyncio.sleep(interval_seconds)
                continue

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue
            else:
                return
