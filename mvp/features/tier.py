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
import logging
import os
import time
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from data import store
from data.capture.rotation import write_parquet_atomic
from data.dates import next_utc_date
from data.holdout import assert_not_quarantined
from data.store import (
    FEATURES_TIER,
    issue_manifest,
    manifest_path,
)
from spec.catalogue import get_feature, get_label

logger = logging.getLogger(__name__)

__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "FEATURE_ROW_SCHEMA",
    "BOOKKEEPING_COLUMNS",
    "FEATURE_COLUMNS",
    "LABEL_COLUMNS",
    "PRIMARY_LABEL",
    "LABEL_TAIL_ROLE",
    "feature_partition_path",
    "staged_feature_partition_path",
    "commit_feature_partition",
    "refused_dates_for",
    "write_feature_partition",
    "curated_manifest_input",
    "issue_feature_manifest",
    "assert_buildable",
    "load_features",
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

#: The `inputs[].role` of the curated manifest whose quotes became day
#: D's long-horizon label tail. Spelled once: `build.py` writes it and
#: `refused_dates_for` reads it, and the two drifting apart would silently
#: widen the read-time gate's blind spot rather than fail.
LABEL_TAIL_ROLE: str = "l1_label_tail"

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


#: The name a partition is written under BEFORE its manifest exists
#: (04-REVIEW.md WR-01). Deliberately not `part-`: an unmanifested file
#: that reads as a committed partition is what wedged a date and needed a
#: human to delete a file from a write-once tier. A `partial-` file is
#: never data -- the next build of that date removes it and starts over.
STAGING_PREFIX = "partial-"


def staged_feature_partition_path(final_path: Path) -> Path:
    """The `partial-` sibling of a final `part-<ns>.parquet` path."""
    return final_path.with_name(final_path.name.replace("part-", STAGING_PREFIX, 1))


