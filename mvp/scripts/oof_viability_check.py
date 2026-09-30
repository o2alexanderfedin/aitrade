"""Does the frozen one-feature winner earn anything? Asked on the five OOF
blocks that ALREADY HAVE CACHED FRAMES, so the answer costs ZERO looks.

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.oof_viability_check --counters-only
    ./.venv/bin/python3 -m scripts.oof_viability_check

WHAT THIS IS NOT. It is NOT plan 07-11. It never names the `val` segment, it
writes no prediction table, it issues no manifest, it opens no MLflow run and
it logs no tag. `val`'s look budget is untouched by construction, not by
carefulness -- see `_forbid_materialize` below.

WHY IT IS IN-SAMPLE TWICE OVER, stated here because every number this script
prints inherits it:

1. SELECTION. These five blocks CHOSE the frozen winner (D-07-04). A P&L
   measured on them is an in-sample number in the selection sense, whatever
   its sign.
2. THE FIT. The frozen body was fitted on the whole 44.2M-row train cache,
   which is days 2026-09-12..16 -- i.e. the five OOF blocks themselves. The
   sweep's per-block fits excluded each block through a row mask; this script
   deliberately does NOT refit, because the artifact that would go to `val` is
   exactly the full-train body. So the frozen coefficient has seen every row
   it is scored on here.

Neither is a defect; both make this a VIABILITY CHECK and not a performance
claim. A clean negative here is worth more than the `val` look it saves.

`NUMBA_CACHE_DIR` IS PINNED AT THE TOP, BEFORE ANY IMPORT, for
`scripts/run_stage1_slice.py`'s reason in its own words: with the variable
unset, `@njit(cache=True)` writes its `.nbi`/`.nbc` files into the
`__pycache__` next to the defining source, i.e. into `mvp/sim/__pycache__/`.
Same `setdefault` expression as that script and as `tests/conftest.py`.
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
from collections import Counter  # noqa: E402
from dataclasses import replace  # noqa: E402
from typing import Any  # noqa: E402

import numpy as np  # noqa: E402

from data.lake_paths import (  # noqa: E402
    LAKE_REGISTRY_ROOT,
    lake_root as resolve_lake_root,
    mlflow_tracking_root,
)
from features.normalize import (  # noqa: E402
    load_normalization,
    normalization_dataset,
)
from harness import budget  # noqa: E402
from harness.segments import read_segment_manifest  # noqa: E402
from models import cache as cache_module  # noqa: E402
from models.cache import (  # noqa: E402
    CACHE_ROOT,
    materialize_once,
    repo_root,
    segment_cache_path,
)
from models.conversion import neutral_fill_null_predictions  # noqa: E402
from models.frozen import FrozenLinearPredictor, read_frozen_predictor  # noqa: E402
from models.gates import (  # noqa: E402
    CeilingExceededError,
    closed_pnl_ticks,
    gate_forecast,
    gate_monetization,
    guard_against_ceiling,
    perfect_foresight_ceiling,
    ticks_to_usd_at_traded_lot,
    ticks_to_usd_per_btc,
)
from models.metrics import forecast_metrics  # noqa: E402
from models.regression import TARGET_NAME  # noqa: E402
from models.sweep import _scoring_columns  # noqa: E402
from sim.arrays import sim_arrays  # noqa: E402
from sim.kernel import run_sim_checked  # noqa: E402
from sim.outputs import SimResult  # noqa: E402
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED  # noqa: E402

#: The committed frozen winner (07-10). Named by its manifest id, which is its
#: own self-hash -- `read_frozen_predictor` re-verifies that, re-derives
#: `predictor_id` from the body's five recipe fields and cross-checks the
#: design, so a tampered body cannot be scored by this script either.
FROZEN_PREDICTOR_MANIFEST_ID: str = (
    "e3b4b235d0fe3c025be7c7cdbf7eaa3d2d5675ad9571d6f6a30b1a45680c2d01"
)

#: The Option-A segment manifest whose five OOF blocks are cached.
SEGMENT_MANIFEST_ID: str = (
    "807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548"
)

#: THE ONE SEGMENT NAME THIS SCRIPT MUST NEVER MATERIALIZE. Spelled as a
#: constant so the refusal below is greppable rather than implied.
FORBIDDEN_SEGMENT_NAME: str = "val"

#: The single-feature amplitude family from 07-10's disclosure, block-0 fits,
#: plus the frozen body's own full-train coefficient. Every one of these is an
#: `imb_top`-only model: `ofi` and `trade_flow` are exactly zero in all of
#: them, so they differ ONLY by a positive scalar on one column.
AMPLITUDE_TWINS: tuple[tuple[str, float], ...] = (
    ("grid08", 3.095050e-05),
    ("grid25", 2.610783e-05),
    ("grid26", 1.642992e-05),
    ("grid27", 1.062137e-05),
    ("grid29", 6.747348e-06),
)

#: Every catalogue horizon a perfect-foresight ceiling is measured at. The
#: POINT of measuring four is that `models.gates.perfect_foresight_ceiling` is
#: parameterised by a LABEL, so "the ceiling" is a different number for each
#: one -- on the same rows, walked by the same rule.
CEILING_HORIZONS: tuple[str, ...] = (
    "ret_1s_mid",
    "ret_10s_mid",
    "ret_1min_mid",
    "ret_10min_mid",
)

#: The amplitude given to a perfect SIGN predictor, in return units. 1e-4 is
#: ~7.7 ticks at this window's midprice -- comfortably past the one-full-tick
#: overshoot the kernel's floor/ceil rule demands, so a sign predictor at this
#: amplitude fires on every row whose sign it knows. Chosen to be large, not
#: tuned: the measurement it produces is "what does a perfect next-row SIGN
#: earn when it is allowed to fire", and any amplitude past the overshoot gives
#: the same answer.
PERFECT_SIGN_AMPLITUDE: float = 1e-4

#: `x_bps` values probed on every block. 0 is the phase's only simulated
#: threshold (D-07-18(b)); 1 is probed to MEASURE the integer-bps granularity
#: rather than assume Stage 2 has a usable sweep axis here.
X_BPS_PROBES: tuple[int, ...] = (0, 1)

#: The permutation seed for the `frozen_shuffled` spread control. Pinned so
#: the number it produces is reproducible rather than a different draw on every
#: run.
SHUFFLE_SEED: int = 20260929

EVIDENCE_DIR = Path(".planning/phases/07-regression-track-vertical-slice/evidence")


class ZeroLookViolation(RuntimeError):
    """A cache miss where this script requires a cache hit.

    Raised INSTEAD of `harness.accessor.materialize` ever running. A dedicated
    class rather than a bare `RuntimeError` so a reader of a traceback knows
    immediately that nothing was spent -- the interlock fired before the
    accessor, not after it.
    """


def _forbid_materialize() -> None:
    """Replace the `materialize` name INSIDE `models.cache` with a raiser.

    THIS IS THE LOAD-BEARING ZERO-LOOK CONTROL, and it is deliberately not a
    convention. `materialize_once` reads `materialize` out of its own module
    globals on a cache MISS; rebinding that name means a miss -- from a wrong
    tracking root, a moved scratch directory, a renamed segment -- raises
    instead of spending an irreversible look on a question this script has no
    budget for. The cache-path pre-assertion below is the readable check; this
    is the one that holds when the readable check is wrong.
    """

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        raise ZeroLookViolation(
            "oof_viability_check: harness.accessor.materialize was reached, "
            f"which means a CACHE MISS for args={args!r} kwargs="
            f"{ {k: v for k, v in kwargs.items() if k != 'run_tags'}!r}. This "
            "script runs at zero look cost and every frame it needs is "
            "already cached; a miss is a wrong root or a moved scratch "
            "directory, never a reason to materialize."
        )

    cache_module.materialize = _refuse  # type: ignore[assignment]


def _counters(registry_root: Path, tracking_root: str) -> dict[str, Any]:
    """Every look counter for every segment name of every COMMITTED segment
    manifest -- derived by globbing the registry, never a hardcoded list, so
    a manifest added later is counted rather than silently missed.
    """
    segment_dir = Path(registry_root) / "segments"
    manifest_ids = sorted(path.stem for path in segment_dir.glob("*.json"))
    if not manifest_ids:
        raise ZeroLookViolation(
            f"oof_viability_check: no committed segment manifest under "
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


def _trade_tuples(result: SimResult) -> list[tuple[int, int, int]]:
    """The trade log as `(etime, side, price_ticks)` triples, SLICED to
    `fill_count` first -- the tail past it is uninitialised memory
    (`sim.outputs.new_trade_log`'s own documented hazard).

    The kernel's log carries no row index, so a triple is the finest identity
    available without re-deriving the decision rule. Two fills could in
    principle share all three values at one `etime`; they are compared as a
    MULTISET below for exactly that reason.
    """
    k = int(result.fill_count)
    return list(
        zip(
            result.trade_log["etime"][:k].tolist(),
            result.trade_log["side"][:k].tolist(),
            result.trade_log["price_ticks"][:k].tolist(),
            strict=True,
        )
    )


def _sim_summary(
    name: str,
    result: SimResult,
    *,
    ceiling_ticks: int,
    segment_name: str,
) -> dict[str, Any]:
    """The numbers one simulation produced, plus the ceiling guard's verdict.

    `pnl_fraction_of_ceiling` is reported RAW and is negative when the P&L is
    negative. Clamping it would turn a loss into a small positive fraction of
    a bound it never approached.
    """
    ticks = closed_pnl_ticks(result)
    trades = int(result.fill_count)
    k = trades
    sides = result.trade_log["side"][:k]
    guard_tripped = False
    guard_message = None
    try:
        guard_against_ceiling(ticks, int(ceiling_ticks), segment_name)
    except CeilingExceededError as error:
        guard_tripped = True
        guard_message = str(error)
    passed, reason = gate_monetization(result)
    return {
        "variant": name,
        "trades": trades,
        "long_fills": int((sides == 1).sum()),
        "short_fills": int((sides == -1).sum()),
        "flips": int(result.counters["flips"]),
        "rows_in_market": int(result.counters["rows_in_market"]),
        "closed_pnl_ticks": int(ticks),
        "closed_pnl_usd_at_traded_lot": ticks_to_usd_at_traded_lot(ticks),
        "closed_pnl_usd_per_btc": ticks_to_usd_per_btc(ticks),
        "pnl_fraction_of_ceiling": (
            float(ticks) / float(ceiling_ticks) if int(ceiling_ticks) != 0 else None
        ),
        "monetization_gate_passed": bool(passed),
        "monetization_gate_reason": reason,
        "ceiling_guard_tripped": guard_tripped,
        "ceiling_guard_message": guard_message,
    }


def _trigger_counts(
    pred_price: np.ndarray,
    bid_ticks: np.ndarray,
    ask_ticks: np.ndarray,
    *,
    x_bps: int,
) -> dict[str, int]:
    """The kernel's own two trigger conditions, evaluated in numpy.

    NOT A SECOND DECISION IMPLEMENTATION and never used to produce a P&L: it
    exists to make the MECHANISM behind a trade-count difference countable
    (how many rows each side's trigger admits), and to give the fill sets a
    necessary condition to satisfy. `np.rint` is half-to-even, which is what
    numba's one-argument `round` does; `half_tick_exact_ties` is reported so a
    rounding-mode disagreement would show up as a number rather than as an
    argument.
    """
    scaled = pred_price * PRICE_SCALE
    rounded = np.rint(scaled)
    ties = int(np.count_nonzero(np.abs(scaled - rounded) == 0.5))
    s = rounded.astype(np.int64)
    floor_ticks = s // TICK_SIZE_SCALED
    ceil_ticks = -((-s) // TICK_SIZE_SCALED)
    x_ticks = (bid_ticks + ask_ticks) * int(x_bps) // 20_000
    long_trigger = floor_ticks > ask_ticks + x_ticks
    short_trigger = ceil_ticks < bid_ticks - x_ticks
    return {
        "long_trigger_rows": int(long_trigger.sum()),
        "short_trigger_rows": int(short_trigger.sum()),
        "half_tick_exact_ties": ties,
        "x_ticks_min": int(x_ticks.min()),
        "x_ticks_max": int(x_ticks.max()),
        "negative_scaled_pred_rows": int((s < 0).sum()),
    }


def _path_diagnostics(
    frame: Any,
    arrays: dict[str, np.ndarray],
    mid: np.ndarray,
    pred_return: np.ndarray,
) -> dict[str, Any]:
    """Why a P&L can exceed `perfect_foresight_ceiling` without being
    impossible -- measured, on this block's own rows.

    WHAT THE CEILING ACTUALLY MEASURES. `perfect_foresight_ceiling` feeds the
    REALISED FUTURE MID at one label's horizon into the flip-only rule. That
    input is a PRICE, and the kernel fires only when the predicted price
    clears the touch by a full tick (its symmetric floor/ceil rule). At a
    one-tick spread the future mid sits a half tick off the grid, so a
    horizon-h perfect predictor fires only when the mid moves ~1.5 ticks
    within h -- which makes the resulting P&L a decreasing function of h and
    NOT an upper bound on anything. Four horizons are measured here so that
    dependence is a table rather than an argument.

    THE BOUND THAT IS ACTUALLY PHYSICAL for a one-lot flip-only policy is the
    midprice's own TOTAL VARIATION: no sequence of flips can extract more
    ticks than the mid travelled, and the spread is paid on top. It is
    reported beside two attainable references -- perfect foresight at the
    DECISION-ROW resolution (pred = the next row's mid, the finest horizon the
    data has) and a perfect next-row SIGN at an amplitude large enough to
    always fire.

    AND THE ARITHMETIC THAT EXPLAINS THE MODEL'S P&L: how often the sign of
    the model's own prediction agrees with the sign of the next row's mid
    change, unweighted and weighted by the size of that change. The weighted
    number times the total variation IS the P&L, up to the spread -- so this
    is the single number a reader needs to judge whether the P&L is credible.
    """
    mid_ticks = (arrays["bid_ticks"] + arrays["ask_ticks"]) / 2.0
    delta = np.diff(mid_ticks)
    total_variation = float(np.abs(delta).sum())
    spread = arrays["ask_ticks"] - arrays["bid_ticks"]

    horizons: dict[str, Any] = {}
    for label in CEILING_HORIZONS:
        if label in frame.columns:
            horizons[label] = perfect_foresight_ceiling(frame, label_column=label)

    next_mid = np.empty_like(mid)
    next_mid[:-1] = mid[1:]
    next_mid[-1] = mid[-1]
    next_return = next_mid / mid - 1.0
    attainable: dict[str, Any] = {}
    for name, series in (
        ("perfect_next_row_mid", next_return),
        ("perfect_next_row_sign", PERFECT_SIGN_AMPLITUDE * np.sign(next_return)),
    ):
        pred_price, _ = neutral_fill_null_predictions(
            np.ascontiguousarray(series, dtype=np.float64), mid
        )
        result = run_sim_checked(
            arrays["etime"],
            arrays["bid_ticks"],
            arrays["ask_ticks"],
            pred_price,
            x_bps=0,
        )
        ticks = closed_pnl_ticks(result)
        attainable[name] = {
            "trades": int(result.fill_count),
            "closed_pnl_ticks": int(ticks),
            "closed_pnl_usd_at_traded_lot": ticks_to_usd_at_traded_lot(ticks),
        }
        del result, pred_price

    sign_pred = np.sign(pred_return[:-1])
    moved = (delta != 0.0) & np.isfinite(pred_return[:-1]) & (sign_pred != 0.0)
    agree = np.sign(delta[moved]) == sign_pred[moved]
    weight = np.abs(delta[moved])
    weight_total = float(weight.sum())
    return {
        "mid_total_variation_ticks": total_variation,
        "rows_whose_next_mid_differs": int(moved.sum()),
        "spread_ticks_min": int(spread.min()),
        "spread_ticks_mean": float(spread.mean()),
        "spread_ticks_max": int(spread.max()),
        "ceiling_by_horizon": horizons,
        "attainable_references": attainable,
        "frozen_sign_agrees_with_next_mid_move": float(agree.mean()),
        "frozen_sign_agrees_tick_weighted": (
            float(weight[agree].sum() / weight_total) if weight_total else None
        ),
        "signed_ticks_a_perfect_follower_of_this_sign_captures": (
            float(weight[agree].sum() - weight[~agree].sum())
        ),
    }


def _compare_trade_sets(
    left_name: str,
    left: list[tuple[int, int, int]],
    right_name: str,
    right: list[tuple[int, int, int]],
) -> dict[str, Any]:
    """Are the two trade logs the SAME SET of trades, and if not, where does
    the first difference appear?

    Compared three ways because they answer different questions: an ordered
    common prefix (how far the two runs agree step for step), a multiset
    intersection (how much of the work is shared at all), and the first
    divergent log row printed in full (what actually differs).
    """
    prefix = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        prefix += 1
    left_counter, right_counter = Counter(left), Counter(right)
    shared = sum((left_counter & right_counter).values())
    identical = left == right
    return {
        "left": left_name,
        "right": right_name,
        "left_trades": len(left),
        "right_trades": len(right),
        "identical_sequences": identical,
        "common_ordered_prefix": prefix,
        "shared_multiset_size": shared,
        "only_in_left": len(left) - shared,
        "only_in_right": len(right) - shared,
        "first_divergence_index": None if identical else prefix,
        "first_divergence_left": (
            None if identical or prefix >= len(left) else list(left[prefix])
        ),
        "first_divergence_right": (
            None if identical or prefix >= len(right) else list(right[prefix])
        ),
    }


def _variant(
    predictor: FrozenLinearPredictor, *, coef0: float, intercept: float
) -> FrozenLinearPredictor:
    """The frozen body with one coefficient and the intercept substituted, and
    NOTHING else -- so every variant's prediction leaves the SAME
    `FrozenLinearPredictor.predict` site as the frozen winner's does.

    `dataclasses.replace` re-runs `__post_init__`, so a variant that could
    never predict correctly is refused exactly as a tampered body would be.
    """
    coef = (float(coef0),) + tuple(0.0 for _ in predictor.coef[1:])
    return replace(predictor, coef=coef, intercept=float(intercept))


def _run_block(
    *,
    segment_manifest_id: str,
    segment_name: str,
    predictor: FrozenLinearPredictor,
    params: dict[str, Any],
    registry_root: Path,
    lake_root: Path,
    tracking_root: str,
    cache_root: Path,
) -> dict[str, Any]:
    """One OOF block, end to end: cached frame, ceiling, frozen predictor,
    the zero floor, and the whole amplitude family."""
    frame = materialize_once(
        segment_manifest_id,
        segment_name,
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=tracking_root,
        run_tags={"never_used": "this script cannot reach materialize"},
        cache_root=cache_root,
    )
    features, target = _scoring_columns(
        frame, feature_names=predictor.feature_names, params=params
    )
    mid = np.ascontiguousarray(
        np.asarray(frame["mid"].to_numpy(), dtype=np.float64), dtype=np.float64
    )
    arrays = sim_arrays(frame)
    ceiling = perfect_foresight_ceiling(frame, label_column=TARGET_NAME)
    diagnostics = _path_diagnostics(
        frame,
        arrays,
        mid,
        np.asarray(predictor.predict(features), dtype=np.float64),
    )
    etime_min = int(frame["etime"][0])
    etime_max = int(frame["etime"][-1])
    rows = int(frame.height)
    del frame

    print(
        f"  {segment_name}: {rows} rows, ceiling "
        f"{ceiling['trades']} trades / {ceiling['closed_pnl_ticks']} ticks / "
        f"${ceiling['closed_pnl_usd_at_traded_lot']:.4f}"
    )

    variants: list[tuple[str, FrozenLinearPredictor | None, float, int]] = [
        ("frozen", predictor, 1.0, 0),
        ("zero_predictor", None, 1.0, 0),
        (
            "intercept_only",
            _variant(predictor, coef0=0.0, intercept=predictor.intercept),
            1.0,
            0,
        ),
        (
            "frozen_no_intercept",
            _variant(predictor, coef0=predictor.coef[0], intercept=0.0),
            1.0,
            0,
        ),
    ]
    for name, coefficient in AMPLITUDE_TWINS:
        variants.append(
            (
                name,
                _variant(predictor, coef0=coefficient, intercept=predictor.intercept),
                1.0,
                0,
            )
        )
        variants.append(
            (
                f"{name}_no_intercept",
                _variant(predictor, coef0=coefficient, intercept=0.0),
                1.0,
                0,
            )
        )
    variants.append(("frozen_lambda2", predictor, 2.0, 0))
    variants.append(("frozen_lambda0.5", predictor, 0.5, 0))
    # THE TWO CONTROLS THAT DECIDE WHETHER A POSITIVE P&L MEANS ANYTHING.
    # `frozen_negated` is the sign control: if reversing every decision also
    # earns, the P&L is a property of the flip-only rule and the price path,
    # not of the forecast. `frozen_lag*` is the latency control: the MVP
    # simulates ZERO latency, so the model trades at the touch of the very
    # book update its feature was computed from. Delaying the prediction by
    # one and by ten decision rows is the cheapest measurement of how much of
    # the P&L that simplification is carrying.
    variants.append(("frozen_negated", predictor, -1.0, 0))
    variants.append(("frozen_lag1", predictor, 1.0, 1))
    variants.append(("frozen_lag10", predictor, 1.0, 10))
    variants.append(("frozen_lag100", predictor, 1.0, 100))
    # A LEAD is genuine leakage and is simulated ON PURPOSE, as the shape
    # check a lag alone cannot give: if the P&L PEAKED at a positive lag, the
    # prediction array would already be running ahead of the book and the
    # alignment would be wrong. Measured, it peaks at a lead, which is what a
    # correctly aligned persistent signal looks like.
    variants.append(("frozen_lead1", predictor, 1.0, -1))
    variants.append(("frozen_lead10", predictor, 1.0, -10))
    # THE SPREAD CONTROL the question "or does the spread eat it?" needs: the
    # same prediction values in a seeded random ORDER. Same marginal
    # distribution, same amplitude, no alignment -- so whatever it earns is
    # what this policy earns from the price path alone.
    variants.append(("frozen_shuffled", predictor, 1.0, 0))

    results: dict[str, dict[str, Any]] = {}
    trades_by_variant: dict[str, dict[int, list[tuple[int, int, int]]]] = {}
    for name, variant, lam, lag in variants:
        if variant is None:
            pred_return = np.zeros(rows, dtype=np.float64)
        else:
            pred_return = np.asarray(variant.predict(features), dtype=np.float64)
        if lam != 1.0:
            pred_return = pred_return * float(lam)
        if lag > 0:
            # The first `lag` rows get a ZERO return, i.e. the neutral mid --
            # the same substitution `neutral_fill_null_predictions` makes for
            # a row with no prediction, and measured on this project's data to
            # trigger neither side.
            delayed = np.zeros_like(pred_return)
            delayed[lag:] = pred_return[:-lag]
            pred_return = delayed
        elif lag < 0:
            lead = -lag
            advanced = np.zeros_like(pred_return)
            advanced[:-lead] = pred_return[lead:]
            pred_return = advanced
        if name == "frozen_shuffled":
            pred_return = np.random.default_rng(SHUFFLE_SEED).permutation(pred_return)
        pred_price, n_missing = neutral_fill_null_predictions(pred_return, mid)
        entry: dict[str, Any] = {
            "coef_imb_top": (0.0 if variant is None else variant.coef[0]) * lam,
            "intercept": (0.0 if variant is None else variant.intercept) * lam,
            "lambda": lam,
            "lag_rows": int(lag),
            "n_pred_missing": int(n_missing),
            "by_x_bps": {},
        }
        if variant is not None:
            # Scored on the series that was SIMULATED -- lagged and negated
            # included -- so a variant's metrics and its P&L describe the same
            # array rather than two different ones.
            metrics = dict(
                forecast_metrics(
                    pred_return, target, train_mean=predictor.train_target_mean
                )
            )
            entry["forecast_metrics"] = metrics
            passed, reason = gate_forecast(metrics)
            entry["forecast_gate_passed"] = bool(passed)
            entry["forecast_gate_reason"] = reason
        trades_by_variant[name] = {}
        for x_bps in X_BPS_PROBES:
            result = run_sim_checked(
                arrays["etime"],
                arrays["bid_ticks"],
                arrays["ask_ticks"],
                pred_price,
                x_bps=x_bps,
            )
            summary = _sim_summary(
                name,
                result,
                ceiling_ticks=int(ceiling["closed_pnl_ticks"]),
                segment_name=segment_name,
            )
            summary["triggers"] = _trigger_counts(
                pred_price, arrays["bid_ticks"], arrays["ask_ticks"], x_bps=x_bps
            )
            tuples = _trade_tuples(result)
            trades_by_variant[name][x_bps] = tuples
            summary["fill_etimes_subset_of_trigger_etimes"] = _necessary_condition(
                tuples, pred_price, arrays, x_bps=x_bps
            )
            entry["by_x_bps"][str(x_bps)] = summary
            del result
        results[name] = entry
        print(
            f"    {name:24s} x0: {results[name]['by_x_bps']['0']['trades']:>8d} trades "
            f"{results[name]['by_x_bps']['0']['closed_pnl_ticks']:>+12d} ticks "
            f"${results[name]['by_x_bps']['0']['closed_pnl_usd_at_traded_lot']:>+11.4f}"
        )
        del pred_return, pred_price

    comparisons = [
        _compare_trade_sets(
            left, trades_by_variant[left][0], right, trades_by_variant[right][0]
        )
        for left, right in (
            ("grid08", "grid29"),
            ("grid08", "grid26"),
            ("grid26", "frozen"),
            ("frozen", "grid27"),
            ("frozen", "frozen_lambda2"),
            ("frozen", "frozen_no_intercept"),
            ("grid08_no_intercept", "grid29_no_intercept"),
            ("frozen", "frozen_lag1"),
        )
    ]
    del features, target, mid, arrays, trades_by_variant
    return {
        "segment_name": segment_name,
        "rows": rows,
        "etime_min": etime_min,
        "etime_max": etime_max,
        "ceiling": ceiling,
        "variants": results,
        "amplitude_comparisons": comparisons,
        "path_diagnostics": diagnostics,
    }


def _necessary_condition(
    tuples: list[tuple[int, int, int]],
    pred_price: np.ndarray,
    arrays: dict[str, np.ndarray],
    *,
    x_bps: int,
) -> bool | None:
    """Every fill's `etime` must be an `etime` at which that side's trigger
    holds -- a NECESSARY condition on the kernel's output, checked against the
    numpy mirror of its own comparison.

    `None` when there are no fills at all: there is nothing to check, and
    `True` would read as a verified claim.
    """
    if not tuples:
        return None
    scaled = np.rint(pred_price * PRICE_SCALE).astype(np.int64)
    floor_ticks = scaled // TICK_SIZE_SCALED
    ceil_ticks = -((-scaled) // TICK_SIZE_SCALED)
    bid, ask = arrays["bid_ticks"], arrays["ask_ticks"]
    x_ticks = (bid + ask) * int(x_bps) // 20_000
    etime = arrays["etime"]
    long_etimes = etime[floor_ticks > ask + x_ticks]
    short_etimes = etime[ceil_ticks < bid - x_ticks]
    fills = np.asarray([[t[0], t[1]] for t in tuples], dtype=np.int64)
    ok_long = bool(np.isin(fills[fills[:, 1] == 1][:, 0], long_etimes).all())
    ok_short = bool(np.isin(fills[fills[:, 1] == -1][:, 0], short_etimes).all())
    return ok_long and ok_short


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Simulate the frozen Stage-1 winner on the five ALREADY-CACHED OOF "
            "blocks at zero look cost, and test 07-10's amplitude-invariance "
            "claim by simulating the twins."
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
        help=(
            "comma-separated subset of oof_block names to run (default: all "
            "five). A subset is for a smoke test only -- the reported result "
            "must come from a full run"
        ),
    )
    parser.add_argument("--label", default="results", help="evidence file stem suffix")
    args = parser.parse_args(argv)

    registry_root = LAKE_REGISTRY_ROOT
    lake_root = resolve_lake_root()
    tracking_root = str(mlflow_tracking_root(None))
    cache_root = CACHE_ROOT
    evidence_dir = repo_root() / EVIDENCE_DIR
    out_path = (
        Path(args.out)
        if args.out
        else evidence_dir / f"07-oof-viability-{args.label}.json"
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
            f"oof_viability_check: {FORBIDDEN_SEGMENT_NAME!r} appears in the "
            "OOF block list -- refusing to proceed"
        )
    if args.blocks:
        requested = [token.strip() for token in str(args.blocks).split(",")]
        unknown = [name for name in requested if name not in block_names]
        if unknown:
            raise ZeroLookViolation(
                f"oof_viability_check: --blocks names {unknown}, which are not "
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
                f"oof_viability_check: no cached frame at {path} for segment "
                f"{name!r} -- this script requires a cache HIT for every block "
                "and will not materialize one"
            )
        print(f"cache hit ready  {name:12s} {path}")

    predictor = read_frozen_predictor(
        FROZEN_PREDICTOR_MANIFEST_ID, registry_root=registry_root
    )
    print(f"predictor_id    {predictor.predictor_id}")
    print(
        f"coef            {dict(zip(predictor.feature_names, predictor.coef, strict=True))}"
    )
    print(f"intercept       {predictor.intercept!r}")
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
                segment_manifest_id=SEGMENT_MANIFEST_ID,
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
                f"oof_viability_check: look counters changed after {name} -- "
                f"before={before['counts']} after={mid_check['counts']}"
            )

    after = _counters(registry_root, tracking_root)
    _print_counters(after, "AFTER")
    body = {
        "generated_by": "mvp/scripts/oof_viability_check.py",
        "in_sample_disclosure": (
            "These five OOF blocks chose the frozen winner (selection "
            "in-sample), and the frozen body was fitted on the whole train "
            "cache, which contains all five blocks (fit in-sample). Every "
            "number here is a viability check, not a performance claim."
        ),
        "frozen_predictor_manifest_id": FROZEN_PREDICTOR_MANIFEST_ID,
        "predictor_id": predictor.predictor_id,
        "segment_manifest_id": SEGMENT_MANIFEST_ID,
        "normalization_manifest_id": predictor.normalization_manifest_id,
        "coef": dict(zip(predictor.feature_names, predictor.coef, strict=True)),
        "intercept": predictor.intercept,
        "train_target_mean": predictor.train_target_mean,
        "x_bps_probes": list(X_BPS_PROBES),
        "look_counters_before": before,
        "look_counters_after": after,
        "blocks": blocks,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(body, sort_keys=True, indent=2, default=float) + "\n"
    )
    print(f"OK: wrote {out_path}")
    # THE CEILING GUARD'S VERDICT IS THE HEADLINE, so it is summarised per
    # block rather than listed per variant: a 66-entry list of (block,
    # variant) pairs buries the one fact a reader needs, which is that the
    # guard fired at all and on which blocks.
    tripped: dict[str, int] = {}
    for block in blocks:
        count = sum(
            1
            for entry in block["variants"].values()
            for probe in entry["by_x_bps"].values()
            if probe["ceiling_guard_tripped"]
        )
        if count:
            tripped[str(block["segment_name"])] = count
    for block in blocks:
        frozen = block["variants"]["frozen"]["by_x_bps"]["0"]
        diagnostics = block["path_diagnostics"]
        print(
            f"  {block['segment_name']}: frozen "
            f"{frozen['closed_pnl_ticks']:+,} ticks = "
            f"{frozen['pnl_fraction_of_ceiling']:.3f} x the {TARGET_NAME} "
            f"ceiling, "
            f"{frozen['closed_pnl_ticks'] / diagnostics['mid_total_variation_ticks']:.3f}"
            f" x the mid's total variation"
        )
    if tripped:
        print(
            "FAIL: guard_against_ceiling fired -- "
            + ", ".join(f"{name}: {count} variants" for name, count in tripped.items())
            + ". See path_diagnostics in the evidence JSON before reading this "
            "as a leak: the ceiling is measured at ONE label's horizon and is "
            "not an upper bound on a rule that re-decides every row."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
