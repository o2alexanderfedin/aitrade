"""Split the one validation look into its two days, so a reader can see whether
both days carried the result or one of them did.

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.val_per_day_report

WHY A TWO-DAY `val` WAS CHOSEN, and therefore why this script exists. Plan 07-11
picked a two-day validation window precisely so ONE look yields two independent
observations. A pooled number cannot tell the difference between a model that
works on both days and a model that works on one and is carried by it, and that
difference is the whole reason the window has two days in it.

ZERO LOOKS, AND THIS IS THE LOAD-BEARING PROPERTY. Every number here comes from
the ALREADY-PAID val cache (`<cache_root>/<tracking>/<segment>/val.parquet`) and
the ALREADY-STORED prediction table in the lake. `harness.accessor` is never
imported and `materialize` is never called, so re-reading this as many times as
anyone likes costs nothing. `val` is already 1 and must stay 1.

THE STATISTICS ARE NOT REIMPLEMENTED. `models.metrics.forecast_metrics`,
`models.sim.run_sim_checked` and the three bound functions are the SAME
callables `models.slice` used for the pooled numbers, called here on a row slice.
A second implementation that happened to differ in its tie handling or its
scorable mask would look like a per-day finding. The pooled numbers are
recomputed here too and cross-checked against the MLflow run's own metrics, so
the slicing is verified before it is trusted.

`train_mean` COMES FROM THE FROZEN BODY, never from a day's own rows. The
zero-skill control column is a constant at the TRAIN mean; a per-day mean would
be a different and strictly weaker statistic reported under a name that says
train.

WHAT A DAY BOUNDARY DOES TO THE SIMULATION, stated because the numbers will not
add up and that is correct. The simulator starts flat and ends flat, so running
it on each day separately forces a close at midnight and a fresh open after it.
The two per-day P&Ls therefore do NOT sum to the pooled P&L, and the residual is
reported rather than hidden. The forecast metrics have no such coupling: they are
row-wise, so those DO reconcile and the script checks that they do.
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

from models.frozen import read_frozen_predictor  # noqa: E402
from models.gates import (  # noqa: E402
    closed_pnl_ticks,
    decision_row_perfect_foresight,
    mid_total_variation,
    neutral_fill_null_predictions,
    perfect_foresight_ceiling,
    run_sim_checked,
    sim_arrays,
)
from models.metrics import forecast_metrics  # noqa: E402
from models.slice import TARGET_NAME  # noqa: E402

#: The paid val cache and the stored table. Both are read-only here.
DEFAULT_CACHE = Path(
    "/Volumes/ProjectsSSD/aihedgefund/scratch/phase07/0d85c8daacc2fe5e/"
    "807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548/val.parquet"
)
DEFAULT_TABLE = Path(
    "/Volumes/ProjectsSSD/aihedgefund/lake/predictions/symbol=BTCUSDT/"
    "segment_manifest=807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548/"
    "segment=val/predictor=ff91c9aa2a595513/part-1790835666098050000.parquet"
)

#: The frozen winner, by its registry manifest id.
PREDICTOR_MANIFEST_ID = (
    "e3b4b235d0fe3c025be7c7cdbf7eaa3d2d5675ad9571d6f6a30b1a45680c2d01"
)

#: The pooled metrics the MLflow run recorded, transcribed so the slicing can be
#: verified against them before any per-day number is believed. A mismatch here
#: means this script is not reading what the run read.
POOLED_EXPECTED: dict[str, float] = {
    "n_admitted": 16294059.0,
    "n_scorable": 16285445.0,
    "r2_vs_zero": 0.014151305572264405,
    "r2_vs_mean": 0.013563359281659748,
    "rank_ic_all": 0.24851772029981803,
    "rank_ic_non_tied": 0.2463008632016707,
    "tie_fraction": 0.13832959430951994,
    "r2_vs_zero_of_constant_train_mean": 0.00019688603089518253,
    "sim_trades": 70546.0,
    "sim_closed_pnl_ticks": 2638760.0,
}

#: Relative tolerance for the pooled cross-check. The forecast metrics must agree
#: to floating-point noise; a real slicing error moves them far more than this.
POOLED_RTOL: float = 1e-12


#: The UTC date each row belongs to, derived WITHOUT arithmetic.
#: `pl.from_epoch(..., time_unit="ns")` is used rather than dividing by a
#: billion, because `tools/check_ms_to_ns_site.py` counts a division by 1e9 as a
#: seconds-to-nanoseconds conversion SITE and permits only allowlisted ones --
#: and it is right to: this script has no business owning a time-unit
#: conversion. It is also the faster route, the alternative being a Python
#: callable mapped over sixteen million rows.
DAY_EXPR = pl.from_epoch(pl.col("etime"), time_unit="ns").dt.date().alias("day")


def _score(
    frame: pl.DataFrame, pred: np.ndarray, *, train_mean: float
) -> dict[str, Any]:
    """Forecast metrics, the three bounds and the simulation, for one row slice."""
    target = np.asarray(
        frame[TARGET_NAME].fill_null(float("nan")).to_numpy(), dtype=np.float64
    )
    out: dict[str, Any] = dict(forecast_metrics(pred, target, train_mean=train_mean))
    out["n_admitted"] = float(frame.height)

    mid = np.ascontiguousarray(
        np.asarray(frame["mid"].to_numpy(), dtype=np.float64), dtype=np.float64
    )
    pred_price, n_missing = neutral_fill_null_predictions(pred, mid)
    out["n_pred_missing"] = float(n_missing)

    arrays = sim_arrays(frame)
    sim = run_sim_checked(
        arrays["etime"],
        arrays["bid_ticks"],
        arrays["ask_ticks"],
        pred_price,
        x_bps=0,
    )
    ticks = closed_pnl_ticks(sim)
    bound = decision_row_perfect_foresight(frame)
    variation = mid_total_variation(frame)
    ceiling = perfect_foresight_ceiling(frame, label_column=TARGET_NAME)
    out.update(
        {
            "sim_trades": float(sim.fill_count),
            "sim_flips": float(sim.counters["flips"]),
            "sim_closed_pnl_ticks": float(ticks),
            "ceiling_closed_pnl_ticks": float(ceiling["closed_pnl_ticks"]),
            "ceiling_pnl_usd": float(ceiling["closed_pnl_usd_at_traded_lot"]),
            "pnl_fraction_of_ceiling": (
                float(ticks) / float(ceiling["closed_pnl_ticks"])
                if int(ceiling["closed_pnl_ticks"])
                else float("nan")
            ),
            "mid_total_variation_ticks": float(variation["total_variation_ticks"]),
            "pnl_fraction_of_mid_total_variation": (
                float(ticks) / float(variation["total_variation_ticks"])
                if int(variation["total_variation_ticks"])
                else float("nan")
            ),
            "decision_row_pf_closed_pnl_ticks": float(bound["closed_pnl_ticks"]),
            "pnl_fraction_of_decision_row_pf": (
                float(ticks) / float(bound["closed_pnl_ticks"])
                if int(bound["closed_pnl_ticks"])
                else float("nan")
            ),
        }
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument(
        "--registry-root", type=Path, default=Path("data/lake_registry")
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    for path in (args.cache, args.table):
        if not path.is_file():
            raise SystemExit(f"val_per_day_report: missing {path}")

    val = pl.read_parquet(args.cache)
    table = pl.read_parquet(args.table)
    if table.height != val.height:
        raise SystemExit(
            f"val_per_day_report: the table has {table.height} rows and the "
            f"cached frame {val.height} -- they do not describe the same segment"
        )
    # The alignment assertion the run already made, repeated independently: the
    # table is emitted POSITIONALLY, so both keys must match row for row.
    for key in ("etime", "decision_seq"):
        if not np.array_equal(val[key].to_numpy(), table[key].to_numpy()):
            raise SystemExit(
                f"val_per_day_report: {key} differs between the cached frame and "
                "the stored table -- the prediction array is misaligned"
            )

    predictor = read_frozen_predictor(
        PREDICTOR_MANIFEST_ID, registry_root=args.registry_root
    )
    train_mean = float(predictor.train_target_mean)
    pred = np.asarray(table["pred"].to_numpy(), dtype=np.float64)

    report: dict[str, Any] = {
        "generated_by": "mvp/scripts/val_per_day_report.py",
        "segment": "val",
        "predictor_manifest_id": PREDICTOR_MANIFEST_ID,
        "predictor_id": predictor.predictor_id,
        "train_target_mean": train_mean,
        "looks_spent_by_this_script": 0,
        "day_boundary_note": (
            "The simulator starts and ends flat, so a per-day run forces a close "
            "at midnight and a fresh open after it. The per-day P&Ls therefore do "
            "not sum to the pooled P&L; the residual is reported. Forecast metrics "
            "are row-wise and do reconcile, which this script checks."
        ),
        "pooled": {},
        "pooled_crosscheck": {},
        "days": {},
    }

    pooled = _score(val, pred, train_mean=train_mean)
    report["pooled"] = pooled
    bad: list[str] = []
    for key, expected in POOLED_EXPECTED.items():
        got = float(pooled[key])
        ok = (
            got == expected
            if expected == 0.0
            else abs(got - expected) <= POOLED_RTOL * abs(expected)
        )
        report["pooled_crosscheck"][key] = {
            "mlflow_run": expected,
            "recomputed_here": got,
            "agrees": bool(ok),
        }
        if not ok:
            bad.append(f"{key}: run={expected!r} here={got!r}")
    if bad:
        raise SystemExit(
            "val_per_day_report: the pooled recomputation disagrees with the "
            "MLflow run, so no per-day number from this script can be trusted:\n  "
            + "\n  ".join(bad)
        )
    print(
        "OK: pooled recomputation matches the MLflow run on all "
        f"{len(POOLED_EXPECTED)} transcribed metrics"
    )

    days = np.asarray(
        [d.isoformat() for d in val.select(DAY_EXPR)["day"].to_list()], dtype=object
    )
    sum_ticks = 0.0
    sum_scorable = 0.0
    for day in sorted(set(days.tolist())):
        mask = days == day
        slice_frame = val.filter(pl.Series(mask))
        entry = _score(slice_frame, pred[mask], train_mean=train_mean)
        entry["utc_date"] = day
        report["days"][day] = entry
        sum_ticks += entry["sim_closed_pnl_ticks"]
        sum_scorable += entry["n_scorable"]
        print(
            f"  {day}  n={int(entry['n_admitted']):>9,}  "
            f"r2_vs_zero={entry['r2_vs_zero']:+.6f}  "
            f"ic_non_tied={entry['rank_ic_non_tied']:+.6f}  "
            f"tie={entry['tie_fraction']:.6f}  "
            f"trades={int(entry['sim_trades']):>7,}  "
            f"ticks={int(entry['sim_closed_pnl_ticks']):>9,}",
            flush=True,
        )

    report["reconciliation"] = {
        "scorable_rows_sum_of_days": sum_scorable,
        "scorable_rows_pooled": float(pooled["n_scorable"]),
        "scorable_rows_reconcile": bool(sum_scorable == float(pooled["n_scorable"])),
        "sim_ticks_sum_of_days": sum_ticks,
        "sim_ticks_pooled": float(pooled["sim_closed_pnl_ticks"]),
        "sim_ticks_residual_from_day_boundary": (
            float(pooled["sim_closed_pnl_ticks"]) - sum_ticks
        ),
    }
    rec = report["reconciliation"]
    if not rec["scorable_rows_reconcile"]:
        raise SystemExit(
            "val_per_day_report: the per-day scorable counts do not sum to the "
            "pooled count -- the day split loses or duplicates rows"
        )
    print(
        f"OK: scorable rows reconcile ({int(sum_scorable):,}); simulated ticks "
        f"differ by {int(rec['sim_ticks_residual_from_day_boundary']):,} from the "
        "pooled run, which is the forced midnight close"
    )

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
