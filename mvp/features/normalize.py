"""FEAT-05 / D-04-06: the expanding z-score, fit on the training segment
ONLY, stored as a manifest-addressed artifact that inference and the
simulator LOAD.

WHAT IS NORMALIZED, AND WHERE. Every entry in `spec/features.toml` declares
`normalization = "none"`, and nothing here changes that. It is a statement
about the STORED column: `lake/features/` holds raw feature values, which
is what makes one written partition reusable across folds whose training
segments differ. The z-score below is a MODEL-INPUT transform applied on
the way into a model, parameterised by the artifact this module writes. If
a later phase wants a normalized column STORED, that is a new catalogue
name, never an edit to an existing `definition`.

THE ONE QUESTION D-04-06 LEAVES OPEN, ANSWERED:

- WITHIN the training segment, the value at row `t` is normalized by the
  expanding statistics over training rows `<= t`. Strictly causal, and row
  `t`'s own value counts -- using what is known AT `t` is not lookahead.
- OUTSIDE it -- validation, inference, the simulator -- the parameters are
  FROZEN at the training segment's terminal statistics and loaded from the
  artifact. Nothing downstream ever recomputes a statistic from the data it
  is scoring. That is the leak no feature-level property would catch: a
  validation-window mean is a summary of the future, smuggled in through a
  denominator.

WHY WELFORD `(count, mean, M2)` AND NOT `(mean, std)`. The triple is
sufficient both to freeze (std falls out of it) and to RESUME the expanding
path deterministically when a later fold extends the training segment by
appending rows -- merging a second segment's Welford state is exact, while
re-deriving it from `(mean, std)` is not. It also makes the fit auditable:
`count` is the number of finite rows the parameters actually saw, so
`train_row_count - count` is the excluded-null count, recorded rather than
implied.

SAMPLE, NOT POPULATION. `welford_std` divides `M2` by `count - 1`. With one
row the standard deviation does not exist and the expanding transform emits
NaN rather than 0/0 -- the same "undefined, not neutral" rule the kernel
applies to `ofi`'s first update.

THE TRAIN/VALIDATION BOUNDARY LIVES IN `fit_training_segment`. A fit
function that is only ever handed training rows cannot be tested for
train-onlyness -- the slice would live in the caller, and the mutation
"fit over everything" would have nowhere in this file to live. So the
boundary is an argument (`train_end_etime`, inclusive) and the selection is
this module's code, which is what
`test_validation_data_cannot_change_the_parameters` mutates.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from data.capture.rotation import write_parquet_atomic
from data.store import (
    FEATURES_NORM_TIER,
    issue_manifest,
    read_verified_partitions,
    resolve_manifest,
)

__all__ = [
    "FEATURES_NORM_SCHEMA_VERSION",
    "NORMALIZATION_ARTIFACT_SCHEMA",
    "NormalizationArtifact",
    "TrainingSegmentFit",
    "ZeroVarianceError",
    "apply_normalization",
    "expanding_z",
    "fit_normalization",
    "fit_training_segment",
    "load_normalization",
    "normalization_artifact_path",
    "normalization_dataset",
    "welford_std",
    "write_normalization_artifact",
]

#: Bumped when the artifact's columns change. Read back and asserted by
#: `load_normalization`, so an old artifact cannot be read as a new one.
FEATURES_NORM_SCHEMA_VERSION = 1

#: One row per feature, with the segment metadata repeated on every row.
#: Repetition rather than a sidecar: the artifact must answer "which data
#: were these fit on" ON ITS OWN, and a second file is a second thing that
#: can go missing.
NORMALIZATION_ARTIFACT_SCHEMA: dict[str, pl.DataType] = {
    "feature": pl.Utf8,
    "count": pl.Int64,
    "mean": pl.Float64,
    "m2": pl.Float64,
    "std": pl.Float64,
    "excluded_nulls": pl.Int64,
    "symbol": pl.Utf8,
    "train_end_date": pl.Utf8,
    "train_row_count": pl.Int64,
    "train_etime_min": pl.Int64,
    "train_etime_max": pl.Int64,
    "source_feature_manifest_ids": pl.List(pl.Utf8),
    "schema_version": pl.Int32,
}


class ZeroVarianceError(ValueError):
    """A feature whose training-segment variance is zero, or that has fewer
    than two finite rows, NAMED.

    Dividing by it would emit `inf`/`NaN` for every row of a column a model
    would then train on. A constant feature is a modelling problem, and the
    place to notice it is the fit, not the loss curve.
    """


@dataclass(frozen=True)
class TrainingSegmentFit:
    """What `fit_training_segment` measured: the parameters plus the segment
    they describe, so the artifact writer never has to re-derive either."""

    params: dict[str, tuple[int, float, float]]
    train_row_count: int
    train_etime_range: tuple[int, int]


@dataclass(frozen=True)
class NormalizationArtifact:
    """A loaded artifact. `params` is what `apply_normalization` takes; the
    rest is the answer to "which data were these fit on"."""

    manifest_id: str
    symbol: str
    train_end_date: str
    train_row_count: int
    train_etime_range: tuple[int, int]
    source_feature_manifest_ids: list[str]
    excluded_nulls: dict[str, int]
    schema_version: int
    params: dict[str, tuple[int, float, float]]


def normalization_dataset(symbol: str) -> str:
    """`"<SYMBOL>.features_norm"` -- a namespace disjoint from the curated
    and features ones, so no manifest of this tier can ever collide with a
    pointer another loader follows."""
    return f"{symbol}.{FEATURES_NORM_TIER}"


def welford_std(count: int, m2: float) -> float:
    """The SAMPLE standard deviation `sqrt(M2 / (count - 1))`, or NaN when
    fewer than two finite rows have been seen."""
    if count < 2:
        return math.nan
    return math.sqrt(m2 / (count - 1))


def _welford(values: np.ndarray) -> tuple[int, float, float]:
    """`(count, mean, M2)` over the FINITE rows of `values`, in order.

    In order, not vectorised, because the order is the contract: the same
    accumulation drives `expanding_z`, and two implementations of a running
    mean would disagree in the last bit long before anyone noticed.
    """
    count = 0
    mean = 0.0
    m2 = 0.0
    for value in values:
        if not math.isfinite(value):
            continue
        count += 1
        delta = value - mean
        mean += delta / count
        m2 += delta * (value - mean)
    return count, mean, m2


def fit_normalization(
    values: dict[str, np.ndarray],
) -> dict[str, tuple[int, float, float]]:
    """Welford `(count, mean, M2)` per feature over the rows it is given.

    Non-finite rows (a null feature arrives from the tier as NaN) are
    EXCLUDED, never imputed: a null imputed to zero is a made-up
    observation that moves both the mean and the variance.

    Raises `ZeroVarianceError`, naming the feature, on a constant column or
    on one with fewer than two finite rows.
    """
    params: dict[str, tuple[int, float, float]] = {}
    for name in sorted(values):
        column = np.asarray(values[name], dtype=np.float64)
        count, mean, m2 = _welford(column)
        if count < 2:
            raise ZeroVarianceError(
                f"fit_normalization: feature {name!r} has {count} finite row(s) "
                "in the training segment -- fewer than two finite rows means "
                "no standard deviation exists (need more than one finite row)"
            )
        if m2 == 0.0:
            raise ZeroVarianceError(
                f"fit_normalization: feature {name!r} is constant at {mean!r} "
                f"over {count} training rows -- refusing to divide by a zero "
                "standard deviation"
            )
        params[name] = (count, mean, m2)
    return params


def fit_training_segment(
    values: dict[str, np.ndarray],
    etime: np.ndarray,
    *,
    train_end_etime: int,
) -> TrainingSegmentFit:
    """Fit on the rows with `etime <= train_end_etime` and NOTHING ELSE.

    THE SELECTION IS THE POINT. The caller may hand in a whole fold --
    training rows and validation rows in one array, which is exactly what a
    walk-forward harness has in hand -- and the parameters must not depend
    on a single value past the boundary. `train_end_etime` is INCLUSIVE and
    is compared on the int64 ns clock, never on a date string.
    """
    etime = np.asarray(etime, dtype=np.int64)
    in_train = etime <= np.int64(train_end_etime)
    train_row_count = int(in_train.sum())
    if train_row_count == 0:
        raise ValueError(
            f"fit_training_segment: no row has etime <= {train_end_etime} -- "
            "the training segment is empty"
        )
    for name, column in values.items():
        if column.shape[0] != etime.shape[0]:
            raise ValueError(
                f"fit_training_segment: feature {name!r} has "
                f"{column.shape[0]} rows, etime has {etime.shape[0]}"
            )
    params = fit_normalization(
        {name: column[in_train] for name, column in values.items()}
    )
    train_etime = etime[in_train]
    return TrainingSegmentFit(
        params=params,
        train_row_count=train_row_count,
        train_etime_range=(int(train_etime[0]), int(train_etime[-1])),
    )


def expanding_z(values: np.ndarray) -> np.ndarray:
    """The WITHIN-TRAINING transform: row `t` normalized by the expanding
    statistics over rows `<= t`.

    NaN until two finite rows have been seen (no sample standard deviation
    exists before then), and NaN at a row whose own value is not finite --
    an undefined feature has an undefined normalized value, and a 0.0 there
    would be a plausible-looking "average" reading.

    Never centres on the whole segment: a two-pass mean at row `t` is a
    summary of rows after `t`, which is the leak this whole module exists
    to make impossible.
    """
    column = np.asarray(values, dtype=np.float64)
    out = np.full(column.shape[0], np.nan, dtype=np.float64)
    count = 0
    mean = 0.0
    m2 = 0.0
    for index in range(column.shape[0]):
        value = column[index]
        if not math.isfinite(value):
            continue
        count += 1
        delta = value - mean
        mean += delta / count
        m2 += delta * (value - mean)
        if count < 2 or m2 == 0.0:
            continue
        out[index] = (value - mean) / math.sqrt(m2 / (count - 1))
    return out


def apply_normalization(
    values: np.ndarray, params: tuple[int, float, float]
) -> np.ndarray:
    """The FROZEN transform: `(values - mean) / std` with `(mean, std)` from
    a stored artifact and from nowhere else.

    Deliberately has no path that looks at `values` to decide `mean` or
    `std`. That is the whole contract -- everything scored by a run is
    scored with the run's training parameters, whatever data it is handed.
    """
    count, mean, m2 = params
    std = welford_std(count, m2)
    if not math.isfinite(std) or std == 0.0:
        raise ZeroVarianceError(
            f"apply_normalization: stored parameters have count={count}, "
            f"M2={m2!r}, so std={std!r} -- an artifact that cannot divide"
        )
    return (np.asarray(values, dtype=np.float64) - mean) / std


def normalization_artifact_path(lake_root: Path, symbol: str, train_end: str) -> Path:
    """`lake_root/features_norm/symbol=<symbol>/train_end=<date>/part-<ns>.parquet`.

    `train_end=` rather than `date=`, and no `stream=` level: this tier is
    addressed by manifest id, and the directory name says what the segment
    ENDED at rather than what day it "is" -- a normalization artifact
    belongs to a fold, not to a date (`BY_DATE_INDEXED_TIERS` deliberately
    excludes it).
    """
    return (
        Path(lake_root)
        / FEATURES_NORM_TIER
        / f"symbol={symbol}"
        / f"train_end={train_end}"
        / f"part-{time.time_ns()}.parquet"
    )


def write_normalization_artifact(
    params: dict[str, tuple[int, float, float]],
    *,
    symbol: str,
    train_end_date: str,
    source_feature_manifest_ids: list[dict],
    train_row_count: int,
    train_etime_range: tuple[int, int],
    lake_root: Path,
    registry_root: Path,
    code_hash: str,
) -> dict:
    """Write one artifact and issue its manifest; return the manifest dict.

    `source_feature_manifest_ids` is a list of `inputs[]` entries (the shape
    `features.tier.curated_manifest_input` builds), so provenance stays a
    chain: curated -> features -> normalization -> run. Their ids are ALSO
    written into the artifact body, because a body that cannot say what it
    was fit on is only auditable while its manifest is at hand.

    `dates=[]` is passed EXPLICITLY. `issue_manifest` defaults to the
    distinct `date` of each partition entry, and this tier's entries carry
    none: a by-date pointer here would be a date-addressable entry point
    into an artifact that belongs to a fold.
    """
    lake_root, registry_root = Path(lake_root), Path(registry_root)
    ids = [entry["manifest_id"] for entry in source_feature_manifest_ids]
    names = sorted(params)
    body = pl.DataFrame(
        {
            "feature": names,
            "count": [params[name][0] for name in names],
            "mean": [params[name][1] for name in names],
            "m2": [params[name][2] for name in names],
            "std": [welford_std(*params[name][::2]) for name in names],
            "excluded_nulls": [train_row_count - params[name][0] for name in names],
            "symbol": [symbol] * len(names),
            "train_end_date": [train_end_date] * len(names),
            "train_row_count": [train_row_count] * len(names),
            "train_etime_min": [int(train_etime_range[0])] * len(names),
            "train_etime_max": [int(train_etime_range[1])] * len(names),
            "source_feature_manifest_ids": [ids] * len(names),
            "schema_version": [FEATURES_NORM_SCHEMA_VERSION] * len(names),
        },
        schema=dict(NORMALIZATION_ARTIFACT_SCHEMA),
    )

    final_path = normalization_artifact_path(lake_root, symbol, train_end_date)
    if final_path.exists():
        raise FileExistsError(
            f"normalization artifact {final_path} already exists -- partitions "
            "are write-once"
        )
    write_parquet_atomic(body, final_path, compression="zstd")

    on_disk = final_path.read_bytes()
    stat = final_path.stat()
    partition_entry = {
        "path": str(final_path.relative_to(lake_root)),
        "sha256": hashlib.sha256(on_disk).hexdigest(),
        "rows": body.height,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "etime_min": int(train_etime_range[0]),
        "etime_max": int(train_etime_range[1]),
    }
    return issue_manifest(
        dataset=normalization_dataset(symbol),
        symbol=symbol,
        stream=FEATURES_NORM_TIER,
        tier=FEATURES_NORM_TIER,
        schema_version=FEATURES_NORM_SCHEMA_VERSION,
        inputs=list(source_feature_manifest_ids),
        partitions=[partition_entry],
        code_hash=code_hash,
        registry_root=registry_root,
        dates=[],
    )


def load_normalization(
    manifest_id: str,
    dataset: str,
    *,
    registry_root: Path,
    lake_root: Path,
) -> NormalizationArtifact:
    """Resolve and read one artifact: the manifest must re-hash to its id,
    its tier must be `features_norm`, every partition must resolve inside
    `lake_root/features_norm/`, and the bytes read must hash to what the
    manifest names.

    DELIBERATELY DOES NOT CALL `_enforce_dq_pause`, AND NOBODY SHOULD "FIX"
    THAT. The gate asks, per partition, for the DQ verdict of the DATE that
    partition covers. This tier's partitions cover a training SEGMENT and
    carry no `date` field at all, and no DQ check ever emits a row for it --
    so routing through the gate would read "no rows" as `missing` and pause
    every run forever. `test_load_normalization_does_not_enforce_a_dq_pause`
    pins both halves: the loader works, and the gate is inapplicable.

    The data quality of an artifact is not a separate question anyway: it is
    a deterministic function of feature partitions that the gate already
    judged when `load_features` read them.
    """
    manifest = resolve_manifest(
        manifest_id,
        dataset,
        registry_root=Path(registry_root),
        lake_root=Path(lake_root),
        expected_tier=FEATURES_NORM_TIER,
    )
    frames = read_verified_partitions(manifest, lake_root=Path(lake_root))
    body = pl.concat(frames, how="vertical")
    if body.schema != dict(NORMALIZATION_ARTIFACT_SCHEMA):
        raise ValueError(
            f"load_normalization: artifact schema {body.schema} does not match "
            f"NORMALIZATION_ARTIFACT_SCHEMA {dict(NORMALIZATION_ARTIFACT_SCHEMA)}"
        )
    versions = set(body["schema_version"].to_list())
    if versions != {FEATURES_NORM_SCHEMA_VERSION}:
        raise ValueError(
            f"load_normalization: artifact schema_version(s) {sorted(versions)} "
            f"!= {FEATURES_NORM_SCHEMA_VERSION}"
        )
    first = body.row(0, named=True)
    return NormalizationArtifact(
        manifest_id=manifest["manifest_id"],
        symbol=first["symbol"],
        train_end_date=first["train_end_date"],
        train_row_count=int(first["train_row_count"]),
        train_etime_range=(
            int(first["train_etime_min"]),
            int(first["train_etime_max"]),
        ),
        source_feature_manifest_ids=list(first["source_feature_manifest_ids"]),
        excluded_nulls={
            row["feature"]: int(row["excluded_nulls"])
            for row in body.iter_rows(named=True)
        },
        schema_version=int(first["schema_version"]),
        params={
            row["feature"]: (int(row["count"]), float(row["mean"]), float(row["m2"]))
            for row in body.iter_rows(named=True)
        },
    )
