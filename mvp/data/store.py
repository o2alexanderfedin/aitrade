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
"the default loader" -- it never constructs a path under `lockbox/` (Plan
05 verifies this by construction: the string "lockbox" does not appear
anywhere in this module).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import polars as pl

__all__ = [
    "ManifestHashMismatch",
    "canonicalize_manifest",
    "compute_manifest_id",
    "issue_manifest",
    "resolve_manifest",
    "load_curated",
    "manifest_path",
    "by_date_index_path",
]


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

    Returns the full manifest dict (including the computed `manifest_id`).
    """
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

    covered_dates = (
        dates if dates is not None else sorted({p["date"] for p in partitions})
    )
    for date in covered_dates:
        _atomic_write_json(
            by_date_index_path(registry_root, dataset, symbol, stream, date),
            {"manifest_id": manifest_id},
        )

    return manifest


def resolve_manifest(
    manifest_id: str, dataset: str, *, registry_root: Path, lake_root: Path
) -> dict:
    """Read the committed manifest JSON and verify every claim it makes
    BEFORE returning it: the body must still hash to `manifest_id` (guards
    against a hand-edited `sha256` field inside an otherwise-untouched
    manifest), and each `partitions[]` entry's recomputed on-disk sha256
    must match its stored value.

    Raises `ManifestHashMismatch` on either kind of divergence -- RP-2: a
    manifest resolves to exactly the bytes it names, never silently
    returning wrong data on a hash mismatch.
    """
    path = manifest_path(registry_root, dataset, manifest_id)
    manifest = json.loads(path.read_text())

    recomputed_id = compute_manifest_id(manifest)
    if recomputed_id != manifest_id:
        raise ManifestHashMismatch(str(path), manifest_id, recomputed_id)

    for part in manifest["partitions"]:
        on_disk_path = Path(lake_root) / part["path"]
        if not on_disk_path.exists():
            raise ManifestHashMismatch(str(on_disk_path), part["sha256"], "<missing>")
        on_disk_hash = hashlib.sha256(on_disk_path.read_bytes()).hexdigest()
        if on_disk_hash != part["sha256"]:
            raise ManifestHashMismatch(str(on_disk_path), part["sha256"], on_disk_hash)

    return manifest


def load_curated(
    manifest_id: str, dataset: str, *, registry_root: Path, lake_root: Path
) -> pl.DataFrame:
    """Resolve `manifest_id` (verifying every partition's hash) and return
    the concatenated curated rows.

    The only reading path this phase builds that is called "the default
    loader" -- it never constructs a path under `lockbox/` (Plan 05
    verifies this by construction, not by a runtime check inside this
    function -- the string does not appear anywhere in this module).
    """
    manifest = resolve_manifest(
        manifest_id, dataset, registry_root=registry_root, lake_root=lake_root
    )
    frames = [
        pl.read_parquet(Path(lake_root) / part["path"])
        for part in manifest["partitions"]
    ]
    return pl.concat(frames, how="vertical")
