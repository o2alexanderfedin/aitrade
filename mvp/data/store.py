"""Manifest issuance, resolution, and the default curated-tier loader.

The immutability and addressability core of the data lake (DATA-05,
DATA-06): a manifest is a claim about bytes on disk, and every read path
this module exposes VERIFIES that claim against the file currently on disk
before returning anything -- stored-hash-vs-on-disk-hash, never
rebuild-vs-compare (03-RESEARCH.md Pattern 3; a future polars/pyarrow
writer bump could change the byte encoding for logically-identical data,
which would make a rebuild-and-compare check fail on unchanged data).

Manifest JSON lives under `LAKE_REGISTRY_ROOT / "manifests" / <dataset> /
<manifest_id>.json` -- git-committed (see `data/lake_paths.py`'s docstring
for why this is a separate root from the physical SSD-only `lake_root()`).
`partitions[].path` are lake-root-RELATIVE; `inputs[].path` are absolute
(provenance may span the lake's own `raw/` tier AND the capture daemon's
separate `capture/parsed/` root -- two different physical roots, so only an
absolute path works for both without inventing a second root concept).

`load_curated` is the ONLY reading path this phase builds that is called
"the default loader" -- it never constructs a path under the quarantined
tier, and it refuses at runtime any manifest whose tier is not `curated` or
whose partition paths resolve outside `lake_root/curated/` (03-REVIEW.md
CR-04; see `resolve_manifest`).

DQ pause enforcement (Plan 04, DATA-07): `load_curated` ALSO refuses to
return rows for any requested day whose data-quality status is
`"failed"`, `"degraded"`, or has no DQ report at all, unless a matching
acknowledgement file is committed under
`LAKE_REGISTRY_ROOT / "dq_acknowledgements"`. This is checked AFTER
`resolve_manifest`'s hash verification (a corrupted/mismatched manifest
must never even get to a DQ conversation) and reads ONLY
`lake_root()/dq/date=.../report.parquet` -- this module never imports
`data.dq.report` (that module is the one PRODUCING the report this one
reads; importing it back would be circular).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import polars as pl

__all__ = [
    "CURATED_TIER",
    "DQPauseError",
    "ManifestHashMismatch",
    "ManifestTierError",
    "canonicalize_manifest",
    "compute_manifest_id",
    "dq_acknowledgement_path",
    "dq_report_path",
    "issue_manifest",
    "resolve_manifest",
    "load_curated",
    "manifest_path",
    "by_date_index_path",
]


class DQPauseError(ValueError):
    """Raised by `load_curated` when any date the requested manifest covers
    has an unacknowledged `"failed"`/`"degraded"`/missing-report DQ status
    (DATA-07's mechanical training pause). Add a git-committed
    acknowledgement JSON at `dq_acknowledgement_path(...)` to proceed."""


#: The only tier the default loader (`load_curated`) and the by-date index
#: ever serve. Every other tier is reachable only through its own audited
#: module, which passes its own tier name to `resolve_manifest`.
CURATED_TIER = "curated"


class ManifestTierError(ValueError):
    """Raised by `resolve_manifest` BEFORE any partition is read when a
    manifest's `tier` is not the tier the caller asked for, or when any
    `partitions[].path` does not resolve (after `..` normalisation and
    symlink resolution) to a file under `lake_root/<expected_tier>/`
    (03-REVIEW.md CR-04): the default loader has no code path to another
    tier, not a flag, not an absolute path, not a symlink."""


class ManifestHashMismatch(ValueError):
    """Raised by `resolve_manifest` when a partition's recomputed on-disk
    sha256 does not match the manifest's stored value, OR when the
    manifest's own body hash no longer matches its `manifest_id` -- either
    way, the caller must never receive rows from a manifest that does not
    resolve to exactly the bytes it names (RP-2)."""

    def __init__(self, path: str, expected: str, actual: str) -> None:
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"manifest hash mismatch for {path}: expected {expected}, got {actual}"
        )


def canonicalize_manifest(manifest: dict) -> bytes:
    """Return the canonical byte encoding of `manifest`'s body, EXCLUDING
    the `manifest_id` key itself -- the id is computed FROM the rest, never
    included in what it hashes (avoids a self-referential hash)."""
    body = {k: v for k, v in manifest.items() if k != "manifest_id"}
    return json.dumps(body, sort_keys=True).encode()


def compute_manifest_id(manifest: dict) -> str:
    """Return the sha256 hex digest of `canonicalize_manifest(manifest)`."""
    return hashlib.sha256(canonicalize_manifest(manifest)).hexdigest()


def manifest_path(registry_root: Path, dataset: str, manifest_id: str) -> Path:
    """Single source of truth for a manifest JSON's on-disk path."""
    return Path(registry_root) / "manifests" / dataset / f"{manifest_id}.json"


def dq_report_path(lake_root: Path, date: str) -> Path:
    """Single source of truth for a date's DQ report parquet path --
    matches `data.dq.report.dq_report_path` exactly (duplicated here, not
    imported, to avoid this module importing `data.dq.report` -- see
    module docstring)."""
    return Path(lake_root) / "dq" / f"date={date}" / "report.parquet"


def dq_acknowledgement_path(
    registry_root: Path, symbol: str, stream: str, date: str
) -> Path:
    """Single source of truth for a DQ acknowledgement JSON's on-disk path
    -- `LAKE_REGISTRY_ROOT / "dq_acknowledgements" / "{symbol}__{stream}__{date}.json"`,
    git-committed (same registry-root convention as `manifest_path`/
    `by_date_index_path` above)."""
    return (
        Path(registry_root) / "dq_acknowledgements" / f"{symbol}__{stream}__{date}.json"
    )


def by_date_index_path(
    registry_root: Path, dataset: str, symbol: str, stream: str, date: str
) -> Path:
    """Single source of truth for a by-date index pointer's on-disk path.

    NOT itself immutable -- a rebuild issues a NEW manifest and repoints
    this index; the OLD manifest file keeps resolving by its own id. This
    is why the index is a separate, mutable pointer rather than the
    immutability boundary itself (that boundary is the manifest JSON plus
    the partition files it names).
    """
    return (
        Path(registry_root)
        / "manifests"
        / dataset
        / "by-date"
        / f"{symbol}__{stream}__{date}.json"
    )


def _atomic_write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(body, sort_keys=True, indent=2))
    tmp_path.replace(path)


