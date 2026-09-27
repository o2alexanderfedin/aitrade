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
from harness.purge_embargo import FOLD_EMBARGO_NS, PURGE_HORIZON_NS
from harness.segments import issue_segment_manifest
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


#: The `train` entry's width in ROWS (one row per `step_ns`). 18,000 at a
#: 1 s step gives five 3,600 s OOF blocks -- see
#: `_assert_fixture_geometry_is_not_starved` for why 3,600 s is the floor.
FIXTURE_TRAIN_ROWS: int = 18_000

#: The number of inner OOF blocks (D-05-08). Five, as the real manifest
#: uses, so the fixture exercises the same derivation.
FIXTURE_K: int = 5

#: Deliberately generous, and the reason matters: a fixture manifest lives
#: in a `tmp_path` that is discarded when the test ends, so the only thing a
#: tight allowance could ever do here is make a test that materializes the
#: same segment twice fail for a BUDGET reason instead of the reason it was
#: written to test. The real manifest's allowance is 3 and is counted
#: (D-07-03); this number is not a precedent for it.
FIXTURE_BUDGET_ALLOWANCE: int = 50

FIXTURE_ADMISSION: dict = {
    "policy": "stale_book",
    "max_age_ns": None,
    "exclude_undefined_age": True,
    "counts": {},
}

FIXTURE_FOLD_CONFIG_REASON: str = (
    "test fixture (07-03): compressed_3seg over one learnable model_span "
    "partition. compressed_3seg rather than 5seg because declaring a real "
    "held_out window is Phase 8's own success criterion, so held_out is the "
    "zero-width D-05-16 sentinel; the five inner OOF blocks are what the "
    "model-selection tests read."
)


def _assert_fixture_geometry_is_not_starved(
    *, train_start_ns: int, train_end_ns: int, k: int, step_ns: int
) -> dict:
    """Derive -- never assert against a magic number -- the row budget each
    OOF block's own training set is left with, and refuse a geometry that
    `harness.segments._refuse_starved_oof_blocks` would refuse.

    `harness.kfold.training_rows_for_block` excludes, for target block `j`,
    the two-sided purge band `(start_j - purge, end_j + purge)` plus the
    trailing embargo -- a width of `block + 2*purge + embargo` out of the
    whole train range. With `PURGE_HORIZON_NS` 600 s and `FOLD_EMBARGO_NS`
    1 s that is the block's own width plus 1,201 s, whatever the block's
    width happens to be.
    """
    train_ns = train_end_ns - train_start_ns
    block_ns = train_ns // k
    excluded_ns = block_ns + 2 * PURGE_HORIZON_NS + FOLD_EMBARGO_NS
    surviving_ns = train_ns - excluded_ns
    if surviving_ns <= 0:
        raise AssertionError(
            f"model_span fixture geometry is starved: a {train_ns} ns train "
            f"entry split k={k} gives {block_ns} ns blocks, each excluding "
            f"{excluded_ns} ns (its own width plus 2*{PURGE_HORIZON_NS} ns "
            f"purge plus {FOLD_EMBARGO_NS} ns embargo), leaving "
            f"{surviving_ns} ns -- issue_segment_manifest would refuse it. "
            "Remedy: a smaller k, or a wider train entry."
        )
    if block_ns < step_ns:
        raise AssertionError(
            f"model_span fixture geometry: block width {block_ns} ns is "
            f"narrower than one row ({step_ns} ns), so a block would receive "
            "no rows at all"
        )
    return {
        "train_ns": train_ns,
        "block_ns": block_ns,
        "excluded_ns": excluded_ns,
        "surviving_ns": surviving_ns,
        "surviving_rows": surviving_ns // step_ns,
    }


