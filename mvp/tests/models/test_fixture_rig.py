"""Every property the later Phase-7 tests lean on, MEASURED here first.

Plans 07-04 through 07-09 assert things about models on this fixture: that a
fit beats a baseline, that a prediction table feeds the simulator, that a
P&L sits below the perfect-foresight ceiling. Each of those assertions is
vacuous unless the fixture can actually exercise it -- and the fixture that
came before this one could not: `tests/fixtures/harness_span.py`'s labels
are `0.0001 * (i % 97)`, perfectly correlated with its own features and
unrelated to its prices, so on it every model scores R^2 near 1.0, the
perfect-foresight ceiling is zero trades, and no gate can fail.

So this file measures and PRINTS six numbers before anything depends on
them. Both bounds, everywhere they exist: a fixture at R^2 0.85 would pass
every downstream gate while looking nothing like the real data's 0.013 to
0.048, so the R^2 test has a CEILING, not only a floor.

Every test here builds its own `tmp_path` lake, registry and MLflow
tracking root through `tests/models/conftest.py`. None can reach a
canonical root: pre-commit runs the full suite on every commit, so a test
that did would spend an irreversible validation look once per commit
forever (D-07-34).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl

from data.time_ns import QTY_SCALE
from harness.accessor import materialize
from sim.arrays import sim_arrays
from sim.kernel import run_sim_checked
from sim.outputs import SimResult
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED
from tests.fixtures.model_span import build_model_span_fixture

#: D-07-09's three model inputs. `mid`, `bid_price` and `ask_price` are
#: BOOKKEEPING and never reach an estimator -- a raw price level smuggles
#: the day's trend into the design matrix. `models/` owns the production
#: constant from plan 07-04; this is the rig's own copy.
MODEL_INPUT_COLUMNS: tuple[str, ...] = ("imb_top", "ofi", "trade_flow")

PRIMARY_LABEL_COLUMN: str = "ret_10s_mid"

#: `harness.accessor.materialize` merges these into the look's MLflow tags;
#: the mandatory keys must all be present. Copied from
#: `tests/harness/test_accessor.py`.
RUN_TAGS: dict[str, str] = {
    "code_hash": "a" * 40,
    "data_hash": "none",
    "seed": "0",
    "env_hash": "b" * 64,
    "model_class": "none (fixture rig, no model in this plan)",
}

#: One tick in raw USDT price units -- the scale `pred` arrives in, the same
#: route `tests/sim/`'s `_price_at_ticks` takes.
ONE_TICK: float = TICK_SIZE_SCALED / PRICE_SCALE


def _rig(lake_root: Path, registry_root: Path, tracking_root: Path, **knobs) -> dict:
    return build_model_span_fixture(lake_root, registry_root, tracking_root, **knobs)


def _look(rig: dict, name: str, lake_root: Path, registry_root: Path, tracking_root):
    """One accessor look at `name`, against the `tmp_path` roots only."""
    return materialize(
        rig["manifest"]["manifest_id"],
        name,
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )


def _closed_pnl_ticks(result: SimResult) -> int:
    """The tick-only closed-P&L sum, the same walk
    `tests/sim/test_path_dependence.py::_closed_pnl_ticks` defines (its
    docstring carries the derivation and why it is not the kernel's
    `realized_pnl_scaled` accumulator)."""
    k = result.fill_count
    prices = result.trade_log["price_ticks"][:k]
    positions = result.trade_log["position_after"][:k]
    return sum(
        int(positions[j - 1]) * (int(prices[j]) - int(prices[j - 1]))
        for j in range(1, k)
    )


def _closed_pnl_usd(result: SimResult) -> float:
    """The same walk weighted by the closed leg's own quantity, in dollars.
    This is the number C4 reports as `$112.05` for the real Option A `val`
    window: tick moves times the position's `qty_scaled`, converted once by
    `TICK_SIZE_SCALED / PRICE_SCALE / QTY_SCALE`."""
    k = result.fill_count
    prices = result.trade_log["price_ticks"][:k]
    positions = result.trade_log["position_after"][:k]
    quantities = result.trade_log["qty_scaled"][:k]
    weighted = sum(
        int(positions[j - 1])
        * (int(prices[j]) - int(prices[j - 1]))
        * int(quantities[j - 1])
        for j in range(1, k)
    )
    return weighted * TICK_SIZE_SCALED / PRICE_SCALE / QTY_SCALE


def _simulate(df: pl.DataFrame, pred: np.ndarray) -> SimResult:
    arrays = sim_arrays(df)
    return run_sim_checked(
        arrays["etime"],
        arrays["bid_ticks"],
        arrays["ask_ticks"],
        np.ascontiguousarray(pred, dtype=np.float64),
        x_bps=0,
    )


def _design_matrix(df: pl.DataFrame) -> np.ndarray:
    """`[1, imb_top, ofi, trade_flow]`. An intercept column, because a
    no-intercept fit would be scored against a mean-centred baseline and
    the comparison would flatter it."""
    return np.column_stack(
        [np.ones(df.height)] + [df[name].to_numpy() for name in MODEL_INPUT_COLUMNS]
    )


def _r2_vs_mean(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """`1 - SSE / SS_tot` with `SS_tot` taken around the EVALUATED rows' own
    mean -- the ordinary R^2, and deliberately not "SSE relative to a
    constant at the TRAIN mean", which would score that constant exactly
    0.0 and make the comparison in
    `test_a_three_feature_ols_beats_the_constant_at_the_train_mean...`
    true by definition rather than by measurement."""
    ss_res = float(((y_true - y_pred) ** 2).sum())
    ss_tot = float(((y_true - y_true.mean()) ** 2).sum())
    return 1.0 - ss_res / ss_tot


def _fit_and_score(train: pl.DataFrame, held_back: pl.DataFrame) -> dict:
    """Plain-numpy OLS on `train`, scored on `held_back`. `models/metrics.py`
    is plan 07-07's; the point here is only that the fixture can tell a fit
    from a constant."""
    y_train = train[PRIMARY_LABEL_COLUMN].to_numpy()
    y_held = held_back[PRIMARY_LABEL_COLUMN].to_numpy()
    beta, *_ = np.linalg.lstsq(_design_matrix(train), y_train, rcond=None)
    fitted = _design_matrix(held_back) @ beta
    constant = np.full(held_back.height, y_train.mean())
    return {
        "beta": beta,
        "r2_fit": _r2_vs_mean(y_held, fitted),
        "r2_constant_at_train_mean": _r2_vs_mean(y_held, constant),
        "train_mean": float(y_train.mean()),
        "held_back_mean": float(y_held.mean()),
        "held_back_variance": float(y_held.var()),
    }


# --------------------------------------------------------------------------
# 1. Anti-vacuity: every declared segment has rows
# --------------------------------------------------------------------------


def test_every_declared_segment_receives_a_nonzero_number_of_admitted_rows(
    lake_root, registry_root, tracking_root
):
    """`tests/harness/test_accessor.py::_build_fixture`'s discipline, one
    level further on: the fixture's physical coverage is asserted by the
    builder, and here every segment a later test will read is MATERIALIZED
    and counted. Per segment, naming itself -- an `all(...)` over the six
    would report "something was empty" and leave the reader to find out
    which."""
    rig = _rig(lake_root, registry_root, tracking_root)
    geometry = rig["geometry"]
    counts: dict[str, int] = {}
    for name in ("val", *geometry["oof_block_names"]):
        frame = _look(rig, name, lake_root, registry_root, tracking_root)
        counts[name] = frame.height
        assert frame.height > 0, (
            f"segment {name!r} received 0 admitted rows -- every later test "
            "that reads it would pass vacuously"
        )
    print("admitted rows per segment:", counts)
    assert counts["val"] == geometry["rows"] - geometry["train_rows"] - 1, (
        "val should hold every row from the train end up to but excluding "
        f"the covered end; got {counts['val']}"
    )
    for name in geometry["oof_block_names"]:
        assert counts[name] == geometry["block_ns"] // geometry["step_ns"], (
            f"{name} holds {counts[name]} rows, not the block's own width in rows"
        )


# --------------------------------------------------------------------------
# 2-3. The simulator's two reference points on this fixture
# --------------------------------------------------------------------------


def test_perfect_foresight_on_the_fixture_val_segment_actually_trades(
    lake_root, registry_root, tracking_root
):
    """The perfect-foresight ceiling (D-07-19) is a HARD guard in plan
    07-07: a reported P&L at or above it raises. A ceiling of zero trades
    would make that guard vacuous, so the ceiling is measured here.

    `pred = mid * (1 + ret_10s_mid)` is the future mid EXACTLY on this
    fixture, because the label was derived from the same price path
    (D-07-23's conversion, and the reason `model_span.py` builds the path
    first). Where the label is null the prediction is `mid` itself, which
    the next test proves is neutral rather than a silent trade generator."""
    rig = _rig(lake_root, registry_root, tracking_root)
    val = _look(rig, "val", lake_root, registry_root, tracking_root)
    mid = val["mid"].to_numpy()
    forward_return = val[PRIMARY_LABEL_COLUMN].fill_null(0.0).to_numpy()
    pred = mid * (1.0 + forward_return)

    moved = int((pred != mid).sum())
    assert moved > val.height // 2, (
        f"vacuous: perfect foresight differs from mid on only {moved} of "
        f"{val.height} rows, so this is barely the pred=mid case"
    )

    result = _simulate(val, pred)
    ticks = _closed_pnl_ticks(result)
    usd = _closed_pnl_usd(result)
    print(
        f"perfect foresight on val ({val.height} rows): "
        f"trades={result.fill_count} closed_ticks={ticks} usd={usd:+.6f}"
    )
    assert result.fill_count > 0, "the ceiling never trades -- 07-07's guard is vacuous"
    assert ticks > 0, "the ceiling loses money -- it is not a ceiling"
    assert usd > 0.0


def test_pred_equal_to_mid_never_trades_and_one_tick_beyond_the_quote_trades_every_row(
    lake_root, registry_root, tracking_root
):
    """Three measured legs, because the neutral-fill convention and its
    anti-vacuity counterpart are only meaningful together.

    1. `pred = mid` -> 0 trades. This is what makes the null-label fill in
       the test above honest rather than a silent trade generator (C4
       measured the same 0 on both real candidate windows).
    2. `pred = mid + ONE tick` -> ALSO 0 trades, and this is the trap worth
       writing down. At a one-tick spread the mid sits half a tick above
       the bid, so `mid + 1 tick` is half a tick above the ASK, and the
       kernel's `floor(pred) > ask_ticks` long trigger rounds it back down
       ONTO the ask. "One tick beyond" has to mean one tick beyond the
       QUOTE, never one tick beyond the mid.
    3. `ask + 1 tick` on even rows, `bid - 1 tick` on odd -> a flip every
       row, which is the counterpart proving the fixture COULD trade. It
       loses money doing it (it crosses the spread on every row), and that
       is the right answer, not a defect."""
    rig = _rig(lake_root, registry_root, tracking_root)
    val = _look(rig, "val", lake_root, registry_root, tracking_root)
    mid = val["mid"].to_numpy()
    bid = val["bid_price"].to_numpy()
    ask = val["ask_price"].to_numpy()

    at_mid = _simulate(val, mid.copy())
    print(f"pred=mid: trades={at_mid.fill_count}")
    assert at_mid.fill_count == 0

    one_tick_above_mid = _simulate(val, mid + ONE_TICK)
    print(f"pred=mid+1tick: trades={one_tick_above_mid.fill_count}")
    assert one_tick_above_mid.fill_count == 0, (
        "a one-tick offset from the half-tick mid is expected NOT to trade; "
        "if it now does, the quantisation rule changed"
    )

    beyond_the_quote = np.where(
        np.arange(val.height) % 2 == 0, ask + ONE_TICK, bid - ONE_TICK
    )
    crossing = _simulate(val, beyond_the_quote)
    print(
        f"pred one tick beyond the quote, alternating side: "
        f"trades={crossing.fill_count} of {val.height} rows, "
        f"closed_ticks={_closed_pnl_ticks(crossing)} "
        f"usd={_closed_pnl_usd(crossing):+.6f}"
    )
    assert crossing.fill_count == val.height, (
        "vacuous: the fixture cannot be made to trade on every row, so "
        f"'pred=mid trades nothing' says little; got {crossing.fill_count}"
    )


# --------------------------------------------------------------------------
# 4. The label's zero point mass
# --------------------------------------------------------------------------


def test_the_zero_point_mass_of_the_fixture_label_is_a_stated_nontrivial_fraction(
    lake_root, registry_root, tracking_root
):
    """`ret_10s_mid` carries a real point mass at exactly 0.0, because the
    10-second midprice move is exactly zero whenever the walk returns to
    where it started. Every rank-IC computation in this phase has to
    exclude those ties (D-07-18, corrected by C2), so a fixture without
    them cannot exercise the tie handling at all.

    THE STATED BAND: the plan's own bound is `0 < fraction < 0.5`; the real
    neighbourhood is 9.8% to 20.0% (pool 20.0%, Option A `val` 13.83%,
    correction C3), and `flat_fraction=0.3` was chosen to land inside it.
    Measured here: 14.7% on `val`, 14.7% across the five OOF blocks."""
    rig = _rig(lake_root, registry_root, tracking_root)
    val = _look(rig, "val", lake_root, registry_root, tracking_root)
    blocks = pl.concat(
        [
            _look(rig, name, lake_root, registry_root, tracking_root)
            for name in rig["geometry"]["oof_block_names"]
        ]
    )
    measured = {}
    for label, frame in (("val", val), ("oof_blocks", blocks)):
        y = frame[PRIMARY_LABEL_COLUMN].drop_nulls().to_numpy()
        assert y.size > 0
        measured[label] = float((y == 0.0).mean())
    print("zero point mass of ret_10s_mid:", measured)
    for label, fraction in measured.items():
        assert 0.0 < fraction < 0.5, f"{label}: {fraction}"
        assert 0.09 < fraction < 0.25, (
            f"{label}: {fraction} has drifted out of the real 9.8%-20% "
            "neighbourhood the flat_fraction knob was calibrated against"
        )
    # The null tail is the other half of the label's shape: the rows whose
    # future mid is not in this partition.
    assert val[PRIMARY_LABEL_COLUMN].null_count() > 0, (
        "vacuous: the val frame carries no null labels, so the null-label "
        "path the real val frame has (8,614 non-finite rows) is untested"
    )


# --------------------------------------------------------------------------
# 5-6. The fixture is learnable, and only just
# --------------------------------------------------------------------------


def test_a_three_feature_ols_beats_the_constant_at_the_train_mean_on_a_held_back_block(
    lake_root, registry_root, tracking_root
):
    """Fit on four OOF blocks, score the fifth. The fit must have
    `r2_vs_mean > 0` while the constant-at-train-mean predictor has
    `r2_vs_mean <= 0`.

    WHY THE BASELINE IS THE TRAIN MEAN AND NOT ZERO: D-07-32 measured a
    constant at the sample mean scoring `R^2_vs_zero = +0.001647` with
    exactly zero skill, which is why "beats zero" needs two numbers. Scored
    against the evaluated rows' own mean, that same constant scores
    NEGATIVE -- measured here at -3.07e-03 -- and the gap between it and
    the fit is what a later plan's selection can actually rest on."""
    rig = _rig(lake_root, registry_root, tracking_root)
    names = rig["geometry"]["oof_block_names"]
    frames = [
        _look(rig, name, lake_root, registry_root, tracking_root) for name in names
    ]
    scored = _fit_and_score(pl.concat(frames[:-1]), frames[-1])
    print(
        f"OLS on {names[:-1]} scored on {names[-1]}: "
        f"r2_fit={scored['r2_fit']:+.6f} "
        f"r2_constant_at_train_mean={scored['r2_constant_at_train_mean']:+.6e} "
        f"beta={scored['beta']}"
    )
    assert scored["held_back_variance"] > 0.0, (
        "vacuous: the held-back block's label is constant, so every "
        "predictor scores the same"
    )
    assert scored["train_mean"] != 0.0, (
        "vacuous: the train mean is exactly zero, so the constant-at-train"
        "-mean baseline is the constant-at-zero baseline and the two gates "
        "D-07-32 separates have collapsed into one"
    )
    assert scored["r2_fit"] > 0.0
    assert scored["r2_constant_at_train_mean"] <= 0.0
    assert scored["r2_fit"] > scored["r2_constant_at_train_mean"]
    assert np.all(np.isfinite(scored["beta"]))
    assert np.any(scored["beta"][1:] != 0.0), "the fit found no signal at all"


