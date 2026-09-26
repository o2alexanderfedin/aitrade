"""Row alignment, and the smallest end-to-end slice this phase can make: hand
written coefficients to a stored table to a simulated P&L.

WHY ALIGNMENT IS THE PHASE'S TOP RISK (D-07-31), stated once here. A prediction
array is POSITIONAL against its decision rows. Shift it by one and every
number downstream is still finite, still plausible, and now contains one row of
look-ahead: the pred that belongs to row `i` is scored against row `i+1`'s
book. No exception fires anywhere. polars 1.41.2 documents join output order as
unspecified, and `harness.errata.mask_errata_cells` joins every look-role frame
on its way out of the accessor -- so the two assertions in
`models.predictions` exist, and this file is where they are shown to bite.

THE ONE THING NO KEY ASSERTION CAN CATCH is a table whose `etime`/
`decision_seq` are right and whose `pred` is shifted. `assert_table_aligned`
passes on it, and it must -- which is precisely why the table is emitted
POSITIONALLY from the scored frame, in one expression with no join, no sort and
no group_by. `test_a_table_shifted_by_one_row_is_refused_naming_the_etime_
sequence` reports both halves: the key roll RAISES, the pred-only roll passes
every key check and changes the P&L.

NO ESTIMATOR AND NO `models.frozen` HERE. The end-to-end test picks its
coefficients by hand and multiplies them out with `X @ coef`, so this file
proves the PLUMBING -- accessor frame to design matrix to stored table to
conversion to kernel -- with nothing in it that a fitting library could
explain. Plan 07-06 brings the estimators.

Every test uses `tests/models/conftest.py`'s `tmp_path` roots, so no
validation look is ever spent against a canonical store (D-07-34).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from data.time_ns import QTY_SCALE
from harness.accessor import materialize
from models.conversion import neutral_fill_null_predictions
from models.predictions import (
    PREDICTION_TABLE_SCHEMA,
    assert_decision_order,
    assert_table_aligned,
    load_prediction_table,
)
from sim.arrays import sim_arrays
from sim.kernel import run_sim_checked
from sim.outputs import SimResult
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED
from tests.fixtures.model_span import build_model_span_fixture
from tests.fixtures.prediction_span import (
    FIXTURE_PREDICTOR_ID,
    build_prediction_span,
    prediction_table_from_frame,
)

#: D-07-09's three model inputs. `mid`, `bid_price` and `ask_price` are
#: BOOKKEEPING: `mid` is read by `models.conversion` and by nothing else, and a
#: raw price level in the design matrix would smuggle the day's trend into the
#: fit.
MODEL_INPUT_COLUMNS: tuple[str, ...] = ("imb_top", "ofi", "trade_flow")

PRIMARY_LABEL_COLUMN: str = "ret_10s_mid"

#: `tests/models/test_fixture_rig.py::test_perfect_foresight_on_the_fixture_
#: val_segment_actually_trades` measured this on the same fixture at the same
#: default knobs (plan 07-03). It is RE-MEASURED in the end-to-end test below
#: rather than trusted, so fixture drift fails loudly instead of silently
#: moving the bound a model is judged against.
FIXTURE_CEILING_TRADES: int = 98

#: A hand-picked linear model, not a fitted one. The scale is derived, not
#: groped for: one tick at a $70,000 mid is 1.43e-6 in return units, and the
#: long trigger needs `pred >= ask + 1 tick` = `mid + 1.5 ticks`, so a return
#: has to exceed ~2.1e-6 to cross. The fixture's features have std ~6, so a
#: coefficient of 1.5e-7 puts the combined prediction's std at ~1e-6 and about
#: 4% of rows past the crossing threshold. Measured: 23 trades against the
#: 98-trade ceiling. The (1, 0.5, 0.3) ratio is the fixture's own signal
#: ordering (`model_span.FEATURE_SIGNAL_COEFFICIENTS`), so the hand model is
#: pointed the same way a fit would be.
HAND_COEFFICIENTS: tuple[float, float, float] = (1.5e-7, 0.75e-7, 0.45e-7)
HAND_INTERCEPT: float = 3e-7

RUN_TAGS: dict[str, str] = {
    "code_hash": "a" * 40,
    "data_hash": "none",
    "seed": "0",
    "env_hash": "b" * 64,
    "model_class": "hand-specified linear (no estimator, no models.frozen)",
}


def _rig_and_val(lake_root: Path, registry_root: Path, tracking_root: Path):
    """The fixture rig plus ONE accessor look at its `val` segment, against
    `tmp_path` roots. The default knobs, deliberately: the 98-trade ceiling
    and the calibrated R^2 are properties of those knobs."""
    rig = build_model_span_fixture(lake_root, registry_root, tracking_root)
    val = materialize(
        rig["manifest"]["manifest_id"],
        "val",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    return rig, val


def _simulate(frame: pl.DataFrame, pred_price: np.ndarray) -> SimResult:
    arrays = sim_arrays(frame)
    return run_sim_checked(
        arrays["etime"],
        arrays["bid_ticks"],
        arrays["ask_ticks"],
        np.ascontiguousarray(pred_price, dtype=np.float64),
        x_bps=0,
    )


def _closed_pnl(result: SimResult) -> tuple[int, float]:
    """`(closed ticks, closed USD)` by the same walk
    `tests/models/test_fixture_rig.py` defines: tick moves signed by the
    position being closed, and the same moves weighted by that position's own
    `qty_scaled`.

    SLICED TO `[:fill_count]` FIRST, always. `sim.outputs.new_trade_log(n)`
    preallocates with `np.empty`, so the tail past `fill_count` is
    uninitialised memory.
    """
    k = result.fill_count
    prices = result.trade_log["price_ticks"][:k]
    positions = result.trade_log["position_after"][:k]
    quantities = result.trade_log["qty_scaled"][:k]
    ticks = sum(
        int(positions[j - 1]) * (int(prices[j]) - int(prices[j - 1]))
        for j in range(1, k)
    )
    weighted = sum(
        int(positions[j - 1])
        * (int(prices[j]) - int(prices[j - 1]))
        * int(quantities[j - 1])
        for j in range(1, k)
    )
    return ticks, weighted * TICK_SIZE_SCALED / PRICE_SCALE / QTY_SCALE


def _trade_log_digest(result: SimResult) -> str:
    """sha256 over the SLICED trade log's raw bytes -- dtype, shape, then
    contents, per column (`tests/sim/test_determinism.py`'s mechanic). Hashing
    a repr would hide a dtype change; hashing the unsliced arrays would hash
    uninitialised memory and never reproduce."""
    digest = hashlib.sha256()
    k = result.fill_count
    for name in ("etime", "side", "price_ticks", "qty_scaled", "position_after"):
        column = result.trade_log[name][:k]
        digest.update(column.dtype.str.encode())
        digest.update(np.array(column.shape, dtype=np.int64).tobytes())
        digest.update(column.tobytes())
    return digest.hexdigest()


def _hand_model_returns(frame: pl.DataFrame) -> np.ndarray:
    """`intercept + X @ coef` with a bare numpy dot product.

    THE FEATURES ARE RAW, NOT NORMALIZED, and that is a scoping choice worth
    naming: the FEAT-05 normalization artifact is resolved BY MANIFEST at fit
    time (D-07-11) and this fixture has none, while an OLS-style linear
    prediction is invariant under a per-feature rescaling that the
    coefficients absorb. Nothing this file asserts -- alignment, storage, the
    unit conversion, the kernel wiring -- can tell the difference. Plan 07-06
    loads the artifact.
    """
    X = np.column_stack([frame[name].to_numpy() for name in MODEL_INPUT_COLUMNS])
    assert X.shape == (frame.height, 3), X.shape
    return HAND_INTERCEPT + X @ np.asarray(HAND_COEFFICIENTS, dtype=np.float64)


# --------------------------------------------------------------------------
# 1. The round trip preserves the row sequence
# --------------------------------------------------------------------------


def test_a_table_emitted_positionally_from_the_scored_frame_reads_back_row_for_row_identical(
    lake_root, registry_root, tracking_root
):
    """The claim this plan exists to make: a table emitted from a frame,
    written to the lake, addressed by a manifest and read back is still the
    same row sequence as the frame it claims to score.

    The emission is one `pl.DataFrame({...})` off that very frame -- no join,
    no sort, nothing that COULD misalign -- so a failure here is a failure of
    the write/issue/read round trip, which is the only thing between the
    emission and the assertion."""
    rig, val = _rig_and_val(lake_root, registry_root, tracking_root)
    pred_return = _hand_model_returns(val)
    written = build_prediction_span(
        lake_root,
        registry_root,
        frame=val,
        segment_manifest_id=rig["manifest"]["manifest_id"],
        pred=pred_return,
    )
    loaded = load_prediction_table(
        written["manifest"]["manifest_id"],
        written["dataset"],
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert loaded.table.height == val.height
    assert_table_aligned(val, loaded.table)
    # Anti-vacuity: `assert_table_aligned` on a frame of one row would pass
    # for any table, so the frame it just proved must be a real sequence.
    assert val.height > 1_000, f"vacuous: {val.height} rows"


# --------------------------------------------------------------------------
# 2. A one-row shift, in both of its forms
# --------------------------------------------------------------------------


def test_a_table_shifted_by_one_row_is_refused_naming_the_etime_sequence(
    lake_root, registry_root, tracking_root
):
    """The mutation that must be OBSERVED to fire, and its companion that
    cannot be.

    FIRST HALF -- the keys rolled. `np.roll` by one row on `etime` and
    `decision_seq` is exactly what an accidental reorder looks like, and
    `assert_table_aligned` raises naming the etime sequence.

    SECOND HALF -- `pred` alone rolled, the keys left alone. Every key check
    PASSES, because the keys are correct: this is the shift no assertion over
    keys can ever catch, and it is the entire reason the table is emitted
    positionally from the scored frame rather than joined back onto it. What
    it changes is the RESULT: the trade log's bytes and the closed P&L both
    move while the trade COUNT happens to stay the same -- so a test that
    watched only `fill_count` would have called the two runs identical.
    """
    rig, val = _rig_and_val(lake_root, registry_root, tracking_root)
    pred_return = _hand_model_returns(val)
    table = prediction_table_from_frame(val, pred_return)
    assert_table_aligned(val, table)

    rolled_keys = table.with_columns(
        pl.Series("etime", np.roll(table["etime"].to_numpy(), 1), dtype=pl.Int64),
        pl.Series(
            "decision_seq",
            np.roll(table["decision_seq"].to_numpy(), 1),
            dtype=pl.Int64,
        ),
    )
    with pytest.raises(ValueError, match="etime sequence"):
        assert_table_aligned(val, rolled_keys)
    # ...and rolling decision_seq ALONE trips the cross-check, which is the
    # half the etime assertion cannot see.
    with pytest.raises(ValueError, match="decision_seq"):
        assert_table_aligned(
            val,
            table.with_columns(
                pl.Series(
                    "decision_seq",
                    np.roll(table["decision_seq"].to_numpy(), 1),
                    dtype=pl.Int64,
                )
            ),
        )

    mid = val["mid"].to_numpy()
    aligned_price, _ = neutral_fill_null_predictions(pred_return, mid)
    rolled_price, _ = neutral_fill_null_predictions(np.roll(pred_return, 1), mid)
    aligned = _simulate(val, aligned_price)
    rolled = _simulate(val, rolled_price)

    rolled_pred_table = table.with_columns(
        pl.Series("pred", np.roll(table["pred"].to_numpy(), 1), dtype=pl.Float64)
    )
    assert_table_aligned(val, rolled_pred_table)  # passes, and must

    print(
        "pred rolled by one row: "
        f"trades {aligned.fill_count} -> {rolled.fill_count}, "
        f"closed ticks {_closed_pnl(aligned)[0]} -> {_closed_pnl(rolled)[0]}, "
        f"digest {_trade_log_digest(aligned)[:12]} -> "
        f"{_trade_log_digest(rolled)[:12]}"
    )
    assert aligned.fill_count > 0, "vacuous: the aligned run never traded"
    assert _trade_log_digest(aligned) != _trade_log_digest(rolled), (
        "a one-row pred shift produced a byte-identical trade log -- then this "
        "fixture cannot demonstrate the hazard at all"
    )
    assert _closed_pnl(aligned)[0] != _closed_pnl(rolled)[0], (
        "the closed P&L is blind to the shift on this fixture; the digest "
        "above is then the only witness"
    )


# --------------------------------------------------------------------------
# 3. Order is refused before any simulation
# --------------------------------------------------------------------------


def test_a_frame_that_is_not_strictly_etime_ascending_is_refused_before_any_simulation():
    """Two frames, because "strictly" is doing work that "sorted" would not.

    A DESCENDING frame is the obvious case. A frame with a REPEATED etime is
    the one that matters: it is sorted, every key still finds a partner in any
    join, and it is exactly what a `>` weakened to `>=` would wave through --
    while `etime` being unique is what makes `assert_table_aligned`'s
    `array_equal` a comparison of distinct keys rather than of a multiset
    (correction C1).

    No lake and no simulator: the refusal is a property of the function, and
    the point is that it happens BEFORE `run_sim` gets a chance to scan a
    frame it cannot scan correctly."""
    ascending = pl.DataFrame({"etime": pl.Series([10, 20, 30, 40], dtype=pl.Int64)})
    assert assert_decision_order(ascending).tolist() == [10, 20, 30, 40]

    with pytest.raises(ValueError, match="strictly ascending"):
        assert_decision_order(ascending.reverse())
    with pytest.raises(ValueError, match="strictly ascending"):
        assert_decision_order(
            pl.DataFrame({"etime": pl.Series([10, 20, 20, 40], dtype=pl.Int64)})
        )
    # The message names the first offending row pair, not merely the fact.
    with pytest.raises(ValueError, match=r"row 2 \(20\) does not exceed row 1 \(20\)"):
        assert_decision_order(
            pl.DataFrame({"etime": pl.Series([10, 20, 20, 40], dtype=pl.Int64)})
        )


# --------------------------------------------------------------------------
# 4. The stored column's dtype and bits
# --------------------------------------------------------------------------


def test_the_stored_pred_column_reads_back_as_float64_with_bit_identical_values(
    lake_root, registry_root, tracking_root
):
    """D-07-16 pins `pred` to Float64 because the kernel quantises it with a
    symmetric floor/ceil rule and a float32 rounding error near the half-tick
    boundary flips a trigger. So the round trip is asserted on the BITS
    (`array_equal`), never on closeness -- a table that came back 1 ULP
    different would be a table whose simulated trades are not the ones the
    model asked for.

    The values written include a subnormal-adjacent magnitude and a value one
    ULP from another, so "bit-identical" is a claim about the encoding rather
    than about three round numbers."""
    rig, val = _rig_and_val(lake_root, registry_root, tracking_root)
    pred_return = _hand_model_returns(val)
    pred_return[0] = 5e-324  # the smallest positive subnormal
    pred_return[1] = np.nextafter(1e-6, np.inf)
    pred_return[2] = 1e-6
    pred_return[3] = np.nan  # "no prediction", the one spelling this tier uses

    written = build_prediction_span(
        lake_root,
        registry_root,
        frame=val,
        segment_manifest_id=rig["manifest"]["manifest_id"],
        pred=pred_return,
    )
    loaded = load_prediction_table(
        written["manifest"]["manifest_id"],
        written["dataset"],
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert loaded.table.schema == dict(PREDICTION_TABLE_SCHEMA)
    assert loaded.table.schema["pred"] == pl.Float64
    assert loaded.table["pred"].null_count() == 0, "a NaN came back as a null"

    round_tripped = loaded.table["pred"].to_numpy()
    assert np.array_equal(round_tripped, pred_return, equal_nan=True), (
        "the stored pred column is not bit-identical to what was written"
    )
    assert round_tripped[1] != round_tripped[2], (
        "the one-ULP pair collapsed, so 'bit-identical' was not tested at the "
        "precision it claims"
    )
    assert loaded.predictor_id == FIXTURE_PREDICTOR_ID


# --------------------------------------------------------------------------
# 5. The slice, end to end
# --------------------------------------------------------------------------


def test_a_hand_specified_linear_model_scores_the_fixture_val_segment_into_a_simulated_pnl(
    lake_root, registry_root, tracking_root
):
    """The whole right-hand half of the vertical slice, with no estimator and
    no `models.frozen` anywhere in it: accessor look -> design matrix ->
    `intercept + X @ coef` -> stored table -> manifest -> read back ->
    alignment proof -> return-to-price conversion -> kernel -> P&L.

    THE CEILING IS RE-MEASURED, NOT CITED. Perfect foresight on this segment
    is `mid * (1 + ret_10s_mid)` -- exactly the future mid, because the
    fixture derives its labels from its own price path -- and it goes through
    the SAME conversion site as the model's prediction (D-07-33), so the bound
    and the thing it bounds can never be converted by two different rules.
    `98` is asserted rather than assumed: if the fixture drifts, this fails
    loudly instead of silently moving the bound.

    `0 < trades < ceiling` IS A TUNING CONSTRAINT ON THIS FIXTURE AND NOT A
    LAW. A noisy model can easily trade MORE often than perfect foresight
    does -- crossing the spread on every other row is a way to lose money
    quickly, not a way to be right. What the upper bound catches here is the
    coefficients having been scaled into that regime; the lower bound catches
    a model that never triggers, which would make every downstream assertion
    about P&L vacuous."""
    rig, val = _rig_and_val(lake_root, registry_root, tracking_root)
    mid = val["mid"].to_numpy()

    ceiling_return = val[PRIMARY_LABEL_COLUMN].fill_null(np.nan).to_numpy()
    ceiling_price, ceiling_fills = neutral_fill_null_predictions(ceiling_return, mid)
    ceiling = _simulate(val, ceiling_price)
    print(
        f"perfect foresight on val ({val.height} rows): "
        f"trades={ceiling.fill_count} neutral-filled={ceiling_fills} "
        f"closed={_closed_pnl(ceiling)}"
    )
    assert ceiling_fills > 0, (
        "vacuous: no row of this val frame has a null label, so the neutral "
        "fill is not exercised by the ceiling either"
    )
    assert ceiling.fill_count == FIXTURE_CEILING_TRADES, (
        f"the fixture's perfect-foresight ceiling has moved from "
        f"{FIXTURE_CEILING_TRADES} to {ceiling.fill_count} -- the bound below "
        "is measured on this fixture and cannot be inherited across a change "
        "to it"
    )

    pred_return = _hand_model_returns(val)
    written = build_prediction_span(
        lake_root,
        registry_root,
        frame=val,
        segment_manifest_id=rig["manifest"]["manifest_id"],
        pred=pred_return,
    )
    loaded = load_prediction_table(
        written["manifest"]["manifest_id"],
        written["dataset"],
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert_table_aligned(val, loaded.table)

    pred_price, substituted = neutral_fill_null_predictions(
        loaded.table["pred"].to_numpy(), mid
    )
    assert substituted == 0, (
        "the hand model left rows unscored -- the fixture's features are "
        "finite on every row by construction, so this means the design matrix "
        "picked up a label column"
    )
    result = _simulate(val, pred_price)
    ticks, usd = _closed_pnl(result)
    print(
        f"hand model on val: intercept={HAND_INTERCEPT:.2e} "
        f"coef={HAND_COEFFICIENTS} trades={result.fill_count} "
        f"closed_ticks={ticks} usd={usd:+.6f}"
    )

    assert result.fill_count > 0, (
        "the hand model never triggered, so every statement about its P&L is vacuous"
    )
    assert result.fill_count < FIXTURE_CEILING_TRADES, (
        f"{result.fill_count} trades against a {FIXTURE_CEILING_TRADES}-trade "
        "ceiling -- the coefficients have been scaled into the "
        "cross-the-spread-every-row regime"
    )
    assert np.isfinite(usd)
    assert np.all(np.isfinite(result.equity_scaled[: val.height]))

    # The intercept is really in the path: dropping it changes the run. A test
    # whose model happens to be intercept-free would pass with `intercept +`
    # deleted from `_hand_model_returns`.
    without_intercept, _ = neutral_fill_null_predictions(
        pred_return - HAND_INTERCEPT, mid
    )
    assert _simulate(val, without_intercept).fill_count != result.fill_count
