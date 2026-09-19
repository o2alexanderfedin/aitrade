"""FEAT-05: normalization parameters are fit on the training segment only,
stored as a manifest-addressed artifact, and LOADED by whatever is scored.

The load-bearing test in this file is
`test_validation_data_cannot_change_the_parameters`: it is the one FEAT-05
names, and it is the only one that can tell a train-only fit apart from a
fit over everything the caller happened to hand in. Every causality
assertion here has an anti-vacuity counterpart beside it, because an
invariance property alone passes on a function that returns a constant
(demonstrated in 04-03).

Lives in `tests/features/`, which is NOT a package: `mvp/features/` is a
real one and a same-named test package shadows it on `sys.path`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from data import store
from data.store import FEATURES_NORM_TIER, ManifestHashMismatch
from data.time_ns import NS_PER_SECOND
from features.normalize import (
    FEATURES_NORM_SCHEMA_VERSION,
    ZeroVarianceError,
    apply_normalization,
    expanding_z,
    fit_normalization,
    fit_training_segment,
    load_normalization,
    normalization_dataset,
    welford_std,
    write_normalization_artifact,
)

SYMBOL = "BTCUSDT"
TRAIN_END_DATE = "2026-09-13"
#: The two days the fixture's source feature manifests cover, newest last.
TRAIN_DATES = ("2026-09-12", "2026-09-13")
CODE_HASH = "0" * 40


def _segment(
    n: int = 400, *, seed: int = 7
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """`(etime, values)` for a synthetic two-feature segment, one row per
    10 ms, with no NaN anywhere unless a test plants one."""
    rng = np.random.default_rng(seed)
    etime = np.arange(n, dtype=np.int64) * (NS_PER_SECOND // 100)
    values = {
        "mid": 100.0 + np.cumsum(rng.normal(0.0, 0.01, size=n)),
        "imb_top": rng.normal(0.0, 1.0, size=n),
    }
    return etime, values


def _artifact_inputs(registry_root: Path) -> list[dict]:
    """Two `inputs[]` entries standing in for the feature manifests a real
    fit consumes -- written to disk so their sha256 is a real file hash."""
    inputs = []
    for index, manifest_id in enumerate(("a" * 64, "b" * 64)):
        path = registry_root / "manifests" / "BTCUSDT.features" / f"{manifest_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "manifest_id": manifest_id,
            "row_count": 10 * (index + 1),
            # The dates the fit's rows came from. Real feature manifests
            # always carry these; the fixture used to omit them, which is
            # how a loader that had no holdout gate also had nothing to
            # gate ON (04-REVIEW.md WR-03).
            "partitions": [{"date": TRAIN_DATES[index]}],
        }
        path.write_text(json.dumps(body))
        inputs.append(
            {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "rows": body["row_count"],
                "dataset": "BTCUSDT.features",
                "manifest_id": manifest_id,
                "role": "features_day",
            }
        )
    return inputs


def _write(
    params,
    tmp_path: Path,
    *,
    train_row_count: int,
    train_etime_range: tuple[int, int],
    train_end_date: str = TRAIN_END_DATE,
) -> dict:
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    return write_normalization_artifact(
        params,
        symbol=SYMBOL,
        train_end_date=train_end_date,
        source_feature_manifest_ids=_artifact_inputs(registry_root),
        train_row_count=train_row_count,
        train_etime_range=train_etime_range,
        lake_root=lake_root,
        registry_root=registry_root,
        code_hash=CODE_HASH,
    )


def _artifact_bytes(manifest: dict, lake_root: Path) -> bytes:
    return (lake_root / manifest["partitions"][0]["path"]).read_bytes()


# ---------------------------------------------------------------- FEAT-05


def test_validation_data_cannot_change_the_parameters(tmp_path):
    """The requirement, verbatim. Fit on `[0, k)`; make `[k, n)` anything at
    all; the stored parameters must be bit-identical -- not close."""
    n, k = 400, 250
    etime, values = _segment(n)
    train_end_etime = int(etime[k - 1])

    first = fit_training_segment(values, etime, train_end_etime=train_end_etime)

    mutated = {name: column.copy() for name, column in values.items()}
    for name, column in mutated.items():
        column[k:] = np.linspace(-1e9, 1e9, n - k)
    mutated["mid"][k + 3] = np.nan

    second = fit_training_segment(mutated, etime, train_end_etime=train_end_etime)

    assert first.params == second.params, (
        "validation rows changed the fitted parameters -- the fit is not train-only"
    )
    assert first.train_row_count == second.train_row_count == k
    assert first.train_etime_range == second.train_etime_range

    one = _write(
        first.params,
        tmp_path / "one",
        train_row_count=first.train_row_count,
        train_etime_range=first.train_etime_range,
    )
    two = _write(
        second.params,
        tmp_path / "two",
        train_row_count=second.train_row_count,
        train_etime_range=second.train_etime_range,
    )
    assert _artifact_bytes(one, tmp_path / "one" / "lake") == _artifact_bytes(
        two, tmp_path / "two" / "lake"
    ), "the stored artifact bytes moved when only validation data moved"


def test_the_fit_is_not_vacuous_about_training_rows(tmp_path):
    """Anti-vacuity for the test above: a function ignoring its input
    entirely would pass it. Moving a row INSIDE the training segment must
    move the parameters."""
    n, k = 400, 250
    etime, values = _segment(n)
    train_end_etime = int(etime[k - 1])

    first = fit_training_segment(values, etime, train_end_etime=train_end_etime)
    mutated = {name: column.copy() for name, column in values.items()}
    mutated["mid"][k - 1] += 5.0
    second = fit_training_segment(mutated, etime, train_end_etime=train_end_etime)

    assert first.params["mid"] != second.params["mid"]
    assert first.params["imb_top"] == second.params["imb_top"]


def test_nan_rows_are_excluded_from_the_fit_and_counted(tmp_path):
    etime, values = _segment(100)
    with_nan = {name: column.copy() for name, column in values.items()}
    with_nan["mid"][7] = np.nan
    with_nan["mid"][11] = np.nan

    clean = fit_normalization({"mid": np.delete(values["mid"], [7, 11])})
    nulled = fit_normalization({"mid": with_nan["mid"]})

    assert nulled["mid"] == clean["mid"], (
        "a NaN row changed the fit -- nulls must be excluded, not treated as 0"
    )
    assert nulled["mid"][0] == 98


# --------------------------------------------------- the causal transform


def test_expanding_z_at_t_uses_only_rows_up_to_t():
    _etime, values = _segment(120)
    column = values["mid"]
    t = 60

    base = expanding_z(column)

    future = column.copy()
    future[t + 1 :] = 1e6
    assert np.array_equal(
        expanding_z(future)[: t + 1], base[: t + 1], equal_nan=True
    ), "a row after t changed the normalized value at t"

    past = column.copy()
    past[t - 1] += 10.0
    assert expanding_z(past)[t] != base[t], (
        "a row at t' <= t did NOT change the value at t -- the invariance "
        "above would pass on a constant function"
    )

    own = column.copy()
    own[t] += 10.0
    assert expanding_z(own)[t] != base[t], "row t's own value is part of its z-score"


def test_expanding_z_is_undefined_before_the_variance_exists():
    z = expanding_z(np.array([1.0, 2.0, 3.0]))
    assert np.isnan(z[0]), "one row has no sample standard deviation"
    assert not np.isnan(z[1]) and not np.isnan(z[2])


def test_expanding_z_ends_at_the_same_statistics_the_fit_stores():
    """`frozen at the training segment's terminal statistics` is only one
    claim if the expanding path and the batch fit agree at the last row."""
    _etime, values = _segment(200)
    column = values["mid"]
    count, mean, m2 = fit_normalization({"mid": column})["mid"]
    assert count == column.size
    expected = (column[-1] - mean) / welford_std(count, m2)
    assert expanding_z(column)[-1] == expected


def test_zero_variance_feature_is_refused_not_divided_by_zero():
    with pytest.raises(ZeroVarianceError, match="imb_top"):
        fit_normalization({"mid": np.arange(10.0), "imb_top": np.full(10, 0.25)})
    with pytest.raises(ZeroVarianceError, match="one finite row"):
        fit_normalization({"mid": np.array([1.0, np.nan, np.nan])})


# ------------------------------------------------------------ the artifact


def test_inference_loads_parameters_and_does_not_refit(tmp_path):
    n, k = 400, 250
    etime, values = _segment(n)
    fit = fit_training_segment(values, etime, train_end_etime=int(etime[k - 1]))
    manifest = _write(
        fit.params,
        tmp_path,
        train_row_count=fit.train_row_count,
        train_etime_range=fit.train_etime_range,
    )
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    before = _artifact_bytes(manifest, lake_root)

    artifact = load_normalization(
        manifest["manifest_id"],
        normalization_dataset(SYMBOL),
        registry_root=registry_root,
        lake_root=lake_root,
    )

    validation = values["mid"][k:]
    count, mean, m2 = artifact.params["mid"]
    by_hand = (validation - mean) / welford_std(count, m2)
    assert np.array_equal(
        apply_normalization(validation, artifact.params["mid"]), by_hand
    )

    other = validation * 3.0 + 17.0
    assert np.array_equal(
        apply_normalization(other, artifact.params["mid"]),
        (other - mean) / welford_std(count, m2),
    ), "the frozen transform moved with the data it was scoring"
    assert _artifact_bytes(manifest, lake_root) == before
    assert artifact.params == fit.params


def test_artifact_round_trips_through_the_manifest(tmp_path):
    etime, values = _segment(300)
    fit = fit_training_segment(values, etime, train_end_etime=int(etime[-1]))
    manifest = _write(
        fit.params,
        tmp_path,
        train_row_count=fit.train_row_count,
        train_etime_range=fit.train_etime_range,
    )
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    dataset = normalization_dataset(SYMBOL)

    artifact = load_normalization(
        manifest["manifest_id"],
        dataset,
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert artifact.params == fit.params
    assert artifact.manifest_id == manifest["manifest_id"]
    assert manifest["tier"] == FEATURES_NORM_TIER

    path = lake_root / manifest["partitions"][0]["path"]
    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    path.write_bytes(bytes(raw))

    with pytest.raises(ManifestHashMismatch):
        load_normalization(
            manifest["manifest_id"],
            dataset,
            registry_root=registry_root,
            lake_root=lake_root,
        )


def test_the_artifact_tier_gets_no_by_date_pointer(tmp_path):
    etime, values = _segment(50)
    fit = fit_training_segment(values, etime, train_end_etime=int(etime[-1]))
    _write(
        fit.params,
        tmp_path,
        train_row_count=fit.train_row_count,
        train_etime_range=fit.train_etime_range,
    )
    pointers = list((tmp_path / "registry").rglob("by-date/*.json"))
    assert not pointers, (
        f"a normalization artifact wrote a by-date pointer: {pointers} -- it "
        "belongs to a fold, not to a date"
    )


def test_artifact_records_its_training_segment(tmp_path):
    n, k = 400, 250
    etime, values = _segment(n)
    fit = fit_training_segment(values, etime, train_end_etime=int(etime[k - 1]))
    manifest = _write(
        fit.params,
        tmp_path,
        train_row_count=fit.train_row_count,
        train_etime_range=fit.train_etime_range,
    )
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"

    artifact = load_normalization(
        manifest["manifest_id"],
        normalization_dataset(SYMBOL),
        registry_root=registry_root,
        lake_root=lake_root,
    )

    assert artifact.symbol == SYMBOL
    assert artifact.train_end_date == TRAIN_END_DATE
    assert artifact.train_row_count == k
    assert artifact.train_etime_range == (int(etime[0]), int(etime[k - 1]))
    assert artifact.source_feature_manifest_ids == ["a" * 64, "b" * 64]
    assert artifact.schema_version == FEATURES_NORM_SCHEMA_VERSION
    assert artifact.excluded_nulls == {"mid": 0, "imb_top": 0}

    body = pl.read_parquet(lake_root / manifest["partitions"][0]["path"])
    assert set(body["feature"]) == {"mid", "imb_top"}
    assert "date" not in manifest["partitions"][0], (
        "the training segment's end date belongs in the artifact BODY -- a "
        "partition `date` field is what the DQ pause gate reads"
    )


def test_load_normalization_does_not_enforce_a_dq_pause(tmp_path):
    """The artifact tier produces no DQ report rows by design. Routing this
    loader through the pause gate would read "no rows" as `missing` and
    pause it forever -- so the omission is deliberate, and this test is what
    makes it deliberate rather than an oversight."""
    etime, values = _segment(80)
    fit = fit_training_segment(values, etime, train_end_etime=int(etime[-1]))
    manifest = _write(
        fit.params,
        tmp_path,
        train_row_count=fit.train_row_count,
        train_etime_range=fit.train_etime_range,
    )
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    assert not (lake_root / "dq").exists(), "this lake has no DQ report at all"

    artifact = load_normalization(
        manifest["manifest_id"],
        normalization_dataset(SYMBOL),
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert artifact.params == fit.params

    # Anti-vacuity: the gate is not merely unnecessary here, it is
    # INAPPLICABLE -- it asks each partition for the date it covers, and an
    # artifact's partitions cover a fold, not a date.
    with pytest.raises(KeyError, match="date"):
        store._enforce_dq_pause(
            manifest, registry_root=registry_root, lake_root=lake_root
        )


def test_a_second_artifact_for_the_same_train_end_is_refused(tmp_path):
    """The write-once refusal used to be unreachable.

    `normalization_artifact_path` embeds `time.time_ns()`, so the
    `if final_path.exists()` guard on the very next line tested a path
    constructed nanoseconds earlier and could never be true. Two artifacts
    for the same `(symbol, train_end)` landed side by side, each with its
    own manifest -- nothing corrupted, but a guard that reads as
    protection and is not one is worse than no guard at all. The features
    tier one directory over globs the parent for written part files, which
    is what actually enforces the rule; this mirrors it.
    """
    etime, values = _segment(50)
    params = fit_training_segment(values, etime, train_end_etime=int(etime[-1])).params

    first = _write(params, tmp_path, train_row_count=50, train_etime_range=(1, 2))
    lake_root = tmp_path / "lake"
    parent = (lake_root / first["partitions"][0]["path"]).parent
    assert len(list(parent.glob("part-*.parquet"))) == 1

    with pytest.raises(FileExistsError, match="write-once"):
        _write(params, tmp_path, train_row_count=50, train_etime_range=(1, 2))

    assert len(list(parent.glob("part-*.parquet"))) == 1, (
        "the refusal must happen BEFORE the write, not as a cleanup"
    )


def test_load_normalization_refuses_a_held_out_training_date(tmp_path):
    """The same shape as CR-01, one tier over.

    `load_normalization` ran `resolve_manifest` and
    `read_verified_partitions` and nothing else. A mean and a standard
    deviation are a weaker channel than a price path, but they are still a
    summary of whatever rows went in, and the loader could not have
    refused even if it wanted to: the body records `train_end_date` and an
    etime range, not the list of dates the parameters were fit over.

    The dates are DERIVED from the source feature manifests rather than
    read out of the artifact body, for the same reason CR-01's tail is
    derived: the artifact already on the lake is write-once and its
    manifest body is content-hashed, so a gate that depended on a new
    field would fail OPEN on exactly the artifact that exists.
    """
    from data.holdout import QuarantinedDateError
    from tests.fixtures.feature_tier import write_holdout_registry

    etime, values = _segment(200)
    fit = fit_training_segment(values, etime, train_end_etime=int(etime[-1]))
    manifest = _write(
        fit.params,
        tmp_path,
        train_row_count=fit.train_row_count,
        train_etime_range=fit.train_etime_range,
    )
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    dataset = normalization_dataset(SYMBOL)

    # Nothing held out: it loads.
    assert (
        load_normalization(
            manifest["manifest_id"],
            dataset,
            registry_root=registry_root,
            lake_root=lake_root,
        ).params
        == fit.params
    )

    write_holdout_registry(registry_root, [TRAIN_DATES[0]])
    with pytest.raises(QuarantinedDateError, match=TRAIN_DATES[0]):
        load_normalization(
            manifest["manifest_id"],
            dataset,
            registry_root=registry_root,
            lake_root=lake_root,
        )

    # Discriminating half: a date the fit never saw does not refuse it.
    write_holdout_registry(registry_root, ["2026-09-20"])
    assert (
        load_normalization(
            manifest["manifest_id"],
            dataset,
            registry_root=registry_root,
            lake_root=lake_root,
        ).params
        == fit.params
    )


def test_an_artifact_that_cannot_say_what_it_was_fit_on_is_refused(tmp_path):
    """Fail-closed, not fail-open: an artifact whose inputs name no dates
    cannot be gated, so it cannot be read."""
    from features.normalize import _train_dates

    with pytest.raises(ValueError, match="cannot say which dates"):
        _train_dates({"inputs": [], "symbol": SYMBOL}, registry_root=tmp_path)
