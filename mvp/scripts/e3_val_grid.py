"""Put the validation days on the same regularly-spaced clock a foundation model
needs, so Google's TimesFM can be judged on exactly the data the project's own
model was judged on.

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.e3_val_grid

WHY THIS EXISTS. Experiments E1 and E2 scored TimesFM 3.0 on the five
out-of-sample blocks, which all sit INSIDE the training period. The project's own
frozen model was selected on those blocks, so every comparison there flattered
TimesFM's opponent: one model was in sample and the other had never seen the
data. The validation days 2026-09-17..18 are the one window where NEITHER model
has any advantage -- the frozen body was committed to git before those days were
ever read, and TimesFM is zero-shot by construction. That makes this the only
like-for-like measurement in the project.

ZERO LOOKS, AND BY A STRONGER ROUTE THAN E1'S. E1 called `models.cache
.materialize_once` with the accessor rebound to a raiser, so a cache miss would
abort instead of spending a look. This script does not import the accessor or
the cache at all: it reads the already-paid `val.parquet` DIRECTLY with polars.
There is no code path from here to a look. `look_count(val)` is 1 and nothing in
this file can make it 2.

THE GRID RULE IS IMPORTED, NOT RESTATED. `_grid_stamps`, `_asof_backward`,
`_clean_mask`, `_admissible_anchors` and `_scoring_columns` all come from the E1
exporter and the sweep, so the validation grid is built by the same code that
built the five block grids. A second implementation that differed in its hole
handling or its null accounting would look like a finding about the validation
days.

THE FROZEN PREDICTION IS COMPUTED TWICE, ON PURPOSE. Once by running the frozen
body over the grid's normalised features -- E1's route -- and once by taking the
COMMITTED prediction table, the one plan 07-11 stored and manifest-addressed, and
carrying it onto the grid stamps with the same backward join. Those are two
independent paths to the same number: the first re-derives it from coefficients
and the normalisation artifact, the second reads what the production run actually
wrote and committed. They must agree, and the script refuses if they do not.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/nbc")

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

from features.normalize import load_normalization, normalization_dataset  # noqa: E402
from models.frozen import read_frozen_predictor  # noqa: E402
from models.sweep import _scoring_columns  # noqa: E402
from scripts.timesfm_export_grid import (  # noqa: E402
    CONTEXT,
    HORIZON,
    MAX_STALE_NS,
    STRIDE,
    TARGET_NAME,
    _admissible_anchors,
    _asof_backward,
    _assert_horizon_is_the_label_horizon,
    _clean_mask,
    _grid_stamps,
)

#: The already-paid validation frame. Read-only, and the ONLY data input.
DEFAULT_CACHE = Path(
    "/Volumes/ProjectsSSD/aihedgefund/scratch/phase07/0d85c8daacc2fe5e/"
    "807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548/val.parquet"
)

#: The committed prediction table, used as the independent check on the frozen
#: prediction rather than as its source.
DEFAULT_TABLE = Path(
    "/Volumes/ProjectsSSD/aihedgefund/lake/predictions/symbol=BTCUSDT/"
    "segment_manifest=807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548/"
    "segment=val/predictor=ff91c9aa2a595513/part-1790835666098050000.parquet"
)

PREDICTOR_MANIFEST_ID = (
    "e3b4b235d0fe3c025be7c7cdbf7eaa3d2d5675ad9571d6f6a30b1a45680c2d01"
)
SEGMENT_MANIFEST_ID = "807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548"
SYMBOL = "BTCUSDT"

#: Where the TimesFM interpreter reads its inputs.
DEFAULT_EXPORT_ROOT = Path("/Volumes/ProjectsSSD/aihedgefund/timesfm/work/e3")

#: The two paths to the frozen prediction must agree to this, in return units.
#: Not zero: the committed table was written by a different process at a
#: different commit, so the tolerance allows float64 round-trip through parquet
#: and nothing more.
PRED_AGREEMENT_TOL: float = 1e-15


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument(
        "--registry-root", type=Path, default=Path("data/lake_registry")
    )
    parser.add_argument(
        "--lake-root", type=Path, default=Path("/Volumes/ProjectsSSD/aihedgefund/lake")
    )
    parser.add_argument("--export-root", type=Path, default=DEFAULT_EXPORT_ROOT)
    parser.add_argument("--stride", type=int, default=STRIDE)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    for path in (args.cache, args.table):
        if not path.is_file():
            raise SystemExit(f"e3_val_grid: missing {path}")

    predictor = read_frozen_predictor(
        PREDICTOR_MANIFEST_ID, registry_root=args.registry_root
    )
    artifact = load_normalization(
        predictor.normalization_manifest_id,
        normalization_dataset(SYMBOL),
        registry_root=args.registry_root,
        lake_root=args.lake_root,
    )
    params: dict[str, Any] = dict(artifact.params)

    columns = [
        "etime",
        "mid",
        *predictor.feature_names,
        TARGET_NAME,
        "warmup",
        "post_gap_warmup",
    ]
    val = pl.read_parquet(args.cache).select(columns)
    rows = int(val.height)
    print(f"cached val frame   {rows:,} rows (read directly; no accessor imported)")

    stamps = _grid_stamps(val["etime"].to_numpy())
    _assert_horizon_is_the_label_horizon(stamps)
    grid = _asof_backward(val, stamps)
    del val

    clean = _clean_mask(grid)
    features, target_frame = _scoring_columns(
        grid, feature_names=list(predictor.feature_names), params=params
    )
    pred_frozen = np.asarray(predictor.predict(features), dtype=np.float64)
    imb_top_norm = np.ascontiguousarray(features[:, 0], dtype=np.float64)
    mid = np.asarray(grid["mid"].to_numpy(), dtype=np.float64)
    anchor_etime = grid["anchor_etime"].to_numpy().astype(np.int64)
    grid_ns = grid["grid_ns"].to_numpy().astype(np.int64)
    stale_ns = grid_ns - anchor_etime

    # THE INDEPENDENT CHECK. The committed table carries one prediction per
    # DECISION row; the grid's value at stamp T belongs to the last decision row
    # at or before T, which is exactly `anchor_etime`. So joining the table on
    # `anchor_etime` must reproduce `pred_frozen` -- re-derived from
    # coefficients here, read from committed bytes there.
    table = pl.read_parquet(args.table).select("etime", "pred")
    joined = (
        pl.DataFrame({"etime": anchor_etime})
        .join(table, on="etime", how="left")
        .get_column("pred")
        .to_numpy()
    )
    both = np.isfinite(pred_frozen) & np.isfinite(joined)
    if not both.any():
        raise SystemExit(
            "e3_val_grid: the committed table and the re-derived prediction "
            "share no finite row -- the join key is wrong"
        )
    worst = float(np.max(np.abs(pred_frozen[both] - joined[both])))
    if worst > PRED_AGREEMENT_TOL:
        raise SystemExit(
            f"e3_val_grid: the frozen prediction re-derived from coefficients "
            f"disagrees with the COMMITTED prediction table by {worst:.3e} -- one "
            "of the two paths is wrong and no TimesFM comparison built on this "
            "grid would mean anything"
        )
    missing_in_table = int((~np.isfinite(joined) & np.isfinite(pred_frozen)).sum())
    print(
        f"frozen prediction  two independent paths agree to {worst:.3e} on "
        f"{int(both.sum()):,} grid rows ({missing_in_table} grid rows had no "
        "table match)"
    )

    anchor_ok = (
        np.isfinite(pred_frozen)
        & np.isfinite(target_frame)
        & np.isfinite(mid)
        & (mid > 0.0)
    )
    del grid, features

    anchors = _admissible_anchors(clean, anchor_ok, stride=int(args.stride))
    if anchors.size == 0:
        raise SystemExit("e3_val_grid: zero admissible windows -- nothing to export")

    args.export_root.mkdir(parents=True, exist_ok=True)
    out_path = args.export_root / "val.npz"
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
    report: dict[str, Any] = {
        "generated_by": "mvp/scripts/e3_val_grid.py",
        "segment": "val",
        "segment_manifest_id": SEGMENT_MANIFEST_ID,
        "predictor_manifest_id": PREDICTOR_MANIFEST_ID,
        "predictor_id": predictor.predictor_id,
        "normalization_manifest_id": predictor.normalization_manifest_id,
        "source_cache": str(args.cache),
        "committed_table": str(args.table),
        "looks_spent_by_this_script": 0,
        "decision_rows": rows,
        "grid_bars": int(grid_ns.shape[0]),
        "clean_bars": int(clean.sum()),
        "clean_fraction": float(clean.mean()),
        "anchor_ok_bars": int(anchor_ok.sum()),
        "admissible_windows": int(anchors.shape[0]),
        "context_bars": CONTEXT,
        "horizon_bars": HORIZON,
        "stride": int(args.stride),
        "max_stale_ns": MAX_STALE_NS,
        "frozen_pred_two_path_max_abs_diff": worst,
        "frozen_pred_grid_rows_without_table_match": missing_in_table,
        "output": str(out_path),
    }
    print(
        f"grid               {report['grid_bars']:,} bars, "
        f"{report['clean_fraction']:.6f} clean, "
        f"{report['admissible_windows']:,} admissible windows"
    )
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
