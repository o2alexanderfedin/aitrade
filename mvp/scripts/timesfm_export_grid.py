"""Stage 1 of experiment E1: turn the five ALREADY-CACHED OOF blocks into a
REGULARLY-SPACED 1-second series that a foundation model can be handed, and
write it where an interpreter that cannot import this package can read it.

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.timesfm_export_grid --counters-only
    ./.venv/bin/python3 -m scripts.timesfm_export_grid

WHY THIS SCRIPT EXISTS AT ALL, i.e. why the export is not just a `predict`
call inside `oof_viability_check`. TimesFM 3.0 pulls `torch` and `numpy` 2.5,
and `mvp/.venv` is lockfile-pinned to `numpy<2.5` because `numba` requires it
-- so the two environments cannot import each other and nothing that reads
this lake can also call the model. The split is forced by the pin, not chosen:
this half reads the lake and writes `.npz`; `tools/timesfm_forecast.py` reads
the `.npz` under the other interpreter. See README.md's TimesFM section.

ZERO LOOKS, BY THE SAME INTERLOCK AS `scripts/oof_viability_check.py`, copied
rather than imported so a reader of THIS file can see the control that makes
its cost zero. `models.cache.materialize` is rebound to a raiser before any
frame is opened, so a cache MISS -- wrong tracking root, moved scratch
directory, renamed segment -- raises instead of spending an irreversible look.
The five OOF blocks are already cached; `val` is never named.

THE ONE DECISION THAT MAKES THE EXPERIMENT MEANINGFUL, and the reason this
script writes `pred_frozen` itself instead of leaving it to the scorer. A
foundation model needs a regularly-spaced series; this project's decision rows
are event-driven and irregular (measured: median inter-row gap 1-2 ms, 4.1M to
12.1M rows per block). Resampling to 1 s therefore changes the ROW POPULATION,
and an r-squared measured on a different population has a different
denominator -- so quoting the frozen predictor's published 0.006-0.035 beside
a grid number would be meaningless. Every model in this experiment is scored
on the SAME grid rows, and the frozen predictor's prediction for those rows is
computed HERE, from the committed body and the committed normalization
artifact, because both live behind imports only this venv has.

WHAT A GRID POINT IS. `join_asof(strategy="backward")` at exact 1-second
stamps: the value at stamp `T` is the last decision row with `etime <= T`.
That is the PREVAILING book, not an interpolation and not an invented
observation -- between two updates the mid genuinely is the last quote, and
`imb_top` genuinely is the last top-of-book state. So the frozen predictor is
fed a real feature value it would have seen live, and the grid target is the
realised return between two prevailing mids exactly 10 s apart.

WHAT IS EXCLUDED, AND WHY IT HAD TO BE. The capture has real HOLES: measured
maximum inter-row gaps are 38.6 s (block 0), 1.5 s (block 1), 2889 s (block
2), 6048 s (block 3) and 3071 s (block 4). An asof-backward grid carries the
last quote flat across a 100-minute hole and then shows the whole intervening
move as one 10-second return. A window is therefore admissible only when EVERY
one of its `CONTEXT + HORIZON` bars is `clean` (see `_clean_mask`), which
excludes every window that spans a hole, and the anchor bar additionally needs
finite features and a finite frame label.

`NUMBA_CACHE_DIR` IS PINNED AT THE TOP, BEFORE ANY IMPORT, for
`scripts/run_stage1_slice.py`'s reason in its own words: with the variable
unset, `@njit(cache=True)` writes its `.nbi`/`.nbc` files into the
`__pycache__` next to the defining source. Same `setdefault` expression as
that script, as `scripts/oof_viability_check.py` and as `tests/conftest.py`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# BEFORE ANY IMPORT THAT CAN REACH NUMBA.
os.environ.setdefault(
    "NUMBA_CACHE_DIR", str(Path(tempfile.gettempdir()) / "aihf-numba-cache")
)

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from typing import Any  # noqa: E402

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

from data.lake_paths import (  # noqa: E402
    LAKE_REGISTRY_ROOT,
    lake_root as resolve_lake_root,
    mlflow_tracking_root,
)
from data.time_ns import NS_PER_SECOND, RET_10S_NS  # noqa: E402
from features.normalize import (  # noqa: E402
    load_normalization,
    normalization_dataset,
)
from harness import budget  # noqa: E402
from harness.row_admission import STALE_BOOK_MAX_AGE_NS  # noqa: E402
from harness.segments import read_segment_manifest  # noqa: E402
from models import cache as cache_module  # noqa: E402
from models.cache import (  # noqa: E402
    CACHE_ROOT,
    materialize_once,
    repo_root,
    segment_cache_path,
)
from models.frozen import read_frozen_predictor  # noqa: E402
from models.regression import TARGET_NAME  # noqa: E402
from models.sweep import _scoring_columns  # noqa: E402

#: The committed frozen winner (07-10), named by its manifest id exactly as
#: `scripts/oof_viability_check.py` names it. `read_frozen_predictor`
#: re-verifies the self-hash and re-derives `predictor_id`, so a tampered body
#: cannot be exported either.
FROZEN_PREDICTOR_MANIFEST_ID: str = (
    "e3b4b235d0fe3c025be7c7cdbf7eaa3d2d5675ad9571d6f6a30b1a45680c2d01"
)

#: The Option-A segment manifest whose five OOF blocks are cached.
SEGMENT_MANIFEST_ID: str = (
    "807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548"
)

#: THE ONE SEGMENT NAME THIS SCRIPT MUST NEVER MATERIALIZE. Spelled as a
#: constant so the refusal is greppable rather than implied.
FORBIDDEN_SEGMENT_NAME: str = "val"

#: The grid step in ns, re-exported from `data/time_ns.py` rather than
#: respelled -- a rebinding, not a conversion. That `HORIZON` bars of it is the
#: project's own `ret_10s_mid` horizon is checked ON THE GRID ITSELF
#: (`_assert_horizon_is_the_label_horizon`), from two realised stamps, rather
#: than as arithmetic between two constants.
GRID_STEP_NS: int = NS_PER_SECOND

#: Bars of context handed to the model, and bars of horizon asked of it.
#: 512 is the context length the README's own measurement used; 10 bars at a
#: 1 s step is the 10 s the project forecasts.
CONTEXT: int = 512
HORIZON: int = 10

#: The grid step as polars' own unit string. Paired with `GRID_STEP_NS` and
#: cross-checked against it in `_grid_stamps`' caller, so the two spellings of
#: one step cannot drift apart.
GRID_STEP: str = "1s"

#: How stale a prevailing quote may be before its grid point stops counting as
#: `clean`. NOT A NUMBER THIS SCRIPT CHOSE: it is D-05-21's decided
#: project-wide answer to "the feed went quiet", imported from the module that
#: owns it, on that module's own stated reasoning -- reuse one constant for one
#: underlying concept rather than inventing a second independent number for it.
#: 5 s sits between normal silence (Q7 measured p999 stale age 0.293 s on a
#: clean day) and an outage (1492 s on 2026-09-14), and equals the capture gap
#: ledger's `gap_threshold_seconds`.
#:
#: THE QUANTITY IS NOT IDENTICAL TO `harness.row_admission`'s, and the
#: difference is worth stating: that module measures how long since the BOOK
#: last moved, this one how long since the last DECISION ROW of any kind. The
#: threshold's concept -- the boundary between quiet and absent -- is the same,
#: which is why the constant is shared and the quantity is not.
#:
#: The sensitivity to this choice is MEASURED rather than asserted:
#: `clean_points_at_one_grid_step` in each block summary reports what a bound
#: of one grid step would have admitted instead.
MAX_STALE_NS: int = STALE_BOOK_MAX_AGE_NS

#: Distance between consecutive admissible anchors, in bars. 20 > HORIZON, so
#: NO TWO SCORED TARGETS OVERLAP IN TIME -- which is why the rank IC below is
#: not inflated by the autocorrelation that overlapping forward returns
#: manufacture. Also what bounds the compute: ~3k-4k windows per block.
STRIDE: int = 20

#: Where the `.npz` files go: beside the weights, outside this repo, in the
#: directory README.md designates for exactly this ("$TIMESFM_ROOT/work").
#: NOT under `scratch/phase07`, which holds the segment cache this script must
#: not disturb.
DEFAULT_EXPORT_ROOT: Path = Path("/Volumes/ProjectsSSD/aihedgefund/timesfm/work/e1")

EVIDENCE_DIR = Path(".planning/phases/07-regression-track-vertical-slice/evidence")


class ZeroLookViolation(RuntimeError):
    """A cache miss where this script requires a cache hit.

    Raised INSTEAD of `harness.accessor.materialize` ever running, so a reader
    of a traceback knows immediately that nothing was spent -- the interlock
    fired before the accessor, not after it. Same class name and same role as
    `scripts/oof_viability_check.py`'s.
    """


def _forbid_materialize() -> None:
    """Replace the `materialize` name INSIDE `models.cache` with a raiser.

    THE LOAD-BEARING ZERO-LOOK CONTROL. `materialize_once` reads `materialize`
    out of its own module globals on a cache MISS; rebinding that name means a
    miss raises instead of spending an irreversible look on a question this
    script has no budget for. The cache-path pre-assertion in `main` is the
    readable check; this is the one that holds when the readable check is
    wrong.
    """

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        raise ZeroLookViolation(
            "timesfm_export_grid: harness.accessor.materialize was reached, "
            f"which means a CACHE MISS for args={args!r} kwargs="
            f"{ {k: v for k, v in kwargs.items() if k != 'run_tags'}!r}. This "
            "script runs at zero look cost and every frame it needs is "
            "already cached; a miss is a wrong root or a moved scratch "
            "directory, never a reason to materialize."
        )

    cache_module.materialize = _refuse  # type: ignore[assignment]


def _counters(registry_root: Path, tracking_root: str) -> dict[str, Any]:
    """Every look counter for every segment name of every COMMITTED segment
    manifest -- derived by globbing the registry, never a hardcoded list, so a
    manifest added later is counted rather than silently missed.
    """
    segment_dir = Path(registry_root) / "segments"
    manifest_ids = sorted(path.stem for path in segment_dir.glob("*.json"))
    if not manifest_ids:
        raise ZeroLookViolation(
            f"timesfm_export_grid: no committed segment manifest under "
            f"{segment_dir} -- a counter report over zero manifests would read "
            "as 'nothing spent'"
        )
    counts: dict[str, dict[str, int]] = {}
    for manifest_id in manifest_ids:
        manifest = read_segment_manifest(Path(registry_root), manifest_id)
        names = sorted({str(entry["name"]) for entry in manifest["segments"]})
        counts[manifest_id] = {
            name: budget.look_count(manifest_id, name, tracking_root=tracking_root)
            for name in names
        }
    total = sum(sum(per.values()) for per in counts.values())
    return {
        "tracking_root": str(tracking_root),
        "counts": counts,
        "total": int(total),
        "n_counters": sum(len(per) for per in counts.values()),
    }


def _print_counters(report: dict[str, Any], label: str) -> None:
    print(f"--- look counters {label} ({report['n_counters']} counters) ---")
    for manifest_id, per in report["counts"].items():
        print(f"  {manifest_id[:12]}")
        for name, count in per.items():
            print(f"    {name:14s} {count}")
    print(f"  TOTAL {report['total']}")


def _block_names(manifest: dict[str, Any]) -> list[str]:
    return [
        str(entry["name"])
        for entry in sorted(
            (e for e in manifest["segments"] if e["role"] == "oof_block"),
            key=lambda e: e["start_ns"],
        )
    ]


def _floor_to_step(values: list[int]) -> list[int]:
    """Each int64 ns instant floored to a whole `GRID_STEP` boundary.

    THE FLOOR IS DELEGATED TO POLARS ON PURPOSE, and this is the whole reason
    the function exists instead of one inline `// NS_PER_SECOND * NS_PER_SECOND`
    expression. That spelling is a seconds-to-nanoseconds conversion site, and
    `tools/check_ms_to_ns_site.py` allows those in exactly six files -- none of
    them a script -- so that a new one has to be argued for rather than typed.
    Here there is nothing to argue: `dt.truncate("1s")` expresses the step as a
    UNIT STRING and lets the library that owns time units do the arithmetic,
    which is the same delegation every feature and label module already makes
    by importing a pre-multiplied constant from `data/time_ns.py`. Routing
    around the checker with a modulo would have passed it and taught a reader
    nothing.

    `Datetime("ns")` is asserted, not assumed: a silent cast to microseconds
    would floor to the wrong boundary and every downstream index would shift.
    """
    column = (
        pl.Series("instant", values, dtype=pl.Int64)
        .cast(pl.Datetime(time_unit="ns"))
        .dt.truncate(GRID_STEP)
    )
    if column.dtype != pl.Datetime(time_unit="ns"):
        raise ZeroLookViolation(
            f"timesfm_export_grid: dt.truncate returned {column.dtype}, not a "
            "nanosecond Datetime -- a coarser unit floors to the wrong boundary"
        )
    return [int(v) for v in column.cast(pl.Int64).to_list()]


def _grid_stamps(etime: np.ndarray) -> np.ndarray:
    """The exact 1-second stamps inside `[etime[0], etime[-1]]`.

    The first stamp is the smallest whole-second boundary that is `>=
    etime[0]`, so every stamp has at least one row at or before it and the asof
    join can never produce a null. Built with `np.arange` on int64, never on a
    float: at 1.79e18 ns a float64 stamp is already coarser than a millisecond.
    """
    start, end = int(etime[0]), int(etime[-1])
    floor_start, last = _floor_to_step([start, end])
    first = floor_start if floor_start == start else floor_start + GRID_STEP_NS
    if last < first:
        raise ZeroLookViolation(
            "timesfm_export_grid: block spans less than one grid step "
            f"({start}..{end}) -- there is no 1-second grid to build"
        )
    return np.arange(first, last + GRID_STEP_NS, GRID_STEP_NS, dtype=np.int64)


def _assert_horizon_is_the_label_horizon(stamps: np.ndarray) -> None:
    """`HORIZON` bars of this grid must be exactly the `ret_10s_mid` horizon.

    Checked from TWO REALISED STAMPS, not by dividing two constants. If the grid
    step were ever wrong -- a coarser `Datetime` unit, a truncation to the wrong
    boundary -- a constant-arithmetic assertion would still pass while the data
    said something else. This one reads the data.

    It is the assertion that keeps the comparison honest: the frozen predictor
    answers "what is the 10-second midprice return", so a horizon of any other
    length would score the three models against a different question than the
    published number answers.
    """
    if stamps.shape[0] <= HORIZON:
        raise ZeroLookViolation(
            f"timesfm_export_grid: {stamps.shape[0]} grid stamps cannot span a "
            f"{HORIZON}-bar horizon"
        )
    span = int(stamps[HORIZON]) - int(stamps[0])
    if span != RET_10S_NS:
        raise ZeroLookViolation(
            f"timesfm_export_grid: {HORIZON} bars of this grid span {span} ns, "
            f"not the {RET_10S_NS} ns horizon the project forecasts -- every "
            "model here would be scored against a different question than the "
            "frozen predictor answers"
        )
    step = np.diff(stamps)
    if int(step.min()) != GRID_STEP_NS or int(step.max()) != GRID_STEP_NS:
        raise ZeroLookViolation(
            f"timesfm_export_grid: grid spacing is not uniform at "
            f"{GRID_STEP_NS} ns (min {int(step.min())}, max {int(step.max())}) "
            "-- a foundation model handed an irregular series is being told a "
            "lie about its own clock"
        )


def _asof_backward(frame: pl.DataFrame, stamps: np.ndarray) -> pl.DataFrame:
    """One row per stamp, carrying the last decision row with `etime <= stamp`.

    CROSS-CHECKED AGAINST `np.searchsorted` HERE, not in a test, because the
    alignment is the whole experiment: an off-by-one in the direction of the
    future would hand every model a row it could not have seen. polars'
    `join_asof` and numpy's `searchsorted(..., side="right") - 1` are two
    independent implementations of the same lookup, so an index disagreement
    is a hard failure rather than a silent shift.
    """
    etime = frame["etime"].to_numpy()
    if np.any(np.diff(etime) <= 0):
        raise ZeroLookViolation(
            "timesfm_export_grid: block `etime` is not strictly increasing, so "
            "'the last row at or before T' is not well defined -- the cached "
            "frames were measured to hold one row per etime, and a duplicate "
            "here means the decision rule changed"
        )
    expected = np.searchsorted(etime, stamps, side="right") - 1
    if int(expected.min()) < 0:
        raise ZeroLookViolation(
            "timesfm_export_grid: a grid stamp precedes every row of the block "
            "-- `_grid_stamps` is supposed to make that impossible"
        )
    joined = (
        pl.DataFrame({"grid_ns": stamps})
        .join_asof(
            frame.with_row_index("row_index"),
            left_on="grid_ns",
            right_on="etime",
            strategy="backward",
        )
        .rename({"etime": "anchor_etime"})
    )
    got = joined["row_index"].to_numpy()
    if not np.array_equal(got, expected.astype(got.dtype)):
        n_bad = int((got != expected).sum())
        raise ZeroLookViolation(
            f"timesfm_export_grid: polars join_asof and numpy searchsorted "
            f"disagree on {n_bad} of {len(stamps)} grid stamps -- the anchor "
            "row alignment is the experiment's whole premise and cannot be "
            "taken on trust"
        )
    return joined.drop("row_index")


def _clean_mask(grid: pl.DataFrame) -> np.ndarray:
    """Which grid points carry a quote this experiment is willing to call the
    prevailing price.

    Three conditions, each excluding a different way a grid point can be a
    fiction rather than an observation:

    - `stale_ns <= MAX_STALE_NS`: the anchor row is at most 2 s old. This is
      what excludes the capture holes, which run to 100 minutes.
    - `warmup` is false: the project's own flag for a row whose features are
      not yet trustworthy.
    - `post_gap_warmup` is false: the project's own flag for a row that sits
      inside the recovery window after a capture gap. 18k-38k such rows exist
      on blocks 2, 3 and 4.

    Returns a plain bool array over grid points, NOT applied to the frame: the
    grid must stay REGULARLY SPACED for the model, so unclean points are
    carried and then excluded at the window level by `_admissible_anchors`.
    """
    stale = grid["grid_ns"].to_numpy() - grid["anchor_etime"].to_numpy()
    if int(stale.min()) < 0:
        raise ZeroLookViolation(
            "timesfm_export_grid: a grid point's anchor row is AFTER its stamp "
            f"(min staleness {int(stale.min())} ns) -- the asof join looked "
            "forward"
        )
    return (
        (stale <= MAX_STALE_NS)
        & ~grid["warmup"].to_numpy()
        & ~grid["post_gap_warmup"].to_numpy()
    )


def _admissible_anchors(
    clean: np.ndarray,
    anchor_ok: np.ndarray,
    *,
    stride: int,
    admit_unclean: bool = False,
) -> np.ndarray:
    """Anchor bar indices at which a whole window is usable, every `stride`.

    A window at anchor `k` occupies bars `k - CONTEXT + 1 .. k + HORIZON`: the
    context the model reads, and the horizon it is scored against. EVERY bar in
    that span must be `clean`, which is what keeps a hole out of the context as
    well as out of the target -- a 512-bar context with a flat 100-minute plateau
    in it is not a midprice series, and a model handed one is being asked a
    different question.

    `anchor_ok` is required at bar `k` only: it carries the anchor-specific
    requirements (finite features, finite frame label) that have no meaning at
    the other bars.

    Implemented as a cumulative sum over `~clean` so the all-clean test is O(1)
    per candidate rather than O(CONTEXT).

    `admit_unclean=True` IS THE SENSITIVITY ARM, not an option anyone should
    use for a reported number. It keeps every window whose ANCHOR is usable and
    drops the all-clean span requirement -- i.e. it admits the windows whose
    512-bar context contains a forward-filled plateau across a capture hole, and
    the windows whose 10-bar target straddles one. Exists so "the exclusion is
    load-bearing" is a measurement rather than an assertion: run both and
    compare. The anchor requirements are NOT relaxed, because a window with a
    null feature or a null label has nothing to score at all.
    """
    n = clean.shape[0]
    unclean_cumsum = np.concatenate(
        ([0], np.cumsum((~clean).astype(np.int64)))
    )  # prefix sums
    candidates = np.arange(CONTEXT - 1, n - HORIZON, stride, dtype=np.int64)
    if candidates.size == 0:
        return candidates
    lo = candidates - (CONTEXT - 1)
    hi = candidates + HORIZON + 1
    span_unclean = unclean_cumsum[hi] - unclean_cumsum[lo]
    span_ok = (
        np.ones(candidates.shape[0], dtype=bool) if admit_unclean else span_unclean == 0
    )
    return candidates[span_ok & anchor_ok[candidates]]


def _export_block(
    *,
    segment_name: str,
    predictor: Any,
    params: dict[str, Any],
    registry_root: Path,
    lake_root: Path,
    tracking_root: str,
    cache_root: Path,
    export_root: Path,
    stride: int,
    admit_unclean: bool,
) -> dict[str, Any]:
    """One OOF block: cached frame in, one `.npz` and one summary dict out."""
    started = time.monotonic()
    frame = materialize_once(
        SEGMENT_MANIFEST_ID,
        segment_name,
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=tracking_root,
        run_tags={"never_used": "this script cannot reach materialize"},
        cache_root=cache_root,
    )
    columns = [
        "etime",
        "mid",
        *predictor.feature_names,
        TARGET_NAME,
        "warmup",
        "post_gap_warmup",
    ]
    slim = frame.select(columns)
    rows = int(frame.height)
    del frame

    stamps = _grid_stamps(slim["etime"].to_numpy())
    _assert_horizon_is_the_label_horizon(stamps)
    grid = _asof_backward(slim, stamps)
    del slim

    clean = _clean_mask(grid)
    # `_scoring_columns` is the SAME null accounting the sweep used, reached
    # here on the grid frame so the frozen prediction and the frame label come
    # from one code path rather than two. It NaN-fills nulls explicitly, which
    # matters because `0.0 * nan` is `nan`: the frozen body's coefficients on
    # `ofi` and `trade_flow` are exactly zero, yet a null in either still makes
    # the prediction non-finite. Measured: 1 null `ofi` row on blocks 0-2.
    features, target_frame = _scoring_columns(
        grid, feature_names=list(predictor.feature_names), params=params
    )
    pred_frozen = np.asarray(predictor.predict(features), dtype=np.float64)
    # The NORMALISED `imb_top` the predictor actually consumed, not the raw
    # column: the scorer needs the number that produced `pred_frozen`, and
    # re-reading the raw feature would open a second path to the same value.
    imb_top_norm = np.ascontiguousarray(features[:, 0], dtype=np.float64)
    mid = np.asarray(grid["mid"].to_numpy(), dtype=np.float64)
    anchor_etime = grid["anchor_etime"].to_numpy().astype(np.int64)
    grid_ns = grid["grid_ns"].to_numpy().astype(np.int64)
    stale_ns = grid_ns - anchor_etime
    anchor_ok = (
        np.isfinite(pred_frozen)
        & np.isfinite(target_frame)
        & np.isfinite(mid)
        & (mid > 0.0)
    )
    del grid, features

    anchors = _admissible_anchors(
        clean, anchor_ok, stride=stride, admit_unclean=admit_unclean
    )
    strict = _admissible_anchors(clean, anchor_ok, stride=stride)
    if anchors.size == 0:
        raise ZeroLookViolation(
            f"timesfm_export_grid: {segment_name} yielded zero admissible "
            f"windows out of {len(stamps)} grid points -- an empty export is "
            "not an export"
        )

    out_path = export_root / f"{segment_name}.npz"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        out_path,
        grid_ns=grid_ns,
        anchor_etime=anchor_etime,
        stale_ns=stale_ns,
        mid=mid,
        imb_top_norm=imb_top_norm,
        pred_frozen=pred_frozen,
        ret_10s_mid_frame=target_frame,
        clean=clean,
        anchor_ok=anchor_ok,
        anchors=anchors,
    )
    elapsed = time.monotonic() - started
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    summary = {
        "segment_name": segment_name,
        "frame_rows": rows,
        "grid_points": int(len(stamps)),
        "grid_ns_first": int(grid_ns[0]),
        "grid_ns_last": int(grid_ns[-1]),
        "clean_points": int(clean.sum()),
        "clean_fraction": float(clean.mean()),
        "anchor_ok_points": int(anchor_ok.sum()),
        "admissible_anchors": int(anchors.size),
        "admit_unclean": bool(admit_unclean),
        "admissible_anchors_strict": int(strict.size),
        "anchors_the_gap_exclusion_removed": int(anchors.size - strict.size),
        "stale_ns_max": int(stale_ns.max()),
        "stale_ns_p50": float(np.percentile(stale_ns, 50)),
        "stale_ns_p99": float(np.percentile(stale_ns, 99)),
        "stale_ns_max_on_clean": int(stale_ns[clean].max()),
        "clean_points_at_one_grid_step": int(
            (clean & (stale_ns <= GRID_STEP_NS)).sum()
        ),
        "clean_points_above_one_grid_step": int(
            (clean & (stale_ns > GRID_STEP_NS)).sum()
        ),
        "mid_min": float(np.nanmin(mid)),
        "mid_max": float(np.nanmax(mid)),
        "npz_path": str(out_path),
        "npz_sha256": digest,
        "npz_bytes": int(out_path.stat().st_size),
        "seconds": float(elapsed),
    }
    print(
        f"  {segment_name}: {rows:>10,} rows -> {len(stamps):>6,} bars, "
        f"{int(clean.sum()):>6,} clean ({clean.mean():.3f}), "
        f"{anchors.size:>5,} windows @stride {stride}, {elapsed:.1f}s"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export a regularly-spaced 1-second midprice grid, plus the frozen "
            "predictor's prediction on the same grid rows, from the five "
            "ALREADY-CACHED OOF blocks at zero look cost."
        )
    )
    parser.add_argument(
        "--counters-only",
        action="store_true",
        help="query and write every look counter, then exit without reading a frame",
    )
    parser.add_argument("--out", default=None, help="evidence JSON path")
    parser.add_argument(
        "--export-root",
        default=str(DEFAULT_EXPORT_ROOT),
        help="directory the .npz files are written to (must be outside this repo)",
    )
    parser.add_argument(
        "--blocks",
        default=None,
        help="comma-separated subset of oof_block names (default: all five)",
    )
    parser.add_argument("--stride", type=int, default=STRIDE)
    parser.add_argument(
        "--admit-unclean",
        action="store_true",
        help=(
            "SENSITIVITY ARM ONLY: keep windows whose context or target spans a "
            "capture hole, so the cost of excluding them can be measured "
            "instead of asserted. Never for a reported number."
        ),
    )
    args = parser.parse_args(argv)

    registry_root = LAKE_REGISTRY_ROOT
    lake_root = resolve_lake_root()
    tracking_root = str(mlflow_tracking_root(None))
    cache_root = CACHE_ROOT
    export_root = Path(args.export_root).resolve()
    evidence_dir = repo_root() / EVIDENCE_DIR
    out_path = (
        Path(args.out) if args.out else evidence_dir / "07-e1-timesfm-export.json"
    )
    if export_root.is_relative_to(repo_root()):
        raise ZeroLookViolation(
            f"timesfm_export_grid: --export-root {export_root} is inside the "
            f"repo ({repo_root()}) -- the grid is bulk data and must not be "
            "committable"
        )
    print(f"registry_root   {registry_root}")
    print(f"lake_root       {lake_root}")
    print(f"tracking_root   {tracking_root}")
    print(f"cache_root      {cache_root}")
    print(f"export_root     {export_root}")
    print(f"out             {out_path}")

    before = _counters(registry_root, tracking_root)
    _print_counters(before, "BEFORE")
    if args.counters_only:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(before, sort_keys=True, indent=2) + "\n")
        print(f"OK: wrote {out_path}")
        return 0

    _forbid_materialize()

    manifest = read_segment_manifest(registry_root, SEGMENT_MANIFEST_ID)
    block_names = _block_names(manifest)
    if FORBIDDEN_SEGMENT_NAME in block_names:
        raise ZeroLookViolation(
            f"timesfm_export_grid: {FORBIDDEN_SEGMENT_NAME!r} appears in the "
            "OOF block list -- refusing to proceed"
        )
    if args.blocks:
        requested = [token.strip() for token in str(args.blocks).split(",")]
        unknown = [name for name in requested if name not in block_names]
        if unknown:
            raise ZeroLookViolation(
                f"timesfm_export_grid: --blocks names {unknown}, which are not "
                f"OOF blocks of this manifest ({block_names}) -- refusing to "
                "guess, and refusing outright to accept a name this manifest "
                "gives another role"
            )
        block_names = requested
        print(f"NOTE: --blocks restricts this run to {block_names}")
    for name in block_names:
        path = segment_cache_path(cache_root, tracking_root, SEGMENT_MANIFEST_ID, name)
        if not path.is_file():
            raise ZeroLookViolation(
                f"timesfm_export_grid: no cached frame at {path} for segment "
                f"{name!r} -- this script requires a cache HIT for every block "
                "and will not materialize one"
            )
        print(f"cache hit ready  {name:12s} {path}")

    predictor = read_frozen_predictor(
        FROZEN_PREDICTOR_MANIFEST_ID, registry_root=registry_root
    )
    artifact = load_normalization(
        predictor.normalization_manifest_id,
        normalization_dataset(str(manifest["symbol"])),
        registry_root=registry_root,
        lake_root=lake_root,
    )
    print(f"predictor_id    {predictor.predictor_id}")
    print(
        f"coef            "
        f"{dict(zip(predictor.feature_names, predictor.coef, strict=True))}"
    )

    blocks = []
    for name in block_names:
        blocks.append(
            _export_block(
                segment_name=name,
                predictor=predictor,
                params=dict(artifact.params),
                registry_root=registry_root,
                lake_root=lake_root,
                tracking_root=tracking_root,
                cache_root=cache_root,
                export_root=export_root,
                stride=int(args.stride),
                admit_unclean=bool(args.admit_unclean),
            )
        )
        mid_check = _counters(registry_root, tracking_root)
        if mid_check["counts"] != before["counts"]:
            raise ZeroLookViolation(
                f"timesfm_export_grid: look counters changed after {name} -- "
                f"before={before['counts']} after={mid_check['counts']}"
            )

    after = _counters(registry_root, tracking_root)
    _print_counters(after, "AFTER")
    body = {
        "generated_by": "mvp/scripts/timesfm_export_grid.py",
        "experiment": "E1 zero-shot TimesFM 3.0 as a baseline to beat",
        "grid_step_ns": GRID_STEP_NS,
        "context_bars": CONTEXT,
        "horizon_bars": HORIZON,
        "max_stale_ns": MAX_STALE_NS,
        "stride_bars": int(args.stride),
        "stride_exceeds_horizon": bool(int(args.stride) > HORIZON),
        "admit_unclean": bool(args.admit_unclean),
        "segment_manifest_id": SEGMENT_MANIFEST_ID,
        "frozen_predictor_manifest_id": FROZEN_PREDICTOR_MANIFEST_ID,
        "predictor_id": predictor.predictor_id,
        "normalization_manifest_id": predictor.normalization_manifest_id,
        "coef": dict(zip(predictor.feature_names, predictor.coef, strict=True)),
        "intercept": predictor.intercept,
        "train_target_mean": predictor.train_target_mean,
        "export_root": str(export_root),
        "look_counters_before": before,
        "look_counters_after": after,
        "blocks": blocks,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(body, sort_keys=True, indent=2, default=float) + "\n"
    )
    print(f"OK: wrote {out_path}")
    total = sum(int(block["admissible_anchors"]) for block in blocks)
    print(f"OK: {total:,} admissible windows across {len(blocks)} block(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