def commit_feature_partition(partition_entry: dict, *, lake_root: Path) -> Path:
    """Rename a staged partition into its final `part-<ns>.parquet` name.

    Called by the build immediately BEFORE `issue_feature_manifest`, which
    is what keeps the manifest's own rule true -- it never names bytes that
    are not already on disk under the path it names. The rename is
    `os.replace`, so the sha256, size and mtime the entry already carries
    stay correct.

    The window this does NOT close is its own: a crash between this rename
    and the manifest still leaves an unmanifested `part-` file, and that
    date is genuinely wedged until someone looks. `build_features_range`
    reports it as `"orphaned"` and carries on rather than aborting the
    remaining dates.
    """
    final_path = Path(lake_root) / partition_entry["path"]
    staged = staged_feature_partition_path(final_path)
    if not staged.exists():
        raise FileNotFoundError(
            f"commit_feature_partition: nothing staged at {staged} -- the "
            "partition was either already committed or never written"
        )
    os.replace(staged, final_path)
    return final_path


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
    registry_root: Path | None = None,
    commit: bool = True,
) -> dict:
    """Write one write-once feature partition and return the partition entry
    `issue_manifest` requires (`path` lake-root-RELATIVE, `sha256`,
    `size_bytes`, `mtime_ns`, `rows`, `etime_min`, `etime_max`, `date`).

    REFUSES A HELD-OUT DATE BEFORE TOUCHING THE FILESYSTEM (D-04-11). The
    refusal is not a cleanup: nothing is created, so there is nothing to
    clean up. `assert_buildable` is the gate a build calls first and is
    the one that also covers day D+1; this second check is here so a
    caller that skipped the gate still cannot materialize the day.
    `registry_root` names the holdout registry to consult (`None` = the
    git-committed default).

    Refuses (`FileExistsError`) a `date=` directory that already holds a
    part file, mirroring `data/ingest/normalize.py:write_raw_partition`: a
    second write for an already-written day is an error, never a silent
    overwrite and never a silent duplicate.

    `commit=False` leaves the bytes under the `partial-` staging name and
    the caller must call `commit_feature_partition` once the rest of the
    build has succeeded (04-REVIEW.md WR-01). `build_features_day` is the
    only caller that needs it: it is the only one with work left to do --
    `build_stats.json`, then the manifest -- between the write and the
    point at which the partition becomes real. A caller that just wants a
    written partition (every test here, and any one-off) takes the default
    and gets today's behaviour.

    EITHER WAY, ANY STALE `partial-` FILE IN THE DIRECTORY IS REMOVED
    FIRST, loudly. An unmanifested staging file is not data: nothing in
    the registry names it, no manifest hashes it, and leaving it would
    turn a crashed build into a permanently wedged date.

    THE WRITE-ONCE GLOB IS A CHECK, NOT A LOCK (04-REVIEW.md IN-04, left
    open deliberately). It is a time-of-check/time-of-use test: two
    concurrent builds of the same date both glob an empty directory, both
    write distinct `part-<ns>.parquet` files, both issue manifests, and
    the second repoints the by-date pointer; a third build then sees two
    files and refuses. Nothing is corrupted -- each manifest names its own
    bytes and `resolve_manifest` still verifies them -- but a reader must
    not mistake this refusal for an invariant. Making it one needs an
    `O_EXCL` lock file in the `date=` directory, which is a real change to
    the tier's failure modes (a crashed build would then leave a lock to
    reap, on top of the staging file it already leaves). The daily build
    is single-process, so this is not a live risk, and it is recorded here
    rather than fixed.
    """
    assert_not_quarantined(
        [date],
        symbol=symbol,
        registry_root=registry_root,
        context=f"feature partition write of {date}",
    )
    _assert_schema(df)
    converted = _nan_to_null(df)

    final_path = feature_partition_path(lake_root, symbol, date)
    existing = sorted(final_path.parent.glob("part-*.parquet"))
    if existing:
        raise FileExistsError(
            f"feature partition {final_path.parent} already has a written part "
            f"file: {existing[0]} -- partitions are write-once. If no manifest "
            "names it, a previous build died between the rename and the "
            "manifest; that file must be removed before this date can be "
            "rebuilt."
        )
    for stale in sorted(final_path.parent.glob(f"{STAGING_PREFIX}*.parquet")):
        logger.warning(
            "removing a stale staged feature partition %s -- a previous build "
            "of %s died before its manifest was issued; the file was never a "
            "committed partition",
            stale,
            date,
        )
        stale.unlink()

    written_path = final_path if commit else staged_feature_partition_path(final_path)
    write_parquet_atomic(converted, written_path, compression="zstd")

    on_disk = written_path.read_bytes()
    st = written_path.stat()
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
        # PROVENANCE, NOT THE GATE (04-REVIEW.md CR-01). Recording the
        # dates an input covers makes the cross-day dependency legible in
        # the artifact instead of only in the build script. It must not be
        # the thing `refused_dates_for` relies on: manifest bodies are
        # content-hashed and append-only, so the three bodies already
        # committed cannot grow this key, and a gate that consulted it
        # alone would fail OPEN on exactly the partitions that matter.
        "dates": sorted(
            {part["date"] for part in body["partitions"] if "date" in part}
        ),
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


def assert_buildable(
    symbol: str, date: str, next_date: str, *, registry_root: Path | None = None
) -> None:
    """The build's FIRST gate, before it reads anything at all: neither
    `date` nor `next_date` may be held out.

    `next_date` is the half that is easy to leave out and expensive to get
    wrong. Day D's `ret_10min_mid` is computed from the prevailing mids of
    day D+1, so a partition for D built while D+1 is held out writes
    held-out prices into a readable tier -- a near-lossless transform of
    exactly the bytes the holdout withholds (D-04-11). Refusing D on D+1's
    account is what stops the holdout being laundered through a
    neighbouring day's label tail.
    """
    assert_not_quarantined(
        [date],
        symbol=symbol,
        registry_root=registry_root,
        context=f"feature build of {date}",
    )
    assert_not_quarantined(
        [next_date],
        symbol=symbol,
        registry_root=registry_root,
        context=(
            f"feature build of {date}: its long-horizon label tail reads {next_date}"
        ),
    )


