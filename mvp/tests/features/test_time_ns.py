"""Tests for `data/time_ns.py` -- the ONE module allowed to turn seconds into
nanoseconds for Phase 4's windows and horizons (04-CONTEXT.md D-04-13).

The point of the module is negative: every OTHER Phase 4 file imports a
pre-multiplied `int` and never multiplies, so `tools.check_ms_to_ns_site`
(which folds constants across modules) fails a second conversion convention
the moment someone writes `10 * NS_PER_SECOND` in `features/labels.py`. The
allowlist half of that invariant is tested in
`tests/tools/test_check_ms_to_ns_site_allowlist.py`; this file tests the
values themselves, that no existing importer of the old home broke, and that
no numba cache artifact has been scattered into the package tree.
"""

from __future__ import annotations

import os
from pathlib import Path

from data import time_ns
from data.dq import checks
from spec.catalogue import load_labels

PKG_ROOT = Path(__file__).resolve().parents[2]

#: Directories that never hold project source. `__pycache__` is deliberately
#: NOT pruned: it is exactly where `@njit(cache=True)` writes `*.nbi`/`*.nbc`
#: when `NUMBA_CACHE_DIR` is unset, which is what the walk below looks for.
_PRUNE_DIRNAMES = frozenset({".venv", ".pytest_cache", ".ruff_cache", ".git"})


def test_time_ns_exposes_every_phase4_window_as_ns():
    assert time_ns.NS_PER_SECOND == 1_000_000_000
    assert time_ns.NS_PER_DAY == 86_400 * time_ns.NS_PER_SECOND

    # D-04-03: the `trade_flow` trailing window is 1 s, half-open `(t-w, t]`.
    assert time_ns.TRADE_FLOW_WINDOW_NS == time_ns.NS_PER_SECOND

    assert time_ns.LABEL_HORIZON_NS == {
        "ret_1s_mid": 1_000_000_000,
        "ret_10s_mid": 10_000_000_000,
        "ret_1min_mid": 60_000_000_000,
        "ret_10min_mid": 600_000_000_000,
    }
    # Every value an `int`, never a float literal: `check_numba_globals` lets
    # an `@njit` function read UPPER_CASE module constants, and a float
    # horizon would silently widen an int64 `etime` comparison in a kernel.
    assert all(isinstance(v, int) for v in time_ns.LABEL_HORIZON_NS.values())
    assert all(isinstance(v, int) for v in (time_ns.NS_PER_SECOND, time_ns.NS_PER_DAY))
    assert isinstance(time_ns.QTY_SCALE, int)
    assert time_ns.QTY_SCALE == 100_000_000

    # Consistency is asserted in the direction that needs no second
    # conversion site: the catalogue's label NAMES must all have a horizon
    # here. Parsing "10min" into ns at runtime would be a conversion in a
    # non-allowlisted module.
    assert set(time_ns.LABEL_HORIZON_NS) == set(load_labels())


def test_dq_checks_still_exports_the_ns_constants():
    """`data/dq/checks.py` was the old home; every existing importer of
    `from data.dq.checks import NS_PER_SECOND` must keep resolving, to the
    SAME integers now defined in `data.time_ns`."""
    assert checks.NS_PER_SECOND == time_ns.NS_PER_SECOND == 1_000_000_000
    assert checks.NS_PER_DAY == time_ns.NS_PER_DAY == 86_400 * 1_000_000_000
    assert checks.NS_PER_SECOND is time_ns.NS_PER_SECOND


def test_numba_cache_dir_is_pinned_outside_the_repo():
    """`tests/conftest.py` pins `NUMBA_CACHE_DIR` before anything can import
    numba. Unset, `@njit(cache=True)` writes its `.nbi`/`.nbc` next to the
    defining source file (measured, 04-RESEARCH-NOTES.md Q3)."""
    cache_dir = os.environ.get("NUMBA_CACHE_DIR")
    assert cache_dir, "NUMBA_CACHE_DIR is not set -- see tests/conftest.py"
    assert not Path(cache_dir).resolve().is_relative_to(PKG_ROOT)


def test_no_numba_cache_artifacts_under_mvp():
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(PKG_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRNAMES]
        for name in filenames:
            if name.endswith((".nbi", ".nbc")):
                found.append(str(Path(dirpath, name).relative_to(PKG_ROOT)))
    assert not found, f"numba cache artifacts written into the package: {found}"
