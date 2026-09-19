"""The `lake/features/` tier: where decision rows live, and the only way
back out of it.

04-CONTEXT.md D-04-07: the decision-row matrix is a manifest-addressed lake
tier that reuses Phase 3's machinery -- content-hashed write-once
partitions, git-committed manifest bodies, `resolve_manifest`'s sha256 on
every read, the DQ pause gate. Nothing here re-implements any of that; a
second copy of a hash or a containment rule is a second thing that can
drift. What this module adds is the shape of a feature row, the physical
path convention, and the provenance chain: a features manifest records the
curated manifests it was built from, so the chain from a decision row back
to the bytes it came from is recoverable from the features manifest alone.

`load_features` is this tier's own loader, the exact mirror of
`data.store.load_curated` one tier over. It exists as a separate function
for the same reason the quarantined tier's loader does: every reader names
the one tier it is allowed to reach, and `resolve_manifest` refuses
everything else before a byte is read.

NULL, NEVER NaN -- and the conversion happens HERE. Three modules
legitimately produce NaN on the way to a decision row: the streaming kernel
(numba has no null), the label computation (same numeric reason), and the
merged event stream's cross-stream payload filler. If each converted its
own, a partition written by a fourth path would ship NaN unnoticed.
`write_feature_partition` is therefore the single conversion point: it maps
every Float64 column's NaN to null and then ASSERTS the frame it is about
to write has none left. NaN survives arithmetic silently; a null propagates
visibly through polars aggregations and is what `is_null()` consumers
filter on.

NO `rtime`, NO `event_time`. A decision row is not a message; it has no
receive clock. A consequence worth stating: `store.manifest_source()`
returns `"unknown"` for a features manifest, because its inputs are
manifest JSON paths rather than `/source=.../` partition paths. That is
harmless -- `check_rtime_plausibility` is only ever invoked from the DQ
report's curated stream loop, and this tier has no rtime to judge.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from data.capture.rotation import write_parquet_atomic
from data.store import (
    FEATURES_TIER,
    issue_manifest,
    manifest_path,
)
from spec.catalogue import get_feature, get_label

__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "FEATURE_ROW_SCHEMA",
    "BOOKKEEPING_COLUMNS",
    "FEATURE_COLUMNS",
    "LABEL_COLUMNS",
    "PRIMARY_LABEL",
    "feature_partition_path",
    "write_feature_partition",
    "curated_manifest_input",
    "issue_feature_manifest",
]

FEATURE_SCHEMA_VERSION = 1

# Every catalogue name below is a LITERAL argument to get_feature/get_label:
# `tools/check_catalogue_completeness.py` rejects a dynamically-constructed
# name, which is exactly what makes this schema unable to drift away from
# the catalogue without CI noticing.
FEATURE_COLUMNS: tuple[str, ...] = (
    get_feature("mid").name,
    get_feature("imb_top").name,
    get_feature("ofi").name,
    get_feature("trade_flow").name,
)

LABEL_COLUMNS: tuple[str, ...] = (
    get_label("ret_10s_mid").name,
    get_label("ret_1s_mid").name,
    get_label("ret_1min_mid").name,
    get_label("ret_10min_mid").name,
)

#: The label Stage 1 trades on; the one whose coverage the feature-tier DQ
#: report judges (the other three are diagnostic, D-04-17).
PRIMARY_LABEL: str = LABEL_COLUMNS[0]

#: Every column that is NOT a catalogued feature or label, named
#: explicitly. `test_feature_row_schema_is_exactly_the_catalogue_plus_
#: bookkeeping` asserts the partition of the schema into these two sets is
#: exact, so an uncatalogued feature column cannot be added by accident.
BOOKKEEPING_COLUMNS: frozenset[str] = frozenset(
    {
        "etime",
        "decision_source_rank",
        "decision_seq",
        "warmup",
        "post_gap_warmup",
        "schema_version",
    }
)

#: One decision row. ORDERED -- `write_feature_partition` requires exact
#: schema equality (names, order, dtypes), the same contract
#: `features.event_stream.event_arrays` imposes at the numba boundary.
#:
#: `decision_source_rank`/`decision_seq` say WHICH merged event row was the
#: decision row for its `etime`. They are the only way to re-derive the
#: `(etime, source_rank, seq)` tie pin from the artifact alone, which is
#: what makes the decision-row rule auditable after the fact rather than
#: only re-runnable.
FEATURE_ROW_SCHEMA: dict[str, pl.DataType] = {
    "etime": pl.Int64,
    "decision_source_rank": pl.Int8,
    "decision_seq": pl.Int64,
    **{name: pl.Float64 for name in FEATURE_COLUMNS},
    **{name: pl.Float64 for name in LABEL_COLUMNS},
    "warmup": pl.Boolean,
    "post_gap_warmup": pl.Boolean,
    "schema_version": pl.Int32,
}


def feature_partition_path(lake_root: Path, symbol: str, date: str) -> Path:
    """`lake_root/features/symbol=<symbol>/date=<date>/part-<ns>.parquet`.

    No `stream=` level, deliberately: a decision row is the merge of BOTH
    curated streams, and a per-stream path would invite a per-stream read
    that cannot exist.
    """
    return (
        Path(lake_root)
        / FEATURES_TIER
        / f"symbol={symbol}"
        / f"date={date}"
        / f"part-{time.time_ns()}.parquet"
    )


def _assert_schema(df: pl.DataFrame) -> None:
    if df.schema != dict(FEATURE_ROW_SCHEMA):
        raise ValueError(
            f"write_feature_partition: frame schema {df.schema} does not match "
            f"FEATURE_ROW_SCHEMA {dict(FEATURE_ROW_SCHEMA)}"
        )


def _nan_to_null(df: pl.DataFrame) -> pl.DataFrame:
    """The single NaN -> null conversion point (see the module docstring),
    followed by the assertion that makes it a guarantee rather than a hope."""
    float_columns = [
        name for name, dtype in FEATURE_ROW_SCHEMA.items() if dtype == pl.Float64
    ]
    converted = df.with_columns(
        [
            pl.when(pl.col(name).is_nan())
            .then(None)
            .otherwise(pl.col(name))
            .alias(name)
            for name in float_columns
        ]
    )
    remaining = {
        name: int(converted[name].is_nan().sum() or 0) for name in float_columns
    }
    offenders = {name: count for name, count in remaining.items() if count}
    if offenders:
        raise AssertionError(
            f"write_feature_partition: NaN survived conversion in {offenders} -- "
            "a feature partition's undefined values are NULL, never NaN"
        )
    return converted


def write_feature_partition(
    df: pl.DataFrame,
    *,
    lake_root: Path,
    symbol: str,
    date: str,
) -> dict:
    """Write one write-once feature partition and return the partition entry
    `issue_manifest` requires (`path` lake-root-RELATIVE, `sha256`,
    `size_bytes`, `mtime_ns`, `rows`, `etime_min`, `etime_max`, `date`).

    Refuses (`FileExistsError`) a `date=` directory that already holds a
    part file, mirroring `data/ingest/normalize.py:write_raw_partition`: a
    second write for an already-written day is an error, never a silent
    overwrite and never a silent duplicate.
    """
    _assert_schema(df)
    converted = _nan_to_null(df)

    final_path = feature_partition_path(lake_root, symbol, date)
    existing = sorted(final_path.parent.glob("part-*.parquet"))
    if existing:
        raise FileExistsError(
            f"feature partition {final_path.parent} already has a written part "
            f"file: {existing[0]} -- partitions are write-once"
        )
    write_parquet_atomic(converted, final_path, compression="zstd")

    on_disk = final_path.read_bytes()
    st = final_path.stat()
    return {
        "date": date,
        "path": str(final_path.relative_to(Path(lake_root))),
        "sha256": hashlib.sha256(on_disk).hexdigest(),
        "rows": converted.height,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": int(converted["etime"].min()),
        "etime_max": int(converted["etime"].max()),
    }


def curated_manifest_input(
    dataset: str, manifest_id: str, *, registry_root: Path, role: str
) -> dict:
    """One `inputs[]` entry naming a curated manifest this build consumed.

    Carries the manifest JSON's ABSOLUTE path (provenance may span the
    lake's own roots, so only an absolute path works -- `issue_manifest`'s
    existing convention), the sha256 of that file's bytes, its `row_count`,
    its `manifest_id`, and the `role` it played (`"l1_day"`,
    `"trade_day"`, `"l1_label_tail"`). The role is what makes the D+1
    label-tail dependency visible in the artifact rather than only in the
    build script.
    """
    path = manifest_path(Path(registry_root), dataset, manifest_id).resolve()
    body = json.loads(path.read_text())
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": body["row_count"],
        "dataset": dataset,
        "manifest_id": manifest_id,
        "role": role,
    }


def issue_feature_manifest(
    *,
    symbol: str,
    date: str,
    partition_entry: dict,
    curated_manifests: Sequence[dict],
    code_hash: str,
    registry_root: Path,
) -> dict:
    """Issue the features manifest for one built day.

    Thin by design: `data.store.issue_manifest` already owns hashing, path
    validation, the write-once refusal and the by-date pointer, and a
    second implementation of any of them is a second thing that can drift.

    `code_hash` stays a caller-supplied parameter, exactly as
    `build_curated_day` takes it: `features/` must not import
    `tracking.mlflow_utils`, so the run's own script computes it.
    """
    if "date" not in partition_entry:
        raise ValueError(
            "issue_feature_manifest: partition entry has no 'date' -- the DQ "
            "pause gate reads it per partition, and its absence would skip "
            "the gate rather than fail it"
        )
    return issue_manifest(
        dataset=f"{symbol}.{FEATURES_TIER}",
        symbol=symbol,
        stream=FEATURES_TIER,
        tier=FEATURES_TIER,
        schema_version=FEATURE_SCHEMA_VERSION,
        inputs=list(curated_manifests),
        partitions=[partition_entry],
        code_hash=code_hash,
        registry_root=Path(registry_root),
        dates=[date],
    )
