"""One `(symbol, date)` in, one manifest-addressed feature partition out.

THE ORDER IS THE CONTROL. Everything this module does exists somewhere
else -- the gates in `features/tier.py` and `features/labels.py`, the
merge in `features/event_stream.py`, the arithmetic in `features/kernel.py`
-- and what is new here is the SEQUENCE they run in relative to the first
byte written:

    1. assert_next_day_available   (D+1 must be ingested; D is not finished
                                    until it is, and a write-once partition
                                    cannot be corrected later)
    2. assert_buildable            (neither D nor D+1 may be held out --
                                    D's long-horizon labels ARE D+1's
                                    prices, so a held-out D+1 would be
                                    laundered into a readable tier)
    3. load_curated D x2           (each raising DQPauseError on an
                                    unacknowledged day)
    4. merge -> assert_strict_total_order -> event_arrays -> decision rows
    5. run_kernel_checked
    6. compute_labels (against D's quotes + D+1's tail)
    7. post_gap_warmup, joined from Phase 3's resync sidecar
    8. write_feature_partition     <-- THE FIRST WRITE, under a
                                    `partial-` staging name
    9. build_stats.json
   10. commit_feature_partition (rename partial- -> part-), then
       issue_feature_manifest      <-- LAST

Steps 1 and 2 cost nothing and refuse before a single partition byte is
READ, so a quarantined date never spends a one-look token and never lands
in a tier anything can read. Steps 8-10 are in that order so a manifest
never names bytes that are not already on disk with their stats beside
them: `data.dq.report.build_stats_problem` binds the two by
`partition_sha256` precisely because the build can die between them.

NaN IS NOT CONVERTED HERE. `features/tier.py:write_feature_partition` is
the single NaN -> null conversion point and asserts its own result; a
second conversion in this module is exactly the duplication that pin
exists to prevent. The frame assembled below carries NaN, deliberately.

NO MLFLOW, NO `tracking` IMPORT. `code_hash` is a caller-supplied
parameter, exactly as `data.ingest.curated_build.build_curated_day` takes
it -- `features/` must stay importable without pulling mlflow in.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import polars as pl

from data.dq.checks import DQThresholds, load_dq_thresholds
from data.dq.feature_checks import FEATURE_BUILD_STATS_KEYS, feature_build_stats_path
from data.dq.report import dq_resync_windows_path
from data.store import by_date_index_path
from features.api import FEATURE_PASS_SCHEMA, for_build
from features.event_stream import (
    assert_strict_total_order,
    merge_curated_streams,
)
from features.labels import (
    NULL_REASONS,
    NextDayUnavailableError,
    append_next_day_quotes,
    assert_next_day_available,
    compute_labels,
    next_day_quote_series,
    next_utc_date,
)
from features.tier import (
    FEATURE_COLUMNS,
    FEATURE_ROW_SCHEMA,
    FEATURE_SCHEMA_VERSION,
    LABEL_COLUMNS,
    assert_buildable,
    commit_feature_partition,
    curated_manifest_input,
    issue_feature_manifest,
    write_feature_partition,
)

__all__ = [
    "FEATURE_BUILD_STATS_KEYS",
    "CuratedInputMissingError",
    "L1_STREAM",
    "TRADE_STREAM",
    "build_features_day",
    "build_features_range",
]


class CuratedInputMissingError(FileNotFoundError):
    """Day D itself has no curated manifest for one of the two streams.

    DELIBERATELY NOT `NextDayUnavailableError`, which means something
    else and is handled differently: a missing D+1 is the normal, daily,
    expected state of the most recent captured day and
    `build_features_range` turns it into a skip. A missing partition for
    the day being built is an ingest hole, and a range loop that absorbed
    it would quietly produce a range of days minus the broken ones.
    """


#: Re-exported, NOT redefined. `data/dq/feature_checks.py` declares the
#: contract between the build that writes `build_stats.json` and the
#: checks that read it; a second frozenset here would be a second
#: contract, and the two would drift on the first added key. The build
#: asserts it produced every one of these before persisting, and produces
#: a documented SUPERSET besides (see `_build_stats`).
FEATURE_BUILD_STATS_KEYS = FEATURE_BUILD_STATS_KEYS

L1_STREAM = "bookTicker"
TRADE_STREAM = "trade"


def _load_curated_day(symbol: str, stream: str, date: str, *, registry_root, lake_root):
    """Day `date`'s curated `stream` partition, through the by-date
    POINTER and the default loader -- so the manifest's own hashes, the
    holdout refusal and that day's DQ pause all apply. Returns
    `(frame, manifest_id)`."""
    from data import store

    dataset = f"{symbol}.{stream}"
    idx = by_date_index_path(Path(registry_root), dataset, symbol, stream, date)
    if not idx.exists():
        raise CuratedInputMissingError(
            f"feature build of {date} refused: no curated {stream} manifest "
            f"for {date} ({idx} does not exist)"
        )
    manifest_id = json.loads(idx.read_text())["manifest_id"]
    df = store.load_curated(
        manifest_id,
        dataset,
        registry_root=Path(registry_root),
        lake_root=Path(lake_root),
    )
    return df, manifest_id


def _post_gap_warmup_tags(
    decision_etime: np.ndarray, lake_root: Path, date: str
) -> np.ndarray:
    """Which decision rows fall inside a post-outage warm-up window.

    D-04-09: THE TAG TRAVELS WITH THE ROW. Phase 3's
    `resync_windows.parquet` is the only place the outage boundaries live
    (curated partitions are write-once and were never rewritten to carry
    them), so the join happens once, here, at build time -- and every
    consumer downstream reads a column instead of re-deriving a window it
    could get subtly wrong.

    The interval is HALF-OPEN `[gap_end_etime_approx,
    warmup_end_etime_approx)`, matching every other window convention in
    this phase. `_etime_approx` is Phase 3's honest naming: an outage
    boundary has no real exchange time, so these are the outage's own
    rtime values used as an approximate etime.

    One `searchsorted` over the window starts, not a loop over rows: the
    sidecar's windows are disjoint and ascending (they come from
    `collapse_outage_intervals`), so the last window starting at or before
    `t` is the only one that can contain `t`.

    NO SIDECAR IS NOT A FINDING. A date whose DQ report ran but saw no
    outage writes an EMPTY sidecar; a date whose report has not been
    generated has none at all. Both mean "no post-gap rows", and neither
    should stop a build -- the report is regenerable, the partition is not.
    """
    path = dq_resync_windows_path(Path(lake_root), date)
    tags = np.zeros(decision_etime.shape, dtype=np.bool_)
    if not path.exists():
        return tags
    windows = pl.read_parquet(path)
    if windows.height == 0:
        return tags
    windows = windows.sort("gap_end_etime_approx")
    starts = windows["gap_end_etime_approx"].to_numpy()
    ends = windows["warmup_end_etime_approx"].to_numpy()
    idx = np.searchsorted(starts, decision_etime, side="right") - 1
    safe = np.maximum(idx, 0)
    np.logical_and(idx >= 0, decision_etime < ends[safe], out=tags)
    return tags


def _build_stats(
    *,
    merge_stats: dict,
    counters: dict[str, int],
    window_capacity: int,
    label_stats: dict,
    warmup_rows: int,
    post_gap_warmup_rows: int,
    curated_manifest_ids: dict[str, str],
    next_day_manifest_id: str,
) -> dict:
    """Everything `data/dq/feature_checks.py` reads, plus the provenance
    and per-horizon detail the report does not read but an investigation
    would want.

    A SUPERSET, and the difference is deliberate. `FEATURE_BUILD_STATS_KEYS`
    is the contract -- fourteen flat keys, asserted present before this
    file is persisted, because a check that cannot find its number reports
    `failed` and pauses the day for a reason invisible in the report.
    Everything else here (`n_events`, `n_quote_rows`, `crossed_locked_rows`,
    the per-horizon `label_*` breakdowns, the two manifest-id fields) is
    recorded because it is free at this point in the build and
    irrecoverable afterwards: a written partition structurally cannot say
    how many rows were filtered out before it existed.

    `window_overflow` is always False in a persisted build and that is not
    a stub: `run_kernel_checked` RAISES on a ring overflow, so a build that
    reached this function did not have one. The key exists so
    `check_feature_window` has the number it fails on rather than reporting
    `failed` for a missing key, and so the claim is recorded per day rather
    than inferred from the absence of a crash.
    """
    slots = dict(counters)
    per_horizon = label_stats["per_horizon"]
    n_decision_rows = int(merge_stats["n_decision_rows"])
    disagreements = int(label_stats.get("asof_convention_disagreement_rows", 0))

    stats: dict = {
        # --- the merge, and the two counted row classes (D-04-12) ---
        "n_events": int(merge_stats["n_events"]),
        "n_quote_rows": int(merge_stats["n_quote_rows"]),
        "n_trade_rows": int(merge_stats["n_trade_rows"]),
        "n_decision_rows": n_decision_rows,
        "na_placeholder_excluded": int(merge_stats["na_placeholder_excluded"]),
        "unknown_side_rows": int(merge_stats["unknown_side_rows"]),
        # --- the kernel's own counters ---
        "max_window_occupancy": slots["max_occupancy"],
        "window_capacity": int(window_capacity),
        "window_overflow": False,
        "empty_window_rows": slots["empty_window_rows"],
        "crossed_locked_rows": slots["crossed_locked_rows"],
        # --- the two warm-up kinds, counted separately ---
        "warmup_rows": int(warmup_rows),
        "post_gap_warmup_rows": int(post_gap_warmup_rows),
        # --- labels ---
        "label_null_counts": {
            name: {reason: h[reason] for reason in ("null_total", *NULL_REASONS)}
            for name, h in per_horizon.items()
        },
        "label_zero_fraction": {
            name: h["zero_fraction"] for name, h in per_horizon.items()
        },
        "label_std": {name: h["std"] for name, h in per_horizon.items()},
        "label_horizon_ns": {name: h["horizon_ns"] for name, h in per_horizon.items()},
        "asof_convention_delta_pct": (
            disagreements / n_decision_rows * 100 if n_decision_rows else 0.0
        ),
        "gap_threshold_ns": int(label_stats["gap_threshold_ns"]),
        "n_big_quote_gaps": int(label_stats["n_big_quote_gaps"]),
        # --- provenance ---
        "curated_manifest_ids": dict(curated_manifest_ids),
        "next_day_manifest_id": next_day_manifest_id,
    }
    # The flat keys labels.py already names, verbatim -- copied rather than
    # re-derived, so `PRIMARY_LABEL` appears in exactly one place.
    for key, value in label_stats.items():
        if key not in ("per_horizon",):
            stats.setdefault(key, value)

    missing = FEATURE_BUILD_STATS_KEYS - set(stats)
    if missing:
        raise AssertionError(
            f"build_features_day: build_stats is missing {sorted(missing)} -- "
            "every key in FEATURE_BUILD_STATS_KEYS must be produced before "
            "the file is persisted, or the day pauses at load time for a "
            "reason the DQ report cannot express"
        )
    return stats


def _atomic_write_json(path: Path, body: dict) -> None:
    """Same shape as `curated_build._atomic_write_json`: a stats file is
    either the whole build's or it is not there at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(body, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def build_features_day(
    symbol: str,
    date: str,
    *,
    lake_root: Path,
    registry_root: Path,
    code_hash: str,
    thresholds: DQThresholds | None = None,
) -> dict:
    """Build, write and manifest one day of decision rows.

    Returns `{"date", "manifest", "partition_entry", "build_stats",
    "elapsed_seconds"}`. Raises before any write on: a missing D+1
    (`NextDayUnavailableError`), a held-out D or D+1
    (`QuarantinedDateError`), an
    unacknowledged DQ finding on either day (`DQPauseError`), or a merge
    whose order is not strict (`ValueError`). Raises `FileExistsError` on
    a second build of the same date -- feature partitions are write-once.

    See the module docstring for why the steps are in the order they are.
    """
    lake_root, registry_root = Path(lake_root), Path(registry_root)
    thresholds = load_dq_thresholds() if thresholds is None else thresholds
    started = time.monotonic()

    # (1) the day boundary, before anything is read: D is not finished
    #     until D+1 exists, and a write-once partition issued early could
    #     only ever be superseded, never corrected.
    next_date = assert_next_day_available(symbol, date, registry_root=registry_root)

    # (2) the holdout refusal, covering D AND D+1, still before any read.
    assert_buildable(symbol, date, next_date, registry_root=registry_root)

    # (3) the two curated loads. Each carries its own manifest hashes and
    #     its own DQ pause; neither is a bare read_parquet.
    l1_df, l1_manifest_id = _load_curated_day(
        symbol, L1_STREAM, date, registry_root=registry_root, lake_root=lake_root
    )
    trade_df, trade_manifest_id = _load_curated_day(
        symbol, TRADE_STREAM, date, registry_root=registry_root, lake_root=lake_root
    )

    # (4) one event stream in (etime, source_rank, seq) order. The
    #     assertion is repeated here on purpose: `merge_curated_streams`
    #     runs it on its own result, and this call is what keeps the gate
    #     visible at the level that would be blamed for a mis-ordered day.
    merged, merge_stats = merge_curated_streams(l1_df, trade_df)
    del l1_df, trade_df
    assert_strict_total_order(merged)

    # (5) the one kernel, THROUGH THE ONE ENTRY POINT. This module used to
    #     call `run_kernel_checked` itself -- a second in-package caller
    #     that `tools/check_single_feature_path.py` did not watch, because
    #     the sanction was the whole `features/` directory
    #     (04-VERIFICATION.md gap A). `features.api.for_build` runs the
    #     same `_kernel_pass` the trainer, the serving loop and the
    #     simulator run, and applies the same `decision_row_index` rule;
    #     what it adds is the per-quote `mid` series the labels need.
    #
    #     Everything full-length lives and dies inside that call, so day
    #     D's ~2.7 GB is not still resident while day D+1 loads.
    built = for_build(merged)
    del merged
    quote_etime, quote_mid = built.quote_etime, built.quote_mid
    decision = {name: built.frame[name].to_numpy() for name in FEATURE_PASS_SCHEMA}

    # (6) labels, against D's quotes EXTENDED by D+1's -- the cross-day
    #     read that makes day D's last ten minutes labellable at all.
    next_etime, next_mid = next_day_quote_series(
        symbol, date, registry_root=registry_root, lake_root=lake_root
    )
    next_manifest_id = json.loads(
        by_date_index_path(
            registry_root, f"{symbol}.{L1_STREAM}", symbol, L1_STREAM, next_date
        ).read_text()
    )["manifest_id"]
    quote_etime, quote_mid = append_next_day_quotes(
        quote_etime, quote_mid, next_etime, next_mid
    )
    del next_etime, next_mid
    labels, label_stats = compute_labels(
        decision["etime"],
        decision["mid"],
        quote_etime,
        quote_mid,
        gap_threshold_ns=thresholds.label_gap.max_quote_gap_ns,
    )
    del quote_etime, quote_mid

    # (7) the post-gap warm-up tag, joined from Phase 3's sidecar.
    post_gap = _post_gap_warmup_tags(decision["etime"], lake_root, date)

    # (8) the frame, in FEATURE_ROW_SCHEMA order, WITH NaN still in it.
    n = decision["etime"].size
    columns = {
        "etime": pl.Series(decision["etime"], dtype=pl.Int64),
        "decision_source_rank": pl.Series(
            decision["decision_source_rank"], dtype=pl.Int8
        ),
        "decision_seq": pl.Series(decision["decision_seq"], dtype=pl.Int64),
        **{
            name: pl.Series(decision[name], dtype=pl.Float64)
            for name in FEATURE_COLUMNS
        },
        **{name: pl.Series(labels[name], dtype=pl.Float64) for name in LABEL_COLUMNS},
        "warmup": pl.Series(decision["warmup"], dtype=pl.Boolean),
        "post_gap_warmup": pl.Series(post_gap, dtype=pl.Boolean),
        "schema_version": pl.Series(
            np.full(n, FEATURE_SCHEMA_VERSION, dtype=np.int32), dtype=pl.Int32
        ),
    }
    frame = pl.DataFrame(
        {name: columns[name] for name in FEATURE_ROW_SCHEMA},
        schema=dict(FEATURE_ROW_SCHEMA),
    )
    warmup_rows = int(decision["warmup"].sum())
    post_gap_warmup_rows = int(post_gap.sum())
    del decision, labels, post_gap, columns

    # STAGED, not committed (04-REVIEW.md WR-01). The bytes land under
    # `partial-<ns>.parquet`; step 9's stats and step 10's rename +
    # manifest are what turn them into a partition. A crash anywhere
    # below leaves a file no reader can mistake for a committed
    # partition, and the next build removes it and starts over.
    partition_entry = write_feature_partition(
        frame,
        lake_root=lake_root,
        symbol=symbol,
        date=date,
        registry_root=registry_root,
        commit=False,
    )
    del frame

    # (9) stats BEFORE the manifest, bound to the partition's sha256 --
    #     `data.dq.report.build_stats_problem` accepts that binding
    #     precisely so a crash in the window below is still diagnosable.
    stats = _build_stats(
        merge_stats=merge_stats,
        counters=built.counters,
        window_capacity=built.window_capacity,
        label_stats=label_stats,
        warmup_rows=warmup_rows,
        post_gap_warmup_rows=post_gap_warmup_rows,
        curated_manifest_ids={
            L1_STREAM: l1_manifest_id,
            TRADE_STREAM: trade_manifest_id,
        },
        next_day_manifest_id=next_manifest_id,
    )
    stats["partition_path"] = partition_entry["path"]
    stats["partition_sha256"] = partition_entry["sha256"]
    stats["manifest_id"] = None
    stats_path = feature_build_stats_path(lake_root, symbol, date)
    _atomic_write_json(stats_path, stats)

    # (10) the rename, then the manifest LAST: the manifest never names
    #      bytes that are not already on disk, under the path it names,
    #      with their stats beside them. The rename preserves the sha256,
    #      size and mtime `partition_entry` already carries.
    commit_feature_partition(partition_entry, lake_root=lake_root)
    manifest = issue_feature_manifest(
        symbol=symbol,
        date=date,
        partition_entry=partition_entry,
        curated_manifests=[
            curated_manifest_input(
                f"{symbol}.{L1_STREAM}",
                l1_manifest_id,
                registry_root=registry_root,
                role="l1_day",
            ),
            curated_manifest_input(
                f"{symbol}.{TRADE_STREAM}",
                trade_manifest_id,
                registry_root=registry_root,
                role="trade_day",
            ),
            curated_manifest_input(
                f"{symbol}.{L1_STREAM}",
                next_manifest_id,
                registry_root=registry_root,
                role="l1_label_tail",
            ),
        ],
        code_hash=code_hash,
        registry_root=registry_root,
    )
    stats["manifest_id"] = manifest["manifest_id"]
    _atomic_write_json(stats_path, stats)

    return {
        "date": date,
        "manifest": manifest,
        "partition_entry": partition_entry,
        "build_stats": stats,
        "elapsed_seconds": time.monotonic() - started,
    }


