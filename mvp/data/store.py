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
`"failed"`, `"degraded"`, or has no DQ report at all, unless an
acknowledgement file naming every finding of that day is committed (a regular
file byte-identical to its blob in `HEAD`, WR-19) under
`LAKE_REGISTRY_ROOT / "dq_acknowledgements"`. This is checked AFTER
`resolve_manifest`'s hash verification (a corrupted/mismatched manifest
must never even get to a DQ conversation) and reads ONLY
`lake_root()/dq/date=.../report.parquet` -- this module never imports
`data.dq.report` (that module is the one PRODUCING the report this one
reads; importing it back would be circular).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import posixpath
import re
import subprocess
import time
import unicodedata
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from tools.git_env import scrubbed_git_env

__all__ = [
    "CURATED_TIER",
    "DQPauseError",
    "ManifestHashMismatch",
    "ManifestTierError",
    "canonicalize_manifest",
    "compute_manifest_id",
    "dq_acknowledgement_ids",
    "read_verified_partitions",
    "dq_acknowledgement_path",
    "dq_report_path",
    "issue_manifest",
    "resolve_manifest",
    "validate_dq_acknowledgement",
    "load_curated",
    "manifest_path",
    "manifests_for_dataset",
    "by_date_index_path",
    "partition_path_key",
    "partition_path_problem",
]


class DQPauseError(ValueError):
    """Raised by `load_curated` when any date the requested manifest covers
    has an unacknowledged `"failed"`/`"degraded"`/missing-report DQ status
    (DATA-07's mechanical training pause). Add a git-committed
    acknowledgement JSON at `dq_acknowledgement_path(...)` to proceed."""


#: The only tier the default loader (`load_curated`) ever serves. Every
#: other tier is reachable only through its own audited module, which
#: passes its own tier name to `resolve_manifest`.
CURATED_TIER = "curated"

#: The decision-row matrix tier (04-CONTEXT.md D-04-07), read by
#: `features.tier.load_features` and by nothing else.
FEATURES_TIER = "features"

#: The train-only normalization-parameter tier (D-04-06). Named here
#: alongside its sibling so the plan that uses it never has to edit this
#: file; it is deliberately NOT in `BY_DATE_INDEXED_TIERS` -- a
#: normalization artifact belongs to a fold, not to a date.
FEATURES_NORM_TIER = "features_norm"

#: The stored-prediction tier (07-CONTEXT.md D-07-26), read by
#: `models.predictions.load_prediction_table` and by nothing else. Named
#: here alongside its siblings so the plan that uses it never has to edit
#: this file; it is deliberately NOT in `BY_DATE_INDEXED_TIERS` -- a
#: prediction table belongs to a `(segment_manifest, segment, predictor)`
#: triple, exactly as a normalization artifact belongs to a fold, not to a
#: date. A caller of `issue_manifest` for this tier must therefore pass
#: `dates=[]` EXPLICITLY: `covered_dates` is computed from `p["date"]`
#: before the tier is consulted, and this tier's partition entries carry
#: no `date` key at all.
PREDICTIONS_TIER = "predictions"

#: Tiers whose manifests get a by-date `(dataset, symbol, stream, date)`
#: pointer. Everything else is addressable by `manifest_id` only.
#:
#: The CR-04 rule this preserves is that a QUARANTINED segment must never
#: re-point a pointer some loader will follow. It still holds with the
#: features tier added, for two independent reasons: the quarantined tier
#: is not a member of this set, and the features dataset namespace
#: (`"<SYMBOL>.features"` with `stream="features"`) is disjoint from the
#: curated one (`"<SYMBOL>.trade"` / `"<SYMBOL>.bookTicker"`), so no
#: manifest of any tier can collide with a curated or a features pointer.
#:
#: Adding a tier here is a deliberate, reviewed act: it grants that tier a
#: date-addressable entry point that anything holding a date can follow.
BY_DATE_INDEXED_TIERS: frozenset[str] = frozenset({CURATED_TIER, FEATURES_TIER})


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


def manifests_for_dataset(registry_root: Path, dataset: str) -> list[dict]:
    """Every manifest JSON directly under `registry_root/manifests/<dataset>/`,
    parsed, sorted by filename (`manifest_id`) for deterministic ordering.

    `[]` when the dataset directory does not exist yet -- no manifest has
    ever been issued for it. `Path.glob("*.json")` is non-recursive by
    construction, so it never descends into the `by-date/` subdirectory:
    no extra filter is needed to exclude the mutable by-date pointers from
    this immutable-manifest listing (T-06-02's discovery primitive --
    `data/dq/report.py:build_feature_report_rows_for_date` uses this
    instead of resolving only the by-date pointer's CURRENT manifest, so a
    superseded manifest's DQ report row is regenerated too).
    """
    dataset_dir = Path(registry_root) / "manifests" / dataset
    if not dataset_dir.exists():
        return []
    return [json.loads(path.read_text()) for path in sorted(dataset_dir.glob("*.json"))]


