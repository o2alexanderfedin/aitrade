"""Stage 3 of experiment E1: score zero-shot TimesFM 3.0, the constant-zero
reference and the frozen Stage-1 winner ON THE SAME 1-SECOND GRID ROWS, and
ask whether a foundation model's quantiles know anything about volatility.

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.timesfm_score_grid --counters-only
    ./.venv/bin/python3 -m scripts.timesfm_score_grid

IT OPENS NO FRAME AND TOUCHES NO LAKE TIER. Everything it scores arrives in the
`.npz` files written by `scripts/timesfm_export_grid.py` (the grid, the frozen
predictor's own predictions) and `tools/timesfm_forecast.py` (the forecasts).
The look counters are still queried before and after, because "it cannot have
spent a look" is an argument and a counter is a measurement.

THE ONE THING THAT MAKES THE COMPARISON MEAN ANYTHING. All three models are
scored through ONE shared row mask and `models.metrics.forecast_metrics` is
handed the same `y` for each, with `n_scorable` asserted equal across them. The
frozen predictor's published `r2_vs_zero` of 0.006-0.035 was measured on
EVENT-DRIVEN decision rows; nothing here may be quoted beside it, because a
1-second grid is a different row population with a different denominator. What
IS comparable is the three columns below, against each other.

THE NEGATIVE CONTROLS ARE WRITTEN HERE BEFORE ANY NUMBER WAS SEEN, and they run
UNCONDITIONALLY rather than only when the headline looks good -- a control that
fires only when it is wanted is not a control:

- `timesfm_stale`: the PREVIOUS window's forecast scored at this window's
  anchor, i.e. a forecast 20 bars out of date. A forecast carrying timely
  information must lose most of its score when it is stale. One that does not
  is a slow-moving artifact of the price level, not a forecast.
- `y_permuted`: the headline forecast scored against a seeded permutation of
  its own targets. This is the null: whatever it scores is what this statistic
  pays for having the right marginal distribution and no alignment.
- `timesfm_raw_contexts`: the same model on the same windows fed the RAW price
  instead of the level-shifted price. Not a null -- a PRECISION arm, kept
  because the 64-window control measured the float32 disagreement at 41% of one
  return sigma and a number that large cannot be waved away.

A BOOTSTRAP CONFIDENCE INTERVAL ON EVERY HEADLINE STATISTIC, for the same
reason. `r2_vs_zero = +0.0003` and `r2_vs_zero = -0.0003` are the same finding,
and only an interval says so. Ordinary resampling over windows is legitimate
HERE and would not be on the raw decision rows: the exporter's stride (20 bars)
exceeds the horizon (10 bars), so NO TWO SCORED TARGETS OVERLAP IN TIME and the
autocorrelation that makes naive bootstrapping wrong on overlapping forward
returns is absent by construction.

`NUMBA_CACHE_DIR` IS PINNED AT THE TOP, BEFORE ANY IMPORT, for
`scripts/run_stage1_slice.py`'s reason in its own words: with the variable
unset, `@njit(cache=True)` writes its `.nbi`/`.nbc` files into the `__pycache__`
next to the defining source. Same `setdefault` expression as that script, as
`scripts/oof_viability_check.py` and as `tests/conftest.py`.
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
import warnings  # noqa: E402
from typing import Any  # noqa: E402

import numpy as np  # noqa: E402
from scipy.stats import ConstantInputWarning, spearmanr  # noqa: E402

from data.lake_paths import (  # noqa: E402
    LAKE_REGISTRY_ROOT,
    mlflow_tracking_root,
)
from harness import budget  # noqa: E402
from harness.segments import read_segment_manifest  # noqa: E402
from models.cache import repo_root  # noqa: E402
from models.metrics import forecast_metrics  # noqa: E402
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED  # noqa: E402

#: Bars of horizon. Must equal the exporter's `HORIZON`; cross-checked against
#: the export manifest's own recorded value rather than trusted.
HORIZON: int = 10

#: Bars between consecutive scored windows, cross-checked the same way. Its
#: only job in THIS file is to justify the bootstrap: `STRIDE > HORIZON` is
#: what makes the scored targets non-overlapping.
STRIDE: int = 20

#: The nine quantile levels, in the order `tools/timesfm_forecast.py` wrote
#: them. Cross-checked against the `quantile_levels` array in every forecast
#: `.npz` so a reordering is a failure rather than a mislabelled column.
QUANTILE_LEVELS: tuple[float, ...] = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)

#: One tick in dollars, DERIVED from the two scaled constants `sim/ticks.py`
#: pins against the real venue rather than respelled as 0.1 here. The sim works
#: in scaled integers and never needs this; a price-space mean absolute error
#: does, because "$0.94" means nothing until it is read as "9.4 ticks".
TICK_SIZE: float = TICK_SIZE_SCALED / PRICE_SCALE

#: Bootstrap resamples per statistic, and the seed. 2000 is enough for a
#: two-sided 95% interval to be stable in its third decimal, which is one more
#: digit than any conclusion here rests on.
N_BOOTSTRAP: int = 2000
BOOTSTRAP_SEED: int = 20260930

#: Seed for the `y_permuted` null. Pinned so the number is reproducible rather
#: than a different draw on every run.
PERMUTATION_SEED: int = 20260930

#: Trailing bars used by the volatility BASELINE the quantile width is compared
#: against. Equal to the model's own context, so the two see the same history
#: and the comparison is about what each does with it.
VOL_LOOKBACK: int = 512

EVIDENCE_DIR = Path(".planning/phases/07-regression-track-vertical-slice/evidence")
DEFAULT_EXPORT_ROOT = Path("/Volumes/ProjectsSSD/aihedgefund/timesfm/work/e1")


class ScoringError(RuntimeError):
    """An input this script will not score rather than score wrongly."""


def _counters(registry_root: Path, tracking_root: str) -> dict[str, Any]:
    """Every look counter for every segment name of every COMMITTED segment
    manifest -- globbed, never a hardcoded list, so a manifest added later is
    counted rather than silently missed. Same shape as
    `scripts/oof_viability_check.py`'s so the two reports are comparable.
    """
    segment_dir = Path(registry_root) / "segments"
    manifest_ids = sorted(path.stem for path in segment_dir.glob("*.json"))
    if not manifest_ids:
        raise ScoringError(
            f"timesfm_score_grid: no committed segment manifest under "
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


def _rank_ic(pred: np.ndarray, y: np.ndarray) -> float:
    """`spearmanr(...).statistic`, NaN for a constant input rather than a
    warning printed to a log nobody reads -- `models.metrics._rank_ic`'s own
    handling, reused here for the statistics that module does not compute.
    """
    if pred.shape[0] < 2:
        return float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConstantInputWarning)
        try:
            return float(spearmanr(pred, y).statistic)
        except ConstantInputWarning:
            return float("nan")


def _bootstrap(
    pred: np.ndarray, y: np.ndarray, *, seed: int, n: int
) -> dict[str, list[float] | None]:
    """Percentile intervals for `r2_vs_zero` and `rank_ic_all`.

    Resamples WINDOWS with replacement. Legitimate only because the scored
    targets do not overlap (`STRIDE > HORIZON`); on overlapping forward returns
    this would understate the interval badly, which is why the precondition is
    asserted by the caller rather than assumed here.

    Returns `None` for a statistic that is undefined on the resample (a
    constant prediction has no rank correlation), rather than a number that
    would read as a measurement.
    """
    rng = np.random.default_rng(seed)
    size = pred.shape[0]
    r2 = np.empty(n, dtype=np.float64)
    ic = np.empty(n, dtype=np.float64)
    for i in range(n):
        idx = rng.integers(0, size, size=size)
        p, t = pred[idx], y[idx]
        ss_zero = float((t**2).sum())
        r2[i] = 1.0 - float(((t - p) ** 2).sum()) / ss_zero if ss_zero else np.nan
        ic[i] = _rank_ic(p, t)
    out: dict[str, list[float] | None] = {}
    for key, draws in (("r2_vs_zero", r2), ("rank_ic_all", ic)):
        finite = draws[np.isfinite(draws)]
        out[f"{key}_ci95"] = (
            [float(np.percentile(finite, 2.5)), float(np.percentile(finite, 97.5))]
            if finite.size >= n // 2
            else None
        )
    return out


def _quantile_findings(
    *,
    quantiles: np.ndarray,
    anchor_mid: np.ndarray,
    realised_terminal_mid: np.ndarray,
    y: np.ndarray,
    trailing_vol: np.ndarray,
) -> dict[str, Any]:
    """Does the forecast's own uncertainty band know anything -- and does it
    know more than one line of numpy?

    Two questions, kept apart because they have different answers:

    1. CALIBRATION. How often does the realised terminal mid actually fall
       inside the 80% and 60% bands? A band that covers 40% of the time when it
       claims 80% is not a volatility forecast whatever it correlates with.
    2. DISCRIMINATION. Does the band get WIDER when the next ten seconds turn
       out to be bigger? Measured as Spearman(width, |y|) -- and beside it the
       same Spearman for a TRAILING REALISED VOLATILITY baseline over the same
       512 bars the model saw. Without that baseline "the width tracks
       volatility" is unfalsifiable: trailing vol predicts future vol in every
       financial series ever measured, so the only interesting question is
       whether a 1.2 GiB pretrained prior beats `std(diff(log(mid)))`.
    """
    lo80 = quantiles[:, HORIZON - 1, 0]
    hi80 = quantiles[:, HORIZON - 1, 8]
    lo60 = quantiles[:, HORIZON - 1, 1]
    hi60 = quantiles[:, HORIZON - 1, 7]
    width80 = (hi80 - lo80) / anchor_mid
    width60 = (hi60 - lo60) / anchor_mid
    abs_y = np.abs(y)
    return {
        "coverage_80_nominal": float(
            ((realised_terminal_mid >= lo80) & (realised_terminal_mid <= hi80)).mean()
        ),
        "coverage_60_nominal": float(
            ((realised_terminal_mid >= lo60) & (realised_terminal_mid <= hi60)).mean()
        ),
        "width_80_return_median": float(np.median(width80)),
        "width_80_return_mean": float(width80.mean()),
        "width_60_return_median": float(np.median(width60)),
        "abs_y_median": float(np.median(abs_y)),
        "abs_y_mean": float(abs_y.mean()),
        "width_80_over_abs_y_median": (
            float(np.median(width80) / np.median(abs_y))
            if np.median(abs_y) > 0.0
            else None
        ),
        "spearman_width80_vs_abs_y": _rank_ic(width80, abs_y),
        "spearman_trailing_vol_vs_abs_y": _rank_ic(trailing_vol, abs_y),
        "spearman_width80_vs_trailing_vol": _rank_ic(width80, trailing_vol),
        "trailing_vol_median": float(np.median(trailing_vol)),
        "quantiles_monotone_fraction": float(
            (np.diff(quantiles[:, HORIZON - 1, :], axis=1) >= 0.0).all(axis=1).mean()
        ),
    }


def _trailing_vol(mid: np.ndarray, anchors: np.ndarray) -> np.ndarray:
    """Realised 1-second return volatility over the trailing `VOL_LOOKBACK`
    bars, scaled to the 10-bar horizon by the square root of time.

    The baseline the model's quantile width has to beat. Uses only bars at or
    before the anchor -- the same information set the model's context is, so a
    win either way is about the method and not about the data.
    """
    out = np.empty(anchors.shape[0], dtype=np.float64)
    for i, k in enumerate(anchors):
        k = int(k)
        window = mid[k - VOL_LOOKBACK + 1 : k + 1]
        step = np.diff(window) / window[:-1]
        out[i] = float(step.std()) * np.sqrt(float(HORIZON))
    return out


def _score_block(
    *,
    block: str,
    export_root: Path,
    shifted_root: Path,
    raw_root: Path,
    train_target_mean: float,
) -> dict[str, Any]:
    """One block: every model, one shared row mask, one target."""
    grid = np.load(export_root / f"{block}.npz")
    shifted = np.load(shifted_root / f"{block}.npz")
    raw = np.load(raw_root / f"{block}.npz")

    for name, forecast in (("shifted", shifted), ("raw", raw)):
        levels = np.asarray(forecast["quantile_levels"], dtype=np.float64)
        if not np.array_equal(levels, np.asarray(QUANTILE_LEVELS, dtype=np.float64)):
            raise ScoringError(
                f"timesfm_score_grid: {block} {name} arm records quantile "
                f"levels {levels.tolist()}, not {list(QUANTILE_LEVELS)} -- "
                "every quantile column would be mislabelled"
            )

    mid = np.asarray(grid["mid"], dtype=np.float64)
    anchors = np.asarray(grid["anchors"], dtype=np.int64)
    for name, forecast in (("shifted", shifted), ("raw", raw)):
        if not np.array_equal(np.asarray(forecast["anchors"], dtype=np.int64), anchors):
            raise ScoringError(
                f"timesfm_score_grid: {block} {name} arm's anchors differ from "
                "the export's -- two different row populations cannot be scored "
                "against one target"
            )
        # The alignment claim, re-derived here rather than trusted: the last bar
        # the model read must be the anchor's own mid.
        recorded = np.asarray(forecast["context_last"], dtype=np.float64)
        if not np.array_equal(recorded, mid[anchors]):
            raise ScoringError(
                f"timesfm_score_grid: {block} {name} arm's recorded context tail "
                "is not the anchor's mid -- the forecast window was misaligned"
            )

    anchor_mid = mid[anchors]
    realised_terminal_mid = mid[anchors + HORIZON]
    # THE TARGET EVERY MODEL IS SCORED AGAINST: the realised return between two
    # PREVAILING mids exactly HORIZON bars -- 10 s -- apart.
    y_grid = realised_terminal_mid / anchor_mid - 1.0
    # The frame's OWN `ret_10s_mid` at the same anchor rows, carried through so
    # the grid target can be checked against the catalogue label rather than
    # asserted to resemble it.
    y_frame = np.asarray(grid["ret_10s_mid_frame"], dtype=np.float64)[anchors]

    point_shifted = np.asarray(shifted["point"], dtype=np.float64)
    point_raw = np.asarray(raw["point"], dtype=np.float64)
    pred_timesfm = point_shifted[:, HORIZON - 1] / anchor_mid - 1.0
    pred_timesfm_raw = point_raw[:, HORIZON - 1] / anchor_mid - 1.0
    pred_frozen = np.asarray(grid["pred_frozen"], dtype=np.float64)[anchors]
    pred_zero = np.zeros_like(pred_timesfm)
    # The stale control: the PREVIOUS window's forecast, which is STRIDE bars
    # out of date. Window 0 has no predecessor, so it is filled with NaN and
    # falls out of the shared mask -- never filled with zero, which would be a
    # different model's prediction wearing this one's name.
    pred_stale = np.full_like(pred_timesfm, np.nan)
    pred_stale[1:] = pred_timesfm[:-1]

    candidates = {
        "timesfm": pred_timesfm,
        "constant_zero": pred_zero,
        "frozen": pred_frozen,
        "timesfm_raw_contexts": pred_timesfm_raw,
        "timesfm_stale": pred_stale,
    }
    # ONE SHARED MASK over the target, the frame label and EVERY prediction --
    # including the stale control, so a model is never scored on rows another
    # model was excluded from.
    mask = np.isfinite(y_grid) & np.isfinite(y_frame)
    for pred in candidates.values():
        mask &= np.isfinite(pred)
    n_scorable = int(mask.sum())
    if n_scorable < 2:
        raise ScoringError(
            f"timesfm_score_grid: {block} has {n_scorable} rows on which every "
            "model is finite -- an empty comparison is not a comparison"
        )

    y = y_grid[mask]
    y_label = y_frame[mask]
    rng = np.random.default_rng(PERMUTATION_SEED)
    y_permuted = rng.permutation(y)

    models: dict[str, Any] = {}
    for name, pred in candidates.items():
        p = np.ascontiguousarray(pred[mask], dtype=np.float64)
        metrics = dict(forecast_metrics(p, y, train_mean=float(train_target_mean)))
        if int(metrics["n_scorable"]) != n_scorable:
            raise ScoringError(
                f"timesfm_score_grid: {block} model {name!r} scored "
                f"{metrics['n_scorable']} rows, not the shared {n_scorable} -- "
                "the models are not on identical rows"
            )
        entry: dict[str, Any] = {"vs_grid_return": metrics}
        entry["vs_frame_label"] = dict(
            forecast_metrics(p, y_label, train_mean=float(train_target_mean))
        )
        entry.update(_bootstrap(p, y, seed=BOOTSTRAP_SEED, n=N_BOOTSTRAP))
        entry["pred_std"] = float(p.std())
        entry["pred_mean"] = float(p.mean())
        models[name] = entry

    # The permuted-target null, run on the headline prediction. Reported as its
    # own row rather than folded into the table, because it is a property of
    # the STATISTIC, not of a model.
    models["timesfm_vs_permuted_y"] = {
        "vs_grid_return": dict(
            forecast_metrics(
                np.ascontiguousarray(pred_timesfm[mask], dtype=np.float64),
                y_permuted,
                train_mean=float(train_target_mean),
            )
        )
    }

    # PRICE-SPACE MEAN ABSOLUTE ERROR, over the whole horizon and at its last
    # step, against holding the last value. This is the README's own synthetic
    # table repeated on real data: there, TimesFM beat persistence by ~260x on
    # a clean sine and by 7% on a random walk.
    horizon_truth = np.stack([mid[anchors + h + 1] for h in range(HORIZON)], axis=1)[
        mask
    ]
    persistence = np.repeat(anchor_mid[mask][:, None], HORIZON, axis=1)
    mae = {
        "timesfm_whole_horizon": float(
            np.abs(point_shifted[mask] - horizon_truth).mean()
        ),
        "hold_last_value_whole_horizon": float(
            np.abs(persistence - horizon_truth).mean()
        ),
        "timesfm_terminal": float(
            np.abs(point_shifted[mask][:, HORIZON - 1] - horizon_truth[:, -1]).mean()
        ),
        "hold_last_value_terminal": float(
            np.abs(anchor_mid[mask] - horizon_truth[:, -1]).mean()
        ),
        "tick_size": float(TICK_SIZE),
    }
    mae["timesfm_over_hold_last_value_whole_horizon"] = (
        mae["timesfm_whole_horizon"] / mae["hold_last_value_whole_horizon"]
    )
    mae["timesfm_over_hold_last_value_terminal"] = (
        mae["timesfm_terminal"] / mae["hold_last_value_terminal"]
    )

    # HOW FAR THE FORECAST TRAVELS FROM THE LAST OBSERVATION -- the README's
    # 0.23-against-a-sigma-of-1.0 diagnostic, which is how a collapse to
    # persistence shows up as a number instead of as an impression.
    departure = np.abs(point_shifted[mask][:, HORIZON - 1] - anchor_mid[mask])
    realised_move = realised_terminal_mid[mask] - anchor_mid[mask]
    persistence_diag = {
        "mean_abs_departure_from_last_value": float(departure.mean()),
        "realised_terminal_move_std": float(realised_move.std()),
        "departure_over_realised_std": float(departure.mean() / realised_move.std()),
        "fraction_within_half_a_tick": float((departure < TICK_SIZE / 2.0).mean()),
        "fraction_exactly_zero": float((departure == 0.0).mean()),
        "forecast_sign_agrees_with_realised": float(
            (np.sign(pred_timesfm[mask]) == np.sign(realised_move)).mean()
        ),
    }

    quantile_findings = _quantile_findings(
        quantiles=np.asarray(shifted["quantiles"], dtype=np.float64)[mask],
        anchor_mid=anchor_mid[mask],
        realised_terminal_mid=realised_terminal_mid[mask],
        y=y,
        trailing_vol=_trailing_vol(mid, anchors)[mask],
    )

    summary = {
        "block": block,
        "n_windows": int(anchors.shape[0]),
        "n_scorable": n_scorable,
        "grid_target_vs_frame_label_pearson": float(np.corrcoef(y, y_label)[0, 1]),
        "grid_target_vs_frame_label_spearman": _rank_ic(y, y_label),
        "grid_target_std": float(y.std()),
        "frame_label_std": float(y_label.std()),
        "float32_arm_max_abs_return_diff": float(
            np.abs(pred_timesfm[mask] - pred_timesfm_raw[mask]).max()
        ),
        "float32_arm_mean_abs_return_diff": float(
            np.abs(pred_timesfm[mask] - pred_timesfm_raw[mask]).mean()
        ),
        "models": models,
        "mae_price_space": mae,
        "persistence_diagnostic": persistence_diag,
        "quantile_findings": quantile_findings,
    }
    head = models["timesfm"]["vs_grid_return"]
    ci = models["timesfm"]["r2_vs_zero_ci95"]
    print(
        f"  {block}: n={n_scorable:>5,}  timesfm r2_vs_zero="
        f"{head['r2_vs_zero']:+.6f} ci95={ci}  IC={head['rank_ic_all']:+.5f}  "
        f"MAE/persistence={mae['timesfm_over_hold_last_value_terminal']:.4f}"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Score zero-shot TimesFM, constant zero and the frozen Stage-1 "
            "winner on identical 1-second grid rows."
        )
    )
    parser.add_argument("--counters-only", action="store_true")
    parser.add_argument("--out", default=None)
    parser.add_argument("--export-root", default=str(DEFAULT_EXPORT_ROOT))
    parser.add_argument("--shifted-root", default=None)
    parser.add_argument("--raw-root", default=None)
    parser.add_argument("--blocks", default=None)
    args = parser.parse_args(argv)

    registry_root = LAKE_REGISTRY_ROOT
    tracking_root = str(mlflow_tracking_root(None))
    export_root = Path(args.export_root).resolve()
    shifted_root = Path(args.shifted_root or export_root / "fc-shifted").resolve()
    raw_root = Path(args.raw_root or export_root / "fc-raw").resolve()
    evidence_dir = repo_root() / EVIDENCE_DIR
    out_path = (
        Path(args.out) if args.out else evidence_dir / "07-e1-timesfm-scores.json"
    )

    print(f"export_root     {export_root}")
    print(f"shifted_root    {shifted_root}")
    print(f"raw_root        {raw_root}")
    print(f"out             {out_path}")

    before = _counters(registry_root, tracking_root)
    _print_counters(before, "BEFORE")
    if args.counters_only:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(before, sort_keys=True, indent=2) + "\n")
        print(f"OK: wrote {out_path}")
        return 0

    manifest_path = evidence_dir / "07-e1-timesfm-export.json"
    export_manifest = json.loads(manifest_path.read_text())
    if int(export_manifest["horizon_bars"]) != HORIZON:
        raise ScoringError(
            f"timesfm_score_grid: the export used a "
            f"{export_manifest['horizon_bars']}-bar horizon, this scorer "
            f"assumes {HORIZON}"
        )
    if int(export_manifest["stride_bars"]) != STRIDE:
        raise ScoringError(
            f"timesfm_score_grid: the export used stride "
            f"{export_manifest['stride_bars']}, this scorer assumes {STRIDE}"
        )
    if STRIDE <= HORIZON:
        raise ScoringError(
            f"timesfm_score_grid: stride {STRIDE} does not exceed horizon "
            f"{HORIZON}, so consecutive targets OVERLAP and the bootstrap "
            "interval below would be too narrow -- refusing to report one"
        )
    train_target_mean = float(export_manifest["train_target_mean"])
    print(f"train_target_mean {train_target_mean!r}")

    blocks = (
        [token.strip() for token in str(args.blocks).split(",")]
        if args.blocks
        else [str(entry["segment_name"]) for entry in export_manifest["blocks"]]
    )

    results = [
        _score_block(
            block=block,
            export_root=export_root,
            shifted_root=shifted_root,
            raw_root=raw_root,
            train_target_mean=train_target_mean,
        )
        for block in blocks
    ]

    after = _counters(registry_root, tracking_root)
    _print_counters(after, "AFTER")
    if after["counts"] != before["counts"]:
        raise ScoringError(
            f"timesfm_score_grid: look counters changed -- before="
            f"{before['counts']} after={after['counts']}"
        )

    # THE HEADLINE, DERIVED RATHER THAN NARRATED: on how many blocks does
    # TimesFM's r2_vs_zero exceed the constant-zero reference's, and on how many
    # is its 95% interval entirely above zero? The second question is the one
    # that matters, and it is answered here so a reader cannot be handed a
    # positive point estimate without its interval.
    verdict = {
        "blocks_where_timesfm_r2_exceeds_zero_point_estimate": [
            str(r["block"])
            for r in results
            if r["models"]["timesfm"]["vs_grid_return"]["r2_vs_zero"] > 0.0
        ],
        "blocks_where_timesfm_r2_ci95_entirely_above_zero": [
            str(r["block"])
            for r in results
            if (r["models"]["timesfm"]["r2_vs_zero_ci95"] or [-1.0, -1.0])[0] > 0.0
        ],
        "blocks_where_frozen_r2_ci95_entirely_above_zero": [
            str(r["block"])
            for r in results
            if (r["models"]["frozen"]["r2_vs_zero_ci95"] or [-1.0, -1.0])[0] > 0.0
        ],
        "n_blocks": len(results),
    }
    verdict["timesfm_beats_constant_zero"] = bool(
        len(verdict["blocks_where_timesfm_r2_ci95_entirely_above_zero"]) == len(results)
    )

    body = {
        "generated_by": "mvp/scripts/timesfm_score_grid.py",
        "experiment": "E1 zero-shot TimesFM 3.0 as a baseline to beat",
        "comparability_note": (
            "Every model here is scored on the SAME 1-second grid rows through "
            "one shared finite mask, with n_scorable asserted equal across "
            "them. The frozen predictor's published r2_vs_zero of 0.006-0.035 "
            "was measured on EVENT-DRIVEN decision rows and must NOT be quoted "
            "beside these numbers: a 1-second grid is a different row "
            "population with a different denominator. The frozen column here "
            "is the frozen body re-scored on THIS population."
        ),
        "in_sample_disclosure": (
            "These five blocks chose the frozen winner (selection in-sample) "
            "and the frozen body was fitted on the whole train cache, which "
            "contains all five (fit in-sample). TimesFM saw none of them in "
            "this project, but its pretraining corpus is unknown and cannot be "
            "audited -- which is exactly why it is a baseline to beat and not a "
            "catalogued feature."
        ),
        "horizon_bars": HORIZON,
        "stride_bars": STRIDE,
        "quantile_levels": list(QUANTILE_LEVELS),
        "n_bootstrap": N_BOOTSTRAP,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "permutation_seed": PERMUTATION_SEED,
        "vol_lookback_bars": VOL_LOOKBACK,
        "train_target_mean": train_target_mean,
        "export_manifest": str(manifest_path),
        "verdict": verdict,
        "look_counters_before": before,
        "look_counters_after": after,
        "blocks": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(body, sort_keys=True, indent=2, default=float) + "\n"
    )
    print(f"OK: wrote {out_path}")
    print(
        f"VERDICT timesfm_beats_constant_zero = {verdict['timesfm_beats_constant_zero']}"
    )
    print(
        "  CI95 above zero on: "
        f"{verdict['blocks_where_timesfm_r2_ci95_entirely_above_zero'] or 'no block'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
