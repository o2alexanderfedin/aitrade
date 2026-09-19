"""One `(symbol, date)` in, one manifest-addressed feature partition out --
and every refusal happening before the first byte.

The pipeline this file pins is an ORDER, not a set of steps: day-boundary
gate, holdout gate, curated loads (each with its own DQ pause), merge,
total-order assertion, kernel, labels, warm-up tags, write, stats,
manifest. Reordering any of the first four turns a refusal into a
half-written day, and a feature partition is write-once -- so a day that
should never have existed cannot be taken back, only superseded by an
audit trail saying it was built wrong.

Hermetic. Every test builds its own `tmp_path` lake and registry through
`tests/fixtures/feature_build.py`; nothing here touches the real lake on
the SSD (that is the plan's Task 2, which runs as a script, never as a
collected test).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from data.dq.feature_checks import (
    FEATURE_BUILD_STATS_KEYS,
    check_feature_asof_convention,
    check_feature_label_coverage,
    check_feature_quantization,
    check_feature_row_filters,
    check_feature_warmup,
    check_feature_window,
    feature_build_stats_path,
)
from data.dq.checks import load_dq_thresholds
from data.holdout import QuarantinedDateError
from data.store import DQPauseError, by_date_index_path, resolve_manifest
from data.time_ns import NS_PER_SECOND
from features.build import (
    build_features_day,
    build_features_range,
)
from features.labels import NextDayUnavailableError
from features.tier import (
    FEATURE_ROW_SCHEMA,
    feature_partition_path,
)
from tests.fixtures.feature_build import (
    DATE,
    DAY_START,
    NEXT_DATE,
    SYMBOL,
    quote_frame,
    seed_day,
    trade_frame,
    seed_two_days,
    write_dq_report,
    write_resync_windows,
)
from tests.fixtures.feature_tier import write_holdout_registry

FEATURE_CHECKS = (
    check_feature_row_filters,
    check_feature_label_coverage,
    check_feature_quantization,
    check_feature_warmup,
    check_feature_window,
    check_feature_asof_convention,
)


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "lake", tmp_path / "registry"


def _build(lake_root: Path, registry_root: Path, date: str = DATE, **kw) -> dict:
    return build_features_day(
        SYMBOL,
        date,
        lake_root=lake_root,
        registry_root=registry_root,
        code_hash="deadbeef",
        **kw,
    )


def _partition_dir(lake_root: Path, date: str = DATE) -> Path:
    return feature_partition_path(lake_root, SYMBOL, date).parent


def _written_frame(lake_root: Path, result: dict) -> pl.DataFrame:
    return pl.read_parquet(Path(lake_root) / result["partition_entry"]["path"])


# --------------------------------------------------------------------------
# (1) refusals happen before any write
# --------------------------------------------------------------------------


def test_pipeline_order_refusals_come_before_any_write(tmp_path: Path):
    """Four ways a build must refuse, and the assertion that makes the
    ORDER load-bearing: the date directory does not exist afterwards.

    Asserting only the exception would pass with every gate moved to the
    end -- the partition written, the manifest issued, and then a raise.
    """
    for case in ("quarantined_d", "quarantined_d1", "no_next_day", "dq_paused"):
        lake_root, registry_root = _roots(tmp_path / case)
        seed_two_days(lake_root, registry_root, seed_next_day=case != "no_next_day")

        if case == "quarantined_d":
            write_holdout_registry(registry_root, [DATE])
            expected = QuarantinedDateError
        elif case == "quarantined_d1":
            write_holdout_registry(registry_root, [NEXT_DATE])
            expected = QuarantinedDateError
        elif case == "no_next_day":
            expected = NextDayUnavailableError
        else:
            # D's bookTicker report row goes degraded with no committed
            # acknowledgement -- load_curated's own pause, hit at step 3.
            ids = json.loads(
                by_date_index_path(
                    registry_root, f"{SYMBOL}.bookTicker", SYMBOL, "bookTicker", DATE
                ).read_text()
            )["manifest_id"]
            trade_id = json.loads(
                by_date_index_path(
                    registry_root, f"{SYMBOL}.trade", SYMBOL, "trade", DATE
                ).read_text()
            )["manifest_id"]
            write_dq_report(
                lake_root,
                DATE,
                [("bookTicker", ids, "degraded"), ("trade", trade_id, "ok")],
            )
            expected = DQPauseError

        with pytest.raises(expected):
            _build(lake_root, registry_root)

        assert not _partition_dir(lake_root).exists(), (
            f"{case}: the refusal left a features date directory behind -- "
            "the gate ran after the write, not before it"
        )
        assert not by_date_index_path(
            registry_root, f"{SYMBOL}.features", SYMBOL, "features", DATE
        ).exists(), f"{case}: a features by-date pointer was issued despite the refusal"


# --------------------------------------------------------------------------
# (2) one row per distinct etime
# --------------------------------------------------------------------------


def test_decision_row_count_equals_distinct_etimes(tmp_path: Path):
    lake_root, registry_root = _roots(tmp_path)
    seed_two_days(lake_root, registry_root)
    result = _build(lake_root, registry_root)
    written = _written_frame(lake_root, result)

    merged_etimes = set()
    for stream in ("bookTicker", "trade"):
        path = (
            lake_root
            / f"curated/symbol={SYMBOL}/stream={stream}/date={DATE}/part-1.parquet"
        )
        df = pl.read_parquet(path)
        if stream == "trade":  # the NA placeholders never become events
            df = df.filter((pl.col("price") != 0.0) | (pl.col("qty") != 0.0))
        merged_etimes |= set(df["etime"].to_list())

    assert written.height == len(merged_etimes)
    assert result["build_stats"]["n_decision_rows"] == len(merged_etimes)
    assert written["etime"].n_unique() == written.height
    assert written["etime"].is_sorted()


# --------------------------------------------------------------------------
# (3) post_gap_warmup comes from the sidecar
# --------------------------------------------------------------------------


def test_post_gap_warmup_is_tagged_from_the_sidecar(tmp_path: Path):
    lake_root, registry_root = _roots(tmp_path)
    seed_two_days(lake_root, registry_root)
    gap_end = DAY_START + 3_600 * NS_PER_SECOND
    warmup_end = gap_end + 60 * NS_PER_SECOND
    write_resync_windows(lake_root, DATE, [(gap_end, warmup_end)])

    written = _written_frame(lake_root, _build(lake_root, registry_root))
    etime = written["etime"].to_numpy()
    inside = (etime >= gap_end) & (etime < warmup_end)
    tagged = written["post_gap_warmup"].to_numpy()

    assert inside.sum() > 0, "the fixture window must actually contain rows"
    assert np.array_equal(tagged, inside), (
        "post_gap_warmup must be true on exactly [gap_end_etime_approx, "
        "warmup_end_etime_approx) -- the interval is half-open"
    )


def test_a_date_with_no_sidecar_still_builds_with_every_tag_false(tmp_path: Path):
    lake_root, registry_root = _roots(tmp_path)
    seed_two_days(lake_root, registry_root)
    result = _build(lake_root, registry_root)
    written = _written_frame(lake_root, result)

    assert not written["post_gap_warmup"].any()
    assert result["build_stats"]["post_gap_warmup_rows"] == 0


def test_the_tag_follows_the_sidecar_rather_than_the_row(tmp_path: Path):
    """The tag is JOINED, not derived. Move the window and the column has
    to move with it; a `post_gap_warmup` recomputed from the row's own
    features would not notice."""
    first = DAY_START + 1_000 * NS_PER_SECOND
    second = DAY_START + 50_000 * NS_PER_SECOND
    tagged = {}
    for name, gap_end in (("early", first), ("late", second)):
        lake_root, registry_root = _roots(tmp_path / name)
        seed_two_days(lake_root, registry_root)
        write_resync_windows(lake_root, DATE, [(gap_end, gap_end + 30 * NS_PER_SECOND)])
        written = _written_frame(lake_root, _build(lake_root, registry_root))
        etime = written["etime"].to_numpy()
        tagged[name] = set(etime[written["post_gap_warmup"].to_numpy()].tolist())
        # independently recomputed from the sidecar the build was handed
        expected = set(
            etime[(etime >= gap_end) & (etime < gap_end + 30 * NS_PER_SECOND)].tolist()
        )
        assert tagged[name] == expected

    assert tagged["early"] and tagged["late"]
    assert tagged["early"].isdisjoint(tagged["late"]), (
        "perturbing the sidecar must move the tagged rows; if it does not, "
        "the column is being derived from the row rather than joined"
    )


# --------------------------------------------------------------------------
# (4) warmup and post_gap_warmup are independent
# --------------------------------------------------------------------------


def test_warmup_and_post_gap_warmup_are_two_independent_flags(tmp_path: Path):
    """All four combinations occur, and neither flag overwrites the other.

    `warmup` is the kernel's own "state not filled yet" at the START of
    the partition -- the first second of rows, here. `post_gap_warmup` is
    the sidecar's "capture had just resumed". The resync window is placed
    to OVERLAP that first second without covering it: opening half a
    second in and closing five seconds in leaves the partition's first
    quote warm-up-only, the trade at +0.5 s both, the rows out to +5 s
    post-gap-only, and everything after neither.
    """
    lake_root, registry_root = _roots(tmp_path)
    seed_two_days(lake_root, registry_root)
    write_resync_windows(
        lake_root,
        DATE,
        [(DAY_START + NS_PER_SECOND // 2, DAY_START + 5 * NS_PER_SECOND)],
    )
    written = _written_frame(lake_root, _build(lake_root, registry_root))

    warmup = written["warmup"].to_numpy()
    post_gap = written["post_gap_warmup"].to_numpy()
    combos = {(bool(w), bool(p)) for w, p in zip(warmup, post_gap)}
    assert combos == {(True, True), (True, False), (False, True), (False, False)}, (
        f"expected all four (warmup, post_gap_warmup) combinations, got {combos}"
    )


# --------------------------------------------------------------------------
# (5) the stats contract with the DQ checks
# --------------------------------------------------------------------------


def test_build_stats_carry_every_key_the_dq_checks_read(tmp_path: Path):
    lake_root, registry_root = _roots(tmp_path)
    seed_two_days(lake_root, registry_root, na_rows=3, unknown_rows=2)
    result = _build(lake_root, registry_root)

    persisted = json.loads(
        feature_build_stats_path(lake_root, SYMBOL, DATE).read_text()
    )
    missing = FEATURE_BUILD_STATS_KEYS - set(persisted)
    assert not missing, (
        f"build_stats.json is missing {sorted(missing)} -- the checks that "
        "read them would report `failed`, and the day would pause for a "
        "reason nobody could see from the report"
    )

    thresholds = load_dq_thresholds()
    rows = [check(persisted, thresholds) for check in FEATURE_CHECKS]
    failed = [r for r in rows if r["dq_status"] == "failed"]
    assert not failed, f"a clean synthetic day produced failed rows: {failed}"

    # The two counted row classes are the fixture's, not zero-by-accident.
    assert persisted["na_placeholder_excluded"] == 3
    assert persisted["unknown_side_rows"] == 2
    assert persisted["manifest_id"] == result["manifest"]["manifest_id"]
    assert persisted["partition_sha256"] == result["partition_entry"]["sha256"]


# --------------------------------------------------------------------------
# (6) write-once, all the way through
# --------------------------------------------------------------------------


def test_rebuilding_the_same_date_refuses(tmp_path: Path):
    lake_root, registry_root = _roots(tmp_path)
    seed_two_days(lake_root, registry_root)
    first = _build(lake_root, registry_root)

    with pytest.raises(FileExistsError):
        _build(lake_root, registry_root)

    resolved = resolve_manifest(
        first["manifest"]["manifest_id"],
        f"{SYMBOL}.features",
        registry_root=registry_root,
        lake_root=lake_root,
        expected_tier="features",
    )
    assert resolved["partitions"][0]["sha256"] == first["partition_entry"]["sha256"]
    assert (
        json.loads(
            by_date_index_path(
                registry_root, f"{SYMBOL}.features", SYMBOL, "features", DATE
            ).read_text()
        )["manifest_id"]
        == first["manifest"]["manifest_id"]
    )


# --------------------------------------------------------------------------
# (7) NULL, never NaN
# --------------------------------------------------------------------------


def test_undefined_values_are_null_not_nan_in_the_written_partition(tmp_path: Path):
    """A day whose first rows are trades with no quote yet: `mid`,
    `imb_top` and `ofi` are undefined there, and the partition must say so
    with NULLs rather than with NaNs that survive every aggregation."""
    lake_root, registry_root = _roots(tmp_path)
    quote_etimes = [DAY_START + (600 + i) * NS_PER_SECOND for i in range(85_800)]
    trade_etimes = [DAY_START + i * NS_PER_SECOND for i in range(30)]
    seed_two_days(
        lake_root,
        registry_root,
        quote_etimes=quote_etimes,
        trade_etimes=trade_etimes,
        na_rows=0,
        unknown_rows=0,
    )
    written = _written_frame(lake_root, _build(lake_root, registry_root))

    float_columns = [n for n, d in FEATURE_ROW_SCHEMA.items() if d == pl.Float64]
    for name in float_columns:
        assert written[name].is_nan().sum() == 0, f"{name} carries NaN"
    assert written["mid"].null_count() == 30, (
        "the 30 trade rows before the first quote have no mid, and must be "
        "NULL rather than NaN or a carried-forward zero"
    )
    assert written["imb_top"].null_count() == 30
    # `ofi` is additionally undefined for the FIRST quote (there is no
    # previous book to difference against), hence 31 rather than 30.
    assert written["ofi"].null_count() == 31
    assert written["trade_flow"].null_count() == 0


# --------------------------------------------------------------------------
# build_features_range
# --------------------------------------------------------------------------


def test_range_skips_a_built_day_and_a_day_with_no_successor(tmp_path: Path):
    lake_root, registry_root = _roots(tmp_path)
    seed_two_days(lake_root, registry_root)
    # D+1 exists as curated L1 but D+2 does not, so D+1 is itself
    # unbuildable -- the "never the most recent day" rule, from the range.
    statuses = {
        r["date"]: r
        for r in build_features_range(
            SYMBOL,
            DATE,
            NEXT_DATE,
            lake_root=lake_root,
            registry_root=registry_root,
            code_hash="deadbeef",
        )
    }
    assert statuses[DATE]["status"] == "written"
    assert statuses[NEXT_DATE]["status"] == "skipped"
    assert statuses[NEXT_DATE]["reason"] == "no next day"

    again = build_features_range(
        SYMBOL,
        DATE,
        DATE,
        lake_root=lake_root,
        registry_root=registry_root,
        code_hash="deadbeef",
    )
    assert again[0]["status"] == "already_present"
    assert again[0]["manifest_id"] == statuses[DATE]["manifest_id"]


def test_range_propagates_a_refusal_that_is_not_a_missing_successor(tmp_path: Path):
    lake_root, registry_root = _roots(tmp_path)
    seed_two_days(lake_root, registry_root)
    write_holdout_registry(registry_root, [DATE])
    with pytest.raises(QuarantinedDateError):
        build_features_range(
            SYMBOL,
            DATE,
            DATE,
            lake_root=lake_root,
            registry_root=registry_root,
            code_hash="deadbeef",
        )


def test_a_days_manifest_names_the_curated_manifests_it_was_built_from(
    tmp_path: Path,
):
    """Provenance is recoverable from the features manifest alone -- both
    of D's curated inputs and, distinctly, D+1's label tail."""
    lake_root, registry_root = _roots(tmp_path)
    ids = seed_two_days(lake_root, registry_root)
    result = _build(lake_root, registry_root)

    roles = {i["role"]: i["manifest_id"] for i in result["manifest"]["inputs"]}
    assert roles["l1_day"] == ids["d"]["bookTicker"]
    assert roles["trade_day"] == ids["d"]["trade"]
    assert roles["l1_label_tail"] == ids["d1"]["bookTicker"]
    assert result["build_stats"]["next_day_manifest_id"] == ids["d1"]["bookTicker"]
    assert result["build_stats"]["curated_manifest_ids"] == {
        "bookTicker": ids["d"]["bookTicker"],
        "trade": ids["d"]["trade"],
    }


def test_the_total_order_gate_is_a_runtime_refusal_not_only_a_test(tmp_path: Path):
    """Two individually-sorted curated partitions whose MERGE is
    ambiguous: a duplicated `(etime, seq)` pair inside the L1 stream.

    `merge_sorted` accepts it -- it only ever compares `etime` -- and the
    kernel then counts that quote twice in `ofi`. Nothing downstream can
    tell. `assert_strict_total_order` is the only thing that can.
    """
    lake_root, registry_root = _roots(tmp_path)
    seed_two_days(lake_root, registry_root)
    duplicated = quote_frame(
        [DAY_START, DAY_START, DAY_START + NS_PER_SECOND], [100.0, 100.1, 100.2]
    ).with_columns(pl.Series("seq", [0, 0, 1], dtype=pl.Int64))
    lake2, registry2 = _roots(tmp_path / "dup")
    seed_day(
        lake2,
        registry2,
        DATE,
        quotes=duplicated,
        trades=trade_frame([DAY_START], [100.0], [1 / 1e8], [1]),
    )
    seed_day(
        lake2,
        registry2,
        NEXT_DATE,
        quotes=quote_frame([DAY_START + 86_400 * NS_PER_SECOND], [100.0]),
    )

    with pytest.raises(ValueError, match="strict"):
        _build(lake2, registry2)
    assert not _partition_dir(lake2).exists()


# --------------------------------------------------------------------------
# (8) the build IS features.api -- 04-VERIFICATION.md gap A
# --------------------------------------------------------------------------


def test_the_built_partition_matches_features_api_bit_for_bit(tmp_path: Path):
    """The build's feature columns are `features.api`'s, exactly.

    THE HOLE THIS CLOSES. `build_features_day` used to drive
    `run_kernel_checked` + `decision_row_index` itself, and nothing
    mechanical compared it with `features.api` -- the guardrail sanctioned
    the whole `features/` directory, `tests/features/test_build.py`
    contained zero references to `api`, and
    `test_three_call_sites_are_byte_identical` compared three call sites
    that were all inside `api.py`. A deliberately divergent second caller
    inside the package passed every gate.

    NaN-for-NaN, because `np.nan != np.nan` would make a naive equality
    check pass on a column that is entirely undefined.
    """
    from features.api import FEATURE_PASS_SCHEMA, for_training
    from features.event_stream import merge_curated_streams
    from features.tier import FEATURE_COLUMNS

    lake_root, registry_root = _roots(tmp_path)
    seed_two_days(lake_root, registry_root)
    result = _build(lake_root, registry_root)
    written = _written_frame(lake_root, result)

    l1 = pl.read_parquet(
        lake_root / f"curated/symbol={SYMBOL}/stream=bookTicker/date={DATE}"
    )
    trades = pl.read_parquet(
        lake_root / f"curated/symbol={SYMBOL}/stream=trade/date={DATE}"
    )
    merged, _stats = merge_curated_streams(l1, trades)
    through_api = for_training(merged).frame

    assert through_api.height == written.height
    for name in FEATURE_PASS_SCHEMA:
        left = through_api[name].to_numpy()
        right = written[name].to_numpy()
        if name in FEATURE_COLUMNS:
            both_nan = np.isnan(left) & np.isnan(right)
            assert np.array_equal(left[~both_nan], right[~both_nan]), name
            assert (
                both_nan.sum()
                == int(np.isnan(left).sum())
                == int(np.isnan(right).sum())
            ), name
        else:
            assert np.array_equal(left, right), name

    # Anti-vacuity: a comparison over zero rows, or over columns that are
    # entirely null, proves nothing.
    assert written.height > 1_000
    for name in FEATURE_COLUMNS:
        assert int(np.isnan(through_api[name].to_numpy()).sum()) < written.height, name


def test_the_build_does_not_import_the_kernel(tmp_path: Path):
    """The static half of the same claim, run over the two modules the
    guardrail used to sanction wholesale."""
    from tools.check_single_feature_path import SANCTIONED_FILES, scan_source

    root = Path(__file__).resolve().parents[2]
    for relative in ("features/build.py", "features/labels.py"):
        assert relative not in SANCTIONED_FILES, (
            f"{relative} must not be sanctioned -- that is the hole gap A names"
        )
        violations = scan_source((root / relative).read_text(), relative)
        assert violations == [], [str(v) for v in violations]


# --------------------------------------------------------------------------
# (9) a mid-build crash does not wedge the date or abort the range (WR-01)
# --------------------------------------------------------------------------


def _seed_three_days(lake_root: Path, registry_root: Path) -> None:
    """D and D+1 both buildable: D+1 carries both streams and D+2 exists
    as curated L1, so the day-boundary rule lets the first two through."""
    from tests.fixtures.feature_build import NEXT_DAY_START, THIRD_DATE

    seed_two_days(lake_root, registry_root, seed_next_day=False)
    mids = [100.0 + 0.1 * (i % 50) for i in range(1_200)]
    next_etimes = [NEXT_DAY_START + i * NS_PER_SECOND for i in range(1_200)]
    seed_day(
        lake_root,
        registry_root,
        NEXT_DATE,
        quotes=quote_frame(next_etimes, mids),
        trades=trade_frame(
            [e + NS_PER_SECOND // 2 for e in next_etimes[:100]],
            [100.0] * 100,
            [(i + 1) / 1e8 for i in range(100)],
            [1 if i % 2 else -1 for i in range(100)],
        ),
    )
    third_start = NEXT_DAY_START + 86_400 * NS_PER_SECOND
    third = [third_start + i * NS_PER_SECOND for i in range(1_200)]
    seed_day(lake_root, registry_root, THIRD_DATE, quotes=quote_frame(third, mids))


def test_a_crash_between_the_write_and_the_manifest_is_recovered_automatically(
    tmp_path: Path, monkeypatch
):
    """A build that dies after the partition write leaves a `partial-`
    file, not a `part-` one -- and the next build removes it and rebuilds.

    The old shape wrote the final `part-<ns>.parquet` at step 8 and the
    manifest at step 10, so anything in between (a full disk on
    `build_stats.json`, a SIGKILL) left a valid-looking orphan with no
    manifest. `_already_built` saw no pointer and retried;
    `write_feature_partition`'s write-once glob then refused FOREVER, and
    the repair was a human deleting a file from a write-once tier -- the
    one operation the tier exists to forbid.
    """
    import features.build as build_module

    lake_root, registry_root = _roots(tmp_path)
    seed_two_days(lake_root, registry_root)

    def die(*args, **kwargs):
        raise AssertionError("simulated crash after the write")

    monkeypatch.setattr(build_module, "_build_stats", die)
    with pytest.raises(AssertionError, match="simulated crash"):
        _build(lake_root, registry_root)

    date_dir = _partition_dir(lake_root)
    assert sorted(p.name[:8] for p in date_dir.glob("*.parquet")) == ["partial-"], (
        "the orphan must be CLEARLY NAMED -- never a file a reader could "
        "mistake for a committed partition"
    )
    assert not by_date_index_path(
        registry_root, f"{SYMBOL}.features", SYMBOL, "features", DATE
    ).exists()

    monkeypatch.undo()
    result = _build(lake_root, registry_root)
    assert result["manifest"]["manifest_id"]
    assert [p.name[:5] for p in sorted(date_dir.glob("*.parquet"))] == ["part-"], (
        "exactly one committed partition, and no leftover staging file"
    )


def test_an_orphan_part_file_reports_the_date_and_does_not_abort_the_range(
    tmp_path: Path, monkeypatch
):
    """The remaining window is the rename-to-manifest one. If a build dies
    there, the date is genuinely wedged -- but ONE wedged day silently
    truncating a week of builds is the failure that looks most like
    success, so the range reports it and carries on.
    """
    import features.build as build_module
    from tests.fixtures.feature_build import THIRD_DATE

    lake_root, registry_root = _roots(tmp_path)
    _seed_three_days(lake_root, registry_root)

    def die(*args, **kwargs):
        raise RuntimeError("simulated crash after the rename")

    monkeypatch.setattr(build_module, "issue_feature_manifest", die)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _build(lake_root, registry_root)
    monkeypatch.undo()

    assert [p.name[:5] for p in _partition_dir(lake_root).glob("*.parquet")] == [
        "part-"
    ]

    statuses = {
        r["date"]: r
        for r in build_features_range(
            SYMBOL,
            DATE,
            THIRD_DATE,
            lake_root=lake_root,
            registry_root=registry_root,
            code_hash="deadbeef",
        )
    }
    assert statuses[DATE]["status"] == "orphaned"
    assert "no manifest" in statuses[DATE]["reason"]
    assert statuses[NEXT_DATE]["status"] == "written", (
        "the later dates must still be attempted -- one wedged day must not "
        "truncate the range"
    )
    assert statuses[THIRD_DATE]["status"] == "skipped"
