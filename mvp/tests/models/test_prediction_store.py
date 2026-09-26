"""The storage contract of `models.predictions`: the tier, the path, the
write-once refusal, the manifest, and the loader's two gates.

WHAT IS DELIBERATELY NOT HERE. Row alignment and the end-to-end fit-to-P&L
slice live in `test_prediction_table.py`; the return-to-price conversion lives
in `test_neutral_pred.py`. This file never asks whether a `pred` value is
right, only whether the bytes that hold it can be written once, addressed by a
manifest, and refused when they should be.

THE RIG IS DELIBERATELY SMALLER THAN THE DEFAULT. `build_model_span_fixture`'s
default knobs exist so the fitted-model tests see a 1,799-row `val` with a
calibrated R^2; a storage test needs a segment manifest and a feature
partition with real dates, and nothing else. Pre-commit hook 19 runs the full
suite on every commit, so the rows a test does not need are rows every future
commit pays for. `rows`/`train_rows` are passed explicitly here; the geometry
guard inside the builder still refuses a starved layout.

Every test writes to its own `tmp_path` lake, registry and MLflow tracking
root via `tests/models/conftest.py`. No canonical root is reachable, so no
validation look is ever spent (D-07-34).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from data import store
from data.store import (
    BY_DATE_INDEXED_TIERS,
    CURATED_TIER,
    FEATURES_TIER,
    PREDICTIONS_TIER,
    by_date_index_path,
    dq_report_path,
    issue_manifest,
)
from data.holdout import QuarantinedDateError
from harness.segments import segment_manifest_path
from models.predictions import (
    PREDICTION_TABLE_SCHEMA,
    PREDICTIONS_SCHEMA_VERSION,
    SCORED_SEGMENT_INPUT_ROLE,
    assert_decision_order,
    load_prediction_table,
    partition_overlaps_segment,
    prediction_table_path,
    predictions_dataset,
    scored_segment_input,
    write_prediction_table,
)
from tests.fixtures.feature_tier import write_holdout_registry
from tests.fixtures.model_span import DEFAULT_DATE, SYMBOL, build_model_span_fixture
from tests.fixtures.prediction_span import (
    FIXTURE_PREDICTOR_ID,
    build_prediction_span,
    prediction_table_from_frame,
)

#: Small enough to be cheap, large enough that the builder's own geometry
#: guard passes: a 6,000 s train split k=5 leaves each OOF block's training
#: set 3,599 s after the two-sided purge band and the embargo.
SMALL_ROWS = 6_600
SMALL_TRAIN_ROWS = 6_000


def _rig(lake_root: Path, registry_root: Path, tracking_root: Path) -> dict:
    return build_model_span_fixture(
        lake_root,
        registry_root,
        tracking_root,
        rows=SMALL_ROWS,
        train_rows=SMALL_TRAIN_ROWS,
    )


def _val_shaped_frame(rig: dict, rows: int = 64) -> pl.DataFrame:
    """A frame with `rows` strictly-ascending etimes inside the rig's own
    `val` range, and the two columns a prediction table is emitted from.

    NOT an accessor look. A storage test's subject is the bytes and the
    manifest; materializing the real segment costs a third of a second per
    test and proves nothing this file asserts. `test_prediction_table.py`
    uses the real frame, where alignment IS the subject.
    """
    geometry = rig["geometry"]
    start = geometry["train_end_ns"]
    etime = start + np.arange(rows, dtype=np.int64) * geometry["step_ns"]
    return pl.DataFrame(
        {
            "etime": pl.Series("etime", etime, dtype=pl.Int64),
            "decision_seq": pl.Series(
                "decision_seq", np.arange(rows, dtype=np.int64), dtype=pl.Int64
            ),
        }
    )


def _write(rig: dict, lake_root: Path, registry_root: Path, **kwargs) -> dict:
    frame = kwargs.pop("frame", None)
    if frame is None:
        frame = _val_shaped_frame(rig)
    return build_prediction_span(
        lake_root,
        registry_root,
        frame=frame,
        segment_manifest_id=rig["manifest"]["manifest_id"],
        **kwargs,
    )


# --------------------------------------------------------------------------
# The tier itself
# --------------------------------------------------------------------------


def test_the_predictions_tier_is_not_date_indexed_and_did_not_widen_the_allowlist():
    """`BY_DATE_INDEXED_TIERS` grants a tier a date-addressable entry point
    that anything holding a date can follow, and its own comment calls adding
    a member "a deliberate, reviewed act". A prediction table belongs to a
    `(segment_manifest, segment, predictor)` triple (D-07-26), so it gets no
    such pointer -- and the allowlist is asserted whole, not merely checked
    for this tier's absence, because a future edit that adds some OTHER tier
    should have to come past a test."""
    assert PREDICTIONS_TIER == "predictions"
    assert PREDICTIONS_TIER not in BY_DATE_INDEXED_TIERS
    assert BY_DATE_INDEXED_TIERS == frozenset({CURATED_TIER, FEATURES_TIER})


def test_prediction_table_path_names_the_triple_and_keeps_the_part_ns_leaf(tmp_path):
    """The directory names say what the table belongs to; the `part-<ns>`
    leaf is what the write-once PARENT glob needs to have something to find.
    """
    path = prediction_table_path(
        tmp_path / "lake", SYMBOL, "a" * 64, "val", FIXTURE_PREDICTOR_ID
    )
    parts = path.relative_to(tmp_path / "lake").parts
    assert parts[:5] == (
        PREDICTIONS_TIER,
        f"symbol={SYMBOL}",
        f"segment_manifest={'a' * 64}",
        "segment=val",
        f"predictor={FIXTURE_PREDICTOR_ID[:16]}",
    )
    assert parts[5].startswith("part-") and parts[5].endswith(".parquet")


def test_prediction_table_path_refuses_a_short_predictor_id_and_a_path_shaped_name(
    tmp_path,
):
    """Both refusals exist because the path TRUNCATES the predictor id to 16
    characters: a 16-character argument would address a directory whose name
    is indistinguishable from the full id's, and a segment name carrying a
    separator would address a directory outside the triple entirely."""
    with pytest.raises(ValueError, match="predictor_id"):
        prediction_table_path(
            tmp_path, SYMBOL, "a" * 64, "val", FIXTURE_PREDICTOR_ID[:16]
        )
    with pytest.raises(ValueError, match="predictor_id"):
        prediction_table_path(tmp_path, SYMBOL, "a" * 64, "val", "A" * 64)
    with pytest.raises(ValueError, match="segment_name"):
        prediction_table_path(
            tmp_path, SYMBOL, "a" * 64, "../held_out", FIXTURE_PREDICTOR_ID
        )


# --------------------------------------------------------------------------
# Write, manifest, read back
# --------------------------------------------------------------------------


def test_a_stored_table_reads_back_with_its_triple_and_its_rows_recovered(
    lake_root, registry_root, tracking_root
):
    """The round trip, and the triple with it. The body is three numeric
    columns, so the segment manifest id, the segment name and the FULL
    `predictor_id` can only come from the manifest -- the partition
    directory truncates the last of those to 16 characters and cannot be
    inverted."""
    rig = _rig(lake_root, registry_root, tracking_root)
    written = _write(rig, lake_root, registry_root)

    loaded = load_prediction_table(
        written["manifest"]["manifest_id"],
        written["dataset"],
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert loaded.segment_manifest_id == rig["manifest"]["manifest_id"]
    assert loaded.segment_name == "val"
    assert loaded.predictor_id == FIXTURE_PREDICTOR_ID
    assert loaded.symbol == SYMBOL
    assert loaded.schema_version == PREDICTIONS_SCHEMA_VERSION
    assert loaded.table.schema == dict(PREDICTION_TABLE_SCHEMA)
    assert loaded.table.equals(written["table"])
    # Anti-vacuity: an all-zero pred column would round-trip through any
    # amount of corruption undetected.
    assert loaded.table["pred"].to_numpy().any()


def test_the_manifest_records_the_seven_partition_keys_check_no_rewrite_needs(
    lake_root, registry_root, tracking_root
):
    """`size_bytes`/`mtime_ns` feed `check_no_manifest_rewrite`'s cheap
    per-commit `verify_manifest_fast` leg; omitting either breaks a hook
    rather than a test, which is the kind of failure nobody attributes to the
    writer that caused it. The path is lake-root-RELATIVE, because
    `_enforce_tier_containment` resolves it against the caller's own root."""
    rig = _rig(lake_root, registry_root, tracking_root)
    written = _write(rig, lake_root, registry_root)
    entry = written["manifest"]["partitions"][0]

    assert set(entry) == {
        "path",
        "sha256",
        "rows",
        "size_bytes",
        "mtime_ns",
        "etime_min",
        "etime_max",
    }
    assert not Path(entry["path"]).is_absolute()
    assert entry["path"].startswith(f"{PREDICTIONS_TIER}/")
    on_disk = written["path"]
    assert entry["size_bytes"] == on_disk.stat().st_size
    assert entry["rows"] == written["table"].height
    assert entry["etime_min"] == written["table"]["etime"][0]
    assert entry["etime_max"] == written["table"]["etime"][-1]