def refused_dates_for(manifest: dict) -> list[str]:
    """Every date `load_features` must refuse this manifest on: its own
    partition dates, PLUS each of their label-tail days.

    THE TAIL IS THE POINT (04-REVIEW.md CR-01). A features partition for
    day D has exactly one partition date, D -- and it stores D+1's price
    path. `mid` is written raw and every label is a ratio, so
    `mid_t * (1 + ret_10min_mid)` reconstructs D+1's prevailing mid to
    1.5e-11 USDT. Refusing only D would hand the lockbox back through the
    neighbouring day's label tail, which is the one thing D-04-11 forbids.

    DERIVED FIRST, RECORDED SECOND, UNION OF BOTH. The tail is always
    exactly D+1 -- `features.labels.next_day_quote_series` builds it from
    `next_utc_date(date)` and there is no other path into it -- so the
    derivation needs no manifest field and therefore covers the three
    bodies already committed, which are content-hashed and can never be
    backfilled. `inputs[].dates` is read ON TOP of that, not instead of
    it: a future build whose tail reached further would be refused on
    what it recorded, and a body that recorded nothing still gets the
    derived day. Neither source can narrow the other.
    """
    dates = {part["date"] for part in manifest.get("partitions", []) if "date" in part}
    refused = set(dates) | {next_utc_date(date) for date in dates}
    for entry in manifest.get("inputs", []):
        if entry.get("role") != LABEL_TAIL_ROLE:
            continue
        refused.update(entry.get("dates") or ())
    return sorted(refused)


def load_features(
    manifest_id: str, dataset: str, *, registry_root: Path, lake_root: Path
) -> pl.DataFrame:
    """The features tier's own loader -- `data.store.load_curated` one tier
    over, and the only reading path into `lake/features/`.

    Four gates, in this order, and the ORDER IS THE POINT:

    1. `resolve_manifest(expected_tier=FEATURES_TIER)` -- the body must
       re-hash to its id, the tier must be this one, every partition path
       must genuinely resolve inside `lake_root/features/` (symlink- and
       hard-link-aware), and every partition's on-disk sha256 must match.
       Integrity FIRST: a manifest that fails its own hash must never
       reach a holdout or a DQ conversation.
    2. the holdout refusal, over `refused_dates_for(manifest)` -- the
       partitions' own dates AND their label-tail days. RUNTIME-FIRST, and
       read-time rather than build-time only: the registry is the
       authority, not the build history, so a partition written before its
       date was declared held out is still refused now, and so is one
       whose LABEL TAIL reaches into a day declared held out afterwards
       (04-REVIEW.md CR-01).
    3. `store._enforce_dq_pause` -- a features manifest with no report row
       of its own is `missing`, which pauses, so the feature-tier DQ rows
       are not optional decoration.
    4. `store._log_provenance`, then the verified read.

    WHAT GATE 2 DOES NOT UNDO. Gate 1 has already hashed the partition
    bytes by the time gate 2 runs -- that is the price of integrity-first
    ordering, and it is why `load_features` returning no rows is the
    guarantee, not "the file was never opened". The bytes on disk are the
    residual T-04-09 accepts: a features partition stays readable to
    anything that bypasses this loader, and moving or quarantining it is
    part of Phase 5's declaration step, not something a read-time refusal
    can do for it.

    AND THE PARTITION THAT STEP MUST MOVE IS NOT THE OBVIOUS ONE. Declaring
    day X held out makes `features/date=X` (which may not exist -- the most
    recent day never does) AND `features/date=X-1` unreadable here, because
    X-1's label tail is X's price path. It is the day BEFORE the declared
    date that carries the held-out bytes.
    """
    manifest = store.resolve_manifest(
        manifest_id,
        dataset,
        registry_root=registry_root,
        lake_root=lake_root,
        expected_tier=FEATURES_TIER,
    )
    assert_not_quarantined(
        refused_dates_for(manifest),
        symbol=manifest["symbol"],
        registry_root=registry_root,
        context=(
            f"load_features of manifest {manifest_id[:12]} (its long-horizon "
            "label tail stores the NEXT day's prevailing mids: mid_t * (1 + "
            "ret_10min_mid) reconstructs them)"
        ),
    )
    acks = store._enforce_dq_pause(
        manifest, registry_root=registry_root, lake_root=lake_root
    )
    store._log_provenance(manifest, acks)
    frames = store.read_verified_partitions(manifest, lake_root=Path(lake_root))
    return pl.concat(frames, how="vertical")
