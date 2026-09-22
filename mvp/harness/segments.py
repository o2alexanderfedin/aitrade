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

05-02-PLAN.md Task 1 (this revision) adds the real D-05-02 geometric
refusals (overlap, order, coverage, held_out's exemption from the upper
coverage bound per D-05-16, and the starvation refusal), plus the derived
`effective_intervals`/`purged_row_count`/`embargoed_row_count` fields
D-05-09 requires -- measured against REAL upstream partitions at issuance,
through the identical `harness.purge_embargo.effective_train_intervals`
P1's accessor already calls at read time (checker iteration 1 blocker 1),
so the two can never disagree. Reading real partitions requires a real
`lake_root` -- a new required keyword-only parameter of
`issue_segment_manifest` this revision adds (not in P1's version, which
had nothing to read).

Task 2 (05-02-PLAN.md) adds `compressed_3seg`'s inner purged+embargoed
k-fold OOF blocks (`harness/kfold.py`) and wires them through this same
validation/derivation pipeline; until then `issue_segment_manifest(
layout="compressed_3seg", ...)` still raises `NotImplementedError`.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import polars as pl

from data import store
from data.store import FEATURES_TIER, compute_manifest_id
from features.tier import load_features
from harness.purge_embargo import (
    FOLD_EMBARGO_NS,
    PURGE_HORIZON_NS,
    effective_train_intervals,
)

__all__ = [
    "FIVE_SEG_NAMES",
    "FIVE_SEG_ROLES",
    "issue_segment_manifest",
    "segment_manifest_path",
    "read_segment_manifest",
]

#: The 5seg layout's five named entries, in declaration order (D-05-08).
#: `compressed_3seg`'s `train | val | held_out` (+ `oof_block_*`) names are
#: 05-02-PLAN.md Task 2's own concern.
FIVE_SEG_NAMES: tuple[str, ...] = (
    "train_s1",
    "val_s1",
    "train_s2",
    "val_s2",
    "held_out",
)
FIVE_SEG_ROLES: tuple[str, ...] = ("train", "val", "train", "val", "held_out")

#: Roles a train entry's own purge/embargo geometry is computed against
#: (D-05-04): validation and held-out label windows only -- never a
#: sibling `train` entry, and never a `train`'s own nested `oof_block`
#: children (they partition that very train; see
#: `harness.purge_embargo`'s module docstring for the full D-05-04
#: citation of this scoping rule).
_PURGE_EMBARGO_OTHER_ROLES: frozenset[str] = frozenset({"val", "held_out"})


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
            f"order; got names={names} roles={roles}."
        )


def _intervals_overlap(a: dict, b: dict) -> bool:
    return a["start_ns"] < b["end_ns"] and b["start_ns"] < a["end_ns"]


def _validate_segments(
    segments: list[dict], *, covered_start_ns: int, covered_end_ns: int
) -> None:
    """D-05-02's geometric refusals, applied to any layout's segment list.

    `held_out` sits forward in time (D-05-16) and is REQUIRED, but exempt
    from the upper coverage bound every `train`/`val` entry must respect --
    the sentinel encoding D-05-16 describes (`start_ns == end_ns ==
    covered_end_ns`) is ACCEPTED here, but never required: any `start_ns`
    at or after the last `train`/`val` entry's `end_ns` is valid, sentinel
    or not.

    `oof_block` entries (`compressed_3seg` only, Task 2) are sub-partitions
    of their own parent `train` entry by construction (D-05-08) -- nesting
    inside it is not an overlap, so they are checked by CONTAINMENT against
    their parent `train` entry instead of the top-level overlap/order pass
    below, which only ever sees `train`/`val` entries.
    """
    top_level = [s for s in segments if s["role"] in ("train", "val")]
    held_out_entries = [s for s in segments if s["role"] == "held_out"]
    oof_blocks = [s for s in segments if s["role"] == "oof_block"]

    # --- overlap, across every train/val pair (D-05-02) ---
    for a, b in itertools.combinations(top_level, 2):
        if _intervals_overlap(a, b):
            lo, hi = max(a["start_ns"], b["start_ns"]), min(a["end_ns"], b["end_ns"])
            raise ValueError(
                f"_validate_segments: {a['name']!r} and {b['name']!r} overlap "
                f"on [{lo}, {hi})"
            )

    # --- chronological order: declared order must match sorted order,
    # which also catches "a val/held_out segment precedes the train
    # segment it is meant to follow" (D-05-02) for any pair whose overlap
    # test above did not already fire ---
    chrono = sorted(top_level, key=lambda s: s["start_ns"])
    declared_names = [s["name"] for s in top_level]
    chrono_names = [s["name"] for s in chrono]
    if declared_names != chrono_names:
        raise ValueError(
            "_validate_segments: declared segment order "
            f"{declared_names} does not match chronological order "
            f"{chrono_names} -- segments out of order, or a val segment "
            "precedes the train entry it is meant to follow (D-05-02)"
        )

    # --- coverage: every train/val entry must fit inside the covered
    # upstream etime range (D-05-02) ---
    for entry in top_level:
        if entry["start_ns"] < covered_start_ns or entry["end_ns"] > covered_end_ns:
            raise ValueError(
                f"_validate_segments: {entry['name']!r} range "
                f"[{entry['start_ns']}, {entry['end_ns']}) exceeds the covered "
                f"etime range [{covered_start_ns}, {covered_end_ns}) (D-05-02)"
            )

    # --- held_out: required exactly once, must not precede what it
    # follows, EXEMPT from the upper coverage bound (D-05-16) ---
    if len(held_out_entries) != 1:
        raise ValueError(
            "_validate_segments: exactly one 'held_out' entry is required, "
            f"got {len(held_out_entries)}"
        )
    held_out = held_out_entries[0]
    last_end_ns = chrono[-1]["end_ns"] if chrono else covered_start_ns
    if held_out["start_ns"] < last_end_ns:
        raise ValueError(
            f"_validate_segments: 'held_out' (start_ns={held_out['start_ns']}) "
            f"precedes the last train/val entry it follows (end_ns={last_end_ns})"
        )

    # --- oof_block: each nests inside SOME train entry (never an
    # overlap), oof_blocks never overlap each other (D-05-08) ---
    train_entries = [s for s in top_level if s["role"] == "train"]
    for block in oof_blocks:
        if not any(
            t["start_ns"] <= block["start_ns"] and block["end_ns"] <= t["end_ns"]
            for t in train_entries
        ):
            raise ValueError(
                f"_validate_segments: oof_block {block['name']!r} "
                f"[{block['start_ns']}, {block['end_ns']}) is not contained "
                "within any train entry (D-05-08: OOF blocks partition their "
                "own train entry)"
            )
    for a, b in itertools.combinations(oof_blocks, 2):
        if _intervals_overlap(a, b):
            raise ValueError(
                f"_validate_segments: oof_block {a['name']!r} and {b['name']!r} overlap"
            )


