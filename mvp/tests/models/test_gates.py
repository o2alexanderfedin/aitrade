"""`models.gates`: the ceiling as code, the guard biting at its boundary, and
both measured degenerate predictors rejected -- each for its own stated
reason.

TWO KINDS OF FIXTURE HERE, DELIBERATELY.

The GATE tests feed `gate_forecast` the REAL MEASURED metric rows from
`07-RESEARCH.md` Q4's table (2026-09-18, in sample) as literal dicts. The
gate is a pure function of a metrics dict, so handing it the numbers actually
measured on the real data is a stronger statement than handing it a
reconstruction: what is asserted is that the gate rejects the predictors that
were MEASURED to have no skill, not that it rejects something shaped like
them. `tests/models/test_metrics.py` is where `forecast_metrics` is shown to
PRODUCE those numbers from arrays.

The CEILING tests use `tests/fixtures/model_span.py`'s learnable rig through
`tests/models/conftest.py`'s `tmp_path` lake, registry and MLflow tracking
root. Nothing here reaches a canonical root: pre-commit hook 19 runs the full
suite on every commit, so a test that did would spend an irreversible
validation look once per commit forever (D-07-34). The real reference values
(2,192 / 294,554 / $29.4554 on 2026-09-13, and 9,946 / 1,120,460 / $112.0460
on the approved `val` window) are documented in `models/gates.py` and
asserted NOWHERE, for exactly that reason.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

from data.time_ns import QTY_SCALE
from harness.accessor import materialize
from models.gates import (
    CeilingExceededError,
    closed_pnl_ticks,
    gate_forecast,
    gate_monetization,
    guard_against_ceiling,
    perfect_foresight_ceiling,
    ticks_to_usd_at_traded_lot,
    ticks_to_usd_per_btc,
)
from models.metrics import forecast_metrics
from sim.arrays import sim_arrays
from sim.kernel import run_sim_checked
from sim.outputs import SimResult, new_trade_log
from sim.ticks import LOT_STEP_SCALED, PRICE_SCALE, TICK_SIZE_SCALED
from tests.fixtures.model_span import build_model_span_fixture

RUN_TAGS: dict[str, str] = {
    "code_hash": "a" * 40,
    "data_hash": "none",
    "seed": "0",
    "env_hash": "b" * 64,
    "model_class": "none (gate test, no model fitted)",
}

#: 07-RESEARCH.md Q4's measured table, 2026-09-18 in sample. The three rows
#: are the whole argument for D-07-32 and they are copied here as data rather
#: than re-derived.
MEASURED_HONEST_OLS: dict[str, float] = {
    "n_scorable": 7_859_419,
    "r2_vs_zero": 0.022238,
    "r2_vs_mean": 0.020625,
    "rank_ic_all": 0.207494,
    "rank_ic_non_tied": 0.204823,
    "tie_fraction": 0.0977,
    "r2_vs_zero_of_constant_train_mean": 0.001647,
}

#: A CONSTANT at the sample mean: no feature used at all. It WINS the
#: r2_vs_zero half of D-07-18(a) as originally written.
MEASURED_CONSTANT_AT_TRAIN_MEAN: dict[str, float] = {
    "n_scorable": 7_859_419,
    "r2_vs_zero": 0.001647,
    "r2_vs_mean": 0.000000,
    "rank_ic_all": float("nan"),
    "rank_ic_non_tied": float("nan"),
    "tie_fraction": 0.0977,
    "r2_vs_zero_of_constant_train_mean": 0.001647,
}

#: The same OLS multiplied by 1e-6: identical ranks, so it WINS the rank-IC
#: half. `r2_vs_zero` appears as "+0.000000" in the research table, which is a
#: SIX-DECIMAL ROUNDING of a strictly positive number -- this repo measured
#: +4.250915431e-08 for the same construction on calibrated synthetic data
#: (`tests/models/test_metrics.py`). The SIGN is the entire loophole: at
#: exactly 0.0 the original gate would already have rejected this model.
MEASURED_SHRUNK_BY_1E_6: dict[str, float] = {
    "n_scorable": 7_859_419,
    "r2_vs_zero": 4.250915431e-08,
    "r2_vs_mean": -0.001649,
    "rank_ic_all": 0.207494,
    "rank_ic_non_tied": 0.204823,
    "tie_fraction": 0.0977,
    "r2_vs_zero_of_constant_train_mean": 0.001647,
}


def _val_frame(lake_root: Path, registry_root: Path, tracking_root: Path):
    """One accessor look at the fixture's `val` segment, `tmp_path` roots
    only -- the same helper `tests/models/test_neutral_pred.py` uses."""
    rig = build_model_span_fixture(lake_root, registry_root, tracking_root)
    return materialize(
        rig["manifest"]["manifest_id"],
        "val",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )


def _fabricated_sim_result(prices: list[int], sides: list[int]) -> SimResult:
    """A hand-built `SimResult` with `fill_count` fills, for the two
    `gate_monetization` cases the fixture cannot produce on demand.

    `side == position_after` on every row, because the flip-only rule makes
    them equal by construction and `closed_pnl_ticks` refuses a log where they
    disagree. The tail past `fill_count` is left UNINITIALISED on purpose --
    that is the hazard the slicing discipline exists for, and a fabricated log
    that zeroed it would hide a caller that forgot to slice.
    """
    capacity = len(prices) + 5
    log = new_trade_log(capacity)
    k = len(prices)
    log["etime"][:k] = np.arange(k, dtype=np.int64)
    log["side"][:k] = np.asarray(sides, dtype=np.int8)
    log["position_after"][:k] = np.asarray(sides, dtype=np.int8)
    log["price_ticks"][:k] = np.asarray(prices, dtype=np.int64)
    log["qty_scaled"][:k] = LOT_STEP_SCALED
    return SimResult(
        trade_log=log,
        fill_count=k,
        equity_scaled=np.zeros(capacity, dtype=np.int64),
        counters={"trades": k, "flips": max(k - 1, 0), "rows_in_market": k},
    )


# --------------------------------------------------------------------------
# 1. The guard, at the boundary and one tick below it
# --------------------------------------------------------------------------


def test_a_fabricated_pnl_at_the_ceiling_raises_and_one_tick_below_does_not():
    """D-07-19's guard, both sides of `>=`, in the register of
    `test_position_size_at_the_int64_overflow_bound_succeeds_one_tick_over_raises`:
    the boundary VALUE is exercised, not a value near it.

    At the ceiling is already too high. Equality means the model matched
    perfect foresight tick for tick over millions of rows, which no forecast
    of a 10-second return does -- so the interesting case is the one that is
    exactly equal, and it must raise.
    """
    ceiling = 1_120_460  # the approved val window's measured ceiling (C4)
    guard_against_ceiling(ceiling - 1, ceiling, "val")  # must not raise
    with pytest.raises(CeilingExceededError) as excinfo:
        guard_against_ceiling(ceiling, ceiling, "val")
    message = str(excinfo.value)
    print("guard at the ceiling:", message)
    assert "val" in message and str(ceiling) in message
    assert "INVESTIGATE" in message
    assert "not a theorem" in message and "impossibility" in message, (
        "the message must say the ceiling is not a theorem; 'INVESTIGATE' "
        "alone leaves a reader to conclude the opposite"
    )
    assert "misaligned" in message and "leaking" in message, (
        "the two likeliest causes are named in the message so the reader does "
        "not have to find this docstring"
    )
    with pytest.raises(CeilingExceededError):
        guard_against_ceiling(ceiling + 1, ceiling, "val")
    # A ValueError catch still works -- CeilingExceededError is a subclass.
    with pytest.raises(ValueError):
        guard_against_ceiling(ceiling, ceiling, "val")


# --------------------------------------------------------------------------
# 2. The ceiling on the fixture's val segment
# --------------------------------------------------------------------------


def test_the_ceiling_on_the_fixture_val_segment_trades_and_reports_both_usd_conventions(
    lake_root, registry_root, tracking_root
):
    """The measurement Phase 6 never committed, run through committed code.

    Three things are asserted and one is printed. ANTI-VACUITY FIRST: a
    ceiling of zero trades makes `guard_against_ceiling` fire on every
    reported P&L, so `trades > 0` and `closed_pnl_ticks > 0` come before
    anything else. Then the two USD conventions, and the exact integer 1000
    between them.
    """
    val = _val_frame(lake_root, registry_root, tracking_root)
    ceiling = perfect_foresight_ceiling(val)
    print(
        f"fixture val ceiling over {ceiling['rows_walked']} admitted rows "
        f"({ceiling['null_label_rows_filled']} null labels given the neutral "
        f"mid): trades={ceiling['trades']} flips={ceiling['flips']} "
        f"rows_in_market={ceiling['rows_in_market']} "
        f"closed_pnl_ticks={ceiling['closed_pnl_ticks']}\n"
        f"  per 1 BTC convention       ${ceiling['closed_pnl_usd_per_btc']:,.4f}\n"
        f"  realised at the 0.001 lot  ${ceiling['closed_pnl_usd_at_traded_lot']:,.6f}\n"
        f"  closed + unrealised        "
        f"${ceiling['closed_plus_unrealised_usd_at_traded_lot']:,.6f}\n"
        "  (real approved val window, measured in 07-RESEARCH.md Q7 and NOT "
        "asserted here: 9,946 trades / 1,120,460 ticks / $112.0460)"
    )
    assert ceiling["trades"] > 0, (
        "the ceiling never trades -- guard_against_ceiling would then raise on "
        "every reported P&L and the bound would be meaningless"
    )
    assert ceiling["closed_pnl_ticks"] > 0, (
        "the ceiling loses money, so it is not a ceiling"
    )
    assert ceiling["flips"] == ceiling["trades"] - 1, (
        "under perfect foresight every trade after the first is a flip; a "
        "different relationship means the run went flat somewhere"
    )
    assert ceiling["rows_walked"] == val.height

    # The two conventions. The factor is exactly 1000 as an INTEGER ratio;
    # the two floats agree with that to round-off, because neither $0.10 nor
    # 0.001 is binary-exact.
    assert QTY_SCALE % LOT_STEP_SCALED == 0
    assert QTY_SCALE // LOT_STEP_SCALED == 1000
    assert ceiling["closed_pnl_usd_per_btc"] == pytest.approx(
        ceiling["closed_pnl_usd_at_traded_lot"] * 1000.0, rel=1e-12
    )
    assert ceiling["closed_pnl_usd_per_btc"] == pytest.approx(
        ceiling["closed_pnl_ticks"] * TICK_SIZE_SCALED / PRICE_SCALE, rel=1e-12
    )
    # The least-ambiguous expression includes the OPEN leg's mark at the last
    # row, so it is a different number -- reported, never conflated (measured
    # on the real window: 1,120,460 closed against 1,120,530 closed plus
    # unrealised).
    assert ceiling["closed_plus_unrealised_usd_at_traded_lot"] >= (
        ceiling["closed_pnl_usd_at_traded_lot"] - 1e-12
    )


def test_the_ceiling_function_reproduces_the_ad_hoc_walk_the_transcript_used(
    lake_root, registry_root, tracking_root
):
    """D-07-35, checked rather than asserted: the committed function must
    agree with the throwaway measurement it replaces, computed here the way
    `tests/models/test_fixture_rig.py` computes it -- `fill_null(0.0)` on the
    label, `mid * (1 + ret)` inline, and the walk signed by `position_after`.

    If they ever disagree, the ceiling changed meaning and one of the two is
    now wrong; without this test the replacement would be a claim.
    """
    val = _val_frame(lake_root, registry_root, tracking_root)
    mid = val["mid"].to_numpy()
    pred = mid * (1.0 + val["ret_10s_mid"].fill_null(0.0).to_numpy())
    arrays = sim_arrays(val)
    ad_hoc = run_sim_checked(
        arrays["etime"],
        arrays["bid_ticks"],
        arrays["ask_ticks"],
        np.ascontiguousarray(pred, dtype=np.float64),
        x_bps=0,
    )
    k = ad_hoc.fill_count
    prices = ad_hoc.trade_log["price_ticks"][:k]
    positions = ad_hoc.trade_log["position_after"][:k]
    ad_hoc_ticks = sum(
        int(positions[j - 1]) * (int(prices[j]) - int(prices[j - 1]))
        for j in range(1, k)
    )
    ceiling = perfect_foresight_ceiling(val)
    print(
        f"ad hoc: trades={k} ticks={ad_hoc_ticks}  |  committed: "
        f"trades={ceiling['trades']} ticks={ceiling['closed_pnl_ticks']}"
    )
    assert ceiling["trades"] == k
    assert ceiling["closed_pnl_ticks"] == ad_hoc_ticks
    assert closed_pnl_ticks(ad_hoc) == ad_hoc_ticks


def test_a_null_label_row_is_given_the_neutral_mid_and_cannot_fabricate_a_trade(
    lake_root, registry_root, tracking_root
):
    """The rows at the end of a partition whose future mid is not in it have
    no label, and the perfect predictor cannot predict them either. They get
    `pred = mid`, measured neutral -- so the ceiling counts no trade on any of
    them.

    Anti-vacuity is the first assertion: if the fixture's `val` carried zero
    null labels this test would prove nothing at all.
    """
    val = _val_frame(lake_root, registry_root, tracking_root)
    null_rows = val.select(pl.col("etime").filter(pl.col("ret_10s_mid").is_null()))[
        "etime"
    ].to_numpy()
    ceiling = perfect_foresight_ceiling(val)
    print(
        f"val holds {len(null_rows)} null-label rows of {val.height}; the "
        f"ceiling substituted the neutral mid on "
        f"{ceiling['null_label_rows_filled']} of them"
    )
    assert len(null_rows) > 0, (
        "the fixture's val segment has no null labels, so the neutral-fill "
        "path is never exercised and this test is vacuous"
    )
    assert ceiling["null_label_rows_filled"] == len(null_rows), (
        "the substitution count must be exactly the null labels -- the number "
        "is reported so an all-NaN prediction column cannot read as 'no signal'"
    )
    # Re-run to read the trade log, then check no fill landed on a null row.
    mid = np.ascontiguousarray(val["mid"].to_numpy(), dtype=np.float64)
    arrays = sim_arrays(val)
    from models.conversion import neutral_fill_null_predictions

    pred_price, _ = neutral_fill_null_predictions(
        np.ascontiguousarray(
            val["ret_10s_mid"].fill_null(float("nan")).to_numpy(), dtype=np.float64
        ),
        mid,
    )
    result = run_sim_checked(
        arrays["etime"], arrays["bid_ticks"], arrays["ask_ticks"], pred_price, x_bps=0
    )
    traded_at = result.trade_log["etime"][: result.fill_count]
    overlap = np.intersect1d(traded_at, null_rows)
    print(f"fills landing on a null-label row: {overlap.size}")
    assert overlap.size == 0, (
        f"{overlap.size} fill(s) landed on a row with no label -- the neutral "
        "mid fabricated a trade, and the ceiling is counting predictions "
        "nobody made"
    )
    assert result.fill_count == ceiling["trades"]


# --------------------------------------------------------------------------
# 3. The forecast gate, on the measured degenerate predictors
# --------------------------------------------------------------------------


def test_gate_forecast_fails_a_nan_ic_with_its_own_message():
    """A non-finite IC is its OWN failure, not a special case of "IC not
    positive". Asserted on the MESSAGE TEXT, so a future refactor cannot
    collapse the two and leave the booleans looking right.

    The contrast case is the point: a finite NEGATIVE IC must produce the
    generic message and must NOT claim the prediction had no ordering.
    """
    passed, message = gate_forecast(dict(MEASURED_CONSTANT_AT_TRAIN_MEAN))
    print("NaN IC ->", message)
    assert passed is False
    assert "no ordering at all" in message
    assert "not finite" in message.lower()
    assert "spearmanr" in message

    negative_ic = dict(MEASURED_HONEST_OLS)
    negative_ic["rank_ic_non_tied"] = -0.01
    also_failed, other_message = gate_forecast(negative_ic)
    print("negative IC ->", other_message)
    assert also_failed is False
    assert "no ordering at all" not in other_message, (
        "a finite negative IC is a DIFFERENT finding from a constant "
        "prediction; one message for both destroys the distinction D-07-32 "
        "exists to preserve"
    )
    assert "rank_ic_non_tied=-0.01" in other_message
    assert message != other_message


def test_gate_forecast_fails_the_constant_at_train_mean_predictor():
    """The first measured degenerate model. It WINS the r2_vs_zero half of
    D-07-18(a) as originally written -- +0.001647 with no feature used at all
    -- so what rejects it is the IC check, and the message has to say why."""
    metrics = dict(MEASURED_CONSTANT_AT_TRAIN_MEAN)
    assert metrics["r2_vs_zero"] > 0.0, (
        "if the constant-at-the-mean predictor did not beat the constant-zero "
        "reference there would be no loophole to close"
    )
    passed, message = gate_forecast(metrics)
    print("constant at the train mean ->", message)
    assert passed is False
    assert "no ordering at all" in message
    # And the control column agrees with r2_vs_zero, because the predictor IS
    # the control: that equality is the loophole in one line.
    assert metrics["r2_vs_zero_of_constant_train_mean"] == metrics["r2_vs_zero"]


def test_gate_forecast_fails_a_model_shrunk_into_irrelevance():
    """The second measured degenerate model, and the sharper one. Its ranks
    are identical to the honest fit's, so it passes the IC check; its
    `r2_vs_zero` is strictly positive, so it passes D-07-18(a)'s R-squared
    check as written. ONLY the second R-squared reference rejects it."""
    metrics = dict(MEASURED_SHRUNK_BY_1E_6)
    # D-07-18(a) as originally written, evaluated here: it passes.
    assert metrics["r2_vs_zero"] > 0.0 and metrics["rank_ic_non_tied"] > 0.0, (
        "the shrunk model must pass the ORIGINAL gate, or D-07-32 corrects nothing"
    )
    passed, message = gate_forecast(metrics)
    print("shrunk by 1e-6 ->", message)
    assert passed is False
    assert "r2_vs_mean" in message
    assert "unconditional MEAN" in message
    assert "learned the drift" in message
    assert "+0.001647" in message, (
        "the message cites the measured number, so a reader does not have to "
        "take the claim on faith"
    )


def test_gate_forecast_passes_the_honest_measured_fit():
    """Anti-vacuity for all three tests above: the same gate, given the real
    2026-09-18 OLS row, PASSES. A gate that rejected everything would satisfy
    every rejection test in this file."""
    passed, message = gate_forecast(dict(MEASURED_HONEST_OLS))
    print("honest OLS ->", message)
    assert passed is True
    assert message.startswith("ok:")
    assert "tie_fraction" in message, (
        "the tie fraction is reported beside the IC as a first-class number "
        "(D-07-18), including on the passing path"
    )


def test_the_gate_reads_a_metrics_dict_computed_from_arrays_not_only_a_literal():
    """The one end-to-end wiring test: real arrays through
    `forecast_metrics` and straight into `gate_forecast`, so the two modules
    are shown to agree on key names. A renamed key would otherwise be caught
    by neither file."""
    rng = np.random.default_rng(7)
    n = 20_000
    y = np.where(rng.random(n) < 0.15, 0.0, 4.5e-5 + 1.0e-3 * rng.standard_normal(n))
    # A real one-feature OLS, fitted in sample: a hand-written "0.15 * y plus
    # noise" is NOT a fit and scores a negative R-squared against the mean --
    # measured, and the reason this is a lstsq and not a literal.
    feature = y + 6.67e-3 * rng.standard_normal(n)
    design = np.column_stack([np.ones(n), feature])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    honest = design @ beta
    train_mean = 4.5e-5
    passed, message = gate_forecast(forecast_metrics(honest, y, train_mean=train_mean))
    print("end-to-end honest ->", passed, message)
    assert passed is True
    constant = np.full(n, train_mean)
    failed, constant_message = gate_forecast(
        forecast_metrics(constant, y, train_mean=train_mean)
    )
    print("end-to-end constant ->", failed, constant_message)
    assert failed is False
    assert "no ordering at all" in constant_message
    with pytest.raises(KeyError, match="r2_vs_zero"):
        gate_forecast({"rank_ic_non_tied": 0.2, "r2_vs_mean": 0.01})


# --------------------------------------------------------------------------
# 4. The monetization gate
# --------------------------------------------------------------------------


def test_gate_monetization_fails_zero_trades_and_fails_a_negative_pnl():
    """D-07-18(b): strictly more than 0 trades AND strictly more than $0.

    Both failures need their own case because they mean different things. Zero
    trades has a P&L of exactly $0, which a `>= 0` check would pass while
    nothing was monetised at all; a losing run traded and lost.
    """
    flat = _fabricated_sim_result([], [])
    passed, message = gate_monetization(flat)
    print("zero trades ->", message)
    assert passed is False
    assert "0 trades" in message
    assert "absent" in message

    losing = _fabricated_sim_result([100, 99, 101], [1, -1, 1])
    passed, message = gate_monetization(losing)
    print("losing run ->", message)
    assert passed is False
    assert closed_pnl_ticks(losing) == -3
    assert "closed_pnl_ticks=-3" in message
    assert "per 1 BTC" in message, (
        "both USD conventions are named whenever a figure is reported, so a "
        "1000x misreading cannot start here"
    )

    winning = _fabricated_sim_result([100, 101, 99], [1, -1, 1])
    passed, message = gate_monetization(winning)
    print("winning run ->", message)
    assert passed is True
    assert closed_pnl_ticks(winning) == 3
    assert ticks_to_usd_at_traded_lot(3) == pytest.approx(3 * 0.10 * 0.001, rel=1e-12)
    assert ticks_to_usd_per_btc(3) == pytest.approx(0.30, rel=1e-12)


def test_the_closed_pnl_walk_slices_the_uninitialised_tail_and_refuses_a_log_it_cannot_sign():
    """Two hazards in one place. The trade log's tail past `fill_count` is
    uninitialised memory (`np.empty`), so a walk that forgot to slice would
    sum garbage without failing -- here the same fills are placed in a log
    with a longer capacity and the answer must not move. And `side` is what
    signs each closed leg: a log where it disagrees with `position_after` is
    refused rather than silently walked, because under any future rule that
    allowed a partial fill the two stop being interchangeable.
    """
    short = _fabricated_sim_result([100, 101, 99], [1, -1, 1])
    padded = _fabricated_sim_result([100, 101, 99], [1, -1, 1])
    padded.trade_log["price_ticks"][3:] = 10_000_000  # poke the tail
    padded.trade_log["side"][3:] = 1
    assert closed_pnl_ticks(short) == closed_pnl_ticks(padded) == 3

    broken = _fabricated_sim_result([100, 101, 99], [1, -1, 1])
    broken.trade_log["position_after"][1] = 1
    with pytest.raises(ValueError, match="disagree"):
        closed_pnl_ticks(broken)


def test_the_ceiling_refuses_an_empty_frame_and_a_frame_without_the_label():
    """Both would otherwise return a ceiling of 0, and a ceiling of 0 makes
    `guard_against_ceiling` raise on every reported P&L -- a bound that
    rejects everything is worse than no bound."""
    empty = pl.DataFrame(
        schema={
            "etime": pl.Int64,
            "bid_price": pl.Float64,
            "ask_price": pl.Float64,
            "mid": pl.Float64,
            "ret_10s_mid": pl.Float64,
        }
    )
    with pytest.raises(ValueError, match="no rows"):
        perfect_foresight_ceiling(empty)
    no_label = pl.DataFrame(
        {
            "etime": [1, 2],
            "bid_price": [70_000.0, 70_000.1],
            "ask_price": [70_000.1, 70_000.2],
            "mid": [70_000.05, 70_000.15],
        }
    )
    with pytest.raises(ValueError, match="no 'ret_10s_mid' column"):
        perfect_foresight_ceiling(no_label)


def test_the_ceiling_routes_its_prediction_through_the_one_conversion_site(
    lake_root, registry_root, tracking_root, monkeypatch
):
    """The key link D-07-33 asks for, proved BEHAVIOURALLY rather than by
    reading the source: `models.conversion.neutral_fill_null_predictions` is
    replaced with something that refuses, and the ceiling must then fail.

    An identity check on the imported name would pass a module that imported
    the function and then computed `mid * (1 + ret)` inline anyway -- and an
    inline copy produces the SAME NUMBERS, so no value assertion anywhere in
    this file could see it. That is exactly how Phase 6 lost a plan to a
    quantisation asymmetry: two sites, same intent, one of them edited.
    """
    val = _val_frame(lake_root, registry_root, tracking_root)

    def _refuse(*args, **kwargs):
        raise RuntimeError("the one conversion site was called")

    monkeypatch.setattr(
        "models.gates.neutral_fill_null_predictions", _refuse, raising=True
    )
    with pytest.raises(RuntimeError, match="the one conversion site was called"):
        perfect_foresight_ceiling(val)