def issue_manifest(
    dataset: str,
    symbol: str,
    stream: str,
    tier: str,
    schema_version: int,
    inputs: list[dict],
    partitions: list[dict],
    code_hash: str,
    *,
    registry_root: Path,
    dates: list[str] | None = None,
) -> dict:
    """Build, hash, and git-committed-write a manifest; repoint the by-date
    index for every date in `dates` (default: every distinct `date` present
    in `partitions`).

    `inputs[].path` are absolute paths with their own `sha256`/`rows`.
    `partitions[].path` are lake-root-RELATIVE; each entry additionally
    carries `size_bytes` and `mtime_ns` (captured from the just-written
    file's own `stat()` at issuance time, alongside `sha256`) -- these feed
    `check_no_manifest_rewrite`'s cheap per-commit `verify_manifest_fast`
    path, which never needs to reopen file contents.

    Refuses (`ValueError`) a `partitions[].path` that any manifest already
    issued for `dataset` names (03-REVIEW.md CR-02): a partition path is
    write-once, so a second manifest naming it can only mean the file was
    rewritten in place. A rebuild writes a NEW `part-<ns>` file.

    Returns the full manifest dict (including the computed `manifest_id`).
    """
    new_paths = {p["path"] for p in partitions}
    dataset_dir = Path(registry_root) / "manifests" / dataset
    if new_paths and dataset_dir.exists():
        for existing_file in dataset_dir.glob("*.json"):
            existing = json.loads(existing_file.read_text())
            reused = new_paths & {p["path"] for p in existing.get("partitions", [])}
            if reused:
                raise ValueError(
                    f"issue_manifest: partition path(s) {sorted(reused)} already "
                    f"named by manifest {existing_file.stem} -- partitions are "
                    "write-once; write a new part file instead of reusing a path"
                )

    body = {
        "dataset": dataset,
        "symbol": symbol,
        "stream": stream,
        "tier": tier,
        "schema_version": schema_version,
        "built_at": time.time_ns(),
        "code_hash": code_hash,
        "inputs": inputs,
        "partitions": partitions,
        "row_count": sum(p["rows"] for p in partitions),
        "etime_range": [
            min(p["etime_min"] for p in partitions) if partitions else None,
            max(p["etime_max"] for p in partitions) if partitions else None,
        ],
    }
    manifest_id = compute_manifest_id(body)
    manifest = {"manifest_id": manifest_id, **body}

    _atomic_write_json(manifest_path(registry_root, dataset, manifest_id), manifest)

    # The by-date index is the CURATED tier's date -> manifest pointer, read
    # by the DQ report and the curated build. A manifest of any other tier
    # never writes it: a quarantined segment issued for a date must not
    # silently re-point the curated pointer for that date (03-REVIEW.md
    # CR-04). Other tiers are addressed by manifest_id only.
    covered_dates = (
        dates if dates is not None else sorted({p["date"] for p in partitions})
    )
    if tier != CURATED_TIER:
        covered_dates = []
    for date in covered_dates:
        _atomic_write_json(
            by_date_index_path(registry_root, dataset, symbol, stream, date),
            {"manifest_id": manifest_id},
        )

    return manifest