def partition_path_key(path: str) -> str:
    """The identity of a lake-relative partition path for write-once
    comparisons (03-REVIEW-ITER2.md WR-13): `posixpath.normpath` (so
    `curated/./x`, `curated//x`, `curated/y/../x` and `curated/x/` are one
    path, as they are to `pathlib` and `open`), Unicode NFC, then casefold
    (APFS, where the lake lives, is case- and normalisation-insensitive by
    default). Over-approximates on a case-sensitive filesystem, which this
    layout never relies on."""
    return unicodedata.normalize("NFC", posixpath.normpath(path)).casefold()


def partition_path_problem(path: object) -> str | None:
    """`None` if `path` is a well-formed lake-relative partition path, else
    why not (03-REVIEW-ITER3.md IN-19). Well-formed: a non-empty string, not
    absolute, whose first normalised segment is not `..` (`normpath` leaves
    `../curated/x` unchanged, so a canonical-spelling test alone passed it),
    and spelled canonically (`normpath(path) == path`)."""
    if not isinstance(path, str) or not path:
        return f"partition path {path!r} is not a non-empty string"
    if path.startswith("/"):
        return f"partition path {path!r} is absolute (must be lake-root-relative)"
    normalised = posixpath.normpath(path)
    if normalised == ".." or normalised.startswith("../"):
        return f"partition path {path!r} escapes the lake root ({normalised!r})"
    if normalised != path:
        return f"partition path {path!r} is not canonical (expected {normalised!r})"
    return None


#: The manifest sources this codebase knows how to judge. A source outside
#: this set has no `rtime` bounds, so it cannot be scored (WR-03).
KNOWN_MANIFEST_SOURCES: frozenset[str] = frozenset({"archive", "capture"})

#: What `manifest_source` returns when it cannot name ONE known source.
UNKNOWN_MANIFEST_SOURCE = "unknown"

#: A raw-archive input names its source in a hive component, because the
#: backfill staging tree is partitioned by it.
_SOURCE_COMPONENT_RE = re.compile(r"/source=([^/]+)/")

#: A capture input carries no `source=` component at all -- it is an
#: absolute path into the capture daemon's OWN tree
#: (`data.capture.rotation`: `<capture_root>/symbol=.../stream=.../date=...`)
#: and `curated_build` records `str(path.resolve())` verbatim. Capture is
#: therefore recognised POSITIVELY, by requiring all three hive components;
#: it is NOT the fall-through, which is what made every unknown source
#: silently a capture one (WR-03).
#:
#: RESIDUAL: a future source staged under the same three components and no
#: `source=` marker would read as capture. Any new source must be staged
#: with its own `/source=<name>/` component -- the backfill staging tree
#: already is -- and that is the contract `check_rtime_plausibility`'s
#: unknown-source message spells out.
_CAPTURE_PATH_RE = re.compile(r"/symbol=[^/]+/stream=[^/]+/date=[^/]+/")


def _input_source(path: object) -> str | None:
    """The source one input path names, or `None` if it names none."""
    if not isinstance(path, str):
        return None
    found = _SOURCE_COMPONENT_RE.search(path)
    if found is not None:
        return found.group(1)
    return "capture" if _CAPTURE_PATH_RE.search(path) else None