def _covered_range(
    upstream_feature_manifest_ids: list[str],
    *,
    symbol: str,
    registry_root: Path,
    lake_root: Path,
) -> tuple[int, int]:
    """The `[min(etime_min), max(etime_max)]` span covered by every
    upstream feature manifest -- read from each manifest's own,
    integrity-verified `partitions[]` metadata (`data.store.resolve_manifest`,
    the same gate `features.tier.load_features` applies first: self-hash,
    tier containment, on-disk sha256 per partition). No partition is
    parsed into rows for this -- only the metadata already written into
    each manifest at ITS OWN issuance is read.
    """
    if not upstream_feature_manifest_ids:
        raise ValueError(
            "_covered_range: upstream_feature_manifest_ids is empty -- a "
            "segment manifest naming no upstream feature manifest covers "
            "nothing"
        )
    dataset = f"{symbol}.{FEATURES_TIER}"
    etime_mins: list[int] = []
    etime_maxs: list[int] = []
    for manifest_id in upstream_feature_manifest_ids:
        manifest = store.resolve_manifest(
            manifest_id,
            dataset,
            registry_root=registry_root,
            lake_root=lake_root,
            expected_tier=FEATURES_TIER,
        )
        for part in manifest["partitions"]:
            etime_mins.append(part["etime_min"])
            etime_maxs.append(part["etime_max"])
    return min(etime_mins), max(etime_maxs)


def _train_effective_intervals(
    segments: list[dict],
) -> dict[str, list[tuple[int, int]]]:
    """Per train entry's `effective_train_intervals` against its own
    `val`/`held_out` neighbours only (D-05-04) -- never a sibling `train`
    entry, and never its own nested `oof_block` children. PURE GEOMETRY,
    no partition read -- the starvation refusal below fires before any
    upstream data is touched, so a starved layout is refused cheaply.
    """
    result: dict[str, list[tuple[int, int]]] = {}
    for entry in segments:
        if entry["role"] != "train":
            continue
        others = [
            other
            for other in segments
            if other["name"] != entry["name"]
            and other["role"] in _PURGE_EMBARGO_OTHER_ROLES
        ]
        intervals = effective_train_intervals(
            entry["start_ns"],
            entry["end_ns"],
            others,
            purge_ns=PURGE_HORIZON_NS,
            embargo_ns=FOLD_EMBARGO_NS,
        )
        if not intervals:
            raise ValueError(
                f"_derive_purge_embargo_fields: train entry {entry['name']!r} "
                "is starved -- every row of its declared range is purged or "
                "embargoed by its neighbouring val/held_out entries "
                f"{[o['name'] for o in others]} (D-05-14's remedy: a new, "
                "non-overlapping segment layout)"
            )
        result[entry["name"]] = intervals
    return result


