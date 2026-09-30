"""How much of the frozen predictor's measured skill survives paying the
spread? Asked on the five OOF blocks that ALREADY HAVE CACHED FRAMES, so the
answer costs ZERO looks.

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.fill_skill_gap --counters-only
    ./.venv/bin/python3 -m scripts.fill_skill_gap

WHY THIS EXISTS. `mvp/spec.md`'s "Fast alpha / non-tradeable alpha" pitfall
already asks for it -- "track mid-vs-fill skill gap explicitly (report IC on
mid and IC on a fillable proxy)", with "Standard reporting includes both mid IC
and fillable-proxy IC" as its stated enforcement. Nothing implemented it, so
every `rank_ic` in this phase is an IC on a MID, and the simulator's
unconditional fill at the touch is what turns that into money. This script is
the first half of that enforcement: the IC on a fillable proxy, measured.

IT IS NOT THE ADVERSE-SELECTION REPORT. The same pitfall asks for an
"immediate-after-fill price reversion histogram" and this script does not
produce one. What it produces is the SKILL GAP, which is the cheaper and more
basic of the two.

THE FILLABLE PROXY IS DEFINED HERE, BECAUSE `spec.md` DOES NOT DEFINE IT, and
it is defined at the DECISION-ROW resolution rather than at the label's
10-second horizon. The reason is that the cached frame carries `bid_price` and
`ask_price` on its own rows only: recovering the book ten seconds forward would
mean re-implementing the features tier's time-based forward index outside the
tier, which is exactly the duplication the leakage CI exists to prevent. At the
decision-row resolution no such machinery is needed, and the question it asks is
if anything SHARPER: the row the simulator fills on IS the row whose book
produced the feature, so this is the assumption under scrutiny measured at its
own resolution.

    mid change      (bid + ask)[t+1] - (bid + ask)[t]      half ticks
    long fill        bid[t+1]        - ask[t]              ticks
    short fill      (bid[t]          - ask[t+1]) * -1      ticks, NEGATED

`long fill` is what a long entered at the touch on row `t` and closed at the
touch on row `t+1` actually realises -- the simulator's own per-leg arithmetic,
one row wide. `short fill` is the same for a short, NEGATED so that a larger
prediction should make it larger and its IC is comparable in SIGN to the other
two. The last row is dropped from every target: it has no next row.

ONLY RANK IC IS COMPARED ACROSS TARGETS. The four targets have four different
units (a return, half ticks, ticks, ticks), so `r2_vs_zero` and `r2_vs_mean`
are NOT comparable between them and are emitted per target for completeness
only. Spearman rank correlation is scale-free, which is why the spec's own
wording asks for IC and not R-squared.

`NUMBA_CACHE_DIR` IS PINNED AT THE TOP, BEFORE ANY IMPORT, for
`scripts/run_stage1_slice.py`'s reason in its own words: with the variable
unset, `@njit(cache=True)` writes its `.nbi`/`.nbc` files into the
`__pycache__` next to the defining source. Same `setdefault` expression as that
script, as `scripts/oof_viability_check.py` and as `tests/conftest.py`.
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
import json  # noqa: E402
from typing import Any  # noqa: E402

import numpy as np  # noqa: E402

from data.lake_paths import (  # noqa: E402
    LAKE_REGISTRY_ROOT,
    lake_root as resolve_lake_root,
    mlflow_tracking_root,
)
from features.normalize import load_normalization, normalization_dataset  # noqa: E402
from harness import budget  # noqa: E402
from harness.segments import read_segment_manifest  # noqa: E402
from models import cache as cache_module  # noqa: E402
from models.cache import (  # noqa: E402
    CACHE_ROOT,
    materialize_once,
    repo_root,
    segment_cache_path,
)
from models.frozen import read_frozen_predictor  # noqa: E402
from models.metrics import forecast_metrics  # noqa: E402
from models.regression import TARGET_NAME  # noqa: E402
from models.sweep import _scoring_columns  # noqa: E402
from sim.arrays import sim_arrays  # noqa: E402

#: The committed frozen winner (07-10), by its manifest id, which is its own
#: self-hash -- `read_frozen_predictor` re-verifies that and re-derives
#: `predictor_id` from the body's recipe fields, so a tampered body cannot be
#: scored here either.
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

EVIDENCE_DIR = Path(".planning/phases/07-regression-track-vertical-slice/evidence")


class ZeroLookViolation(RuntimeError):
    """A cache miss where this script requires a cache hit -- raised INSTEAD of
    `harness.accessor.materialize` ever running, so a reader of a traceback
    knows immediately that nothing was spent."""


def _forbid_materialize() -> None:
    """Replace the `materialize` name INSIDE `models.cache` with a raiser.

    THE LOAD-BEARING ZERO-LOOK CONTROL, copied deliberately from
    `scripts/oof_viability_check.py`: `materialize_once` reads `materialize` out
    of its own module globals on a cache MISS, so rebinding that name means a
    miss raises instead of spending an irreversible look. The cache-path
    pre-assertion in `main` is the readable check; this is the one that holds
    when the readable check is wrong.
    """

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        raise ZeroLookViolation(
            "fill_skill_gap: harness.accessor.materialize was reached, which "
            f"means a CACHE MISS for args={args!r}. This script runs at zero "
            "look cost and every frame it needs is already cached; a miss is a "
            "wrong root or a moved scratch directory, never a reason to "
            "materialize."
        )

    cache_module.materialize = _refuse  # type: ignore[assignment]


def _counters(registry_root: Path, tracking_root: str) -> dict[str, Any]:
    """Every look counter for every segment name of every COMMITTED segment
    manifest -- derived by globbing the registry, never a hardcoded list."""
    segment_dir = Path(registry_root) / "segments"
    manifest_ids = sorted(path.stem for path in segment_dir.glob("*.json"))
    if not manifest_ids:
        raise ZeroLookViolation(
            f"fill_skill_gap: no committed segment manifest under {segment_dir} "
            "-- a counter report over zero manifests would read as 'nothing "
            "spent'"
        )
    counts: dict[str, dict[str, int]] = {}
    for manifest_id in manifest_ids:
        manifest = read_segment_manifest(Path(registry_root), manifest_id)
        names = sorted({str(entry["name"]) for entry in manifest["segments"]})
        counts[manifest_id] = {
            name: budget.look_count(manifest_id, name, tracking_root=tracking_root)
            for name in names
        }
    return {
        "tracking_root": str(tracking_root),
        "counts": counts,
        "total": int(sum(sum(per.values()) for per in counts.values())),
        "n_counters": int(sum(len(per) for per in counts.values())),
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


def _targets(arrays: dict[str, np.ndarray], label: np.ndarray) -> dict[str, np.ndarray]:
    """The four targets, all on rows `0 .. n-2`, all float64.

    See the module docstring for each definition. `label` is the committed
    `ret_10s_mid`, sliced to the same rows so the comparison is over ONE row
    set -- an IC on a different row set is not a comparison.
    """
    bid = arrays["bid_ticks"].astype(np.int64)
    ask = arrays["ask_ticks"].astype(np.int64)
    book_sum = bid + ask
    return {
        "mid_10s_return_the_committed_label": np.ascontiguousarray(
            label[:-1], dtype=np.float64
        ),
        "mid_next_row_change_half_ticks": np.ascontiguousarray(
            np.diff(book_sum), dtype=np.float64
        ),
        "fill_next_row_long_ticks": np.ascontiguousarray(
            bid[1:] - ask[:-1], dtype=np.float64
        ),
        "fill_next_row_short_ticks_negated": np.ascontiguousarray(
            -(bid[:-1] - ask[1:]), dtype=np.float64
        ),
    }


def _run_block(
    *,
    segment_name: str,
    predictor: Any,
    params: dict[str, Any],
    registry_root: Path,
    lake_root: Path,
    tracking_root: str,
    cache_root: Path,
) -> dict[str, Any]:
    """One cached OOF block: the frozen prediction, scored against the mid and
    against both fillable legs, plus the countable version of the gap."""
    frame = materialize_once(
        SEGMENT_MANIFEST_ID,
        segment_name,
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=tracking_root,
        run_tags={"never_used": "this script cannot reach materialize"},
        cache_root=cache_root,
    )
    features, label = _scoring_columns(
        frame, feature_names=predictor.feature_names, params=params
    )
    arrays = sim_arrays(frame)
    pred = np.ascontiguousarray(
        np.asarray(predictor.predict(features), dtype=np.float64)[:-1], dtype=np.float64
    )
    targets = _targets(arrays, np.asarray(label, dtype=np.float64))
    rows = int(frame.height)
    spread = arrays["ask_ticks"] - arrays["bid_ticks"]
    del frame, features, label

    scored: dict[str, Any] = {}
    for name, target in targets.items():
        scored[name] = dict(
            forecast_metrics(pred, target, train_mean=predictor.train_target_mean)
        )

    # THE COUNTABLE VERSION OF THE GAP: rows where the mid moved the way the
    # prediction said, and crossing the spread ate it anyway. This is the
    # quantity the unconditional-fill assumption hands the strategy for free.
    mid_change = targets["mid_next_row_change_half_ticks"]
    long_fill = targets["fill_next_row_long_ticks"]
    short_fill = -targets["fill_next_row_short_ticks_negated"]
    predicted_long = pred > 0.0
    predicted_short = pred < 0.0
    right_way_long = predicted_long & (mid_change > 0.0)
    right_way_short = predicted_short & (mid_change < 0.0)
    eaten_long = right_way_long & (long_fill <= 0)
    eaten_short = right_way_short & (short_fill <= 0)
    right_way = int(right_way_long.sum() + right_way_short.sum())

    result = {
        "segment_name": segment_name,
        "rows": rows,
        "rows_scored": int(pred.shape[0]),
        "spread_ticks_mean": float(spread.mean()),
        "spread_ticks_min": int(spread.min()),
        "metrics_by_target": scored,
        "rank_ic_gap_mid_next_row_minus_long_fill": (
            scored["mid_next_row_change_half_ticks"]["rank_ic_all"]
            - scored["fill_next_row_long_ticks"]["rank_ic_all"]
        ),
        "rank_ic_gap_mid_next_row_minus_short_fill": (
            scored["mid_next_row_change_half_ticks"]["rank_ic_all"]
            - scored["fill_next_row_short_ticks_negated"]["rank_ic_all"]
        ),
        "rows_where_the_mid_moved_the_predicted_way": right_way,
        "of_those_rows_the_spread_ate": int(eaten_long.sum() + eaten_short.sum()),
        "fraction_of_those_rows_the_spread_ate": (
            float((int(eaten_long.sum()) + int(eaten_short.sum())) / right_way)
            if right_way
            else None
        ),
    }
    print(
        f"  {segment_name}: {rows} rows, spread {result['spread_ticks_mean']:.4f} "
        f"ticks mean\n"
        f"    rank IC on the 10s mid label   "
        f"{scored['mid_10s_return_the_committed_label']['rank_ic_all']:+.6f}\n"
        f"    rank IC on the next-row mid    "
        f"{scored['mid_next_row_change_half_ticks']['rank_ic_all']:+.6f}\n"
        f"    rank IC on a LONG at the touch "
        f"{scored['fill_next_row_long_ticks']['rank_ic_all']:+.6f}\n"
        f"    rank IC on a SHORT at the touch "
        f"{scored['fill_next_row_short_ticks_negated']['rank_ic_all']:+.6f}\n"
        f"    the mid moved the predicted way on {right_way} rows; the spread "
        f"ate {result['of_those_rows_the_spread_ate']} of them "
        f"({(result['fraction_of_those_rows_the_spread_ate'] or 0.0):.2%})"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure the mid-vs-fill skill gap spec.md's fast-alpha pitfall asks "
            "for, on the already-cached OOF blocks, at zero look cost."
        )
    )
    parser.add_argument(
        "--counters-only",
        action="store_true",
        help="query and write every look counter, then exit without reading a frame",
    )
    parser.add_argument("--out", default=None, help="evidence JSON path")
    parser.add_argument(
        "--blocks",
        default=None,
        help="comma-separated subset of oof_block names (default: all five)",
    )
    args = parser.parse_args(argv)

    registry_root = LAKE_REGISTRY_ROOT
    lake_root = resolve_lake_root()
    tracking_root = str(mlflow_tracking_root(None))
    cache_root = CACHE_ROOT
    out_path = (
        Path(args.out)
        if args.out
        else repo_root() / EVIDENCE_DIR / "07-fill-skill-gap.json"
    )
    print(f"registry_root   {registry_root}")
    print(f"lake_root       {lake_root}")
    print(f"tracking_root   {tracking_root}")
    print(f"cache_root      {cache_root}")
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
            f"fill_skill_gap: {FORBIDDEN_SEGMENT_NAME!r} appears in the OOF "
            "block list -- refusing to proceed"
        )
    if args.blocks:
        requested = [token.strip() for token in str(args.blocks).split(",")]
        unknown = [name for name in requested if name not in block_names]
        if unknown:
            raise ZeroLookViolation(
                f"fill_skill_gap: --blocks names {unknown}, which are not OOF "
                f"blocks of this manifest ({block_names})"
            )
        block_names = requested
        print(f"NOTE: --blocks restricts this run to {block_names}")
    for name in block_names:
        path = segment_cache_path(cache_root, tracking_root, SEGMENT_MANIFEST_ID, name)
        if not path.is_file():
            raise ZeroLookViolation(
                f"fill_skill_gap: no cached frame at {path} for segment {name!r} "
                "-- this script requires a cache HIT for every block and will "
                "not materialize one"
            )
        print(f"cache hit ready  {name:12s} {path}")

    predictor = read_frozen_predictor(
        FROZEN_PREDICTOR_MANIFEST_ID, registry_root=registry_root
    )
    print(f"predictor_id    {predictor.predictor_id}")
    artifact = load_normalization(
        predictor.normalization_manifest_id,
        normalization_dataset(str(manifest["symbol"])),
        registry_root=registry_root,
        lake_root=lake_root,
    )

    blocks = []
    for name in block_names:
        blocks.append(
            _run_block(
                segment_name=name,
                predictor=predictor,
                params=dict(artifact.params),
                registry_root=registry_root,
                lake_root=lake_root,
                tracking_root=tracking_root,
                cache_root=cache_root,
            )
        )
        mid_check = _counters(registry_root, tracking_root)
        if mid_check["counts"] != before["counts"]:
            raise ZeroLookViolation(
                f"fill_skill_gap: look counters changed after {name} -- "
                f"before={before['counts']} after={mid_check['counts']}"
            )

    after = _counters(registry_root, tracking_root)
    _print_counters(after, "AFTER")
    body = {
        "generated_by": "mvp/scripts/fill_skill_gap.py",
        "what_this_measures": (
            "spec.md's 'report IC on mid and IC on a fillable proxy' for the "
            "committed frozen predictor, at the DECISION-ROW resolution: the "
            "next row's mid change against what a long or a short entered and "
            "closed at the touch actually realises. NOT the 10-second-horizon "
            "fillable return, which would need the book ten seconds forward and "
            "therefore a second copy of the features tier's forward index."
        ),
        "in_sample_disclosure": (
            "These five OOF blocks chose the frozen winner and the frozen body "
            "was fitted on a train cache containing all five, so every number "
            "here is in sample twice over -- a diagnostic, not a performance "
            "claim. Same disclosure as 07-oof-viability-results.json."
        ),
        "only_rank_ic_is_comparable_across_targets": (
            "The four targets have four different units (a return, half ticks, "
            "ticks, ticks). r2_vs_zero and r2_vs_mean are emitted per target for "
            "completeness and must NOT be compared between targets; Spearman "
            "rank correlation is scale-free, which is why the spec asks for IC."
        ),
        "frozen_predictor_manifest_id": FROZEN_PREDICTOR_MANIFEST_ID,
        "predictor_id": predictor.predictor_id,
        "segment_manifest_id": SEGMENT_MANIFEST_ID,
        "target_name_fitted": TARGET_NAME,
        "look_counters_before": before,
        "look_counters_after": after,
        "blocks": blocks,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(body, sort_keys=True, indent=2, default=float) + "\n"
    )
    print(f"OK: wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