def manifest_source(manifest: dict) -> str:
    """The ONE source every one of this manifest's inputs came from --
    `"archive"`, `"capture"`, or `UNKNOWN_MANIFEST_SOURCE` -- read from the
    manifest itself (committed and bound to its id), never from the mutable
    `build_stats.json`.

    Lives here, not in `data.ingest.curated_build`, because two readers need
    it: the builder's supersede logic and the DQ report's source-dependent
    `rtime` gate (`data.dq.checks.check_rtime_plausibility`).

    A THREE-WAY ANSWER, NOT A DEFAULT (03-REVIEW-FOLLOWUPS.md WR-03). This
    used to return `"archive"` only when every input path contained
    `/source=archive/` and `"capture"` for everything else -- so a manifest
    with no `inputs` key, an empty list, a mixed list, or a future
    `/source=tardis/` was judged by the capture branch's
    `[-60 s, +3600 s]` `rtime - etime` skew window. A vendor download's
    `rtime` is a DOWNLOAD time, days from `etime`; Tardis.dev is this
    project's documented plan for historical L1, so the first vendor-sourced
    dataset would have been paused on every single day, blamed on a window
    that was never meant for it. Saying `"unknown"` out loud costs the same
    pause and buys an actionable message.
    """
    sources: set[str | None] = {
        _input_source(item.get("path") if isinstance(item, dict) else None)
        for item in manifest.get("inputs") or []
    }
    if len(sources) == 1:
        only = next(iter(sources))
        if only in KNOWN_MANIFEST_SOURCES:
            return only
    return UNKNOWN_MANIFEST_SOURCE


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
    rewritten in place. A rebuild writes a NEW `part-<ns>` file. Paths are
    compared by `partition_path_key`, never as raw strings (WR-13).

    Also refuses (IN-19) a malformed path (`partition_path_problem`: absolute,
    escaping the lake root, or non-canonical) and one partition named twice
    within the new manifest under any spelling.

    Returns the full manifest dict (including the computed `manifest_id`).
    """
    if not partitions:
        raise ValueError(
            "issue_manifest: no partitions -- a manifest that names nothing "
            "verifies nothing (03-REVIEW-ITER2.md IN-15)"
        )
    new_paths: dict[str, str] = {}
    for part in partitions:
        if not isinstance(part.get("path"), str) or not part["path"]:
            raise ValueError(
                f"issue_manifest: {partition_path_problem(part.get('path'))}"
            )
        key = partition_path_key(part["path"])
        if key in new_paths:
            raise ValueError(
                f"issue_manifest: partition {part['path']!r} is named more than "
                f"once in this manifest (also as {new_paths[key]!r})"
            )
        new_paths[key] = part["path"]
    dataset_dir = Path(registry_root) / "manifests" / dataset
    if new_paths and dataset_dir.exists():
        for existing_file in dataset_dir.glob("*.json"):
            existing = json.loads(existing_file.read_text())
            existing_keys = {
                partition_path_key(p["path"]) for p in existing.get("partitions", [])
            }
            reused = {new_paths[k] for k in new_paths.keys() & existing_keys}
            if reused:
                raise ValueError(
                    f"issue_manifest: partition path(s) {sorted(reused)} already "
                    f"named by manifest {existing_file.stem} -- partitions are "
                    "write-once; write a new part file instead of reusing a path"
                )
    # After the reuse refusal, so a reuse under another spelling is still
    # reported as the reuse it is (WR-13).
    for part in partitions:
        problem = partition_path_problem(part["path"])
        if problem is not None:
            raise ValueError(f"issue_manifest: {problem}")

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

    # The by-date index is a date -> manifest pointer for the tiers in
    # BY_DATE_INDEXED_TIERS (curated, read by the DQ report and the curated
    # build; features, read by the DQ report and the feature loader). A
    # manifest of any other tier never writes one: a quarantined segment
    # issued for a date must not silently re-point a pointer some loader
    # will follow (03-REVIEW.md CR-04). See BY_DATE_INDEXED_TIERS for why
    # adding the features tier cannot make a quarantined manifest reachable.
    covered_dates = (
        dates if dates is not None else sorted({p["date"] for p in partitions})
    )
    if tier not in BY_DATE_INDEXED_TIERS:
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
    partition that is not genuinely contained in `lake_root/<expected_tier>/`.

    Path arithmetic plus `lstat` -- never opens a partition file. Containment
    by resolved path alone is not enough (03-REVIEW-ITER2.md IN-10), because
    two same-uid actions defeat it without touching any manifest:

    - **the tier directory itself being a symlink.** If `lake/curated` is a
      link to `lake/lockbox`, `tier_root` and every partition resolve inside
      the lockbox, so both sides of the containment test agree and the read
      is allowed. The tier directory must be a real directory.
    - **a hard link.** `resolve()` cannot see one: a hard link in `curated/`
      to a lockbox partition IS a path under `curated/`, with the quarantined
      bytes behind it. A partition file must have exactly one name
      (`st_nlink == 1`).
    """
    tier = manifest.get("tier")
    if tier != expected_tier:
        raise ManifestTierError(
            f"{manifest_file}: manifest tier {tier!r} is not {expected_tier!r} "
            "-- refusing to resolve it through this loader"
        )
    tier_dir = Path(lake_root) / expected_tier
    if tier_dir.is_symlink():
        raise ManifestTierError(
            f"{manifest_file}: {tier_dir} is a symlink, not a real directory -- "
            "containment cannot be decided by resolving paths through it"
        )
    tier_root = tier_dir.resolve()
    for part in manifest["partitions"]:
        rel = Path(part["path"])
        on_disk = (Path(lake_root) / rel).resolve()
        if rel.is_absolute() or not on_disk.is_relative_to(tier_root):
            raise ManifestTierError(
                f"{manifest_file}: partition path {part['path']!r} resolves to "
                f"{on_disk}, outside {tier_root} -- refusing to read it"
            )
        lstat_path = Path(lake_root) / rel
        if lstat_path.is_file() and lstat_path.lstat().st_nlink != 1:
            raise ManifestTierError(
                f"{manifest_file}: partition {part['path']!r} has "
                f"{lstat_path.lstat().st_nlink} hard links -- the same bytes are "
                "reachable under another name, so a path under "
                f"{tier_root} does not prove containment"
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


#: Every status a DQ check may emit. Anything else is treated as `failed`
#: (03-REVIEW-ITER2.md IN-12: an unknown status next to an `ok` row used to
#: unpause the day).
DQ_STATUSES: frozenset[str] = frozenset({"ok", "degraded", "failed", "n/a"})


#: The `check` name an acknowledgement uses for a `missing` verdict (no
#: report, no row for this manifest, or every row n/a).
MISSING_REPORT_CHECK = "dq_report"

Finding = tuple[str, str]  # (check, dq_status)


def _dq_status_for_date(
    lake_root: Path,
    symbol: str,
    stream: str,
    date: str,
    *,
    manifest: dict,
    registry_root: Path,
) -> tuple[str, str | None]:
    """`(status, detail)` of `_dq_verdict_for_date`."""
    status, detail, _findings = _dq_verdict_for_date(
        lake_root, symbol, stream, date, manifest=manifest, registry_root=registry_root
    )
    return status, detail


def _dq_verdict_for_date(
    lake_root: Path,
    symbol: str,
    stream: str,
    date: str,
    *,
    manifest: dict,
    registry_root: Path,
) -> tuple[str, str | None, frozenset[Finding]]:
    """Return `(status, detail, findings)` for `manifest` on
    `(symbol, stream, date)`. `findings` is every non-ok, non-n/a
    `(check, dq_status)` pair of this manifest's rows, or
    `{(MISSING_REPORT_CHECK, "missing")}` for a `missing` verdict -- what an
    acknowledgement must name to unpause the day (WR-16). The status is:
    the worst-of status across every row of that date's `report.parquet`
    matching `(symbol, stream)` AND scoring THIS manifest.

    BOUND TO THE MANIFEST (03-REVIEW-ITER2.md WR-15): a date can have more
    than one resolvable manifest (a rebuild, WR-03's supersede), and the
    report scores whichever one the by-date pointer named when it ran. The
    verdict used to be looked up by date alone, so a superseded manifest --
    possibly the partial day the rebuild replaced -- loaded under its
    successor's `ok`. Rows now carry `manifest_id`; a manifest with no rows
    of its own is `missing`.

    A report WITHOUT that column is `missing` for every manifest
    (03-REVIEW-ITER3.md WR-18). The legacy shim that accepted one for the
    by-date pointer's manifest when the file was newer than `built_at` is
    gone: a `touch`, `cp -R`, restore or volume move made a report that
    scored a superseded build vouch for its successor. The real lake's
    legacy reports were regenerated in the per-manifest shape first.

    `"missing"` also covers: no `report.parquet` for this date, no row for
    `(symbol, stream)`, and every matching row `"n/a"` (the report ran but
    no substantive check produced a signal) -- all fail closed.

    `"ok"` only when at least one row is `"ok"`, none is `"failed"` or
    `"degraded"`, and none carries a status outside `DQ_STATUSES` (IN-12).
    """
    report_path = dq_report_path(lake_root, date)
    if not report_path.exists():
        return (
            "missing",
            "no DQ report generated for this date",
            frozenset({(MISSING_REPORT_CHECK, "missing")}),
        )

    report = pl.read_parquet(report_path)
    rows = report.filter((pl.col("symbol") == symbol) & (pl.col("stream") == stream))
    if rows.height == 0:
        return (
            "missing",
            "no DQ report generated for this date",
            frozenset({(MISSING_REPORT_CHECK, "missing")}),
        )

    manifest_id = manifest["manifest_id"]
    if "manifest_id" not in rows.columns:
        return (
            "missing",
            "legacy DQ report (no manifest_id column) cannot say which manifest "
            f"it scored, so it does not vouch for {manifest_id[:12]} -- "
            "regenerate it with `python -m data.dq.report`",
            frozenset({(MISSING_REPORT_CHECK, "missing")}),
        )
    own = rows.filter(pl.col("manifest_id") == manifest_id)
    if own.height == 0:
        scored = sorted({str(m)[:12] for m in rows["manifest_id"].to_list()})
        return (
            "missing",
            f"the DQ report for this date scored manifest(s) {scored}, not "
            f"{manifest_id[:12]} -- this manifest was never reported on",
            frozenset({(MISSING_REPORT_CHECK, "missing")}),
        )
    rows = own

    statuses = set(rows["dq_status"].to_list())
    findings = frozenset(
        (str(check), str(status))
        for check, status in rows.select("check", "dq_status").iter_rows()
        if status not in {"ok", "n/a"}
    )
    unknown = statuses - DQ_STATUSES
    if unknown:
        return (
            "failed",
            f"unknown DQ status(es) {sorted(map(str, unknown))}",
            findings,
        )
    if "failed" in statuses:
        return "failed", None, findings
    if "degraded" in statuses:
        return "degraded", None, findings
    if "ok" in statuses:
        return "ok", None, findings
    return (
        "missing",
        "every DQ check reported n/a for this date -- no substantive signal",
        frozenset({(MISSING_REPORT_CHECK, "missing")}),
    )


#: String fields every DQ acknowledgement JSON must carry (03-CONTEXT.md:
#: "reason + who + when, git-committed"), plus the (date, symbol, stream) it
#: acknowledges, which must match the file it lives in.
DQ_ACK_REQUIRED_FIELDS: tuple[str, ...] = (
    "date",
    "symbol",
    "stream",
    "reason",
    "who",
    "when",
)

#: The list field binding an acknowledgement to the findings it covers
#: (03-REVIEW-ITER2.md WR-16): `[{"check": ..., "dq_status": ...}, ...]`.
DQ_ACK_FINDINGS_FIELD = "acknowledged"

#: Statuses a human may acknowledge. `ok`/`n/a` need no acknowledgement; an
#: unknown status is a bug to fix, not a finding to wave through.
DQ_ACKNOWLEDGEABLE_STATUSES: frozenset[str] = frozenset(
    {"failed", "degraded", "missing"}
)

#: Clock-skew allowance for an acknowledgement's `when`.
_ACK_FUTURE_TOLERANCE = dt.timedelta(minutes=10)


def _format_findings(findings) -> str:
    return ", ".join(f"{check}={status}" for check, status in sorted(findings))


def validate_dq_acknowledgement(
    ack_path: Path,
    *,
    symbol: str,
    stream: str,
    date: str,
    findings: frozenset[Finding] | None = None,
    content: bytes | None = None,
) -> str | None:
    """Return `None` if `ack_path`'s CONTENT is a valid acknowledgement of
    `(symbol, stream, date)`, else a one-line reason it is not.

    03-REVIEW.md WR-01: existence alone used to unpause a day, so a zero-byte
    file (or an ack copied from another date/stream) unpaused a `failed` day.
    Valid means: parses as a JSON object; every `DQ_ACK_REQUIRED_FIELDS` key
    is a non-blank string; `date`/`symbol`/`stream` equal the day being
    loaded; `when` parses as an ISO 8601 timestamp that is not in the future;
    and (03-REVIEW-ITER2.md WR-16) `acknowledged` is a non-empty list of
    `{"check", "dq_status"}` objects with an acknowledgeable status. When
    `findings` is given, every finding must be in that list: an ack written
    for "reconciliation degraded" does not cover the day once a rebuild fails
    it on `build_stats`.

    Content only. The loader additionally requires the file to be committed
    (`_dq_ack_git_problem`), and passes the exact bytes it proved committed as
    `content`, so the bytes validated are the bytes hashed (WR-19)."""
    if content is None:
        if not ack_path.exists():
            return "no acknowledgement file"
        content = ack_path.read_bytes()
    try:
        body = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return f"acknowledgement is not valid JSON ({exc.__class__.__name__})"
    if not isinstance(body, dict):
        return "acknowledgement is not a JSON object"
    for key in DQ_ACK_REQUIRED_FIELDS:
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            return f"acknowledgement field {key!r} is missing or blank"
    expected = {"date": date, "symbol": symbol, "stream": stream}
    for key, want in expected.items():
        if body[key] != want:
            return (
                f"acknowledgement {key}={body[key]!r} does not match the "
                f"acknowledged day's {key}={want!r}"
            )
    try:
        when = dt.datetime.fromisoformat(body["when"].replace("Z", "+00:00"))
    except ValueError:
        return f"acknowledgement when={body['when']!r} is not an ISO 8601 timestamp"
    if when.tzinfo is None:
        return f"acknowledgement when={body['when']!r} has no timezone"
    if when > dt.datetime.now(dt.UTC) + _ACK_FUTURE_TOLERANCE:
        return f"acknowledgement when={body['when']!r} is in the future"

    listed = body.get(DQ_ACK_FINDINGS_FIELD)
    if not isinstance(listed, list) or not listed:
        return (
            f"acknowledgement field {DQ_ACK_FINDINGS_FIELD!r} must be a non-empty "
            'list of {"check", "dq_status"} objects naming what it acknowledges'
        )
    acknowledged: set[Finding] = set()
    for item in listed:
        check = item.get("check") if isinstance(item, dict) else None
        status = item.get("dq_status") if isinstance(item, dict) else None
        if not isinstance(check, str) or not check.strip():
            return f"acknowledgement entry {item!r} has no check name"
        if status not in DQ_ACKNOWLEDGEABLE_STATUSES:
            return (
                f"acknowledgement entry {item!r} has dq_status {status!r}; "
                f"acknowledgeable: {sorted(DQ_ACKNOWLEDGEABLE_STATUSES)}"
            )
        acknowledged.add((check, status))
    if findings is not None:
        uncovered = set(findings) - acknowledged
        if uncovered:
            return (
                "acknowledgement does not acknowledge finding(s) "
                f"{_format_findings(uncovered)} (it acknowledges "
                f"{_format_findings(acknowledged)})"
            )
    return None


#: Git index/tree modes of a regular file.
_REGULAR_FILE_MODES = frozenset({"100644", "100755"})


def _dq_ack_git_problem(ack_path: Path, content: bytes | None = None) -> str | None:
    """`None` if `content` (default: `ack_path`'s bytes) is EXACTLY the
    regular-file blob committed in `HEAD` at `ack_path`, else why not.
    Fails closed on any git error.

    03-REVIEW-ITER2.md WR-16: 03-CONTEXT requires the acknowledgement to be
    git-committed; an untracked or edited file used to unpause a day.
    03-REVIEW-ITER3.md WR-19: `git diff HEAD` is not proof of that. It trusts
    `--assume-unchanged`/`--skip-worktree` index flags and compares a symlink
    by its target string, so each let uncommitted bytes unpause a day. So:

    1. neither the ack nor any directory between it and the repository root
       is a symlink (`lstat`, never followed);
    2. the index entry is a regular file with no index flag
       (`git ls-files -v` tag exactly `H`);
    3. `HEAD` holds a regular-file blob at that path;
    4. `git hash-object --no-filters` of the bytes equals that blob."""

    def run(args: list[str], cwd: Path, stdin: bytes | None = None):
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            input=stdin,
            capture_output=True,
            env=scrubbed_git_env(),
        )

    not_committed = "acknowledgement is not committed"
    try:
        if ack_path.is_symlink():
            return f"{not_committed}: it is a symlink, not a regular file"
        if not ack_path.parent.is_dir():
            return "no acknowledgement file"
        top_run = run(["rev-parse", "--show-toplevel"], ack_path.parent)
        if top_run.returncode != 0:
            return (
                "acknowledgement is not committed to git (untracked, or not "
                "inside a git repository)"
            )
        toplevel = Path(top_run.stdout.decode().strip()).resolve()
        lineage = [ack_path, *ack_path.parents]
        top_index = next(
            (
                i
                for i in range(len(lineage) - 1, 0, -1)
                if lineage[i].resolve() == toplevel
            ),
            None,
        )
        if top_index is None:
            return f"{not_committed}: it is not inside repository {toplevel}"
        for component in lineage[1:top_index]:
            if component.is_symlink():
                return f"{not_committed}: its directory {component} is a symlink"
        rel = ack_path.relative_to(lineage[top_index]).as_posix()
        if content is None:
            content = ack_path.read_bytes()

        staged = run(["ls-files", "-s", "-z", "--", rel], toplevel)
        tagged = run(["ls-files", "-v", "-z", "--", rel], toplevel)
        committed = run(["ls-tree", "-z", "HEAD", "--", rel], toplevel)
        hashed = run(["hash-object", "--no-filters", "--stdin"], toplevel, content)
    except OSError as exc:
        return f"{not_committed}: git could not run ({exc})"
    for result in (staged, tagged, committed, hashed):
        if result.returncode != 0:
            return (
                f"{not_committed}: git failed "
                f"({result.stderr.decode(errors='replace').strip()})"
            )

    staged_entries = [e for e in staged.stdout.decode().split("\0") if e]
    if not staged_entries:
        return (
            "acknowledgement is not committed to git (untracked, or not "
            "inside a git repository)"
        )
    staged_meta = staged_entries[0].partition("\t")[0].split()
    if len(staged_entries) != 1 or staged_meta[0] not in _REGULAR_FILE_MODES:
        return f"{not_committed}: its index entry is not one regular file"
    tag = tagged.stdout.decode().split(" ", 1)[0]
    if tag != "H":
        return (
            f"{not_committed}: its index entry carries a flag (git ls-files -v "
            f"tag {tag!r}; --assume-unchanged/--skip-worktree hide edits)"
        )
    tree_entries = [e for e in committed.stdout.decode().split("\0") if e]
    if not tree_entries:
        return f"{not_committed}: HEAD has no file at {rel} (staged only)"
    mode, _type, blob = tree_entries[0].partition("\t")[0].split()
    if mode not in _REGULAR_FILE_MODES:
        return f"{not_committed}: HEAD holds mode {mode} at {rel}, not a regular file"
    if hashed.stdout.decode().strip() != blob:
        return f"{not_committed}: it differs from HEAD (staged or unstaged edits)"
    return None


