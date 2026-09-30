"""Forecast the Bitcoin midprice with TimesFM 3.0 while handing it extra
correlated channels, so "several streams at once beat one" becomes a
measurement instead of a claim.

NEVER COLLECTED BY PYTEST -- run manually under the TimesFM interpreter:

    V=/Volumes/ProjectsSSD/aihedgefund/timesfm/.venv/bin/python
    $V tools/timesfm_forecast_mv.py --verify-e1          # regression guard
    $V tools/timesfm_forecast_mv.py --arm none --anchors e2
    $V tools/timesfm_forecast_mv.py --arm eth  --anchors e2

WHY A SECOND RUNNER RATHER THAN A FLAG ON THE FIRST. `tools/timesfm_forecast.py`
produced E1's published numbers. Extending it would put every one of those
numbers behind a refactor, so this file leaves it untouched and IMPORTS its
constants, its loader and its environment setup -- one model, one context
length, one quantile labelling, and no second copy able to drift. `--verify-e1`
closes the loop the other way: the univariate arm here must reproduce E1's
stored `fc-shifted` forecasts EXACTLY, or this runner is not measuring the same
thing. `fc-shifted` and not `fc-raw`: the shifted contexts are the ones E1's
scorer reported, and `--batch 64` matches it too, because `predict_batch`
groups by `per_core_batch_size` and a different grouping reduces a different
set of float32 sums.

WHAT AN ARM IS. Every arm forecasts the same target (the Bitcoin midprice) at
the same anchors from the same 512 bars. Only the extra channels change:

    none     nothing. E1's configuration, re-run as the paired baseline.
    imb      normalised top-of-book imbalance -- the ONE feature the frozen
             predictor's body actually uses; its other two coefficients are
             exactly zero.
    eth      the prevailing Ethereum trade price on the same stamps.
    imb_eth  both, as two channels.

The channels are `past_only_covariates`: known over the context and NOT over
the horizon, which is the honest shape for a live signal. The library masks
them to zero across the horizon, so nothing about the future enters through
them.

THEY ARE SCORED ON IDENTICAL ROWS OR NOT AT ALL. `--anchors e2` recomputes
admissible windows against `btc_clean AND eth_clean`, so a hole in EITHER tape
excludes the window; the univariate arm must then be re-run on those same
anchors, because comparing an arm on 18,877 windows against an arm on 18,500
different ones measures the population, not the model.

THE ANCHOR RULE IS COPIED, AND THE COPY IS PROVEN. `_admissible` restates
`scripts/timesfm_export_grid._admissible_anchors` -- that module cannot be
imported here, because it needs the lake-side interpreter. So the copy is
CHECKED rather than trusted: handed the Bitcoin-only clean mask it must
reproduce the exact anchor array the export stored, and a mismatch is fatal.
That also pins `STRIDE`, which is likewise restated.

THE PERMUTATION NULL, `--permute`. The covariate windows are shuffled across
anchors, so each window gets a real covariate belonging to some OTHER moment.
An arm that "improves" with its covariates deliberately misaligned improved by
adding variance, not information.

ZERO LOOKS. Reads only `work/e1/*.npz` (already exported from cached blocks)
and `work/e2/eth_block_*.npz`. No lake, no accessor, no `val`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from timesfm_forecast import (  # noqa: E402
    CONTEXT,
    DEFAULT_TIMESFM_ROOT,
    HORIZON,
    QUANTILE_LEVELS,
    _load,
    _peak_rss_bytes,
    _prepare_environment,
)

#: Restated from `scripts/timesfm_export_grid.STRIDE` and PROVEN equal by
#: `_check_anchor_rule`, which reproduces the stored anchor array with it.
STRIDE: int = 20

#: Blocks E1 exported. Five OOF blocks; `val` is not among them and cannot be
#: named from this file.
BLOCKS: tuple[int, ...] = (0, 1, 2, 3, 4)

#: Arms this runner knows, and the channels each adds.
ARMS: dict[str, tuple[str, ...]] = {
    "none": (),
    "imb": ("imb",),
    "eth": ("eth",),
    "imb_eth": ("imb", "eth"),
}

#: Seed for `--permute`. Fixed so the null is reproducible.
PERMUTE_SEED: int = 20260930


def _admissible(
    clean: np.ndarray, anchor_ok: np.ndarray, *, stride: int
) -> np.ndarray:
    """Anchor bars whose whole `CONTEXT + HORIZON` span is clean, every `stride`.

    Copied from `scripts/timesfm_export_grid._admissible_anchors`; see this
    module's docstring for why it is copied and how the copy is proven.
    """
    n = clean.shape[0]
    unclean_cumsum = np.concatenate(([0], np.cumsum((~clean).astype(np.int64))))
    candidates = np.arange(CONTEXT - 1, n - HORIZON, stride, dtype=np.int64)
    if candidates.size == 0:
        return candidates
    lo = candidates - (CONTEXT - 1)
    hi = candidates + HORIZON + 1
    span_ok = (unclean_cumsum[hi] - unclean_cumsum[lo]) == 0
    return candidates[span_ok & anchor_ok[candidates]]


def _check_anchor_rule(
    clean: np.ndarray, anchor_ok: np.ndarray, stored: np.ndarray, block: int
) -> None:
    """The copied rule must reproduce the export's own anchors, bar for bar."""
    mine = _admissible(clean, anchor_ok, stride=STRIDE)
    if not np.array_equal(mine, stored):
        raise SystemExit(
            f"timesfm_forecast_mv: on block {block} the copied anchor rule "
            f"yields {mine.shape[0]} anchors, the export stored "
            f"{stored.shape[0]} -- the copy has drifted from "
            "scripts/timesfm_export_grid, or STRIDE is wrong"
        )


