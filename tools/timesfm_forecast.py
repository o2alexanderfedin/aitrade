#!/usr/bin/env python3
"""Stage 2 of experiment E1: run zero-shot TimesFM 3.0 over the 1-second grid
that `mvp/scripts/timesfm_export_grid.py` exported, and write the forecasts
back as `.npz` for a scorer that cannot import `torch`.

RUN IT UNDER THE OTHER INTERPRETER. Not `mvp/.venv`'s -- that environment is
lockfile-pinned to `numpy<2.5` for `numba`, and installing `torch` there fails
the `uv lock --check` and `check_pin_versions` hooks until it is torn out
again. From the repo root:

    TR=/Volumes/ProjectsSSD/aihedgefund/timesfm
    "$TR/.venv/bin/python" tools/timesfm_forecast.py --verify-only
    "$TR/.venv/bin/python" tools/timesfm_forecast.py

WHY THIS FILE IS HERE AND NOT SOMEWHERE MORE OBVIOUS. It cannot live under
`mvp/` -- that tree is reserved for MVP code by the containment rule and is
closed to anything the MVP venv cannot import. It cannot live under a
directory named `timesfm/` INSIDE the repo either: the root `.gitignore` lists
`timesfm/`, so `git add` would report success and the file would never land.
So it sits beside `tools/setup-timesfm.sh`, which is here for the same reason.
Verified with `git check-ignore -v` rather than assumed.

IT IMPORTS NOTHING FROM `mvp/`, by necessity rather than by taste: the two
virtualenvs cannot import each other. Everything it needs about the grid
arrives in the `.npz`, including the frozen predictor's own predictions, which
the exporter computed while it still had the lake and the normalization
artifact in reach.

THE THREE CONTROLS THIS SCRIPT RUNS BEFORE IT RUNS THE EXPERIMENT, each
closing a way the headline number could be wrong rather than merely noisy:

1. FLOAT32. `predict_batch` casts every context to `float32` before the model
   sees it. At a mid of ~$77,000 the float32 spacing is ~$0.009, about a tenth
   of a tick -- small against a 10-second sigma of a few dollars, but not
   obviously harmless, and "obviously harmless" is not a measurement. So the
   context is fed SHIFTED (`mid - mid[k]`, the level added back afterwards),
   which costs nothing -- the model's own RevIN and linear detrending are
   shift-invariant -- and buys full float32 precision on the part that varies.
   `--verify-only` measures the raw-versus-shifted disagreement in return
   space on the first `--verify-windows` windows.

2. DEVICE. README.md verified MPS produces the right SHAPES. It did not verify
   it produces the right NUMBERS. The same windows are run on CPU and on the
   requested device and the two forecasts compared.

3. ALIGNMENT. Every window asserts `context[-1] == mid[anchor]`: the last bar
   the model reads must be the anchor bar, because a context that ran one bar
   ahead would be reading the first bar of its own target. Asserted per
   window, not sampled.

WHAT THE POINT FORECAST IS. `ForecastOutput.forecast` is the MEDIAN quantile
(`median_quantile_index = 4` of nine), not a mean. Recorded here because a
median forecast of a near-symmetric distribution is close to its mode, and on
a random-walk-like series the mode is the last value -- which is exactly the
behaviour this experiment expects to find.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np

#: The TimesFM root the setup script laid down: its own venv, the weights, and
#: `work/`. Overridable, but defaulted so the command line stays short.
DEFAULT_TIMESFM_ROOT = Path("/Volumes/ProjectsSSD/aihedgefund/timesfm")

#: Bars of context and bars of horizon. MUST match the exporter's own
#: constants; the loaded `.npz` cannot carry them, so they are cross-checked
#: against the export manifest when one is present and otherwise asserted
#: against the grid's own length.
CONTEXT = 512
HORIZON = 10

#: The checkpoint. Pinned rather than defaulted inside the library so the run
#: record names the weights it used.
CHECKPOINT = "google/timesfm-3.0-pytorch"

#: Quantile levels `timesfm3` returns, in order. Hardcoded from
#: `_ModelConfig.quantiles`' default and CROSS-CHECKED against the live config
#: in `_load`, so a library default change is a failure rather than a silently
#: mislabelled column.
QUANTILE_LEVELS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def _peak_rss_bytes() -> int:
    """Peak resident set size of this process, in BYTES.

    `ru_maxrss` is in bytes on macOS and in KILOBYTES on Linux -- the single
    most commonly misreported number in a benchmark. Branched on `sys.platform`
    rather than assumed.
    """
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(raw) if sys.platform == "darwin" else int(raw) * 1024


def _prepare_environment(timesfm_root: Path) -> dict[str, str]:
    """Point the hub cache at the local weights BEFORE `timesfm3` is imported.

    `HUGGINGFACE_HUB_CACHE` is the load-bearing one: `HF_HOME` alone resolves
    the hub cache to `$HF_HOME/hub`, one level below where the weights sit, and
    the load fails with `LocalEntryNotFoundError`. Both are set anyway so
    nothing strays into `~/.cache`, per README.md.
    """
    cache = timesfm_root / "hf-cache"
    if not cache.is_dir():
        raise SystemExit(
            f"timesfm_forecast: no weight cache at {cache} -- run "
            "tools/setup-timesfm.sh first"
        )
    env = {
        "HF_HOME": str(cache),
        "HUGGINGFACE_HUB_CACHE": str(cache),
        "TOKENIZERS_PARALLELISM": "false",
    }
    os.environ.update(env)
    return env


def _load(device: str, batch: int):  # noqa: ANN201 - the library type is private
    """One forecaster on one device, with the quantile labelling cross-checked.

    `local_files_only=True` is deliberate and not belt-and-braces: it proves
    the weights came from the local directory instead of quietly re-downloading
    a possibly different revision mid-experiment.
    """
    from timesfm3 import TimesFM3Forecaster

    forecaster = TimesFM3Forecaster.from_pretrained(
        CHECKPOINT,
        cache_dir=os.environ["HUGGINGFACE_HUB_CACHE"],
        local_files_only=True,
        device=device,
        per_core_batch_size=int(batch),
    )
    live = tuple(float(q) for q in forecaster.config.quantiles)
    if live != QUANTILE_LEVELS:
        raise SystemExit(
            f"timesfm_forecast: the library returns quantiles {live}, not "
            f"{QUANTILE_LEVELS} -- every quantile column here would be "
            "mislabelled"
        )
    if int(forecaster.config.median_quantile_index) != QUANTILE_LEVELS.index(0.5):
        raise SystemExit(
            "timesfm_forecast: the point forecast is not the 0.5 quantile "
            f"(median_quantile_index={forecaster.config.median_quantile_index})"
        )
    return forecaster


def _contexts(
    mid: np.ndarray, anchors: np.ndarray, *, shifted: bool
) -> tuple[list[np.ndarray], np.ndarray]:
    """One context array per anchor, plus the level each was shifted by.

    THE ALIGNMENT ASSERTION LIVES HERE and fires per window, not per batch: the
    last bar of a context must be the anchor bar itself. A context built one
    bar late would be reading the first bar of its own target, which is the one
    mistake that would make a foundation model look prescient.
    """
    out: list[np.ndarray] = []
    levels = np.empty(anchors.shape[0], dtype=np.float64)
    for i, k in enumerate(anchors):
        k = int(k)
        window = mid[k - CONTEXT + 1 : k + 1]
        if window.shape[0] != CONTEXT:
            raise SystemExit(
                f"timesfm_forecast: anchor {k} yields a {window.shape[0]}-bar "
                f"context, not {CONTEXT}"
            )
        if window[-1] != mid[k]:
            raise SystemExit(
                f"timesfm_forecast: context for anchor {k} ends at "
                f"{window[-1]!r}, not at the anchor's own mid {mid[k]!r} -- the "
                "window is misaligned with the bar it forecasts from"
            )
        level = float(mid[k]) if shifted else 0.0
        levels[i] = level
        out.append(np.ascontiguousarray(window - level, dtype=np.float64))
    return out, levels


def _forecast(
    forecaster,  # noqa: ANN001 - the library type is private
    contexts: list[np.ndarray],
    levels: np.ndarray,
    *,
    chunk: int,
) -> tuple[np.ndarray, np.ndarray]:
    """`(point, quantiles)` in PRICE units, with the shift added back.

    Chunked so a long block never holds every context and every output at once,
    and so a crash leaves a bounded amount of work to redo.
    """
    n = len(contexts)
    point = np.empty((n, HORIZON), dtype=np.float64)
    quantiles = np.empty((n, HORIZON, len(QUANTILE_LEVELS)), dtype=np.float64)
    for start in range(0, n, chunk):
        stop = min(start + chunk, n)
        outputs = list(
            forecaster.predict_batch(
                contexts=contexts[start:stop],
                horizon=HORIZON,
                return_quantiles=True,
            )
        )
        if len(outputs) != stop - start:
            raise SystemExit(
                f"timesfm_forecast: asked for {stop - start} forecasts, got "
                f"{len(outputs)}"
            )
        for j, out in enumerate(outputs):
            i = start + j
            if out.forecast is None or out.quantiles is None:
                raise SystemExit(
                    f"timesfm_forecast: window {i} came back with no forecast"
                )
            if out.forecast.shape != (HORIZON,):
                raise SystemExit(
                    f"timesfm_forecast: window {i} forecast has shape "
                    f"{out.forecast.shape}, expected ({HORIZON},) -- a 1-D "
                    "context must give a 1-D forecast"
                )
            if out.quantiles.shape != (HORIZON, len(QUANTILE_LEVELS)):
                raise SystemExit(
                    f"timesfm_forecast: window {i} quantiles have shape "
                    f"{out.quantiles.shape}, expected "
                    f"({HORIZON}, {len(QUANTILE_LEVELS)})"
                )
            point[i] = np.asarray(out.forecast, dtype=np.float64) + levels[i]
            quantiles[i] = np.asarray(out.quantiles, dtype=np.float64) + levels[i]
    return point, quantiles


def _terminal_return(point: np.ndarray, anchor_mid: np.ndarray) -> np.ndarray:
    """The forecast expressed as the quantity the project forecasts: the return
    from the anchor's mid to the last horizon step.

    The comparison is made IN RETURN SPACE and not in price space, because a
    price-space agreement between two runs is dominated by the shared level and
    would hide a disagreement in the only part that carries information.
    """
    return point[:, HORIZON - 1] / anchor_mid - 1.0


def _verify(
    *,
    export_root: Path,
    block: str,
    device: str,
    batch: int,
    n_windows: int,
) -> dict[str, object]:
    """The float32 control and the device control, on the same windows.

    Both are reported as the maximum absolute disagreement in RETURN space
    beside the standard deviation of the realised returns on those same
    windows, because "1e-9" means nothing until it is next to the number it has
    to be small compared with.
    """
    data = np.load(export_root / f"{block}.npz")
    mid = np.asarray(data["mid"], dtype=np.float64)
    anchors = np.asarray(data["anchors"], dtype=np.int64)[:n_windows]
    anchor_mid = mid[anchors]
    realised = mid[anchors + HORIZON] / anchor_mid - 1.0

    shifted_ctx, shifted_lv = _contexts(mid, anchors, shifted=True)
    raw_ctx, raw_lv = _contexts(mid, anchors, shifted=False)

    report: dict[str, object] = {
        "block": block,
        "n_windows": int(anchors.shape[0]),
        "realised_return_std": float(realised.std()),
        "float32_ulp_at_mid": float(np.spacing(np.float32(anchor_mid.mean()))),
    }

    primary = _load(device, batch)
    shifted_point, _ = _forecast(primary, shifted_ctx, shifted_lv, chunk=batch)
    raw_point, _ = _forecast(primary, raw_ctx, raw_lv, chunk=batch)
    shifted_ret = _terminal_return(shifted_point, anchor_mid)
    raw_ret = _terminal_return(raw_point, anchor_mid)
    report["device"] = device
    report["shift_control_max_abs_return_diff"] = float(
        np.abs(shifted_ret - raw_ret).max()
    )
    report["shift_control_mean_abs_return_diff"] = float(
        np.abs(shifted_ret - raw_ret).mean()
    )
    del primary
    gc.collect()

    if device != "cpu":
        cpu = _load("cpu", batch)
        cpu_point, _ = _forecast(cpu, shifted_ctx, shifted_lv, chunk=batch)
        cpu_ret = _terminal_return(cpu_point, anchor_mid)
        report["device_control_max_abs_return_diff"] = float(
            np.abs(shifted_ret - cpu_ret).max()
        )
        report["device_control_mean_abs_return_diff"] = float(
            np.abs(shifted_ret - cpu_ret).mean()
        )
        del cpu
        gc.collect()
    else:
        report["device_control_max_abs_return_diff"] = None
        report["device_control_mean_abs_return_diff"] = None

    report["peak_rss_bytes"] = _peak_rss_bytes()
    return report


def _run_block(
    forecaster,  # noqa: ANN001 - the library type is private
    *,
    export_root: Path,
    out_root: Path,
    block: str,
    batch: int,
    shifted: bool,
) -> dict[str, object]:
    """Every admissible window of one block, forecast and written.

    `shifted=False` exists because the 64-window control measured the float32
    disagreement at 41% of one return sigma in the worst case -- far too large
    to dismiss on a 64-window sample. At ~180 windows/s the whole raw variant
    costs about two minutes, so the sensitivity is measured on every scored
    window instead of argued from a subsample.
    """
    started = time.monotonic()
    data = np.load(export_root / f"{block}.npz")
    mid = np.asarray(data["mid"], dtype=np.float64)
    anchors = np.asarray(data["anchors"], dtype=np.int64)
    contexts, levels = _contexts(mid, anchors, shifted=shifted)
    point, quantiles = _forecast(forecaster, contexts, levels, chunk=batch)
    del contexts
    elapsed = time.monotonic() - started

    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{block}.npz"
    np.savez_compressed(
        out_path,
        anchors=anchors,
        point=point,
        quantiles=quantiles,
        # `context_last` is the anchor's own mid, written so the scorer can
        # re-derive the alignment instead of trusting it. `shift_level` is what
        # was actually subtracted before the float32 cast -- equal to
        # `context_last` in the shifted run and zero in the raw one.
        context_last=np.asarray(mid[anchors], dtype=np.float64),
        shift_level=levels,
        quantile_levels=np.asarray(QUANTILE_LEVELS, dtype=np.float64),
    )
    print(
        f"  {block}: {anchors.shape[0]:>5,} windows in {elapsed:>7.1f}s "
        f"({anchors.shape[0] / elapsed:>6.1f}/s) -> {out_path.name}",
        flush=True,
    )
    return {
        "block": block,
        "n_windows": int(anchors.shape[0]),
        "seconds": float(elapsed),
        "windows_per_second": float(anchors.shape[0] / elapsed),
        "npz_path": str(out_path),
        "npz_bytes": int(out_path.stat().st_size),
        "peak_rss_bytes": _peak_rss_bytes(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run zero-shot TimesFM 3.0 over the exported 1-second midprice "
            "grid. Requires the TimesFM venv, never mvp/.venv."
        )
    )
    parser.add_argument("--timesfm-root", default=str(DEFAULT_TIMESFM_ROOT))
    parser.add_argument(
        "--export-root",
        default=None,
        help="where the exporter's .npz files are (default: $TIMESFM_ROOT/work/e1)",
    )
    parser.add_argument(
        "--out-root",
        default=None,
        help="where forecasts go (default: $TIMESFM_ROOT/work/e1/forecasts)",
    )
    parser.add_argument("--blocks", default=None, help="comma-separated subset")
    parser.add_argument("--device", default="mps", help="mps, cpu or cuda")
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--verify-windows", type=int, default=64)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="run the float32 and device controls, write the report, and stop",
    )
    parser.add_argument(
        "--raw-contexts",
        action="store_true",
        help=(
            "feed the RAW price instead of the shifted price -- the float32 "
            "sensitivity arm, not the headline run"
        ),
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="forecast without re-running the controls (they are already recorded)",
    )
    args = parser.parse_args(argv)

    timesfm_root = Path(args.timesfm_root).resolve()
    export_root = Path(args.export_root or timesfm_root / "work" / "e1").resolve()
    out_root = Path(args.out_root or export_root / "forecasts").resolve()
    env = _prepare_environment(timesfm_root)

    import torch  # imported AFTER the cache variables are set

    available = {
        "cpu": True,
        "mps": bool(torch.backends.mps.is_available()),
        "cuda": bool(torch.cuda.is_available()),
    }
    if not available.get(args.device, False):
        raise SystemExit(
            f"timesfm_forecast: device {args.device!r} is not available "
            f"({available}) -- pass --device cpu rather than letting a silent "
            "fallback make the run record wrong"
        )

    blocks = (
        [token.strip() for token in str(args.blocks).split(",")]
        if args.blocks
        else sorted(p.stem for p in export_root.glob("oof_block_*.npz"))
    )
    if not blocks:
        raise SystemExit(f"timesfm_forecast: no exported blocks under {export_root}")
    for block in blocks:
        path = export_root / f"{block}.npz"
        if not path.is_file():
            raise SystemExit(f"timesfm_forecast: no export at {path}")

    print(f"timesfm_root  {timesfm_root}")
    print(f"export_root   {export_root}")
    print(f"out_root      {out_root}")
    print(f"device        {args.device}  (available: {available})")
    print(f"batch         {args.batch}")
    print(f"blocks        {blocks}")
    print(f"torch         {torch.__version__}")
    started = time.monotonic()

    report: dict[str, object] = {
        "generated_by": "tools/timesfm_forecast.py",
        "checkpoint": CHECKPOINT,
        "context_bars": CONTEXT,
        "horizon_bars": HORIZON,
        "quantile_levels": list(QUANTILE_LEVELS),
        "device": args.device,
        "device_availability": available,
        "batch": int(args.batch),
        "contexts": "raw" if args.raw_contexts else "shifted",
        "torch_version": str(torch.__version__),
        "python_version": sys.version.split()[0],
        "numpy_version": np.__version__,
        "env": env,
        "export_root": str(export_root),
        "out_root": str(out_root),
        "blocks": [],
    }

    if not args.skip_verify:
        print("--- controls (float32 shift, device) ---", flush=True)
        report["controls"] = _verify(
            export_root=export_root,
            block=blocks[0],
            device=args.device,
            batch=int(args.batch),
            n_windows=int(args.verify_windows),
        )
        for key, value in report["controls"].items():  # type: ignore[union-attr]
            print(f"  {key:38s} {value}")
        if args.verify_only:
            out_root.mkdir(parents=True, exist_ok=True)
            path = out_root / "controls.json"
            path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
            print(f"OK: wrote {path}")
            return 0

    import timesfm3  # noqa: F401 - version only

    forecaster = _load(args.device, int(args.batch))
    print("--- forecasting ---", flush=True)
    for block in blocks:
        report["blocks"].append(  # type: ignore[union-attr]
            _run_block(
                forecaster,
                export_root=export_root,
                out_root=out_root,
                block=block,
                batch=int(args.batch),
                shifted=not args.raw_contexts,
            )
        )

    report["total_seconds"] = float(time.monotonic() - started)
    report["peak_rss_bytes"] = _peak_rss_bytes()
    if args.device == "mps":
        report["mps_driver_allocated_bytes"] = int(torch.mps.driver_allocated_memory())
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / "forecast_run.json"
    path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    total = sum(int(b["n_windows"]) for b in report["blocks"])  # type: ignore[union-attr]
    print(f"OK: {total:,} windows, {report['total_seconds']:.1f}s total")
    print(f"OK: peak RSS {report['peak_rss_bytes'] / 2**30:.2f} GiB")
    print(f"OK: wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
