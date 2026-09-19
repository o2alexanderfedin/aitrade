"""RP-4: a held-out date is refused at BOTH ends of the features tier.

Decision rows are a near-lossless transform of L1 (04-CONTEXT.md D-04-11),
so a feature partition for a held-out date would hand back exactly what the
holdout withholds. The refusal therefore has to exist before any date is
declared held out -- which is today: measured 2026-09-17 and re-measured
for this plan, no holdout range has been chosen yet.

The one refusal these tests do NOT cover here is the quarantined TIER (a
manifest of that tier offered to `load_features`); that test needs the
tier's name as a string constant, so it lives in
`tests/store/test_loader_tier_containment.py`, the file
`tools/check_lockbox_containment.py` already sanctions for exactly that.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from data import store
from data.holdout import (
    QuarantinedDateError,
    assert_not_quarantined,
    holdout_registry_path,
    quarantined_dates,
)
from features.tier import (
    assert_buildable,
    feature_partition_path,
    issue_feature_manifest,
    load_features,
    write_feature_partition,
)
from tests.fixtures.feature_tier import (
    DATE,
    NEXT_DATE,
    SYMBOL,
    feature_frame,
    write_features_dq_report,
    write_holdout_registry,
)


def _built_day(lake_root: Path, registry_root: Path, date: str = DATE) -> dict:
    entry = write_feature_partition(
        feature_frame(), lake_root=lake_root, symbol=SYMBOL, date=date
    )
    manifest = issue_feature_manifest(
        symbol=SYMBOL,
        date=date,
        partition_entry=entry,
        curated_manifests=[],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    write_features_dq_report(lake_root, date, manifest["manifest_id"])
    return manifest


# --------------------------------------------------------------------------
# The registry itself
# --------------------------------------------------------------------------


def test_holdout_registry_absent_is_explicit_not_silent(tmp_path: Path, caplog):
    registry_root = tmp_path / "registry"
    assert not holdout_registry_path(registry_root).exists()
    with caplog.at_level(logging.INFO, logger="data.holdout"):
        dates = quarantined_dates(registry_root=registry_root, symbol=SYMBOL)
    assert dates == frozenset()
    assert dates.declared is False, (
        "'no holdout range has been declared yet' must be distinguishable "
        "from 'a declared holdout range that is currently empty'"
    )
    assert any("no holdout registry" in r.getMessage() for r in caplog.records)


def test_a_declared_but_empty_registry_is_not_the_same_as_an_absent_one(
    tmp_path: Path,
):
    registry_root = tmp_path / "registry"
    write_holdout_registry(registry_root, [])
    dates = quarantined_dates(registry_root=registry_root, symbol=SYMBOL)
    assert dates == frozenset()
    assert dates.declared is True


@pytest.mark.parametrize(
    "body",
    [
        "{not json at all",
        {"version": 1, "symbol": SYMBOL, "locked_at": 1, "reason": "r"},  # no dates
        {
            "version": 1,
            "symbol": SYMBOL,
            "dates": ["2026-9-13"],
            "locked_at": 1,
            "reason": "r",
        },
        {
            "version": 1,
            "symbol": SYMBOL,
            "dates": "2026-09-13",
            "locked_at": 1,
            "reason": "r",
        },
        {
            "version": 1,
            "symbol": SYMBOL,
            "dates": [20260913],
            "locked_at": 1,
            "reason": "r",
        },
        ["2026-09-13"],  # not an object
    ],
)
def test_holdout_registry_malformed_fails_closed(tmp_path: Path, body):
    registry_root = tmp_path / "registry"
    write_holdout_registry(registry_root, [], body=body)
    with pytest.raises(ValueError):
        quarantined_dates(registry_root=registry_root, symbol=SYMBOL)


def test_holdout_registry_for_another_symbol_raises(tmp_path: Path):
    registry_root = tmp_path / "registry"
    write_holdout_registry(registry_root, [DATE], symbol="ETHUSDT")
    with pytest.raises(ValueError, match="ETHUSDT"):
        quarantined_dates(registry_root=registry_root, symbol=SYMBOL)


def test_assert_not_quarantined_names_every_offender_and_the_context(
    tmp_path: Path,
):
    registry_root = tmp_path / "registry"
    write_holdout_registry(registry_root, [DATE, NEXT_DATE])
    with pytest.raises(QuarantinedDateError) as exc:
        assert_not_quarantined(
            [DATE, NEXT_DATE, "2026-09-16"],
            symbol=SYMBOL,
            registry_root=registry_root,
            context="a context string the message must carry",
        )
    message = str(exc.value)
    assert DATE in message and NEXT_DATE in message
    assert "2026-09-16" not in message
    assert "a context string the message must carry" in message


# --------------------------------------------------------------------------
# RP-4: the build refuses, and writes nothing
# --------------------------------------------------------------------------


def test_build_refuses_a_quarantined_date_and_writes_nothing(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    write_holdout_registry(registry_root, [DATE])

    with pytest.raises(QuarantinedDateError, match=DATE):
        assert_buildable(SYMBOL, DATE, NEXT_DATE, registry_root=registry_root)

    # ...and the writer refuses on its own, so a caller that skipped the
    # gate still cannot materialize the day.
    with pytest.raises(QuarantinedDateError, match=DATE):
        write_feature_partition(
            feature_frame(),
            lake_root=lake_root,
            symbol=SYMBOL,
            date=DATE,
            registry_root=registry_root,
        )

    date_dir = feature_partition_path(lake_root, SYMBOL, DATE).parent
    assert not date_dir.exists(), (
        "the refusal must happen BEFORE any write -- not as a cleanup"
    )
    assert not (lake_root / "features").exists()


def test_build_refuses_when_only_the_NEXT_day_is_quarantined(tmp_path: Path):
    registry_root = tmp_path / "registry"
    write_holdout_registry(registry_root, [NEXT_DATE])

    # D itself is perfectly loadable...
    assert quarantined_dates(registry_root=registry_root, symbol=SYMBOL) == frozenset(
        {NEXT_DATE}
    )
    # ...and the build of D still refuses, because D's long-horizon label
    # tail is computed from D+1's prevailing mids.
    with pytest.raises(QuarantinedDateError) as exc:
        assert_buildable(SYMBOL, DATE, NEXT_DATE, registry_root=registry_root)
    message = str(exc.value)
    assert NEXT_DATE in message, "the message must say WHICH day triggered it"
    assert DATE in message, "...and which day's build it was refusing"


# --------------------------------------------------------------------------
# ...and the read end
# --------------------------------------------------------------------------


def test_load_features_refuses_a_quarantined_date_already_on_disk(tmp_path: Path):
    """The registry is the authority at read time, not the build history: a
    partition built BEFORE its date was held out is still refused."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    manifest = _built_day(lake_root, registry_root)

    # Built while nothing was quarantined -- it loads.
    df = load_features(
        manifest["manifest_id"],
        f"{SYMBOL}.features",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert df.height == 3

    write_holdout_registry(registry_root, [DATE])
    with pytest.raises(QuarantinedDateError, match=DATE):
        load_features(
            manifest["manifest_id"],
            f"{SYMBOL}.features",
            registry_root=registry_root,
            lake_root=lake_root,
        )


def test_the_quarantine_refusal_precedes_the_dq_gate_and_the_read(
    tmp_path: Path, monkeypatch
):
    """Ordering, asserted rather than assumed: nothing past the refusal
    runs. `resolve_manifest`'s own hash verification DOES run first, by
    design -- a manifest that fails its own integrity check must never
    reach a quarantine conversation -- so what this pins is that no rows
    are read, no DQ verdict is formed, and no provenance is logged."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    manifest = _built_day(lake_root, registry_root)
    write_holdout_registry(registry_root, [DATE])

    def refuse(*args, **kwargs):
        raise AssertionError("reached past the quarantine refusal")

    monkeypatch.setattr(store, "read_verified_partitions", refuse)
    monkeypatch.setattr(store, "_enforce_dq_pause", refuse)
    monkeypatch.setattr(store, "_log_provenance", refuse)

    with pytest.raises(QuarantinedDateError):
        load_features(
            manifest["manifest_id"],
            f"{SYMBOL}.features",
            registry_root=registry_root,
            lake_root=lake_root,
        )


def test_load_features_pauses_on_an_unacknowledged_feature_day(tmp_path: Path):
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
    write_features_dq_report(
        lake_root,
        DATE,
        manifest["manifest_id"],
        dq_status="failed",
        check="feature_window",
    )
    with pytest.raises(store.DQPauseError, match="features"):
        load_features(
            manifest["manifest_id"],
            f"{SYMBOL}.features",
            registry_root=registry_root,
            lake_root=lake_root,
        )


def test_load_features_refuses_a_curated_manifest(tmp_path: Path, monkeypatch):
    """A manifest of another tier is refused by `resolve_manifest` before a
    partition byte is read -- `load_features` names its one tier, exactly
    as `load_curated` names its own."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    from tests.fixtures.feature_tier import issue_curated_day

    curated = issue_curated_day(lake_root, registry_root, "trade", DATE)

    reads: list = []
    real_read = store.pl.read_parquet

    def spy(path, *args, **kwargs):
        reads.append(path)
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(store.pl, "read_parquet", spy)
    with pytest.raises(store.ManifestTierError):
        load_features(
            curated["manifest_id"],
            f"{SYMBOL}.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )
    assert reads == []
