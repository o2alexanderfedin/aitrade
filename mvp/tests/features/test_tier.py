"""The `lake/features/` tier: schema, write-once partition, manifest.

Hermetic -- every test builds a `tmp_path` lake and registry and never
touches the real lake (04-02-PLAN.md: this plan writes no real feature
row).

The tier's OTHER half -- that a quarantined-tier manifest still gets no
by-date pointer now that the features tier does -- lives in
`tests/store/test_loader_tier_containment.py`, which is the file
`tools/check_lockbox_containment.py` already sanctions for naming that
tier in a string constant.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from data.store import by_date_index_path, manifest_path
from features.tier import (
    BOOKKEEPING_COLUMNS,
    FEATURE_ROW_SCHEMA,
    FEATURE_SCHEMA_VERSION,
    curated_manifest_input,
    feature_partition_path,
    issue_feature_manifest,
    write_feature_partition,
)
from spec.catalogue import load_features as load_catalogue_features
from spec.catalogue import load_labels as load_catalogue_labels
from tests.fixtures.feature_tier import (
    DATE,
    SYMBOL,
    feature_frame,
    issue_curated_day,
)


def test_feature_row_schema_is_exactly_the_catalogue_plus_bookkeeping():
    catalogued = set(load_catalogue_features()) | set(load_catalogue_labels())
    columns = set(FEATURE_ROW_SCHEMA)
    assert catalogued <= columns, (
        f"catalogued name(s) missing from FEATURE_ROW_SCHEMA: "
        f"{sorted(catalogued - columns)}"
    )
    uncatalogued = columns - catalogued
    assert uncatalogued == set(BOOKKEEPING_COLUMNS), (
        "every non-catalogue column must be named in BOOKKEEPING_COLUMNS; "
        f"unaccounted for: {sorted(uncatalogued ^ set(BOOKKEEPING_COLUMNS))}"
    )
    assert set(BOOKKEEPING_COLUMNS) & catalogued == set(), (
        "a catalogued feature/label name must never also be bookkeeping"
    )


def test_written_partition_has_no_nan_and_no_rtime(tmp_path: Path):
    lake_root = tmp_path / "lake"
    df = feature_frame(rows=3, nan_in="mid")
    entry = write_feature_partition(df, lake_root=lake_root, symbol=SYMBOL, date=DATE)
    written = pl.read_parquet(lake_root / entry["path"])

    assert "rtime" not in written.columns
    assert "event_time" not in written.columns
    for name, dtype in FEATURE_ROW_SCHEMA.items():
        if dtype == pl.Float64:
            assert written[name].is_nan().sum() == 0, (
                f"{name} still carries NaN; the writer is the single "
                "NaN -> null conversion point"
            )
    assert written["mid"].null_count() == 1, (
        "the NaN the caller supplied must have become a NULL, not vanished"
    )
    assert written.height == 3
    assert entry["rows"] == 3
    assert entry["date"] == DATE
    assert entry["etime_min"] == int(df["etime"].min())
    assert entry["etime_max"] == int(df["etime"].max())


def test_feature_manifest_gets_a_by_date_pointer(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    entry = write_feature_partition(
        feature_frame(), lake_root=lake_root, symbol=SYMBOL, date=DATE
    )
    manifest = issue_feature_manifest(
        symbol=SYMBOL,
        date=DATE,
        partition_entry=entry,
        curated_manifests=[],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    assert manifest["tier"] == "features"
    assert manifest["stream"] == "features"
    assert manifest["dataset"] == f"{SYMBOL}.features"
    assert manifest["schema_version"] == FEATURE_SCHEMA_VERSION

    idx = by_date_index_path(
        registry_root, f"{SYMBOL}.features", SYMBOL, "features", DATE
    )
    assert idx.exists(), "a features manifest must get a by-date pointer"
    assert json.loads(idx.read_text())["manifest_id"] == manifest["manifest_id"]


def test_feature_partition_path_is_write_once(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    entry = write_feature_partition(
        feature_frame(), lake_root=lake_root, symbol=SYMBOL, date=DATE
    )
    issue_feature_manifest(
        symbol=SYMBOL,
        date=DATE,
        partition_entry=entry,
        curated_manifests=[],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    with pytest.raises(ValueError, match="write-once"):
        issue_feature_manifest(
            symbol=SYMBOL,
            date=DATE,
            partition_entry=entry,
            curated_manifests=[],
            code_hash="deadbeef",
            registry_root=registry_root,
        )

    # ... and the writer refuses to put a second part file in the same
    # date directory in the first place, so the manifest-level refusal is
    # the second line of defence, not the only one.
    with pytest.raises(FileExistsError):
        write_feature_partition(
            feature_frame(), lake_root=lake_root, symbol=SYMBOL, date=DATE
        )


def test_feature_manifest_records_its_curated_inputs(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    l1 = issue_curated_day(lake_root, registry_root, "bookTicker", DATE)
    trades = issue_curated_day(lake_root, registry_root, "trade", DATE)
    tail = issue_curated_day(lake_root, registry_root, "bookTicker", "2026-09-14")

    entry = write_feature_partition(
        feature_frame(), lake_root=lake_root, symbol=SYMBOL, date=DATE
    )
    manifest = issue_feature_manifest(
        symbol=SYMBOL,
        date=DATE,
        partition_entry=entry,
        curated_manifests=[
            curated_manifest_input(
                f"{SYMBOL}.bookTicker",
                l1["manifest_id"],
                registry_root=registry_root,
                role="l1_day",
            ),
            curated_manifest_input(
                f"{SYMBOL}.trade",
                trades["manifest_id"],
                registry_root=registry_root,
                role="trade_day",
            ),
            curated_manifest_input(
                f"{SYMBOL}.bookTicker",
                tail["manifest_id"],
                registry_root=registry_root,
                role="l1_label_tail",
            ),
        ],
        code_hash="deadbeef",
        registry_root=registry_root,
    )

    inputs = manifest["inputs"]
    assert len(inputs) == 3
    for entry_dict, source in zip(inputs, (l1, trades, tail), strict=True):
        assert Path(entry_dict["path"]).is_absolute(), (
            "inputs[].path is absolute (provenance may span roots)"
        )
        assert entry_dict["manifest_id"] == source["manifest_id"]
        expected = manifest_path(
            registry_root, source["dataset"], source["manifest_id"]
        )
        assert Path(entry_dict["path"]) == expected.resolve()
        import hashlib

        assert entry_dict["sha256"] == hashlib.sha256(expected.read_bytes()).hexdigest()
        assert entry_dict["rows"] == source["row_count"]

    assert [e["role"] for e in inputs] == ["l1_day", "trade_day", "l1_label_tail"]
    # The chain is recoverable from the features manifest ALONE.
    assert {e["manifest_id"] for e in inputs} == {
        l1["manifest_id"],
        trades["manifest_id"],
        tail["manifest_id"],
    }


def test_feature_partition_path_shape(tmp_path: Path):
    path = feature_partition_path(tmp_path, SYMBOL, DATE)
    rel = path.relative_to(tmp_path)
    assert rel.parts[0] == "features"
    assert rel.parts[1] == f"symbol={SYMBOL}"
    assert rel.parts[2] == f"date={DATE}"
    assert rel.parts[3].startswith("part-") and rel.parts[3].endswith(".parquet")
    assert not any(part.startswith("stream=") for part in rel.parts), (
        "a decision row is the merge of BOTH streams; a stream= level would "
        "invite a per-stream read that cannot exist"
    )


def test_the_two_PRIMARY_LABEL_derivations_cannot_drift_apart():
    """04-REVIEW.md IN-03: `PRIMARY_LABEL` is defined twice.

    `features/labels.py` derives it as `get_label("ret_10s_mid").name` and
    `features/tier.py` as `LABEL_COLUMNS[0]`. Both reach the catalogue, so
    neither can drift toward an UNCATALOGUED name -- but they can drift
    from EACH OTHER: reordering `LABEL_COLUMNS` moves one and not the
    other, and `labels.py`'s docstring explicitly declines to import from
    `tier.py`. The consequence would be silent: `build_stats.json` would
    record `null_primary_label_rows` for one label while the feature-tier
    DQ check judged coverage of another.

    One assertion is cheaper than either module importing the other.
    """
    from features import labels as labels_module
    from features import tier as tier_module

    assert tier_module.PRIMARY_LABEL == labels_module.PRIMARY_LABEL
    assert tier_module.PRIMARY_LABEL == "ret_10s_mid"
    assert tier_module.LABEL_COLUMNS[0] == labels_module.PRIMARY_LABEL, (
        "the tier's first label column IS the primary one; a reorder that "
        "changed that would change which label the DQ report judges"
    )