def _committed_ack_problem(
    ack_path: Path,
    *,
    symbol: str,
    stream: str,
    date: str,
    findings: frozenset[Finding],
) -> tuple[str | None, bytes | None]:
    """`(problem, content)`: read the acknowledgement ONCE, then validate and
    prove committed those same bytes (WR-19: no second read between the two
    checks). The bytes come back so the caller can hash exactly what it
    honoured, rather than re-reading a file that may have changed since."""
    if ack_path.is_symlink():
        return (
            "acknowledgement is not committed: it is a symlink, not a regular file",
            None,
        )
    if not ack_path.is_file():
        return "no acknowledgement file", None
    content = ack_path.read_bytes()
    problem = validate_dq_acknowledgement(
        ack_path,
        symbol=symbol,
        stream=stream,
        date=date,
        findings=findings,
        content=content,
    ) or _dq_ack_git_problem(ack_path, content)
    return problem, content


def _dq_pause_findings(
    manifest: dict, *, registry_root: Path, lake_root: Path
) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str]]]:
    """For every date the manifest covers: `(unacknowledged, acks)` where
    `unacknowledged` lists `(date, status, detail)` for non-ok days without a
    VALID, COMMITTED acknowledgement covering every finding, and `acks` lists
    `(acknowledgement id, sha256 of the exact bytes honoured)` for every
    acknowledgement actually relied on to unpause a non-ok day.

    The hash is taken from the bytes the validation read, not from a second
    read of the file: what a run's provenance records must be what the gate
    actually honoured."""
    symbol = manifest["symbol"]
    stream = manifest["stream"]
    dates = sorted({part["date"] for part in manifest["partitions"]})

    unacknowledged: list[tuple[str, str, str | None]] = []
    acks: list[tuple[str, str]] = []
    for date in dates:
        status, detail, findings = _dq_verdict_for_date(
            Path(lake_root),
            symbol,
            stream,
            date,
            manifest=manifest,
            registry_root=Path(registry_root),
        )
        if status == "ok":
            continue
        ack_path = dq_acknowledgement_path(Path(registry_root), symbol, stream, date)
        problem, content = _committed_ack_problem(
            ack_path, symbol=symbol, stream=stream, date=date, findings=findings
        )
        if problem is None:
            acks.append((ack_path.stem, hashlib.sha256(content or b"").hexdigest()))
        else:
            problem = f"findings {_format_findings(findings)}; {problem}"
            detail = f"{detail}; {problem}" if detail else problem
            unacknowledged.append((date, status, detail))
    return unacknowledged, acks