def _enforce_tier_containment(
    manifest: dict, manifest_file: Path, *, lake_root: Path, expected_tier: str
) -> None:
    """Refuse (`ManifestTierError`) a manifest of the wrong tier, or any
    partition whose path resolves outside `lake_root/<expected_tier>/`.
    Pure path arithmetic -- never opens a partition file."""
    tier = manifest.get("tier")
    if tier != expected_tier:
        raise ManifestTierError(
            f"{manifest_file}: manifest tier {tier!r} is not {expected_tier!r} "
            "-- refusing to resolve it through this loader"
        )
    tier_root = (Path(lake_root) / expected_tier).resolve()
    for part in manifest["partitions"]:
        rel = Path(part["path"])
        on_disk = (Path(lake_root) / rel).resolve()
        if rel.is_absolute() or not on_disk.is_relative_to(tier_root):
            raise ManifestTierError(
                f"{manifest_file}: partition path {part['path']!r} resolves to "
                f"{on_disk}, outside {tier_root} -- refusing to read it"
            )


def resolve_manifest(
    manifest_id: str,
    dataset: str,
    *,
    registry_root: Path,
    lake_root: Path,
    expected_tier: str,
) -> dict:
    """Read the committed manifest JSON and verify every claim it makes
    BEFORE returning it, in this order:

    1. the body must still hash to `manifest_id` (guards against a
       hand-edited `sha256` field inside an otherwise-untouched manifest);
    2. `manifest["tier"] == expected_tier`, and every `partitions[].path`
       resolves under `lake_root/<expected_tier>/` -- checked before any
       partition byte is read (`ManifestTierError`, 03-REVIEW.md CR-04).
       `expected_tier` is required, never defaulted: every caller names the
       one tier it is allowed to reach;
    3. each partition's recomputed on-disk sha256 must match its stored
       value.

    Raises `ManifestHashMismatch` on (1)/(3) -- RP-2: a manifest resolves to
    exactly the bytes it names, never silently returning wrong data.
    """
    path = manifest_path(registry_root, dataset, manifest_id)
    manifest = json.loads(path.read_text())

    recomputed_id = compute_manifest_id(manifest)
    if recomputed_id != manifest_id:
        raise ManifestHashMismatch(str(path), manifest_id, recomputed_id)

    _enforce_tier_containment(
        manifest, path, lake_root=Path(lake_root), expected_tier=expected_tier
    )

    for part in manifest["partitions"]:
        on_disk_path = Path(lake_root) / part["path"]
        if not on_disk_path.exists():
            raise ManifestHashMismatch(str(on_disk_path), part["sha256"], "<missing>")
        on_disk_hash = hashlib.sha256(on_disk_path.read_bytes()).hexdigest()
        if on_disk_hash != part["sha256"]:
            raise ManifestHashMismatch(str(on_disk_path), part["sha256"], on_disk_hash)

    return manifest


