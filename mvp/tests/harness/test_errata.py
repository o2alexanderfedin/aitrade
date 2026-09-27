"""Tests for harness.errata: D-05-20's 249-cell null-masking gate.
`mask_errata_cells`'s shape/join-key behaviour is tested against a
synthetic frame (no lake needed); `compute_errata_cells`'s DIFF/shape
contract is proven end to end against a small, hermetic `tmp_path`
curated fixture carrying one DELIBERATELY fabricated zero (mirroring the
real historical bug this function exists to find). The real, measured
249-cell reproduction against the actual three-day lake is a standalone
script, not a collected test (per this plan's own constraints) -- see
05-04-SUMMARY.md for its transcript."""

from __future__ import annotations

from pathlib import Path

import numpy as np

import polars as pl

from data.dq.checks import load_dq_thresholds
from features.api import for_build
from features.event_stream import assert_strict_total_order, merge_curated_streams
from features.labels import (
    append_next_day_quotes,
    compute_labels,
    next_day_quote_series,
)
from features.tier import (
    FEATURE_COLUMNS,
    FEATURE_ROW_SCHEMA,
    FEATURE_SCHEMA_VERSION,
    LABEL_COLUMNS,
    issue_feature_manifest,
    write_feature_partition,
)
from harness.errata import compute_errata_cells, mask_errata_cells
from tests.fixtures.feature_build import DATE, DAY_START, SYMBOL, seed_two_days

NS_PER_SECOND = 1_000_000_000


def _labelled_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "etime": [100, 100, 200, 300, 300],
            "decision_seq": [0, 1, 0, 0, 1],
            "ret_1s_mid": [0.1, 0.2, 0.3, 0.4, 0.5],
            "ret_10s_mid": [1.1, 1.2, 1.3, 1.4, 1.5],
        }
    )


def test_mask_errata_cells_nulls_only_named_cells():
    df = _labelled_frame()
    cells = [
        {
            "date": "2026-09-12",
            "etime": 100,
            "decision_seq": 1,
            "label_column": "ret_1s_mid",
        }
    ]

    out = mask_errata_cells(df, cells)

    hit = out.filter((pl.col("etime") == 100) & (pl.col("decision_seq") == 1))
    assert hit["ret_1s_mid"].to_list() == [None]
    # The OTHER column on the same (etime, decision_seq) is untouched.
    assert hit["ret_10s_mid"].to_list() == [1.2]

    unchanged = out.filter(~((pl.col("etime") == 100) & (pl.col("decision_seq") == 1)))
    expected_unchanged = df.filter(
        ~((pl.col("etime") == 100) & (pl.col("decision_seq") == 1))
    )
    assert unchanged.sort(["etime", "decision_seq"]).equals(
        expected_unchanged.sort(["etime", "decision_seq"])
    )


def test_mask_errata_cells_empty_list_is_a_no_op():
    df = _labelled_frame()
    out = mask_errata_cells(df, [])
    assert out.equals(df)


def _decision_rows(rows: int = 2_000) -> pl.DataFrame:
    """A decision-row-shaped frame: `etime` strictly ascending and globally
    unique, which is what the last-row-per-`etime` rule produces on the real
    pool (60,926,503 distinct values in 60,926,503 rows, correction C1).
    `_labelled_frame` above deliberately repeats etimes to exercise the join
    KEY; this one exercises the join's ORDER, and needs enough rows that a
    reordering would be a real reordering."""
    return pl.DataFrame(
        {
            "etime": pl.Series(
                "etime",
                [1_000_000_000 + i * 1_000 for i in range(rows)],
                dtype=pl.Int64,
            ),
            "decision_seq": pl.Series("decision_seq", range(rows), dtype=pl.Int64),
            "ret_1s_mid": pl.Series(
                "ret_1s_mid", [0.001 * i for i in range(rows)], dtype=pl.Float64
            ),
            "ret_10s_mid": pl.Series(
                "ret_10s_mid", [0.002 * i for i in range(rows)], dtype=pl.Float64
            ),
        }
    )