def test_no_by_date_pointer_is_written_and_omitting_dates_raises_a_bare_key_error(
    lake_root, registry_root, tracking_root
):
    """`dates=[]` is load-bearing, not tidy (D-07-26).

    First half: after a real write, the dataset has no `by-date/` directory
    at all -- this tier gets no date-addressable entry point.

    Second half, the anti-vacuity one: `issue_manifest` computes
    `covered_dates` from `p["date"]` BEFORE it consults the tier, so the same
    partition entry issued with `dates` omitted raises a bare `KeyError`. The
    explicit argument is therefore the thing that makes the writer work, not
    a style choice -- and this is the assertion that would fail if someone
    "cleaned it up"."""
    rig = _rig(lake_root, registry_root, tracking_root)
    written = _write(rig, lake_root, registry_root)
    dataset = written["dataset"]

    assert not (registry_root / "manifests" / dataset / "by-date").exists()
    assert not by_date_index_path(
        registry_root, dataset, SYMBOL, PREDICTIONS_TIER, DEFAULT_DATE
    ).exists()

    entry = dict(written["manifest"]["partitions"][0])
    entry["path"] = entry["path"].replace("part-", "part-second-")
    with pytest.raises(KeyError, match="date"):
        issue_manifest(
            dataset=dataset,
            symbol=SYMBOL,
            stream=PREDICTIONS_TIER,
            tier=PREDICTIONS_TIER,
            schema_version=PREDICTIONS_SCHEMA_VERSION,
            inputs=[],
            partitions=[entry],
            code_hash="deadbeef",
            registry_root=registry_root,
        )


