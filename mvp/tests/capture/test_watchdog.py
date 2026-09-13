"""Hardening tests for watchdog.py's Watchdog -- turns 01-04-PLAN.md Task
1's <behavior> cases into executable, committed tests. No real sleeping:
`_tick()` is called directly, and `run()` is exercised with
`interval_seconds=0` and a pre-set `shutdown_event` for exactly one
iteration.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from collections import namedtuple
from pathlib import Path

import pytest

from data.capture.gap_ledger import GapLedger
from data.capture.watchdog import Watchdog

_DiskUsage = namedtuple("_DiskUsage", ["total", "used", "free"])


@pytest.fixture(autouse=True)
def _healthy_disk_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the host's real free space.

    The watchdog checks `shutil.disk_usage(data_root)` on every tick and
    records a `__disk__` ledger row when free < min_free_gb (50 GiB default).
    `tmp_path` lives on the developer's internal volume, so the stall-only
    tests silently gained an extra ledger row the moment that volume dipped
    below 50 GiB — they passed on 2026-09-12 and failed on 2026-09-13 with no
    code change. Tests must not depend on how full the developer's laptop is.
    The three disk-specific tests below re-patch `disk_usage` in-body and are
    unaffected by this default.
    """
    plenty = _DiskUsage(total=1_000_000_000_000, used=1_000_000_000, free=900_000_000_000)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: plenty)



def test_stall_beyond_threshold_records_one_row_then_does_not_duplicate(
    tmp_path: Path,
) -> None:
    """A stalls (40s > 30s threshold) while B keeps "merged" fresh (B's own
    frames keep arriving, so merged tracks B's recency, not A's) -> one
    connection-silent-ongoing row for A, no merged-silent row (mirrors
    rotation.py's reactive "one connection silent while the other flows"
    case). A second consecutive tick within the same ongoing stall does
    NOT record a duplicate row."""
    now_ns = time.time_ns()
    last_seen_state = {"A": now_ns - 40_000_000_000, "B": now_ns, "merged": now_ns}
    ledger = GapLedger(tmp_path)
    watchdog = Watchdog(
        last_seen_state, ledger, tmp_path, conn_ids=["A", "B"], stall_threshold_seconds=30.0
    )

    watchdog._tick()
    watchdog._tick()

    df = ledger.read_all()
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["conn_id"] == "A"
    assert row["cause"].startswith("connection-silent-ongoing")


def test_both_connections_stalled_records_only_merged_not_per_connection(
    tmp_path: Path,
) -> None:
    """Both A and B individually exceed the threshold at once (merged is
    therefore also stale, since neither sent anything to refresh it) ->
    only ONE merged-silent-ongoing row, not three (merged + A + B) --
    mirrors rotation.py's reactive merged-silent-takes-priority rule."""
    now_ns = time.time_ns()
    last_seen_state = {
        "A": now_ns - 40_000_000_000,
        "B": now_ns - 40_000_000_000,
        "merged": now_ns - 40_000_000_000,
    }
    ledger = GapLedger(tmp_path)
    watchdog = Watchdog(
        last_seen_state, ledger, tmp_path, conn_ids=["A", "B"], stall_threshold_seconds=30.0
    )

    watchdog._tick()

    df = ledger.read_all()
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["conn_id"] == "watchdog"
    assert row["cause"].startswith("merged-silent-ongoing")


def test_stall_resolves_then_recurs_records_two_rows(tmp_path: Path) -> None:
    """Once the stream resumes (last_seen_state advances), the stall flag
    resets, so a SUBSEQUENT stall on the same connection records again --
    one row per outage, not one row ever."""
    now_ns = time.time_ns()
    last_seen_state = {
        "A": now_ns - 40_000_000_000,
        "B": now_ns - 40_000_000_000,
        "merged": now_ns - 40_000_000_000,
    }
    ledger = GapLedger(tmp_path)
    watchdog = Watchdog(
        last_seen_state, ledger, tmp_path, conn_ids=["A", "B"], stall_threshold_seconds=30.0
    )

    watchdog._tick()
    assert ledger.read_all().height == 1  # only merged-silent (both equally stale)

    # Connection resumes.
    last_seen_state["A"] = time.time_ns()
    last_seen_state["B"] = time.time_ns()
    last_seen_state["merged"] = time.time_ns()
    watchdog._tick()
    assert ledger.read_all().height == 1  # unchanged -- no new stall

    # Goes stale again -- a genuinely new, second outage.
    last_seen_state["A"] = time.time_ns() - 40_000_000_000
    last_seen_state["B"] = time.time_ns() - 40_000_000_000
    last_seen_state["merged"] = time.time_ns() - 40_000_000_000
    watchdog._tick()
    assert ledger.read_all().height == 2


