"""Segment manifests as data (D-05-07..10): a sibling, content-addressed
registry to `data.store`'s `manifests/`, deliberately NOT written through
`data.store.issue_manifest` -- a segment names no partition bytes of its
own (D-05-09), and `issue_manifest` hard-refuses an empty `partitions`
list ("a manifest that names nothing verifies nothing").

`purge_ns`/`embargo_ns` are DERIVED fields, never caller-supplied: every
manifest this module issues carries `harness.purge_embargo.PURGE_HORIZON_NS`
/`FOLD_EMBARGO_NS` unconditionally, so a manifest's own purge/embargo
fields can never silently understate the constants the accessor also
imports (checker iteration 1 blocker 1; a later issuer additionally
REFUSES a hand-edited manifest whose fields disagree, T-05-20 -- this
plan's writer alone cannot close that, and does not claim to).

This plan implements the `5seg` layout only. `compressed_3seg` -- the
purged+embargoed inner k-fold OOF split -- is 05-02-PLAN.md's job; calling
`issue_segment_manifest(layout="compressed_3seg", ...)` here raises
`NotImplementedError` naming that plan.
"""

from __future__ import annotations

import json
from pathlib import Path

from data.store import compute_manifest_id
from harness.purge_embargo import FOLD_EMBARGO_NS, PURGE_HORIZON_NS

__all__ = [
    "FIVE_SEG_NAMES",
    "FIVE_SEG_ROLES",
    "issue_segment_manifest",
    "segment_manifest_path",
    "read_segment_manifest",
]

#: The 5seg layout's five named entries, in declaration order (D-05-08).
#: `compressed_3seg`'s `train | val | held_out` (+ `oof_block_*`) names are
#: 05-02-PLAN.md's own concern -- this plan implements 5seg only.
FIVE_SEG_NAMES: tuple[str, ...] = (
    "train_s1",
    "val_s1",
    "train_s2",
    "val_s2",
    "held_out",
)
FIVE_SEG_ROLES: tuple[str, ...] = ("train", "val", "train", "val", "held_out")


def _atomic_write_json(path: Path, body: dict) -> None:
    """Duplicated from `data.store._atomic_write_json` (underscore-private,
    crossed by copying its 5 lines rather than importing, per the project's
    own stated norm -- `data.lockbox` does the same)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(body, sort_keys=True, indent=2))
    tmp_path.replace(path)


def segment_manifest_path(registry_root: Path, manifest_id: str) -> Path:
    """Single source of truth for a segment manifest JSON's on-disk path --
    `registry_root/segments/<manifest_id>.json`, flat (D-05-08: one
    manifest per fold layout, no dataset subdirectory needed)."""
    return Path(registry_root) / "segments" / f"{manifest_id}.json"


def read_segment_manifest(registry_root: Path, manifest_id: str) -> dict:
    """Read a segment manifest JSON and re-verify it still hashes to its
    own id (T-05-04) -- the same self-hash-on-read discipline
    `data.store.resolve_manifest` applies to a partitioned manifest,
    scaled down: a segment manifest has no partitions of its own to
    verify, only its own body."""
    path = segment_manifest_path(registry_root, manifest_id)
    manifest = json.loads(path.read_text())
    recomputed = compute_manifest_id(manifest)
    if recomputed != manifest_id:
        raise ValueError(
            f"{path}: segment manifest hash mismatch -- expected {manifest_id}, "
            f"recomputed {recomputed} from its own body"
        )
    return manifest


def _validate_5seg(segments: list[dict]) -> None:
    names = tuple(s.get("name") for s in segments)
    roles = tuple(s.get("role") for s in segments)
    if names != FIVE_SEG_NAMES or roles != FIVE_SEG_ROLES:
        raise ValueError(
            "issue_segment_manifest: layout='5seg' requires segments named "
            f"{FIVE_SEG_NAMES} with roles {FIVE_SEG_ROLES} exactly, in that "
            f"order; got names={names} roles={roles}. (D-05-02's overlap/"
            "order/coverage refusals are 05-02-PLAN.md's job -- this plan "
            "validates only the 5seg layout's fixed name/role shape.)"
        )


def issue_segment_manifest(
    layout: str,
    segments: list[dict],
    *,
    upstream_feature_manifest_ids: list[str],
    admission: dict,
    errata_id: str | None,
    budget_allowance: int,
    symbol: str,
    version: int,
    code_hash: str,
    registry_root: Path,
) -> dict:
    """Issue a segment manifest for one fold layout (D-05-07..09).

    Every D-05-09 field is written from this plan's first version, even
    where the VALUE is an honest placeholder: `admission` and `errata_id`
    are caller-supplied (this function does not validate `admission`'s
    shape beyond presence -- 05-04-PLAN.md owns the real policy
    semantics), and a caller with nothing to say passes the documented
    honest defaults (`{"policy": "stale_book", "max_age_ns": None,
    "exclude_undefined_age": True, "counts": {}}`, `errata_id=None`).

    `purge_ns`/`embargo_ns` are NOT caller parameters: they are set here,
    unconditionally, from `harness.purge_embargo`'s own constants -- a
    caller cannot understate them (checker iteration 1 blocker 1).

    Body OMITS `partitions` entirely (D-05-07) -- not an empty list, so no
    downstream `manifest.get("partitions", [])` mistakes this for a
    zero-partition manifest `data.store.issue_manifest` would have
    refused.
    """
    if layout == "compressed_3seg":
        raise NotImplementedError(
            "issue_segment_manifest(layout='compressed_3seg'): the inner "
            "purged+embargoed k-fold OOF split is 05-02-PLAN.md's job, not "
            "this plan's"
        )
    if layout != "5seg":
        raise ValueError(f"issue_segment_manifest: unknown layout {layout!r}")
    _validate_5seg(segments)

    body = {
        "layout": layout,
        "segments": segments,
        "purge_ns": PURGE_HORIZON_NS,
        "embargo_ns": FOLD_EMBARGO_NS,
        "upstream_feature_manifest_ids": list(upstream_feature_manifest_ids),
        "admission": admission,
        "errata_id": errata_id,
        "budget_allowance": budget_allowance,
        "symbol": symbol,
        "version": version,
        "code_hash": code_hash,
    }
    manifest_id = compute_manifest_id(body)
    manifest = {"manifest_id": manifest_id, **body}
    _atomic_write_json(segment_manifest_path(registry_root, manifest_id), manifest)
    return manifest