def dq_acknowledgement_ids(
    manifest: dict, *, registry_root: Path, lake_root: Path
) -> list[str]:
    """The acknowledgement ids a `load_curated` of `manifest` relies on, for
    logging as the `dq_ack_ids` MLflow tag via
    `tracking.mlflow_utils.start_tracked_run(..., dq_ack_ids=...)` (03-CONTEXT:
    "Acknowledgement ids are logged as an MLflow run tag")."""
    _unacked, acks = _dq_pause_findings(
        manifest, registry_root=registry_root, lake_root=lake_root
    )
    return [ack_id for ack_id, _sha in acks]


def _enforce_dq_pause(
    manifest: dict, *, registry_root: Path, lake_root: Path
) -> list[tuple[str, str]]:
    """DATA-07's mechanical training pause: for every date this manifest
    covers, require an `"ok"` status OR a matching, VALID committed
    acknowledgement (`validate_dq_acknowledgement`). Raises `DQPauseError`
    naming every unacknowledged day, and why, if any remain.

    Returns the `(ack id, sha256)` pairs it honoured, so the caller can
    record them without asking the gate the same question twice.
    """
    symbol = manifest["symbol"]
    stream = manifest["stream"]
    unacknowledged, acks = _dq_pause_findings(
        manifest, registry_root=registry_root, lake_root=lake_root
    )

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
            "Add a valid, git-committed acknowledgement JSON "
            f"({', '.join(DQ_ACK_REQUIRED_FIELDS)}, {DQ_ACK_FINDINGS_FIELD}; "
            f"e.g. {example_path}) to proceed."
        )
    return acks