def _channels(
    *,
    names: tuple[str, ...],
    imb: np.ndarray,
    eth: np.ndarray,
    anchors: np.ndarray,
) -> list[np.ndarray] | None:
    """One `(channels, CONTEXT)` covariate array per anchor, or `None`.

    A price channel is shifted by its own anchor value for the same reason the
    target is: float32 then spends its precision on the part that varies. A
    dimensionless channel is left alone. Per-variate RevIN inside the model
    removes any constant offset anyway, so the shift is a precision measure and
    not a modelling choice.
    """
    if not names:
        return None
    out: list[np.ndarray] = []
    for raw_k in anchors:
        k = int(raw_k)
        lo, hi = k - CONTEXT + 1, k + 1
        rows: list[np.ndarray] = []
        for name in names:
            if name == "imb":
                rows.append(np.asarray(imb[lo:hi], dtype=np.float64))
            elif name == "eth":
                rows.append(np.asarray(eth[lo:hi] - eth[k], dtype=np.float64))
            else:
                raise SystemExit(f"timesfm_forecast_mv: unknown channel {name!r}")
        block = np.ascontiguousarray(np.vstack(rows), dtype=np.float64)
        if block.shape != (len(names), CONTEXT):
            raise SystemExit(
                f"timesfm_forecast_mv: anchor {k} covariate block has shape "
                f"{block.shape}, expected ({len(names)}, {CONTEXT})"
            )
        if not np.all(np.isfinite(block)):
            # `predict_batch` exposes no per-channel mask and `forward` turns
            # NaN into 0.0 in RAW units -- a silent zero price, not a gap.
            raise SystemExit(
                f"timesfm_forecast_mv: anchor {k} has a non-finite covariate; "
                "the model would silently read it as zero. Use --anchors e2, "
                "which excludes windows with a hole in either tape."
            )
        out.append(block)
    return out


def _contexts(mid: np.ndarray, anchors: np.ndarray) -> tuple[list[np.ndarray], np.ndarray]:
    """E1's context construction, unchanged: `mid - mid[anchor]`, per window."""
    out: list[np.ndarray] = []
    levels = np.empty(anchors.shape[0], dtype=np.float64)
    for i, raw_k in enumerate(anchors):
        k = int(raw_k)
        window = mid[k - CONTEXT + 1 : k + 1]
        if window.shape[0] != CONTEXT:
            raise SystemExit(
                f"timesfm_forecast_mv: anchor {k} yields a {window.shape[0]}-bar "
                f"context, not {CONTEXT}"
            )
        if window[-1] != mid[k]:
            raise SystemExit(
                f"timesfm_forecast_mv: context for anchor {k} ends at "
                f"{window[-1]!r}, not at the anchor's own mid {mid[k]!r}"
            )
        level = float(mid[k])
        levels[i] = level
        out.append(np.ascontiguousarray(window - level, dtype=np.float64))
    return out, levels