def test_mask_errata_cells_preserves_the_input_etime_sequence():
    """A REGRESSION GUARD, NOT A DEMONSTRATION -- and the distinction is the
    honest half of this test.

    polars 1.41.2 documents join output order as unspecified by default
    (`maintain_order=None` resolves to `"none"`: "the ordering might differ
    across Polars versions or even between different runs"), and this function
    joins on EVERY look-role frame on its way out of
    `harness.accessor.materialize`. Phase 7 is the first consumer for which
    that order is load-bearing: a prediction array is positional against its
    decision rows, so a silent reorder misattributes every prediction and
    nothing fails (D-07-31).

    The order is preserved today, so THIS TEST PASSED BEFORE
    `maintain_order="left"` was added as well as after. It does not
    demonstrate that the one-liner works; it is what will fail if a future
    polars release changes the default, or if someone removes the argument.

    Two things it does make sensitive rather than decorative:

    * the cells are REAL HITS across BOTH label columns, so the function
      performs two sequential joins rather than returning early;
    * the cell list is built in a SCRAMBLED order, so an implementation that
      followed the right-hand frame's order would produce a visibly different
      sequence rather than accidentally agreeing with the left's.
    """
    df = _decision_rows()
    before_etime = df["etime"].to_numpy().copy()
    before_seq = df["decision_seq"].to_numpy().copy()

    # Scrambled on purpose (see the docstring): descending, interleaved across
    # the two columns, and not aligned with either frame's row order.
    hit_rows = list(range(1_900, 100, -37))
    cells = [
        {
            "date": "2026-09-12",
            "etime": int(df["etime"][row]),
            "decision_seq": int(df["decision_seq"][row]),
            "label_column": "ret_1s_mid" if index % 2 else "ret_10s_mid",
        }
        for index, row in enumerate(hit_rows)
    ]

    out = mask_errata_cells(df, cells)

    assert np.array_equal(out["etime"].to_numpy(), before_etime), (
        "mask_errata_cells reordered the frame's etime sequence -- every "
        "prediction downstream is positional against these rows"
    )
    assert np.array_equal(out["decision_seq"].to_numpy(), before_seq)
    assert out.height == df.height

    # Anti-vacuity: the join really did null cells in both columns, so the
    # order above was preserved ACROSS two joins and not across two no-ops.
    nulled = {
        column: out[column].null_count() for column in ("ret_1s_mid", "ret_10s_mid")
    }
    assert nulled["ret_1s_mid"] > 0 and nulled["ret_10s_mid"] > 0, nulled
    assert nulled["ret_1s_mid"] + nulled["ret_10s_mid"] == len(hit_rows)
    # ...and the values that were NOT hit are still in their own rows, which is
    # the property a reorder would break without changing any null count.
    untouched = [row for row in range(df.height) if row not in set(hit_rows)]
    assert out["ret_10s_mid"][untouched[-1]] == df["ret_10s_mid"][untouched[-1]]