def _already_built(registry_root: Path, symbol: str, date: str) -> str | None:
    idx = by_date_index_path(
        Path(registry_root), f"{symbol}.features", symbol, "features", date
    )
    if not idx.exists():
        return None
    return json.loads(idx.read_text())["manifest_id"]


def build_features_range(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    lake_root: Path,
    registry_root: Path,
    code_hash: str,
    thresholds: DQThresholds | None = None,
) -> list[dict]:
    """`build_features_day` once per UTC date in `[start_date, end_date]`,
    idempotently. One status dict per date, mirroring
    `data.ingest.curated_build.build_curated_range`'s shape:

    - `"already_present"`: a features by-date pointer already exists. The
      day is SKIPPED, not rebuilt -- write-once means a rebuild is a new
      manifest and a new partition, which is an audit-trail event, not a
      range-loop convenience.
    - `"skipped"` with `reason="no next day"`: D+1 has no curated L1
      manifest yet. Expected on the most recent captured day, every day,
      by design -- so it is a status rather than a crash.
    - `"orphaned"`: a `part-<ns>.parquet` exists for the date with no
      manifest naming it -- a previous build died in the one remaining
      window, between the staging rename and the manifest. That date is
      genuinely wedged until someone looks at it, but the LATER DATES ARE
      STILL ATTEMPTED (04-REVIEW.md WR-01): one wedged day silently
      truncating a week of builds is the failure that looks most like
      success.
    - `"written"`: built now.

    Every OTHER refusal propagates. A held-out date, an unacknowledged DQ
    finding and a non-strict merge order are not conditions a range loop
    may absorb: skipping them silently is how a range of days quietly
    becomes a range of days minus the interesting ones.
    """
    lake_root, registry_root = Path(lake_root), Path(registry_root)
    results: list[dict] = []
    date = start_date
    while date <= end_date:
        existing = _already_built(registry_root, symbol, date)
        if existing is not None:
            results.append(
                {"date": date, "status": "already_present", "manifest_id": existing}
            )
        else:
            try:
                built = build_features_day(
                    symbol,
                    date,
                    lake_root=lake_root,
                    registry_root=registry_root,
                    code_hash=code_hash,
                    thresholds=thresholds,
                )
            except NextDayUnavailableError as exc:
                results.append(
                    {
                        "date": date,
                        "status": "skipped",
                        "manifest_id": None,
                        "reason": "no next day",
                        "detail": str(exc),
                    }
                )
            except FileExistsError as exc:
                results.append(
                    {
                        "date": date,
                        "status": "orphaned",
                        "manifest_id": None,
                        "reason": (
                            "a part file exists with no manifest -- a previous "
                            "build died between the staging rename and the "
                            "manifest; the file must be removed before this "
                            "date can be rebuilt"
                        ),
                        "detail": str(exc),
                    }
                )
            else:
                results.append(
                    {
                        "date": date,
                        "status": "written",
                        "manifest_id": built["manifest"]["manifest_id"],
                        "rows": built["partition_entry"]["rows"],
                        "elapsed_seconds": built["elapsed_seconds"],
                    }
                )
        date = next_utc_date(date)
    return results
