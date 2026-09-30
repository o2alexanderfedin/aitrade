"""Answer one question before any multivariate experiment is worth running:
does a covariate channel handed to TimesFM 3.0 change the forecast at all, and
by more than the device's own run-to-run noise?

NEVER COLLECTED BY PYTEST -- run manually under the TimesFM interpreter:

    /Volumes/ProjectsSSD/aihedgefund/timesfm/.venv/bin/python \
        tools/timesfm_covariate_probe.py --windows 256

WHY THIS EXISTS AS ITS OWN FILE. Experiment E1 concluded that the foundation
model loses to predicting nothing on a ten-second midprice, and it did so with
ONE channel. The open question is the multivariate one: TimesFM 3.0 carries a
dedicated cross-variate attention block
(`timesfm3/torch/transformer.py`: "Variate attention: pre_ln ->
reshape(bn,v,d) -> MHA -> post_ln + residual"), so extra channels can in
principle inform the target. `E2` is the experiment that tests it. But an E2
that reports "covariates did not help" is WORTHLESS unless the covariate
reached the model, and five separate instrument failures in this phase were all
the same shape: a check that could not fail. So the covariate path gets a
POSITIVE CONTROL before a single byte of Ethereum data is downloaded.

THE THREE NUMBERS THIS PRODUCES, in the order they must be read.

1. `noise_floor` -- the same arm run TWICE with no covariate. Non-zero because
   the context is cast to float32 and reduced on the GPU in a
   non-deterministic order. Every difference below is meaningless unless it
   exceeds this.

2. `noise` -- an iid Gaussian covariate, which carries no information about the
   target. Its forecast must differ from arm 1 by MORE than `noise_floor`. If
   it does not, `predict_batch` is not forwarding the covariate and every
   negative multivariate result would be vacuous.

3. `leak` -- a covariate that IS the answer: `cov[t] = mid[t + HORIZON]`, so
   the covariate's last context position holds exactly the value the model is
   asked to forecast. Its r-squared must jump. If a PERFECT covariate cannot
   move the forecast, the model cannot exploit a covariate on this data at all,
   and that ceiling is worth knowing before spending effort on a real one.

`imb` is a fourth arm and not a control: the normalised top-of-book imbalance
is the ONE feature the frozen predictor's body actually uses (the other two
coefficients are exactly zero), so it is the most promising real covariate
available without leaving the cached export.

ZERO LOOKS. This reads only `work/e1/oof_block_*.npz`, which
`scripts/timesfm_export_grid.py` already wrote from the five ALREADY-CACHED OOF
blocks. It never touches the lake, never imports the mvp package, and `val` is
not nameable from here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from timesfm_forecast import (  # noqa: E402
    CONTEXT,
    HORIZON,
    DEFAULT_TIMESFM_ROOT,
    _load,
    _peak_rss_bytes,
    _prepare_environment,
)

#: Seed for the uninformative arm. Fixed so the arm is reproducible; its VALUE
#: is irrelevant by construction, which is the point of the arm.
NOISE_SEED = 20260930


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Rank correlation without scipy, which the TimesFM venv does not carry.

    Ties are averaged, so a constant arm gives a zero-variance rank vector and
    a NaN rather than a spuriously perfect score.
    """
    def _rank(x: np.ndarray) -> np.ndarray:
        order = np.argsort(x, kind="stable")
        ranks = np.empty(x.shape[0], dtype=np.float64)
        sorted_x = x[order]
        i = 0
        while i < x.shape[0]:
            j = i
            while j + 1 < x.shape[0] and sorted_x[j + 1] == sorted_x[i]:
                j += 1
            ranks[order[i : j + 1]] = 0.5 * (i + j) + 1.0
            i = j + 1
        return ranks

    ra, rb = _rank(a), _rank(b)
    sa, sb = ra.std(), rb.std()
    if sa == 0.0 or sb == 0.0:
        return float("nan")
    return float(np.mean((ra - ra.mean()) * (rb - rb.mean())) / (sa * sb))


def _r2_vs_zero(pred: np.ndarray, realised: np.ndarray) -> float:
    """1 - SSE(pred) / SSE(0). Negative means the constant zero forecast wins."""
    sse_zero = float(np.sum(realised**2))
    if sse_zero == 0.0:
        return float("nan")
    return 1.0 - float(np.sum((pred - realised) ** 2)) / sse_zero


