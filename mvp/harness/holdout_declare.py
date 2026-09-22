"""The held-out declaration tool (05-05-PLAN.md, D-05-16/17/18) -- built,
tested end to end, and NEVER INVOKED by any Phase 5 plan, fixture, or CLI
wiring. Phase 8 is the first real caller (D-05-17: "Phase 5 builds the
declaration tool; it does not declare").

`dry_run` needs no write access at all and is safe to run against the real
lake right now, `chmod 0000` barrier and all (`mvp/scripts/
holdout_declare_dry_run_real_lake.py` is the standalone, non-pytest proof
of that). `declare` is the real orchestration: it moves `D_lock` AND
`D_lock - 1`'s feature partitions (D-05-18 -- `D_lock - 1`'s long-horizon
label tail carries `D_lock`'s prices, so leaving it in the readable tier
would hand the held-out window back through a neighbouring day's labels,
exactly what `features.tier.assert_buildable`/`refused_dates_for` already
refuse for ordinary builds and reads), THEN writes `holdout.json` --
partitions move BEFORE the registry declares them, so a crash mid-way
leaves an undeclared-but-partially-quarantined state, never a
declared-but-still-readable one.

Orchestrates around the ONE sanctioned crossing into the quarantined
tier's own path construction (`data.lockbox.quarantine_feature_partition`)
-- this module itself never spells that path; every date or manifest id
it reports came back from a return value, never a literal it built.
"""

from __future__ import annotations

import json
from pathlib import Path

from data.dates import prev_utc_date
from data.holdout import quarantined_dates, write_holdout_registry
from data.lockbox import quarantine_feature_partition
from data.store import FEATURES_TIER, by_date_index_path, manifest_path

__all__ = ["dry_run", "declare"]


def _d_minus_1(date: str) -> str:
    """`D_lock - 1` -- the day whose long-horizon label tail carries
    `D_lock`'s prices (D-05-18). A thin, named wrapper around
    `data.dates.prev_utc_date` (the one home for calendar arithmetic this
    codebase keeps; see that module's own docstring) rather than a second,
    harness-local implementation of "subtract a day from a YYYY-MM-DD
    string"."""
    return prev_utc_date(date)


def _partition_status(
    date: str, *, symbol: str, registry_root: Path, lake_root: Path
) -> dict | None:
    """Read-only lookup: `None` if no features partition is on record for
    `date` -- no by-date pointer, no committed manifest body, or the
    manifest's own recorded partition path does not actually exist on
    disk. Otherwise `{"date": date, "manifest_id": ...}`.

    Deliberately never `resolve_manifest`'s hash-verifying read (which
    raises loudly on a manifest that was never built, exactly the case a
    dry run must handle quietly): reads the committed manifest JSON body
    directly, with no integrity check, matching what a report of "what the
    registry claims" needs and no more.
    """
    dataset = f"{symbol}.{FEATURES_TIER}"
    pointer = by_date_index_path(registry_root, dataset, symbol, FEATURES_TIER, date)
    if not pointer.exists():
        return None
    manifest_id = json.loads(pointer.read_text())["manifest_id"]
    manifest_file = manifest_path(Path(registry_root), dataset, manifest_id)
    if not manifest_file.exists():
        return None
    body = json.loads(manifest_file.read_text())
    partitions = body.get("partitions") or []
    if not partitions:
        return None
    on_disk = Path(lake_root) / partitions[0]["path"]
    if not on_disk.exists():
        return None
    return {"date": date, "manifest_id": manifest_id}


def _candidate_dates(dates: list[str]) -> list[str]:
    """`dates`, plus each one's `D - 1`, in order, each candidate named
    once even if two input dates would otherwise derive the same
    `D - 1`."""
    seen: set[str] = set()
    ordered: list[str] = []
    for date in dates:
        for candidate in (date, _d_minus_1(date)):
            if candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)
    return ordered


def dry_run(
    dates: list[str], *, symbol: str, registry_root: Path, lake_root: Path
) -> dict:
    """Report exactly which partitions WOULD move and which manifests
    WOULD stop resolving if `declare(dates, ...)` ran for real -- writes
    nothing, ever, and needs no write access to `registry_root` or
    `lake_root` (every read below is a plain file existence check or a
    `read_text`/`json.loads`, never a directory listing of anything this
    tool is not allowed to see, and never a call into the quarantined
    tier at all).

    For each date in `dates`, both it AND its `D - 1` (D-05-18) are
    considered -- a `D - 1` with nothing built (the common case: the
    declared date's neighbour may itself not exist, or may already be the
    lake's own most recent day) is reported as simply absent from
    `would_move`, not an error.

    Returns `{"would_move": [{"date", "manifest_id"}, ...],
    "would_stop_resolving": [manifest_id, ...], "already_declared": bool}`.
    """
    already = quarantined_dates(registry_root=registry_root, symbol=symbol).declared
    would_move: list[dict] = []
    would_stop_resolving: list[str] = []
    for candidate in _candidate_dates(dates):
        status = _partition_status(
            candidate, symbol=symbol, registry_root=registry_root, lake_root=lake_root
        )
        if status is None:
            continue
        would_move.append(status)
        would_stop_resolving.append(status["manifest_id"])
    return {
        "would_move": would_move,
        "would_stop_resolving": would_stop_resolving,
        "already_declared": already,
    }