def _log_provenance(manifest: dict, acks: list[tuple[str, str]]) -> None:
    """Record on the active MLflow run (if any) which manifest was read and
    which acknowledgements were honoured to allow it.

    03-CONTEXT.md DATA-07 requires the acknowledgement ids to be logged as a
    run tag, and until now nothing outside tests ever did it: `load_curated`
    computed them and discarded them, so a training run's provenance never
    showed what had been waived. `tracking.mlflow_utils` is imported lazily,
    inside the function: `data.store` must stay importable without pulling
    `mlflow` in (see `tests/tracking/test_no_pandas_via_mlflow.py`).

    RECORD-OR-FAIL, and where the line sits (03-REVIEW-FOLLOWUPS.md WR-08).
    An environment with no `mlflow` installed has no run to record onto, so
    there is nothing to fail about: the import is guarded and the loader
    reads data exactly as it did before provenance logging existed. The guard
    is narrow on purpose -- `exc.name != "mlflow"` re-raises everything that
    is not mlflow's own absence, so a typo'd import inside
    `tracking.mlflow_utils`, or that module going missing outright, stays a
    loud bug rather than becoming a silent skip.

    Everything past the import is NOT swallowed. With no active run nothing
    is logged and nothing raises, but if a run IS active and MLflow refuses
    the tags, that exception propagates out of `load_curated`: provenance
    that cannot be recorded is the same fail-closed direction as every other
    gate here.
    """
    try:
        from tracking.mlflow_utils import log_data_provenance
    except ImportError as exc:
        if exc.name != "mlflow":
            raise
        return  # no tracking installed: no run to record onto, nothing to fail

    log_data_provenance(
        manifest_ids=[manifest["manifest_id"]],
        dq_ack_ids=[ack_id for ack_id, _sha in acks],
        dq_ack_sha256=[sha for _id, sha in acks],
    )