def _forecast(
    forecaster,  # noqa: ANN001 - the library type is private
    contexts: list[np.ndarray],
    covariates: list[np.ndarray] | None,
    levels: np.ndarray,
    *,
    chunk: int,
) -> tuple[np.ndarray, np.ndarray]:
    """`(point, quantiles)` in PRICE units, with the per-window shift added back."""
    n = len(contexts)
    point = np.empty((n, HORIZON), dtype=np.float64)
    quantiles = np.empty((n, HORIZON, len(QUANTILE_LEVELS)), dtype=np.float64)
    for start in range(0, n, chunk):
        stop = min(start + chunk, n)
        outputs = list(
            forecaster.predict_batch(
                contexts=contexts[start:stop],
                horizon=HORIZON,
                past_only_covariates=(
                    None if covariates is None else covariates[start:stop]
                ),
                return_quantiles=True,
            )
        )
        if len(outputs) != stop - start:
            raise SystemExit(
                f"timesfm_forecast_mv: asked for {stop - start} forecasts, got "
                f"{len(outputs)}"
            )
        for j, out in enumerate(outputs):
            i = start + j
            if out.forecast is None or out.quantiles is None:
                raise SystemExit(
                    f"timesfm_forecast_mv: window {i} came back with no forecast"
                )
            if out.forecast.shape != (HORIZON,):
                raise SystemExit(
                    f"timesfm_forecast_mv: window {i} forecast has shape "
                    f"{out.forecast.shape}, expected ({HORIZON},) -- a covariate "
                    "leaked into the target variate count"
                )
            if out.quantiles.shape != (HORIZON, len(QUANTILE_LEVELS)):
                raise SystemExit(
                    f"timesfm_forecast_mv: window {i} quantiles have shape "
                    f"{out.quantiles.shape}"
                )
            point[i] = np.asarray(out.forecast, dtype=np.float64) + levels[i]
            quantiles[i] = np.asarray(out.quantiles, dtype=np.float64) + levels[i]
    return point, quantiles