def test_the_fitted_ols_scores_inside_a_stated_band_not_a_near_perfect_r2(
    lake_root, registry_root, tracking_root
):
    """BOTH BOUNDS, and the ceiling is the one that matters. A loose ceiling
    like `< 0.9` is nearly useless: a fixture sitting at 0.85 passes every
    downstream gate while looking nothing like the real data's measured
    0.013 to 0.048, and every "the model learned something" test built on
    it would be measuring the fixture.

    THE STATED BAND: `0 < r2_vs_mean < 0.5` is the plan's bound;
    `0.01 <= r2_vs_mean <= 0.05` is the calibration this fixture is
    actually tuned to, and it is a POPULATION prediction rather than a
    tuned-until-green number -- `model_span.py`'s noise is built so the
    three-feature R^2 is `A / (A + noise_scale^2)` = `1.34 / 37.34` =
    0.0359. A held-back 3,600-row block measures +0.027806."""
    rig = _rig(lake_root, registry_root, tracking_root)
    names = rig["geometry"]["oof_block_names"]
    frames = [
        _look(rig, name, lake_root, registry_root, tracking_root) for name in names
    ]
    r2 = _fit_and_score(pl.concat(frames[:-1]), frames[-1])["r2_fit"]
    analytic = 1.34 / (1.34 + 6.0**2)
    print(f"held-back block R^2 = {r2:+.6f} (population prediction {analytic:.6f})")
    assert 0.0 < r2 < 0.5, f"outside the plan's band: {r2}"
    assert 0.01 <= r2 <= 0.05, (
        f"R^2 {r2} has left the 0.01-0.05 band this fixture is calibrated "
        f"to (population {analytic:.6f}, real data 0.013-0.048) -- either "
        "noise_scale moved or the label/feature construction did"
    )