def declare(
    dates: list[str],
    *,
    symbol: str,
    registry_root: Path,
    lake_root: Path,
    code_hash: str,
    reason: str,
) -> dict:
    """The real orchestration -- built, tested, and NEVER invoked by any
    Phase 5 plan, fixture, or CLI wiring. Phase 8 is the first real caller.

    (1) Refuses (`ValueError`) if a holdout window is already declared for
        `symbol`, naming the existing declaration -- BEFORE touching any
        partition (write-once, matching `write_holdout_registry`'s own
        posture one layer up).
    (2) For each date in `dates` and its `D - 1` that has a built
        partition on record, calls `data.lockbox.quarantine_feature_partition`
        -- the one sanctioned crossing into the quarantined tier's own
        path construction. A `D - 1` with nothing built is skipped, not an
        error (D-05-16: the most recent built day never has a successor
        yet, and `D_lock` itself may have nothing built at all -- the
        ordinary forward-declaration case).
    (3) ONLY THEN calls `data.holdout.write_holdout_registry(dates, ...)`
        -- partitions move BEFORE the registry declares them, so a crash
        between steps (2) and (3) leaves an undeclared-but-partially
        -quarantined state, never a declared-but-still-readable one. Note
        `dates` here is the caller's ORIGINAL list (e.g. just `[D_lock]`),
        not the expanded `D`/`D-1` candidate set: `D_lock - 1`'s own
        manifests are refused going forward via `refused_dates_for`'s
        existing D+1 derivation once `D_lock` itself is in the registry,
        so `D_lock - 1` does not need its own entry in `holdout.json`'s
        `dates` list.

    Returns `{"moved": [lockbox manifest dict, ...],
    "holdout_registry_path": Path}`.
    """
    existing = quarantined_dates(registry_root=registry_root, symbol=symbol)
    if existing.declared:
        raise ValueError(
            f"declare: a holdout window is already declared for {symbol} "
            f"({sorted(existing)!r}) -- refusing a second declaration before "
            "touching any partition"
        )
    moved: list[dict] = []
    for candidate in _candidate_dates(dates):
        status = _partition_status(
            candidate, symbol=symbol, registry_root=registry_root, lake_root=lake_root
        )
        if status is None:
            continue
        manifest = quarantine_feature_partition(
            candidate,
            symbol=symbol,
            lake_root=lake_root,
            registry_root=registry_root,
            code_hash=code_hash,
            reason=reason,
        )
        moved.append(manifest)
    registry_path = write_holdout_registry(
        dates, reason=reason, symbol=symbol, registry_root=registry_root
    )
    return {"moved": moved, "holdout_registry_path": registry_path}


if __name__ == "__main__":
    import argparse

    from data.lake_paths import LAKE_REGISTRY_ROOT
    from data.lake_paths import lake_root as _resolve_lake_root

    parser = argparse.ArgumentParser(
        description=(
            "Holdout declaration tool. Without --declare, prints a dry_run "
            "report and touches nothing. --declare performs the real, "
            "irreversible partition move plus the write-once holdout.json "
            "write."
        )
    )
    parser.add_argument("dates", nargs="+", help="YYYY-MM-DD date(s) to declare")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--registry-root", type=Path, default=None)
    parser.add_argument("--lake-root", type=Path, default=None)
    parser.add_argument("--code-hash", default=None)
    parser.add_argument("--reason", default=None)
    parser.add_argument(
        "--declare",
        action="store_true",
        help="Perform the real declaration instead of a dry run.",
    )
    args = parser.parse_args()

    cli_registry_root = args.registry_root or LAKE_REGISTRY_ROOT
    cli_lake_root = args.lake_root or _resolve_lake_root()

    if not args.declare:
        cli_report = dry_run(
            args.dates,
            symbol=args.symbol,
            registry_root=cli_registry_root,
            lake_root=cli_lake_root,
        )
        print(json.dumps(cli_report, indent=2, default=str))
    else:
        if not args.code_hash or not args.reason:
            parser.error("--declare requires --code-hash and --reason")
        cli_result = declare(
            args.dates,
            symbol=args.symbol,
            registry_root=cli_registry_root,
            lake_root=cli_lake_root,
            code_hash=args.code_hash,
            reason=args.reason,
        )
        print(
            json.dumps(
                {
                    "holdout_registry_path": str(cli_result["holdout_registry_path"]),
                    "moved_manifest_ids": [
                        m["manifest_id"] for m in cli_result["moved"]
                    ],
                    "moved_to": [
                        m["partitions"][0]["path"] for m in cli_result["moved"]
                    ],
                },
                indent=2,
            )
        )