def _build(
    mid: np.ndarray, imb: np.ndarray, anchors: np.ndarray, arm: str
) -> tuple[list[np.ndarray], list[np.ndarray] | None, np.ndarray]:
    """Contexts, covariates and the per-window level, for one arm.

    The level shift is E1's: the anchor's own mid is subtracted so float32
    spends its precision on the part that varies. The covariate is shifted the
    same way when it is a PRICE (`leak`) and left alone when it is not (`noise`,
    `imb`), because per-variate RevIN normalises each channel independently.
    """
    rng = np.random.default_rng(NOISE_SEED)
    contexts: list[np.ndarray] = []
    covariates: list[np.ndarray] = []
    levels = np.empty(anchors.shape[0], dtype=np.float64)
    for i, raw_k in enumerate(anchors):
        k = int(raw_k)
        lo, hi = k - CONTEXT + 1, k + 1
        window = mid[lo:hi]
        if window.shape[0] != CONTEXT or window[-1] != mid[k]:
            raise SystemExit(
                f"timesfm_covariate_probe: anchor {k} gives a misaligned "
                f"{window.shape[0]}-bar context"
            )
        level = float(mid[k])
        levels[i] = level
        contexts.append(np.ascontiguousarray(window - level, dtype=np.float64))
        if arm == "none":
            continue
        if arm == "noise":
            cov = rng.standard_normal(CONTEXT)
        elif arm == "imb":
            cov = imb[lo:hi]
        elif arm == "leak":
            # cov[t] = mid[t + HORIZON]: the last context position holds the
            # exact value the model is asked to forecast.
            cov = mid[lo + HORIZON : hi + HORIZON] - level
        else:
            raise SystemExit(f"timesfm_covariate_probe: unknown arm {arm!r}")
        cov = np.ascontiguousarray(cov, dtype=np.float64)
        if cov.shape[0] != CONTEXT:
            raise SystemExit(
                f"timesfm_covariate_probe: arm {arm} covariate for anchor {k} "
                f"has {cov.shape[0]} bars, not {CONTEXT}"
            )
        if not np.all(np.isfinite(cov)):
            # There is no per-channel covariate mask in `predict_batch`, and
            # `forward` turns NaN into 0.0 in RAW units -- a silent zero price.
            raise SystemExit(
                f"timesfm_covariate_probe: arm {arm} covariate for anchor {k} "
                "is not finite; the model would silently read it as zero"
            )
        covariates.append(cov)
    return contexts, (covariates or None), levels


def _run_arm(
    forecaster,  # noqa: ANN001 - the library type is private
    contexts: list[np.ndarray],
    covariates: list[np.ndarray] | None,
    levels: np.ndarray,
    *,
    chunk: int,
) -> np.ndarray:
    """Terminal-bar forecast in PRICE units, one row per window."""
    n = len(contexts)
    point = np.empty(n, dtype=np.float64)
    for start in range(0, n, chunk):
        stop = min(start + chunk, n)
        outputs = list(
            forecaster.predict_batch(
                contexts=contexts[start:stop],
                horizon=HORIZON,
                past_only_covariates=(
                    None if covariates is None else covariates[start:stop]
                ),
            )
        )
        if len(outputs) != stop - start:
            raise SystemExit(
                f"timesfm_covariate_probe: asked for {stop - start} forecasts, "
                f"got {len(outputs)}"
            )
        for j, out in enumerate(outputs):
            if out.forecast is None or out.forecast.shape != (HORIZON,):
                raise SystemExit(
                    f"timesfm_covariate_probe: window {start + j} came back "
                    "with no usable forecast"
                )
            point[start + j] = float(out.forecast[HORIZON - 1]) + levels[start + j]
    return point


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timesfm-root", type=Path, default=DEFAULT_TIMESFM_ROOT)
    parser.add_argument("--block", type=int, default=0)
    parser.add_argument("--windows", type=int, default=256)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--chunk", type=int, default=256)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    _prepare_environment(args.timesfm_root)
    npz_path = args.timesfm_root / "work" / "e1" / f"oof_block_{args.block}.npz"
    if not npz_path.is_file():
        raise SystemExit(f"timesfm_covariate_probe: no export at {npz_path}")
    with np.load(npz_path) as data:
        mid = np.asarray(data["mid"], dtype=np.float64)
        imb = np.asarray(data["imb_top_norm"], dtype=np.float64)
        anchors = np.asarray(data["anchors"], dtype=np.int64)

    anchors = anchors[: args.windows]
    if anchors.shape[0] == 0:
        raise SystemExit("timesfm_covariate_probe: no anchors selected")
    anchor_mid = mid[anchors]
    realised = mid[anchors + HORIZON] / anchor_mid - 1.0

    forecaster = _load(args.device, args.batch)

    arms: dict[str, np.ndarray] = {}
    for arm in ("none", "none_repeat", "noise", "imb", "leak"):
        contexts, covariates, levels = _build(
            mid, imb, anchors, "none" if arm == "none_repeat" else arm
        )
        price = _run_arm(
            forecaster, contexts, covariates, levels, chunk=int(args.chunk)
        )
        arms[arm] = price / anchor_mid - 1.0
        print(f"  {arm:12s} done", flush=True)

    base = arms["none"]
    noise_floor = float(np.max(np.abs(arms["none_repeat"] - base)))
    report: dict[str, object] = {
        "block": int(args.block),
        "windows": int(anchors.shape[0]),
        "device": args.device,
        "context_bars": CONTEXT,
        "horizon_bars": HORIZON,
        "noise_seed": NOISE_SEED,
        "noise_floor_max_abs_return_diff": noise_floor,
        "realised_std": float(realised.std()),
        "arms": {},
        "peak_rss_bytes": _peak_rss_bytes(),
    }
    for arm, pred in arms.items():
        diff = np.abs(pred - base)
        report["arms"][arm] = {
            "r2_vs_zero": _r2_vs_zero(pred, realised),
            "rank_ic": _spearman(pred, realised),
            "max_abs_return_diff_vs_none": float(np.max(diff)),
            "mean_abs_return_diff_vs_none": float(np.mean(diff)),
            "exceeds_noise_floor": bool(float(np.max(diff)) > noise_floor),
            "pred_std": float(pred.std()),
        }

    # The two gates. Both are stated as VERDICTS in the report rather than
    # raised on, so a failure is recorded rather than only printed.
    report["gate_covariate_is_forwarded"] = bool(
        report["arms"]["noise"]["max_abs_return_diff_vs_none"] > noise_floor
    )
    report["gate_perfect_covariate_is_exploitable"] = bool(
        report["arms"]["leak"]["r2_vs_zero"] > report["arms"]["none"]["r2_vs_zero"]
    )

    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