def test_a_second_table_for_the_same_triple_is_refused_naming_the_existing_part_file(
    lake_root, registry_root, tracking_root
):
    """The guard globs the PARENT, never the file: the filename embeds
    `time.time_ns()`, so `final_path.exists()` on it can never be true. That
    exact bug put two normalization artifacts side by side, each with its own
    manifest (04-REVIEW.md WR-05), and this tier would have repeated it
    verbatim."""
    rig = _rig(lake_root, registry_root, tracking_root)
    first = _write(rig, lake_root, registry_root)

    with pytest.raises(FileExistsError, match=r"part-.*\.parquet"):
        _write(rig, lake_root, registry_root)

    # ...and a DIFFERENT predictor is a different directory, so it is allowed.
    other = _write(rig, lake_root, registry_root, predictor_id="7e" * 32)
    assert other["path"].parent != first["path"].parent
    assert other["manifest"]["manifest_id"] != first["manifest"]["manifest_id"]


def test_write_prediction_table_refuses_a_null_where_a_nan_is_meant(
    lake_root, registry_root, tracking_root
):
    """A nullable Float64 column becomes a NaN-filled COPY at `.to_numpy()`
    and the reader cannot tell (`sim/arrays.py`'s own finding). So "no
    prediction" has exactly one spelling in this tier -- NaN -- and a null is
    refused at the writer, before any bytes exist to read back."""
    rig = _rig(lake_root, registry_root, tracking_root)
    frame = _val_shaped_frame(rig)
    pred = np.full(frame.height, 0.5)
    table = prediction_table_from_frame(frame, pred).with_columns(
        pl.when(pl.col("decision_seq") == 3)
        .then(None)
        .otherwise(pl.col("pred"))
        .alias("pred")
    )
    with pytest.raises(ValueError, match="null value"):
        write_prediction_table(
            table,
            symbol=SYMBOL,
            segment_manifest_id=rig["manifest"]["manifest_id"],
            segment_name="val",
            predictor_id=FIXTURE_PREDICTOR_ID,
            lake_root=lake_root,
            registry_root=registry_root,
            code_hash="deadbeef",
        )

    # Anti-vacuity: the same table with a NaN in that cell is ACCEPTED, so
    # the refusal above is about the spelling and not about the row.
    with_nan = prediction_table_from_frame(
        frame, np.where(np.arange(frame.height) == 3, np.nan, pred)
    )
    manifest = write_prediction_table(
        with_nan,
        symbol=SYMBOL,
        segment_manifest_id=rig["manifest"]["manifest_id"],
        segment_name="val",
        predictor_id=FIXTURE_PREDICTOR_ID,
        lake_root=lake_root,
        registry_root=registry_root,
        code_hash="deadbeef",
    )
    loaded = load_prediction_table(
        manifest["manifest_id"],
        predictions_dataset(SYMBOL),
        registry_root=registry_root,
        lake_root=lake_root,
    )
    round_tripped = loaded.table["pred"].to_numpy()
    assert np.isnan(round_tripped[3]), "NaN did not survive the Parquet round trip"
    assert loaded.table["pred"].null_count() == 0, "NaN came back as null"