def read_verified_partitions(
    manifest: dict, *, lake_root: Path, columns: Sequence[str] | None = None
) -> list[pl.DataFrame]:
    """Read each of `manifest`'s partitions ONCE, verify the sha256 of THOSE
    bytes, and parse the DataFrame out of the same buffer.

    03-REVIEW-ITER2.md IN-10: `resolve_manifest` hashed the file with
    `read_bytes()` and then `pl.read_parquet` reopened it, so the verified
    bytes and the returned bytes came from two different opens -- anything
    that changed the file in between was returned unverified. Hashing the
    buffer that is then parsed removes the window entirely.

    The cost is one extra read of each partition relative to
    `resolve_manifest`'s own verification pass (largest real partition: 516
    MiB), which is the price of the two hashes being over the same bytes.

    `columns` NARROWS THE PARSE, NEVER THE BYTES THAT ARE HASHED (07-02-PLAN.md
    Task 1). `path.read_bytes()` still reads the WHOLE file and
    `hashlib.sha256(buffer)` still hashes ALL of it, BEFORE `pl.read_parquet`
    touches the buffer -- so IN-10's guarantee (the verified bytes and the
    returned rows come from one buffer) holds identically whether one column is
    parsed out of it or sixteen. What a projection buys is memory: the caller
    that only filters on `etime` no longer materializes fifteen float64 columns
    it will never read (`harness.segments` measured 19.35 GiB -> a few GiB).
    `None` (the default) parses every column, so every pre-existing caller is
    byte-for-byte unchanged. A name not present in the file raises out of
    polars rather than yielding a silently missing column.
    """
    frames: list[pl.DataFrame] = []
    for part in manifest["partitions"]:
        path = Path(lake_root) / part["path"]
        buffer = path.read_bytes()
        digest = hashlib.sha256(buffer).hexdigest()
        if digest != part["sha256"]:
            raise ManifestHashMismatch(str(path), part["sha256"], digest)
        frames.append(
            pl.read_parquet(
                io.BytesIO(buffer),
                columns=list(columns) if columns is not None else None,
            )
        )
    return frames


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

    When an MLflow run is active, the manifest id, the acknowledgement ids
    honoured and their sha256s are recorded on it as the `data_manifest_ids`,
    `dq_ack_ids` and `dq_ack_sha256` tags (03-CONTEXT.md DATA-07). With no
    active run nothing is logged and nothing raises -- the loader is usable
    outside a tracked run, and equally usable in an environment where
    `mlflow` is not installed at all (03-REVIEW-FOLLOWUPS.md WR-08). What
    does NOT pass silently is an active run whose tags MLflow refuses; see
    `_log_provenance` for exactly where that line sits.
    """
    manifest = resolve_manifest(
        manifest_id,
        dataset,
        registry_root=registry_root,
        lake_root=lake_root,
        expected_tier=CURATED_TIER,
    )
    acks = _enforce_dq_pause(manifest, registry_root=registry_root, lake_root=lake_root)
    _log_provenance(manifest, acks)
    frames = read_verified_partitions(manifest, lake_root=Path(lake_root))
    return pl.concat(frames, how="vertical")