def _derive_purge_embargo_fields(
    segments: list[dict],
    *,
    upstream_feature_manifest_ids: list[str],
    symbol: str,
    registry_root: Path,
    lake_root: Path,
) -> dict:
    """D-05-09's derived fields, measured against REAL upstream partitions,
    never a caller-supplied number: per train entry, `effective_intervals`
    (via the identical `harness.purge_embargo.effective_train_intervals`
    P1's accessor calls at read time -- the starvation refusal fires here,
    before any partition is read), plus `purged_row_count`/
    `embargoed_row_count` measured over that train entry's own
    declared-range rows.

    PURGE TAKES PRECEDENCE. A row can fall in one neighbour's embargo
    clause and simultaneously in a DIFFERENT neighbour's purge clause (the
    two clauses are disjoint only for a single "other" entry, not for two)
    -- such a row is counted once, as purged. `purged_row_count` +
    `embargoed_row_count` is therefore a partition of the excluded rows,
    never a double-count.
    """
    effective_intervals = _train_effective_intervals(segments)

    dataset = f"{symbol}.{FEATURES_TIER}"
    frames = [
        load_features(
            manifest_id, dataset, registry_root=registry_root, lake_root=lake_root
        )
        for manifest_id in upstream_feature_manifest_ids
    ]
    df = pl.concat(frames, how="vertical")

    purged_row_count: dict[str, int] = {}
    embargoed_row_count: dict[str, int] = {}
    for entry in segments:
        if entry["role"] != "train":
            continue
        others = [
            other
            for other in segments
            if other["name"] != entry["name"]
            and other["role"] in _PURGE_EMBARGO_OTHER_ROLES
        ]
        entry_etimes = df.filter(
            (pl.col("etime") >= entry["start_ns"]) & (pl.col("etime") < entry["end_ns"])
        )["etime"].to_list()

        purged = 0
        embargoed = 0
        for etime in entry_etimes:
            if any(
                (other["start_ns"] - PURGE_HORIZON_NS)
                <= etime
                < (other["end_ns"] + PURGE_HORIZON_NS)
                for other in others
            ):
                purged += 1
                continue
            if any(
                (other["end_ns"] + PURGE_HORIZON_NS)
                <= etime
                < (other["end_ns"] + PURGE_HORIZON_NS + FOLD_EMBARGO_NS)
                for other in others
            ):
                embargoed += 1
        purged_row_count[entry["name"]] = purged
        embargoed_row_count[entry["name"]] = embargoed

    return {
        # JSON-safe (lists, not tuples) so the in-memory return value
        # matches byte-for-byte what a later `read_segment_manifest` call
        # re-hashes and returns.
        "effective_intervals": {
            name: [[start, end] for start, end in intervals]
            for name, intervals in effective_intervals.items()
        },
        "purge_ns": PURGE_HORIZON_NS,
        "embargo_ns": FOLD_EMBARGO_NS,
        "purged_row_count": purged_row_count,
        "embargoed_row_count": embargoed_row_count,
    }


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
    lake_root: Path,
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

    `lake_root` is a NEW required keyword-only parameter (05-02-PLAN.md
    Task 1, absent from P1's version): `effective_intervals`,
    `purged_row_count`, `embargoed_row_count` are DERIVED against real
    upstream partitions (D-05-09), which cannot exist without reading
    them. There is no optional/opt-out variant -- every issued manifest
    carries real, measured fields, never a caller-supplied placeholder.

    Body OMITS `partitions` entirely (D-05-07) -- not an empty list, so no
    downstream `manifest.get("partitions", [])` mistakes this for a
    zero-partition manifest `data.store.issue_manifest` would have
    refused.
    """
    if layout == "compressed_3seg":
        raise NotImplementedError(
            "issue_segment_manifest(layout='compressed_3seg'): the inner "
            "purged+embargoed k-fold OOF split is 05-02-PLAN.md Task 2's job, "
            "not Task 1's"
        )
    if layout != "5seg":
        raise ValueError(f"issue_segment_manifest: unknown layout {layout!r}")
    _validate_5seg(segments)

    covered_start_ns, covered_end_ns = _covered_range(
        upstream_feature_manifest_ids,
        symbol=symbol,
        registry_root=registry_root,
        lake_root=lake_root,
    )
    _validate_segments(
        segments, covered_start_ns=covered_start_ns, covered_end_ns=covered_end_ns
    )
    derived = _derive_purge_embargo_fields(
        segments,
        upstream_feature_manifest_ids=upstream_feature_manifest_ids,
        symbol=symbol,
        registry_root=registry_root,
        lake_root=lake_root,
    )

    body = {
        "layout": layout,
        "segments": segments,
        "upstream_feature_manifest_ids": list(upstream_feature_manifest_ids),
        "admission": admission,
        "errata_id": errata_id,
        "budget_allowance": budget_allowance,
        "symbol": symbol,
        "version": version,
        "code_hash": code_hash,
        **derived,
    }
    manifest_id = compute_manifest_id(body)
    manifest = {"manifest_id": manifest_id, **body}
    _atomic_write_json(segment_manifest_path(registry_root, manifest_id), manifest)
    return manifest