def test_write_prediction_table_refuses_an_unordered_or_empty_table(
    lake_root, registry_root, tracking_root
):
    """The writer runs `assert_decision_order` before it writes a byte, so a
    table whose rows are not a decision sequence never reaches the lake at
    all -- the read-back assertion is the second line of defence, not the
    first."""
    rig = _rig(lake_root, registry_root, tracking_root)
    frame = _val_shaped_frame(rig)
    table = prediction_table_from_frame(frame, np.full(frame.height, 0.5))
    kwargs = {
        "symbol": SYMBOL,
        "segment_manifest_id": rig["manifest"]["manifest_id"],
        "segment_name": "val",
        "predictor_id": FIXTURE_PREDICTOR_ID,
        "lake_root": lake_root,
        "registry_root": registry_root,
        "code_hash": "deadbeef",
    }
    with pytest.raises(ValueError, match="strictly ascending"):
        write_prediction_table(table.reverse(), **kwargs)
    with pytest.raises(ValueError, match="empty table"):
        write_prediction_table(table.head(0), **kwargs)
    with pytest.raises(ValueError, match="PREDICTION_TABLE_SCHEMA"):
        write_prediction_table(
            table.with_columns(pl.col("pred").cast(pl.Float32)), **kwargs
        )
    assert not (lake_root / PREDICTIONS_TIER).exists(), (
        "a refused write left bytes on the lake -- every refusal above must "
        "happen before the parquet is written"
    )


# --------------------------------------------------------------------------
# The loader's two gates
# --------------------------------------------------------------------------


