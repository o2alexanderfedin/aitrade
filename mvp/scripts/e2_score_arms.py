"""Answer whether extra correlated channels helped TimesFM 3.0 forecast the
Bitcoin midprice, by comparing every arm on exactly the same windows.

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.e2_score_arms

WHAT THIS DECIDES. E1 established that a single-channel TimesFM 3.0 loses to
forecasting a constant zero. E2 asks the question E1 could not: the model has a
cross-variate attention block, so does handing it several correlated streams at
once change the answer? The arms are `none` (E1's configuration), `imb`
(normalised top-of-book imbalance), `eth` (the prevailing Ethereum trade price)
and `imb_eth` (both). Each has a permutation null in which its covariate windows
were shuffled across anchors.

THE COMPARISON IS PAIRED, WHICH IS THE WHOLE POINT. Two absolute r-squared
values measured on two window populations cannot be subtracted. Every arm here
forecast the SAME anchors -- asserted, not assumed -- so the statistic is a
per-window difference in squared error, and its interval comes from resampling
those differences. That is far more sensitive than comparing two noisy
absolutes, and it is the only form in which a small real effect is detectable at
all.

THE INTERVAL IS A MOVING-BLOCK BOOTSTRAP, not an independent one. Anchors are
`STRIDE = 20` bars apart and the horizon is 10, so windows do not mechanically
overlap -- but volatility clusters, and neighbouring windows share a regime. An
independent bootstrap would therefore report an interval narrower than the truth
and could manufacture a significant improvement out of one quiet hour.
Contiguous blocks of `BOOTSTRAP_BLOCK` windows are resampled instead.

REUSED, NOT RESTATED: `_rank_ic` and `_r2_decomposition` are imported from
`scripts.timesfm_score_grid`, so every number here is computed by the same code
that produced E1's published numbers. A second implementation that happened to
differ in tie handling would look like a finding.

THE FROZEN PREDICTOR'S COLUMN IS A VIABILITY CHECK, NOT A RESULT. It is scored
on these rows so a reader can see the scale of a signal that does work, but it
is IN SAMPLE twice over -- these are the blocks its grid was selected on -- and
it sits on a time-weighted 1-second population rather than the event-weighted
decision rows its published range was measured on. The two ranges are not
comparable and this script says so in its own output.

ZERO LOOKS. Reads `work/e1/*.npz`, `work/e2/eth_block_*.npz` and the arms'
forecast `.npz`. No lake partition is opened, no accessor is imported, `val` is
never named.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/nbc")

import numpy as np  # noqa: E402

from scripts.timesfm_score_grid import (  # noqa: E402
    _r2_decomposition,
    _rank_ic,
)

#: Where the TimesFM tree lives.
DEFAULT_TIMESFM_ROOT = Path("/Volumes/ProjectsSSD/aihedgefund/timesfm")

#: Bars of horizon; must match the exporter and the runner.
HORIZON: int = 10

#: Blocks E1 exported.
BLOCKS: tuple[int, ...] = (0, 1, 2, 3, 4)

#: Arm tag -> directory suffix under `work/e2/`. `none` is the paired baseline
#: every other arm is differenced against.
ARM_TAGS: tuple[str, ...] = (
    "none",
    "imb",
    "eth",
    "imb_eth",
    "btc_dup",
    "imb-perm",
    "eth-perm",
    "imb_eth-perm",
    "btc_dup-perm",
)

#: Arms that carry covariates, each paired with its permutation null.
COVARIATE_ARMS: tuple[str, ...] = ("imb", "eth", "imb_eth", "btc_dup")

#: Windows per resampled block. 50 windows span 1000 grid seconds, long enough
#: to carry a volatility regime and short enough to leave many blocks per day.
BOOTSTRAP_BLOCK: int = 50

#: Bootstrap draws and seed. Same seed as E1's scorer, so the two reports'
#: intervals are drawn from the same stream.
N_BOOTSTRAP: int = 2000
BOOTSTRAP_SEED: int = 20260930


class ScoringError(RuntimeError):
    """A scoring precondition failed."""


def _moving_block_indices(
    rng: np.random.Generator, size: int, *, block: int
) -> np.ndarray:
    """Indices of one moving-block resample covering `size` windows."""
    if size <= block:
        return rng.integers(0, size, size=size)
    n_blocks = int(np.ceil(size / block))
    starts = rng.integers(0, size - block + 1, size=n_blocks)
    offsets = np.arange(block, dtype=np.int64)
    return (starts[:, None] + offsets[None, :]).reshape(-1)[:size]


def _paired_interval(
    arm: np.ndarray, base: np.ndarray, y: np.ndarray, *, seed: int
) -> dict[str, Any]:
    """Interval for the r-squared DIFFERENCE and the rank-IC difference.

    Both statistics are recomputed inside each resample rather than differenced
    afterwards, because r-squared has the resample's own target sum of squares in
    its denominator.
    """
    rng = np.random.default_rng(seed)
    size = y.shape[0]
    d_r2 = np.empty(N_BOOTSTRAP, dtype=np.float64)
    d_ic = np.empty(N_BOOTSTRAP, dtype=np.float64)
    for i in range(N_BOOTSTRAP):
        idx = _moving_block_indices(rng, size, block=BOOTSTRAP_BLOCK)
        t = y[idx]
        ss = float((t**2).sum())
        if ss == 0.0:
            d_r2[i] = np.nan
        else:
            a = float(((t - arm[idx]) ** 2).sum())
            b = float(((t - base[idx]) ** 2).sum())
            d_r2[i] = (b - a) / ss
        d_ic[i] = _rank_ic(arm[idx], t) - _rank_ic(base[idx], t)
    out: dict[str, Any] = {}
    for key, draws in (("delta_r2_vs_zero", d_r2), ("delta_rank_ic", d_ic)):
        finite = draws[np.isfinite(draws)]
        out[f"{key}_ci95"] = (
            [float(np.percentile(finite, 2.5)), float(np.percentile(finite, 97.5))]
            if finite.size >= N_BOOTSTRAP // 2
            else None
        )
        out[f"{key}_fraction_above_zero"] = (
            float(np.mean(finite > 0.0)) if finite.size else None
        )
    return out


def _metrics(pred: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    ss_zero = float((y**2).sum())
    if ss_zero == 0.0:
        raise ScoringError("e2_score_arms: the target has zero variance")
    moved = y != 0.0
    entry: dict[str, Any] = {
        "n": int(y.shape[0]),
        "r2_vs_zero": 1.0 - float(((y - pred) ** 2).sum()) / ss_zero,
        "rank_ic": _rank_ic(pred, y),
        "mae": float(np.mean(np.abs(y - pred))),
        "mae_ratio_vs_zero": (
            float(np.mean(np.abs(y - pred)) / np.mean(np.abs(y)))
            if float(np.mean(np.abs(y))) > 0.0
            else None
        ),
        "pred_std": float(pred.std()),
        "target_std": float(y.std()),
        "sign_agreement_on_moved_rows": (
            float(np.mean(np.sign(pred[moved]) == np.sign(y[moved])))
            if moved.any()
            else None
        ),
        "moved_rows": int(moved.sum()),
    }
    entry.update(_r2_decomposition(pred, y))
    # The correlation this forecast would need, at its own amplitude, merely to
    # draw with predicting zero: `rho > (sigma_p / sigma_y) / 2`.
    entry["breakeven_pearson"] = 0.5 * entry["pred_std_over_target_std"]
    entry["pearson_minus_breakeven"] = (
        entry["pearson_pred_vs_target"] - entry["breakeven_pearson"]
    )
    return entry


def _least_squares_scale(pred: np.ndarray, y: np.ndarray) -> float:
    """The single number that minimises squared error for `scale * pred`.

    `argmin_s sum((y - s*p)^2) = sum(p*y) / sum(p*p)`. Nothing is fitted but the
    amplitude: the ORDERING of the predictions is untouched, so rank IC is
    unchanged by construction and only the r-squared can move.
    """
    denominator = float((pred**2).sum())
    return float((pred * y).sum()) / denominator if denominator > 0.0 else 0.0


def _shrunk_out_of_block(
    per_block_pred: list[np.ndarray], per_block_y: list[np.ndarray]
) -> dict[str, Any]:
    """Each block rescaled by an amplitude fitted on the OTHER four.

    WHY THIS ARM EXISTS. Expanding r-squared about the constant-zero predictor
    gives `2*rho*(sigma_p/sigma_y) - (sigma_p/sigma_y)^2`, so a forecast only
    beats zero once `rho` exceeds HALF its relative amplitude. A model whose
    forecasts are correctly ORDERED but too large is therefore charged for
    confidence it has not earned, and loses to zero while carrying real signal.
    Rescaling separates those two failures: what survives is the information,
    what disappears was calibration.

    The scale is fitted OUT OF BLOCK because an in-sample scale is worth exactly
    `rho^2` by construction and would prove nothing.
    """
    blocks = len(per_block_pred)
    scales: dict[str, float] = {}
    scaled: list[np.ndarray] = []
    for held in range(blocks):
        fit_pred = np.concatenate(
            [per_block_pred[b] for b in range(blocks) if b != held]
        )
        fit_y = np.concatenate([per_block_y[b] for b in range(blocks) if b != held])
        scale = _least_squares_scale(fit_pred, fit_y)
        scales[str(held)] = scale
        scaled.append(scale * per_block_pred[held])
    pooled_pred = np.concatenate(scaled)
    pooled_y = np.concatenate(per_block_y)
    entry = _metrics(pooled_pred, pooled_y)
    entry["scale_per_held_out_block"] = scales
    return entry


def _load_arm(work: Path, tag: str, block: int) -> dict[str, np.ndarray]:
    path = work / "e2" / f"fc-{tag}-e2" / f"oof_block_{block}.npz"
    if not path.is_file():
        raise ScoringError(f"e2_score_arms: no forecast at {path}")
    with np.load(path) as data:
        return {
            "anchors": np.asarray(data["anchors"], dtype=np.int64),
            "point": np.asarray(data["point"], dtype=np.float64),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timesfm-root", type=Path, default=DEFAULT_TIMESFM_ROOT)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    work = args.timesfm_root / "work"

    report: dict[str, Any] = {
        "experiment": "E2 -- multivariate TimesFM 3.0 on the 10 s Bitcoin mid",
        "generated_by": "mvp/scripts/e2_score_arms.py",
        "arms": list(ARM_TAGS),
        "baseline_arm": "none",
        "bootstrap_block_windows": BOOTSTRAP_BLOCK,
        "n_bootstrap": N_BOOTSTRAP,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "horizon_bars": HORIZON,
        "comparability_note": (
            "Every arm forecast the same anchors, asserted per block. The frozen "
            "predictor's column is IN SAMPLE and sits on a time-weighted "
            "1-second population, not the event-weighted decision rows its "
            "published 0.006-0.035 range was measured on; the two must not be "
            "quoted side by side."
        ),
        "blocks": {},
        "pooled": {},
    }

    pooled: dict[str, list[np.ndarray]] = {tag: [] for tag in ARM_TAGS}
    pooled["frozen"] = []
    pooled_y: list[np.ndarray] = []

    for block in BLOCKS:
        with np.load(work / "e1" / f"oof_block_{block}.npz") as data:
            mid = np.asarray(data["mid"], dtype=np.float64)
            frozen = np.asarray(data["pred_frozen"], dtype=np.float64)

        arms = {tag: _load_arm(work, tag, block) for tag in ARM_TAGS}
        anchors = arms["none"]["anchors"]
        for tag, payload in arms.items():
            if not np.array_equal(payload["anchors"], anchors):
                raise ScoringError(
                    f"e2_score_arms: block {block} arm {tag} forecast "
                    f"{payload['anchors'].shape[0]} anchors, the baseline "
                    f"{anchors.shape[0]} -- these arms are not comparable"
                )
        anchor_mid = mid[anchors]
        y = mid[anchors + HORIZON] / anchor_mid - 1.0

        entry: dict[str, Any] = {"windows": int(anchors.shape[0]), "arms": {}}
        base_pred = arms["none"]["point"][:, HORIZON - 1] / anchor_mid - 1.0
        for tag in ARM_TAGS:
            pred = arms[tag]["point"][:, HORIZON - 1] / anchor_mid - 1.0
            row = _metrics(pred, y)
            if tag != "none":
                row["delta_r2_vs_zero"] = row["r2_vs_zero"] - float(
                    1.0 - ((y - base_pred) ** 2).sum() / (y**2).sum()
                )
                row["delta_rank_ic"] = row["rank_ic"] - _rank_ic(base_pred, y)
                row["wins_vs_baseline_fraction"] = float(
                    np.mean((y - pred) ** 2 < (y - base_pred) ** 2)
                )
                row.update(
                    _paired_interval(pred, base_pred, y, seed=BOOTSTRAP_SEED + block)
                )
            entry["arms"][tag] = row
            pooled[tag].append(pred)
        entry["frozen_in_sample"] = _metrics(frozen[anchors], y)
        pooled["frozen"].append(frozen[anchors])
        pooled_y.append(y)
        report["blocks"][str(block)] = entry
        best = max(COVARIATE_ARMS, key=lambda t: entry["arms"][t]["rank_ic"])
        print(
            f"  block {block}  n={entry['windows']:>5}  "
            f"none r2={entry['arms']['none']['r2_vs_zero']:+.6f} "
            f"ic={entry['arms']['none']['rank_ic']:+.4f}  |  best {best} "
            f"ic={entry['arms'][best]['rank_ic']:+.4f}  |  frozen "
            f"ic={entry['frozen_in_sample']['rank_ic']:+.4f}",
            flush=True,
        )

    y_all = np.concatenate(pooled_y)
    base_all = np.concatenate(pooled["none"])
    for tag in (*ARM_TAGS, "frozen"):
        pred_all = np.concatenate(pooled[tag])
        row = _metrics(pred_all, y_all)
        if tag not in ("none", "frozen"):
            row["delta_r2_vs_zero"] = row["r2_vs_zero"] - float(
                1.0 - ((y_all - base_all) ** 2).sum() / (y_all**2).sum()
            )
            row["delta_rank_ic"] = row["rank_ic"] - _rank_ic(base_all, y_all)
            row["wins_vs_baseline_fraction"] = float(
                np.mean((y_all - pred_all) ** 2 < (y_all - base_all) ** 2)
            )
            row.update(_paired_interval(pred_all, base_all, y_all, seed=BOOTSTRAP_SEED))
        if tag != "frozen":
            row["shrunk_out_of_block"] = _shrunk_out_of_block(pooled[tag], pooled_y)
        report["pooled"][tag] = row

    # The verdict is derived from the numbers, not written beside them. An arm
    # only counts as an improvement over UNIVARIATE TimesFM when its paired
    # interval clears zero AND the gain survives its own misaligned null. None
    # of that makes it beat forecasting a constant zero, which is reported
    # separately and on purpose.
    verdicts: dict[str, Any] = {}
    for tag in COVARIATE_ARMS:
        row = report["pooled"][tag]
        null = report["pooled"][f"{tag}-perm"]
        ci_r2 = row.get("delta_r2_vs_zero_ci95")
        ci_ic = row.get("delta_rank_ic_ci95")
        clears_baseline = bool(
            ci_r2 is not None
            and ci_ic is not None
            and ci_r2[0] > 0.0
            and ci_ic[0] > 0.0
        )
        # The NET gain: how much survives once the same channel, misaligned in
        # time, is given the same chance. A null that also improves means the
        # model gained from having a second structured channel at all, not from
        # anything the channel says.
        net_r2 = row["delta_r2_vs_zero"] - null["delta_r2_vs_zero"]
        net_ic = row["delta_rank_ic"] - null["delta_rank_ic"]
        verdicts[tag] = {
            "delta_r2_vs_univariate": row["delta_r2_vs_zero"],
            "delta_rank_ic_vs_univariate": row["delta_rank_ic"],
            "null_delta_r2_vs_univariate": null["delta_r2_vs_zero"],
            "null_delta_rank_ic_vs_univariate": null["delta_rank_ic"],
            "net_delta_r2_over_null": net_r2,
            "net_delta_rank_ic_over_null": net_ic,
            "paired_interval_clears_univariate_baseline": clears_baseline,
            "still_loses_to_constant_zero": bool(row["r2_vs_zero"] < 0.0),
            "verdict": (
                "beats univariate TimesFM"
                if clears_baseline and net_ic > 0.0
                else (
                    "not separable from its own misaligned null"
                    if net_ic <= 0.0
                    else "ranking only"
                )
            ),
        }
    report["verdict"] = verdicts

    # THE HONEST REFERENCE ROW. The frozen predictor's column is in sample on
    # these blocks. `scripts/e2_channel_information.py` fits least squares on the
    # SAME channels over the SAME windows leave-one-block-out, so it is the
    # comparison that needs no caveat: same inputs, same rows, out of sample.
    info_path = (
        Path(__file__).resolve().parents[2]
        / ".planning"
        / "phases"
        / "07-regression-track-vertical-slice"
        / "evidence"
        / "07-e2-channel-information.json"
    )
    if info_path.is_file():
        info = json.loads(info_path.read_text(encoding="utf-8"))
        lobo = info["designs_leave_one_block_out"]
        report["reference_least_squares_out_of_sample"] = {
            "source": info_path.name,
            "note": (
                "Least squares on the same channels, the same windows, "
                "leave-one-block-out. The comparison that needs no caveat."
            ),
            "imb": lobo["imb"],
            "imb+btc_mom": lobo["imb+btc_mom"],
            "imb+eth": lobo["imb+eth"],
        }

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    print("verdict:", json.dumps(verdicts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
