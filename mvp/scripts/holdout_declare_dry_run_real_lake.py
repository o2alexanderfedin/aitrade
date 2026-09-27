"""Standalone, read-only proof that `harness.holdout_declare.dry_run` is
safe to run against the REAL lake and the REAL, git-committed registry
right now -- with the physical `chmod 0000` barrier around the quarantined
tier still in place, and untouched by this script (05-05-PLAN.md, D-05-17,
checker iteration 1 warning 1).

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.holdout_declare_dry_run_real_lake [DATE]

`DATE` defaults to `2027-01-01`, a synthetic future date with nothing
built, so the script can run today without declaring anything real. Pass a
real built date (e.g. `2026-09-14`) to see `dry_run` name the actual
`D`/`D-1` partitions and manifest ids it would move.

Deliberately does NOT call `data.lake_paths.lake_root()` (which
`mkdir`s and write-probes the target directory) -- this script reads the
real lake root directly, never validates or touches it for write.

The before/after snapshot walks the real lake root and the real registry
root, recording `(size, mtime_ns)` per file. It NEVER names the
quarantined tier's own directory segment anywhere in this file (not even
to prune it): `os.walk`'s `onerror` callback silently absorbs the
`PermissionError` that `chmod 0000` raises the moment `os.scandir` tries
to list it, so that branch is simply never descended into -- the same
"blinds `iterdir`/`glob`" behaviour `data/lockbox_POLICY.md` documents,
relied on here rather than reproduced.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from data.lake_paths import DEFAULT_LAKE_ROOT, LAKE_REGISTRY_ROOT
from harness.holdout_declare import dry_run

SYMBOL = "BTCUSDT"
DEFAULT_CANDIDATE_DATE = "2027-01-01"


def _ignore_unreadable(_error: OSError) -> None:
    """`os.walk`'s `onerror` hook: a directory `os.scandir` cannot list
    (the `chmod 0000` barrier) is silently treated as a dead end, never
    raised -- this is what keeps that branch unentered without this script
    ever naming it."""
    return None


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    entries: dict[str, tuple[int, int]] = {}
    for dirpath, _dirnames, filenames in os.walk(root, onerror=_ignore_unreadable):
        for fname in filenames:
            path = Path(dirpath) / fname
            st = path.stat()
            entries[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return entries


def main(argv: list[str]) -> int:
    candidate_date = argv[0] if argv else DEFAULT_CANDIDATE_DATE

    real_lake_root = Path(DEFAULT_LAKE_ROOT)
    real_registry_root = LAKE_REGISTRY_ROOT

    print(f"candidate date: {candidate_date}")
    print(f"real lake root: {real_lake_root}")
    print(f"real registry root: {real_registry_root}")

    before_lake = _snapshot(real_lake_root)
    before_registry = _snapshot(real_registry_root)

    report = dry_run(
        [candidate_date],
        symbol=SYMBOL,
        registry_root=real_registry_root,
        lake_root=real_lake_root,
    )

    after_lake = _snapshot(real_lake_root)
    after_registry = _snapshot(real_registry_root)

    print("dry_run report:")
    print(f"  would_move: {report['would_move']}")
    print(f"  would_stop_resolving: {report['would_stop_resolving']}")
    print(f"  already_declared: {report['already_declared']}")

    lake_unchanged = before_lake == after_lake
    registry_unchanged = before_registry == after_registry
    print(f"lake snapshot unchanged: {lake_unchanged}")
    print(f"registry snapshot unchanged: {registry_unchanged}")

    ok = lake_unchanged and registry_unchanged
    if not ok:
        lake_diff = set(before_lake.items()) ^ set(after_lake.items())
        registry_diff = set(before_registry.items()) ^ set(after_registry.items())
        print(f"FAIL: lake diff: {lake_diff}")
        print(f"FAIL: registry diff: {registry_diff}")
        return 1

    print(
        f"OK: dry_run against the real lake for {candidate_date} touched nothing "
        f"({len(before_lake)} lake files, {len(before_registry)} registry files "
        "unchanged)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