def test_both_streams_seen_recently_records_zero_rows(tmp_path: Path) -> None:
    now_ns = time.time_ns()
    last_seen_state = {"A": now_ns - 500_000_000, "B": now_ns - 200_000_000, "merged": now_ns - 200_000_000}
    ledger = GapLedger(tmp_path)
    watchdog = Watchdog(
        last_seen_state, ledger, tmp_path, conn_ids=["A", "B"], stall_threshold_seconds=30.0
    )

    watchdog._tick()

    assert ledger.read_all().height == 0


def test_connection_not_yet_started_is_not_alarmed(tmp_path: Path) -> None:
    """conn_id "B" absent from last_seen_state (e.g. during daemon.py's
    stagger window, before B has ever connected) must not be treated as a
    stall -- absence is "not started yet", not "silent"."""
    now_ns = time.time_ns()
    last_seen_state = {"A": now_ns, "merged": now_ns}  # "B" deliberately absent
    ledger = GapLedger(tmp_path)
    watchdog = Watchdog(
        last_seen_state, ledger, tmp_path, conn_ids=["A", "B"], stall_threshold_seconds=30.0
    )

    watchdog._tick()

    assert ledger.read_all().height == 0


def test_low_free_space_records_one_row_with_free_in_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now_ns = time.time_ns()
    last_seen_state = {"A": now_ns, "B": now_ns, "merged": now_ns}
    ledger = GapLedger(tmp_path)
    watchdog = Watchdog(
        last_seen_state, ledger, tmp_path, conn_ids=["A", "B"], min_free_gb=50.0
    )

    fake_usage = _DiskUsage(total=1_000_000_000_000, used=999_000_000_000, free=1_000_000_000)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: fake_usage)

    watchdog._tick()

    df = ledger.read_all()
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["conn_id"] == "watchdog"
    assert "free" in row["cause"]


def test_sufficient_free_space_records_zero_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now_ns = time.time_ns()
    last_seen_state = {"A": now_ns, "B": now_ns, "merged": now_ns}
    ledger = GapLedger(tmp_path)
    watchdog = Watchdog(
        last_seen_state, ledger, tmp_path, conn_ids=["A", "B"], min_free_gb=50.0
    )

    fake_usage = _DiskUsage(total=1_000_000_000_000, used=1_000_000_000, free=900_000_000_000)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: fake_usage)

    watchdog._tick()

    assert ledger.read_all().height == 0


def test_disk_usage_raising_oserror_is_recorded_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deferred external-volume-unmount operational check's failure
    mode: shutil.disk_usage() itself can raise if the volume disappears.
    The watchdog must record it, not crash."""
    now_ns = time.time_ns()
    last_seen_state = {"A": now_ns, "B": now_ns, "merged": now_ns}
    ledger = GapLedger(tmp_path)
    watchdog = Watchdog(last_seen_state, ledger, tmp_path, conn_ids=["A", "B"])

    def _boom(_path):
        raise OSError("volume unmounted")

    monkeypatch.setattr(shutil, "disk_usage", _boom)

    watchdog._tick()  # must not raise

    df = ledger.read_all()
    assert df.height == 1
    assert "free space check failed" in df.row(0, named=True)["cause"]


def test_run_single_iteration_with_preset_shutdown_event_ticks_exactly_once(
    tmp_path: Path,
) -> None:
    """run() with a pre-set shutdown_event and interval_seconds=0 executes
    exactly one tick then returns immediately -- no real sleeping."""
    now_ns = time.time_ns()
    last_seen_state = {"A": now_ns - 40_000_000_000, "B": now_ns - 40_000_000_000, "merged": now_ns - 40_000_000_000}
    ledger = GapLedger(tmp_path)
    watchdog = Watchdog(
        last_seen_state, ledger, tmp_path, conn_ids=["A", "B"], stall_threshold_seconds=30.0
    )
    shutdown_event = asyncio.Event()
    shutdown_event.set()

    start = time.monotonic()
    asyncio.run(watchdog.run(interval_seconds=0, shutdown_event=shutdown_event))
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert ledger.read_all().height >= 1