def _build_ground_truth_frame(lake_root: Path, registry_root: Path) -> pl.DataFrame:
    """Independently recompute D's decision rows through the same
    sanctioned path `compute_errata_cells` uses -- `features.api.for_build`
    + `features.labels.compute_labels`, against the curated fixture
    `seed_two_days` just wrote. Used ONLY to construct this test's
    deliberately-corrupted committed partition below, never compared
    against `compute_errata_cells`'s own internals directly (that
    comparison IS the test)."""
    from data.store import by_date_index_path, load_curated
    import json

    def _curated(stream: str, date: str) -> pl.DataFrame:
        idx = by_date_index_path(
            registry_root, f"{SYMBOL}.{stream}", SYMBOL, stream, date
        )
        manifest_id = json.loads(idx.read_text())["manifest_id"]
        return load_curated(
            manifest_id,
            f"{SYMBOL}.{stream}",
            registry_root=registry_root,
            lake_root=lake_root,
        )

    l1_df = _curated("bookTicker", DATE)
    trade_df = _curated("trade", DATE)
    merged, _ = merge_curated_streams(l1_df, trade_df)
    assert_strict_total_order(merged)
    built = for_build(merged)

    next_etime, next_mid = next_day_quote_series(
        SYMBOL, DATE, registry_root=registry_root, lake_root=lake_root
    )
    quote_etime, quote_mid = append_next_day_quotes(
        built.quote_etime, built.quote_mid, next_etime, next_mid
    )
    labels, _ = compute_labels(
        built.frame["etime"].to_numpy(),
        built.frame["mid"].to_numpy(),
        quote_etime,
        quote_mid,
        gap_threshold_ns=load_dq_thresholds().label_gap.max_quote_gap_ns,
    )

    n = built.frame.height
    columns = {
        "etime": built.frame["etime"],
        "decision_source_rank": built.frame["decision_source_rank"],
        "decision_seq": built.frame["decision_seq"],
        "bid_price": built.frame["bid_price"],
        "ask_price": built.frame["ask_price"],
        **{name: built.frame[name] for name in FEATURE_COLUMNS},
        **{name: pl.Series(labels[name], dtype=pl.Float64) for name in LABEL_COLUMNS},
        "warmup": built.frame["warmup"],
        "post_gap_warmup": pl.Series([False] * n, dtype=pl.Boolean),
        "schema_version": pl.Series([FEATURE_SCHEMA_VERSION] * n, dtype=pl.Int32),
    }
    return pl.DataFrame(
        {name: columns[name] for name in FEATURE_ROW_SCHEMA},
        schema=dict(FEATURE_ROW_SCHEMA),
    )


def test_compute_errata_cells_shape(tmp_path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"

    # A deliberate >30s silence (the DQ label-gap threshold is 30s) in the
    # middle of the day, so at least one decision row's ret_1s_mid/
    # ret_10s_mid horizon genuinely lands inside a real quote gap and is
    # null under the CURRENT (fixed, `null_stale`) rule -- not a vacuous
    # fixture where every label happens to resolve.
    quote_etimes = [DAY_START + i * NS_PER_SECOND for i in range(50)] + [
        DAY_START + (100 + i) * NS_PER_SECOND for i in range(50)
    ]
    seed_two_days(lake_root, registry_root, quote_etimes=quote_etimes)

    ground_truth = _build_ground_truth_frame(lake_root, registry_root)
    # NaN -> null, exactly as features.tier.write_feature_partition does.
    ground_truth = ground_truth.with_columns(
        [pl.col(name).fill_nan(None) for name in LABEL_COLUMNS]
    )

    null_rows = ground_truth.filter(pl.col("ret_1s_mid").is_null())
    assert null_rows.height > 0, (
        "fixture did not produce a genuine null_gap ret_1s_mid label -- "
        "nothing for this test to fabricate a historical zero out of"
    )
    corrupt_etime = int(null_rows["etime"][0])
    corrupt_seq = int(null_rows["decision_seq"][0])

    # THE DELIBERATE FABRICATION: exactly what the real 2026-09-19 bug did
    # -- a genuinely-null label written as a finite 0.0.
    corrupted = ground_truth.with_columns(
        pl.when(
            (pl.col("etime") == corrupt_etime) & (pl.col("decision_seq") == corrupt_seq)
        )
        .then(0.0)
        .otherwise(pl.col("ret_1s_mid"))
        .alias("ret_1s_mid")
    )

    partition_entry = write_feature_partition(
        corrupted,
        lake_root=lake_root,
        symbol=SYMBOL,
        date=DATE,
        registry_root=registry_root,
    )
    issue_feature_manifest(
        symbol=SYMBOL,
        date=DATE,
        partition_entry=partition_entry,
        curated_manifests=[],
        code_hash="deadbeef",
        registry_root=registry_root,
    )

    cells = compute_errata_cells(
        SYMBOL, [DATE], registry_root=registry_root, lake_root=lake_root
    )

    assert cells == [
        {
            "date": DATE,
            "etime": corrupt_etime,
            "decision_seq": corrupt_seq,
            "label_column": "ret_1s_mid",
        }
    ]
    for cell in cells:
        assert set(cell) == {"date", "etime", "decision_seq", "label_column"}