def _dq_status_for_date(
    lake_root: Path, symbol: str, stream: str, date: str
) -> tuple[str, str | None]:
    """Return `(status, detail)` for `(symbol, stream, date)`: the worst-of
    status across every row of that date's `report.parquet` matching
    `(symbol, stream)`.

    `"missing"` covers TWO distinct absences, both treated identically as
    a pause-requiring status (fail-closed -- nothing in this phase
    schedules the report to run automatically, so any day nobody has
    reported on yet must not silently pass):
    - no `report.parquet` at all for this date, or no row in it matching
      `(symbol, stream)` -- this `(symbol, stream, date)` was never
      reported on.
    - every matching row says `"n/a"` -- the report DID run, but no
      substantive check actually produced a signal for this
      `(symbol, stream, date)` (e.g. every check happened to be
      inapplicable that day), which must not be silently treated as a
      pass either.

    `"ok"` only when at least one row is `"ok"` and none is
    `"failed"`/`"degraded"`.
    """
    report_path = dq_report_path(lake_root, date)
    if not report_path.exists():
        return "missing", "no DQ report generated for this date"

    report = pl.read_parquet(report_path)
    rows = report.filter((pl.col("symbol") == symbol) & (pl.col("stream") == stream))
    if rows.height == 0:
        return "missing", "no DQ report generated for this date"

    statuses = set(rows["dq_status"].to_list())
    if "failed" in statuses:
        return "failed", None
    if "degraded" in statuses:
        return "degraded", None
    if "ok" in statuses:
        return "ok", None
    return (
        "missing",
        "every DQ check reported n/a for this date -- no substantive signal",
    )


def _enforce_dq_pause(manifest: dict, *, registry_root: Path, lake_root: Path) -> None:
    """DATA-07's mechanical training pause: for every date this manifest
    covers, require an `"ok"` status OR a matching committed acknowledgement.
    Raises `DQPauseError` naming every unacknowledged day if any remain.
    """
    symbol = manifest["symbol"]
    stream = manifest["stream"]
    dates = sorted({part["date"] for part in manifest["partitions"]})

    unacknowledged: list[tuple[str, str, str | None]] = []
    for date in dates:
        status, detail = _dq_status_for_date(Path(lake_root), symbol, stream, date)
        if status == "ok":
            continue
        ack_path = dq_acknowledgement_path(Path(registry_root), symbol, stream, date)
        if not ack_path.exists():
            unacknowledged.append((date, status, detail))

    if unacknowledged:
        summary = "; ".join(
            f"{date}: {status}" + (f" ({detail})" if detail else "")
            for date, status, detail in unacknowledged
        )
        example_path = dq_acknowledgement_path(
            Path(registry_root), symbol, stream, unacknowledged[0][0]
        )
        raise DQPauseError(
            f"DQ pause: {symbol}.{stream} has unacknowledged day(s): {summary}. "
            f"Add a git-committed acknowledgement JSON (e.g. {example_path}) to proceed."
        )


def load_curated(
    manifest_id: str, dataset: str, *, registry_root: Path, lake_root: Path
) -> pl.DataFrame:
    """Resolve `manifest_id` (verifying every partition's hash), enforce
    DATA-07's DQ pause (raises `DQPauseError` on any unacknowledged
    failed/degraded/missing-report day), and return the concatenated
    curated rows.

    The only reading path this phase builds that is called "the default
    loader". It reaches the curated tier only, enforced at RUNTIME, not just
    by the absence of a string: `resolve_manifest(expected_tier=CURATED_TIER)`
    refuses a manifest of any other tier and any partition path that
    resolves outside `lake_root/curated/` (`..`, absolute paths, symlinks)
    before a single partition byte is read (03-REVIEW.md CR-04). Hash
    verification runs BEFORE the DQ pause check: a manifest that fails
    integrity must never even reach a DQ conversation.
    """
    manifest = resolve_manifest(
        manifest_id,
        dataset,
        registry_root=registry_root,
        lake_root=lake_root,
        expected_tier=CURATED_TIER,
    )
    _enforce_dq_pause(manifest, registry_root=registry_root, lake_root=lake_root)
    frames = [
        pl.read_parquet(Path(lake_root) / part["path"])
        for part in manifest["partitions"]
    ]
    return pl.concat(frames, how="vertical")
