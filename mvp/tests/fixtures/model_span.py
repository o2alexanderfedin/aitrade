"""A LEARNABLE, sim-realistic features-tier fixture: a price path first,
and labels DERIVED from it.

WHY THIS FILE EXISTS ALONGSIDE `harness_span.py`. That fixture's labels are
`0.0001 * (i % 97)` and its features are `float(i % 97)` -- perfectly
correlated with each other and unrelated to its prices. It is exactly right
for what it was built for (purge/embargo geometry, where only `etime` and
the bookkeeping columns matter) and useless for anything a model touches:
on it every estimator scores R^2 near 1.0, `mid` has nothing to do with
`bid_price`/`ask_price`, and `mid * (1 + ret_10s_mid)` is not the future
mid, so the perfect-foresight ceiling is meaningless and no model gate can
fail. `harness_span.py` is NOT modified -- five harness test modules depend
on its exact values.

THE ORDER OF CONSTRUCTION IS THE POINT:

1. A deterministic random walk in integer TICKS, so every price is exactly
   on the $0.10 grid and `sim.ticks.price_to_ticks` (which refuses off-grid
   input) cannot reject it.
2. A constant ONE-tick spread, so `mid` is always a half tick -- the real
   pool's own condition 98.8% of the time (D-07-23), and the one under
   which `mid * (1 + ret)` reconstructing a future mid cannot straddle an
   integer tick boundary under the kernel's floor/ceil quantisation.
3. Labels derived from that path: `ret_10s_mid[t] = (mid[t+h] - mid[t]) /
   mid[t]`. So `mid * (1 + ret_10s_mid)` really IS the future mid, and the
   perfect-foresight ceiling is a real ceiling.
4. Features derived from the label, with noise: a real fit must beat a
   constant AND must not score 1.0.

MEASURED PROPERTIES AT THE DEFAULT KNOBS (`seed=20260925`,
`flat_fraction=0.3`, `noise_scale=6.0`, `rows=19_800`, 1 s steps) --
`tests/models/test_fixture_rig.py` measures and PRINTS each of these, and
the numbers below are what it measured:

| property | fixture | real pool |
|---|---|---|
| zero point mass of `ret_10s_mid` | 14.66% | 20.0% pool, 13.83% Option A `val` (C3) |
| 3-feature OLS R^2 vs the eval block's own mean | +0.0278 | +0.013..+0.048 train-internal |
| constant-at-train-mean, same score | -0.0031 | zero skill by construction (D-07-32) |
| perfect foresight on `val` | 98 trades / 311 ticks / +$0.0311 | 9,946 / 1,120,460 / +$112.05 (C4) |
| `pred = mid` | 0 trades | 0 trades, measured (C4) |

THE R^2 IS CALIBRATED, NOT GROPED FOR. With `x_i = a_i z + s e_i` for
independent unit-normal `e_i` and `z` the unit-variance label, the
population R^2 of the three-feature linear fit is `A / (A + s^2)` where
`A = sum(a_i^2)` -- second moments only, so it holds whatever the label's
distribution (and this label has a 15% point mass at zero). At the default
coefficients `A = 1.0^2 + 0.5^2 + 0.3^2 = 1.34` and `s = 6.0`, that is
`1.34 / 37.34 = 0.0359`, and a held-back 3,600-row block measures +0.0278.
Turning the `noise_scale` knob moves the fixture along a known curve
instead of by trial and error.

FEATURE MAGNITUDES ARE NOT UNIT-REALISTIC and cannot matter: OLS R^2 is
invariant under a per-feature rescaling, so the fixture models the
CORRELATION STRUCTURE of `imb_top`/`ofi`/`trade_flow` against the label and
nothing about their units. The PRICE columns are the ones that must be
realistic, because the simulator quantises them.

THIS FILE USES THE TIER'S WRITER ONLY (`write_feature_partition`,
`issue_feature_manifest`). `features.tier.load_features` is never imported
here or anywhere under `models/`/`tests/models/` -- D-05-15's rule, with
`tools/check_harness_accessor_only.py` as its tripwire. Rows are read back
through `harness.accessor.materialize`, which is what counts the look.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl

from data.time_ns import LABEL_HORIZON_NS, NS_PER_SECOND
from features.tier import (
    FEATURE_COLUMNS,
    FEATURE_ROW_SCHEMA,
    FEATURE_SCHEMA_VERSION,
    LABEL_COLUMNS,
    PRIMARY_LABEL,
    issue_feature_manifest,
    write_feature_partition,
)
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED, price_to_ticks
from tests.fixtures.feature_build import write_dq_report

SYMBOL = "BTCUSDT"

#: One tick, always. See the module docstring: a one-tick spread puts `mid`
#: on a half tick, which is the real pool's condition and the one the
#: kernel's symmetric floor/ceil rule was fixed for (D-07-23).
SPREAD_TICKS: int = 1

#: $70,000.0 at `TICK_SIZE_SCALED` -- the same order of magnitude as the
#: real 2026-09 BTCUSDT book, so a $100 notional cap still buys a whole
#: `LOT_STEP_SCALED` and `position_size_ticks` has something to size.
BASE_BID_TICKS: int = 700_000

#: `P(increment == 0)` per step. 0.3 was chosen by measurement, not taste:
#: the 10-second label's zero point mass is `P(sum of 10 increments == 0)`,
#: not this number, and at 0.3 that works out to 14.66% -- next to the
#: 13.83% correction C3 measured for the Option A `val` window. Raising it
#: to 0.5 gives 17.6%; 0.0 gives 24.6% (all ten steps are then +/-1 and the
#: sum is zero whenever five go each way), so the relationship is not
#: monotone in the direction intuition suggests.
DEFAULT_FLAT_FRACTION: float = 0.3

#: The per-feature noise multiplier `s` in the `A / (A + s^2)` law above.
#: 6.0 lands the three-feature R^2 at 0.0359 in population and +0.0278 on a
#: held-back block -- inside the 0.013..0.048 the real train-internal
#: splits measured, which is a far tighter target than "below 0.5".
DEFAULT_NOISE_SCALE: float = 6.0

#: How strongly each model input carries the label, before noise. `imb_top`
#: strongest, `trade_flow` weakest -- the ordering the real rank ICs showed.
#: `sum(a^2)` is the `A` of the R^2 law.
FEATURE_SIGNAL_COEFFICIENTS: dict[str, float] = {
    "imb_top": 1.0,
    "ofi": 0.5,
    "trade_flow": 0.3,
}

DEFAULT_SEED: int = 20260925
DEFAULT_ROWS: int = 19_800
DEFAULT_DATE: str = "2026-09-13"


def _prices_from_ticks(ticks: np.ndarray) -> np.ndarray:
    """Tick counts -> USDT prices, by the same fixed-point route
    `tests/sim/`'s `_price_at_ticks` uses (`ticks * TICK_SIZE_SCALED /
    PRICE_SCALE`), so every value round-trips through
    `sim.ticks.price_to_ticks` exactly."""
    return ticks * TICK_SIZE_SCALED / PRICE_SCALE


def _assert_partition_invariants(
    df: pl.DataFrame,
    *,
    bid_ticks: np.ndarray,
    ask_ticks: np.ndarray,
    step_ns: int,
    rows: int,
) -> None:
    """Every property a caller of this builder is entitled to assume,
    asserted on the constructed frame BEFORE it is written -- the
    `tests/harness/test_accessor.py::_build_fixture` discipline: a fixture
    nobody checked is a fixture whose failures look like the system's.
    """
    etime = df["etime"].to_numpy()
    if not np.all(np.diff(etime) > 0):
        raise AssertionError(
            "build_model_span_partition: etime is not strictly ascending -- "
            "the real pool's etime is globally unique and sorted (60,926,503 "
            "distinct values in 60,926,503 rows, correction C1) and the "
            "fixture must share that property"
        )
    if len(np.unique(etime)) != rows:
        raise AssertionError("build_model_span_partition: etime is not unique")

    bid = df["bid_price"].to_numpy()
    ask = df["ask_price"].to_numpy()
    mid = df["mid"].to_numpy()
    if not np.array_equal(mid, (bid + ask) / 2.0):
        raise AssertionError(
            "build_model_span_partition: mid != (bid_price + ask_price) / 2.0 "
            "-- the v2 partitions satisfy that identity on every row and the "
            "return-to-price conversion depends on it"
        )
    # `price_to_ticks` refuses off-grid input and refuses an exact half-tick
    # round-trip; both quote columns must survive it, and must come back as
    # the very integers the walk was built from.
    if not np.array_equal(price_to_ticks(bid), bid_ticks):
        raise AssertionError("build_model_span_partition: bid_price is off-grid")
    if not np.array_equal(price_to_ticks(ask), ask_ticks):
        raise AssertionError("build_model_span_partition: ask_price is off-grid")

    for name, horizon_ns in LABEL_HORIZON_NS.items():
        h = horizon_ns // step_ns
        column = df[name]
        expected_nulls = min(h, rows)
        if column.null_count() != expected_nulls:
            raise AssertionError(
                f"build_model_span_partition: {name} has "
                f"{column.null_count()} nulls, expected exactly the trailing "
                f"{expected_nulls} (its horizon in rows) -- the label of a row "
                "whose future mid is not in this partition is null, which is "
                "also how the real val frame's non-finite rows arise"
            )
        tail = column.tail(expected_nulls).to_list()
        if any(value is not None for value in tail):
            raise AssertionError(
                f"build_model_span_partition: {name}'s nulls are not the trailing rows"
            )

    for name in FEATURE_SIGNAL_COEFFICIENTS:
        values = df[name].to_numpy()
        if not np.all(np.isfinite(values)):
            raise AssertionError(
                f"build_model_span_partition: {name} is not finite on every "
                "row -- the rows whose label is null must still carry usable "
                "features, or the null-label path is never exercised"
            )


def build_model_span_partition(
    lake_root: Path,
    registry_root: Path,
    *,
    symbol: str = SYMBOL,
    date: str = DEFAULT_DATE,
    start_ns: int = 0,
    step_ns: int = NS_PER_SECOND,
    rows: int = DEFAULT_ROWS,
    seed: int = DEFAULT_SEED,
    flat_fraction: float = DEFAULT_FLAT_FRACTION,
    noise_scale: float = DEFAULT_NOISE_SCALE,
    code_hash: str = "deadbeef",
) -> dict:
    """Write one real, `FEATURE_ROW_SCHEMA`-shaped features-tier partition
    whose labels come from its own price path, through the real
    writer/manifest-issuer pair against `tmp_path`-derived roots. Returns
    the same keys `harness_span.build_span_partition` returns
    (`manifest_id`, `dataset`, `symbol`, `date`, `etime_min`, `etime_max`,
    `rows`).

    THE WALK IS `rows + 600` STEPS LONG AND ONLY `rows` ARE WRITTEN. Every
    written row therefore has a true future mid to build its FEATURES from,
    including the tail -- while its written LABEL is null wherever the
    future mid is not itself a written row. Without the overhang the tail
    rows would have to carry pure noise as features, and the null-label
    path would be exercised on rows no model could use.

    Knobs, each with the number it was set by (see the module docstring's
    table): `flat_fraction` aims the label's zero point mass at C3's
    measured 13.83%; `noise_scale` aims the three-feature R^2 at the
    0.013..0.048 the real train-internal splits measured, via
    `A / (A + noise_scale^2)`; `seed` fixes both, so every number this
    fixture produces is reproducible to the bit.
    """
    # A catalogue addition must fail HERE, naming itself, rather than as a
    # polars ColumnNotFound forty lines down.
    if set(FEATURE_SIGNAL_COEFFICIENTS) | {"mid"} != set(FEATURE_COLUMNS):
        raise AssertionError(
            "build_model_span_partition: FEATURE_SIGNAL_COEFFICIENTS plus "
            f"'mid' is {sorted(set(FEATURE_SIGNAL_COEFFICIENTS) | {'mid'})}, "
            f"but the catalogue's FEATURE_COLUMNS is {sorted(FEATURE_COLUMNS)} "
            "-- give the new feature a signal coefficient (or 0.0 for pure "
            "noise) before this fixture can carry it"
        )

    horizons = {name: LABEL_HORIZON_NS[name] // step_ns for name in LABEL_COLUMNS}
    overhang = max(horizons.values())
    if overhang >= rows:
        raise ValueError(
            f"build_model_span_partition: rows={rows} is not more than the "
            f"longest label horizon in rows ({overhang}) -- every label "
            "column would be entirely null"
        )
    path_rows = rows + overhang

    rng = np.random.default_rng(seed)
    flat = rng.random(path_rows - 1) < flat_fraction
    direction = rng.integers(0, 2, size=path_rows - 1).astype(np.int64) * 2 - 1
    increments = np.where(flat, 0, direction).astype(np.int64)
    bid_ticks_path = BASE_BID_TICKS + np.concatenate(
        [np.zeros(1, dtype=np.int64), np.cumsum(increments)]
    )
    ask_ticks_path = bid_ticks_path + SPREAD_TICKS
    bid_path = _prices_from_ticks(bid_ticks_path)
    ask_path = _prices_from_ticks(ask_ticks_path)
    mid_path = (bid_path + ask_path) / 2.0

    # Labels: the forward simple return over each horizon, computed against
    # the overhanging path so it is defined on every written row, then
    # NULLED wherever the future mid is not itself a written row.
    row_index = np.arange(rows)
    forward: dict[str, np.ndarray] = {}
    written_label: dict[str, np.ndarray] = {}
    for name, h in horizons.items():
        future = mid_path[h : h + rows]
        here = mid_path[:rows]
        forward[name] = (future - here) / here
        written_label[name] = np.where(row_index + h < rows, forward[name], np.nan)

    # Features: the primary label, scaled to unit variance, plus noise.
    primary = forward[PRIMARY_LABEL]
    z = primary / primary.std()
    noise = rng.standard_normal((len(FEATURE_SIGNAL_COEFFICIENTS), rows))
    features = {
        name: coefficient * z + noise_scale * noise[i]
        for i, (name, coefficient) in enumerate(FEATURE_SIGNAL_COEFFICIENTS.items())
    }

    columns: dict[str, pl.Series] = {
        "etime": pl.Series("etime", start_ns + row_index * step_ns, dtype=pl.Int64),
        "decision_source_rank": pl.Series(
            "decision_source_rank", np.zeros(rows, dtype=np.int8), dtype=pl.Int8
        ),
        "decision_seq": pl.Series("decision_seq", row_index, dtype=pl.Int64),
        "bid_price": pl.Series("bid_price", bid_path[:rows], dtype=pl.Float64),
        "ask_price": pl.Series("ask_price", ask_path[:rows], dtype=pl.Float64),
        "mid": pl.Series("mid", mid_path[:rows], dtype=pl.Float64),
    }
    for name, values in features.items():
        columns[name] = pl.Series(name, values, dtype=pl.Float64)
    for name, values in written_label.items():
        # `write_feature_partition` would convert NaN to null itself; doing
        # it here means the in-memory frame this builder asserts over is
        # already the frame that gets written.
        columns[name] = pl.Series(name, values, dtype=pl.Float64).fill_nan(None)
    columns["warmup"] = pl.Series("warmup", np.zeros(rows, dtype=bool))
    columns["post_gap_warmup"] = pl.Series(
        "post_gap_warmup", np.zeros(rows, dtype=bool)
    )
    columns["schema_version"] = pl.Series(
        "schema_version",
        np.full(rows, FEATURE_SCHEMA_VERSION, dtype=np.int32),
        dtype=pl.Int32,
    )

    df = pl.DataFrame(columns).select(list(FEATURE_ROW_SCHEMA))
    _assert_partition_invariants(
        df,
        bid_ticks=bid_ticks_path[:rows],
        ask_ticks=ask_ticks_path[:rows],
        step_ns=step_ns,
        rows=rows,
    )

    partition_entry = write_feature_partition(
        df, lake_root=lake_root, symbol=symbol, date=date, registry_root=registry_root
    )
    manifest = issue_feature_manifest(
        symbol=symbol,
        date=date,
        partition_entry=partition_entry,
        curated_manifests=[],
        code_hash=code_hash,
        registry_root=registry_root,
    )
    # Without an "ok" DQ report row for THIS manifest, every read through
    # the harness accessor raises `DQPauseError` on a perfectly healthy
    # synthetic fixture -- `store._enforce_dq_pause` requires one
    # unconditionally (the same discovery `harness_span.py` records).
    write_dq_report(
        lake_root,
        date,
        [("features", manifest["manifest_id"], "ok")],
        symbol=symbol,
    )
    return {
        "manifest_id": manifest["manifest_id"],
        "dataset": manifest["dataset"],
        "symbol": symbol,
        "date": date,
        "etime_min": partition_entry["etime_min"],
        "etime_max": partition_entry["etime_max"],
        "rows": rows,
    }
