"""Measure whether the extra channels carry information about Bitcoin's NEXT ten
seconds at all, using ordinary least squares and no foundation model.

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.e2_channel_information

WHY THIS IS THE LOAD-BEARING HALF OF E2. If TimesFM gains nothing from an
Ethereum channel, there are two completely different explanations and they lead
to opposite conclusions:

    (a) the channel carries no usable information about the Bitcoin 10-second
        return, in which case the multi-stream IDEA fails here and no model
        could have rescued it; or
    (b) the information is there and TimesFM did not extract it, in which case
        the idea survives and the model is the wrong instrument.

A foundation model cannot distinguish those. Least squares can: it is the
simplest estimator that would find a linear lead-lag relation if one exists, it
has no hyperparameters to tune away a negative, and its marginal R-squared from
ADDING a channel is exactly the quantity in question.

LEAVE-ONE-BLOCK-OUT, NOT IN SAMPLE. With 18,000 rows and five regressors an
in-sample R-squared is guaranteed positive and means nothing. Each block is
predicted by a fit on the OTHER FOUR, so a reported improvement is an
improvement on rows the coefficients never saw. The in-sample number is reported
beside it only to show the gap.

SAME ROWS AS THE ARMS. The anchors are read from the `none` arm's own forecast
file rather than recomputed, so this audit and the model comparison stand on one
population.

THE CHANNELS. `imb_top_norm` is the single feature the frozen predictor uses.
The Ethereum channels are trailing returns over 1, 5 and 10 seconds -- a
RETURN and not a level, because a level regressed on a level would fit the
shared trend rather than the lead-lag. A trailing Bitcoin return is included as
the momentum control: without it, any Ethereum coefficient could simply be
re-reading Bitcoin's own recent move through a correlated instrument.

ZERO LOOKS. Reads `work/e1/*.npz`, `work/e2/eth_block_*.npz` and the `none`
arm's anchors. No lake, no accessor, no `val`.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/nbc")

import numpy as np  # noqa: E402

from scripts.timesfm_score_grid import _rank_ic  # noqa: E402

DEFAULT_TIMESFM_ROOT = Path("/Volumes/ProjectsSSD/aihedgefund/timesfm")

HORIZON: int = 10
BLOCKS: tuple[int, ...] = (0, 1, 2, 3, 4)

#: Trailing windows, in bars (= seconds on this grid), for the return channels.
LAGS: tuple[int, ...] = (1, 5, 10)

#: The regressor sets compared. Each is a tuple of channel names; the marginal
#: value of a channel is the difference between a set and the set without it.
DESIGNS: dict[str, tuple[str, ...]] = {
    "imb": ("imb",),
    "btc_mom": ("btc_ret_10",),
    "imb+btc_mom": ("imb", "btc_ret_10"),
    # SYMMETRIC CONTROL. Ethereum is offered three lags, so Bitcoin's own tape
    # must be offered the same three before "Ethereum adds nothing" is a fair
    # statement -- otherwise the Ethereum channels could be winning only the
    # freedom that the Bitcoin control was denied.
    "imb+btc_all": ("imb", "btc_ret_1", "btc_ret_5", "btc_ret_10"),
    "imb+btc_all+eth": (
        "imb",
        "btc_ret_1",
        "btc_ret_5",
        "btc_ret_10",
        "eth_ret_1",
        "eth_ret_5",
        "eth_ret_10",
    ),
    "eth_only": ("eth_ret_1", "eth_ret_5", "eth_ret_10"),
    "imb+eth": ("imb", "eth_ret_1", "eth_ret_5", "eth_ret_10"),
    "imb+btc_mom+eth": (
        "imb",
        "btc_ret_10",
        "eth_ret_1",
        "eth_ret_5",
        "eth_ret_10",
    ),
}


def _channels(
    mid: np.ndarray, eth: np.ndarray, imb: np.ndarray, anchors: np.ndarray
) -> dict[str, np.ndarray]:
    """One column per channel, all observable strictly at or before the anchor."""
    out: dict[str, np.ndarray] = {"imb": imb[anchors]}
    for lag in LAGS:
        out[f"btc_ret_{lag}"] = mid[anchors] / mid[anchors - lag] - 1.0
        out[f"eth_ret_{lag}"] = eth[anchors] / eth[anchors - lag] - 1.0
    return out


def _fit_predict(
    x_fit: np.ndarray, y_fit: np.ndarray, x_apply: np.ndarray
) -> np.ndarray:
    """Least squares with an intercept, applied to held-out rows."""
    a_fit = np.column_stack([np.ones(x_fit.shape[0]), x_fit])
    beta, *_ = np.linalg.lstsq(a_fit, y_fit, rcond=None)
    a_apply = np.column_stack([np.ones(x_apply.shape[0]), x_apply])
    return a_apply @ beta


def _r2_vs_zero(pred: np.ndarray, y: np.ndarray) -> float:
    ss = float((y**2).sum())
    return 1.0 - float(((y - pred) ** 2).sum()) / ss if ss else float("nan")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timesfm-root", type=Path, default=DEFAULT_TIMESFM_ROOT)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    work = args.timesfm_root / "work"

    per_block: dict[int, dict[str, np.ndarray]] = {}
    targets: dict[int, np.ndarray] = {}
    for block in BLOCKS:
        with np.load(work / "e1" / f"oof_block_{block}.npz") as data:
            mid = np.asarray(data["mid"], dtype=np.float64)
            imb = np.asarray(data["imb_top_norm"], dtype=np.float64)
        with np.load(work / "e2" / f"eth_block_{block}.npz") as data:
            eth = np.asarray(data["eth_price"], dtype=np.float64)
        arm = work / "e2" / "fc-none-e2" / f"oof_block_{block}.npz"
        if not arm.is_file():
            raise SystemExit(
                f"e2_channel_information: no baseline arm at {arm} -- run "
                "tools/timesfm_forecast_mv.py --arm none --anchors e2 first"
            )
        with np.load(arm) as data:
            anchors = np.asarray(data["anchors"], dtype=np.int64)
        channels = _channels(mid, eth, imb, anchors)
        for name, column in channels.items():
            if not np.all(np.isfinite(column)):
                raise SystemExit(
                    f"e2_channel_information: channel {name} on block {block} is "
                    "not finite on the arm's own anchors"
                )
        per_block[block] = channels
        targets[block] = mid[anchors + HORIZON] / mid[anchors] - 1.0

    report: dict[str, Any] = {
        "experiment": "E2 -- is the information in the extra channels at all?",
        "generated_by": "mvp/scripts/e2_channel_information.py",
        "horizon_bars": HORIZON,
        "lags_bars": list(LAGS),
        "designs": {k: list(v) for k, v in DESIGNS.items()},
        "single_channel_rank_ic": {},
        "designs_leave_one_block_out": {},
        "designs_in_sample": {},
        "blocks": {b: int(targets[b].shape[0]) for b in BLOCKS},
    }

    names = sorted(per_block[BLOCKS[0]])
    y_all = np.concatenate([targets[b] for b in BLOCKS])
    for name in names:
        col = np.concatenate([per_block[b][name] for b in BLOCKS])
        report["single_channel_rank_ic"][name] = {
            "pooled_rank_ic": _rank_ic(col, y_all),
            "pooled_pearson": float(np.corrcoef(col, y_all)[0, 1]),
            "per_block_rank_ic": {
                str(b): _rank_ic(per_block[b][name], targets[b]) for b in BLOCKS
            },
        }

    for design, cols in DESIGNS.items():
        oos_pred: list[np.ndarray] = []
        oos_y: list[np.ndarray] = []
        per_block_r2: dict[str, float] = {}
        for held in BLOCKS:
            fit_blocks = [b for b in BLOCKS if b != held]
            x_fit = np.column_stack(
                [np.concatenate([per_block[b][c] for b in fit_blocks]) for c in cols]
            )
            y_fit = np.concatenate([targets[b] for b in fit_blocks])
            x_apply = np.column_stack([per_block[held][c] for c in cols])
            pred = _fit_predict(x_fit, y_fit, x_apply)
            per_block_r2[str(held)] = _r2_vs_zero(pred, targets[held])
            oos_pred.append(pred)
            oos_y.append(targets[held])
        pooled_pred = np.concatenate(oos_pred)
        pooled_y = np.concatenate(oos_y)
        report["designs_leave_one_block_out"][design] = {
            "pooled_r2_vs_zero": _r2_vs_zero(pooled_pred, pooled_y),
            "pooled_rank_ic": _rank_ic(pooled_pred, pooled_y),
            "per_block_r2_vs_zero": per_block_r2,
        }
        x_all = np.column_stack(
            [np.concatenate([per_block[b][c] for b in BLOCKS]) for c in cols]
        )
        in_pred = _fit_predict(x_all, y_all, x_all)
        report["designs_in_sample"][design] = {
            "pooled_r2_vs_zero": _r2_vs_zero(in_pred, y_all),
            "pooled_rank_ic": _rank_ic(in_pred, y_all),
        }
        print(
            f"  {design:20s} oos r2={report['designs_leave_one_block_out'][design]['pooled_r2_vs_zero']:+.6f} "
            f"ic={report['designs_leave_one_block_out'][design]['pooled_rank_ic']:+.4f}   "
            f"(in-sample r2={report['designs_in_sample'][design]['pooled_r2_vs_zero']:+.6f})",
            flush=True,
        )

    lobo = report["designs_leave_one_block_out"]
    report["marginal_value_out_of_sample"] = {
        "eth_added_to_imb": lobo["imb+eth"]["pooled_r2_vs_zero"]
        - lobo["imb"]["pooled_r2_vs_zero"],
        "eth_added_to_imb_plus_btc_momentum": lobo["imb+btc_mom+eth"][
            "pooled_r2_vs_zero"
        ]
        - lobo["imb+btc_mom"]["pooled_r2_vs_zero"],
        "btc_momentum_added_to_imb": lobo["imb+btc_mom"]["pooled_r2_vs_zero"]
        - lobo["imb"]["pooled_r2_vs_zero"],
        "eth_added_to_imb_plus_all_btc_lags": lobo["imb+btc_all+eth"][
            "pooled_r2_vs_zero"
        ]
        - lobo["imb+btc_all"]["pooled_r2_vs_zero"],
        "all_btc_lags_added_to_imb": lobo["imb+btc_all"]["pooled_r2_vs_zero"]
        - lobo["imb"]["pooled_r2_vs_zero"],
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    print("marginal:", json.dumps(report["marginal_value_out_of_sample"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