def build_model_span_fixture(
    lake_root: Path,
    registry_root: Path,
    tracking_root: Path | str,
    *,
    symbol: str = SYMBOL,
    date: str = DEFAULT_DATE,
    start_ns: int = 0,
    step_ns: int = NS_PER_SECOND,
    rows: int = DEFAULT_ROWS,
    train_rows: int = FIXTURE_TRAIN_ROWS,
    k: int = FIXTURE_K,
    seed: int = DEFAULT_SEED,
    flat_fraction: float = DEFAULT_FLAT_FRACTION,
    noise_scale: float = DEFAULT_NOISE_SCALE,
    code_hash: str = "deadbeef",
) -> dict:
    """One `build_model_span_partition` plus a REAL `compressed_3seg`
    segment manifest over it, issued through
    `harness.segments.issue_segment_manifest` -- so the five `oof_block_*`
    entries come from the real derivation (`harness.kfold`), the admission
    counts and purge/embargo fields are measured against the real written
    partition, and nothing in the body is hand-written.

    Geometry, all of it derived from `rows`/`train_rows`/`k`:

    - `covered_end_ns = start_ns + (rows - 1) * step_ns` -- the LAST ROW's
      own etime, which is what `_covered_range` reads out of the partition
      entry.
    - `train  = [start_ns, start_ns + train_rows * step_ns)`
    - `val    = [train_end, covered_end_ns)` -- ending AT the covered end,
      the largest range `_validate_segments`' coverage check allows.
    - `held_out = [covered_end_ns, covered_end_ns)` -- the zero-width
      D-05-16 sentinel, which is exempt from that upper bound.

    Returns `{"manifest", "span", "geometry"}`. Every caller passes
    `tmp_path`-derived roots; `tracking_root` must be an INITIALISED MLflow
    store (the `tracking_root` fixture in `tests/models/conftest.py`),
    because `issue_segment_manifest` consults the budget for exhausted
    windows before it writes.
    """
    if rows <= train_rows:
        raise AssertionError(
            f"model_span fixture: rows={rows} must exceed train_rows="
            f"{train_rows}, or the val entry is empty before any test runs"
        )
    span = build_model_span_partition(
        lake_root,
        registry_root,
        symbol=symbol,
        date=date,
        start_ns=start_ns,
        step_ns=step_ns,
        rows=rows,
        seed=seed,
        flat_fraction=flat_fraction,
        noise_scale=noise_scale,
        code_hash=code_hash,
    )

    covered_end_ns = start_ns + (rows - 1) * step_ns
    train_end_ns = start_ns + train_rows * step_ns
    segments = [
        {
            "name": "train",
            "role": "train",
            "start_ns": start_ns,
            "end_ns": train_end_ns,
        },
        {
            "name": "val",
            "role": "val",
            "start_ns": train_end_ns,
            "end_ns": covered_end_ns,
        },
        {
            "name": "held_out",
            "role": "held_out",
            "start_ns": covered_end_ns,
            "end_ns": covered_end_ns,
        },
    ]

    # Anti-vacuity, the `tests/harness/test_accessor.py::_build_fixture`
    # rule: the fixture's OWN physical span must be asserted to cover every
    # declared segment BEFORE any behavioural test relies on it, or a
    # too-short `rows` passes every `height > 0` check while a segment
    # silently receives nothing.
    if span["etime_min"] > start_ns or span["etime_max"] != covered_end_ns:
        raise AssertionError(
            f"model_span fixture: partition spans [{span['etime_min']}, "
            f"{span['etime_max']}] but the declared layout needs "
            f"[{start_ns}, {covered_end_ns}]"
        )
    if train_end_ns >= covered_end_ns:
        raise AssertionError(
            f"model_span fixture: train ends at {train_end_ns}, at or past "
            f"the covered end {covered_end_ns} -- val would be empty"
        )
    geometry = _assert_fixture_geometry_is_not_starved(
        train_start_ns=start_ns, train_end_ns=train_end_ns, k=k, step_ns=step_ns
    )

    manifest = issue_segment_manifest(
        layout="compressed_3seg",
        segments=segments,
        upstream_feature_manifest_ids=[span["manifest_id"]],
        admission=dict(FIXTURE_ADMISSION),
        errata_id=None,
        budget_allowance=FIXTURE_BUDGET_ALLOWANCE,
        fold_config_reason=FIXTURE_FOLD_CONFIG_REASON,
        symbol=symbol,
        version=1,
        code_hash=code_hash,
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        k=k,
    )
    geometry.update(
        {
            "start_ns": start_ns,
            "step_ns": step_ns,
            "rows": rows,
            "train_rows": train_rows,
            "train_end_ns": train_end_ns,
            "covered_end_ns": covered_end_ns,
            "k": k,
            "oof_block_names": tuple(f"oof_block_{j}" for j in range(k)),
        }
    )
    return {"manifest": manifest, "span": span, "geometry": geometry}