def test_load_prediction_table_does_not_enforce_a_dq_pause(
    lake_root, registry_root, tracking_root
):
    """This tier produces no DQ report rows by design, so routing the loader
    through the pause gate would read "no rows" as `missing` and pause it
    forever. The omission is deliberate, and this test is what makes it
    deliberate rather than an oversight -- the twin of
    `test_load_normalization_does_not_enforce_a_dq_pause`."""
    rig = _rig(lake_root, registry_root, tracking_root)
    written = _write(rig, lake_root, registry_root)
    # The rig writes a DQ report for its FEATURES partition; nothing anywhere
    # writes one for a predictions partition.
    report = pl.read_parquet(dq_report_path(lake_root, DEFAULT_DATE))
    assert report.height > 0, "this lake has no DQ report at all -- vacuous"
    assert PREDICTIONS_TIER not in set(report["stream"].to_list()), (
        "a DQ row for the predictions tier now exists -- the premise of this "
        "test has changed"
    )

    loaded = load_prediction_table(
        written["manifest"]["manifest_id"],
        written["dataset"],
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert loaded.table.height == written["table"].height

    # Anti-vacuity: the gate is not merely unnecessary here, it is
    # INAPPLICABLE -- it asks each partition for the date it covers, and this
    # tier's partitions cover a segment.
    with pytest.raises(KeyError, match="date"):
        store._enforce_dq_pause(
            written["manifest"], registry_root=registry_root, lake_root=lake_root
        )


def test_load_prediction_table_refuses_a_table_whose_scored_day_is_held_out(
    lake_root, registry_root, tracking_root
):
    """The holdout gate IS kept: a prediction column is a summary of the rows
    it was scored on, so the days those rows came from must still be
    readable. The dates are derived from the scored segment manifest's own
    upstream feature manifests, which is what makes the gate work on a table
    already on the lake without reissuing it."""
    rig = _rig(lake_root, registry_root, tracking_root)
    written = _write(rig, lake_root, registry_root)

    # Anti-vacuity: with no registry at all, the same read succeeds.
    load_prediction_table(
        written["manifest"]["manifest_id"],
        written["dataset"],
        registry_root=registry_root,
        lake_root=lake_root,
    )

    write_holdout_registry(registry_root, [DEFAULT_DATE], symbol=SYMBOL)
    with pytest.raises(QuarantinedDateError, match=DEFAULT_DATE):
        load_prediction_table(
            written["manifest"]["manifest_id"],
            written["dataset"],
            registry_root=registry_root,
            lake_root=lake_root,
        )


def test_the_scored_dates_are_derived_from_the_segment_manifest_and_fail_closed(
    lake_root, registry_root, tracking_root
):
    """Two hops, both checked. The table names a segment manifest; that
    manifest names the feature manifests whose partitions carry the only
    dates in the chain. Delete the segment manifest and the read must
    REFUSE, not degrade to "no dates, nothing held out" -- an unreadable
    provenance entry that reads as "nothing to apply" is the exact failure
    direction `data.holdout`'s fail-closed doctrine forbids."""
    rig = _rig(lake_root, registry_root, tracking_root)
    written = _write(rig, lake_root, registry_root)
    entry = next(
        item
        for item in written["manifest"]["inputs"]
        if item["role"] == SCORED_SEGMENT_INPUT_ROLE
    )
    assert entry["segment_name"] == "val"
    assert entry["predictor_id"] == FIXTURE_PREDICTOR_ID

    path = segment_manifest_path(registry_root, rig["manifest"]["manifest_id"])
    body = path.read_bytes()
    path.unlink()
    with pytest.raises(ValueError, match="not in this registry"):
        load_prediction_table(
            written["manifest"]["manifest_id"],
            written["dataset"],
            registry_root=registry_root,
            lake_root=lake_root,
        )

    # Restored byte-for-byte, the read works again -- so the refusal above
    # was about the missing file and not about anything else this test did.
    path.write_bytes(body)
    load_prediction_table(
        written["manifest"]["manifest_id"],
        written["dataset"],
        registry_root=registry_root,
        lake_root=lake_root,
    )

    # ...and a segment manifest whose BYTES moved is refused too, even though
    # the file is present and self-consistent: the table recorded the sha256
    # of what it actually scored.
    tampered = json.loads(body)
    tampered["fold_config_reason"] += " (edited)"
    path.write_text(json.dumps(tampered, sort_keys=True, indent=2))
    with pytest.raises(ValueError, match="bytes that moved"):
        load_prediction_table(
            written["manifest"]["manifest_id"],
            written["dataset"],
            registry_root=registry_root,
            lake_root=lake_root,
        )


def test_a_manifest_with_no_scored_segment_input_is_refused_rather_than_read(
    lake_root, registry_root, tracking_root
):
    """An ungateable table must not be readable. The scored-segment input is
    where the segment id, the segment name and the full predictor id live, so
    a manifest carrying none -- or two, each claiming a different
    provenance -- is refused before any partition is read."""
    rig = _rig(lake_root, registry_root, tracking_root)
    written = _write(rig, lake_root, registry_root)
    dataset = written["dataset"]
    manifest_file = (
        registry_root
        / "manifests"
        / dataset
        / f"{written['manifest']['manifest_id']}.json"
    )
    body = json.loads(manifest_file.read_text())

    for inputs, expected in (
        ([], "carries 0 inputs"),
        (body["inputs"] * 2, "carries 2 inputs"),
    ):
        stripped = dict(body, inputs=inputs)
        # RE-SIGNED, so this trips the inputs check rather than
        # `resolve_manifest`'s self-hash check one gate earlier.
        stripped["manifest_id"] = store.compute_manifest_id(stripped)
        target = (
            registry_root / "manifests" / dataset / f"{stripped['manifest_id']}.json"
        )
        target.write_text(json.dumps(stripped, sort_keys=True, indent=2))
        with pytest.raises(ValueError, match=expected):
            load_prediction_table(
                stripped["manifest_id"],
                dataset,
                registry_root=registry_root,
                lake_root=lake_root,
            )


def test_scored_segment_input_refuses_a_segment_manifest_that_is_not_in_the_registry(
    tmp_path,
):
    """Provenance for a segment nobody can resolve is not provenance. Caught
    at WRITE time as well as at read time, because a table issued against a
    registry that never had the segment manifest is unreadable forever
    after."""
    with pytest.raises(ValueError, match="not in this registry"):
        scored_segment_input(
            "b" * 64,
            segment_name="val",
            predictor_id=FIXTURE_PREDICTOR_ID,
            registry_root=tmp_path / "registry",
        )


# --------------------------------------------------------------------------
# The narrowing rule, in isolation
# --------------------------------------------------------------------------


def test_partition_overlaps_segment_treats_the_ranges_as_the_two_conventions_they_are():
    """A partition's `[etime_min, etime_max]` is INCLUSIVE (both are real
    rows); a segment's `[start_ns, end_ns)` is HALF-OPEN (D-05-03). Mixing
    the two is how an off-by-one date reaches a holdout gate, so each
    boundary case is named.

    The narrowing matters for a reason no round-trip test can show: without
    it, a `val` table's scored dates are every date the POOL spans, and a
    train day going into quarantine would refuse a val table that never read
    it."""
    # Wholly before / wholly after.
    assert not partition_overlaps_segment(0, 99, 100, 200)
    assert not partition_overlaps_segment(200, 300, 100, 200)
    # The last row of the partition IS the first row of the segment.
    assert partition_overlaps_segment(0, 100, 100, 200)
    # The first row of the partition is the last row BEFORE the segment ends.
    assert partition_overlaps_segment(199, 500, 100, 200)
    # Enclosing and enclosed.
    assert partition_overlaps_segment(0, 500, 100, 200)
    assert partition_overlaps_segment(120, 130, 100, 200)
    # A zero-width segment -- the held_out sentinel -- overlaps nothing.
    assert not partition_overlaps_segment(0, 500, 200, 200)


def test_assert_decision_order_returns_the_etime_array_it_proved(tmp_path):
    """It RETURNS the array rather than only asserting over it, so a caller
    cannot re-read the column by another route and check one thing while
    using another."""
    frame = pl.DataFrame({"etime": pl.Series([10, 20, 30], dtype=pl.Int64)})
    etime = assert_decision_order(frame)
    assert etime.dtype == np.int64
    assert etime.tolist() == [10, 20, 30]
    assert assert_decision_order(frame.head(0)).size == 0
    assert assert_decision_order(frame.head(1)).tolist() == [10]
