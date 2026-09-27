"""FCST-04's stored prediction table: `(etime, decision_seq, pred)` written
once to the lake, addressed by a manifest, and read back with its row
sequence PROVEN identical to the frame it claims to score.

WHAT `pred` HOLDS, AND WHY IT IS NOT A PRICE (D-07-33). The column is the
model's predicted 10-second SIMPLE RETURN -- `spec/labels.toml`'s
`ret_10s_mid` -- never the raw USDT price `sim.kernel.run_sim_checked` wants.
Storing the price would bake one `mid` snapshot into the artifact and make
the table unreadable against any other book; storing the return keeps the
table a statement about the model alone. `models.conversion` owns the single
site where one becomes the other.

THE JOIN KEY IS `etime` ALONE (correction C1). Measured over the whole pool:
60,926,503 distinct `etime` values in 60,926,503 rows -- globally unique and
strictly sorted, because the last-row-per-`etime` decision rule is exactly
what makes it so. The frequently-repeated "many rows share an `etime`"
describes the RAW merged event stream, not the decision rows a fold hands
out. `decision_seq` rides along as a cross-check column and is NEVER a key
by itself: it restarts per partition, so it is not unique on its own.

ROW ORDER IS ASSERTED, NOT TRUSTED -- THIS IS THE PHASE'S TOP RISK
(D-07-31). polars 1.41.2 documents join output order as unspecified ("the
ordering might differ across Polars versions or even between different
runs"), and `harness.errata.mask_errata_cells` joins every look-role frame on
its way out of the accessor. The order holds today, measured, with and
without errata hits -- which is precisely why `assert_decision_order` and
`assert_table_aligned` are written now, while they pass. A `pred` array
shifted by one row against its decision rows produces a plausible P&L that
is pure look-ahead, and nothing anywhere fails.

`np.array_equal` ON THE WHOLE COLUMN IS A STRONGER STATEMENT THAN ANY JOIN.
It proves the two row SEQUENCES are the same sequence; a join proves only
that every key found a partner, which a permutation also satisfies. On 16.3M
int64s it costs milliseconds.

THIS TIER IS SHAPED LIKE `features/normalize.py`, NOT LIKE
`harness/segments.py` (07-PATTERNS.md §B). A prediction table NAMES BYTES ON
DISK, so it goes through `data.store.issue_manifest` like an ordinary tier --
unlike the segment and errata registries, which exist as sibling
content-addressed JSON precisely because `issue_manifest` refuses a manifest
that names nothing. And like a normalization artifact it is NOT date-indexed:
it belongs to a `(segment_manifest, segment, predictor)` triple (D-07-14), so
`dates=[]` is passed explicitly and `data.store.PREDICTIONS_TIER` is
deliberately absent from `BY_DATE_INDEXED_TIERS`.

THE LOADER'S TWO GATES, AND WHY ONLY ONE OF THEM APPLIES:

* IT DOES NOT CALL `store._enforce_dq_pause`, AND NOBODY SHOULD "FIX" THAT.
  That gate asks, per partition, for the DQ verdict of the DATE the partition
  covers. This tier's partitions cover a SEGMENT and carry no `date` field at
  all, and no DQ check ever emits a row for one -- so routing through the
  gate would read "no rows" as `missing` and pause every run forever.
  `test_load_prediction_table_does_not_enforce_a_dq_pause` pins both halves:
  the loader works, and the gate is inapplicable.
* IT DOES CALL `data.holdout.assert_not_quarantined`. A prediction column is
  a summary of the rows it was scored on, so the days those rows came from
  must still be readable. The dates are DERIVED -- from the scored segment
  manifest's own upstream feature manifests, narrowed to the partitions whose
  etime range actually overlaps the scored segment -- never read out of the
  artifact body. A body is write-once and content-hashed, so a gate that
  needed a new column would fail OPEN on exactly the tables that already
  exist; and narrowing matters because a `val` table must not be refused for
  a train day it never scored.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from data.capture.rotation import write_parquet_atomic
from data.holdout import assert_not_quarantined
from data.store import (
    FEATURES_TIER,
    PREDICTIONS_TIER,
    issue_manifest,
    manifest_path,
    read_verified_partitions,
    resolve_manifest,
)
from harness.segments import read_segment_manifest, segment_manifest_path

__all__ = [
    "PREDICTIONS_SCHEMA_VERSION",
    "PREDICTION_TABLE_SCHEMA",
    "PREDICTOR_DIR_PREFIX_LEN",
    "SCORED_SEGMENT_INPUT_ROLE",
    "PredictionTable",
    "assert_decision_order",
    "assert_table_aligned",
    "load_prediction_table",
    "partition_overlaps_segment",
    "prediction_table_path",
    "predictions_dataset",
    "scored_segment_input",
    "write_prediction_table",
]

#: Bumped when the stored columns change. Asserted on every read, so an old
#: table cannot be read as a new one. It lives in the MANIFEST rather than in
#: a body column (which is where `features/normalize.py` keeps its own):
#: D-07-16 pins this tier's body to exactly three columns, and a fourth one
#: repeating a constant over 11M rows would contradict it.
PREDICTIONS_SCHEMA_VERSION = 1

#: D-07-16, exactly. `pred` is Float64 and NOT Float32: the kernel quantises
#: it to ticks with a symmetric floor/ceil rule, and a float32 rounding error
#: near the half-tick boundary flips a trigger -- the precise hazard Phase 6
#: spent a plan fixing.
PREDICTION_TABLE_SCHEMA: dict[str, pl.DataType] = {
    "etime": pl.Int64,
    "decision_seq": pl.Int64,
    "pred": pl.Float64,
}

#: How much of the 64-hex `predictor_id` names the partition directory. The
#: FULL id is recorded in the manifest's scored-segment input entry, because
#: a truncated directory name cannot be inverted and the three-column body
#: has nowhere to carry it.
PREDICTOR_DIR_PREFIX_LEN = 16

#: The `role` of the ONE `inputs[]` entry `write_prediction_table` builds
#: itself. Everything the loader derives -- the scored segment manifest, the
#: segment name, the full `predictor_id`, and through them the dates the
#: holdout gate judges -- hangs off this entry, so it is found by role and a
#: manifest carrying none (or two) is refused rather than read.
SCORED_SEGMENT_INPUT_ROLE = "scored_segment"

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_DIR_TOKEN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


@dataclass(frozen=True)
class PredictionTable:
    """A loaded table plus the triple it belongs to.

    `table` is the three-column body; everything else answers "whose
    predictions are these, over which rows" and is read from the manifest,
    never from the body.
    """

    manifest_id: str
    symbol: str
    segment_manifest_id: str
    segment_name: str
    predictor_id: str
    schema_version: int
    table: pl.DataFrame


def predictions_dataset(symbol: str) -> str:
    """`"<SYMBOL>.predictions"` -- a namespace disjoint from the curated,
    features and features_norm ones, so no manifest of this tier can ever
    collide with a pointer another loader follows."""
    return f"{symbol}.{PREDICTIONS_TIER}"


def _require_hex64(value: str, what: str) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise ValueError(
            f"{what} must be a 64-character lowercase hex sha256, got "
            f"{value!r} -- the partition directory truncates it to "
            f"{PREDICTOR_DIR_PREFIX_LEN} characters, so a short or "
            "differently-cased id would silently address another table"
        )
    return value


def _require_dir_token(value: str, what: str) -> str:
    if not isinstance(value, str) or not _DIR_TOKEN.match(value):
        raise ValueError(
            f"{what} must match {_DIR_TOKEN.pattern} to be a partition "
            f"directory name, got {value!r} -- a path separator, a '=' or a "
            "leading dot in a hive key is a path, not a name"
        )
    return value


def prediction_table_path(
    lake_root: Path,
    symbol: str,
    segment_manifest_id: str,
    segment_name: str,
    predictor_id: str,
) -> Path:
    """`lake_root/predictions/symbol=<SYM>/segment_manifest=<id>/
    segment=<name>/predictor=<first 16 of predictor_id>/part-<ns>.parquet`.

    The directory names say what this table BELONGS TO rather than what day
    it "is" (D-07-14's triple), exactly as `features/normalize.py`'s
    `train_end=` says which fold its parameters end at. No `stream=` level:
    this tier is addressed by manifest id only.

    The `part-<ns>` leaf is load-bearing, not decoration -- it is what makes
    `write_prediction_table`'s write-once guard (a glob of the PARENT) able
    to fire at all.
    """
    _require_hex64(segment_manifest_id, "segment_manifest_id")
    _require_hex64(predictor_id, "predictor_id")
    _require_dir_token(symbol, "symbol")
    _require_dir_token(segment_name, "segment_name")
    return (
        Path(lake_root)
        / PREDICTIONS_TIER
        / f"symbol={symbol}"
        / f"segment_manifest={segment_manifest_id}"
        / f"segment={segment_name}"
        / f"predictor={predictor_id[:PREDICTOR_DIR_PREFIX_LEN]}"
        / f"part-{time.time_ns()}.parquet"
    )


def assert_decision_order(frame: pl.DataFrame) -> np.ndarray:
    """Return `frame["etime"]` as int64, having proven it is STRICTLY
    ascending. Raises `ValueError` naming the first offending row.

    Called at every entry to a fit, a prediction and a simulation, and on
    every table read back off the lake. Three reasons, none of them
    theoretical:

    * `sim.kernel.run_sim` is a SEQUENTIAL scan over decision rows. Fed a
      reordered frame it does not fail -- it simulates a different, wrong
      day, and reports a P&L for it.
    * polars' join order is unspecified by default (`maintain_order=None`
      resolves to `"none"`, documented as "might differ across Polars
      versions or even between different runs"), and
      `harness.errata.mask_errata_cells` joins on EVERY look-role frame.
    * `etime` being strictly ascending is also what makes it a usable key:
      strictly ascending implies unique, so `assert_table_aligned`'s
      `array_equal` is comparing two sequences of distinct keys.
    """
    etime = frame["etime"].to_numpy()
    if etime.size > 1:
        steps = np.diff(etime)
        if not bool(np.all(steps > 0)):
            first = int(np.flatnonzero(steps <= 0)[0])
            raise ValueError(
                "assert_decision_order: etime is not strictly ascending -- "
                f"row {first + 1} ({int(etime[first + 1])}) does not exceed "
                f"row {first} ({int(etime[first])}). sim.kernel.run_sim is a "
                "sequential scan and would be silently wrong on this frame. "
                "polars' join order is unspecified by default and "
                "harness.errata.mask_errata_cells joins on every look-role "
                "frame, so this is asserted rather than assumed (D-07-31)."
            )
    return etime


def assert_table_aligned(frame: pl.DataFrame, table: pl.DataFrame) -> None:
    """Prove `table`'s rows ARE `frame`'s rows, in the same order.

    Height, then `np.array_equal` on `etime`, then on `decision_seq` -- each
    with its own message, so a failure says which of the three broke. This
    is a stronger statement than any join: it proves the two row sequences
    are the SAME SEQUENCE, not merely that every key found a partner.
    """
    etime = assert_decision_order(frame)
    if table.height != frame.height:
        raise ValueError(
            f"assert_table_aligned: prediction table has {table.height} rows "
            f"but the frame it claims to score has {frame.height} -- a table "
            "is emitted positionally from the scored frame and can only "
            "differ in height if it was built from another one"
        )
    if not np.array_equal(table["etime"].to_numpy(), etime):
        raise ValueError(
            "assert_table_aligned: the prediction table's etime sequence is "
            "not the scored frame's etime sequence. Both are strictly "
            "ascending, so this is a different set of rows or the same rows "
            "shifted -- either way every pred would be attributed to the "
            "wrong decision row, which reads downstream as skill (D-07-31)"
        )
    if not np.array_equal(
        table["decision_seq"].to_numpy(), frame["decision_seq"].to_numpy()
    ):
        raise ValueError(
            "assert_table_aligned: etime matches but decision_seq does not. "
            "decision_seq is carried as a CROSS-CHECK, never as a key "
            "(correction C1: it is not unique on its own); a disagreement "
            "here means the two frames came from different partitions"
        )


def partition_overlaps_segment(
    etime_min: int, etime_max: int, start_ns: int, end_ns: int
) -> bool:
    """Does a partition covering the INCLUSIVE `[etime_min, etime_max]` hold
    any row of the HALF-OPEN segment range `[start_ns, end_ns)`?

    The two conventions are not the same and mixing them is how an
    off-by-one date enters a holdout gate: a partition entry's `etime_min`/
    `etime_max` are the first and last row's own etimes (both present in the
    data), while a segment's `end_ns` is one past its last row (D-05-03).
    A zero-width segment -- the `held_out` sentinel -- holds no rows at all,
    so it overlaps nothing. That case needs its own clause: the two
    comparisons below are both satisfied by any partition straddling a
    zero-width range, which is how "empty" would otherwise read as "matches
    everything".
    """
    if int(start_ns) >= int(end_ns):
        return False
    return int(etime_min) < int(end_ns) and int(etime_max) >= int(start_ns)


def scored_segment_input(
    segment_manifest_id: str,
    *,
    segment_name: str,
    predictor_id: str,
    registry_root: Path,
) -> dict:
    """The one `inputs[]` entry that makes a prediction manifest walkable:
    the segment manifest whose rows were scored, by absolute path and file
    sha256, plus the segment name and the FULL `predictor_id`.

    Provenance chains segment manifest -> (whatever the caller adds, e.g. the
    `features_norm` artifact) -> predictions, so MLflow's `data_hash`
    resolves to something a reader can follow. The path is absolute, which is
    `issue_manifest`'s own convention for `inputs[]` (provenance may span
    roots); the loader re-derives it from ITS registry root rather than
    trusting the recorded string, so a manifest still resolves on another
    machine.
    """
    _require_hex64(segment_manifest_id, "segment_manifest_id")
    _require_hex64(predictor_id, "predictor_id")
    _require_dir_token(segment_name, "segment_name")
    path = segment_manifest_path(Path(registry_root), segment_manifest_id).resolve()
    if not path.exists():
        raise ValueError(
            f"scored_segment_input: segment manifest "
            f"{segment_manifest_id[:12]} is not in this registry ({path}) -- "
            "refusing to record provenance for a segment nobody can resolve"
        )
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "manifest_id": segment_manifest_id,
        "role": SCORED_SEGMENT_INPUT_ROLE,
        "segment_name": segment_name,
        "predictor_id": predictor_id,
    }


def write_prediction_table(
    table: pl.DataFrame,
    *,
    symbol: str,
    segment_manifest_id: str,
    segment_name: str,
    predictor_id: str,
    inputs: list[dict] | None = None,
    lake_root: Path,
    registry_root: Path,
    code_hash: str,
) -> dict:
    """Write one prediction table and issue its manifest; return the
    manifest dict.

    `table` must already be `PREDICTION_TABLE_SCHEMA`-shaped, non-empty,
    strictly `etime`-ascending and null-free. NULL IS REFUSED IN EVERY
    COLUMN, `pred` INCLUDED, and the reason is the one `sim/arrays.py`
    documents: polars' `.to_numpy()` on a nullable Float64 column returns a
    NaN-filled COPY and does not raise. A row with no prediction is spelled
    NaN here -- one spelling, which survives the Parquet round trip as the
    same bits -- so a null can only mean the writer lost track of which
    spelling it was using.

    `dates=[]` IS PASSED EXPLICITLY AND IS LOAD-BEARING (D-07-26).
    `issue_manifest` computes `covered_dates = dates if dates is not None
    else sorted({p["date"] for p in partitions})` BEFORE it consults the
    tier, and a predictions partition entry carries no `date` key -- so the
    default branch raises a bare `KeyError`, and a by-date pointer here
    would be a date-addressable entry point into an artifact that belongs to
    a segment.
    """
    lake_root, registry_root = Path(lake_root), Path(registry_root)
    if table.schema != dict(PREDICTION_TABLE_SCHEMA):
        raise ValueError(
            f"write_prediction_table: table schema {table.schema} does not "
            f"match PREDICTION_TABLE_SCHEMA {dict(PREDICTION_TABLE_SCHEMA)}"
        )
    if table.height == 0:
        raise ValueError(
            "write_prediction_table: refusing to store an empty table -- a "
            "manifest that names zero scored rows verifies nothing"
        )
    for column in PREDICTION_TABLE_SCHEMA:
        nulls = table[column].null_count()
        if nulls:
            raise ValueError(
                f"write_prediction_table: column {column!r} has {nulls} "
                "null value(s). A nullable Float64 column becomes a "
                "NaN-filled COPY at .to_numpy() and the reader cannot tell; "
                "a row with no prediction is NaN, never null"
            )
    etime = assert_decision_order(table)

    final_path = prediction_table_path(
        lake_root, symbol, segment_manifest_id, segment_name, predictor_id
    )
    # THE PARENT, NOT THE FILE (04-REVIEW.md WR-05, copied here because the
    # bug it records is one this file would otherwise repeat verbatim).
    # Testing `final_path.exists()` on a path whose name embeds
    # `time.time_ns()`, constructed on the line above, is a guard that can
    # never fire -- in the normalization tier it let two artifacts for one
    # key land side by side, each with its own manifest. Globbing the
    # `predictor=` directory is the version of the rule that is enforced.
    existing = sorted(final_path.parent.glob("part-*.parquet"))
    if existing:
        raise FileExistsError(
            f"prediction table directory {final_path.parent} already holds "
            f"{existing[0].name} -- partitions are write-once. A second "
            "scoring of this (segment_manifest, segment, predictor) triple "
            "is a new predictor_id or a new segment manifest, never a "
            "second file beside the first."
        )
    write_parquet_atomic(table, final_path, compression="zstd")

    on_disk = final_path.read_bytes()
    stat = final_path.stat()
    partition_entry = {
        "path": str(final_path.relative_to(lake_root)),
        "sha256": hashlib.sha256(on_disk).hexdigest(),
        "rows": table.height,
        # size_bytes/mtime_ns feed check_no_manifest_rewrite's cheap
        # per-commit `verify_manifest_fast` leg; omitting them breaks a hook.
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "etime_min": int(etime[0]),
        "etime_max": int(etime[-1]),
    }
    return issue_manifest(
        dataset=predictions_dataset(symbol),
        symbol=symbol,
        stream=PREDICTIONS_TIER,
        tier=PREDICTIONS_TIER,
        schema_version=PREDICTIONS_SCHEMA_VERSION,
        inputs=[
            scored_segment_input(
                segment_manifest_id,
                segment_name=segment_name,
                predictor_id=predictor_id,
                registry_root=registry_root,
            ),
            *(inputs or []),
        ],
        partitions=[partition_entry],
        code_hash=code_hash,
        registry_root=registry_root,
        dates=[],
    )


def _scored_segment_entry(manifest: dict) -> dict:
    """The manifest's single `SCORED_SEGMENT_INPUT_ROLE` input.

    FAILS CLOSED on none and on more than one. None means nothing records
    which rows were scored, so the holdout gate has nothing to judge; two
    mean the table claims two provenances and the loader would silently
    pick whichever came first.
    """
    entries = [
        entry
        for entry in manifest.get("inputs") or []
        if isinstance(entry, dict) and entry.get("role") == SCORED_SEGMENT_INPUT_ROLE
    ]
    if len(entries) != 1:
        raise ValueError(
            f"load_prediction_table: manifest {manifest['manifest_id'][:12]} "
            f"carries {len(entries)} inputs[] entries with role "
            f"{SCORED_SEGMENT_INPUT_ROLE!r}, expected exactly 1 -- a table "
            "that cannot say which segment it scored cannot be gated, and an "
            "ungateable table must not be read"
        )
    entry = entries[0]
    for key in ("manifest_id", "sha256", "segment_name", "predictor_id"):
        if not entry.get(key):
            raise ValueError(
                f"load_prediction_table: the {SCORED_SEGMENT_INPUT_ROLE!r} "
                f"input of {manifest['manifest_id'][:12]} has no {key!r}"
            )
    return entry


def _scored_dates(manifest: dict, *, registry_root: Path) -> list[str]:
    """The dates whose decision rows this table scored, DERIVED from the
    scored segment manifest rather than read out of the body.

    Two hops, and both are checked. The scored-segment input names a segment
    manifest: its bytes must still hash to what this table recorded, and
    `harness.segments.read_segment_manifest` re-verifies the body against
    its own id. That manifest names its upstream FEATURE manifests, and
    those are the only place a `date` exists at all -- a segment manifest
    carries etime ranges, and a prediction body carries three numeric
    columns.

    NARROWED to the partitions whose etime range overlaps THIS segment
    (`partition_overlaps_segment`). Every upstream partition would be the
    dates the POOL spans, which for a `val` table includes days it never
    scored -- and a `val` table refused because a train day was quarantined
    is a false refusal that teaches a reader to route around the gate.

    FAIL-CLOSED throughout: a segment manifest that is missing, whose bytes
    moved, that declares no segment of the recorded name, an upstream
    feature manifest that does not resolve, or an empty date set -- each
    raises. A table that cannot say which days it summarises must not be
    read.
    """
    registry_root = Path(registry_root)
    entry = _scored_segment_entry(manifest)
    segment_manifest_id = entry["manifest_id"]
    path = segment_manifest_path(registry_root, segment_manifest_id)
    if not path.exists():
        raise ValueError(
            f"load_prediction_table: scored segment manifest "
            f"{segment_manifest_id[:12]} is not in this registry ({path}) -- "
            "refusing to read a table whose scored dates cannot be "
            "established"
        )
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != entry["sha256"]:
        raise ValueError(
            f"load_prediction_table: scored segment manifest {path} hashes "
            f"to {actual[:12]} but the table recorded {entry['sha256'][:12]} "
            "-- refusing to establish scored dates from bytes that moved"
        )
    segment_manifest = read_segment_manifest(registry_root, segment_manifest_id)
    named = [
        segment
        for segment in segment_manifest["segments"]
        if segment["name"] == entry["segment_name"]
    ]
    if len(named) != 1:
        raise ValueError(
            f"load_prediction_table: segment manifest "
            f"{segment_manifest_id[:12]} declares {len(named)} segments named "
            f"{entry['segment_name']!r}, expected exactly 1"
        )
    start_ns, end_ns = int(named[0]["start_ns"]), int(named[0]["end_ns"])
    symbol = segment_manifest["symbol"]

    dates: set[str] = set()
    upstream = segment_manifest["upstream_feature_manifest_ids"]
    for feature_manifest_id in upstream:
        feature_path = manifest_path(
            registry_root, f"{symbol}.{FEATURES_TIER}", feature_manifest_id
        )
        if not feature_path.exists():
            raise ValueError(
                f"load_prediction_table: upstream feature manifest "
                f"{feature_manifest_id[:12]} named by segment manifest "
                f"{segment_manifest_id[:12]} is not in this registry "
                f"({feature_path})"
            )
        body = json.loads(feature_path.read_bytes())
        for part in body.get("partitions", []):
            if "date" not in part:
                continue
            if partition_overlaps_segment(
                part["etime_min"], part["etime_max"], start_ns, end_ns
            ):
                dates.add(part["date"])
    if not dates:
        raise ValueError(
            f"load_prediction_table: none of the {len(upstream)} upstream "
            f"feature partition(s) of segment manifest "
            f"{segment_manifest_id[:12]} overlaps segment "
            f"{entry['segment_name']!r} ([{start_ns}, {end_ns})) -- a table "
            "the holdout gate cannot judge must not be read"
        )
    return sorted(dates)


def load_prediction_table(
    manifest_id: str,
    dataset: str,
    *,
    registry_root: Path,
    lake_root: Path,
) -> PredictionTable:
    """Resolve and read one prediction table: the manifest must re-hash to
    its id, its tier must be `predictions`, every partition must resolve
    inside `lake_root/predictions/`, the bytes read must hash to what the
    manifest names, the body must be exactly `PREDICTION_TABLE_SCHEMA`, and
    the stored rows must themselves be strictly `etime`-ascending.

    See this module's docstring for the two-gate reasoning: no DQ pause
    (inapplicable to a tier with no dates), and the holdout gate on dates
    derived from the scored segment manifest.

    READS THE BYTES TWICE, ON PURPOSE. `resolve_manifest` hashes each
    partition and `read_verified_partitions` re-reads and re-hashes the same
    buffer it parses; the window between the two is what that pair exists to
    close. Do not "optimise" it -- budget the time in the caller.
    """
    registry_root, lake_root = Path(registry_root), Path(lake_root)
    manifest = resolve_manifest(
        manifest_id,
        dataset,
        registry_root=registry_root,
        lake_root=lake_root,
        expected_tier=PREDICTIONS_TIER,
    )
    entry = _scored_segment_entry(manifest)
    assert_not_quarantined(
        _scored_dates(manifest, registry_root=registry_root),
        symbol=manifest["symbol"],
        registry_root=registry_root,
        context=(
            f"load_prediction_table of {manifest_id[:12]} (its pred column "
            f"summarises the decision rows of segment "
            f"{entry['segment_name']!r} on those days)"
        ),
    )
    frames = read_verified_partitions(manifest, lake_root=lake_root)
    body = pl.concat(frames, how="vertical")
    if body.schema != dict(PREDICTION_TABLE_SCHEMA):
        raise ValueError(
            f"load_prediction_table: table schema {body.schema} does not "
            f"match PREDICTION_TABLE_SCHEMA {dict(PREDICTION_TABLE_SCHEMA)}"
        )
    if manifest["schema_version"] != PREDICTIONS_SCHEMA_VERSION:
        raise ValueError(
            f"load_prediction_table: table schema_version "
            f"{manifest['schema_version']} != {PREDICTIONS_SCHEMA_VERSION}"
        )
    assert_decision_order(body)
    return PredictionTable(
        manifest_id=manifest["manifest_id"],
        symbol=manifest["symbol"],
        segment_manifest_id=entry["manifest_id"],
        segment_name=entry["segment_name"],
        predictor_id=entry["predictor_id"],
        schema_version=int(manifest["schema_version"]),
        table=body,
    )