def _block_arrays(work: Path, block: int) -> dict[str, np.ndarray]:
    src = work / "e1" / f"oof_block_{block}.npz"
    if not src.is_file():
        raise SystemExit(f"timesfm_forecast_mv: no E1 export at {src}")
    with np.load(src) as data:
        out = {
            "grid_ns": np.asarray(data["grid_ns"], dtype=np.int64),
            "mid": np.asarray(data["mid"], dtype=np.float64),
            "imb": np.asarray(data["imb_top_norm"], dtype=np.float64),
            "clean": np.asarray(data["clean"], dtype=bool),
            "anchor_ok": np.asarray(data["anchor_ok"], dtype=bool),
            "anchors_e1": np.asarray(data["anchors"], dtype=np.int64),
        }
    eth_src = work / "e2" / f"eth_block_{block}.npz"
    if not eth_src.is_file():
        raise SystemExit(
            f"timesfm_forecast_mv: no Ethereum channel at {eth_src} -- run "
            "`python -m scripts.e2_eth_grid` from mvp/ first"
        )
    with np.load(eth_src) as data:
        eth_grid = np.asarray(data["grid_ns"], dtype=np.int64)
        out["eth"] = np.asarray(data["eth_price"], dtype=np.float64)
        out["eth_clean"] = np.asarray(data["eth_clean"], dtype=bool)
    if not np.array_equal(eth_grid, out["grid_ns"]):
        raise SystemExit(
            f"timesfm_forecast_mv: block {block} Ethereum channel sits on "
            "different stamps than the Bitcoin grid -- the two series are not "
            "aligned and no comparison between them is meaningful"
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timesfm-root", type=Path, default=DEFAULT_TIMESFM_ROOT)
    parser.add_argument("--arm", default="none", choices=sorted(ARMS))
    parser.add_argument("--anchors", default="e2", choices=("e1", "e2"))
    parser.add_argument("--blocks", default=",".join(str(b) for b in BLOCKS))
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--chunk", type=int, default=512)
    parser.add_argument("--permute", action="store_true")
    parser.add_argument(
        "--verify-e1",
        action="store_true",
        help=(
            "run the univariate arm on E1's own anchors and require an EXACT "
            "match against E1's stored `fc-shifted` forecasts, then stop"
        ),
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    _prepare_environment(args.timesfm_root)
    work = args.timesfm_root / "work"
    blocks = tuple(int(b) for b in str(args.blocks).split(",") if b != "")

    if args.verify_e1:
        forecaster = _load(args.device, int(args.batch))
        worst = 0.0
        for block in blocks:
            arrays = _block_arrays(work, block)
            _check_anchor_rule(
                arrays["clean"], arrays["anchor_ok"], arrays["anchors_e1"], block
            )
            anchors = arrays["anchors_e1"]
            contexts, levels = _contexts(arrays["mid"], anchors)
            point, _ = _forecast(
                forecaster, contexts, None, levels, chunk=int(args.chunk)
            )
            stored_path = work / "e1" / "fc-shifted" / f"oof_block_{block}.npz"
            with np.load(stored_path) as stored:
                ref_anchors = np.asarray(stored["anchors"], dtype=np.int64)
                ref_point = np.asarray(stored["point"], dtype=np.float64)
            if not np.array_equal(ref_anchors, anchors):
                raise SystemExit(
                    f"timesfm_forecast_mv: block {block} anchors differ from "
                    "E1's stored anchors"
                )
            diff = float(np.max(np.abs(point - ref_point)))
            worst = max(worst, diff)
            status = "EXACT" if diff == 0.0 else f"max|diff|={diff:.6e}"
            print(f"  block {block}  {anchors.shape[0]:>5} windows  {status}", flush=True)
        print(f"verify-e1: worst max|diff| over {len(blocks)} blocks = {worst:.6e}")
        if worst != 0.0:
            raise SystemExit(
                "timesfm_forecast_mv: the univariate arm does NOT reproduce E1 "
                "bit for bit; every paired comparison below would mix two code "
                "paths"
            )
        print("verify-e1: this runner reproduces E1 exactly")
        return 0

    names = ARMS[args.arm]
    if names and args.anchors == "e1":
        print(
            "WARNING: a covariate arm on E1 anchors may hit a hole in the "
            "Ethereum tape and abort; --anchors e2 is the comparable population",
            flush=True,
        )
    tag = f"{args.arm}{'-perm' if args.permute else ''}"
    out_dir = work / "e2" / f"fc-{tag}-{args.anchors}"
    out_dir.mkdir(parents=True, exist_ok=True)

    forecaster = _load(args.device, int(args.batch))
    rng = np.random.default_rng(PERMUTE_SEED)
    report: dict[str, object] = {
        "arm": args.arm,
        "channels": list(names),
        "anchor_set": args.anchors,
        "permuted": bool(args.permute),
        "permute_seed": PERMUTE_SEED if args.permute else None,
        "device": args.device,
        "batch": int(args.batch),
        "context_bars": CONTEXT,
        "horizon_bars": HORIZON,
        "stride": STRIDE,
        "output_dir": str(out_dir),
        "blocks": {},
    }
    started = time.time()
    total = 0
    for block in blocks:
        arrays = _block_arrays(work, block)
        _check_anchor_rule(
            arrays["clean"], arrays["anchor_ok"], arrays["anchors_e1"], block
        )
        if args.anchors == "e1":
            anchors = arrays["anchors_e1"]
        else:
            anchors = _admissible(
                arrays["clean"] & arrays["eth_clean"],
                arrays["anchor_ok"],
                stride=STRIDE,
            )
        if anchors.shape[0] == 0:
            raise SystemExit(f"timesfm_forecast_mv: block {block} has no anchors")
        contexts, levels = _contexts(arrays["mid"], anchors)
        covariates = _channels(
            names=names, imb=arrays["imb"], eth=arrays["eth"], anchors=anchors
        )
        if covariates is not None and args.permute:
            order = rng.permutation(len(covariates))
            covariates = [covariates[int(j)] for j in order]
        t0 = time.time()
        point, quantiles = _forecast(
            forecaster, contexts, covariates, levels, chunk=int(args.chunk)
        )
        elapsed = time.time() - t0
        dst = out_dir / f"oof_block_{block}.npz"
        np.savez_compressed(
            dst,
            anchors=anchors,
            point=point,
            quantiles=quantiles,
            shift_level=levels,
            quantile_levels=np.asarray(QUANTILE_LEVELS, dtype=np.float64),
        )
        total += int(anchors.shape[0])
        report["blocks"][str(block)] = {
            "windows": int(anchors.shape[0]),
            "windows_e1": int(arrays["anchors_e1"].shape[0]),
            "seconds": elapsed,
            "output": str(dst),
        }
        print(
            f"  block {block}  {anchors.shape[0]:>5} windows "
            f"(E1 had {arrays['anchors_e1'].shape[0]:>5})  {elapsed:6.1f}s",
            flush=True,
        )
    report["windows_total"] = total
    report["seconds_total"] = time.time() - started
    report["peak_rss_bytes"] = _peak_rss_bytes()
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
